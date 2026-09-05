#!/usr/bin/env python3
"""Kunyuan AI admin service. Binds to loopback; Nginx owns authentication."""
import base64
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
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("KUNYUAN_ADMIN_DB", "/var/lib/kunyuan-admin/admin.db"))
DATABASE_URL = os.environ.get("KUNYUAN_DATABASE_URL", "")
LEGACY_SQLITE_DB = Path(os.environ.get("KUNYUAN_LEGACY_SQLITE_DB", "/var/lib/kunyuan-admin/admin.db"))
MIGRATION_MARKER = Path(os.environ.get("KUNYUAN_DATABASE_MIGRATION_MARKER", "/var/lib/kunyuan-admin/postgres-migration.done"))
DEPLOY_SCRIPT = os.environ.get("KUNYUAN_ADMIN_DEPLOY_SCRIPT", "/usr/local/sbin/kunyuan-admin-deploy")
WORKSPACE_RUNTIME_RUNNER = os.environ.get("KUNYUAN_WORKSPACE_RUNTIME_RUNNER", "/usr/local/sbin/kunyuan-agent-run")
WORKSPACE_RUNTIME_TIMEOUT_SECONDS = int(os.environ.get("KUNYUAN_WORKSPACE_RUNTIME_TIMEOUT_SECONDS", "90"))
MAX_BODY = 32 * 1024
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
PHONE_EMAIL_SUFFIX = "@phone.kunyuan.invalid"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
WORKSPACE_SSO_COOKIE = "kunyuan_workspace_sso"
WORKSPACE_SSO_TTL_SECONDS = 60 * 5
WORKSPACE_SSO_SECRET = os.environ.get("KUNYUAN_WORKSPACE_SSO_SECRET", "")
IMAGE_GATEWAY_INTERNAL_URL = os.environ.get("KUNYUAN_IMAGE_GATEWAY_INTERNAL_URL", "http://127.0.0.1:8020").rstrip("/")
IMAGE_GATEWAY_ADMIN_TOKEN = os.environ.get("KUNYUAN_IMAGE_GATEWAY_ADMIN_TOKEN", "")
CHAT_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{16,96}$")
WORKSPACE_AGENT_TEMPLATES = {
    "research": {
        "name": "行业研究助手",
        "description": "梳理行业动态、竞品与关键决策信息。",
        "instructions": "你是一名严谨的行业研究助手。先澄清研究范围，再按结论、证据、风险和下一步输出。",
    },
    "sales": {
        "name": "销售策略助手",
        "description": "协助准备客户洞察、拜访策略与跟进内容。",
        "instructions": "你是一名企业销售策略助手。围绕客户目标、决策角色、价值假设和下一步行动给出可执行建议。",
    },
    "service": {
        "name": "客户服务助手",
        "description": "把常见咨询转为清晰、可靠的服务答复。",
        "instructions": "你是一名专业客户服务助手。回答准确、简洁，无法确认的信息要明确说明并建议人工跟进。",
    },
    "custom": {
        "name": "自定义智能体",
        "description": "从一个空白角色开始，配置专属工作方式。",
        "instructions": "你是一个企业智能体。遵守用户设定的角色、边界和交付格式。",
    },
}


class InsufficientAiCredit(Exception):
    """Raised when a user has no available paid AI usage quota."""
