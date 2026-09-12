from __future__ import annotations

import asyncio

from app.domain import RuntimeSession, RuntimeTurn
from app.runtime.codex_provider import CodexRuntimeProvider
from app.service import AgentService, AgentRunError
from app.runtime.base import RuntimeStartError
from app.settings import Settings
from app.store import POCStore
from app.product_service import TaskService
from app.product_store import ProductStore, ResultPersistenceError


class FakeRuntime:
    async def create_session(self, profile, developer_instructions):
        return RuntimeSession(thread_id="thread-test-1", profile_id=profile.id)

    async def resume_session(self, profile, thread_id, **_kwargs):
        return RuntimeSession(thread_id=thread_id, profile_id=profile.id)

    async def run_turn(self, session, message):
        return RuntimeTurn(
            thread_id=session.thread_id,
            text="已生成。",
            input_tokens=12,
            output_tokens=8,
            latency_ms=34,
            mcp_calls=(
                {"server": "platform", "tool": "enterprise_config_get", "input_summary": None, "output_summary": "brand", "status": "completed", "duration_ms": 1, "error": None},
                {"server": "platform", "tool": "knowledge_search", "input_summary": "秋季", "output_summary": "course", "status": "completed", "duration_ms": 1, "error": None, "retrieval_observation": {"query_sha256": "redacted", "query_length": 2, "result_count": 1, "results": [{"chunk_id": "chunk-1", "file_id": "file-1", "score": 0.8, "accepted": True, "rejection_reason": None}]}},
                {"server": "platform", "tool": "asset_search", "input_summary": "logo", "output_summary": "logo", "status": "completed", "duration_ms": 1, "error": None},
                {"server": "platform", "tool": "image_generation", "input_summary": "poster prompt", "output_summary": "image", "status": "completed", "duration_ms": 5, "error": None},
            ),
        )


class FailingStartupRuntime:
    def startup_events(self, profile):
        return (
            {"event": "runtime_start_requested"},
            {"event": "runtime_process_created"},
            {"event": "runtime_start_failed", "stage": "app_server"},
        )

    async def create_session(self, profile, developer_instructions):
        raise RuntimeStartError("app_server")

    async def resume_session(self, profile, thread_id, **_kwargs):
        raise RuntimeStartError("app_server")

    async def run_turn(self, session, message):  # pragma: no cover - startup always fails
        raise AssertionError("must not run a turn")


class RecoveringRuntime(FakeRuntime):
    def __init__(self):
        self.recovery_context = None

    async def resume_session(self, profile, thread_id, **kwargs):
        self.recovery_context = kwargs.get("recovery_context")
        return RuntimeSession(thread_id="thread-recovered", profile_id=profile.id)


class ToolRetryRuntime(FakeRuntime):
    def __init__(self, *, exhaust: bool = False, image: bool = False):
        self.exhaust = exhaust
        self.image = image
        self.turn_count = 0

    async def run_turn(self, session, message):
        self.turn_count += 1
        calls = [
            {"server": "platform", "tool": "enterprise_config_get", "input_summary": None, "output_summary": "brand", "status": "completed", "error": None},
            {"server": "platform", "tool": "knowledge_search", "input_summary": "query", "output_summary": None, "status": "failed", "error": "HTTP 429"},
        ]
        if not self.exhaust:
            calls.append({"server": "platform", "tool": "knowledge_search", "input_summary": "query", "output_summary": "knowledge", "status": "completed", "error": None})
        calls.append({"server": "platform", "tool": "asset_search", "input_summary": None, "output_summary": "assets", "status": "completed", "error": None})
        if self.image:
            calls.append(
                {
                    "server": "platform",
                    "tool": "image_generation",
                    "input_summary": "poster",
                    "output_summary": "{'storage_key': 'generated/tenant-a/reused.png'}",
                    "status": "completed",
                    "error": None,
                }
            )
        return RuntimeTurn(thread_id=session.thread_id, text="非空最终正文", mcp_calls=tuple(calls), status="completed")


def product_task_fixture(tmp_path, monkeypatch, agent_id: str, runtime):
    monkeypatch.setenv("ENTERPRISE_POC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENTERPRISE_POC_DATABASE_URL", f"sqlite:///{tmp_path / 'product.db'}")
    store = POCStore(tmp_path / "product.db")
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()
    product.create_user("tenant-a", "member@tenant-a.test", "unused", "Tenant A Member", "member")
    user = product.user_by_email("member@tenant-a.test")
    task = product.create_task("tenant-a", user["id"], agent_id, "执行真实任务", None)
    agents = AgentService(store, runtime, Settings.from_env())
    return store, product, task, runtime, TaskService(product, agents)


