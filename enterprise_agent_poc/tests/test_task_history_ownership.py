"""Actual isolated DB/TaskService/ASGI tests; no Provider or fake final result."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from app import main
from app.domain import RuntimeSession, RuntimeStreamEvent, RuntimeTurn
from app.product_service import TaskService
from app.runtime.base import RuntimeProvider, RuntimeStartError
from app.service import AgentService
from test_history import PASSWORD, history_env, login


class ControlledRuntime(RuntimeProvider):
    def __init__(self, outcome="completed"):
        self.outcome = outcome
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.creates = self.turns = self.cancels = 0

    async def create_session(self, profile, developer_instructions):
        self.creates += 1
        if self.outcome == "startup_failed":
            raise RuntimeStartError("isolated_session_init")
        return RuntimeSession("isolated-" + str(uuid4()), profile.id)

    async def resume_session(self, profile, thread_id, **kwargs):
        return RuntimeSession(thread_id, profile.id)

    async def run_turn(self, session, message):
        return RuntimeTurn(session.thread_id, "隔离运行的完整真实持久化结果。")

    async def stream_turn(self, session, message):
        self.turns += 1
        self.started.set()
        yield RuntimeStreamEvent.visible_delta("隔离部分正文")
        if self.outcome == "running":
            await self.release.wait()
        if self.outcome == "failed":
            raise RuntimeError("isolated intentional failure")
        yield RuntimeStreamEvent.completed(await self.run_turn(session, message))

    async def cancel_turn(self, session):
        self.cancels += 1
        self.release.set()
        return True

    async def close(self):
        self.release.set()


def execution(env, outcome):
    runtime = ControlledRuntime(outcome)
    agents = AgentService(env.store, runtime, env.settings)
    service = TaskService(env.product, agents)
    task = env.product.create_task("tenant-a", env.owner["id"], "copywriting-agent", "历史归属隔离请求", None)
    return runtime, service, task


def counts(env, task):
    with env.store.connection() as conn:
        return {
            "owners": conn.execute("SELECT COUNT(*) AS n FROM conversation_owners WHERE conversation_id=?", (task["conversation_id"],)).fetchone()["n"],
            "runs": conn.execute("SELECT COUNT(*) AS n FROM run_traces").fetchone()["n"],
            "debits": conn.execute("SELECT COUNT(*) AS n FROM credit_transactions WHERE task_id=? AND amount<0", (task["id"],)).fetchone()["n"],
            "results": conn.execute("SELECT COUNT(*) AS n FROM task_results WHERE task_id=?", (task["id"],)).fetchone()["n"],
        }


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_terminal_task_is_discoverable_and_replay_is_read_only(history_env, outcome):
    env = history_env
    runtime, service, task = execution(env, "running" if outcome == "cancelled" else outcome)

    async def run():
        if outcome != "cancelled":
            await service.execute(task)
            return
        active = asyncio.create_task(service.execute(task))
        await asyncio.wait_for(runtime.started.wait(), timeout=2)
        response = env.client.post(f"/api/v1/tasks/{task['id']}/cancel")
        assert response.status_code == 200
        await asyncio.wait_for(active, timeout=3)

    asyncio.run(run())
    saved = env.product.task_for_worker(task["id"])
    assert saved["status"] == outcome
    projects = env.client.get("/api/v1/conversations").json()
    project = next(p for p in projects if p["id"] == saved["conversation_id"])
    assert project["latest_status"] == outcome
    assert project["latest_task"]["id"] == task["id"]
    detail = env.client.get(f"/api/v1/conversations/{saved['conversation_id']}")
    assert detail.status_code == 200
    assert any(t["id"] == task["id"] and t["status"] == outcome for t in detail.json()["tasks"])
    assert env.client.get(f"/api/v1/tasks/{task['id']}").json()["status"] == outcome
    prior = counts(env, saved)
    assert prior == {"owners": 1, "runs": 1, "debits": int(outcome == "completed"), "results": int(outcome == "completed")}
    if outcome != "completed":
        assert not any(m["role"] == "assistant" for m in detail.json()["messages"])
    for _ in range(3):
        assert env.product.attach_task_run(task["id"], "tenant-a", saved["run_id"], saved["conversation_id"], user_id=env.owner["id"])
        asyncio.run(service.execute(task))
    login(env.client, "owner@history.test")
    assert env.client.get(f"/api/v1/conversations/{saved['conversation_id']}").status_code == 200
    assert counts(env, saved) == prior
    assert env.product.task_for_worker(task["id"]) == saved
    assert runtime.creates == runtime.turns == 1


def test_running_history_reader_close_and_duplicate_delivery_do_not_cancel_or_restart(history_env, monkeypatch):
    env = history_env
    runtime, service, task = execution(env, "running")
    monkeypatch.setattr(main, "task_service", service)

    async def run():
        active = asyncio.create_task(service.execute(task))
        await asyncio.wait_for(runtime.started.wait(), timeout=2)
        saved = env.product.task_for_worker(task["id"])
        before = counts(env, saved)
        assert before == {"owners": 1, "runs": 1, "debits": 0, "results": 0}
        assert any(p["id"] == saved["conversation_id"] and p["latest_status"] == "running"
                   for p in env.client.get("/api/v1/conversations").json())
        response = await main.stream_task_events(task["id"], workbench_session=env.client.cookies.get("workbench_session"))
        assert "event: progress" in await anext(response.body_iterator)
        await response.body_iterator.aclose()
        assert env.client.get("/conversations").status_code == 200
        assert env.client.get(f"/api/v1/tasks/{task['id']}").json()["status"] == "running"
        await service.execute(task)
        assert runtime.creates == runtime.turns == 1 and runtime.cancels == 0
        assert counts(env, saved) == before
        runtime.release.set()
        await asyncio.wait_for(active, timeout=3)

    asyncio.run(run())
    assert env.product.task_for_worker(task["id"])["status"] == "completed"


@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
@pytest.mark.parametrize("account", ["other@history.test", "admin@history.test", "foreign@history.test"])
def test_failed_cancelled_history_still_rejects_other_principals(history_env, outcome, account):
    env = history_env
    runtime, service, task = execution(env, "failed" if outcome == "failed" else "running")

    async def run():
        active = asyncio.create_task(service.execute(task))
        if outcome == "cancelled":
            await asyncio.wait_for(runtime.started.wait(), timeout=2)
            env.product.cancel_task(task["id"], "tenant-a", env.owner["id"])
        await asyncio.wait_for(active, timeout=3)

    asyncio.run(run())
    saved = env.product.task_for_worker(task["id"])
    login(env.client, account)
    assert env.client.get(f"/api/v1/tasks/{task['id']}").status_code == 404
    assert env.client.get(f"/api/v1/conversations/{saved['conversation_id']}").status_code == 404
    assert all(p["id"] != saved["conversation_id"] for p in env.client.get("/api/v1/conversations").json())
    assert env.client.post(f"/api/v1/tasks/{task['id']}/cancel").status_code == 404


def pending_run(env, task, *, conversation_id=None, save_conversation=True, trace_tenant="tenant-a", trace_agent="copywriting-agent"):
    conversation_id = conversation_id or str(uuid4())
    run_id = str(uuid4())
    if save_conversation:
        env.store.save_conversation(conversation_id, trace_tenant, trace_agent, "isolated-profile", "isolated-thread", "isolated")
    env.store.create_run_trace(run_id, conversation_id, trace_tenant, trace_agent, "pending", {})
    return run_id, conversation_id


def test_preinitialization_only_attaches_valid_trace_without_fabricating_history(history_env):
    env = history_env
    _, _, task = execution(env, "completed")
    run_id, conversation_id = pending_run(env, task, save_conversation=False)
    assert env.product.attach_task_run(task["id"], "tenant-a", run_id, conversation_id, user_id=env.owner["id"]) is False
    saved = env.product.task_for_worker(task["id"])
    assert saved["run_id"] == run_id and saved["conversation_id"] == conversation_id
    assert saved["status"] == "queued"
    assert counts(env, saved)["owners"] == 0
    assert env.store.conversation(conversation_id, "tenant-a") is None
    assert env.client.get(f"/api/v1/conversations/{conversation_id}").status_code == 404
    env.store.save_conversation(conversation_id, "tenant-a", "copywriting-agent", "isolated-profile", "isolated-thread", "isolated")
    assert env.product.attach_task_run(task["id"], "tenant-a", run_id, conversation_id, user_id=env.owner["id"])
    assert counts(env, saved)["owners"] == 1


def test_session_startup_failure_retains_task_query_but_no_invented_conversation(history_env):
    env = history_env
    runtime, service, task = execution(env, "startup_failed")
    asyncio.run(service.execute(task))
    saved = env.product.task_for_worker(task["id"])
    assert saved["status"] == "failed" and saved["run_id"]
    assert env.client.get(f"/api/v1/tasks/{task['id']}").status_code == 200
    assert env.store.conversation(saved["conversation_id"], "tenant-a") is None
    assert counts(env, saved) == {"owners": 0, "runs": 1, "debits": 0, "results": 0}
    asyncio.run(service.execute(task))
    assert runtime.creates == 1 and runtime.turns == 0


def test_no_run_failure_and_queued_cancel_remain_exact_user_scoped_tasks(history_env, monkeypatch):
    env = history_env
    runtime, service, task = execution(env, "completed")
    monkeypatch.setattr(env.product, "task_definition", lambda _: (_ for _ in ()).throw(ValueError("isolated pre-run failure")))
    asyncio.run(service.execute(task))
    saved = env.product.task_for_worker(task["id"])
    assert saved["status"] == "failed" and saved["run_id"] is None and saved["conversation_id"] is None
    assert env.client.get(f"/api/v1/tasks/{task['id']}").status_code == 200
    assert runtime.creates == runtime.turns == 0
    queued = env.product.create_task("tenant-a", env.owner["id"], "copywriting-agent", "queued cancel", None)
    assert env.client.post(f"/api/v1/tasks/{queued['id']}/cancel").json()["status"] == "cancelled"
    assert env.product.task_for_worker(queued["id"])["conversation_id"] is None
    login(env.client, "other@history.test")
    assert env.client.get(f"/api/v1/tasks/{task['id']}").status_code == 404
    assert env.client.get(f"/api/v1/tasks/{queued['id']}").status_code == 404
    assert env.client.get("/api/v1/admin/recent-tasks").status_code == 403


def test_concurrent_repeated_attach_has_one_owner_and_unchanged_terminal_task(history_env):
    env = history_env
    runtime, service, task = execution(env, "failed")
    asyncio.run(service.execute(task))
    saved = env.product.task_for_worker(task["id"])
    barrier = Barrier(6)

    def attach(_):
        barrier.wait(timeout=3)
        return env.product.attach_task_run(task["id"], "tenant-a", saved["run_id"], saved["conversation_id"], user_id=env.owner["id"])

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert all(pool.map(attach, range(6)))
    assert env.product.task_for_worker(task["id"]) == saved
    assert counts(env, saved) == {"owners": 1, "runs": 1, "debits": 0, "results": 0}
    assert runtime.turns == 1


@pytest.mark.parametrize("wrong", ["task", "tenant", "user", "run", "trace_tenant", "trace_agent", "conversation_tenant", "conversation_agent", "existing_owner"])
def test_attach_rejects_invalid_identity_and_rolls_back(history_env, wrong):
    env = history_env
    _, _, task = execution(env, "completed")
    before = env.product.task_for_worker(task["id"])
    run_id, conversation_id = pending_run(env, task, trace_tenant="tenant-b" if wrong == "trace_tenant" else "tenant-a",
                                           trace_agent="campaign-agent" if wrong == "trace_agent" else "copywriting-agent")
    if wrong in {"conversation_tenant", "conversation_agent"}:
        with env.store.connection() as conn:
            conn.execute("UPDATE conversations SET tenant_id=?,agent_id=? WHERE id=?",
                         ("tenant-b" if wrong == "conversation_tenant" else "tenant-a",
                          "campaign-agent" if wrong == "conversation_agent" else "copywriting-agent", conversation_id))
    if wrong == "existing_owner":
        peer = env.product.user_by_email("other@history.test")
        env.product.attach_conversation(conversation_id, peer["id"], "Peer private title")
    with pytest.raises((PermissionError, LookupError, ValueError)):
        env.product.attach_task_run("unknown-task" if wrong == "task" else task["id"],
                                    "tenant-b" if wrong == "tenant" else "tenant-a",
                                    "unknown-run" if wrong == "run" else run_id, conversation_id,
                                    user_id="unknown-user" if wrong == "user" else env.owner["id"])
    assert env.product.task_for_worker(task["id"]) == before
    assert env.product.task(task["id"], "tenant-a", env.owner["id"]) == task
    assert counts(env, {**task, "conversation_id": conversation_id})["owners"] == int(wrong == "existing_owner")


def test_same_run_cannot_bind_another_task_and_current_task_cannot_be_rebound(history_env):
    env = history_env
    _, _, task = execution(env, "completed")
    run_id, conversation_id = pending_run(env, task)
    env.product.attach_task_run(task["id"], "tenant-a", run_id, conversation_id)
    second = env.product.create_task("tenant-a", env.owner["id"], "copywriting-agent", "second", None)
    second_before = env.product.task_for_worker(second["id"])
    with pytest.raises(PermissionError):
        env.product.attach_task_run(second["id"], "tenant-a", run_id, conversation_id)
    another_run, another_conversation = pending_run(env, task)
    with pytest.raises(ValueError):
        env.product.attach_task_run(task["id"], "tenant-a", another_run, another_conversation)
    assert env.product.task_for_worker(second["id"]) == second_before
    assert env.product.task(second["id"], "tenant-a", env.owner["id"]) == second


def test_concurrent_cross_user_claim_has_exactly_one_owner(history_env):
    env = history_env
    _, _, task = execution(env, "completed")
    peer = env.product.user_by_email("other@history.test")
    other = env.product.create_task("tenant-a", peer["id"], "copywriting-agent", "peer private request", None)
    run_id, conversation_id = pending_run(env, task)
    peer_run = str(uuid4())
    env.store.create_run_trace(peer_run, conversation_id, "tenant-a", "copywriting-agent", "pending", {})
    barrier = Barrier(2)

    def attach(pair):
        current, run = pair
        barrier.wait(timeout=3)
        try:
            env.product.attach_task_run(current["id"], "tenant-a", run, conversation_id, user_id=current["user_id"])
            return current
        except PermissionError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        winners = [item for item in pool.map(attach, [(task, run_id), (other, peer_run)]) if item]
    assert len(winners) == 1
    with env.store.connection() as conn:
        owners = conn.execute("SELECT user_id FROM conversation_owners WHERE conversation_id=?", (conversation_id,)).fetchall()
    assert len(owners) == 1 and owners[0]["user_id"] == winners[0]["user_id"]


def test_attach_transaction_rolls_back_owner_when_task_update_fails(history_env):
    env = history_env
    _, _, task = execution(env, "completed")
    before = env.product.task_for_worker(task["id"])
    run_id, conversation_id = pending_run(env, task)
    with env.store.connection() as conn:
        # The persistent trigger exists only in this new isolated SQLite fixture.
        conn.execute("CREATE TRIGGER reject_fixture_attach BEFORE UPDATE OF run_id ON tasks BEGIN SELECT RAISE(ABORT, 'isolated update fault'); END")
    with pytest.raises(Exception, match="isolated update fault"):
        env.product.attach_task_run(task["id"], "tenant-a", run_id, conversation_id)
    assert counts(env, {**task, "conversation_id": conversation_id})["owners"] == 0
    assert env.product.task_for_worker(task["id"]) == before
    assert env.product.task(task["id"], "tenant-a", env.owner["id"]) == task


@pytest.mark.parametrize("trace_status", ["failed", "cancelled", "completed"])
def test_terminal_trace_without_conversation_is_not_valid_history_authority(history_env, trace_status):
    env = history_env
    _, _, task = execution(env, "completed")
    before = env.product.task_for_worker(task["id"])
    run_id, conversation_id = pending_run(env, task, save_conversation=False)
    env.store.finish_run_trace(run_id, trace_status, {}, "isolated-thread")
    with pytest.raises(LookupError):
        env.product.attach_task_run(task["id"], "tenant-a", run_id, conversation_id)
    assert env.product.task_for_worker(task["id"]) == before
    assert env.store.conversation(conversation_id, "tenant-a") is None
    assert counts(env, {**task, "conversation_id": conversation_id})["owners"] == 0


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_task_cannot_acquire_an_unassociated_run_or_owner(history_env, status):
    env = history_env
    _, _, task = execution(env, "completed")
    run_id, conversation_id = pending_run(env, task)
    # Malicious terminal-state fixtures are rejection probes, not runtime PASS.
    with env.store.connection() as conn:
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, task["id"]))
    before = env.product.task_for_worker(task["id"])
    with pytest.raises(ValueError):
        env.product.attach_task_run(task["id"], "tenant-a", run_id, conversation_id)
    assert env.product.task_for_worker(task["id"]) == before
    assert counts(env, {**task, "conversation_id": conversation_id})["owners"] == 0


def test_task_user_must_actually_belong_to_task_tenant(history_env):
    env = history_env
    _, _, task = execution(env, "completed")
    run_id, conversation_id = pending_run(env, task)
    foreign = env.product.user_by_email("foreign@history.test")
    with env.store.connection() as conn:
        conn.execute("UPDATE tasks SET user_id=? WHERE id=?", (foreign["id"], task["id"]))
    before = env.product.task_for_worker(task["id"])
    with pytest.raises(PermissionError):
        env.product.attach_task_run(task["id"], "tenant-a", run_id, conversation_id)
    assert env.product.task_for_worker(task["id"]) == before
    assert counts(env, {**task, "conversation_id": conversation_id})["owners"] == 0


def test_attach_preserves_custom_title_and_deleted_history(history_env):
    env = history_env
    _, _, task = execution(env, "completed")
    run_id, conversation_id = pending_run(env, task)
    env.product.attach_task_run(task["id"], "tenant-a", run_id, conversation_id)
    assert env.product.rename_conversation("tenant-a", env.owner["id"], conversation_id, "用户自定义标题")
    assert env.product.delete_conversation("tenant-a", env.owner["id"], conversation_id)
    env.product.attach_task_run(task["id"], "tenant-a", run_id, conversation_id)
    with env.store.connection() as conn:
        owner = conn.execute("SELECT title,deleted_at FROM conversation_owners WHERE conversation_id=?", (conversation_id,)).fetchone()
    assert owner["title"] == "用户自定义标题" and owner["deleted_at"]
    assert env.client.get(f"/api/v1/conversations/{conversation_id}").status_code == 404
