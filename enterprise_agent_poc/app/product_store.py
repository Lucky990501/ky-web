"""Product tables layered over the Gate 2 store. SQLite remains the local adapter."""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from app.agent_catalog import CATALOG, get_agent
from app.store import POCStore


class ResultPersistenceError(RuntimeError):
    def __init__(self, stage: str) -> None:
        super().__init__(f"运行结果持久化失败（stage={stage}）。")
        self.stage = stage


class TaskCancellationRequested(RuntimeError):
    """The cancellation transition won the task finalization race."""


_GENERIC_CONVERSATION_TITLES = {"", "新会话", "新图片会话"}
_PROJECT_TYPES = {
    "image-agent": "图片生成项目",
    "copywriting-agent": "文案创作项目",
    "campaign-agent": "活动策划项目",
}
_SAFE_ACTIVITY_LABELS = {
    "queued": "任务已进入队列",
    "context_loading": "正在加载执行上下文",
    "enterprise_config_loading": "正在加载企业配置",
    "knowledge_retrieving": "正在检索企业知识",
    "asset_retrieving": "正在查找企业素材",
    "tool_running": "正在调用工具",
    "generating": "正在生成回答",
    "full_plan_generating": "正在生成活动方案",
    "structured_validating": "正在校验方案结构",
    "semantic_validating": "正在校验方案内容",
    "semantic_correcting": "正在优化待确认内容",
    "result_rendering": "正在整理最终结果",
    "grounded_drafting": "正在准备可核实的文稿",
    "grounding_auditing": "正在核对事实依据",
    "grounding_correcting": "正在修正未获支持的表述",
    "grounding_revalidating": "正在复核修订内容",
    "grounded_rendering": "正在整理核实后的结果",
    "persisting": "正在保存结果",
    "completed": "已完成",
    "failed": "执行失败",
    "cancelled": "已停止生成",
}


def _project_title(agent_id: str, prompt: str | None) -> str:
    clean = " ".join(str(prompt or "").split())
    if not clean:
        return _PROJECT_TYPES.get(agent_id, "智能创作项目")
    return clean if len(clean) <= 36 else f"{clean[:36]}…"


def _agent_view(agent_id: str) -> dict:
    try:
        agent = get_agent(agent_id)
        return {"id": agent.id, "name": agent.name, "icon": agent.icon}
    except LookupError:
        return {"id": agent_id, "name": "历史智能体", "icon": "bot"}


def _project_type(agent: dict) -> str:
    return _PROJECT_TYPES.get(agent["id"], "历史创作项目" if agent["name"] == "历史智能体" else f"{agent['name']}项目")


def _image_mime_type(storage_key: str) -> str:
    suffix = storage_key.lower().rsplit(".", 1)[-1]
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(
        suffix, "image/png"
    )


def _generation_view(item: dict | None) -> dict | None:
    if not item:
        return None
    result = dict(item)
    storage_key = str(result.get("storage_key") or "").strip()
    result["available"] = bool(storage_key)
    if not storage_key:
        result["content_url"] = None
        result["image_url"] = None
        return result
    content_url = f"/api/v1/storage/{quote(storage_key, safe='/')}"
    result["content_url"] = content_url
    result["image_url"] = content_url
    if result.get("width") and result.get("height"):
        result["actual_size"] = f"{result['width']}x{result['height']}"
    return result


def _chat_image_attachment_view(item: dict) -> dict:
    attachment_id = str(item["id"])
    return {
        "type": "image", "id": attachment_id,
        "filename": item["filename"], "mime_type": item["mime_type"],
        "width": item["width"], "height": item["height"],
        "size_bytes": item["size_bytes"],
        "content_url": f"/api/v1/chat-images/{quote(attachment_id, safe='')}",
    }


