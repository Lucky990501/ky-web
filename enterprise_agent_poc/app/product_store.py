"""Product tables layered over the Gate 2 store. SQLite remains the local adapter."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.agent_catalog import CATALOG, get_agent
from app.store import POCStore


class ResultPersistenceError(RuntimeError):
    def __init__(self, stage: str) -> None:
        super().__init__(f"运行结果持久化失败（stage={stage}）。")
        self.stage = stage


class ProductStore:
    def __init__(self, store: POCStore) -> None:
        self._store = store

    def initialize(self) -> None:
        self._store.initialize()
        if self._store.is_postgres:
            with self._store.connection() as conn:
                conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_storage_key TEXT")
                conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_mime_type TEXT")
                conn.execute("ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS filename TEXT")
                conn.execute("ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS mime_type TEXT")
                conn.execute("ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS size_bytes BIGINT")
                conn.execute("ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS uploaded_by TEXT")
                conn.execute("ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS error_message TEXT")
                conn.execute("ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS parsed_text TEXT")
                conn.execute("ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS chunk_count INTEGER NOT NULL DEFAULT 0")
                conn.execute("ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS embedding_provider TEXT")
                conn.execute("ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS embedding_model TEXT")
                conn.execute("CREATE TABLE IF NOT EXISTS knowledge_chunks (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE, knowledge_base_id TEXT, file_id TEXT NOT NULL REFERENCES knowledge_files(id) ON DELETE CASCADE, content TEXT NOT NULL, title TEXT, section TEXT, page_number INTEGER, chunk_index INTEGER NOT NULL, embedding TEXT, embedding_provider TEXT, embedding_model TEXT, embedding_version TEXT, metadata TEXT NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(file_id,chunk_index))")
                conn.execute("CREATE TABLE IF NOT EXISTS agent_templates (id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, description TEXT NOT NULL, icon TEXT NOT NULL, status TEXT NOT NULL, default_runtime_profile TEXT NOT NULL, credit_cost INTEGER NOT NULL, skill_manifest TEXT NOT NULL, allows_image_generation BOOLEAN NOT NULL DEFAULT FALSE)")
                conn.execute("CREATE TABLE IF NOT EXISTS tenant_agent_instances (tenant_id TEXT NOT NULL REFERENCES tenants(id), agent_id TEXT NOT NULL REFERENCES agent_templates(id), status TEXT NOT NULL, PRIMARY KEY(tenant_id,agent_id))")
            self._seed_agent_catalog()
            return
        with self._store.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('enterprise_admin','member')), avatar_storage_key TEXT, avatar_mime_type TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS credit_accounts (tenant_id TEXT PRIMARY KEY REFERENCES tenants(id), balance INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS credit_transactions (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT REFERENCES users(id), task_id TEXT, amount INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT NOT NULL REFERENCES users(id), agent_id TEXT NOT NULL, conversation_id TEXT, input_text TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled')), stage TEXT, error_code TEXT, user_message TEXT, run_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at TEXT, completed_at TEXT);
                CREATE TABLE IF NOT EXISTS task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id), stage TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS task_results (task_id TEXT PRIMARY KEY REFERENCES tasks(id), final_response TEXT, result_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS conversation_owners (conversation_id TEXT PRIMARY KEY REFERENCES conversations(id), user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL DEFAULT '新图片会话', deleted_at TEXT);
                CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS generations (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT NOT NULL REFERENCES users(id), conversation_id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id), provider TEXT, model TEXT, storage_key TEXT, mime_type TEXT, width INTEGER, height INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, deleted_at TEXT);
                CREATE TABLE IF NOT EXISTS knowledge_bases (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), name TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS knowledge_files (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), knowledge_base_id TEXT REFERENCES knowledge_bases(id), name TEXT NOT NULL, filename TEXT, mime_type TEXT, size_bytes INTEGER, uploaded_by TEXT, status TEXT NOT NULL, storage_key TEXT, error_message TEXT, parsed_text TEXT, chunk_count INTEGER NOT NULL DEFAULT 0, embedding_provider TEXT, embedding_model TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS knowledge_chunks (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), knowledge_base_id TEXT, file_id TEXT NOT NULL REFERENCES knowledge_files(id) ON DELETE CASCADE, content TEXT NOT NULL, title TEXT, section TEXT, page_number INTEGER, chunk_index INTEGER NOT NULL, embedding TEXT, embedding_provider TEXT, embedding_model TEXT, embedding_version TEXT, metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(file_id,chunk_index));
                CREATE TABLE IF NOT EXISTS asset_metadata (asset_id TEXT PRIMARY KEY REFERENCES assets(id), description TEXT NOT NULL DEFAULT '', visibility TEXT NOT NULL DEFAULT 'enterprise');
                CREATE TABLE IF NOT EXISTS agent_templates (id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, description TEXT NOT NULL, icon TEXT NOT NULL, status TEXT NOT NULL, default_runtime_profile TEXT NOT NULL, credit_cost INTEGER NOT NULL, skill_manifest TEXT NOT NULL, allows_image_generation INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS tenant_agent_instances (tenant_id TEXT NOT NULL REFERENCES tenants(id), agent_id TEXT NOT NULL REFERENCES agent_templates(id), status TEXT NOT NULL, PRIMARY KEY(tenant_id,agent_id));
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "avatar_storage_key" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN avatar_storage_key TEXT")
            if "avatar_mime_type" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN avatar_mime_type TEXT")
            file_columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_files)").fetchall()}
            for name, ddl in {"filename":"TEXT", "mime_type":"TEXT", "size_bytes":"INTEGER", "uploaded_by":"TEXT", "error_message":"TEXT", "parsed_text":"TEXT", "chunk_count":"INTEGER NOT NULL DEFAULT 0", "embedding_provider":"TEXT", "embedding_model":"TEXT"}.items():
                if name not in file_columns:
                    conn.execute(f"ALTER TABLE knowledge_files ADD COLUMN {name} {ddl}")
        self._seed_agent_catalog()

    def _seed_agent_catalog(self) -> None:
        with self._store.connection() as conn:
            for agent in CATALOG.values():
                conn.execute(
                    "INSERT INTO agent_templates(id,name,slug,description,icon,status,default_runtime_profile,credit_cost,skill_manifest,allows_image_generation) VALUES (?,?,?,?,?,'enabled','default',?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,slug=excluded.slug,description=excluded.description,icon=excluded.icon,credit_cost=excluded.credit_cost,allows_image_generation=excluded.allows_image_generation",
                    (agent.id, agent.name, agent.slug, agent.description, agent.icon, agent.credit_cost, json.dumps(agent.skill_manifest), agent.allows_image_generation),
                )
            tenants = conn.execute("SELECT id FROM tenants").fetchall()
            for tenant in tenants:
                for agent in CATALOG.values():
                    conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status) VALUES (?,?,'enabled') ON CONFLICT(tenant_id,agent_id) DO NOTHING", (tenant["id"], agent.id))

    def ensure_pgvector_schema(self, dimension: int) -> None:
        """Migrate legacy JSON embeddings only after a formal Provider is configured."""
        if not self._store.is_postgres or not 1 <= dimension <= 4096:
            raise ValueError("pgvector 维度配置无效。")
        with self._store.connection() as conn:
            installed = conn.execute("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector') AS installed").fetchone()["installed"]
            if not installed:
                raise RuntimeError("PostgreSQL 未启用 pgvector 扩展。")
            column = conn.execute("SELECT udt_name FROM information_schema.columns WHERE table_schema='public' AND table_name='knowledge_chunks' AND column_name='embedding'").fetchone()
            if column and column["udt_name"] != "vector":
                conn.execute("ALTER TABLE knowledge_chunks RENAME COLUMN embedding TO embedding_legacy")
                conn.execute(f"ALTER TABLE knowledge_chunks ADD COLUMN embedding vector({dimension})")
            elif not column:
                conn.execute(f"ALTER TABLE knowledge_chunks ADD COLUMN embedding vector({dimension})")
            conn.execute("ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS embedding_dimension INTEGER")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_tenant_model ON knowledge_chunks(tenant_id,embedding_provider,embedding_model,embedding_dimension)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding_hnsw ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)")

    def create_user(self, tenant_id: str, email: str, password_hash: str, display_name: str, role: str) -> None:
        with self._store.connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users(id,tenant_id,email,password_hash,display_name,role,created_at) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (str(uuid.uuid4()), tenant_id, email.lower(), password_hash, display_name, role),
            )
            conn.execute("INSERT OR IGNORE INTO credit_accounts(tenant_id,balance) VALUES (?, 200)", (tenant_id,))

    def user_by_email(self, email: str) -> dict | None:
        with self._store.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
        return dict(row) if row else None

    def user_by_id(self, user_id: str, tenant_id: str) -> dict | None:
        """Return only the signed-in user's own product profile."""
        with self._store.connection() as conn:
            row = conn.execute(
                "SELECT u.id, u.tenant_id, u.email, u.display_name, u.role, u.avatar_storage_key, u.avatar_mime_type, t.name AS tenant_name "
                "FROM users u JOIN tenants t ON t.id=u.tenant_id WHERE u.id=? AND u.tenant_id=?",
                (user_id, tenant_id),
            ).fetchone()
        return dict(row) if row else None

    def update_user_profile(self, user_id: str, tenant_id: str, display_name: str, email: str, avatar_storage_key: str | None = None, avatar_mime_type: str | None = None) -> dict | None:
        with self._store.connection() as conn:
            if avatar_storage_key:
                conn.execute(
                    "UPDATE users SET display_name=?, email=?, avatar_storage_key=?, avatar_mime_type=? WHERE id=? AND tenant_id=?",
                    (display_name, email.lower(), avatar_storage_key, avatar_mime_type, user_id, tenant_id),
                )
            else:
                conn.execute(
                    "UPDATE users SET display_name=?, email=? WHERE id=? AND tenant_id=?",
                    (display_name, email.lower(), user_id, tenant_id),
                )
        return self.user_by_id(user_id, tenant_id)

    def workspace(self, tenant_id: str, user_id: str) -> dict:
        with self._store.connection() as conn:
            tenant = conn.execute("SELECT name FROM tenants WHERE id=?", (tenant_id,)).fetchone()
            credit = conn.execute("SELECT balance FROM credit_accounts WHERE tenant_id=?", (tenant_id,)).fetchone()
        config = self._store.enterprise_config(tenant_id)
        return {"tenant_name": tenant["name"], "brand_name": config.get("brand_name"), "logo": config.get("logo"), "credit_balance": credit["balance"] if credit else 0, "agents": self.agents(tenant_id), "recent_conversations": self.conversations(tenant_id, user_id, 5), "recent_generations": self.generations(tenant_id, user_id, 5)}

    def agents(self, tenant_id: str) -> list[dict]:
        with self._store.connection() as conn:
            rows = conn.execute("SELECT t.id,t.name,t.slug,t.description,t.icon,t.status,t.default_runtime_profile,t.credit_cost,t.skill_manifest,t.allows_image_generation,i.status AS tenant_status FROM agent_templates t LEFT JOIN tenant_agent_instances i ON i.agent_id=t.id AND i.tenant_id=? ORDER BY t.id", (tenant_id,)).fetchall()
        return [{**dict(row), "enabled": dict(row).get("tenant_status") == "enabled"} for row in rows]

    def agent_enabled(self, tenant_id: str, agent_id: str) -> bool:
        get_agent(agent_id)
        with self._store.connection() as conn:
            row = conn.execute("SELECT status FROM tenant_agent_instances WHERE tenant_id=? AND agent_id=?", (tenant_id, agent_id)).fetchone()
        return bool(row and row["status"] == "enabled")

    def create_task(self, tenant_id: str, user_id: str, agent_id: str, text: str, conversation_id: str | None) -> dict:
        with self._store.connection() as conn:
            credit = conn.execute("SELECT balance FROM credit_accounts WHERE tenant_id=?", (tenant_id,)).fetchone()
            agent = get_agent(agent_id)
            instance = conn.execute("SELECT status FROM tenant_agent_instances WHERE tenant_id=? AND agent_id=?", (tenant_id, agent_id)).fetchone()
            if not instance or instance["status"] != "enabled":
                raise LookupError("该智能体尚未为当前企业启用。")
            if not credit or credit["balance"] < agent.credit_cost:
                raise ValueError("insufficient_credit")
            if conversation_id:
                owner = conn.execute("SELECT o.user_id,c.agent_id FROM conversation_owners o JOIN conversations c ON c.id=o.conversation_id WHERE o.conversation_id=? AND o.deleted_at IS NULL AND c.tenant_id=?", (conversation_id, tenant_id)).fetchone()
                if not owner or owner["user_id"] != user_id:
                    raise LookupError("会话不存在或不属于当前用户。")
                if owner["agent_id"] != agent_id:
                    raise ValueError("不能跨智能体复用会话。")
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

    def attach_conversation(self, conversation_id: str, user_id: str, title: str = "新会话") -> None:
        with self._store.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO conversation_owners(conversation_id,user_id,title) VALUES (?,?,?)", (conversation_id,user_id,title))

    def add_message(self, conversation_id: str, role: str, content: str, *, message_id: str | None = None) -> str:
        """Persist user-visible chat content only; never persist hidden reasoning."""
        if role not in {"user", "assistant"}:
            raise ValueError("不支持的消息角色。")
        message_id = message_id or str(uuid.uuid4())
        with self._store.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO messages(id,conversation_id,role,content,created_at) VALUES (?,?,?,?,?)", (message_id, conversation_id, role, content[:12000], datetime.now(timezone.utc).isoformat()))
        return message_id

    def complete_task_success(
        self,
        task: dict,
        *,
        run_id: str,
        conversation_id: str,
        response: str,
        trace_payload: dict,
        image_storage_key: str | None = None,
    ) -> dict:
        """Atomically persist a successful product result and its final Trace.

        Deterministic record IDs and task-scoped charging make replay safe after
        a worker interruption. Provider execution is deliberately outside this
        method, so a persistence retry never invokes the model or image tool.
        """
        task_id, tenant_id, user_id = task["id"], task["tenant_id"], task["user_id"]
        assistant_message_id = f"task:{task_id}:assistant"
        user_message_id = f"task:{task_id}:user"
        generation_id = f"task:{task_id}:generation" if image_storage_key else None
        stage = "task_validation"
        try:
            with self._store.connection() as conn:
                current = conn.execute(
                    "SELECT status,agent_id FROM tasks WHERE id=? AND tenant_id=? AND user_id=?",
                    (task_id, tenant_id, user_id),
                ).fetchone()
                if not current:
                    raise LookupError("任务不存在。")
                if current["status"] == "completed":
                    return {
                        "assistant_message_id": assistant_message_id,
                        "generation_id": generation_id,
                        "replayed": True,
                    }

                stage = "conversation_owner"
                conn.execute(
                    "INSERT OR IGNORE INTO conversation_owners(conversation_id,user_id,title) VALUES (?,?,'新会话')",
                    (conversation_id, user_id),
                )
                if not task.get("conversation_id"):
                    stage = "user_message"
                    conn.execute(
                        "INSERT OR IGNORE INTO messages(id,conversation_id,role,content,created_at) VALUES (?,?,'user',?,?)",
                        (user_message_id, conversation_id, task["input_text"][:12000], datetime.now(timezone.utc).isoformat()),
                    )

                stage = "assistant_message"
                conn.execute(
                    "INSERT OR IGNORE INTO messages(id,conversation_id,role,content,created_at) VALUES (?,?,'assistant',?,?)",
                    (assistant_message_id, conversation_id, response[:12000], datetime.now(timezone.utc).isoformat()),
                )

                stage = "artifact_association"
                if get_agent(current["agent_id"]).allows_image_generation:
                    if not image_storage_key:
                        raise ValueError("图片任务缺少已持久化 storage key。")
                    conn.execute(
                        "INSERT OR IGNORE INTO generations(id,tenant_id,user_id,conversation_id,task_id,provider,model,storage_key,mime_type) VALUES (?,?,?,?,?,?,?,?,?)",
                        (generation_id, tenant_id, user_id, conversation_id, task_id, "image-gateway", "gateway-managed-gpt-image-2", image_storage_key, "image/png"),
                    )

                stage = "task_result"
                result_json = json.dumps(
                    {"run_id": run_id, "assistant_message_id": assistant_message_id, "generation_id": generation_id},
                    ensure_ascii=False,
                )
                conn.execute(
                    "INSERT INTO task_results(task_id,final_response,result_json) VALUES (?,?,?) "
                    "ON CONFLICT(task_id) DO UPDATE SET final_response=excluded.final_response,result_json=excluded.result_json",
                    (task_id, response, result_json),
                )

                stage = "credit_charge"
                charged = conn.execute("SELECT 1 FROM credit_transactions WHERE task_id=?", (task_id,)).fetchone()
                if not charged:
                    amount = get_agent(current["agent_id"]).credit_cost
                    updated = conn.execute(
                        "UPDATE credit_accounts SET balance=balance-?,updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND balance>=?",
                        (amount, tenant_id, amount),
                    )
                    if updated.rowcount != 1:
                        raise ValueError("任务完成时积分余额不足。")
                    conn.execute(
                        "INSERT INTO credit_transactions(id,tenant_id,user_id,task_id,amount,reason) VALUES (?,?,?,?,?,?)",
                        (f"task:{task_id}:charge", tenant_id, user_id, task_id, -amount, get_agent(current["agent_id"]).slug),
                    )

                stage = "run_trace"
                completed_trace = dict(trace_payload)
                completed_trace.update(
                    {
                        "status": "completed",
                        "partial_output": False,
                        "assistant_message_saved": True,
                        "assistant_message_id": assistant_message_id,
                        "result_persistence_status": "completed",
                        "result_persistence_error_stage": None,
                        "artifact_saved": not get_agent(current["agent_id"]).allows_image_generation or bool(generation_id),
                        "generation_id": generation_id,
                        "error": None,
                    }
                )
                updated_trace = conn.execute(
                    "UPDATE run_traces SET status='completed',payload=?,completed_at=CURRENT_TIMESTAMP WHERE run_id=? AND tenant_id=?",
                    (json.dumps(completed_trace, ensure_ascii=False), run_id, tenant_id),
                )
                if updated_trace.rowcount != 1:
                    raise LookupError("Run Trace 不存在。")

                stage = "task_status"
                conn.execute(
                    "UPDATE tasks SET status='completed',stage='completed',run_id=?,error_code=NULL,conversation_id=?,completed_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=?",
                    (run_id, conversation_id, task_id, tenant_id),
                )
                conn.execute("INSERT INTO task_events(task_id,stage,message) VALUES (?,'completed','任务已完成，正文已返回')", (task_id,))
        except Exception as exc:
            if isinstance(exc, ResultPersistenceError):
                raise
            raise ResultPersistenceError(stage) from exc
        return {
            "assistant_message_id": assistant_message_id,
            "generation_id": generation_id,
            "replayed": False,
        }

    def conversation_detail(self, tenant_id: str, user_id: str, conversation_id: str) -> dict | None:
        with self._store.connection() as conn:
            conversation = conn.execute(
                "SELECT c.id,c.agent_id,c.runtime_thread_id,c.created_at,o.title FROM conversations c JOIN conversation_owners o ON o.conversation_id=c.id WHERE c.id=? AND c.tenant_id=? AND o.user_id=? AND o.deleted_at IS NULL",
                (conversation_id, tenant_id, user_id),
            ).fetchone()
            if not conversation:
                return None
            messages = conn.execute("SELECT id,role,content,created_at FROM messages WHERE conversation_id=? ORDER BY created_at,id", (conversation_id,)).fetchall()
            if not messages:
                # Conversations created before message persistence still have a
                # task input/result audit trail. Expose it as read-only history
                # so those users can inspect context and safely resume the same
                # Codex thread without manufacturing any missing content.
                legacy = conn.execute(
                    "SELECT t.id,t.input_text,t.created_at,r.final_response,t.completed_at "
                    "FROM tasks t LEFT JOIN task_results r ON r.task_id=t.id "
                    "WHERE t.conversation_id=? AND t.tenant_id=? AND t.user_id=? "
                    "ORDER BY t.created_at,t.id",
                    (conversation_id, tenant_id, user_id),
                ).fetchall()
                restored: list[dict] = []
                for task in legacy:
                    restored.append({"id": f"legacy-user-{task['id']}", "role": "user", "content": task["input_text"], "created_at": task["created_at"]})
                    if task["final_response"]:
                        restored.append({"id": f"legacy-assistant-{task['id']}", "role": "assistant", "content": task["final_response"], "created_at": task["completed_at"] or task["created_at"]})
                messages = restored
            runs = conn.execute("SELECT run_id,status,created_at,completed_at,payload FROM run_traces WHERE conversation_id=? AND tenant_id=? ORDER BY created_at", (conversation_id, tenant_id)).fetchall()
        result = dict(conversation)
        result["messages"] = [dict(item) for item in messages]
        result["runs"] = [{"run_id": item["run_id"], "status": item["status"], "created_at": item["created_at"], "completed_at": item["completed_at"]} for item in runs]
        return result

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

    def task_events_since(self, task_id: str, tenant_id: str, user_id: str, after_id: int = 0) -> list[dict]:
        with self._store.connection() as conn:
            rows = conn.execute(
                "SELECT e.id,e.stage,e.message,e.created_at FROM task_events e JOIN tasks t ON t.id=e.task_id WHERE e.task_id=? AND t.tenant_id=? AND t.user_id=? AND e.id>? ORDER BY e.id",
                (task_id, tenant_id, user_id, after_id),
            ).fetchall()
        return [dict(row) for row in rows]

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

    def create_generation(self, tenant_id: str, user_id: str, conversation_id: str, task_id: str, storage_key: str, provider: str, model: str) -> str:
        generation_id = f"task:{task_id}:generation"
        with self._store.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO generations(id,tenant_id,user_id,conversation_id,task_id,provider,model,storage_key,mime_type) VALUES (?,?,?,?,?,?,?,?,?)", (generation_id,tenant_id,user_id,conversation_id,task_id,provider,model,storage_key,"image/png"))
        return generation_id

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

    def create_knowledge_file(self, tenant_id: str, user_id: str, filename: str, mime_type: str, size_bytes: int, storage_key: str, knowledge_base_id: str | None = None) -> dict:
        file_id = str(uuid.uuid4())
        with self._store.connection() as conn:
            if knowledge_base_id:
                base = conn.execute("SELECT id FROM knowledge_bases WHERE id=? AND tenant_id=?", (knowledge_base_id, tenant_id)).fetchone()
                if not base:
                    raise ValueError("知识库不存在或不属于当前企业。")
            else:
                base = conn.execute("SELECT id FROM knowledge_bases WHERE tenant_id=? ORDER BY created_at,id LIMIT 1", (tenant_id,)).fetchone()
                if not base:
                    knowledge_base_id = str(uuid.uuid4())
                    conn.execute("INSERT INTO knowledge_bases(id,tenant_id,name) VALUES (?,?,?)", (knowledge_base_id, tenant_id, "企业知识库"))
                else:
                    knowledge_base_id = base["id"]
            conn.execute("INSERT INTO knowledge_files(id,tenant_id,knowledge_base_id,name,filename,mime_type,size_bytes,uploaded_by,status,storage_key) VALUES (?,?,?,?,?,?,?,?,?,?)", (file_id, tenant_id, knowledge_base_id, filename[:180], filename[:180], mime_type, size_bytes, user_id, "uploaded", storage_key))
        return {"file_id": file_id, "knowledge_base_id": knowledge_base_id, "filename": filename, "status": "uploaded"}

    def knowledge_file(self, tenant_id: str, file_id: str) -> dict | None:
        with self._store.connection() as conn:
            row = conn.execute("SELECT * FROM knowledge_files WHERE id=? AND tenant_id=?", (file_id, tenant_id)).fetchone()
        return dict(row) if row else None

    def set_knowledge_file_status(self, tenant_id: str, file_id: str, status: str, *, error_message: str | None = None, parsed_text: str | None = None, chunk_count: int | None = None, embedding_provider: str | None = None, embedding_model: str | None = None) -> None:
        allowed = {"uploaded", "queued", "parsing", "chunking", "embedding", "indexing", "ready", "failed"}
        if status not in allowed:
            raise ValueError("无效知识文件状态。")
        with self._store.connection() as conn:
            conn.execute("UPDATE knowledge_files SET status=?,error_message=?,parsed_text=COALESCE(?,parsed_text),chunk_count=COALESCE(?,chunk_count),embedding_provider=COALESCE(?,embedding_provider),embedding_model=COALESCE(?,embedding_model) WHERE id=? AND tenant_id=?", (status, error_message, parsed_text, chunk_count, embedding_provider, embedding_model, file_id, tenant_id))

    def replace_knowledge_chunks(self, tenant_id: str, file_id: str, chunks: list[dict]) -> None:
        with self._store.connection() as conn:
            conn.execute("DELETE FROM knowledge_chunks WHERE tenant_id=? AND file_id=?", (tenant_id, file_id))
            for chunk in chunks:
                vector = chunk.get("embedding")
                embedding = "[" + ",".join(f"{value:.10g}" for value in vector) + "]" if self._store.is_postgres and vector else json.dumps(vector)
                if self._store.is_postgres:
                    conn.execute("INSERT INTO knowledge_chunks(id,tenant_id,knowledge_base_id,file_id,content,title,section,page_number,chunk_index,embedding,embedding_provider,embedding_model,embedding_dimension,embedding_version,metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), tenant_id, chunk.get("knowledge_base_id"), file_id, chunk["content"], chunk.get("title"), chunk.get("section"), chunk.get("page_number"), chunk["chunk_index"], embedding, chunk.get("embedding_provider"), chunk.get("embedding_model"), len(vector or []), chunk.get("embedding_version"), json.dumps(chunk.get("metadata", {}), ensure_ascii=False)))
                else:
                    conn.execute("INSERT INTO knowledge_chunks(id,tenant_id,knowledge_base_id,file_id,content,title,section,page_number,chunk_index,embedding,embedding_provider,embedding_model,embedding_version,metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), tenant_id, chunk.get("knowledge_base_id"), file_id, chunk["content"], chunk.get("title"), chunk.get("section"), chunk.get("page_number"), chunk["chunk_index"], embedding, chunk.get("embedding_provider"), chunk.get("embedding_model"), chunk.get("embedding_version"), json.dumps(chunk.get("metadata", {}), ensure_ascii=False)))

    def knowledge_chunks(self, tenant_id: str, file_id: str) -> list[dict]:
        with self._store.connection() as conn:
            rows = conn.execute("SELECT id,content,title,section,page_number,chunk_index,embedding,metadata FROM knowledge_chunks WHERE tenant_id=? AND file_id=? ORDER BY chunk_index", (tenant_id, file_id)).fetchall()
        return [dict(row) for row in rows]

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

    def charge_success(self, tenant_id: str, user_id: str, task_id: str) -> None:
        with self._store.connection() as conn:
            task = conn.execute("SELECT agent_id FROM tasks WHERE id=? AND tenant_id=? AND user_id=?", (task_id, tenant_id, user_id)).fetchone()
            if not task:
                return
            agent = get_agent(task["agent_id"])
            if conn.execute("SELECT 1 FROM credit_transactions WHERE task_id=?",(task_id,)).fetchone(): return
            amount = agent.credit_cost
            conn.execute("UPDATE credit_accounts SET balance=balance-?,updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND balance>=?",(amount,tenant_id,amount))
            conn.execute("INSERT INTO credit_transactions(id,tenant_id,user_id,task_id,amount,reason) VALUES (?,?,?,?,?,?)",(str(uuid.uuid4()),tenant_id,user_id,task_id,-amount,agent.slug))

    def recoverable_tasks(self) -> list[dict]:
        with self._store.connection() as conn:
            conn.execute("UPDATE tasks SET status='queued',stage='queued',user_message='服务已恢复，任务重新排队' WHERE status IN ('queued','running')")
            rows=conn.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]
