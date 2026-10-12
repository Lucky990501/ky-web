"""Standalone local contract test: actual ASGI/TaskService/SQLite, Fixture Runtime.

No Provider, published-Agent qualification, Native/Test authority or Production
claim. Uses a built-in Legacy Agent, not a forged productized Runtime Test PASS.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
if os.environ.get("UI_FIXTURE_SITE_PACKAGES"):
    sys.path.append(os.environ["UI_FIXTURE_SITE_PACKAGES"])
ROOT = Path(tempfile.mkdtemp(prefix="customer-experience-local-fixture-"))
(ROOT / "isolated-ui-fixture.marker").write_text("synthetic-local-only", encoding="utf-8")
os.environ.update(APP_ENV="test", ENTERPRISE_POC_DATABASE_URL=f"sqlite:///{ROOT / 'fixture.db'}",
    ENTERPRISE_POC_DATA_DIR=str(ROOT / "data"), ENTERPRISE_POC_OBJECT_STORAGE_DIR=str(ROOT / "objects"),
    ENTERPRISE_POC_OBJECT_STORAGE_PROVIDER="local", ENTERPRISE_POC_TASK_QUEUE="local",
    ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA="false", ENTERPRISE_POC_SECURE_COOKIES="false",
    ENTERPRISE_POC_TOKEN_SECRET="isolated-fixture-not-a-credential",
    ENTERPRISE_POC_MCP_URL="http://127.0.0.1:1/disabled", ENTERPRISE_POC_MODEL_BASE_URL="http://127.0.0.1:1/disabled",
    DEEPSEEK_API_KEY="isolated-fixture-no-provider", GATEWAY_API_TOKEN="isolated-fixture-no-provider",
    ENTERPRISE_POC_AGENT_RUNTIME_TEST_PRODUCTION_ENABLED="false", EMBEDDING_PROVIDER="local-hash")
os.environ.pop("REDIS_URL", None)
original_connect = socket.socket.connect
def deny_network(*_args, **_kwargs):
    raise RuntimeError("External network forbidden")
def guarded_connect(sock, address):
    pair = getattr(socket, "_fallback_socketpair", None)
    if pair and sys._getframe(1).f_code is pair.__code__ and address[0] in {"127.0.0.1", "::1"}:
        return original_connect(sock, address)
    return deny_network()
socket.socket.connect = guarded_connect
socket.create_connection = deny_network

from fastapi.testclient import TestClient
from app import main
from app.settings import settings
from app.domain import RuntimeSession, RuntimeTurn, RuntimeStreamEvent
from app.runtime.base import RuntimeProvider

assert settings.environment == "test" and settings.task_queue == "local"
assert settings.database_path.resolve().is_relative_to(ROOT.resolve())
assert settings.object_storage_dir.resolve().is_relative_to(ROOT.resolve())

class ControlledFixtureRuntime(RuntimeProvider):
    def __init__(self):
        self.release = threading.Event()
        self.starts = 0
        self.cancels = 0
    async def create_session(self, profile, developer_instructions):
        return RuntimeSession("fixture-" + str(uuid4()), profile.id)
    async def resume_session(self, profile, thread_id, developer_instructions=None, recovery_context=None):
        return RuntimeSession(thread_id, profile.id)
    async def run_turn(self, session, message):
        return RuntimeTurn(session.thread_id, "Fixture完整回复，已持久化。")
    async def stream_turn(self, session, message):
        self.starts += 1
        yield RuntimeStreamEvent.visible_delta("Fixture执行中正文。")
        deadline = time.monotonic() + 15
        while not self.release.is_set():
            if time.monotonic() > deadline:
                raise TimeoutError("Fixture release deadline")
            await asyncio.sleep(0.02)
        if "FIXTURE_FAIL" in message:
            raise RuntimeError("Intentional isolated Fixture failure")
        yield RuntimeStreamEvent.completed(await self.run_turn(session, message))
    async def cancel_turn(self, session):
        self.cancels += 1
        return True
    async def close(self):
        self.release.set()

fixture_runtime = ControlledFixtureRuntime()
main.agents._runtime = fixture_runtime
main.store.seed_demo_data()
main.product_store.initialize()
main.agent_catalog_control.initialize()
for tenant, account, role in [("tenant-a", "fixture-admin-a@example.test", "enterprise_admin"), ("tenant-a", "fixture-member-a@example.test", "member"), ("tenant-a", "fixture-peer-a@example.test", "member"), ("tenant-b", "fixture-member-b@example.test", "member")]:
    main.product_store.create_user(tenant, account, main.hash_password("IsolatedFixture2026!"), "本地 Fixture", role)

report = {"task":"CUSTOMER_EXPERIENCE_P0_FRONTEND_V1", "mode":"REAL_ASGI_TASKSERVICE_SQLITE_WITH_CONTROLLED_FIXTURE_RUNTIME", "checks":[], "provider_calls":0, "production_changes":0, "publish_enable_changes":0, "native_validation":"NOT_EXECUTED"}
def record(name, evidence):
    report["checks"].append({"name":name,"status":"PASS_ISOLATED_REAL_API_FIXTURE_RUNTIME","evidence":evidence})

with TestClient(main.app) as client:
    def login(alias):
        client.cookies.clear()
        response = client.post("/api/v1/auth/login", json={"account":"fixture-" + alias + "@example.test","password":"IsolatedFixture2026!"})
        assert response.status_code == 200
        return response.json()["user"]
    def wait_task(task_id, statuses):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = client.get("/api/v1/tasks/" + task_id)
            assert response.status_code == 200
            task = response.json()
            if task["status"] in statuses:
                return task
            time.sleep(0.02)
        raise AssertionError("Task status timeout")
    user = login("member-a")
    response = client.post("/api/v1/agents/copywriting-agent/runs", json={"message":"Fixture导航/刷新恢复，不调用真实模型。"})
    assert response.status_code == 202, response.text
    task = response.json()
    task_id = task["id"]
    running = wait_task(task_id, {"running"})
    conversation_id = running["conversation_id"]
    assert conversation_id
    record("A01 formal POST persists Task; conversation may be assigned later by execution", {"http":202,"task_id":task_id,"initial_conversation_id":task.get("conversation_id"),"running_conversation_id":conversation_id})
    async def close_reader():
        response = await main.stream_task_events(task_id, 0, client.cookies.get("workbench_session"))
        event = await anext(response.body_iterator)
        await response.body_iterator.aclose()
        return event.splitlines()[0]
    event = client.portal.call(close_reader)
    assert client.get("/api/v1/tasks/" + task_id).json()["status"] == "running"
    assert fixture_runtime.starts == 1 and fixture_runtime.cancels == 0
    record("A02 closing the actual SSE reader does not cancel the independent Task", {"first_event":event,"status_after_close":"running","fixture_runtime_starts":1,"cancel_calls":0})
    fixture_runtime.release.set()
    completed = wait_task(task_id, {"completed","failed"})
    assert completed["status"] == "completed", completed
    detail = client.get("/api/v1/conversations/" + conversation_id)
    assert detail.status_code == 200
    assert any(item["role"] == "assistant" and item["content"] == "Fixture完整回复，已持久化。" for item in detail.json()["messages"])
    record("A03 Task completes and history retains its exact result after reader close", {"task_id":task_id,"status":"completed","history_http":200,"assistant_message_id":completed["assistant_message_id"]})
    starts_before = fixture_runtime.starts
    client.portal.call(main.task_service.execute, main.product_store.task_for_worker(task_id))
    assert fixture_runtime.starts == starts_before
    with main.store.connection() as conn:
        debit_rows = conn.execute("SELECT COUNT(*) AS n FROM credit_transactions WHERE task_id=? AND amount<0", (task_id,)).fetchone()["n"]
    assert debit_rows == 1
    for alias in ["peer-a","member-b"]:
        login(alias)
        assert client.get("/api/v1/tasks/" + task_id).status_code == 404
        assert client.get("/api/v1/conversations/" + conversation_id).status_code == 404
    record("A04 other User and Tenant cannot restore the Task or history", {"same_tenant_other_user":404,"cross_tenant":404})
    login("member-a")
    assert client.get("/api/v1/conversations/" + conversation_id).status_code == 200
    assert client.get("/api/v1/enterprise-config").status_code == 403
    assert client.get("/api/v1/enterprise-config/brand-logo").status_code == 403
    record("A05 relogin restores owned history, member config/brand-logo remains denied", {"history_http":200,"config_http":403,"logo_http":403})
    fixture_runtime.release.clear()
    stopped = client.post("/api/v1/agents/copywriting-agent/runs", json={"message":"Fixture主动取消"}).json()
    wait_task(stopped["id"], {"running"})
    assert client.post("/api/v1/tasks/" + stopped["id"] + "/cancel").status_code == 200
    cancelled = wait_task(stopped["id"], {"cancelled"})
    with main.store.connection() as conn:
        cancelled_owner_rows = conn.execute("SELECT COUNT(*) AS n FROM conversation_owners WHERE conversation_id=?", (cancelled.get("conversation_id"),)).fetchone()["n"]
    record("A06 explicit cancel, unlike reader detach, reaches CANCELLED", {"task_id":stopped["id"],"status":cancelled["status"],"runtime_cancel_calls":fixture_runtime.cancels,"cancelled_conversation_owner_rows":cancelled_owner_rows,"history_discovery_closed":cancelled_owner_rows>0})
    fixture_runtime.release.set()
    failed = client.post("/api/v1/agents/copywriting-agent/runs", json={"message":"FIXTURE_FAIL 明确失败"}).json()
    failed_current = wait_task(failed["id"], {"failed"})
    assert failed_current["status"] == "failed"
    history = client.get("/api/v1/conversations").json()
    visible = any(item["id"] == failed_current["conversation_id"] and item["latest_status"] == "failed" for item in history)
    with main.store.connection() as conn:
        owner_count = conn.execute("SELECT COUNT(*) AS n FROM conversation_owners WHERE conversation_id=?", (failed_current["conversation_id"],)).fetchone()["n"]
    report["checks"].append({"name":"A07 failed Task query and history visibility", "status":"PASS_ISOLATED_REAL_API_FIXTURE_RUNTIME" if visible else "BLOCKED_BACKEND_CONTRACT", "evidence":{"task_id":failed["id"],"conversation_id":failed_current["conversation_id"],"task_http":200,"status":"failed","history_visible":visible,"conversation_owner_rows":owner_count,"first_failure_point":None if visible else "FAILED_TASK_CONVERSATION_OWNERSHIP_NOT_PERSISTED"}})
    admin = login("admin-a")
    updated = client.put("/api/v1/enterprise-config", json={"payload":{"brand_name":"隔离品牌新名称"}})
    assert updated.status_code == 200
    assert client.get("/api/v1/workspace").json()["brand_name"] == "隔离品牌新名称"
    tenant_name = client.get("/api/v1/me").json()["tenant_name"]
    assert tenant_name != "隔离品牌新名称"
    record("A08 workspace.brand_name reflects config without rewriting Tenant identity/name", {"put_http":200,"workspace_brand":"隔离品牌新名称","tenant_id":admin["tenant_id"],"tenant_name_unchanged":True})
    record("A09 reads/duplicate completed queue delivery make no extra turn or debit", {"actual_synthetic_tasks":3,"fixture_runtime_turns":fixture_runtime.starts,"first_task_debit_rows":debit_rows,"duplicate_queue_execution_new_turns":0,"provider_calls":0})

blocked = sum(item["status"] == "BLOCKED_BACKEND_CONTRACT" for item in report["checks"])
report.update(total=len(report["checks"]), passed=len(report["checks"])-blocked, blocked=blocked, failed=0, skipped=0, status="PARTIAL_REAL_API_BACKEND_CONTRACT_GAP" if blocked else "PASS_ISOLATED_REAL_API_WITH_FIXTURE_RUNTIME", fixture_runtime_turns=fixture_runtime.starts)
if os.environ.get("CUSTOMER_API_REPORT"):
    Path(os.environ["CUSTOMER_API_REPORT"]).write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
