"""Product tables layered over the Gate 2 store. SQLite remains the local adapter."""

from __future__ import annotations

import json
import uuid

from app.store import POCStore


class ProductStore:
    def __init__(self, store: POCStore) -> None:
        self._store = store

    def initialize(self) -> None:
        self._store.initialize()
        if self._store.is_postgres:
            return
        with self._store.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('enterprise_admin','member')), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS credit_accounts (tenant_id TEXT PRIMARY KEY REFERENCES tenants(id), balance INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS credit_transactions (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT REFERENCES users(id), task_id TEXT, amount INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT NOT NULL REFERENCES users(id), agent_id TEXT NOT NULL, conversation_id TEXT, input_text TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled')), stage TEXT, error_code TEXT, user_message TEXT, run_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at TEXT, completed_at TEXT);
                CREATE TABLE IF NOT EXISTS task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id), stage TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS task_results (task_id TEXT PRIMARY KEY REFERENCES tasks(id), final_response TEXT, result_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS conversation_owners (conversation_id TEXT PRIMARY KEY REFERENCES conversations(id), user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL DEFAULT '新图片会话', deleted_at TEXT);
                CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS generations (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT NOT NULL REFERENCES users(id), conversation_id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id), provider TEXT, model TEXT, storage_key TEXT, mime_type TEXT, width INTEGER, height INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, deleted_at TEXT);
                CREATE TABLE IF NOT EXISTS knowledge_bases (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), name TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS knowledge_files (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), knowledge_base_id TEXT REFERENCES knowledge_bases(id), name TEXT NOT NULL, status TEXT NOT NULL, storage_key TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS asset_metadata (asset_id TEXT PRIMARY KEY REFERENCES assets(id), description TEXT NOT NULL DEFAULT '', visibility TEXT NOT NULL DEFAULT 'enterprise');
            """)

    def create_user(self, tenant_id: str, email: str, password_hash: str, display_name: str, role: str) -> None:
        with self._store.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)", (str(uuid.uuid4()), tenant_id, email.lower(), password_hash, display_name, role))
            conn.execute("INSERT OR IGNORE INTO credit_accounts(tenant_id,balance) VALUES (?, 200)", (tenant_id,))

    def user_by_email(self, email: str) -> dict | None:
        with self._store.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
        return dict(row) if row else None

    def user_by_id(self, user_id: str, tenant_id: str) -> dict | None:
        """Return only the signed-in user's own product profile."""
        with self._store.connection() as conn:
            row = conn.execute(
                "SELECT id, tenant_id, email, display_name, role FROM users WHERE id=? AND tenant_id=?",
                (user_id, tenant_id),
            ).fetchone()
        return dict(row) if row else None

    def workspace(self, tenant_id: str, user_id: str) -> dict:
        with self._store.connection() as conn:
            tenant = conn.execute("SELECT name FROM tenants WHERE id=?", (tenant_id,)).fetchone()
            credit = conn.execute("SELECT balance FROM credit_accounts WHERE tenant_id=?", (tenant_id,)).fetchone()
        config = self._store.enterprise_config(tenant_id)
        return {"tenant_name": tenant["name"], "brand_name": config.get("brand_name"), "logo": config.get("logo"), "credit_balance": credit["balance"] if credit else 0, "recent_conversations": self.conversations(tenant_id, user_id, 5), "recent_generations": self.generations(tenant_id, user_id, 5)}

    def create_task(self, tenant_id: str, user_id: str, agent_id: str, text: str, conversation_id: str | None) -> dict:
        with self._store.connection() as conn:
            credit = conn.execute("SELECT balance FROM credit_accounts WHERE tenant_id=?", (tenant_id,)).fetchone()
            if not credit or credit["balance"] < 20:
                raise ValueError("insufficient_credit")
            if conversation_id:
                owner = conn.execute("SELECT user_id FROM conversation_owners WHERE conversation_id=? AND deleted_at IS NULL", (conversation_id,)).fetchone()
                if not owner or owner["user_id"] != user_id:
                    raise LookupError("会话不存在或不属于当前用户。")
            task_id = str(uuid.uuid4())
            conn.execute("INSERT INTO tasks(id,tenant_id,user_id,agent_id,conversation_id,input_text,status,stage) VALUES (?,?,?,?,?,?,'queued','queued')", (task_id, tenant_id, user_id, agent_id, conversation_id, text))
            conn.execute("INSERT INTO task_events(task_id,stage,message) VALUES (?, 'queued', '任务已进入队列')", (task_id,))
        return self.task(task_id, tenant_id, user_id) or {}

    def set_task(self, task_id: str, tenant_id: str, status: str, stage: str, message: str, *, run_id: str | None = None, error_code: str | None = None, response: str | None = None, conversation_id: str | None = None) -> None:
        with self._store.connection() as conn:
            conn.execute("UPDATE tasks SET status=?,stage=?,run_id=COALESCE(?,run_id),error_code=?,conversation_id=COALESCE(?,conversation_id),started_at=CASE WHEN ?='running' THEN CURRENT_TIMESTAMP ELSE started_at END,completed_at=CASE WHEN ? IN ('completed','failed','cancelled') THEN CURRENT_TIMESTAMP ELSE completed_at END WHERE id=? AND tenant_id=?", (status,stage,run_id,error_code,conversation_id,status,status,task_id,tenant_id))
            conn.execute("INSERT INTO task_events(task_id,stage,message) VALUES (?,?,?)", (task_id,stage,message))
            if response is not None:
                conn.execute("INSERT INTO task_results(task_id,final_response,result_json) VALUES (?,?,?) ON CONFLICT(task_id) DO UPDATE SET final_response=excluded.final_response,result_json=excluded.result_json", (task_id,response,json.dumps({"run_id":run_id},ensure_ascii=False)))

    def attach_conversation(self, conversation_id: str, user_id: str, title: str = "新图片会话") -> None:
        with self._store.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO conversation_owners(conversation_id,user_id,title) VALUES (?,?,?)", (conversation_id,user_id,title))

    def rename_conversation(self, tenant_id: str, user_id: str, conversation_id: str, title: str) -> bool:
        with self._store.connection() as conn:
            cursor=conn.execute("UPDATE conversation_owners SET title=? WHERE conversation_id=? AND user_id=? AND EXISTS (SELECT 1 FROM conversations WHERE id=? AND tenant_id=?)",(title.strip()[:80],conversation_id,user_id,conversation_id,tenant_id))
        return cursor.rowcount == 1

    def delete_conversation(self, tenant_id: str, user_id: str, conversation_id: str) -> bool:
        with self._store.connection() as conn:
            cursor=conn.execute("UPDATE conversation_owners SET deleted_at=CURRENT_TIMESTAMP WHERE conversation_id=? AND user_id=? AND EXISTS (SELECT 1 FROM conversations WHERE id=? AND tenant_id=?)",(conversation_id,user_id,conversation_id,tenant_id))
        return cursor.rowcount == 1

    def task(self, task_id: str, tenant_id: str, user_id: str) -> dict | None:
        with self._store.connection() as conn:
            row = conn.execute("SELECT t.*, r.final_response FROM tasks t LEFT JOIN task_results r ON r.task_id=t.id WHERE t.id=? AND t.tenant_id=? AND t.user_id=?", (task_id,tenant_id,user_id)).fetchone()
            events = conn.execute("SELECT stage,message,created_at FROM task_events WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
        if not row: return None
        result = dict(row); result["events"] = [dict(x) for x in events]; return result

    def task_for_worker(self, task_id: str) -> dict | None:
        """Internal queue lookup. It deliberately has no user-controlled tenant input."""
        with self._store.connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def conversations(self, tenant_id: str, user_id: str, limit: int = 50) -> list[dict]:
        with self._store.connection() as conn:
            rows = conn.execute("SELECT c.id,c.agent_id,c.runtime_thread_id,o.title,c.created_at FROM conversations c JOIN conversation_owners o ON o.conversation_id=c.id WHERE c.tenant_id=? AND o.user_id=? AND o.deleted_at IS NULL ORDER BY c.created_at DESC LIMIT ?", (tenant_id,user_id,limit)).fetchall()
            conversations = [dict(row) for row in rows]
            # Keep the query portable across SQLite and PostgreSQL while exposing
            # only the current user's most recent image for each conversation.
            for conversation in conversations:
                generation = conn.execute(
                    "SELECT id,storage_key,mime_type,created_at FROM generations "
                    "WHERE tenant_id=? AND user_id=? AND conversation_id=? AND deleted_at IS NULL "
                    "ORDER BY created_at DESC LIMIT 1",
                    (tenant_id, user_id, conversation["id"]),
                ).fetchone()
                conversation["latest_generation"] = dict(generation) if generation else None
        return conversations

    def generations(self, tenant_id: str, user_id: str, limit: int = 50) -> list[dict]:
        with self._store.connection() as conn:
            rows=conn.execute("SELECT * FROM generations WHERE tenant_id=? AND user_id=? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT ?",(tenant_id,user_id,limit)).fetchall()
        return [dict(row) for row in rows]

    def create_generation(self, tenant_id: str, user_id: str, conversation_id: str, task_id: str, storage_key: str, provider: str, model: str) -> None:
        with self._store.connection() as conn:
            conn.execute("INSERT INTO generations(id,tenant_id,user_id,conversation_id,task_id,provider,model,storage_key,mime_type) VALUES (?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()),tenant_id,user_id,conversation_id,task_id,provider,model,storage_key,"image/png"))

    def can_read_storage(self, tenant_id: str, user_id: str, storage_key: str) -> bool:
        with self._store.connection() as conn:
            row=conn.execute("SELECT 1 FROM generations WHERE tenant_id=? AND user_id=? AND storage_key=? AND deleted_at IS NULL",(tenant_id,user_id,storage_key)).fetchone()
        return bool(row)

    def delete_generation(self, tenant_id: str, user_id: str, generation_id: str) -> dict | None:
        with self._store.connection() as conn:
            row=conn.execute("SELECT * FROM generations WHERE id=? AND tenant_id=? AND user_id=? AND deleted_at IS NULL",(generation_id,tenant_id,user_id)).fetchone()
            if row: conn.execute("UPDATE generations SET deleted_at=CURRENT_TIMESTAMP WHERE id=?",(generation_id,))
        return dict(row) if row else None

    def save_generation_as_asset(self, tenant_id: str, user_id: str, generation_id: str, name: str) -> dict | None:
        with self._store.connection() as conn:
            generation=conn.execute("SELECT * FROM generations WHERE id=? AND tenant_id=? AND user_id=? AND deleted_at IS NULL",(generation_id,tenant_id,user_id)).fetchone()
            if not generation: return None
            asset_id=str(uuid.uuid4()); conn.execute("INSERT INTO assets(id,tenant_id,name,asset_type,tags,url) VALUES (?,?,?,?,?,?)",(asset_id,tenant_id,name[:120],"poster_reference",json.dumps(["generated"]),f"/api/v1/storage/{generation['storage_key']}")); conn.execute("INSERT INTO asset_metadata(asset_id,description) VALUES (?,?)",(asset_id,"由个人生成记录保存"))
        return {"id":asset_id,"name":name,"type":"poster_reference"}

    def update_enterprise_config(self, tenant_id: str, payload: dict) -> dict:
        current=self._store.enterprise_config(tenant_id); current.update({k:v for k,v in payload.items() if v is not None})
        with self._store.connection() as conn: conn.execute("UPDATE enterprise_configs SET payload=? WHERE tenant_id=?",(json.dumps(current,ensure_ascii=False),tenant_id))
        return current

    def knowledge_files(self, tenant_id: str) -> list[dict]:
        with self._store.connection() as conn: rows=conn.execute("SELECT * FROM knowledge_files WHERE tenant_id=? ORDER BY created_at DESC",(tenant_id,)).fetchall()
        return [dict(x) for x in rows]

    def add_knowledge_text(self, tenant_id: str, name: str, content: str) -> dict:
        file_id=str(uuid.uuid4())
        with self._store.connection() as conn:
            conn.execute("INSERT INTO knowledge_files(id,tenant_id,name,status) VALUES (?,?,?,'ready')",(file_id,tenant_id,name[:180])); conn.execute("INSERT INTO knowledge_documents(tenant_id,title,body) VALUES (?,?,?)",(tenant_id,name[:180],content))
        return {"id":file_id,"name":name,"status":"ready"}

    def delete_knowledge_file(self, tenant_id: str, file_id: str) -> bool:
        with self._store.connection() as conn: cursor=conn.execute("DELETE FROM knowledge_files WHERE id=? AND tenant_id=?",(file_id,tenant_id))
        return cursor.rowcount == 1

    def assets(self, tenant_id: str) -> list[dict]:
        with self._store.connection() as conn: rows=conn.execute("SELECT a.*,COALESCE(m.description,'') description FROM assets a LEFT JOIN asset_metadata m ON m.asset_id=a.id WHERE a.tenant_id=? ORDER BY a.name",(tenant_id,)).fetchall()
        return [dict(x) for x in rows]

    def add_asset(self, tenant_id: str, name: str, asset_type: str, url: str, tags: list[str], description: str) -> dict:
        if asset_type not in {"logo","poster_reference","product_image","teacher_image","other"}: raise ValueError("unsupported_asset_type")
        asset_id=str(uuid.uuid4())
        with self._store.connection() as conn: conn.execute("INSERT INTO assets(id,tenant_id,name,asset_type,tags,url) VALUES (?,?,?,?,?,?)",(asset_id,tenant_id,name[:120],asset_type,json.dumps(tags),url)); conn.execute("INSERT INTO asset_metadata(asset_id,description) VALUES (?,?)",(asset_id,description[:1000]))
        return {"id":asset_id,"name":name,"type":asset_type}

    def delete_asset(self, tenant_id: str, asset_id: str) -> bool:
        with self._store.connection() as conn: cursor=conn.execute("DELETE FROM assets WHERE id=? AND tenant_id=?",(asset_id,tenant_id))
        return cursor.rowcount == 1

    def charge_success(self, tenant_id: str, user_id: str, task_id: str, amount: int = 20) -> None:
        with self._store.connection() as conn:
            if conn.execute("SELECT 1 FROM credit_transactions WHERE task_id=? AND reason='poster-design'",(task_id,)).fetchone(): return
            conn.execute("UPDATE credit_accounts SET balance=balance-?,updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND balance>=?",(amount,tenant_id,amount))
            conn.execute("INSERT INTO credit_transactions(id,tenant_id,user_id,task_id,amount,reason) VALUES (?,?,?,?,?,?)",(str(uuid.uuid4()),tenant_id,user_id,task_id,-amount,"poster-design"))

    def recoverable_tasks(self) -> list[dict]:
        with self._store.connection() as conn:
            conn.execute("UPDATE tasks SET status='queued',stage='queued',user_message='服务已恢复，任务重新排队' WHERE status IN ('queued','running')")
            rows=conn.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]
