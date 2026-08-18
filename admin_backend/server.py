#!/usr/bin/env python3
"""Kunyuan AI admin service. Binds to loopback; Nginx owns authentication."""
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("KUNYUAN_ADMIN_DB", "/var/lib/kunyuan-admin/admin.db"))
DEPLOY_SCRIPT = os.environ.get("KUNYUAN_ADMIN_DEPLOY_SCRIPT", "/usr/local/sbin/kunyuan-admin-deploy")
MAX_BODY = 32 * 1024
DEFAULT_CONTENT = {
    "hero_summary": "从员工 AI 能力、业务场景重构，到 Agent、Ontology 与企业级 AI 系统建设，陪伴企业完成 AI 原生化转型。",
    "cta_summary": "一次 30–60 分钟的初步沟通，帮助您判断当前阶段、优先场景与下一步行动。",
}


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize():
    with db() as conn:
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
        """)
        for key, value in DEFAULT_CONTENT.items():
            conn.execute("INSERT OR IGNORE INTO content VALUES (?, ?, ?)", (key, value, now()))


def json_response(handler, payload, status=HTTPStatus.OK):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


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
        path = urlparse(self.path).path
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
                        "INSERT INTO leads(name, company, contact, challenge, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (*fields.values(), stamp, stamp),
                    )
                return json_response(self, {"id": cursor.lastrowid, "message": "已收到，我们将在 1 个工作日内联系您。"}, HTTPStatus.CREATED)
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