def test_run_trace_records_only_tool_observations(tmp_path, monkeypatch):
    monkeypatch.setenv("ENTERPRISE_POC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENTERPRISE_POC_DATABASE_URL", f"sqlite:///{tmp_path / 'poc.db'}")
    store = POCStore(tmp_path / "poc.db")
    store.seed_demo_data()
    service = AgentService(store, FakeRuntime(), Settings.from_env())

    result = asyncio.run(service.run("tenant-a", "image-agent", "帮我做一张秋季招生海报"))
    trace = store.run_trace(result.run_id, "tenant-a")
    assert trace and trace["status"] == "completed"
    assert trace["payload"]["skill_version"] == "1.0.0"
    assert [item["tool"] for item in trace["payload"]["mcp_calls"]] == [
        "enterprise_config_get", "knowledge_search", "asset_search", "image_generation"
    ]
    assert "reasoning" not in trace["payload"]
    assert trace["payload"]["tool_calls_completed"] is True
    assert trace["payload"]["required_tool_calls_completed"] is True
    assert trace["payload"]["runtime_status"] == "completed"
    assert trace["payload"]["final_response_received"] is True
    assert trace["payload"]["knowledge_retrievals"] == [{"query_sha256": "redacted", "query_length": 2, "result_count": 1, "results": [{"chunk_id": "chunk-1", "file_id": "file-1", "score": 0.8, "accepted": True, "rejection_reason": None}]}]


def test_retrieval_observation_does_not_persist_query_or_chunk_content():
    observation = CodexRuntimeProvider._retrieval_observation(
        '{"query":"企业内部资料"}',
        '[{"chunk_id":"chunk-1","file_id":"file-1","content":"不应保存","score":0.8,"accepted":true}]',
    )

    assert observation["query_length"] == 6
    assert observation["query_sha256"]
    assert "企业内部资料" not in str(observation)
    assert "不应保存" not in str(observation)
    assert observation["results"] == [{"chunk_id": "chunk-1", "file_id": "file-1", "score": 0.8, "accepted": True, "rejection_reason": None}]


def test_startup_failure_creates_a_safe_trace_before_thread_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("ENTERPRISE_POC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENTERPRISE_POC_DATABASE_URL", f"sqlite:///{tmp_path / 'poc.db'}")
    store = POCStore(tmp_path / "poc.db")
    store.seed_demo_data()
    service = AgentService(store, FailingStartupRuntime(), Settings.from_env())

    try:
        asyncio.run(service.run("tenant-a", "image-agent", "生成海报"))
        raise AssertionError("expected startup failure")
    except AgentRunError as exc:
        trace = store.run_trace(exc.run_id, "tenant-a")

    assert trace and trace["status"] == "failed"
    assert trace["codex_thread_id"] == "pending"
    assert trace["payload"]["error"] == "Codex Runtime 未能启动；请查看安全运行时 Trace。"
    assert trace["payload"]["lifecycle_events"][-1] == {"event": "runtime_start_failed", "stage": "app_server"}


def test_missing_rollout_can_rebind_same_agent_using_visible_history(tmp_path, monkeypatch):
    monkeypatch.setenv("ENTERPRISE_POC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENTERPRISE_POC_DATABASE_URL", f"sqlite:///{tmp_path / 'poc.db'}")
    store = POCStore(tmp_path / "poc.db")
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()
    runtime = RecoveringRuntime()
    service = AgentService(store, runtime, Settings.from_env())
    profile = service.profile_for("tenant-a", "copywriting-agent")
    store.save_conversation("conversation-1", "tenant-a", "copywriting-agent", profile.id, "thread-old", profile.runtime_version)
    product.add_message("conversation-1", "user", "写一版招生文案")
    product.add_message("conversation-1", "assistant", "第一版正文")
    product.add_message("conversation-1", "user", "压缩成短文案")

    result = asyncio.run(service.run("tenant-a", "copywriting-agent", "压缩成短文案", "conversation-1"))

    assert result.thread_id == "thread-recovered"
    assert store.conversation("conversation-1", "tenant-a")["runtime_thread_id"] == "thread-recovered"
    assert runtime.recovery_context == "user: 写一版招生文案\nassistant: 第一版正文"


def test_rollout_mapping_conflict_is_recoverable_but_unrelated_errors_are_not():
    mismatch = RuntimeError(
        "failed to read thread: thread-store internal error: session metadata /safe/path "
        "belongs to thread old-thread, expected requested-thread"
    )
    assert CodexRuntimeProvider._rollout_unavailable(mismatch) is True
    assert CodexRuntimeProvider._rollout_unavailable(RuntimeError("image provider unavailable")) is False