class ProductStore:
    def __init__(self, store: POCStore) -> None:
        self._store = store
        self.execution_resolver = None

    def _history_agent(self, conversation_id: str, tenant_id: str, agent_id: str) -> dict:
        agent = _agent_view(agent_id)
        if agent_id in CATALOG or not self.execution_resolver or not conversation_id:
            return agent
        with self._store.connection() as conn:
            context = conn.execute(
                "SELECT c.tool_policy_snapshot,c.skill_manifest_snapshot,c.credit_cost,c.output_policy,t.slug FROM conversation_agent_contexts m "
                "JOIN agent_execution_contexts c ON c.id=m.context_id "
                "JOIN agent_templates t ON t.id=c.agent_id "
                "WHERE m.conversation_id=? AND c.tenant_id=? AND c.agent_id=?",
                (conversation_id, tenant_id, agent_id),
            ).fetchone()
        if context:
            display = json.loads(context["tool_policy_snapshot"])["display"]
            return {"id": agent_id, "slug":context['slug'],"conversation_path":f"/agents/{context['slug']}", "name": display["name"], "icon": display["icon"],
                    "skill_manifest": context["skill_manifest_snapshot"], "credit_cost": context["credit_cost"],
                    "output_policy": context["output_policy"]}
        return agent

    def task_definition(self, task: dict, conn=None):
        if task["agent_id"] in CATALOG:
            return get_agent(task["agent_id"])
        if not self.execution_resolver:
            raise LookupError("Execution resolver unavailable")
        from app.agent_execution import definition
        if conn is not None:
            return definition(self.execution_resolver.task_context(conn, task))
        with self._store.connection() as connection:
            return definition(self.execution_resolver.task_context(connection, task))

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
                CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('enterprise_admin','member')), account_status TEXT NOT NULL DEFAULT 'enabled' CHECK(account_status IN ('enabled','disabled')), avatar_storage_key TEXT, avatar_mime_type TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS credit_accounts (tenant_id TEXT PRIMARY KEY REFERENCES tenants(id), balance INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS credit_transactions (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT REFERENCES users(id), task_id TEXT, amount INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT NOT NULL REFERENCES users(id), agent_id TEXT NOT NULL, conversation_id TEXT, input_text TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled')), stage TEXT, error_code TEXT, user_message TEXT, run_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, started_at TEXT, completed_at TEXT);
                CREATE TABLE IF NOT EXISTS task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id), stage TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS task_results (task_id TEXT PRIMARY KEY REFERENCES tasks(id), final_response TEXT, result_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS conversation_owners (conversation_id TEXT PRIMARY KEY REFERENCES conversations(id), user_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL DEFAULT '新图片会话', deleted_at TEXT);
                CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS generations (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT NOT NULL REFERENCES users(id), conversation_id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id), provider TEXT, model TEXT, storage_key TEXT, mime_type TEXT, width INTEGER, height INTEGER, requested_size TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, deleted_at TEXT);
                CREATE TABLE IF NOT EXISTS chat_image_attachments (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), user_id TEXT NOT NULL REFERENCES users(id), storage_key TEXT NOT NULL UNIQUE, filename TEXT NOT NULL, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, task_id TEXT UNIQUE REFERENCES tasks(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS idx_chat_image_attachments_owner ON chat_image_attachments(tenant_id,user_id,id);
                CREATE TABLE IF NOT EXISTS knowledge_bases (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), name TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS knowledge_files (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), knowledge_base_id TEXT REFERENCES knowledge_bases(id), name TEXT NOT NULL, filename TEXT, mime_type TEXT, size_bytes INTEGER, uploaded_by TEXT, status TEXT NOT NULL, storage_key TEXT, error_message TEXT, parsed_text TEXT, chunk_count INTEGER NOT NULL DEFAULT 0, embedding_provider TEXT, embedding_model TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS knowledge_chunks (id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id), knowledge_base_id TEXT, file_id TEXT NOT NULL REFERENCES knowledge_files(id) ON DELETE CASCADE, content TEXT NOT NULL, title TEXT, section TEXT, page_number INTEGER, chunk_index INTEGER NOT NULL, embedding TEXT, embedding_provider TEXT, embedding_model TEXT, embedding_version TEXT, metadata TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(file_id,chunk_index));
                CREATE TABLE IF NOT EXISTS asset_metadata (asset_id TEXT PRIMARY KEY REFERENCES assets(id), description TEXT NOT NULL DEFAULT '', visibility TEXT NOT NULL DEFAULT 'enterprise');
                CREATE TABLE IF NOT EXISTS agent_templates (id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, description TEXT NOT NULL, icon TEXT NOT NULL, status TEXT NOT NULL, default_runtime_profile TEXT NOT NULL, credit_cost INTEGER NOT NULL, skill_manifest TEXT NOT NULL, allows_image_generation INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS tenant_agent_instances (tenant_id TEXT NOT NULL REFERENCES tenants(id), agent_id TEXT NOT NULL REFERENCES agent_templates(id), status TEXT NOT NULL, PRIMARY KEY(tenant_id,agent_id));
                CREATE INDEX IF NOT EXISTS idx_tasks_history ON tasks(tenant_id,user_id,conversation_id,created_at,id);
                CREATE INDEX IF NOT EXISTS idx_generations_history ON generations(tenant_id,user_id,conversation_id,created_at,id);
                CREATE INDEX IF NOT EXISTS idx_generations_storage ON generations(tenant_id,storage_key);
                CREATE INDEX IF NOT EXISTS idx_assets_tenant_url ON assets(tenant_id,url);
                CREATE INDEX IF NOT EXISTS idx_conversation_owners_history ON conversation_owners(user_id,deleted_at,conversation_id);
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "avatar_storage_key" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN avatar_storage_key TEXT")
            if "avatar_mime_type" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN avatar_mime_type TEXT")
            if "account_status" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN account_status TEXT NOT NULL DEFAULT 'enabled' CHECK(account_status IN ('enabled','disabled'))")
            file_columns = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_files)").fetchall()}
            for name, ddl in {"filename":"TEXT", "mime_type":"TEXT", "size_bytes":"INTEGER", "uploaded_by":"TEXT", "error_message":"TEXT", "parsed_text":"TEXT", "chunk_count":"INTEGER NOT NULL DEFAULT 0", "embedding_provider":"TEXT", "embedding_model":"TEXT"}.items():
                if name not in file_columns:
                    conn.execute(f"ALTER TABLE knowledge_files ADD COLUMN {name} {ddl}")
            generation_columns = {row["name"] for row in conn.execute("PRAGMA table_info(generations)").fetchall()}
            if "requested_size" not in generation_columns:
                conn.execute("ALTER TABLE generations ADD COLUMN requested_size TEXT")
        self._seed_agent_catalog()

    def create_chat_image_attachment(self, tenant_id: str, user_id: str, storage_key: str,
                                     filename: str, mime_type: str, size_bytes: int,
                                     width: int, height: int) -> dict:
        attachment_id = str(uuid.uuid4())
        with self._store.connection() as conn:
            conn.execute(
                "INSERT INTO chat_image_attachments(id,tenant_id,user_id,storage_key,filename,mime_type,size_bytes,width,height) VALUES (?,?,?,?,?,?,?,?,?)",
                (attachment_id, tenant_id, user_id, storage_key, filename[:180], mime_type, size_bytes, width, height),
            )
        return self.chat_image_attachment(tenant_id, user_id, attachment_id) or {}

    def public_chat_image_attachment(self, tenant_id: str, user_id: str, attachment_id: str) -> dict | None:
        record = self.chat_image_attachment(tenant_id, user_id, attachment_id)
        return _chat_image_attachment_view(record) if record else None

    def chat_image_attachment(self, tenant_id: str, user_id: str, attachment_id: str) -> dict | None:
        with self._store.connection() as conn:
            row = conn.execute(
                "SELECT * FROM chat_image_attachments WHERE id=? AND tenant_id=? AND user_id=?",
                (attachment_id, tenant_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def task_chat_image_attachment(self, task_id: str, tenant_id: str) -> dict | None:
        with self._store.connection() as conn:
            row = conn.execute(
                "SELECT a.* FROM chat_image_attachments a JOIN tasks t ON t.id=a.task_id "
                "WHERE t.id=? AND t.tenant_id=? AND t.agent_id='image-agent' AND a.tenant_id=t.tenant_id AND a.user_id=t.user_id",
                (task_id, tenant_id),
            ).fetchone()
        return dict(row) if row else None

    def remove_chat_image_attachment(self, tenant_id: str, user_id: str, attachment_id: str) -> str | None:
        with self._store.connection() as conn:
            row = conn.execute(
                "SELECT storage_key FROM chat_image_attachments WHERE id=? AND tenant_id=? AND user_id=? AND task_id IS NULL",
                (attachment_id, tenant_id, user_id),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM chat_image_attachments WHERE id=? AND tenant_id=? AND user_id=? AND task_id IS NULL",
                             (attachment_id, tenant_id, user_id))
        return row["storage_key"] if row else None

    def _seed_agent_catalog(self) -> None:
        with self._store.connection() as conn:
            from app.test_tenant_seeding import authorized_exclusions
            excluded = authorized_exclusions(conn, self._store)
            # Pre-008 adapters remain compatible. Never overwrite a productized
            # definition; converting the three legacy identities is NOT Stage 1.
            if self._store.is_postgres:
                has_source = conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='agent_templates' AND column_name='definition_source'").fetchone() is not None
            else:
                has_source = "definition_source" in {r["name"] for r in conn.execute("PRAGMA table_info(agent_templates)")}
            legacy_guard = " WHERE agent_templates.definition_source='legacy'" if has_source else ""
            for agent in CATALOG.values():
                conn.execute(
                    "INSERT INTO agent_templates(id,name,slug,description,icon,status,default_runtime_profile,credit_cost,skill_manifest,allows_image_generation) VALUES (?,?,?,?,?,'enabled','default',?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,slug=excluded.slug,description=excluded.description,icon=excluded.icon,credit_cost=excluded.credit_cost,allows_image_generation=excluded.allows_image_generation" + legacy_guard,
                    (agent.id, agent.name, agent.slug, agent.description, agent.icon, agent.credit_cost, json.dumps(agent.skill_manifest), agent.allows_image_generation),
                )
            tenants = conn.execute("SELECT id FROM tenants").fetchall()
            seedable_ids = {r["id"] for r in conn.execute("SELECT id FROM agent_templates WHERE definition_source='legacy'")} if has_source else set(CATALOG)
            for tenant in tenants:
                if tenant['id'] in excluded:
                    continue
                for agent in CATALOG.values():
                    if agent.id not in seedable_ids:
                        continue
                    conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status) VALUES (?,?,'enabled') ON CONFLICT(tenant_id,agent_id) DO NOTHING", (tenant["id"], agent.id))

    def knowledge_embedding_schema(self, conn=None) -> dict:
        """Read the existing pgvector contract; never repair schema on startup/read."""
        if conn is None:
            with self._store.connection() as connection:
                return self.knowledge_embedding_schema(connection)
        row = conn.execute(
            "SELECT t.typname AS type,a.atttypmod AS dimension FROM pg_attribute a "
            "JOIN pg_type t ON t.oid=a.atttypid "
            "WHERE a.attrelid=to_regclass('public.knowledge_chunks') "
            "AND a.attname='embedding' AND NOT a.attisdropped"
        ).fetchone()
        dimension_column = conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name='knowledge_chunks' AND column_name='embedding_dimension'"
        ).fetchone() is not None
        return {"type": row["type"] if row else None,
                "dimension": row["dimension"] if row else None,
                "dimension_column": dimension_column}

    def require_knowledge_embedding_dimension(self, dimension: int, conn=None) -> dict:
        schema = self.knowledge_embedding_schema(conn)
        if schema["type"] != "vector" or schema["dimension"] != dimension or dimension < 1:
            raise RuntimeError("KNOWLEDGE_EMBEDDING_SCHEMA_DIMENSION_MISMATCH")
        return schema

    def ensure_pgvector_schema(self, dimension: int) -> None:
        """Migrate legacy JSON embeddings only after a formal Provider is configured."""
        if not self._store.is_postgres or not 1 <= dimension <= 4096:
            raise ValueError("pgvector 维度配置无效。")
        with self._store.connection() as conn:
            installed = conn.execute("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector') AS installed").fetchone()["installed"]
            if not installed:
                raise RuntimeError("PostgreSQL 未启用 pgvector 扩展。")
            column = conn.execute("SELECT udt_name FROM information_schema.columns WHERE table_schema='public' AND table_name='knowledge_chunks' AND column_name='embedding'").fetchone()
            if column and column["udt_name"] == "vector":
                self.require_knowledge_embedding_dimension(dimension, conn)
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

    def members(self, tenant_id: str) -> list[dict]:
        """Return the tenant's identities without credential material."""
        with self._store.connection() as conn:
            rows = conn.execute(
                "SELECT id,email,display_name,role,account_status AS status,created_at FROM users WHERE tenant_id=? ORDER BY created_at,id",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _lock_member(self, conn, tenant_id: str, user_id: str):
        suffix = " FOR UPDATE" if self._store.is_postgres else ""
        return conn.execute(
            "SELECT id,role,account_status FROM users WHERE id=? AND tenant_id=?" + suffix,
            (user_id, tenant_id),
        ).fetchone()

    def _begin_member_change(self, conn, tenant_id: str) -> None:
        if self._store.is_postgres:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (tenant_id,))
        else:
            conn.execute("BEGIN IMMEDIATE")

    def _require_another_enabled_admin(self, conn, tenant_id: str, user_id: str) -> None:
        suffix = " FOR UPDATE" if self._store.is_postgres else ""
        rows = conn.execute(
            "SELECT id FROM users WHERE tenant_id=? AND role='enterprise_admin' AND account_status='enabled' ORDER BY id" + suffix,
            (tenant_id,),
        ).fetchall()
        if not any(row["id"] != user_id for row in rows):
            raise ValueError("last_enabled_enterprise_admin")

    def update_member_role(self, tenant_id: str, user_id: str, role: str) -> dict | None:
        if role not in {"member", "enterprise_admin"}:
            raise ValueError("invalid_role")
        with self._store.connection() as conn:
            self._begin_member_change(conn, tenant_id)
            row = self._lock_member(conn, tenant_id, user_id)
            if not row:
                return None
            if row["role"] == "enterprise_admin" and row["account_status"] == "enabled" and role != "enterprise_admin":
                self._require_another_enabled_admin(conn, tenant_id, user_id)
            conn.execute("UPDATE users SET role=? WHERE id=? AND tenant_id=?", (role, user_id, tenant_id))
        return next((member for member in self.members(tenant_id) if member["id"] == user_id), None)

    def update_member_status(self, tenant_id: str, user_id: str, status: str) -> dict | None:
        if status not in {"enabled", "disabled"}:
            raise ValueError("invalid_account_status")
        with self._store.connection() as conn:
            self._begin_member_change(conn, tenant_id)
            row = self._lock_member(conn, tenant_id, user_id)
            if not row:
                return None
            if status == "disabled" and row["account_status"] == "enabled" and row["role"] == "enterprise_admin":
                self._require_another_enabled_admin(conn, tenant_id, user_id)
            conn.execute("UPDATE users SET account_status=? WHERE id=? AND tenant_id=?", (status, user_id, tenant_id))
        return next((member for member in self.members(tenant_id) if member["id"] == user_id), None)

    def update_password_hash(self, tenant_id: str, user_id: str, password_hash: str) -> bool:
        with self._store.connection() as conn:
            cursor = conn.execute(
                "UPDATE users SET password_hash=? WHERE id=? AND tenant_id=?",
                (password_hash, user_id, tenant_id),
            )
        return bool(cursor.rowcount)

    def user_by_email(self, email: str) -> dict | None:
        with self._store.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
        return dict(row) if row else None

    def user_by_id(self, user_id: str, tenant_id: str) -> dict | None:
        """Return only the signed-in user's own product profile."""
        with self._store.connection() as conn:
            row = conn.execute(
                "SELECT u.id, u.tenant_id, u.email, u.display_name, u.role, u.account_status, u.avatar_storage_key, u.avatar_mime_type, t.name AS tenant_name "
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
            # Stage 1 catalog drafts / configured instances are not runnable.
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(agent_templates)")} if not self._store.is_postgres else {r["column_name"] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='agent_templates'")}
            category = "COALESCE(t.category,'general')" if "category" in columns else "'general'"
            rows = conn.execute(f"SELECT t.id,t.name,t.slug,t.description,t.icon,{category} AS category,t.status,t.default_runtime_profile,t.credit_cost,t.skill_manifest,t.allows_image_generation,i.status AS tenant_status FROM agent_templates t LEFT JOIN tenant_agent_instances i ON i.agent_id=t.id AND i.tenant_id=? WHERE t.id IN (?,?,?) ORDER BY t.id", (tenant_id, *CATALOG)).fetchall()
        result = [{**dict(row), "enabled": dict(row).get("tenant_status") == "enabled"} for row in rows]
        if self.execution_resolver:
            with self._store.connection() as conn:
                if "definition_source" in columns:
                    candidates = conn.execute("SELECT v.*,i.overrides_json,t.slug AS public_slug FROM agent_templates t JOIN tenant_agent_instances i ON i.agent_id=t.id JOIN agent_template_versions v ON v.id=i.agent_template_version_id WHERE i.tenant_id=? AND i.status='enabled' AND t.definition_source='productized' AND v.status='published' AND t.lifecycle_status='published' AND t.current_published_version_id=v.id", (tenant_id,)).fetchall()
                    for row in candidates:
                        v = dict(row)
                        try:
                            from app.agent_availability import tenant_available
                            if not tenant_available(conn, tenant_id, v["agent_template_id"], postgres=self._store.is_postgres):
                                continue
                            if not self.execution_resolver._runtime_passed(conn, v):
                                continue
                            self.execution_resolver._ready(conn, tenant_id, v)
                            manifest, _ = self.execution_resolver._skills(conn, v["id"])
                            overrides = json.loads(v["overrides_json"] or "{}")
                            result.append({"id": v["agent_template_id"], "name": overrides.get("display_name") or v["name"], "description": v["description"], "icon": v["icon"], "category": v["category"] or "general", "slug": v["public_slug"], "conversation_path": f"/agents/{v['public_slug']}", "credit_cost": v["credit_cost"], "skill_manifest": json.dumps(manifest), "allows_image_generation": v["output_policy"] == "image_required", "output_policy": v["output_policy"], "placeholder": "描述你希望创作的内容…", "enabled": True, "definition_source": "productized"})
                        except (ValueError, LookupError):
                            continue
        return result

    def resolve_agent_reference(self, reference):
        from app.agent_reference import resolve_agent_reference
        with self._store.connection() as conn:
            return resolve_agent_reference(conn,reference,postgres=self._store.is_postgres)

    def agent_enabled(self, tenant_id: str, agent_id: str) -> bool:
        if agent_id not in CATALOG:
            from app.agent_availability import tenant_available
            with self._store.connection() as conn:
                return tenant_available(conn, tenant_id, agent_id, postgres=self._store.is_postgres)
        with self._store.connection() as conn:
            row = conn.execute("SELECT status FROM tenant_agent_instances WHERE tenant_id=? AND agent_id=?", (tenant_id, agent_id)).fetchone()
        return bool(row and row["status"] == "enabled")

    def create_task(self, tenant_id: str, user_id: str, agent_id: str, text: str, conversation_id: str | None, *, attachment_ids: list[str] | None = None, _test_revision=None, _test_id=None, _test_fingerprint=None, _release_operation_id=None, _controlled_action=False, _controlled_qualification=None) -> dict:
        if _controlled_qualification is not None and not _controlled_action:
            raise PermissionError('CONTROLLED_SKILL_ACTION_NOT_ALLOWED')
        if _controlled_action:
            from app.controlled_skill_action import TASK_MARKER
            if type(_controlled_action) is not bool or not text.endswith(TASK_MARKER) or conversation_id or _test_revision:
                raise PermissionError('CONTROLLED_SKILL_ACTION_NOT_ALLOWED')
        if _release_operation_id and not _test_revision:
            raise PermissionError("Release ownership is only valid for controlled Runtime Test")
        attachment_ids = attachment_ids or []
        if len(attachment_ids) > 1 or (attachment_ids and agent_id != "image-agent"):
            raise ValueError("当前仅图片生成智能体支持一张参考图。")
        with self._store.connection() as conn:
            context = None
            if attachment_ids and not self._store.is_postgres:
                conn.execute("BEGIN IMMEDIATE")
            if agent_id not in CATALOG:
                if not self.execution_resolver:
                    raise LookupError("Execution resolver unavailable")
                if not self._store.is_postgres:
                    conn.execute("BEGIN IMMEDIATE")
                context = self.execution_resolver.resolve(conn, tenant_id, user_id, agent_id, conversation_id, test_revision=_test_revision,
                    **({'controlled_qualification': _controlled_qualification} if _controlled_qualification is not None else {}))
                if _test_fingerprint is not None and context['configuration_fingerprint'] != _test_fingerprint:
                    raise ValueError('Runtime Test fingerprint changed before task creation')
                from app.agent_execution import definition
                agent = definition(context)
            else:
                agent = get_agent(agent_id)
            credit = conn.execute("SELECT balance FROM credit_accounts WHERE tenant_id=?", (tenant_id,)).fetchone()
            instance = conn.execute("SELECT status FROM tenant_agent_instances WHERE tenant_id=? AND agent_id=?", (tenant_id, agent_id)).fetchone()
            if not instance or (instance["status"] != "enabled" and _test_revision is None and _controlled_qualification is None):
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
            if _controlled_action:
                # Atomic reservation prevents the ordinary queue/worker from
                # picking this zero-model Task during the creation/execute gap.
                conn.execute("UPDATE tasks SET status='running',stage='controlled_action_reserved',started_at=CURRENT_TIMESTAMP WHERE id=?", (task_id,))
            if attachment_ids:
                claimed = conn.execute(
                    "UPDATE chat_image_attachments SET task_id=? WHERE id=? AND tenant_id=? AND user_id=? AND task_id IS NULL",
                    (task_id, attachment_ids[0], tenant_id, user_id),
                )
                if claimed.rowcount != 1:
                    raise ValueError("参考图片不存在、无权使用或已经发送。")
            conn.execute("INSERT INTO task_events(task_id,stage,message) VALUES (?, 'queued', '任务已进入队列')", (task_id,))
            self._insert_task_activity(conn, task_id, "queued", "started")
            if context:
                conn.execute("INSERT INTO task_agent_contexts VALUES (?,?)", (task_id, context["id"]))
            if _test_revision:
                if not _test_id or not context:
                    raise PermissionError("Controlled Runtime Test association required")
                conn.execute("INSERT INTO agent_template_tests(id,agent_template_version_id,configuration_fingerprint,test_type,task_id,status,result_json) VALUES (?,?,?,'runtime',?,'queued',?)", (_test_id,_test_revision,context["configuration_fingerprint"],task_id,json.dumps({"runtime_test_status":"queued"})))
                if _release_operation_id:
                    from app.agent_release_provenance import AgentReleaseProvenance
                    release = AgentReleaseProvenance(self.execution_resolver.catalog)
                    operation = release._operation(conn, _release_operation_id)
                    if operation["status"] not in {"staged", "published", "enabled"} or operation["agent_slug"] != agent.slug:
                        raise PermissionError("Release Runtime Test ownership mismatch")
                    release._template_mapping(conn, _release_operation_id, agent_id)
                    release._revision_identity(conn, _release_operation_id, agent_id, _test_revision)
                    release._artifact(conn, _release_operation_id, "runtime_validation", task_id, {
                        "task_id": task_id, "test_id": _test_id, "context_id": context["id"],
                        "template_id": agent_id, "revision_id": _test_revision,
                        "fingerprint": context["configuration_fingerprint"],
                        "tenant_id": tenant_id, "instance_id": context["instance_id"],
                    })
                    release._event(conn, _release_operation_id, "runtime_validation_created", json.dumps({"task_id": task_id, "test_id": _test_id}))
        return self.task(task_id, tenant_id, user_id) or {}

    def set_task(self, task_id: str, tenant_id: str, status: str, stage: str, message: str, *, run_id: str | None = None, error_code: str | None = None, response: str | None = None, conversation_id: str | None = None) -> None:
        with self._store.connection() as conn:
            updated = conn.execute("UPDATE tasks SET status=?,stage=?,run_id=COALESCE(?,run_id),error_code=?,conversation_id=COALESCE(?,conversation_id),started_at=CASE WHEN ?='running' THEN COALESCE(started_at,CURRENT_TIMESTAMP) ELSE started_at END,completed_at=CASE WHEN ? IN ('completed','failed','cancelled') THEN CURRENT_TIMESTAMP ELSE completed_at END WHERE id=? AND tenant_id=? AND status NOT IN ('completed','cancelled') AND stage<>'cancelling' AND NOT (status='failed' AND ?='completed')", (status,stage,run_id,error_code,conversation_id,status,status,task_id,tenant_id,status))
            if updated.rowcount != 1:
                return
            conn.execute("INSERT INTO task_events(task_id,stage,message) VALUES (?,?,?)", (task_id,stage,message))
            if response is not None:
                conn.execute("INSERT INTO task_results(task_id,final_response,result_json) VALUES (?,?,?) ON CONFLICT(task_id) DO UPDATE SET final_response=excluded.final_response,result_json=excluded.result_json", (task_id,response,json.dumps({"run_id":run_id},ensure_ascii=False)))

    def attach_task_run(self, task_id: str, tenant_id: str, run_id: str, conversation_id: str) -> None:
        """Expose a started Run to cancellation without advancing task state."""
        with self._store.connection() as conn:
            conn.execute(
                "UPDATE tasks SET run_id=?,conversation_id=COALESCE(conversation_id,?) WHERE id=? AND tenant_id=? AND status NOT IN ('completed','failed','cancelled')",
                (run_id, conversation_id, task_id, tenant_id),
            )

    def cancellation_requested(self, task_id: str, tenant_id: str) -> bool:
        with self._store.connection() as conn:
            row = conn.execute("SELECT status,stage FROM tasks WHERE id=? AND tenant_id=?", (task_id, tenant_id)).fetchone()
        return bool(row and (row["status"] == "cancelled" or row["stage"] == "cancelling"))

    def cancel_task(self, task_id: str, tenant_id: str, user_id: str) -> dict | None:
        """Atomically claim cancellation without changing the database schema.

        Queued work is terminal immediately. A running task stores its requested
        cancellation in ``stage`` (the durable V1 cancelling representation)
        until its worker records the cancelled terminal state.
        """
        with self._store.connection() as conn:
            if not self._store.is_postgres:
                conn.execute("BEGIN IMMEDIATE")
            task = conn.execute(
                "SELECT id,status,stage,run_id FROM tasks WHERE id=? AND tenant_id=? AND user_id=?" + (" FOR UPDATE" if self._store.is_postgres else ""),
                (task_id, tenant_id, user_id),
            ).fetchone()
            if not task:
                return None
            if task["status"] in {"completed", "failed", "cancelled"}:
                return self.task(task_id, tenant_id, user_id)
            if task["status"] == "queued":
                conn.execute("UPDATE tasks SET status='cancelled',stage='cancelled',completed_at=CURRENT_TIMESTAMP WHERE id=?", (task_id,))
                conn.execute("INSERT INTO task_events(task_id,stage,message) VALUES (?,'cancelled','Generation stopped')", (task_id,))
                self._insert_task_activity(conn, task_id, "cancelled", "completed")
            elif task["stage"] != "cancelling":
                conn.execute("UPDATE tasks SET stage='cancelling' WHERE id=?", (task_id,))
                conn.execute("INSERT INTO task_events(task_id,stage,message) VALUES (?,'cancelling','正在停止生成')", (task_id,))
                if task["run_id"]:
                    conn.execute("UPDATE run_traces SET status='cancelling' WHERE run_id=? AND tenant_id=? AND status NOT IN ('completed','failed','cancelled')", (task["run_id"], tenant_id))
        return self.task(task_id, tenant_id, user_id)

    def finalize_task_cancellation(self, task_id: str, tenant_id: str, *, run_id: str | None = None, conversation_id: str | None = None) -> None:
        with self._store.connection() as conn:
            updated = conn.execute(
                "UPDATE tasks SET status='cancelled',stage='cancelled',run_id=COALESCE(?,run_id),conversation_id=COALESCE(?,conversation_id),error_code=NULL,completed_at=CURRENT_TIMESTAMP WHERE id=? AND tenant_id=? AND status NOT IN ('completed','failed','cancelled')",
                (run_id, conversation_id, task_id, tenant_id),
            )
            if updated.rowcount:
                conn.execute("INSERT INTO task_events(task_id,stage,message) VALUES (?,'cancelled','Generation stopped')", (task_id,))
                self._insert_task_activity(conn, task_id, "cancelled", "completed")

    def add_task_delta(self, task_id: str, tenant_id: str, sequence: int, text: str) -> None:
        """Append one already-filtered visible-text delta to the task channel."""
        if not isinstance(sequence, int) or sequence < 1 or not isinstance(text, str) or not text:
            raise ValueError("Invalid task text delta")
        with self._store.connection() as conn:
            active = conn.execute(
                "SELECT 1 FROM tasks WHERE id=? AND tenant_id=? AND status NOT IN ('completed','failed','cancelled') AND stage<>'cancelling'",
                (task_id, tenant_id),
            ).fetchone()
            if active:
                conn.execute(
                    "INSERT INTO task_events(task_id,stage,message) VALUES (?, 'delta', ?)",
                    (task_id, json.dumps({"sequence": sequence, "text": text}, ensure_ascii=False)),
                )

    @staticmethod
    def _insert_task_activity(conn, task_id: str, stage: str, status: str) -> int:
        cursor = conn.execute(
            "INSERT INTO task_events(task_id,stage,message) VALUES (?, 'activity', '{}') RETURNING id",
            (task_id,),
        )
        sequence = int(cursor.fetchone()["id"])
        payload = {"sequence": sequence, "stage": stage, "label": _SAFE_ACTIVITY_LABELS[stage], "status": status}
        conn.execute("UPDATE task_events SET message=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), sequence))
        return sequence

    def add_task_activity(self, task_id: str, tenant_id: str, stage: str, status: str) -> int | None:
        if stage not in _SAFE_ACTIVITY_LABELS or status not in {"started", "completed"}:
            raise ValueError("Invalid safe task activity")
        with self._store.connection() as conn:
            terminal = stage in {"completed", "failed", "cancelled"}
            active = conn.execute(
                "SELECT 1 FROM tasks WHERE id=? AND tenant_id=? AND ("
                "(status NOT IN ('completed','failed','cancelled') AND stage<>'cancelling') OR status=?)",
                (task_id, tenant_id, stage if terminal else ""),
            ).fetchone()
            return self._insert_task_activity(conn, task_id, stage, status) if active else None

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
        image_width: int | None = None,
        image_height: int | None = None,
        image_requested_size: str | None = None,
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
        conversation_title = _project_title(task["agent_id"], task.get("input_text"))
        stage = "task_validation"
        try:
            with self._store.connection() as conn:
                # Serialize duplicate final events before message/credit writes.
                if not self._store.is_postgres:
                    conn.execute("BEGIN IMMEDIATE")
                current = conn.execute(
                    "SELECT status,stage,agent_id,run_id,conversation_id FROM tasks WHERE id=? AND tenant_id=? AND user_id=?" + (" FOR UPDATE" if self._store.is_postgres else ""),
                    (task_id, tenant_id, user_id),
                ).fetchone()
                if not current:
                    raise LookupError("任务不存在。")
                agent = self.task_definition({**task, "agent_id": current["agent_id"]}, conn)
                if current["agent_id"] not in CATALOG:
                    context = self.execution_resolver.task_context(conn, task)
                    association = conn.execute("SELECT context_id FROM conversation_agent_contexts WHERE conversation_id=?", (conversation_id,)).fetchone()
                    if not association or association["context_id"] != context["id"]:
                        raise ValueError("Task / conversation context mismatch")
                stage = "completion_evidence"
                if current["status"] == "cancelled" or current["stage"] == "cancelling":
                    raise TaskCancellationRequested()
                if (current["run_id"] and current["run_id"] != run_id) or (current["conversation_id"] and current["conversation_id"] != conversation_id):
                    raise ValueError("Task/Run association mismatch.")
                if current["status"] == "completed":
                    return {
                        "assistant_message_id": assistant_message_id,
                        "generation_id": generation_id,
                        "replayed": True,
                    }

                saved_trace = conn.execute("SELECT status,payload FROM run_traces WHERE run_id=? AND tenant_id=? AND conversation_id=? AND agent_id=?", (run_id,tenant_id,conversation_id,current["agent_id"])).fetchone()
                evidence = json.loads(saved_trace["payload"]) if saved_trace else {}
                if (not saved_trace or saved_trace["status"] not in {"runtime_completed", "failed"}
                        or evidence.get("runtime_completed") is not True
                        or evidence.get("runtime_status") not in {"completed", "success"}
                        or evidence.get("required_tool_calls_completed") is not True
                        or evidence.get("final_response_received") is not True
                        or not response.strip() or response != evidence.get("final_result")
                        or (saved_trace["status"] == "failed" and evidence.get("result_persistence_status") != "failed")):
                    raise ValueError("Missing successful runtime/final response evidence.")
                if agent.allows_image_generation and trace_payload.get("artifact_available") is not True:
                    raise ValueError("Missing verified image artifact.")

                from app.activity_plan_runtime import validated_envelope
                grounding = tuple(str(call.get("output_summary") or "") for call in evidence.get("mcp_calls", [])
                                  if call.get("tool") in {"knowledge_search", "enterprise_config_get"}
                                  and call.get("status") == "completed")
                structured_result = validated_envelope(evidence.get("structured_result"), response, grounding)

                stage = "conversation_owner"
                conn.execute(
                    "INSERT OR IGNORE INTO conversation_owners(conversation_id,user_id,title) VALUES (?,?,?)",
                    (conversation_id, user_id, conversation_title),
                )
                conn.execute(
                    "UPDATE conversation_owners SET title=? WHERE conversation_id=? AND user_id=? AND title IN ('','新会话','新图片会话')",
                    (conversation_title, conversation_id, user_id),
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
                assistant = conn.execute("SELECT conversation_id,role,content FROM messages WHERE id=?", (assistant_message_id,)).fetchone()
                if not assistant or assistant["conversation_id"] != conversation_id or assistant["role"] != "assistant" or assistant["content"] != response[:12000]:
                    raise ValueError("Existing final message conflicts with runtime result.")

                stage = "artifact_association"
                if agent.allows_image_generation:
                    expected_prefix = f"generated/{tenant_id}/"
                    if (
                        not image_storage_key
                        or not image_storage_key.startswith(expected_prefix)
                        or "\\" in image_storage_key
                        or any(part in {"", ".", ".."} for part in image_storage_key.split("/"))
                    ):
                        raise ValueError("图片任务缺少已持久化 storage key。")
                    conn.execute(
                        "INSERT OR IGNORE INTO generations(id,tenant_id,user_id,conversation_id,task_id,provider,model,storage_key,mime_type,width,height,requested_size) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (generation_id, tenant_id, user_id, conversation_id, task_id, "image-gateway", "gpt-image-2.5-sunburst-c", image_storage_key, _image_mime_type(image_storage_key), image_width, image_height, image_requested_size),
                    )
                    generation = conn.execute("SELECT task_id,conversation_id,storage_key FROM generations WHERE id=?", (generation_id,)).fetchone()
                    if not generation or generation["task_id"] != task_id or generation["conversation_id"] != conversation_id or generation["storage_key"] != image_storage_key:
                        raise ValueError("Existing generation conflicts with runtime result.")

                stage = "task_result"
                result_json = json.dumps(
                    {"run_id": run_id, "assistant_message_id": assistant_message_id, "generation_id": generation_id,
                     "structured_result": structured_result},
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
                    amount = agent.credit_cost
                    updated = conn.execute(
                        "UPDATE credit_accounts SET balance=balance-?,updated_at=CURRENT_TIMESTAMP WHERE tenant_id=? AND balance>=?",
                        (amount, tenant_id, amount),
                    )
                    if updated.rowcount != 1:
                        raise ValueError("任务完成时积分余额不足。")
                    conn.execute(
                        "INSERT INTO credit_transactions(id,tenant_id,user_id,task_id,amount,reason) VALUES (?,?,?,?,?,?)",
                        (f"task:{task_id}:charge", tenant_id, user_id, task_id, -amount, agent.slug),
                    )

                stage = "run_trace"
                completed_trace = dict(evidence)
                completed_trace.update(
                    {
                        "status": "completed",
                        "partial_output": False,
                        "assistant_message_saved": True,
                        "final_response_persisted": True,
                        "assistant_message_id": assistant_message_id,
                        "result_persistence_status": "completed",
                        "result_persistence_error_stage": None,
                        "failure_stage": None,
                        "artifact_saved": not agent.allows_image_generation or bool(generation_id),
                        "artifact_available": True if generation_id else None,
                        "artifact_completed": True if agent.allows_image_generation else None,
                        "generation_id": generation_id,
                        "structured_result": structured_result,
                        "structured_result_diagnostic": evidence.get("structured_result_diagnostic") or (
                            {"code": "persistence_validation_failed"} if evidence.get("structured_result") and not structured_result else None
                        ),
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
            if isinstance(exc, (ResultPersistenceError, TaskCancellationRequested)):
                raise
            raise ResultPersistenceError(stage) from exc
        return {
            "assistant_message_id": assistant_message_id,
            "generation_id": generation_id,
            "replayed": False,
        }

    def conversation_detail(self, tenant_id: str, user_id: str, conversation_id: str) -> dict | None:
        with self._store.connection() as conn:
            from app.agent_availability import retired_validation_conversation
            if retired_validation_conversation(conn, conversation_id, postgres=self._store.is_postgres):
                return None
            conversation = conn.execute(
                "SELECT c.id,c.agent_id,c.runtime_thread_id,c.created_at,o.title FROM conversations c JOIN conversation_owners o ON o.conversation_id=c.id WHERE c.id=? AND c.tenant_id=? AND o.user_id=? AND o.deleted_at IS NULL",
                (conversation_id, tenant_id, user_id),
            ).fetchone()
            if not conversation:
                return None
            tasks = [
                dict(item)
                for item in conn.execute(
                    "SELECT t.id,t.agent_id,t.input_text,t.status,t.stage,t.run_id,t.created_at,t.started_at,t.completed_at "
                    "FROM tasks t WHERE t.conversation_id=? AND t.tenant_id=? AND t.user_id=? ORDER BY t.created_at,t.id",
                    (conversation_id, tenant_id, user_id),
                ).fetchall()
            ]
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
            generations = [
                _generation_view(dict(item))
                for item in conn.execute(
                    "SELECT id,task_id,storage_key,mime_type,width,height,requested_size,created_at FROM generations "
                    "WHERE tenant_id=? AND user_id=? AND conversation_id=? AND deleted_at IS NULL "
                    "AND storage_key IS NOT NULL AND TRIM(storage_key)<>'' ORDER BY created_at,id",
                    (tenant_id, user_id, conversation_id),
                ).fetchall()
            ]
            attachment_rows = conn.execute(
                "SELECT a.id,a.task_id,a.filename,a.mime_type,a.size_bytes,a.width,a.height "
                "FROM chat_image_attachments a JOIN tasks t ON t.id=a.task_id "
                "WHERE t.conversation_id=? AND t.tenant_id=? AND t.user_id=? AND a.tenant_id=t.tenant_id AND a.user_id=t.user_id",
                (conversation_id, tenant_id, user_id),
            ).fetchall()
            runs = conn.execute("SELECT run_id,status,created_at,completed_at,payload FROM run_traces WHERE conversation_id=? AND tenant_id=? ORDER BY created_at", (conversation_id, tenant_id)).fetchall()
        result = dict(conversation)
        agent = self._history_agent(result["id"], tenant_id, result["agent_id"])
        first_prompt = tasks[0]["input_text"] if tasks else None
        display_title = _project_title(result["agent_id"], first_prompt) if result["title"] in _GENERIC_CONVERSATION_TITLES else result["title"]
        result["stored_title"] = result["title"]
        result["title"] = display_title
        result["agent"] = agent
        result["project"] = {"id": result["id"], "name": display_title, "type": _project_type(agent)}
        result["tasks"] = tasks
        result["task_count"] = len(tasks)
        result["generations"] = generations
        result["image_count"] = len(generations)
        generation_by_task = {item["task_id"]: item for item in generations}
        attachments_by_task = {item["task_id"]: _chat_image_attachment_view(dict(item)) for item in attachment_rows}
        task_runs = {item["id"]: item.get("run_id") for item in tasks}
        trace_payloads = {
            item["run_id"]: json.loads(item["payload"])
            for item in runs
            if item["run_id"] and item["payload"]
        }
        reference_ids = {
            str(result.get("file_id"))
            for payload in trace_payloads.values()
            for retrieval in payload.get("knowledge_retrievals", [])
            if isinstance(retrieval, dict)
            for result in retrieval.get("results", [])
            if isinstance(result, dict) and result.get("accepted") is True and result.get("file_id")
        }
        file_names: dict[str, str] = {}
        if reference_ids:
            placeholders = ",".join("?" for _ in reference_ids)
            with self._store.connection() as reference_conn:
                rows = reference_conn.execute(
                    f"SELECT id,name FROM knowledge_files WHERE tenant_id=? AND id IN ({placeholders})",
                    (tenant_id, *sorted(reference_ids)),
                ).fetchall()
            file_names = {str(item["id"]): str(item["name"]) for item in rows}
        message_items = [dict(item) for item in messages]
        for message in message_items:
            message_id = str(message["id"])
            task_id = None
            if message_id.startswith("task:") and message_id.count(":") >= 2:
                task_id = message_id.split(":", 2)[1]
            elif message_id.startswith("legacy-user-"):
                task_id = message_id.removeprefix("legacy-user-")
            elif message_id.startswith("legacy-assistant-"):
                task_id = message_id.removeprefix("legacy-assistant-")
            if task_id:
                message["task_id"] = task_id
                if message["role"] == "user" and task_id in attachments_by_task:
                    message["attachments"] = [attachments_by_task[task_id]]
                if message["role"] == "assistant" and task_id in generation_by_task:
                    message["generation"] = generation_by_task[task_id]
                if message["role"] == "assistant":
                    payload = trace_payloads.get(task_runs.get(task_id), {})
                    referenced = []
                    for retrieval in payload.get("knowledge_retrievals", []):
                        if not isinstance(retrieval, dict):
                            continue
                        for observation in retrieval.get("results", []):
                            file_id = str(observation.get("file_id") or "") if isinstance(observation, dict) else ""
                            if observation.get("accepted") is True and file_id in file_names:
                                referenced.append({"id": file_id, "name": file_names[file_id]})
                    if referenced:
                        unique = {item["id"]: item for item in referenced}
                        message["references"] = {"knowledge": list(unique.values())}
        result["messages"] = message_items
        result["runs"] = [{"run_id": item["run_id"], "status": item["status"], "created_at": item["created_at"], "completed_at": item["completed_at"]} for item in runs]
        return result

    @staticmethod
    def _recent_task_time(value: object) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    def recent_tasks(self, tenant_id: str, *, days: int, status: str = "all", agent_id: str | None = None) -> dict:
        """Tenant-scoped operating view for enterprise administrators.

        This is deliberately a compact recent list rather than a reporting or
        billing surface. Credit usage comes only from committed task charges.
        """
        since = datetime.now(timezone.utc) - timedelta(days=days)
        with self._store.connection() as conn:
            rows = conn.execute(
                "SELECT t.id,t.user_id,t.agent_id,t.status,t.stage,t.error_code,t.user_message,t.created_at,t.started_at,t.completed_at,"
                "u.display_name AS member_name,COALESCE(a.name,t.agent_id) AS agent_name,"
                "COALESCE(SUM(CASE WHEN c.amount < 0 THEN -c.amount ELSE 0 END),0) AS credit_used "
                "FROM tasks t JOIN users u ON u.id=t.user_id "
                "LEFT JOIN agent_templates a ON a.id=t.agent_id "
                "LEFT JOIN credit_transactions c ON c.task_id=t.id AND c.tenant_id=t.tenant_id "
                "WHERE t.tenant_id=? GROUP BY t.id,t.user_id,t.agent_id,t.status,t.stage,t.error_code,t.user_message,"
                "t.created_at,t.started_at,t.completed_at,u.display_name,a.name ORDER BY t.created_at DESC LIMIT 500",
                (tenant_id,),
            ).fetchall()
        status_groups = {"all": {"queued", "running", "completed", "failed"}, "processing": {"queued", "running"}, "completed": {"completed"}, "failed": {"failed"}}
        allowed = status_groups.get(status, status_groups["all"])
        summary_items = []
        tasks = []
        for row in rows:
            item = dict(row)
            created_at = self._recent_task_time(item.get("created_at"))
            if created_at is None or created_at < since:
                continue
            item["credit_used"] = int(item["credit_used"] or 0)
            summary_items.append(item)
            if item["status"] not in allowed:
                continue
            if agent_id and item["agent_id"] != agent_id:
                continue
            tasks.append(item)
        return {
            "summary": {
                "days": days,
                "task_count": len(summary_items),
                "completed_count": sum(item["status"] == "completed" for item in summary_items),
                "failed_count": sum(item["status"] == "failed" for item in summary_items),
                "credit_used": sum(item["credit_used"] for item in summary_items),
            },
            "tasks": tasks[:100],
        }

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
            from app.agent_availability import retired_validation_task
            if retired_validation_task(conn, task_id, postgres=self._store.is_postgres):
                return None
            row = conn.execute("SELECT t.*, r.final_response,r.result_json FROM tasks t LEFT JOIN task_results r ON r.task_id=t.id WHERE t.id=? AND t.tenant_id=? AND t.user_id=?", (task_id,tenant_id,user_id)).fetchone()
            events = conn.execute("SELECT stage,message,created_at FROM task_events WHERE task_id=? AND stage<>'delta' ORDER BY id", (task_id,)).fetchall()
            generation = None
            if row and row["status"] in {"completed", "failed", "cancelled"}:
                generation = conn.execute(
                    "SELECT id,task_id,storage_key,mime_type,width,height,requested_size,created_at FROM generations "
                    "WHERE task_id=? AND tenant_id=? AND user_id=? AND deleted_at IS NULL "
                    "AND storage_key IS NOT NULL AND TRIM(storage_key)<>'' LIMIT 1",
                    (task_id, tenant_id, user_id),
                ).fetchone()
        if not row: return None
        result = dict(row)
        result_metadata = json.loads(result.pop("result_json") or "{}")
        result["assistant_message_id"] = result_metadata.get("assistant_message_id") if result["status"] == "completed" else None
        result["structured_result"] = result_metadata.get("structured_result") if result["status"] == "completed" else None
        result["events"] = [dict(x) for x in events]
        result["current_activity"] = next(
            (
                {
                    "sequence": payload["sequence"], "stage": payload["stage"],
                    "label": _SAFE_ACTIVITY_LABELS[payload["stage"]], "status": payload["status"],
                    "created_at": event["created_at"],
                }
                for event in reversed(result["events"])
                if event["stage"] == "activity"
                for payload in [self._safe_activity_payload(event["message"])]
                if payload is not None
            ),
            None,
        )
        result["generation"] = _generation_view(dict(generation)) if generation else None
        return result

    @staticmethod
    def _safe_activity_payload(message: object) -> dict | None:
        try:
            payload = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return None
        if not (
            isinstance(payload, dict)
            and isinstance(payload.get("sequence"), int)
            and payload.get("stage") in _SAFE_ACTIVITY_LABELS
            and payload.get("status") in {"started", "completed"}
        ):
            return None
        return payload

    def task_events_since(self, task_id: str, tenant_id: str, user_id: str, after_id: int = 0) -> list[dict]:
        with self._store.connection() as conn:
            from app.agent_availability import retired_validation_task
            if retired_validation_task(conn, task_id, postgres=self._store.is_postgres):
                return []
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

    def conversations(self, tenant_id: str, user_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        with self._store.connection() as conn:
            from app.agent_availability import release_provenance_available
            hidden_validation = (
                "AND NOT EXISTS (SELECT 1 FROM agent_release_operations ro "
                "JOIN agent_release_artifacts ra ON ra.operation_id=ro.operation_id "
                "AND ra.artifact_type='runtime_validation' JOIN tasks rt ON rt.id=ra.artifact_id "
                "WHERE ro.status='aborted' AND rt.conversation_id=c.id) "
                if release_provenance_available(conn, postgres=self._store.is_postgres) else ""
            )
            rows = conn.execute(
                "SELECT c.id,c.agent_id,c.runtime_thread_id,o.title,c.created_at,"
                "COALESCE(MAX(COALESCE(t.completed_at,t.started_at,t.created_at)),c.created_at) AS updated_at "
                "FROM conversations c JOIN conversation_owners o ON o.conversation_id=c.id "
                "LEFT JOIN tasks t ON t.conversation_id=c.id AND t.tenant_id=c.tenant_id AND t.user_id=o.user_id "
                "WHERE c.tenant_id=? AND o.user_id=? AND o.deleted_at IS NULL " + hidden_validation +
                "GROUP BY c.id,c.agent_id,c.runtime_thread_id,o.title,c.created_at "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (tenant_id, user_id, limit, offset),
            ).fetchall()
            conversations = [dict(row) for row in rows]
            if not conversations:
                return []
            conversation_ids = [item["id"] for item in conversations]
            placeholders = ",".join("?" for _ in conversation_ids)
            task_rows = conn.execute(
                "SELECT id,conversation_id,input_text,status,stage,created_at,started_at,completed_at FROM tasks "
                f"WHERE tenant_id=? AND user_id=? AND conversation_id IN ({placeholders}) ORDER BY conversation_id,created_at,id",
                (tenant_id, user_id, *conversation_ids),
            ).fetchall()
            generation_rows = conn.execute(
                "SELECT id,conversation_id,task_id,storage_key,mime_type,width,height,created_at FROM generations "
                f"WHERE tenant_id=? AND user_id=? AND conversation_id IN ({placeholders}) AND deleted_at IS NULL "
                "AND storage_key IS NOT NULL AND TRIM(storage_key)<>'' ORDER BY conversation_id,created_at,id",
                (tenant_id, user_id, *conversation_ids),
            ).fetchall()

        task_state: dict[str, dict] = {}
        for row in task_rows:
            item = dict(row)
            state = task_state.setdefault(item["conversation_id"], {"first": item, "latest": item, "count": 0})
            state["count"] += 1
            item_activity = str(item.get("completed_at") or item.get("started_at") or item.get("created_at") or "")
            latest = state["latest"]
            latest_activity = str(latest.get("completed_at") or latest.get("started_at") or latest.get("created_at") or "")
            if (item_activity, item["id"]) >= (latest_activity, latest["id"]):
                state["latest"] = item

        generation_state: dict[str, dict] = {}
        for row in generation_rows:
            item = dict(row)
            state = generation_state.setdefault(item["conversation_id"], {"latest": item, "count": 0})
            state["latest"] = item
            state["count"] += 1

        for conversation in conversations:
            tasks = task_state.get(conversation["id"], {})
            generations = generation_state.get(conversation["id"], {})
            first_task = tasks.get("first")
            latest_task = tasks.get("latest")
            agent = self._history_agent(conversation["id"], tenant_id, conversation["agent_id"])
            first_prompt = first_task["input_text"] if first_task else None
            display_title = _project_title(conversation["agent_id"], first_prompt) if conversation["title"] in _GENERIC_CONVERSATION_TITLES else conversation["title"]
            conversation["stored_title"] = conversation["title"]
            conversation["title"] = display_title
            conversation["agent"] = agent
            conversation["project"] = {"id": conversation["id"], "name": display_title, "type": _project_type(agent)}
            conversation["latest_task"] = latest_task
            conversation["latest_prompt"] = str(latest_task["input_text"] if latest_task else first_prompt or "")[:160]
            conversation["latest_status"] = latest_task["status"] if latest_task else None
            conversation["task_count"] = tasks.get("count", 0)
            conversation["image_count"] = generations.get("count", 0)
            conversation["latest_generation"] = _generation_view(generations.get("latest"))
        return conversations

    def generations(self, tenant_id: str, user_id: str, limit: int = 50) -> list[dict]:
        with self._store.connection() as conn:
            rows = conn.execute(
                "SELECT g.*,t.agent_id,t.input_text,o.title AS conversation_title,o.conversation_id AS owner_conversation_id FROM generations g "
                "LEFT JOIN tasks t ON t.id=g.task_id AND t.tenant_id=g.tenant_id AND t.user_id=g.user_id "
                "LEFT JOIN conversation_owners o ON o.conversation_id=g.conversation_id AND o.user_id=g.user_id AND o.deleted_at IS NULL "
                "WHERE g.tenant_id=? AND g.user_id=? AND g.deleted_at IS NULL "
                "AND g.storage_key IS NOT NULL AND TRIM(g.storage_key)<>'' ORDER BY g.created_at DESC LIMIT ?",
                (tenant_id, user_id, limit),
            ).fetchall()
        results = []
        for row in rows:
            item = _generation_view(dict(row))
            agent_id = item.get("agent_id") or "image-agent"
            agent = self._history_agent(item.get("conversation_id"), tenant_id, agent_id)
            title = item.get("conversation_title")
            if title in _GENERIC_CONVERSATION_TITLES:
                title = _project_title(agent_id, item.get("input_text"))
            item["agent"] = agent
            item["project_available"] = bool(item.get("owner_conversation_id"))
            item["project"] = ({"id": item["conversation_id"], "name": title or _project_title(agent_id, item.get("input_text")), "type": _project_type(agent)} if item["project_available"] else None)
            item["prompt"] = str(item.get("input_text") or "")[:160]
            item.pop("owner_conversation_id", None)
            results.append(item)
        return results

    def create_generation(self, tenant_id: str, user_id: str, conversation_id: str, task_id: str, storage_key: str, provider: str, model: str) -> str:
        generation_id = f"task:{task_id}:generation"
        with self._store.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO generations(id,tenant_id,user_id,conversation_id,task_id,provider,model,storage_key,mime_type) VALUES (?,?,?,?,?,?,?,?,?)", (generation_id,tenant_id,user_id,conversation_id,task_id,provider,model,storage_key,_image_mime_type(storage_key)))
        return generation_id

    def readable_generation(self, tenant_id: str, user_id: str, storage_key: str) -> dict | None:
        canonical_url = f"/api/v1/storage/{quote(storage_key, safe='/')}"
        legacy_url = f"/api/v1/storage/{storage_key}"
        with self._store.connection() as conn:
            row = conn.execute(
                "SELECT id,task_id,conversation_id,storage_key,mime_type,width,height,created_at FROM generations "
                "WHERE tenant_id=? AND storage_key=? AND "
                "((user_id=? AND deleted_at IS NULL) OR EXISTS "
                "(SELECT 1 FROM assets a WHERE a.tenant_id=? AND a.url IN (?,?))) LIMIT 1",
                (tenant_id, storage_key, user_id, tenant_id, canonical_url, legacy_url),
            ).fetchone()
        return _generation_view(dict(row)) if row else None

    def can_read_storage(self, tenant_id: str, user_id: str, storage_key: str) -> bool:
        return self.readable_generation(tenant_id, user_id, storage_key) is not None

    def delete_generation(self, tenant_id: str, user_id: str, generation_id: str) -> dict | None:
        with self._store.connection() as conn:
            row=conn.execute("SELECT * FROM generations WHERE id=? AND tenant_id=? AND user_id=? AND deleted_at IS NULL",(generation_id,tenant_id,user_id)).fetchone()
            if row: conn.execute("UPDATE generations SET deleted_at=CURRENT_TIMESTAMP WHERE id=?",(generation_id,))
        return dict(row) if row else None

    def save_generation_as_asset(self, tenant_id: str, user_id: str, generation_id: str, name: str) -> dict | None:
        with self._store.connection() as conn:
            generation=conn.execute("SELECT * FROM generations WHERE id=? AND tenant_id=? AND user_id=? AND deleted_at IS NULL",(generation_id,tenant_id,user_id)).fetchone()
            if not generation: return None
            asset_id=str(uuid.uuid4()); conn.execute("INSERT INTO assets(id,tenant_id,name,asset_type,tags,url) VALUES (?,?,?,?,?,?)",(asset_id,tenant_id,name[:120],"poster_reference",json.dumps(["generated"]),f"/api/v1/storage/{quote(str(generation['storage_key']), safe='/')}")); conn.execute("INSERT INTO asset_metadata(asset_id,description) VALUES (?,?)",(asset_id,"由个人生成记录保存"))
        return {"id":asset_id,"name":name,"type":"poster_reference"}

    def update_enterprise_config(self, tenant_id: str, payload: dict) -> dict:
        from app.wechat_action_contract import validate_enterprise_wechat_config
        validate_enterprise_wechat_config(payload, tenant_id)
        if {"brand_logo", "brand_logo_metadata", "brand_mark_logo"} & payload.keys():
            raise ValueError("BRAND_LOGO_UPLOAD_REQUIRED")
        with self._store.connection() as conn:
            if not self._store.is_postgres:
                conn.execute("BEGIN IMMEDIATE")
            lock = " FOR UPDATE" if self._store.is_postgres else ""
            row = conn.execute(f"SELECT payload FROM enterprise_configs WHERE tenant_id=?{lock}", (tenant_id,)).fetchone()
            if not row:
                raise LookupError("企业配置不存在。")
            current = json.loads(row["payload"])
            current.update({key: value for key, value in payload.items() if value is not None})
            conn.execute("UPDATE enterprise_configs SET payload=? WHERE tenant_id=?",
                         (json.dumps(current, ensure_ascii=False), tenant_id))
        return current

    def set_brand_logo(self, tenant_id: str, storage_key: str, metadata: dict) -> None:
        """Commit the new logo pointer only after the object-store write succeeds."""
        from app.brand_logo import validate_brand_logo_key

        validate_brand_logo_key(storage_key, tenant_id)
        with self._store.connection() as conn:
            if not self._store.is_postgres:
                conn.execute("BEGIN IMMEDIATE")
            lock = " FOR UPDATE" if self._store.is_postgres else ""
            row = conn.execute(f"SELECT payload FROM enterprise_configs WHERE tenant_id=?{lock}", (tenant_id,)).fetchone()
            if not row:
                raise LookupError("企业配置不存在。")
            current = json.loads(row["payload"])
            current["brand_logo"] = storage_key
            current["brand_logo_metadata"] = metadata
            current.pop("brand_mark_logo", None)
            conn.execute("UPDATE enterprise_configs SET payload=? WHERE tenant_id=?",
                         (json.dumps(current, ensure_ascii=False), tenant_id))

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
            file = conn.execute("SELECT knowledge_base_id FROM knowledge_files WHERE id=? AND tenant_id=?", (file_id, tenant_id)).fetchone()
            if file is None or any(chunk.get("knowledge_base_id") != file["knowledge_base_id"] for chunk in chunks):
                raise ValueError("KNOWLEDGE_FILE_OWNERSHIP_MISMATCH")
            schema = self.knowledge_embedding_schema(conn) if self._store.is_postgres else None
            for chunk in chunks:
                vector = chunk.get("embedding")
                if schema:
                    if (schema["type"] != "vector" or not isinstance(vector, list)
                            or len(vector) != schema["dimension"] or not vector
                            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector)):
                        raise ValueError("KNOWLEDGE_EMBEDDING_SCHEMA_DIMENSION_MISMATCH")
            conn.execute("DELETE FROM knowledge_chunks WHERE tenant_id=? AND file_id=?", (tenant_id, file_id))
            for chunk in chunks:
                vector = chunk.get("embedding")
                embedding = "[" + ",".join(f"{value:.10g}" for value in vector) + "]" if self._store.is_postgres and vector else json.dumps(vector)
                if self._store.is_postgres:
                    # Schema004–015 omits this redundant column. Preserve it when
                    # an already-approved deployment has it; derive reads from vector_dims.
                    dimension_field = ",embedding_dimension" if schema["dimension_column"] else ""
                    dimension_placeholder = ",?" if schema["dimension_column"] else ""
                    values = (str(uuid.uuid4()), tenant_id, chunk.get("knowledge_base_id"), file_id, chunk["content"], chunk.get("title"), chunk.get("section"), chunk.get("page_number"), chunk["chunk_index"], embedding, chunk.get("embedding_provider"), chunk.get("embedding_model"), chunk.get("embedding_version"), json.dumps(chunk.get("metadata", {}), ensure_ascii=False))
                    conn.execute("INSERT INTO knowledge_chunks(id,tenant_id,knowledge_base_id,file_id,content,title,section,page_number,chunk_index,embedding,embedding_provider,embedding_model,embedding_version,metadata" + dimension_field + ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?" + dimension_placeholder + ")", values + ((len(vector),) if schema["dimension_column"] else ()))
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
        with self._store.connection() as conn:
            asset = conn.execute("SELECT id FROM assets WHERE id=? AND tenant_id=?", (asset_id, tenant_id)).fetchone()
            if not asset:
                return False
            # asset_metadata predates ON DELETE CASCADE in production. Remove
            # the dependent row in the same transaction so deleting an asset
            # also revokes the enterprise-wide image grant deterministically.
            conn.execute("DELETE FROM asset_metadata WHERE asset_id=?", (asset_id,))
            cursor = conn.execute("DELETE FROM assets WHERE id=? AND tenant_id=?", (asset_id, tenant_id))
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
