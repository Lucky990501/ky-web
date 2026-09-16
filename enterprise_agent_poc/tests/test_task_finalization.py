from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.domain import RuntimeStreamEvent, RuntimeTurn, RuntimeSession
from app.platform_mcp.service import PlatformMCPService
from app.product_store import ResultPersistenceError
from app.runtime.codex_provider import CodexRuntimeProvider
from test_run_trace import FakeRuntime, ToolRetryRuntime, product_task_fixture


class CallsRuntime(FakeRuntime):
    def __init__(self, calls=(), status="completed", error=None, text="有效最终正文"):
        self.calls, self.status, self.error, self.text = calls, status, error, text
        self.turn_count = 0

    async def run_turn(self, session, message):
        self.turn_count += 1
        return RuntimeTurn(thread_id=session.thread_id, text=self.text, mcp_calls=self.calls,
                           status=self.status, error=self.error)


def call(status, dependency_id="same-query", tool="knowledge_search"):
    return {"server":"platform", "tool":tool, "status":status,
            "dependency_id":dependency_id, "input_summary":None,
            "output_summary":None, "error":None}


def run_task(tmp_path, monkeypatch, runtime, agent_id="campaign-agent"):
    store, product, task, runtime, service = product_task_fixture(tmp_path, monkeypatch, agent_id, runtime)
    asyncio.run(service.execute(task))
    saved = product.task_for_worker(task["id"])
    trace = store.run_trace(saved["run_id"], "tenant-a")
    return store, product, saved, trace, service


def test_dynamic_dependencies_allow_used_tools_without_fixed_sequence(tmp_path, monkeypatch):
    _, _, saved, trace, _ = run_task(tmp_path, monkeypatch, CallsRuntime((call("completed"),)))
    assert saved["status"] == "completed"
    assert set(trace["payload"]["required_tool_calls"]) == {"knowledge_search"}
    assert trace["payload"]["required_tool_calls_completed"] is True
    assert trace["payload"]["runtime_completed"] is True
    assert trace["payload"]["final_response_persisted"] is True
    assert trace["payload"]["artifact_completed"] is None


@pytest.mark.parametrize("calls,expected", [
    ((call("failed"), call("completed")), "completed"),
    ((call("completed"), call("failed")), "failed"),
    ((call("failed","query-a"), call("completed","query-b")), "failed"),
])
def test_dependency_recovery_is_scoped_to_same_request(tmp_path, monkeypatch, calls, expected):
    _, _, saved, trace, _ = run_task(tmp_path, monkeypatch, CallsRuntime(calls))
    assert saved["status"] == expected
    assert len(trace["payload"]["mcp_calls"]) == 2
    assert trace["payload"]["required_tool_calls_completed"] is (expected == "completed")


@pytest.mark.parametrize("status,error,text", [
    ("interrupted",None,"部分正文"), ("failed","terminal failure","部分正文"),
    ("unknown",None,"部分正文"), ("completed",None,"  "),
])
def test_partial_text_or_missing_terminal_proof_cannot_complete(tmp_path, monkeypatch, status, error, text):
    store, _, saved, trace, _ = run_task(tmp_path, monkeypatch, CallsRuntime((call("completed"),),status,error,text))
    assert saved["status"] == "failed"
    assert trace["payload"]["final_response_persisted"] is False
    with store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM task_results WHERE task_id=?",(saved["id"],)).fetchone()["n"] == 0


def test_late_failed_events_cannot_overwrite_completed_records(tmp_path, monkeypatch):
    store, product, saved, trace, service = run_task(tmp_path, monkeypatch, FakeRuntime())
    product.set_task(saved["id"], "tenant-a", "failed", "late_timeout", "late")
    service._agents.mark_persistence_failed(saved["run_id"], "tenant-a", "late_failure")
    store.finish_run_trace(saved["run_id"], "failed", {"status":"failed"})
    assert product.task_for_worker(saved["id"])["status"] == "completed"
    assert store.run_trace(saved["run_id"], "tenant-a") == trace


