"""One Test acceptance budget, not a billing service or a public authorization API.

Runtime never creates or resets this ledger. Operator initialization is separate.
Every HTTP attempt reserves a durable slot before the only upstream send point.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from uuid import UUID, uuid4

from app.security import RuntimePrincipal, RuntimeTokenIssuer
from app.store import POCStore


class TestTextGateDenied(PermissionError):
    __test__ = False


def require(ok, code):
    if not ok:
        raise TestTextGateDenied(code)


def uuid(value):
    require(isinstance(value, str) and str(UUID(value)) == value, "test_text_gate_identity")
    return value


class RequestBudget:
    def __init__(self, path: Path, database_url: str):
        self.path = Path(path)
        self.database_sha = hashlib.sha256(database_url.encode()).hexdigest()

    @property
    def anchor_path(self):
        return self.path.with_name(self.path.name+".initialized")

    @contextmanager
    def connect(self):
        # mode=rw is essential: loss must never create a fresh two-call budget.
        s = self.path.lstat()
        require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1
                and self.path.resolve(strict=True) == self.path.absolute(), "test_text_gate_file")
        if os.name == "posix":
            require(s.st_uid == os.getuid() and stat.S_IMODE(s.st_mode) == 0o600,
                    "test_text_gate_permissions")
            require(not self.path.parent.stat().st_mode & 0o022, "test_text_gate_directory")
        anchor = self.anchor_path.lstat()
        require(stat.S_ISREG(anchor.st_mode) and anchor.st_nlink == 1
                and self.anchor_path.resolve(strict=True) == self.anchor_path.absolute()
                and anchor.st_size <= 2048, "test_text_gate_anchor")
        if os.name == "posix":
            require(anchor.st_uid == os.getuid() and stat.S_IMODE(anchor.st_mode) == 0o400,
                    "test_text_gate_anchor_permissions")
        conn = sqlite3.connect(self.path.as_uri()+"?mode=rw", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @classmethod
    def initialize(cls, path, database_url, tenant_id, operation_id, expires_at):
        uuid(operation_id)
        require(tenant_id and tenant_id != "*" and time.time() < expires_at <= time.time()+86400,
                "test_text_gate_scope")
        path = Path(path).absolute()
        budget = cls(path, database_url)
        # Persistent one-time marker also prevents reinitialization after loss
        # of the mutable counter. Never remove this marker to replenish quota.
        anchor = {"operation_id":operation_id,"tenant_id":tenant_id,"database_sha":budget.database_sha}
        fd = os.open(budget.anchor_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd,"wb") as f:
            f.write(json.dumps(anchor,sort_keys=True).encode())
            f.flush()
            os.fsync(f.fileno())
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with budget.connect() as c:
            c.executescript("""
                CREATE TABLE budget(id INTEGER PRIMARY KEY CHECK(id=1),version TEXT NOT NULL,
                  environment TEXT NOT NULL CHECK(environment='test'),database_sha TEXT NOT NULL,
                  tenant_id TEXT NOT NULL,operation_id TEXT NOT NULL,max_requests INTEGER NOT NULL CHECK(max_requests=2),
                  spent INTEGER NOT NULL CHECK(spent BETWEEN 0 AND 2),expires_at REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE allowed_tasks(task_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,agent_id TEXT NOT NULL);
                CREATE TABLE attempts(slot INTEGER PRIMARY KEY CHECK(slot BETWEEN 1 AND 2),
                  task_id TEXT NOT NULL,run_id TEXT NOT NULL,request_sha TEXT NOT NULL,at REAL NOT NULL);
            """)
            c.execute("INSERT INTO budget VALUES(1,'MINIMAL_TEST_TEXT_GATE_V1','test',?,?,?,?,0,?,0)",
                      (budget.database_sha,tenant_id,operation_id,2,expires_at))
        # The path and its parent must themselves be on persistent local storage.
        if os.name == "posix":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return budget

    def scope(self, c):
        row = c.execute("SELECT * FROM budget WHERE id=1").fetchone()
        require(row and row["version"] == "MINIMAL_TEST_TEXT_GATE_V1" and row["environment"] == "test"
                and row["database_sha"] == self.database_sha and row["max_requests"] == 2
                and not row["revoked"] and time.time() < row["expires_at"], "test_text_gate_unavailable")
        anchor = json.loads(self.anchor_path.read_bytes())
        require(anchor == {"operation_id":row["operation_id"],"tenant_id":row["tenant_id"],
                           "database_sha":self.database_sha}, "test_text_gate_anchor_identity")
        attempts = c.execute("SELECT count(*) AS n FROM attempts").fetchone()["n"]
        require(attempts == row["spent"], "test_text_gate_state_inconsistent")
        return row

    def allow(self, task):
        uuid(task["id"])
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            scope = self.scope(c)
            require(task["tenant_id"] == scope["tenant_id"] and task["agent_id"] != "image-agent"
                    and task["status"] in {"queued", "running"}, "test_text_gate_task_denied")
            values = (task["id"], task["user_id"], task["agent_id"])
            existing = c.execute("SELECT * FROM allowed_tasks WHERE task_id=?", (task["id"],)).fetchone()
            require(not existing or tuple(existing) == values, "test_text_gate_owner_drift")
            c.execute("INSERT OR IGNORE INTO allowed_tasks VALUES(?,?,?)", values)

    def check(self, task):
        with self.connect() as c:
            scope = self.scope(c)
            allowed = c.execute("SELECT * FROM allowed_tasks WHERE task_id=?", (task["id"],)).fetchone()
            require(task["tenant_id"] == scope["tenant_id"] and allowed
                    and task["user_id"] == allowed["user_id"] and task["agent_id"] == allowed["agent_id"]
                    and task["status"] == "running", "test_text_gate_task_denied")
        return task

    def reserve(self, task, raw):
        self.check(task)
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            scope = self.scope(c)
            require(scope["spent"] < 2, "test_text_gate_exhausted")
            slot = scope["spent"]+1
            c.execute("INSERT INTO attempts VALUES(?,?,?,?,?)",
                      (slot,task["id"],task["run_id"],hashlib.sha256(raw).hexdigest(),time.time()))
            c.execute("UPDATE budget SET spent=? WHERE id=1", (slot,))
        # Commit is complete before returning; failures after here are NOT refunded.
        return slot


class TestTextGate:
    __test__ = False

    def __init__(self, settings, issuer: RuntimeTokenIssuer):
        require(settings.environment == "test" and settings.model_provider_id == "deepseek"
                and settings.model_id == "deepseek-v4-pro" and settings.reasoning_effort == "high"
                and settings.model_wire_api == "responses"
                and settings.model_base_url.rstrip('/') == "https://api.deepseek.com"
                and settings.image_provider_id == "disabled-test-provider"
                and settings.embedding_provider == "local-hash", "test_text_gate_configuration")
        require(settings.test_text_gate_state is not None, "test_text_gate_state_required")
        self.settings, self.issuer = settings, issuer
        self.budget = RequestBudget(settings.test_text_gate_state, settings.database_url)
        self.store = POCStore(settings.database_url)
        self.server = None
        self.contexts = {}

    def task(self, tenant, task_id, agent, user=None, run_id=None):
        uuid(task_id)
        # Read actual accepted Task and live Run, never a client-supplied owner.
        with self.store.connection() as c:
            row = c.execute("SELECT t.*,r.tenant_id AS run_tenant,r.agent_id AS run_agent,r.status AS run_status "
                            "FROM tasks t JOIN run_traces r ON r.run_id=t.run_id "
                            "JOIN users u ON u.id=t.user_id AND u.tenant_id=t.tenant_id AND u.account_status='enabled' "
                            "WHERE t.id=?", (task_id,)).fetchone()
            require(row is not None, "test_text_gate_task_missing")
            task = dict(row)
            require(task["tenant_id"] == task["run_tenant"] == tenant
                    and task["agent_id"] == task["run_agent"] == agent
                    and task["run_status"] == "running" and task["status"] == "running"
                    and (user is None or task["user_id"] == user)
                    and (run_id is None or task["run_id"] == run_id), "test_text_gate_task_context")
        return self.budget.check(task)

    async def bind(self, profile, task_id):
        require(profile.model_provider_id == "deepseek" and profile.model_id == "deepseek-v4-pro"
                and profile.reasoning_effort == "high" and profile.agent_id != "image-agent", "test_text_gate_profile")
        task = self.task(profile.tenant_id, task_id, profile.agent_id)
        if self.server is None:
            self.server = await asyncio.start_server(self._handle, '127.0.0.1', 0, limit=65536)
        context = uuid4().hex
        self.contexts[context] = (profile.tenant_id,task_id,profile.agent_id,task["user_id"],task["run_id"])
        credential = self.issuer.issue(RuntimePrincipal(profile.tenant_id,profile.agent_id,context,
                                                       ("test-text:request",),int(time.time())+900))
        port = self.server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}", credential

    @staticmethod
    def bounded_payload(raw):
        p = json.loads(raw)
        require(p.get("model") == "deepseek-v4-pro" and p.get("reasoning") == {"effort":"high"},
                "test_text_gate_model")
        output = p.get("max_output_tokens",512)
        require(type(output) is int and 0 < output <= 512 and isinstance(p.get("input"),str)
                and not any(p.get(k) for k in ("tools","instructions","previous_response_id","conversation")),
                "test_text_gate_input_bound_not_proven")
        require(set(p) <= {"model","input","reasoning","max_output_tokens","stream","store","metadata"},
                "test_text_gate_input_bound_not_proven")
        # Same official single-user/high-effort framing proof as accepted smoke:
        # 563 framed bytes for its 22-byte input; BPE merges never increase bytes.
        require(len(p["input"].encode())+541 <= 1024, "test_text_gate_input_1024_exceeded")
        p["max_output_tokens"] = output
        return p

    async def forward(self, credential, raw):
        principal = self.issuer.verify(credential, "test-text:request")
        context = self.contexts.get(principal.runtime_profile_id)
        require(context and principal.tenant_id == context[0] and principal.agent_id == context[2],
                "test_text_gate_signed_context")
        payload = self.bounded_payload(raw)
        task = self.task(*context[:3], user=context[3], run_id=context[4])
        # No injected URL, headers, user credentials, redirects or HTTP retries.
        import httpx
        key = os.environ.get(self.settings.codex_api_key_env)
        require(bool(key), "test_text_gate_credential_missing")
        self.budget.reserve(task, raw)
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(retries=0),
                                     trust_env=False, follow_redirects=False, timeout=120) as client:
            response = await client.post("https://api.deepseek.com/responses",json=payload,
                                         headers={"Authorization":f"Bearer {key}"})
        return response.status_code, response.headers.get("content-type","application/json"), response.content

    async def _handle(self, reader, writer):
        status, kind, body = 400, "application/json", b'{"error":{"message":"test_text_gate_denied"}}'
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),10)
            lines = header.decode('ascii').split('\r\n')
            require(lines[0] == 'POST /responses HTTP/1.1', "test_text_gate_http_path")
            pairs = [line.split(':',1) for line in lines[1:] if line]
            headers = {k.lower():v.strip() for k,v in pairs}
            require(len(headers) == len(pairs) and 'transfer-encoding' not in headers,
                    "test_text_gate_http_framing")
            length = int(headers['content-length'])
            require(0 < length <= 32768 and headers.get('authorization','').startswith('Bearer '),
                    "test_text_gate_http_request")
            raw = await asyncio.wait_for(reader.readexactly(length),10)
            status,kind,body = await self.forward(headers['authorization'][7:],raw)
            kind = "text/event-stream" if kind.startswith("text/event-stream") else "application/json"
        except TestTextGateDenied as exc:
            body = json.dumps({"error":{"message":str(exc),"type":"test_text_gate_denied"}}).encode()
        except Exception:
            body = b'{"error":{"message":"test_text_gate_unavailable_or_attempt_spent"}}'
        try:
            writer.write(f"HTTP/1.1 {status} Gate\r\nContent-Type: {kind}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()+body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        self.contexts.clear()
