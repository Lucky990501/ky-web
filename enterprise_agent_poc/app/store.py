from __future__ import annotations

import json
import sqlite3
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class _PostgresConnection:
    """Small DB-API compatibility layer while the product store is migrated."""
    def __init__(self, connection) -> None:
        self._connection = connection

    @staticmethod
    def _sql(sql: str) -> str:
        sql = sql.replace("?", "%s")
        if re.match(r"\s*INSERT\s+OR\s+IGNORE\s+INTO\s+", sql, re.I):
            sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, count=1, flags=re.I)
            if "ON CONFLICT" not in sql.upper():
                sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        return sql

    def execute(self, sql: str, params=None):
        return self._connection.execute(self._sql(sql), params or ())


class POCStore:
    def __init__(self, database: Path | str | None) -> None:
        raw = str(database) if database is not None else "sqlite:///./.runtime-data/poc.db"
        self.database_url = raw if raw.startswith(("sqlite:///", "postgresql://", "postgres://")) else f"sqlite:///{raw}"
        self.database_path = Path(self.database_url.removeprefix("sqlite:///")) if self.database_url.startswith("sqlite:///") else None

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgresql://", "postgres://"))

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection | _PostgresConnection]:
        if self.is_postgres:
            from psycopg import connect
            from psycopg.rows import dict_row
            conn = connect(self.database_url, row_factory=dict_row)
            try:
                yield _PostgresConnection(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            return
        assert self.database_path is not None
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        if self.is_postgres:
            migration = Path(__file__).resolve().parents[1] / "migrations" / "postgres" / "001_workbench_v1.sql"
            with self.connection() as conn:
                conn.execute(migration.read_text(encoding="utf-8"))
            return
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tenants (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, poc_api_key TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS enterprise_configs (
                  tenant_id TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
                  payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                  title TEXT NOT NULL, body TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS assets (
                  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                  name TEXT NOT NULL, asset_type TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]', url TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                  agent_id TEXT NOT NULL, runtime_profile_id TEXT NOT NULL, runtime_thread_id TEXT NOT NULL,
                  runtime_version TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS execution_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT,
                  event_type TEXT NOT NULL, payload TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS run_traces (
                  run_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                  tenant_id TEXT NOT NULL, agent_id TEXT NOT NULL, codex_thread_id TEXT,
                  status TEXT NOT NULL, payload TEXT NOT NULL,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS gate2_checks (
                  name TEXT PRIMARY KEY,
                  status TEXT NOT NULL,
                  payload TEXT NOT NULL,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def seed_demo_data(self) -> None:
        self.initialize()
        records = [
            (
                "tenant-a",
                "教育企业 A",
                "tenant-a-local-key",
                {
                    "data_classification": "synthetic_gate2_test_data",
                    "brand_name": "启明教育",
                    "primary_color": "#1584CC",
                    "secondary_color": "#FB9931",
                    "slogan": "从高分突破到终身成长的赋能体系",
                    "brand_style": ["活力", "温暖", "专业"],
                    "forbidden_claims": ["100%提分", "保过", "保证进入年级前十"],
                    "required_rules": ["使用企业品牌色", "展示官方Slogan", "Logo不可修改"],
                },
                ("秋季冲刺课程 A", "面向初中学生的数学与英语秋季冲刺课程，强调诊断和分层教学。"),
                ("asset-a-logo", "启明教育 Logo", "logo", ["brand", "logo"], "https://example.invalid/tenant-a/logo.png"),
            ),
            (
                "tenant-b",
                "教育企业 B",
                "tenant-b-local-key",
                {
                    "brand_name": "知行学堂",
                    "primary_color": "#C92E35",
                    "secondary_color": "#F6C645",
                    "slogan": "让每一次学习看得见进步",
                    "brand_style": ["坚定", "亲和", "成长感"],
                    "forbidden_claims": ["100%提分", "保过", "保证进入年级前十"],
                    "required_rules": ["使用企业品牌色", "展示官方Slogan", "Logo不可修改"],
                },
                ("秋季冲刺课程 B", "面向小学高年级学生的秋季综合提升课程，突出学习习惯和家校协同。"),
                ("asset-b-logo", "知行学堂 Logo", "logo", ["brand", "logo"], "https://example.invalid/tenant-b/logo.png"),
            ),
        ]
        with self.connection() as conn:
            for tenant_id, name, key, config, knowledge, asset in records:
                conn.execute("INSERT OR IGNORE INTO tenants(id, name, poc_api_key) VALUES (?, ?, ?)", (tenant_id, name, key))
                conn.execute(
                    "INSERT INTO enterprise_configs(tenant_id, payload) VALUES (?, ?) "
                    "ON CONFLICT(tenant_id) DO UPDATE SET payload=excluded.payload",
                    (tenant_id, json.dumps(config)),
                )
                conn.execute(
                    """INSERT INTO knowledge_documents(tenant_id, title, body)
                       SELECT ?, ?, ?
                       WHERE NOT EXISTS (
                         SELECT 1 FROM knowledge_documents WHERE tenant_id=? AND title=?
                       )""",
                    (tenant_id, *knowledge, tenant_id, knowledge[0]),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO assets(id, tenant_id, name, asset_type, tags, url) VALUES (?, ?, ?, ?, ?, ?)",
                    (asset[0], tenant_id, asset[1], asset[2], json.dumps(asset[3]), asset[4]),
                )

    def tenant_for_api_key(self, api_key: str) -> str | None:
        with self.connection() as conn:
            row = conn.execute("SELECT id FROM tenants WHERE poc_api_key=?", (api_key,)).fetchone()
        return row["id"] if row else None

    def enterprise_config(self, tenant_id: str) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT payload FROM enterprise_configs WHERE tenant_id=?", (tenant_id,)).fetchone()
        if not row:
            raise LookupError("企业配置不存在。")
        return json.loads(row["payload"])

    def knowledge_search(self, tenant_id: str, query: str, limit: int = 5) -> list[dict]:
        terms = [term for term in query.lower().split() if term]
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id, title, body FROM knowledge_documents WHERE tenant_id=? ORDER BY id DESC", (tenant_id,)
            ).fetchall()
        ranked = []
        for row in rows:
            haystack = f"{row['title']} {row['body']}".lower()
            score = sum(term in haystack for term in terms) or (1 if not terms else 0)
            if score:
                ranked.append({"id": row["id"], "title": row["title"], "content": row["body"], "score": score})
        return sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]

    def asset_search(self, tenant_id: str, query: str, asset_type: str | None = None) -> list[dict]:
        sql = "SELECT id, name, asset_type, tags, url FROM assets WHERE tenant_id=?"
        params: list[str] = [tenant_id]
        if asset_type:
            sql += " AND asset_type=?"
            params.append(asset_type)
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        query_lower = query.lower()
        return [
            {"id": row["id"], "name": row["name"], "type": row["asset_type"], "tags": json.loads(row["tags"]), "url": row["url"]}
            for row in rows
            if not query_lower or query_lower in (row["name"] + " " + row["tags"]).lower()
        ]

    def save_conversation(self, conversation_id: str, tenant_id: str, agent_id: str, profile_id: str, thread_id: str, runtime_version: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (conversation_id, tenant_id, agent_id, profile_id, thread_id, runtime_version),
            )

    def conversation(self, conversation_id: str, tenant_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id=? AND tenant_id=?", (conversation_id, tenant_id)).fetchone()
        return dict(row) if row else None

    def conversation_messages(self, conversation_id: str, tenant_id: str, limit: int = 20) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT m.role,m.content FROM messages m JOIN conversations c ON c.id=m.conversation_id "
                "WHERE m.conversation_id=? AND c.tenant_id=? ORDER BY m.created_at DESC,m.id DESC LIMIT ?",
                (conversation_id, tenant_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def replace_conversation_thread(
        self, conversation_id: str, tenant_id: str, expected_thread_id: str, new_thread_id: str
    ) -> bool:
        with self.connection() as conn:
            cursor = conn.execute(
                "UPDATE conversations SET runtime_thread_id=? WHERE id=? AND tenant_id=? AND runtime_thread_id=?",
                (new_thread_id, conversation_id, tenant_id, expected_thread_id),
            )
        return cursor.rowcount == 1

    def log_event(self, conversation_id: str | None, event_type: str, payload: dict) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO execution_events(conversation_id, event_type, payload) VALUES (?, ?, ?)",
                (conversation_id, event_type, json.dumps(payload, ensure_ascii=False)),
            )

    def create_run_trace(self, run_id: str, conversation_id: str, tenant_id: str, agent_id: str, thread_id: str, payload: dict) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO run_traces(run_id, conversation_id, tenant_id, agent_id, codex_thread_id, status, payload) VALUES (?, ?, ?, ?, ?, 'running', ?)",
                (run_id, conversation_id, tenant_id, agent_id, thread_id, json.dumps(payload, ensure_ascii=False)),
            )

    def finish_run_trace(self, run_id: str, status: str, payload: dict, thread_id: str | None = None) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE run_traces SET status=?, payload=?, codex_thread_id=COALESCE(?, codex_thread_id), completed_at=CURRENT_TIMESTAMP WHERE run_id=?",
                (status, json.dumps(payload, ensure_ascii=False), thread_id, run_id),
            )

    def run_trace(self, run_id: str, tenant_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM run_traces WHERE run_id=? AND tenant_id=?", (run_id, tenant_id)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def latest_mcp_audit(self, tenant_id: str, tool_name: str) -> dict | None:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT payload, created_at FROM execution_events WHERE event_type='mcp.tool' ORDER BY id DESC"
            ).fetchall()
        for row in rows:
            payload = json.loads(row["payload"])
            if payload.get("tenant_id") == tenant_id and payload.get("tool") == tool_name:
                return {"tool": payload["tool"], "status": payload["status"], "created_at": row["created_at"]}
        return None

    def record_gate2_check(self, name: str, status: str, payload: dict) -> None:
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO gate2_checks(name, status, payload, updated_at) VALUES (?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(name) DO UPDATE SET status=excluded.status, payload=excluded.payload, updated_at=CURRENT_TIMESTAMP",
                (name, status, json.dumps(payload, ensure_ascii=False)),
            )

    def gate2_check(self, name: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT name, status, payload, updated_at FROM gate2_checks WHERE name=?", (name,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def latest_run_trace(self) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT run_id, tenant_id, status, payload, created_at, completed_at FROM run_traces ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        result = dict(row); result["payload"] = json.loads(result["payload"]); return result