def test_failed_task_cannot_flip_to_completed_without_evidence(tmp_path, monkeypatch):
    store, product, saved, trace, service = run_task(tmp_path, monkeypatch, ToolRetryRuntime(exhaust=True))
    product.set_task(saved["id"], "tenant-a", "completed", "completed", "unproven", response="fake")
    assert product.task_for_worker(saved["id"])["status"] == "failed"
    store.finish_run_trace(saved["run_id"],"completed",{"status":"completed"})
    assert store.run_trace(saved["run_id"],"tenant-a")["status"] == "failed"
    with pytest.raises(ResultPersistenceError, match="completion_evidence"):
        product.complete_task_success(saved,run_id=saved["run_id"],conversation_id=trace["conversation_id"],
                                      response="fake",trace_payload={"final_result":"fake"})
    asyncio.run(service.execute(saved))
    assert service._agents._runtime.turn_count == 1
    with store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM task_results WHERE task_id=?",(saved["id"],)).fetchone()["n"] == 0


def test_failed_trace_rejects_late_runtime_completed_update(tmp_path, monkeypatch):
    store, _, saved, trace, _ = run_task(
        tmp_path, monkeypatch, ToolRetryRuntime(exhaust=True)
    )
    assert trace["status"] == "failed"

    store.finish_run_trace(
        saved["run_id"], "runtime_completed",
        {"status": "runtime_completed", "runtime_completed": True}, "late-thread"
    )

    assert store.run_trace(saved["run_id"], "tenant-a") == trace


def test_guarded_persistence_recovery_completes_without_generic_trace_flip(tmp_path, monkeypatch):
    runtime = CallsRuntime((call("completed"),))
    store, product, task, _, service = product_task_fixture(
        tmp_path, monkeypatch, "campaign-agent", runtime
    )
    original = product.complete_task_success

    def fail_once(*args, **kwargs):
        monkeypatch.setattr(product, "complete_task_success", original)
        raise ResultPersistenceError("assistant_message")

    monkeypatch.setattr(product, "complete_task_success", fail_once)
    asyncio.run(service.execute(task))
    failed = product.task_for_worker(task["id"])
    trace = store.run_trace(failed["run_id"], "tenant-a")
    assert failed["status"] == "failed" and trace["status"] == "failed"
    assert service._persistence_recoverable(trace) is True

    store.finish_run_trace(
        failed["run_id"], "runtime_completed",
        {**trace["payload"], "status": "runtime_completed"}
    )
    assert store.run_trace(failed["run_id"], "tenant-a") == trace

    completion_calls = []

    def controlled_complete(task, **kwargs):
        current = product.task_for_worker(task["id"])
        assert current["status"] == "running" and current["stage"] == "persisting_result"
        assert store.run_trace(kwargs["run_id"], "tenant-a")["status"] == "failed"
        completion_calls.append(kwargs["run_id"])
        return original(task, **kwargs)

    monkeypatch.setattr(product, "complete_task_success", controlled_complete)
    asyncio.run(service.execute(failed))

    completed = product.task_for_worker(task["id"])
    completed_trace = store.run_trace(failed["run_id"], "tenant-a")
    assert completed["status"] == "completed" and completed_trace["status"] == "completed"
    assert completed_trace["payload"]["final_response_persisted"] is True
    assert completion_calls == [failed["run_id"]]
    assert runtime.turn_count == 1


def test_duplicate_completion_does_not_repeat_message_charge_or_event(tmp_path, monkeypatch):
    store, product, saved, trace, service = run_task(tmp_path, monkeypatch, FakeRuntime())
    assert service._persist_result(saved,trace)["replayed"] is True
    asyncio.run(service.execute(saved))
    with store.connection() as conn:
        for sql in ["SELECT COUNT(*) AS n FROM messages WHERE id=?", "SELECT COUNT(*) AS n FROM credit_transactions WHERE id=?"]:
            suffix="assistant" if "messages" in sql else "charge"
            assert conn.execute(sql,(f"task:{saved['id']}:{suffix}",)).fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM task_events WHERE task_id=? AND stage='completed'",(saved["id"],)).fetchone()["n"] == 1