DEFAULT_CONTENT = {
    "hero_summary": "从员工 AI 能力、业务场景重构，到 Agent、Ontology 与企业级 AI 系统建设，陪伴企业完成 AI 原生化转型。",
    "cta_summary": "一次 30–60 分钟的初步沟通，帮助您判断当前阶段、优先场景与下一步行动。",
    "hero_title": "让 AI 真正进入企业业务流程。", "hero_cta": "预约 AI 转型诊断",
    "problem_title": "做了 AI，却没有形成价值。", "problem_1": "AI 停留在个人工具，没有进入团队协作与业务流程。", "problem_2": "做了很多 Agent，却没有连接知识、规则与真实系统。", "problem_3": "业务与技术缺少共同语言，不知道什么值得优先投入。", "problem_4": "Demo 可以运行，却无法走向生产和持续演化。",
    "path_title": "不是更多技术，而是一条升级路径。", "path_1_title": "AI 能力启航", "path_1_desc": "建立共同语言，让组织先用起来。", "path_2_title": "AI 场景重构", "path_2_desc": "把模糊需求变成可决策的项目蓝图。", "path_3_title": "AI 原生组织建设", "path_3_desc": "让 AI 嵌入知识、流程、系统与治理。",
    "sprint_title": "3 天，把“想做 AI”变成可决策的蓝图。", "sprint_summary": "让业务、技术和管理层围绕同一份可验证的项目方案做决策。",
    "method_title": "不是做一个 Agent，而是构建组织能力。", "industry_title": "让 AI 理解企业的业务世界。", "industry_1": "制造业", "industry_2": "物流供应链", "industry_3": "企业法务", "industry_4": "客服与营销", "cta_title": "不知道从哪里开始？预约企业 AI 转型诊断。",
}
CONTENT_GROUPS = {
    "hero": "首页首屏", "problem": "AI 困境", "path": "转型路径", "sprint": "诊断冲刺", "method": "方法体系", "industry": "行业场景", "cta": "预约转化",
}
CONTENT_FIELDS = {key: {"label": label, "group": group} for key, label, group in (
    ("hero_title", "首屏主标题", "hero"), ("hero_summary", "首屏介绍", "hero"), ("hero_cta", "首屏按钮", "hero"),
    ("problem_title", "区域标题", "problem"), *((f"problem_{i}", f"困境 {i}", "problem") for i in range(1, 5)),
    ("path_title", "区域标题", "path"), *((f"path_{i}_{part}", f"路径 {i}{' 标题' if part == 'title' else ' 说明'}", "path") for i in range(1, 4) for part in ("title", "desc")),
    ("sprint_title", "区域标题", "sprint"), ("sprint_summary", "区域说明", "sprint"), ("method_title", "区域标题", "method"),
    ("industry_title", "区域标题", "industry"), *((f"industry_{i}", f"行业 {i}", "industry") for i in range(1, 5)),
    ("cta_title", "预约标题", "cta"), ("cta_summary", "预约说明", "cta"),
)}


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
            CREATE TABLE IF NOT EXISTS workspace_agents (
              id BIGSERIAL PRIMARY KEY,
              user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              template_key TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              instructions TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workspace_agents_user ON workspace_agents(user_id, id DESC);
            CREATE TABLE IF NOT EXISTS workspace_conversations (
              id BIGSERIAL PRIMARY KEY,
              agent_id BIGINT NOT NULL REFERENCES workspace_agents(id) ON DELETE CASCADE,
              user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              title TEXT NOT NULL DEFAULT '新会话', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workspace_conversations_agent ON workspace_conversations(agent_id, user_id, id DESC);
            CREATE TABLE IF NOT EXISTS workspace_messages (
              id BIGSERIAL PRIMARY KEY,
              conversation_id BIGINT NOT NULL REFERENCES workspace_conversations(id) ON DELETE CASCADE,
              role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
              content TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workspace_messages_conversation ON workspace_messages(conversation_id, id);
            CREATE TABLE IF NOT EXISTS workspace_runs (
              id BIGSERIAL PRIMARY KEY,
              agent_id BIGINT NOT NULL REFERENCES workspace_agents(id) ON DELETE CASCADE,
              user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              conversation_id BIGINT REFERENCES workspace_conversations(id) ON DELETE SET NULL,
              status TEXT NOT NULL CHECK (status IN ('queued', 'completed', 'failed')),
              input TEXT NOT NULL, output TEXT, runtime TEXT NOT NULL DEFAULT 'preview',
              created_at TEXT NOT NULL, completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_workspace_runs_user ON workspace_runs(user_id, id DESC);
            CREATE TABLE IF NOT EXISTS user_ai_credits (
              user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
              balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0), updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_ai_credit_ledger (
              id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              run_id BIGINT REFERENCES workspace_runs(id) ON DELETE SET NULL,
              delta INTEGER NOT NULL, balance_after INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_credit_ledger_user ON user_ai_credit_ledger(user_id, id DESC);
            CREATE TABLE IF NOT EXISTS api_keys (
              id BIGSERIAL PRIMARY KEY,
              user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              token_name TEXT NOT NULL UNIQUE,
              token_prefix TEXT NOT NULL,
              created_at TEXT NOT NULL,
              revoked_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_one_active_per_user
              ON api_keys(user_id) WHERE revoked_at IS NULL;
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
            CREATE TABLE IF NOT EXISTS workspace_agents (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              template_key TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              instructions TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused')),
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workspace_agents_user ON workspace_agents(user_id, id DESC);
            CREATE TABLE IF NOT EXISTS workspace_conversations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              agent_id INTEGER NOT NULL REFERENCES workspace_agents(id) ON DELETE CASCADE,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              title TEXT NOT NULL DEFAULT '新会话', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workspace_conversations_agent ON workspace_conversations(agent_id, user_id, id DESC);
            CREATE TABLE IF NOT EXISTS workspace_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id INTEGER NOT NULL REFERENCES workspace_conversations(id) ON DELETE CASCADE,
              role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
              content TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workspace_messages_conversation ON workspace_messages(conversation_id, id);
            CREATE TABLE IF NOT EXISTS workspace_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              agent_id INTEGER NOT NULL REFERENCES workspace_agents(id) ON DELETE CASCADE,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              conversation_id INTEGER REFERENCES workspace_conversations(id) ON DELETE SET NULL,
              status TEXT NOT NULL CHECK (status IN ('queued', 'completed', 'failed')),
              input TEXT NOT NULL, output TEXT, runtime TEXT NOT NULL DEFAULT 'preview',
              created_at TEXT NOT NULL, completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_workspace_runs_user ON workspace_runs(user_id, id DESC);
            CREATE TABLE IF NOT EXISTS user_ai_credits (
              user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
              balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0), updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_ai_credit_ledger (
              id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              run_id INTEGER REFERENCES workspace_runs(id) ON DELETE SET NULL,
              delta INTEGER NOT NULL, balance_after INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_credit_ledger_user ON user_ai_credit_ledger(user_id, id DESC);
            CREATE TABLE IF NOT EXISTS api_keys (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              token_name TEXT NOT NULL UNIQUE,
              token_prefix TEXT NOT NULL,
              created_at TEXT NOT NULL,
              revoked_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_one_active_per_user
              ON api_keys(user_id) WHERE revoked_at IS NULL;
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


def json_response(handler, payload, status=HTTPStatus.OK, headers=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
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
    if len(values["name"]) > 50:
        raise ValueError("昵称不能超过 50 个字符。")
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


def _base64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_workspace_sso(user_id):
    """Issue a brief signed proof for Nginx to gate the separate AI workspace."""
    if not WORKSPACE_SSO_SECRET:
        raise RuntimeError("AI 工作台统一登录尚未完成配置。")
    payload = {
        "aud": "ai-workspace",
        "exp": int(datetime.now(timezone.utc).timestamp()) + WORKSPACE_SSO_TTL_SECONDS,
        "sub": int(user_id),
    }
    encoded_payload = _base64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signed = f"v1.{encoded_payload}".encode("ascii")
    signature = _base64url(hmac.new(WORKSPACE_SSO_SECRET.encode("utf-8"), signed, hashlib.sha256).digest())
    return f"v1.{encoded_payload}.{signature}"


def valid_workspace_sso(token):
    """Return the workspace user id only for an authentic, unexpired token."""
    if not WORKSPACE_SSO_SECRET or not token:
        return None
    try:
        version, encoded_payload, signature = token.split(".")
        if version != "v1":
            return None
        signed = f"{version}.{encoded_payload}".encode("ascii")
        expected = _base64url(hmac.new(WORKSPACE_SSO_SECRET.encode("utf-8"), signed, hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_base64url_decode(encoded_payload))
        if payload.get("aud") != "ai-workspace" or int(payload.get("exp", 0)) < int(datetime.now(timezone.utc).timestamp()):
            return None
        user_id = int(payload.get("sub", 0))
        return user_id if user_id > 0 else None
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, base64.binascii.Error):
        return None


def workspace_sso_from_request(handler):
    cookie_header = handler.headers.get("Cookie", "")
    for item in cookie_header.split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name == WORKSPACE_SSO_COOKIE:
            return valid_workspace_sso(value)
    return None


def workspace_user(handler):
    """Resolve the user only for an Nginx-authenticated workspace request."""
    if self_header := handler.headers.get("X-Kunyuan-Workspace-Auth"):
        if self_header != "1":
            return None
    else:
        return None
    user_id = workspace_sso_from_request(handler)
    if not user_id:
        return None
    with db() as conn:
        row = conn.execute(
            """SELECT u.id, u.email, u.phone AS login_phone, p.name, p.company, p.job_title
               FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id WHERE u.id=?""",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    user = dict(row)
    if user.get("email", "").endswith(PHONE_EMAIL_SUFFIX):
        user["email"] = None
    return user


def workspace_agent_payload(data):
    template_key = str(data.get("template_key", "custom"))
    if template_key not in WORKSPACE_AGENT_TEMPLATES:
        raise ValueError("智能体模板无效。")
    template = WORKSPACE_AGENT_TEMPLATES[template_key]
    name = str(data.get("name", template["name"])).strip()
    description = str(data.get("description", template["description"])).strip()
    instructions = str(data.get("instructions", template["instructions"])).strip()
    if not 2 <= len(name) <= 50:
        raise ValueError("智能体名称需为 2–50 个字符。")
    if len(description) > 240:
        raise ValueError("智能体说明不能超过 240 个字符。")
    if not 10 <= len(instructions) <= 6000:
        raise ValueError("请填写 10–6000 个字符的智能体指令。")
    return name, template_key, description, instructions


def workspace_agent(conn, agent_id, user_id):
    return conn.execute(
        """SELECT id, user_id, name, template_key, description, instructions, status, created_at, updated_at
           FROM workspace_agents WHERE id=? AND user_id=?""",
        (agent_id, user_id),
    ).fetchone()


def ai_credit_balance(conn, user_id):
    conn.execute(
        "INSERT INTO user_ai_credits(user_id, balance, updated_at) VALUES (?, 0, ?) ON CONFLICT (user_id) DO NOTHING",
        (user_id, now()),
    )
    return conn.execute("SELECT balance, updated_at FROM user_ai_credits WHERE user_id=?", (user_id,)).fetchone()


def reserve_ai_credit(conn, user_id, run_id):
    """Atomically reserve one successful-workspace-run credit."""
    ai_credit_balance(conn, user_id)
    row = conn.execute(
        """UPDATE user_ai_credits SET balance=balance-1, updated_at=?
           WHERE user_id=? AND balance >= 1 RETURNING balance""",
        (now(), user_id),
    ).fetchone()
    if not row:
        return None
    stamp = now()
    conn.execute(
        """INSERT INTO user_ai_credit_ledger(user_id, run_id, delta, balance_after, reason, created_at)
           VALUES (?, ?, -1, ?, 'workspace_run', ?)""",
        (user_id, run_id, row["balance"], stamp),
    )
    return row["balance"]


def refund_ai_credit(conn, user_id, run_id):
    row = conn.execute(
        "UPDATE user_ai_credits SET balance=balance+1, updated_at=? WHERE user_id=? RETURNING balance",
        (now(), user_id),
    ).fetchone()
    conn.execute(
        """INSERT INTO user_ai_credit_ledger(user_id, run_id, delta, balance_after, reason, created_at)
           VALUES (?, ?, 1, ?, 'runtime_refund', ?)""",
        (user_id, run_id, row["balance"], now()),
    )
    return row["balance"]


def workspace_runtime_reply(agent, message, history):
    """Run a user task through the server-owned, tool-free Harness profile."""
    history_text = "\n".join(
        f"{'用户' if item['role'] == 'user' else '智能体'}：{item['content']}"
        for item in history[-8:]
    )[-8000:]
    task = f"""你正在作为锟元 AI 工作台中的「{agent['name']}」提供文本回答。

智能体职责：
{agent['instructions']}

工作边界：仅基于对话内容进行分析、写作和建议。不要声称访问过文件、终端、网络、数据库或任何外部系统；无法确认的事实请明确说明。请用中文输出，结构清晰、可直接交付给业务用户。

最近对话：
{history_text}

请回答本次用户任务：
{message}
"""
    try:
        result = subprocess.run(
            [WORKSPACE_RUNTIME_RUNNER, str(agent["user_id"]), str(agent["id"]), task],
            capture_output=True,
            text=True,
            timeout=WORKSPACE_RUNTIME_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "本次智能体运行超时，请稍后重试。"
    except OSError:
        return None, "智能体运行服务暂不可用，请稍后重试。"
    if result.returncode != 0:
        return None, "智能体运行未完成，请稍后重试。"
    reply = result.stdout.strip()
    if not reply:
        return None, "智能体没有返回内容，请稍后重试。"
    return reply[-12000:], None


def gateway_admin_request(path, method="GET", payload=None):
    """Call the gateway's loopback-only management API without exposing its admin secret."""
    if not IMAGE_GATEWAY_ADMIN_TOKEN:
        raise RuntimeError("图片 API Key 服务尚未完成配置。")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urlrequest.Request(
        f"{IMAGE_GATEWAY_INTERNAL_URL}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {IMAGE_GATEWAY_ADMIN_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlrequest.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urlerror.HTTPError, urlerror.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError("API Key 服务暂时不可用，请稍后再试。") from exc


def issue_image_api_key(user_id):
    token_name = f"user-{user_id}-{secrets.token_hex(8)}"
    result = gateway_admin_request("/internal/v1/tokens", "POST", {"name": token_name})
    token = str(result.get("token", ""))
    if not token:
        raise RuntimeError("API Key 服务返回无效结果。")
    return token_name, token


def revoke_image_api_key(token_name):
    gateway_admin_request(f"/internal/v1/tokens/{token_name}", "DELETE")


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
            if path == "/api/auth/workspace/verify":
                # This endpoint is called only by Nginx's internal auth_request.
                if self.headers.get("X-Kunyuan-Workspace-Auth") != "1":
                    return json_response(self, {"error": "仅供内部鉴权使用。"}, HTTPStatus.NOT_FOUND)
                user_id = workspace_sso_from_request(self)
                if not user_id:
                    return json_response(self, {"error": "AI 工作台登录已失效。"}, HTTPStatus.UNAUTHORIZED)
                with db() as conn:
                    user_exists = conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone()
                if not user_exists:
                    return json_response(self, {"error": "AI 工作台登录已失效。"}, HTTPStatus.UNAUTHORIZED)
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Workspace-User-Id", str(user_id))
                self.end_headers()
                return
            if path == "/api/auth/me":
                user = authenticated_user(self)
                if not user:
                    return json_response(self, {"error": "登录已失效或未登录。"}, HTTPStatus.UNAUTHORIZED)
                return json_response(self, {"user": user})
            if path == "/api/account/ai-balance":
                user = authenticated_user(self)
                if not user:
                    return json_response(self, {"error": "登录已失效或未登录。"}, HTTPStatus.UNAUTHORIZED)
                with db() as conn:
                    credit = ai_credit_balance(conn, user["id"])
                return json_response(self, {"balance": credit["balance"], "unit": "次", "updated_at": credit["updated_at"]})
            if path == "/api/developer/api-keys":
                user = authenticated_user(self)
                if not user:
                    return json_response(self, {"error": "登录已失效或未登录。"}, HTTPStatus.UNAUTHORIZED)
                with db() as conn:
                    rows = conn.execute(
                        """SELECT id, token_prefix, created_at, revoked_at FROM api_keys
                           WHERE user_id=? ORDER BY id DESC""",
                        (user["id"],),
                    ).fetchall()
                return json_response(self, {"items": [dict(row) for row in rows]})
            if path == "/api/workspace/bootstrap":
                user = workspace_user(self)
                if not user:
                    return json_response(self, {"error": "工作台登录已失效。"}, HTTPStatus.UNAUTHORIZED)
                with db() as conn:
                    agents = conn.execute(
                        """SELECT id, name, template_key, description, status, created_at, updated_at
                           FROM workspace_agents WHERE user_id=? ORDER BY id DESC""",
                        (user["id"],),
                    ).fetchall()
                    runs = conn.execute(
                        """SELECT r.id, r.agent_id, a.name AS agent_name, r.status, r.runtime, r.created_at, r.completed_at
                           FROM workspace_runs r JOIN workspace_agents a ON a.id=r.agent_id
                           WHERE r.user_id=? ORDER BY r.id DESC LIMIT 8""",
                        (user["id"],),
                    ).fetchall()
                    credit = ai_credit_balance(conn, user["id"])
                templates = [{"key": key, "name": item["name"], "description": item["description"]} for key, item in WORKSPACE_AGENT_TEMPLATES.items()]
                return json_response(self, {"user": user, "agents": [dict(row) for row in agents], "runs": [dict(row) for row in runs], "templates": templates, "runtime": "harness", "quota": {"balance": credit["balance"], "unit": "次"}})
            if path == "/api/workspace/agents":
                user = workspace_user(self)
                if not user:
                    return json_response(self, {"error": "工作台登录已失效。"}, HTTPStatus.UNAUTHORIZED)
                with db() as conn:
                    rows = conn.execute(
                        """SELECT id, name, template_key, description, status, created_at, updated_at
                           FROM workspace_agents WHERE user_id=? ORDER BY id DESC""",
                        (user["id"],),
                    ).fetchall()
                return json_response(self, {"items": [dict(row) for row in rows]})
            if path.startswith("/api/workspace/agents/") and path.endswith("/messages"):
                user = workspace_user(self)
                if not user:
                    return json_response(self, {"error": "工作台登录已失效。"}, HTTPStatus.UNAUTHORIZED)
                agent_id = int(path.split("/")[4])
                with db() as conn:
                    agent = workspace_agent(conn, agent_id, user["id"])
                    if not agent:
                        return json_response(self, {"error": "未找到该智能体。"}, HTTPStatus.NOT_FOUND)
                    if ai_credit_balance(conn, user["id"])["balance"] < 1:
                        return json_response(self, {"error": "AI 使用额度不足，请充值后再试。"}, HTTPStatus.PAYMENT_REQUIRED)
                    conversation = conn.execute(
                        """SELECT id, title, created_at, updated_at FROM workspace_conversations
                           WHERE agent_id=? AND user_id=? ORDER BY id DESC LIMIT 1""",
                        (agent_id, user["id"]),
                    ).fetchone()
                    if not conversation:
                        return json_response(self, {"conversation": None, "items": []})
                    rows = conn.execute(
                        "SELECT id, role, content, created_at FROM workspace_messages WHERE conversation_id=? ORDER BY id ASC",
                        (conversation["id"],),
                    ).fetchall()
                return json_response(self, {"conversation": dict(conversation), "items": [dict(row) for row in rows]})
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
                page = max(1, int(parse_qs(parsed.query).get("page", ["1"])[0]))
                page_size = 20
                with db() as conn:
                    rows = conn.execute("SELECT * FROM leads ORDER BY id DESC LIMIT ? OFFSET ?", (page_size, (page - 1) * page_size)).fetchall()
                    total = conn.execute("SELECT COUNT(*) AS total FROM leads").fetchone()["total"]
                return json_response(self, {"items": [dict(row) for row in rows], "total": total, "page": page, "page_size": page_size})
            if path == "/admin/api/users":
                page = max(1, int(parse_qs(parsed.query).get("page", ["1"])[0]))
                page_size = 20
                offset = (page - 1) * page_size
                with db() as conn:
                    rows = conn.execute(
                        """SELECT u.id, u.email, u.phone AS login_phone, u.created_at, u.last_login_at,
                                  p.name, p.phone, p.company, p.job_title, p.consent_at,
                                  COALESCE(c.balance, 0) AS ai_credit_balance, c.updated_at AS ai_credit_updated_at
                           FROM users u
                           LEFT JOIN user_profiles p ON p.user_id=u.id
                           LEFT JOIN user_ai_credits c ON c.user_id=u.id
                           ORDER BY u.id DESC LIMIT ? OFFSET ?""", (page_size, offset)
                    ).fetchall()
                    total = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
                items = []
                for row in rows:
                    user = dict(row)
                    if user.get("email", "").endswith(PHONE_EMAIL_SUFFIX):
                        user["email"] = None
                    items.append(user)
                return json_response(self, {"items": items, "total": total, "page": page, "page_size": page_size})
            if path == "/admin/api/content":
                with db() as conn:
                    rows = conn.execute("SELECT content_key, content_value, updated_at FROM content ORDER BY content_key").fetchall()
                items = []
                for row in rows:
                    item = dict(row)
                    item.update(CONTENT_FIELDS.get(item["content_key"], {"label": item["content_key"], "group": "hero"}))
                    items.append(item)
                return json_response(self, {"groups": CONTENT_GROUPS, "items": items})
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
            if path == "/api/auth/workspace/session":
                user = authenticated_user(self)
                if not user:
                    return json_response(self, {"error": "请先登录官网账号。"}, HTTPStatus.UNAUTHORIZED)
                token = issue_workspace_sso(user["id"])
                cookie = (
                    f"{WORKSPACE_SSO_COOKIE}={token}; Max-Age={WORKSPACE_SSO_TTL_SECONDS}; "
                    "Path=/; Domain=.luckio.cn; Secure; HttpOnly; SameSite=Lax"
                )
                return json_response(self, {"url": "https://ai.luckio.cn/"}, headers={"Set-Cookie": cookie})
            if path == "/api/developer/api-keys":
                user = authenticated_user(self)
                if not user:
                    return json_response(self, {"error": "登录已失效或未登录。"}, HTTPStatus.UNAUTHORIZED)
                with db() as conn:
                    active = conn.execute(
                        "SELECT id FROM api_keys WHERE user_id=? AND revoked_at IS NULL",
                        (user["id"],),
                    ).fetchone()
                if active:
                    return json_response(self, {"error": "请先撤销现有 API Key，再创建新的 Key。"}, HTTPStatus.CONFLICT)
                token_name, token = issue_image_api_key(user["id"])
                try:
                    with db() as conn:
                        cursor = conn.execute(
                            """INSERT INTO api_keys(user_id, token_name, token_prefix, created_at)
                               VALUES (?, ?, ?, ?) RETURNING id""",
                            (user["id"], token_name, token[:16], now()),
                        )
                        key_id = cursor.fetchone()["id"]
                except Exception as exc:
                    try:
                        revoke_image_api_key(token_name)
                    except RuntimeError:
                        pass
                    if is_unique_violation(exc):
                        return json_response(self, {"error": "请先撤销现有 API Key，再创建新的 Key。"}, HTTPStatus.CONFLICT)
                    raise
                return json_response(
                    self,
                    {"item": {"id": key_id, "token_prefix": token[:16], "created_at": now(), "revoked_at": None}, "api_key": token},
                    HTTPStatus.CREATED,
                )
            if path == "/api/workspace/agents":
                user = workspace_user(self)
                if not user:
                    return json_response(self, {"error": "工作台登录已失效。"}, HTTPStatus.UNAUTHORIZED)
                name, template_key, description, instructions = workspace_agent_payload(data)
                stamp = now()
                with db() as conn:
                    cursor = conn.execute(
                        """INSERT INTO workspace_agents(user_id, name, template_key, description, instructions, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                        (user["id"], name, template_key, description, instructions, stamp, stamp),
                    )
                    agent_id = cursor.fetchone()["id"]
                    agent = workspace_agent(conn, agent_id, user["id"])
                return json_response(self, {"agent": dict(agent)}, HTTPStatus.CREATED)
            if path.startswith("/api/workspace/agents/") and path.endswith("/messages"):
                user = workspace_user(self)
                if not user:
                    return json_response(self, {"error": "工作台登录已失效。"}, HTTPStatus.UNAUTHORIZED)
                agent_id = int(path.split("/")[4])
                message = str(data.get("message", "")).strip()
                if not 1 <= len(message) <= 4000:
                    return json_response(self, {"error": "请输入不超过 4000 个字符的任务。"}, HTTPStatus.BAD_REQUEST)
                stamp = now()
                with db() as conn:
                    agent = workspace_agent(conn, agent_id, user["id"])
                    if not agent:
                        return json_response(self, {"error": "未找到该智能体。"}, HTTPStatus.NOT_FOUND)
                    conversation = conn.execute(
                        """SELECT id FROM workspace_conversations WHERE agent_id=? AND user_id=?
                           ORDER BY id DESC LIMIT 1""",
                        (agent_id, user["id"]),
                    ).fetchone()
                    if conversation:
                        conversation_id = conversation["id"]
                    else:
                        cursor = conn.execute(
                            """INSERT INTO workspace_conversations(agent_id, user_id, title, created_at, updated_at)
                               VALUES (?, ?, ?, ?, ?) RETURNING id""",
                            (agent_id, user["id"], message[:60], stamp, stamp),
                        )
                        conversation_id = cursor.fetchone()["id"]
                    history = [dict(row) for row in conn.execute(
                        """SELECT role, content FROM workspace_messages WHERE conversation_id=?
                           ORDER BY id DESC LIMIT 8""",
                        (conversation_id,),
                    ).fetchall()][::-1]
                    conn.execute(
                        "INSERT INTO workspace_messages(conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
                        (conversation_id, message, stamp),
                    )
                    cursor = conn.execute(
                        """INSERT INTO workspace_runs(agent_id, user_id, conversation_id, status, input, runtime, created_at)
                           VALUES (?, ?, ?, 'queued', ?, 'harness', ?) RETURNING id""",
                        (agent_id, user["id"], conversation_id, message, stamp),
                    )
                    run_id = cursor.fetchone()["id"]
                    remaining_credit = reserve_ai_credit(conn, user["id"], run_id)
                    if remaining_credit is None:
                        raise InsufficientAiCredit()
                    history.append({"role": "user", "content": message})
                reply, runtime_error = workspace_runtime_reply(agent, message, history)
                output = reply or runtime_error
                status = "completed" if reply else "failed"
                completed_at = now()
                with db() as conn:
                    conn.execute(
                        "INSERT INTO workspace_messages(conversation_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
                        (conversation_id, output, completed_at),
                    )
                    conn.execute(
                        """UPDATE workspace_runs SET status=?, output=?, completed_at=? WHERE id=? AND user_id=?""",
                        (status, output, completed_at, run_id, user["id"]),
                    )
                    conn.execute("UPDATE workspace_conversations SET updated_at=? WHERE id=?", (completed_at, conversation_id))
                    if not reply:
                        remaining_credit = refund_ai_credit(conn, user["id"], run_id)
                return json_response(self, {"run": {"id": run_id, "status": status, "runtime": "harness"}, "reply": output, "created_at": completed_at, "quota": {"balance": remaining_credit, "unit": "次"}}, HTTPStatus.CREATED)
            if path == "/api/auth/register":
                email, phone, password, referral_code = read_registration_credentials(data)
                profile = profile_values(data, require_name=True)
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
        except InsufficientAiCredit:
            json_response(self, {"error": "AI 使用额度不足，请充值后再试。"}, HTTPStatus.PAYMENT_REQUIRED)
        except (ValueError, json.JSONDecodeError) as exc:
            json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except subprocess.TimeoutExpired:
            json_response(self, {"error": "发布检查超时。"}, HTTPStatus.GATEWAY_TIMEOUT)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            if not path.startswith("/api/developer/api-keys/"):
                return self.send_error(HTTPStatus.NOT_FOUND)
            user = authenticated_user(self)
            if not user:
                return json_response(self, {"error": "登录已失效或未登录。"}, HTTPStatus.UNAUTHORIZED)
            key_id = int(path.rsplit("/", 1)[-1])
            with db() as conn:
                row = conn.execute(
                    "SELECT token_name FROM api_keys WHERE id=? AND user_id=? AND revoked_at IS NULL",
                    (key_id, user["id"]),
                ).fetchone()
            if not row:
                return json_response(self, {"error": "未找到可撤销的 API Key。"}, HTTPStatus.NOT_FOUND)
            revoke_image_api_key(row["token_name"])
            with db() as conn:
                conn.execute("UPDATE api_keys SET revoked_at=? WHERE id=? AND user_id=?", (now(), key_id, user["id"]))
            return json_response(self, {"ok": True})
        except ValueError:
            json_response(self, {"error": "API Key 标识无效。"}, HTTPStatus.BAD_REQUEST)
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
            if path.startswith("/admin/api/users/") and path.endswith("/ai-credits"):
                user_id = int(path.split("/")[4])
                delta = int(data.get("delta", 0))
                reason = str(data.get("reason", "manual_admin"))[:120] or "manual_admin"
                if not -100000 <= delta <= 100000 or delta == 0:
                    return json_response(self, {"error": "额度调整需为 -100000 至 100000 的非零整数。"}, HTTPStatus.BAD_REQUEST)
                with db() as conn:
                    if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
                        return json_response(self, {"error": "未找到用户。"}, HTTPStatus.NOT_FOUND)
                    ai_credit_balance(conn, user_id)
                    credit = conn.execute(
                        """UPDATE user_ai_credits SET balance=balance+?, updated_at=?
                           WHERE user_id=? AND balance+? >= 0 RETURNING balance, updated_at""",
                        (delta, now(), user_id, delta),
                    ).fetchone()
                    if not credit:
                        return json_response(self, {"error": "扣减后额度不能小于 0。"}, HTTPStatus.CONFLICT)
                    conn.execute(
                        """INSERT INTO user_ai_credit_ledger(user_id, delta, balance_after, reason, created_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (user_id, delta, credit["balance"], reason, now()),
                    )
                return json_response(self, {"user_id": user_id, "balance": credit["balance"], "unit": "次", "updated_at": credit["updated_at"]})
            if path.startswith("/admin/api/users/"):
                user_id = int(path.rsplit("/", 1)[-1])
                profile = profile_values(data, require_name=True)
                email = str(data.get("email", "")).strip().lower()
                phone = normalize_phone(data.get("phone", ""))
                if not valid_email(email) or email.endswith(PHONE_EMAIL_SUFFIX):
                    return json_response(self, {"error": "请输入有效的邮箱地址。"}, HTTPStatus.BAD_REQUEST)
                if not PHONE_PATTERN.fullmatch(phone):
                    return json_response(self, {"error": "请输入有效的中国大陆手机号。"}, HTTPStatus.BAD_REQUEST)
                profile["phone"] = phone
                try:
                    with db() as conn:
                        conn.execute("UPDATE users SET email=?, phone=?, updated_at=? WHERE id=?", (stored_email(email, phone), phone, now(), user_id))
                        conn.execute("UPDATE user_profiles SET name=?, phone=?, company=?, job_title=?, updated_at=? WHERE user_id=?", (*profile.values(), now(), user_id))
                except Exception as exc:
                    if is_unique_violation(exc):
                        return json_response(self, {"error": "该手机号或邮箱已被其他用户使用。"}, HTTPStatus.CONFLICT)
                    raise
                return json_response(self, {"ok": True})
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, json.JSONDecodeError) as exc:
            json_response(self, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


if __name__ == "__main__":
    initialize()
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("KUNYUAN_ADMIN_PORT", "18780"))), Handler).serve_forever()
