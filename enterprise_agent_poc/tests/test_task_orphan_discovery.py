"""Owner-only read discovery against isolated DB + real TaskService/ASGI.

Status/time mutations below are query fixtures, not Runtime quality evidence.
No new Conversation, execution, charge, Provider call or historical backfill.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from app import main
from app.auth import UserPrincipal
from app.skill_registry import SkillRegistry
from test_history import add_conversation, history_env, login
from test_task_history_ownership import counts, execution, pending_run


PATH = "/api/v1/tasks/orphans"


def create(env, *, email="owner@history.test", tenant="tenant-a", agent="copywriting-agent"):
    user = env.product.user_by_email(email)
    return env.product.create_task(tenant, user["id"], agent, "private-query-fixture-content", None)


def snapshot(env):
    """Compare every persistent business row + schema without logging values."""
    with env.store.connection() as conn:
        names = [r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()]
        rows = {name: sorted((tuple(r) for r in conn.execute(f'SELECT * FROM "{name}"').fetchall()), key=repr)
                for name in names}
        rows["schema"] = [tuple(r) for r in conn.execute("SELECT * FROM sqlite_master ORDER BY name").fetchall()]
        return rows


def test_lost_pointer_session_startup_failure_is_rediscovered_after_relogin(history_env):
    env = history_env
    runtime, service, task = execution(env, "startup_failed")
    asyncio.run(service.execute(task))
    saved = env.product.task_for_worker(task["id"])
    assert saved["status"] == "failed" and saved["run_id"] and saved["conversation_id"]
    assert env.store.conversation(saved["conversation_id"], "tenant-a") is None
    assert counts(env, saved) == {"owners": 0, "runs": 1, "debits": 0, "results": 0}
    assert env.client.get("/api/v1/conversations").json() == []
    # Navigate away and reauthenticate: no Task ID, URL or old browser state
    # is supplied to the discovery request.
    assert env.client.get("/conversations").status_code == 200
    login(env.client, "owner@history.test")
    before = snapshot(env)
    response = env.client.get(PATH)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    found = response.json()["tasks"]
    assert len(found) == 1 and found[0]["id"] == saved["id"]
    assert found[0]["status"] == "failed" and found[0]["conversation_id"] == saved["conversation_id"]
    # The existing scoped Task GET restores the actual terminal state; it
    # never retries the Provider or invents a Conversation for that UUID.
    assert env.client.get(f"/api/v1/tasks/{found[0]['id']}").json()["status"] == "failed"
    assert env.client.get(f"/api/v1/conversations/{found[0]['conversation_id']}").status_code == 404
    for _ in range(3):
        assert env.client.get(PATH).json() == response.json()
    assert snapshot(env) == before
    assert runtime.creates == 1 and runtime.turns == runtime.cancels == 0


@pytest.mark.parametrize("outcome", ["pre_run_failure", "queued_cancel"])
def test_no_run_failure_and_early_cancel_are_discoverable(history_env, monkeypatch, outcome):
    env = history_env
    runtime, service, task = execution(env, "completed")
    if outcome == "pre_run_failure":
        def reject(_):
            raise ValueError("isolated pre-run failure")
        monkeypatch.setattr(env.product, "task_definition", reject)
        asyncio.run(service.execute(task))
    else:
        assert env.client.post(f"/api/v1/tasks/{task['id']}/cancel").status_code == 200
    saved = env.product.task_for_worker(task["id"])
    assert saved["status"] == ("failed" if outcome == "pre_run_failure" else "cancelled")
    assert saved["run_id"] is None and saved["conversation_id"] is None
    before = snapshot(env)
    assert env.client.get(PATH).json()["tasks"][0]["id"] == task["id"]
    assert env.client.get(f"/api/v1/tasks/{task['id']}").json()["status"] == saved["status"]
    assert snapshot(env) == before
    assert runtime.creates == runtime.turns == runtime.cancels == 0


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled", "running"])
def test_real_owned_conversation_tasks_are_not_duplicate_orphans(history_env, outcome):
    env = history_env
    runtime, service, task = execution(env, "running" if outcome in {"cancelled", "running"} else outcome)

    async def run():
        active = asyncio.create_task(service.execute(task))
        await asyncio.wait_for(runtime.started.wait(), timeout=3)
        if outcome == "cancelled":
            env.client.post(f"/api/v1/tasks/{task['id']}/cancel")
        if outcome != "running":
            await asyncio.wait_for(active, timeout=3)
        saved = env.product.task_for_worker(task["id"])
        assert saved["status"] == outcome
        prior = snapshot(env)
        assert env.client.get(PATH).json() == {"tasks": [], "next_cursor": None}
        projects = env.client.get("/api/v1/conversations").json()
        assert len(projects) == 1 and projects[0]["latest_task"]["id"] == task["id"]
        assert projects[0]["latest_status"] == outcome
        assert snapshot(env) == prior
        if outcome == "running":
            runtime.release.set()
            await asyncio.wait_for(active, timeout=3)

    asyncio.run(run())
    assert runtime.creates == runtime.turns == 1
    assert counts(env, env.product.task_for_worker(task["id"]))["debits"] == int(outcome in {"completed", "running"})


def test_deleted_conversations_do_not_reappear_as_orphans(history_env):
    env = history_env
    record = add_conversation(env, agent_id="copywriting-agent", prompt="owned project")
    assert env.product.delete_conversation("tenant-a", env.owner["id"], record.conversation_id)
    before = snapshot(env)
    assert env.client.get(PATH).json()["tasks"] == []
    assert env.client.get("/api/v1/conversations").json() == []
    assert snapshot(env) == before


def test_late_conversation_attach_moves_task_out_of_orphan_list_once(history_env):
    env = history_env
    task = create(env)
    run, conversation = pending_run(env, task, save_conversation=False)
    assert not env.product.attach_task_run(task["id"], "tenant-a", run, conversation, user_id=env.owner["id"])
    assert env.client.get(PATH).json()["tasks"][0]["id"] == task["id"]
    env.store.save_conversation(conversation, "tenant-a", "copywriting-agent", "profile", "thread", "isolated")
    for _ in range(2):
        assert env.product.attach_task_run(task["id"], "tenant-a", run, conversation, user_id=env.owner["id"])
        assert env.client.get(PATH).json()["tasks"] == []
        assert env.client.get("/api/v1/conversations").json()[0]["latest_task"]["id"] == task["id"]
    assert counts(env, env.product.task_for_worker(task["id"])) == {"owners": 1, "runs": 1, "debits": 0, "results": 0}


@pytest.mark.parametrize("account", ["other@history.test", "admin@history.test", "foreign@history.test"])
def test_user_and_tenant_scopes_apply_to_list_detail_and_cursor(history_env, account):
    env = history_env
    owner_task = create(env)
    other = env.product.user_by_email(account)
    other_task = create(env, email=account, tenant=other["tenant_id"])
    login(env.client, account)
    for params in ({}, {"status": "queued", "agent_id": "copywriting-agent"},
                   {"created_from": "2020-01-01T00:00:00Z"}):
        response = env.client.get(PATH, params=params)
        assert response.status_code == 200
        assert [r["id"] for r in response.json()["tasks"]] == [other_task["id"]]
    assert env.client.get(f"/api/v1/tasks/{owner_task['id']}").status_code == 404
    assert env.client.get(PATH, params={"cursor": owner_task["id"]}).status_code == 422


def test_platform_admin_is_not_a_cross_owner_or_cross_tenant_list(history_env, monkeypatch, tmp_path):
    env = history_env
    registry = SkillRegistry(env.store, tmp_path / "registry", Path(__file__).resolve().parents[1] / "skill_packages")
    registry.initialize()
    registry.grant_platform_admin(env.owner["id"])
    assert registry.is_platform_admin(env.owner["id"])
    monkeypatch.setattr(main, "skill_registry", registry)
    own = create(env)
    peer = create(env, email="other@history.test")
    foreign = create(env, email="foreign@history.test", tenant="tenant-b")
    assert env.client.get("/api/v1/me").json()["is_platform_admin"] is True
    before = snapshot(env)
    assert [r["id"] for r in env.client.get(PATH).json()["tasks"]] == [own["id"]]
    for hidden in (peer, foreign):
        assert env.client.get(PATH, params={"cursor": hidden["id"]}).status_code == 422
        assert env.client.get(f"/api/v1/tasks/{hidden['id']}").status_code == 404
    assert snapshot(env) == before


@pytest.mark.parametrize("selector", ["tenant_id", "user_id", "owner_id", "role", "platform_admin"])
def test_client_owner_selectors_are_rejected_not_honored(history_env, selector):
    env = history_env
    create(env)
    before = snapshot(env)
    assert env.client.get(PATH, params={selector: "another-owner"}).status_code == 422
    assert snapshot(env) == before


@pytest.mark.parametrize("auth", ["missing", "malformed", "wrong_tenant", "disabled"])
def test_real_authentication_required_for_discovery(history_env, auth):
    env = history_env
    if auth == "missing":
        env.client.cookies.clear()
    elif auth == "malformed":
        env.client.cookies.clear()
        env.client.cookies.set("workbench_session", "invalid-session")
    elif auth == "wrong_tenant":
        env.client.cookies.clear()
        token = main.sessions.issue(UserPrincipal(env.owner["id"], "tenant-b", "member"))
        env.client.cookies.set("workbench_session", token)
    else:
        with env.store.connection() as conn:
            conn.execute("UPDATE users SET account_status='disabled' WHERE id=?", (env.owner["id"],))
    before = snapshot(env)
    assert env.client.get(PATH).status_code == 401
    assert snapshot(env) == before


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"status": "not-a-state"}, {"cursor": "malformed"},
    {"cursor": str(uuid4())}, {"agent_id": ""}, {"agent_id": "a" * 121},
    {"created_from": "not-a-time"}, {"created_from": "2026-10-12T00:00:00"},
    {"created_from": "2026-10-13T00:00:00Z", "created_before": "2026-10-12T00:00:00Z"},
    {"created_from": "2026-10-12T00:00:00Z", "created_before": "2026-10-12T00:00:00Z"},
    [("limit", "1"), ("limit", "2")], [("status", "failed"), ("status", "all")],
])
def test_invalid_or_duplicate_filters_fail_closed(history_env, params):
    env = history_env
    create(env)
    before = snapshot(env)
    assert env.client.get(PATH, params=params).status_code == 422
    assert snapshot(env) == before


def test_stable_keyset_pages_handle_equal_times_and_newer_insert(history_env):
    env = history_env
    tasks = [create(env) for _ in range(5)]
    with env.store.connection() as conn:
        for t in tasks:
            conn.execute("UPDATE tasks SET created_at=? WHERE id=?", ("2026-10-12 01:00:00", t["id"]))
    expected = sorted((t["id"] for t in tasks), reverse=True)
    first = env.client.get(PATH, params={"limit": 2}).json()
    assert [t["id"] for t in first["tasks"]] == expected[:2]
    assert first["next_cursor"] == expected[1]
    newer = create(env)
    with env.store.connection() as conn:
        conn.execute("UPDATE tasks SET created_at=? WHERE id=?", ("2026-10-12T02:00:00+00:00", newer["id"]))
    seen, cursor = [t["id"] for t in first["tasks"]], first["next_cursor"]
    while cursor:
        page = env.client.get(PATH, params={"limit": 2, "cursor": cursor}).json()
        seen.extend(t["id"] for t in page["tasks"])
        cursor = page["next_cursor"]
    assert seen == expected and len(set(seen)) == 5 and newer["id"] not in seen
    assert env.client.get(PATH).json()["tasks"][0]["id"] == newer["id"]


def test_cursor_remains_valid_if_its_task_now_has_conversation_owner(history_env):
    env = history_env
    tasks = [create(env) for _ in range(3)]
    first = env.client.get(PATH, params={"limit": 1}).json()
    anchor = next(t for t in tasks if t["id"] == first["next_cursor"])
    run, conversation = pending_run(env, anchor)
    env.product.attach_task_run(anchor["id"], "tenant-a", run, conversation, user_id=env.owner["id"])
    page = env.client.get(PATH, params={"cursor": anchor["id"]})
    assert page.status_code == 200
    assert {t["id"] for t in page.json()["tasks"]} == {t["id"] for t in tasks if t != anchor}


def test_status_agent_and_utc_interval_filters_preserve_owner_scope(history_env):
    env = history_env
    first, second, third = [create(env) for _ in range(3)]
    foreign = create(env, email="foreign@history.test", tenant="tenant-b")
    with env.store.connection() as conn:
        for task, state, timestamp in (
            (first, "failed", "2026-10-12 00:00:00"),
            (second, "cancelled", "2026-10-12T09:00:00+08:00"),
            (third, "queued", "2026-10-12T02:00:00Z"),
            (foreign, "failed", "2026-10-12T00:00:00+00:00"),
        ):
            conn.execute("UPDATE tasks SET status=?,created_at=? WHERE id=?", (state, timestamp, task["id"]))
    params = {"created_from": "2026-10-12T08:00:00+08:00", "created_before": "2026-10-12T02:00:00Z"}
    page = env.client.get(PATH, params=params).json()
    assert [t["id"] for t in page["tasks"]] == [second["id"], first["id"]]
    assert [t["created_at"] for t in page["tasks"]] == ["2026-10-12T01:00:00+00:00", "2026-10-12T00:00:00+00:00"]
    assert env.client.get(PATH, params={**params, "status": "failed"}).json()["tasks"][0]["id"] == first["id"]
    assert env.client.get(PATH, params={**params, "agent_id": "image-agent"}).json()["tasks"] == []
    assert env.client.get(PATH, params={"agent_id": "' OR 1=1 --"}).json()["tasks"] == []


def test_bounded_safe_projection_and_concurrent_reads_make_no_writes(history_env):
    env = history_env
    for _ in range(27):
        create(env)
    before = snapshot(env)
    response = env.client.get(PATH)
    assert response.status_code == 200 and len(response.json()["tasks"]) == 25
    assert response.json()["next_cursor"]
    for task in response.json()["tasks"]:
        assert set(task) == {"id", "agent_id", "status", "created_at", "conversation_id"}
    assert "private-query-fixture-content" not in response.text
    expected = env.product.orphan_tasks("tenant-a", env.owner["id"])
    with ThreadPoolExecutor(max_workers=8) as pool:
        pages = list(pool.map(lambda _: env.product.orphan_tasks("tenant-a", env.owner["id"]), range(16)))
    assert all(page == expected for page in pages)
    assert snapshot(env) == before


@pytest.mark.parametrize("invalid", ["conversation_tenant", "conversation_agent", "other_owner"])
def test_conflicting_conversation_identity_is_not_orphan_access_authority(history_env, invalid):
    env = history_env
    task = create(env)
    conversation = str(uuid4())
    env.store.save_conversation(conversation, "tenant-b" if invalid == "conversation_tenant" else "tenant-a",
                                "image-agent" if invalid == "conversation_agent" else "copywriting-agent",
                                "profile", "thread", "isolated")
    if invalid == "other_owner":
        env.product.attach_conversation(conversation, env.product.user_by_email("other@history.test")["id"])
    with env.store.connection() as conn:
        conn.execute("UPDATE tasks SET conversation_id=? WHERE id=?", (conversation, task["id"]))
    before = snapshot(env)
    assert env.client.get(PATH).json()["tasks"] == []
    assert env.client.get(f"/api/v1/conversations/{conversation}").status_code == 404
    assert snapshot(env) == before


def test_rejected_request_before_task_persistence_does_not_invent_history(history_env):
    env = history_env
    before = snapshot(env)
    assert env.client.post("/api/v1/agents/copywriting-agent/runs", json={"message": ""}).status_code == 422
    assert snapshot(env) == before
    with env.store.connection() as conn:
        conn.execute("UPDATE tenant_agent_instances SET status='disabled' WHERE tenant_id=? AND agent_id=?",
                     ("tenant-a", "copywriting-agent"))
    before = snapshot(env)
    assert env.client.post("/api/v1/agents/copywriting-agent/runs", json={"message": "disabled agent"}).status_code == 404
    assert env.client.get(PATH).json() == {"tasks": [], "next_cursor": None}
    assert snapshot(env) == before
