from __future__ import annotations

import asyncio

from app.domain import RuntimeSession, RuntimeTurn
from app.runtime.codex_provider import CodexRuntimeProvider
from app.service import AgentService, AgentRunError
from app.runtime.base import RuntimeStartError
from app.settings import Settings
from app.store import POCStore
from app.product_store import ProductStore


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