def test_missing_image_object_blocks_and_persistence_retry_never_regenerates(tmp_path, monkeypatch):
    runtime=ToolRetryRuntime(image=True)
    store, product, saved, trace, service = run_task(tmp_path, monkeypatch, runtime,"image-agent")
    assert saved["status"] == "failed"
    assert trace["payload"]["result_persistence_error_stage"] == "artifact_verification"
    assert trace["payload"]["artifact_completed"] is False
    from app.storage import storage_provider
    storage_provider(service._agents._settings).put(runtime.image_storage_key,b"existing-image","image/png")
    asyncio.run(service.execute(saved))
    completed=product.task_for_worker(saved["id"])
    assert completed["status"] == "completed"
    assert runtime.turn_count == 1
    assert store.run_trace(saved["run_id"],"tenant-a")["payload"]["artifact_completed"] is True


def test_transaction_rollback_removes_partial_message_generation_and_charge(tmp_path, monkeypatch):
    store, product, task, _, service=product_task_fixture(tmp_path, monkeypatch,"copywriting-agent",FakeRuntime())
    with store.connection() as conn:
        conn.execute("CREATE TRIGGER reject_trace_completion BEFORE UPDATE ON run_traces WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    asyncio.run(service.execute(task))
    saved=product.task_for_worker(task["id"])
    assert saved["status"] == "failed"
    with store.connection() as conn:
        for table in ["task_results","credit_transactions","generations"]:
            assert conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE task_id=?",(task["id"],)).fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM messages WHERE id=?",(f"task:{task['id']}:assistant",)).fetchone()["n"] == 0
    trace=store.run_trace(saved["run_id"],"tenant-a")["payload"]
    assert trace["final_response_received"] is True and trace["final_response_persisted"] is False


def test_quota_429_is_not_retried():
    response=httpx.Response(429,json={"error":{"code":"insufficient_quota"}},request=httpx.Request("POST","https://provider.invalid"))
    retryable, _, _=PlatformMCPService._retry_policy(httpx.HTTPStatusError("quota",request=response.request,response=response))
    assert retryable is False


def test_runtime_missing_status_is_unknown_not_assumed_completed():
    async def fake_run(*args,**kwargs):
        return SimpleNamespace(final_response="正文",items=[],usage=None)
    provider=CodexRuntimeProvider(None)
    profile=SimpleNamespace(sandbox="read_only")
    provider._profiles["profile"]=profile
    provider._threads["thread"]=SimpleNamespace(run=fake_run)
    turn=asyncio.run(provider.run_turn(RuntimeSession("thread","profile"),"message"))
    assert turn.status == "unknown"


def test_runtime_keeps_full_request_identity_and_tool_error_evidence():
    async def fake_run(*args, **kwargs):
        def item(arguments, status="completed", result=None):
            return SimpleNamespace(server="platform", tool="knowledge_search",
                                   arguments=arguments, status=status, result=result, error=None)
        return SimpleNamespace(final_response="正文", status="TurnStatus.completed", usage=None,
                               items=[item('{"query": "same"}', "McpToolCallStatus.failed"),
                                      item({"query":"same"}),
                                      item({"query":"different"},result={"isError":True})])
    provider=CodexRuntimeProvider(None)
    provider._profiles["profile"]=SimpleNamespace(sandbox="read_only")
    provider._threads["thread"]=SimpleNamespace(run=fake_run)
    turn=asyncio.run(provider.run_turn(RuntimeSession("thread","profile"),"message"))
    assert turn.status == "completed" and turn.error is None
    assert turn.mcp_calls[0]["dependency_id"] == turn.mcp_calls[1]["dependency_id"]
    assert turn.mcp_calls[2]["dependency_id"] != turn.mcp_calls[1]["dependency_id"]
    assert turn.mcp_calls[2]["result_is_error"] is True


def test_completed_transport_with_tool_error_cannot_satisfy_dependency(tmp_path, monkeypatch):
    error_call={**call("completed"),"result_is_error":True}
    _, _, saved, trace, _=run_task(tmp_path,monkeypatch,CallsRuntime((error_call,)))
    assert saved["status"] == "failed"
    assert trace["payload"]["required_tool_calls_completed"] is False


def test_unrelated_success_cannot_mask_failed_query_with_other_tools_present(tmp_path,monkeypatch):
    calls=(call("completed",tool="enterprise_config_get"),call("completed",tool="asset_search"),
           call("failed","needed-query"),call("completed","unrelated-query"))
    _,_,saved,trace,_=run_task(tmp_path,monkeypatch,CallsRuntime(calls))
    assert saved["status"] == "failed"
    assert trace["payload"]["required_tool_calls"]["knowledge_search"]["satisfied"] is False


