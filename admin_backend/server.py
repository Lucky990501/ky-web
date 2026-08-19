#!/usr/bin/env python3
"""Kunyuan AI admin service. Binds to loopback; Nginx owns authentication."""
import json
import os
import hashlib
import hmac
import re
import secrets
import sqlite3
import subprocess
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("KUNYUAN_ADMIN_DB", "/var/lib/kunyuan-admin/admin.db"))
DATABASE_URL = os.environ.get("KUNYUAN_DATABASE_URL", "")
LEGACY_SQLITE_DB = Path(os.environ.get("KUNYUAN_LEGACY_SQLITE_DB", "/var/lib/kunyuan-admin/admin.db"))
MIGRATION_MARKER = Path(os.environ.get("KUNYUAN_DATABASE_MIGRATION_MARKER", "/var/lib/kunyuan-admin/postgres-migration.done"))
DEPLOY_SCRIPT = os.environ.get("KUNYUAN_ADMIN_DEPLOY_SCRIPT", "/usr/local/sbin/kunyuan-admin-deploy")
MAX_BODY = 32 * 1024
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
PHONE_EMAIL_SUFFIX = "@phone.kunyuan.invalid"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
CHAT_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
DEFAULT_CONTENT = {
    "hero_summary": "从员工 AI 能力、业务场景重构，到 Agent、Ontology 与企业级 AI 系统建设，陪伴企业完成 AI 原生化转型。",
    "cta_summary": "一次 30–60 分钟的初步沟通，帮助您判断当前阶段、优先场景与下一步行动。",
}


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class PostgresConnection:
    """Small compatibility wrapper so application queries stay parameterized."""
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _exc, _tb):
        if exc_type:
            self.connection.rollback()
        else:
            self.connection.commit()
        self.connection.close()

    def execute(self, query, params=()):
        from psycopg2.extras import RealDictCursor
        cursor = self.connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(query.replace("?", "%s"), params)
        return cursor


