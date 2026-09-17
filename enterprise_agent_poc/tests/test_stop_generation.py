from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.domain import RuntimeStreamEvent, RuntimeTurn
from test_run_trace import FakeRuntime, product_task_fixture


class BlockingRuntime(FakeRuntime):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.interrupted = False
        self.turn_count = 0

    async def stream_turn(self, session, message):
        self.turn_count += 1
        self.started.set()
        yield RuntimeStreamEvent.visible_delta("partial")
        await self.release.wait()
        # A provider can race its interrupt acknowledgement with a final event.
        # The service must not turn this into a successful product result.
        yield RuntimeStreamEvent.completed(RuntimeTurn(session.thread_id, "late final"))

    async def cancel_turn(self, session):
        self.interrupted = True
        self.release.set()
        return True


def test_cancel_queued_task_is_terminal_and_idempotent(tmp_path, monkeypatch):
    _, product, task, _, _ = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", FakeRuntime())
    first = product.cancel_task(task["id"], "tenant-a", task["user_id"])
    second = product.cancel_task(task["id"], "tenant-a", task["user_id"])
    assert first["status"] == second["status"] == "cancelled"
    assert product.task_for_worker(task["id"])["stage"] == "cancelled"


def test_cancel_respects_tenant_and_user_ownership(tmp_path, monkeypatch):
    _, product, task, _, _ = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", FakeRuntime())
    assert product.cancel_task(task["id"], "tenant-b", task["user_id"]) is None
    assert product.cancel_task(task["id"], "tenant-a", "other-user") is None
    assert product.task_for_worker(task["id"])["status"] == "queued"


def test_running_cancel_interrupts_turn_and_never_persists_partial_result(tmp_path, monkeypatch):
    runtime = BlockingRuntime()
    store, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", runtime)

    async def scenario():
        execution = asyncio.create_task(service.execute(task))
        await asyncio.wait_for(runtime.started.wait(), timeout=1)
        cancelling = product.cancel_task(task["id"], "tenant-a", task["user_id"])
        assert cancelling["status"] == "running" and cancelling["stage"] == "cancelling"
        await asyncio.wait_for(execution, timeout=2)

    asyncio.run(scenario())
    saved = product.task_for_worker(task["id"])
    trace = store.run_trace(saved["run_id"], "tenant-a")
    assert saved["status"] == saved["stage"] == "cancelled"
    assert runtime.interrupted is True
    assert trace["status"] == "cancelled"
    assert trace["payload"]["final_response_persisted"] is False
    with store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM task_results WHERE task_id=?", (task["id"],)).fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM messages WHERE id=?", (f"task:{task['id']}:assistant",)).fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM credit_transactions WHERE task_id=?", (task["id"],)).fetchone()["n"] == 0


def test_completed_and_failed_tasks_cannot_be_cancelled(tmp_path, monkeypatch):
    _, product, completed, _, _ = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", FakeRuntime())
    product.set_task(completed["id"], "tenant-a", "completed", "completed", "done")
    assert product.cancel_task(completed["id"], "tenant-a", completed["user_id"])["status"] == "completed"
    _, product, failed, _, _ = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", FakeRuntime())
    product.set_task(failed["id"], "tenant-a", "failed", "failed", "failed")
    assert product.cancel_task(failed["id"], "tenant-a", failed["user_id"])["status"] == "failed"


def test_complete_then_cancel_keeps_completed_as_the_single_authoritative_terminal_state(tmp_path, monkeypatch):
    _, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", FakeRuntime())
    asyncio.run(service.execute(task))
    assert product.cancel_task(task["id"], "tenant-a", task["user_id"])["status"] == "completed"
    events = product.task_events_since(task["id"], "tenant-a", task["user_id"])
    assert [item["stage"] for item in events].count("completed") == 1
    assert "cancelled" not in [item["stage"] for item in events]


def test_cancel_wins_when_requested_during_result_finalization(tmp_path, monkeypatch):
    store, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", FakeRuntime())
    original = product.complete_task_success

    def cancel_before_commit(current, **kwargs):
        product.cancel_task(current["id"], "tenant-a", current["user_id"])
        return original(current, **kwargs)

    monkeypatch.setattr(product, "complete_task_success", cancel_before_commit)
    asyncio.run(service.execute(task))
    saved = product.task_for_worker(task["id"])
    trace = store.run_trace(saved["run_id"], "tenant-a")
    assert saved["status"] == "cancelled" and trace["status"] == "cancelled"
    with store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM task_results WHERE task_id=?", (task["id"],)).fetchone()["n"] == 0


def test_public_task_exposes_durable_cancelling_state_without_schema_change():
    from app.main import _public_task

    assert _public_task({"status": "running", "stage": "cancelling"})["status"] == "cancelling"


def test_sse_uses_cancelled_event_not_error(tmp_path, monkeypatch):
    from app import main

    _, product, task, _, _ = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", FakeRuntime())
    product.cancel_task(task["id"], "tenant-a", task["user_id"])
    monkeypatch.setattr(main, "product_store", product)
    monkeypatch.setattr(main, "current_user", lambda _cookie: SimpleNamespace(tenant_id="tenant-a", user_id=task["user_id"], role="member"))

    async def body():
        response = await main.stream_task_events(task["id"], workbench_session="session")
        return "".join([chunk.decode() if isinstance(chunk, bytes) else chunk async for chunk in response.body_iterator])

    stream = asyncio.run(body())
    assert "event: cancelled" in stream
    assert '"status": "cancelled"' in stream
    assert "event: complete" not in stream and "event: error" not in stream


def test_new_conversation_cancel_keeps_no_formal_messages_or_result(tmp_path, monkeypatch):
    runtime = BlockingRuntime()
    store, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", runtime)

    async def scenario():
        execution = asyncio.create_task(service.execute(task))
        await runtime.started.wait()
        product.cancel_task(task["id"], "tenant-a", task["user_id"])
        await execution

    asyncio.run(scenario())
    saved = product.task_for_worker(task["id"])
    with store.connection() as conn:
        messages = conn.execute("SELECT role FROM messages WHERE conversation_id=?", (saved["conversation_id"],)).fetchall()
        results = conn.execute("SELECT COUNT(*) AS n FROM task_results WHERE task_id=?", (task["id"],)).fetchone()["n"]
    assert messages == [] and results == 0


def test_existing_conversation_cancel_keeps_prior_user_message_but_no_assistant(tmp_path, monkeypatch):
    _, product, first, _, service = product_task_fixture(tmp_path, monkeypatch, "copywriting-agent", FakeRuntime())
    asyncio.run(service.execute(first))
    runtime = BlockingRuntime()
    service._agents._runtime = runtime
    second = product.create_task("tenant-a", first["user_id"], "copywriting-agent", "second prompt", product.task_for_worker(first["id"])["conversation_id"])

    async def scenario():
        execution = asyncio.create_task(service.execute(second))
        await runtime.started.wait()
        product.cancel_task(second["id"], "tenant-a", second["user_id"])
        await execution

    asyncio.run(scenario())
    saved = product.task_for_worker(second["id"])
    with product._store.connection() as conn:
        user = conn.execute("SELECT content FROM messages WHERE id=?", (f"task:{second['id']}:user",)).fetchone()
        assistant = conn.execute("SELECT 1 FROM messages WHERE id=?", (f"task:{second['id']}:assistant",)).fetchone()
    assert saved["status"] == "cancelled"
    assert user and user["content"] == "second prompt" and assistant is None