class StreamingRuntime(FakeRuntime):
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.turn_count = 0

    async def stream_turn(self, session, message):
        self.turn_count += 1
        yield RuntimeStreamEvent.visible_delta("可见")
        yield RuntimeStreamEvent.visible_delta("正文")
        if self.fail:
            raise RuntimeError("provider failed after visible text")
        yield RuntimeStreamEvent.completed(RuntimeTurn(session.thread_id, "可见正文"))


def test_streaming_deltas_are_ordered_and_final_result_stays_atomic(tmp_path, monkeypatch):
    runtime = StreamingRuntime()
    store, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", runtime)
    asyncio.run(service.execute(task))

    deltas = [
        json.loads(item["message"])
        for item in product.task_events_since(task["id"], "tenant-a", task["user_id"])
        if item["stage"] == "delta"
    ]
    saved = product.task_for_worker(task["id"])
    with store.connection() as conn:
        result = conn.execute("SELECT final_response FROM task_results WHERE task_id=?", (task["id"],)).fetchone()
        messages = conn.execute("SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id", (saved["conversation_id"],)).fetchall()
    assert deltas == [{"sequence": 1, "text": "可见"}, {"sequence": 2, "text": "正文"}]
    assert saved["status"] == "completed" and result["final_response"] == "可见正文"
    assert {(row["role"], row["content"]) for row in messages} == {("user", task["input_text"]), ("assistant", "可见正文")}


def test_streaming_failure_never_persists_a_partial_assistant_message(tmp_path, monkeypatch):
    runtime = StreamingRuntime(fail=True)
    store, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", runtime)
    asyncio.run(service.execute(task))

    saved = product.task_for_worker(task["id"])
    with store.connection() as conn:
        results = conn.execute("SELECT COUNT(*) AS n FROM task_results WHERE task_id=?", (task["id"],)).fetchone()["n"]
        assistants = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE id=?", (f"task:{task['id']}:assistant",)).fetchone()["n"]
    assert saved["status"] == "failed"
    assert results == 0 and assistants == 0


def test_codex_provider_streams_only_agent_message_delta_notifications():
    from openai_codex.generated.v2_all import AgentMessageDeltaNotification, ItemCompletedNotification, MessagePhase, Turn, TurnCompletedNotification, TurnStatus

    async def notifications():
        yield SimpleNamespace(method="item/reasoning/textDelta", payload=SimpleNamespace(delta="hidden reasoning"))
        yield SimpleNamespace(method="item/commandExecution/outputDelta", payload=SimpleNamespace(delta="hidden tool output"))
        yield SimpleNamespace(method="item/agentMessage/delta", payload=AgentMessageDeltaNotification(delta="可见", itemId="item-1", threadId="thread", turnId="turn"))
        yield SimpleNamespace(method="item/agentMessage/delta", payload=AgentMessageDeltaNotification(delta="正文", itemId="item-1", threadId="thread", turnId="turn"))
        item = SimpleNamespace(text="可见正文", phase=MessagePhase.final_answer)
        yield SimpleNamespace(method="item/completed", payload=ItemCompletedNotification.model_construct(item=item, thread_id="thread", turn_id="turn"))
        completed = Turn.model_construct(id="turn", status=TurnStatus.completed, items=[item], error=None, duration_ms=9)
        yield SimpleNamespace(method="turn/completed", payload=TurnCompletedNotification.model_construct(thread_id="thread", turn=completed))

    class Handle:
        id = "turn"

        def stream(self):
            return notifications()

    async def turn(*_args, **_kwargs):
        return Handle()

    provider = CodexRuntimeProvider(None)
    provider._profiles["profile"] = SimpleNamespace(sandbox="read_only")
    provider._threads["thread"] = SimpleNamespace(turn=turn)

    async def collect():
        return [item async for item in provider.stream_turn(RuntimeSession("thread", "profile"), "message")]

    events = asyncio.run(collect())
    assert [(event.kind, event.text) for event in events[:-1]] == [("delta", "可见"), ("delta", "正文")]
    assert events[-1].kind == "completed" and events[-1].turn.text == "可见正文"