def test_product_task_persists_final_response_and_completes_task_and_trace(tmp_path, monkeypatch):
    store, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", FakeRuntime())

    asyncio.run(service.execute(task))

    saved = product.task_for_worker(task["id"])
    trace = store.run_trace(saved["run_id"], "tenant-a")
    with store.connection() as conn:
        result = conn.execute("SELECT final_response,result_json FROM task_results WHERE task_id=?", (task["id"],)).fetchone()
        messages = conn.execute("SELECT id,role FROM messages WHERE conversation_id=? ORDER BY created_at,id", (saved["conversation_id"],)).fetchall()
    assert saved["status"] == "completed"
    assert trace["status"] == "completed"
    assert trace["payload"]["assistant_message_saved"] is True
    assert trace["payload"]["assistant_message_id"] == f"task:{task['id']}:assistant"
    assert result["final_response"] == "已生成。"
    assert [row["role"] for row in messages] == ["user", "assistant"]


def test_transient_tool_failure_then_success_satisfies_required_dependency(tmp_path, monkeypatch):
    runtime = ToolRetryRuntime()
    store, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "campaign-agent", runtime)

    asyncio.run(service.execute(task))

    saved = product.task_for_worker(task["id"])
    trace = store.run_trace(saved["run_id"], "tenant-a")["payload"]
    assert saved["status"] == "completed"
    assert trace["tool_calls_completed"] is False
    assert trace["required_tool_calls_completed"] is True
    assert trace["required_tool_calls"]["knowledge_search"] == {
        "attempts": 2,
        "completed_attempts": 1,
        "failed_attempts": 1,
        "satisfied": True,
    }


def test_exhausted_required_tool_fails_without_persisting_partial_output(tmp_path, monkeypatch):
    runtime = ToolRetryRuntime(exhaust=True)
    store, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "campaign-agent", runtime)

    asyncio.run(service.execute(task))

    saved = product.task_for_worker(task["id"])
    trace = store.run_trace(saved["run_id"], "tenant-a")["payload"]
    with store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM task_results WHERE task_id=?", (task["id"],)).fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM credit_transactions WHERE task_id=?", (task["id"],)).fetchone()["n"] == 0
    assert saved["status"] == "failed"
    assert saved["error_code"] == "required_tool_dependency_error"
    assert trace["required_tool_calls_completed"] is False
    assert trace["partial_output"] is True
    assert trace["assistant_message_saved"] is False


def test_received_response_but_database_save_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    store, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", FakeRuntime())

    def fail_save(*_args, **_kwargs):
        raise ResultPersistenceError("assistant_message")

    monkeypatch.setattr(product, "complete_task_success", fail_save)
    asyncio.run(service.execute(task))

    saved = product.task_for_worker(task["id"])
    trace = store.run_trace(saved["run_id"], "tenant-a")["payload"]
    assert saved["status"] == "failed"
    assert saved["error_code"] == "result_persistence_error"
    assert saved["stage"] == "result_persistence_failed"
    assert trace["runtime_status"] == "completed"
    assert trace["final_response_received"] is True
    assert trace["assistant_message_saved"] is False
    assert trace["result_persistence_status"] == "failed"
    assert trace["result_persistence_error_stage"] == "assistant_message"


def test_image_postprocessing_retry_reuses_runtime_result_and_is_idempotent(tmp_path, monkeypatch):
    runtime = ToolRetryRuntime(image=True)
    store, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "image-agent", runtime)
    original = product.complete_task_success

    def fail_once(*_args, **_kwargs):
        monkeypatch.setattr(product, "complete_task_success", original)
        raise ResultPersistenceError("artifact_association")

    monkeypatch.setattr(product, "complete_task_success", fail_once)
    asyncio.run(service.execute(task))
    failed = product.task_for_worker(task["id"])
    assert failed["status"] == "failed"
    assert runtime.turn_count == 1

    asyncio.run(service.execute(failed))
    completed = product.task_for_worker(task["id"])
    asyncio.run(service.execute(completed))

    with store.connection() as conn:
        counts = {
            "assistant": conn.execute("SELECT COUNT(*) AS n FROM messages WHERE conversation_id=? AND role='assistant'", (completed["conversation_id"],)).fetchone()["n"],
            "generation": conn.execute("SELECT COUNT(*) AS n FROM generations WHERE task_id=?", (task["id"],)).fetchone()["n"],
            "charge": conn.execute("SELECT COUNT(*) AS n FROM credit_transactions WHERE task_id=?", (task["id"],)).fetchone()["n"],
        }
    trace = store.run_trace(completed["run_id"], "tenant-a")["payload"]
    assert completed["status"] == "completed"
    assert runtime.turn_count == 1
    assert counts == {"assistant": 1, "generation": 1, "charge": 1}
    assert trace["assistant_message_saved"] is True
    assert trace["artifact_saved"] is True