def db():
    if DATABASE_URL:
        try:
            import psycopg2
        except ImportError as exc:
            raise RuntimeError("PostgreSQL 驱动未安装。请安装 python3-psycopg2。") from exc
        return PostgresConnection(psycopg2.connect(DATABASE_URL, connect_timeout=5))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize():
    with db() as conn:
        if DATABASE_URL:
            schema = """
            CREATE TABLE IF NOT EXISTS leads (
              id BIGSERIAL PRIMARY KEY,
              name TEXT NOT NULL, company TEXT NOT NULL, contact TEXT NOT NULL,
              challenge TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content (
              content_key TEXT PRIMARY KEY, content_value TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
              id BIGSERIAL PRIMARY KEY,
              email TEXT UNIQUE,
              phone TEXT,
              referral_code TEXT,
              password_hash TEXT NOT NULL,
              password_salt TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              last_login_at TEXT
            );
            CREATE TABLE IF NOT EXISTS user_profiles (
              user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
              name TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
              company TEXT NOT NULL DEFAULT '', job_title TEXT NOT NULL DEFAULT '',
              consent_at TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_sessions (
              id BIGSERIAL PRIMARY KEY,
              user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
              expires_at BIGINT NOT NULL, revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_user_sessions_active ON user_sessions(token_hash, expires_at);
            CREATE TABLE IF NOT EXISTS chat_messages (
              id BIGSERIAL PRIMARY KEY,
              session_id TEXT NOT NULL,
              user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
              role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
              content TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);
            """
            for statement in schema.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute("ALTER TABLE users ALTER COLUMN email DROP NOT NULL")
            conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone TEXT")
            conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique ON users(phone)")
        else:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS leads (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL, company TEXT NOT NULL, contact TEXT NOT NULL,
              challenge TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content (
              content_key TEXT PRIMARY KEY, content_value TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              email TEXT NOT NULL COLLATE NOCASE UNIQUE,
              phone TEXT,
              referral_code TEXT,
              password_hash TEXT NOT NULL,
              password_salt TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_login_at TEXT
            );
            CREATE TABLE IF NOT EXISTS user_profiles (
              user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
              name TEXT NOT NULL DEFAULT '',
              phone TEXT NOT NULL DEFAULT '',
              company TEXT NOT NULL DEFAULT '',
              job_title TEXT NOT NULL DEFAULT '',
              consent_at TEXT,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_sessions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              token_hash TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              expires_at INTEGER NOT NULL,
              revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_user_sessions_active
              ON user_sessions(token_hash, expires_at);
            CREATE TABLE IF NOT EXISTS chat_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
              role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
              content TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chat_messages_session
              ON chat_messages(session_id, id);
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
            if "phone" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN phone TEXT")
            if "referral_code" not in columns:
                conn.execute("ALTER TABLE users ADD COLUMN referral_code TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone_unique ON users(phone)")
        for key, value in DEFAULT_CONTENT.items():
            conn.execute("INSERT INTO content(content_key, content_value, updated_at) VALUES (?, ?, ?) ON CONFLICT (content_key) DO NOTHING", (key, value, now()))
    migrate_legacy_sqlite()


def migrate_legacy_sqlite():
    """One-time copy of the pre-PostgreSQL leads/content database during deployment."""
    if not DATABASE_URL or os.environ.get("KUNYUAN_MIGRATE_LEGACY_SQLITE") != "1" or MIGRATION_MARKER.exists() or not LEGACY_SQLITE_DB.exists():
        return
    legacy = sqlite3.connect(LEGACY_SQLITE_DB)
    legacy.row_factory = sqlite3.Row
    try:
        with db() as conn:
            for row in legacy.execute("SELECT name, company, contact, challenge, status, created_at, updated_at FROM leads"):
                conn.execute(
                    "INSERT INTO leads(name, company, contact, challenge, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    tuple(row),
                )
            for row in legacy.execute("SELECT content_key, content_value, updated_at FROM content"):
                conn.execute("UPDATE content SET content_value=?, updated_at=? WHERE content_key=?", (row["content_value"], row["updated_at"], row["content_key"]))
        MIGRATION_MARKER.parent.mkdir(parents=True, exist_ok=True)
        MIGRATION_MARKER.touch()
    finally:
        legacy.close()


def json_response(handler, payload, status=HTTPStatus.OK):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def password_hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1).hex()


def valid_email(value):
    return len(value) <= 254 and bool(EMAIL_PATTERN.fullmatch(value))


def normalize_phone(value):
    phone = re.sub(r"[\s-]", "", str(value))
    if phone.startswith("+86"):
        phone = phone[3:]
    elif phone.startswith("0086"):
        phone = phone[4:]
    return phone


def read_credentials(data):
    identity_type = str(data.get("identity_type", "email"))
    password = data.get("password", "")
    if not isinstance(password, str) or not 9 <= len(password) <= 128:
        raise ValueError("密码至少需要 9 个字符。")
    if identity_type == "email":
        email = str(data.get("email", "")).strip().lower()
        if not valid_email(email) or email.endswith(PHONE_EMAIL_SUFFIX):
            raise ValueError("请输入有效的邮箱地址。")
        return identity_type, email, None, password
    if identity_type == "phone":
        phone = normalize_phone(data.get("phone", ""))
        if not PHONE_PATTERN.fullmatch(phone):
            raise ValueError("请输入有效的中国大陆手机号。")
        return identity_type, None, phone, password
    raise ValueError("请选择邮箱或手机号登录。")


def read_registration_credentials(data):
    email = str(data.get("email", "")).strip().lower()
    phone = normalize_phone(data.get("phone", ""))
    password = data.get("password", "")
    referral_code = str(data.get("referral_code", "")).strip()
    if not valid_email(email) or email.endswith(PHONE_EMAIL_SUFFIX):
        raise ValueError("请输入有效的邮箱地址。")
    if not PHONE_PATTERN.fullmatch(phone):
        raise ValueError("请输入有效的中国大陆手机号。")
    if not isinstance(password, str) or not 9 <= len(password) <= 128:
        raise ValueError("密码至少需要 9 个字符。")
    if len(referral_code) > 64:
        raise ValueError("推荐码不能超过 64 个字符。")
    return email, phone, password, referral_code


def stored_email(email, phone):
    """SQLite's legacy email column is NOT NULL; hide its internal fallback from clients."""
    if email or DATABASE_URL:
        return email
    return f"phone-{phone}{PHONE_EMAIL_SUFFIX}"


def profile_values(data, require_name=False):
    raw = data.get("profile", data)
    if not isinstance(raw, dict):
        raise ValueError("资料格式无效。")
    values = {key: str(raw.get(key, "")).strip() for key in ("name", "phone", "company", "job_title")}
    if any(len(value) > 200 for value in values.values()):
        raise ValueError("单项资料不能超过 200 个字符。")
    if require_name and not values["name"]:
        raise ValueError("请填写姓名。")
    return values


def issue_session(conn, user_id):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    conn.execute("DELETE FROM user_sessions WHERE expires_at < ? OR revoked_at IS NOT NULL", (int(datetime.now(timezone.utc).timestamp()),))
    conn.execute(
        "INSERT INTO user_sessions(user_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (user_id, token_hash, now(), int(datetime.now(timezone.utc).timestamp()) + SESSION_TTL_SECONDS),
    )
    return token


def authenticated_user(handler):
    authorization = handler.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token_hash = hashlib.sha256(authorization[7:].encode("utf-8")).hexdigest()
    with db() as conn:
        row = conn.execute(
            """SELECT u.id, u.email, u.phone AS login_phone, u.created_at, u.last_login_at, p.name, p.phone, p.company, p.job_title, p.consent_at
               FROM user_sessions s JOIN users u ON u.id=s.user_id JOIN user_profiles p ON p.user_id=u.id
               WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at >= ?""",
            (token_hash, int(datetime.now(timezone.utc).timestamp())),
        ).fetchone()
    if not row:
        return None
    user = dict(row)
    if user.get("email", "").endswith(PHONE_EMAIL_SUFFIX):
        user["email"] = None
    return user


def support_reply(message):
    """Safe first-line concierge until a dedicated LLM provider is configured."""
    text = message.lower()
    if any(word in text for word in ("预约", "诊断", "沟通", "咨询")):
        return "可以。您可以点击页面中的“预约诊断”，留下企业情况；我们会在 1 个工作日内联系您。"
    if any(word in text for word in ("制造", "供应链", "物流", "法务", "客服", "营销")):
        return "锟元AI可从真实业务场景切入。我们会先梳理目标、流程、知识与系统边界，再判断最值得优先投入的方向。"
    if any(word in text for word in ("agent", "rag", "知识", "ontology", "大模型")):
        return "技术只是实现路径的一部分。建议先明确业务价值和人机边界，再规划知识、Workflow、Agent 与工程治理。"
    return "我可以协助您了解 AI 转型诊断、行业场景和组织能力建设。您目前最想解决哪类业务问题？"


def chat_session(data):
    value = str(data.get("session_id", ""))
    if not CHAT_SESSION_PATTERN.fullmatch(value):
        raise ValueError("会话标识无效。")
    return value


def is_unique_violation(exc):
    """Recognize the duplicate-key errors raised by the supported databases."""
    return isinstance(exc, sqlite3.IntegrityError) or getattr(exc, "pgcode", None) == "23505"


class Handler(BaseHTTPRequestHandler):
    server_version = "KunyuanAdmin/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            raise ValueError("请求内容无效")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def serve_file(self, filename, content_type):
        content = (ROOT / filename).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/admin", "/admin/"):
                return self.serve_file("admin.html", "text/html; charset=utf-8")
            if path == "/admin/app.css":
                return self.serve_file("admin.css", "text/css; charset=utf-8")
            if path == "/admin/app.js":
                return self.serve_file("admin.js", "application/javascript; charset=utf-8")
            if path == "/api/site-content":
                with db() as conn:
                    rows = conn.execute("SELECT content_key, content_value FROM content").fetchall()
                return json_response(self, {row["content_key"]: row["content_value"] for row in rows})
            if path == "/api/auth/me":
                user = authenticated_user(self)
                if not user:
                    return json_response(self, {"error": "登录已失效或未登录。"}, HTTPStatus.UNAUTHORIZED)
                return json_response(self, {"user": user})
            if path == "/api/chat/history":
                session_id = parse_qs(parsed.query).get("session", [""])[0]
                if not CHAT_SESSION_PATTERN.fullmatch(session_id):
                    return json_response(self, {"items": []})
                user = authenticated_user(self)
                with db() as conn:
                    if user:
                        rows = conn.execute(
                            "SELECT role, content, created_at FROM chat_messages WHERE session_id=? AND (user_id=? OR user_id IS NULL) ORDER BY id DESC LIMIT 50",
                            (session_id, user["id"]),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            "SELECT role, content, created_at FROM chat_messages WHERE session_id=? AND user_id IS NULL ORDER BY id DESC LIMIT 50",
                            (session_id,),
                        ).fetchall()
                return json_response(self, {"items": [dict(row) for row in reversed(rows)]})
            if path == "/admin/api/leads":
                with db() as conn:
                    rows = conn.execute("SELECT * FROM leads ORDER BY id DESC LIMIT 200").fetchall()
                return json_response(self, {"items": [dict(row) for row in rows]})
            if path == "/admin/api/content":
                with db() as conn:
                    rows = conn.execute("SELECT content_key, content_value, updated_at FROM content ORDER BY content_key").fetchall()
                return json_response(self, {"items": [dict(row) for row in rows]})
            if path == "/admin/api/status":
                current = Path("/var/www/kunyuan-ai/current")
                release = str(current.resolve()) if current.exists() else "未发布"
                return json_response(self, {"release": release, "updated_at": now()})
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self.read_json()
            if path == "/api/leads":
                fields = {key: str(data.get(key, "")).strip() for key in ("name", "company", "contact", "challenge")}
                if any(not value or len(value) > 2000 for value in fields.values()):
                    return json_response(self, {"error": "请完整填写预约信息。"}, HTTPStatus.BAD_REQUEST)
                with db() as conn:
                    stamp = now()
                    cursor = conn.execute(
                        "INSERT INTO leads(name, company, contact, challenge, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
                        (*fields.values(), stamp, stamp),
                    )
                    lead_id = cursor.fetchone()["id"]
                return json_response(self, {"id": lead_id, "message": "已收到，我们将在 1 个工作日内联系您。"}, HTTPStatus.CREATED)
            if path == "/api/auth/register":
                email, phone, password, referral_code = read_registration_credentials(data)
                profile = profile_values(data)
                profile["phone"] = phone
                salt = secrets.token_bytes(16)
                stamp = now()
                try:
                    with db() as conn:
                        cursor = conn.execute(
                            "INSERT INTO users(email, phone, referral_code, password_hash, password_salt, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
                            (stored_email(email, phone), phone, referral_code or None, password_hash(password, salt), salt.hex(), stamp, stamp),
                        )
                        user_id = cursor.fetchone()["id"]
                        conn.execute(
                            "INSERT INTO user_profiles(user_id, name, phone, company, job_title, consent_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (user_id, *profile.values(), stamp, stamp),
                        )
                        token = issue_session(conn, user_id)
                except Exception as exc:
                    if is_unique_violation(exc):
                        return json_response(self, {"error": "该手机号或邮箱已注册，请直接登录。"}, HTTPStatus.CONFLICT)
                    raise
                return json_response(self, {"token": token, "message": "注册成功。"}, HTTPStatus.CREATED)
            if path == "/api/auth/login":
                identity_type, email, phone, password = read_credentials(data)
                identifier_column, identifier = ("phone", phone) if identity_type == "phone" else ("email", email)
                with db() as conn:
                    user = conn.execute(f"SELECT id, password_hash, password_salt FROM users WHERE {identifier_column}=?", (identifier,)).fetchone()
                    if not user or not hmac.compare_digest(password_hash(password, bytes.fromhex(user["password_salt"])), user["password_hash"]):
                        label = "手机号" if identity_type == "phone" else "邮箱"
                        return json_response(self, {"error": f"{label}或密码不正确。"}, HTTPStatus.UNAUTHORIZED)
                    stamp = now()
                    conn.execute("UPDATE users SET last_login_at=?, updated_at=? WHERE id=?", (stamp, stamp, user["id"]))
                    token = issue_session(conn, user["id"])
                return json_response(self, {"token": token, "message": "登录成功。"})
            if path == "/api/chat":
                session_id = chat_session(data)
                message = str(data.get("message", "")).strip()
                if not message or len(message) > 1200:
                    return json_response(self, {"error": "请输入不超过 1200 字的问题。"}, HTTPStatus.BAD_REQUEST)
                user = authenticated_user(self)
                stamp = now()
                reply = support_reply(message)
                with db() as conn:
                    conn.execute(
                        "INSERT INTO chat_messages(session_id, user_id, role, content, created_at) VALUES (?, ?, 'user', ?, ?)",
                        (session_id, user["id"] if user else None, message, stamp),
                    )
                    conn.execute(
                        "INSERT INTO chat_messages(session_id, user_id, role, content, created_at) VALUES (?, ?, 'assistant', ?, ?)",
                        (session_id, user["id"] if user else None, reply, now()),
                    )
                return json_response(self, {"reply": reply})
            if path == "/admin/api/deploy":
                result = subprocess.run([DEPLOY_SCRIPT], text=True, capture_output=True, timeout=90, check=False)
                status = HTTPStatus.OK if result.returncode == 0 else HTTPStatus.BAD_GATEWAY
                return json_response(self, {"ok": result.returncode == 0, "output": (result.stdout + result.stderr)[-4000:]}, status)
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except subprocess.TimeoutExpired:
            json_response(self, {"error": "发布检查超时。"}, HTTPStatus.GATEWAY_TIMEOUT)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PUT(self):
        path = urlparse(self.path).path
        try:
            data = self.read_json()
            if path == "/admin/api/content":
                values = data.get("values", {})
                if not isinstance(values, dict) or set(values) - set(DEFAULT_CONTENT):
                    return json_response(self, {"error": "内容字段无效。"}, HTTPStatus.BAD_REQUEST)
                with db() as conn:
                    stamp = now()
                    for key, value in values.items():
                        value = str(value).strip()
                        if not value or len(value) > 1200:
                            return json_response(self, {"error": "内容不能为空且不能超过 1200 字。"}, HTTPStatus.BAD_REQUEST)
                        conn.execute("UPDATE content SET content_value=?, updated_at=? WHERE content_key=?", (value, stamp, key))
                return json_response(self, {"ok": True})
            if path == "/api/auth/profile":
                user = authenticated_user(self)
                if not user:
                    return json_response(self, {"error": "登录已失效或未登录。"}, HTTPStatus.UNAUTHORIZED)
                profile = profile_values(data, require_name=True)
                with db() as conn:
                    conn.execute(
                        "UPDATE user_profiles SET name=?, phone=?, company=?, job_title=?, updated_at=? WHERE user_id=?",
                        (*profile.values(), now(), user["id"]),
                    )
                return json_response(self, {"ok": True})
            if path.startswith("/admin/api/leads/"):
                lead_id = int(path.rsplit("/", 1)[-1])
                status = data.get("status")
                if status not in {"new", "contacted", "closed"}:
                    return json_response(self, {"error": "状态无效。"}, HTTPStatus.BAD_REQUEST)
                with db() as conn:
                    conn.execute("UPDATE leads SET status=?, updated_at=? WHERE id=?", (status, now(), lead_id))
                return json_response(self, {"ok": True})
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


if __name__ == "__main__":
    initialize()
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("KUNYUAN_ADMIN_PORT", "18780"))), Handler).serve_forever()