async def _sse_body(response):
    chunks = [chunk.decode() if isinstance(chunk, bytes) else chunk async for chunk in response.body_iterator]
    return "".join(chunks)


def test_sse_emits_progress_deltas_then_authoritative_complete(tmp_path, monkeypatch):
    from app import main

    _, product, task, _, _ = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", FakeRuntime())
    product.set_task(task["id"], "tenant-a", "running", "starting_runtime", "正在启动")
    product.add_task_delta(task["id"], "tenant-a", 1, "可见")
    product.add_task_delta(task["id"], "tenant-a", 2, "正文")
    product.set_task(task["id"], "tenant-a", "completed", "completed", "完成", response="可见正文")
    monkeypatch.setattr(main, "product_store", product)
    monkeypatch.setattr(main, "current_user", lambda _cookie: SimpleNamespace(tenant_id="tenant-a", user_id=task["user_id"], role="member"))

    response = asyncio.run(main.stream_task_events(task["id"], workbench_session="session"))
    body = asyncio.run(_sse_body(response))
    assert body.index("event: progress") < body.index('event: delta\ndata: {"sequence": 1') < body.index('event: delta\ndata: {"sequence": 2') < body.index("event: complete")
    assert '"final_response": "可见正文"' in body


def test_sse_failure_after_deltas_emits_error_without_complete(tmp_path, monkeypatch):
    from app import main

    _, product, task, _, _ = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", FakeRuntime())
    product.set_task(task["id"], "tenant-a", "running", "starting_runtime", "正在启动")
    product.add_task_delta(task["id"], "tenant-a", 1, "可见")
    product.set_task(task["id"], "tenant-a", "failed", "failed", "失败", error_code="runtime_error")
    monkeypatch.setattr(main, "product_store", product)
    monkeypatch.setattr(main, "current_user", lambda _cookie: SimpleNamespace(tenant_id="tenant-a", user_id=task["user_id"], role="member"))

    response = asyncio.run(main.stream_task_events(task["id"], workbench_session="session"))
    body = asyncio.run(_sse_body(response))
    assert "event: delta" in body and "event: error" in body and "event: complete" not in body


def test_closing_sse_generator_does_not_cancel_the_task(tmp_path, monkeypatch):
    from app import main

    runtime = StreamingRuntime()
    _, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", runtime)
    monkeypatch.setattr(main, "product_store", product)
    monkeypatch.setattr(main, "current_user", lambda _cookie: SimpleNamespace(tenant_id="tenant-a", user_id=task["user_id"], role="member"))

    async def disconnect():
        response = await main.stream_task_events(task["id"], workbench_session="session")
        iterator = response.body_iterator
        await anext(iterator)
        await iterator.aclose()

    asyncio.run(disconnect())
    asyncio.run(service.execute(task))
    assert runtime.turn_count == 1 and product.task_for_worker(task["id"])["status"] == "completed"


@pytest.mark.parametrize("status",[401,403,400])
def test_nonretryable_provider_errors_stop_after_one_attempt(status):
    response=httpx.Response(status,request=httpx.Request("POST","https://provider.invalid"))
    retryable,_,_=PlatformMCPService._retry_policy(httpx.HTTPStatusError("error",request=response.request,response=response))
    assert retryable is False


def test_conflicting_existing_assistant_message_is_not_silently_completed(tmp_path,monkeypatch):
    store, product, task, _, service=product_task_fixture(tmp_path,monkeypatch,"copywriting-agent",FakeRuntime())
    original=product.complete_task_success
    def inject_conflict(task,**kwargs):
        product.add_message(kwargs["conversation_id"],"assistant","conflicting content",message_id=f"task:{task['id']}:assistant")
        return original(task,**kwargs)
    monkeypatch.setattr(product,"complete_task_success",inject_conflict)
    asyncio.run(service.execute(task))
    saved=product.task_for_worker(task["id"])
    assert saved["status"] == "failed"
    trace=store.run_trace(saved["run_id"],"tenant-a")["payload"]
    assert trace["result_persistence_error_stage"] == "assistant_message"
    assert trace["final_response_persisted"] is False
