"""Integrated final P0: real services/ASGI/SQLite, Scripted Provider only.

No real Provider, Native, PostgreSQL or customer acceptance claim.
"""
import asyncio
import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from app import main
from app.document_generator import ActivityPlanDocumentService
from app.product_service import TaskService
from app.service import AgentService
from app.storage import LocalStorage
from test_activity_plan_runtime import ScriptedProvider, PRODUCTION_P0, PRODUCTION_REQUEST, CONFIRMED_BRIEF, full_request, plan
from test_history import history_env, login
from test_task_history_ownership import counts


class PausedPlanProvider(ScriptedProvider):
    def __init__(self, response):
        super().__init__([response])
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.cancels = 0

    async def _stream_provider_turn(self, *args, **kwargs):
        self.started.set()
        await self.release.wait()
        async for event in super()._stream_provider_turn(*args, **kwargs):
            yield event

    async def cancel_turn(self, session):
        self.cancels += 1
        self.release.set()
        return True


def integrated(env, monkeypatch, message, response):
    provider = PausedPlanProvider(response)
    service = TaskService(env.product, AgentService(env.store, provider, env.settings))
    monkeypatch.setattr(main, "task_service", service)
    task = env.product.create_task("tenant-a", env.owner["id"], "campaign-agent", message, None)
    return provider, service, task


@pytest.mark.parametrize("production_request", [False, True])
def test_full_plan_running_history_detach_duplicate_and_word_are_one_run(history_env, monkeypatch, production_request):
    env = history_env
    content = PRODUCTION_P0["scripted_plan"] if production_request else plan()
    # This test starts at final generation. Missing-information interview is
    # covered separately; reuse the existing explicitly confirmed test brief.
    message = PRODUCTION_REQUEST + CONFIRMED_BRIEF if production_request else full_request()
    provider, service, task = integrated(env, monkeypatch, message, json.dumps(content, ensure_ascii=False))
    monkeypatch.setattr(main, "document_service", ActivityPlanDocumentService(LocalStorage(env.settings.object_storage_dir)))

    async def run():
        active = asyncio.create_task(service.execute(task))
        await asyncio.wait_for(provider.started.wait(), 3)
        saved = env.product.task_for_worker(task["id"])
        assert saved["status"] == "running"
        assert counts(env, saved) == {"owners": 1, "runs": 1, "debits": 0, "results": 0}
        response = env.client.get(f"/api/v1/conversations/{saved['conversation_id']}")
        assert response.status_code == 200 and response.json()["tasks"][0]["id"] == task["id"]
        reader = await main.stream_task_events(task["id"], workbench_session=env.client.cookies.get("workbench_session"))
        assert "event: progress" in await anext(reader.body_iterator)
        await reader.body_iterator.aclose()
        await service.execute(task)  # Duplicate running delivery is read-only.
        assert provider.options == [] and provider.cancels == 0
        provider.release.set()
        await asyncio.wait_for(active, 5)

    asyncio.run(run())
    # Result envelopes are assembled by the formal Task reader's task_results
    # JOIN; the queue's raw tasks row intentionally has no structured payload.
    saved = env.product.task(task["id"], "tenant-a", env.owner["id"])
    assert saved["status"] == "completed", saved.get("user_message")
    assert saved["structured_result"] == {"type": "activity_plan", "version": "1", "data": content}
    prior = counts(env, saved)
    assert prior == {"owners": 1, "runs": 1, "debits": 1, "results": 1}
    assert len(provider.options) == 1 and provider.options[0][1] is True
    history = env.client.get(f"/api/v1/conversations/{saved['conversation_id']}")
    assert history.status_code == 200
    assistant = next(m for m in history.json()["messages"] if m["id"] == saved["assistant_message_id"])
    assert assistant["task_id"] == task["id"]
    restored = env.client.get(f"/api/v1/tasks/{assistant['task_id']}").json()
    created = env.client.post("/api/v1/documents/activity-plan", json={"content": restored["structured_result"]["data"]})
    assert created.status_code == 201
    docx = env.client.get(created.json()["download_url"])
    assert docx.status_code == 200
    with ZipFile(BytesIO(docx.content)) as archive:
        assert archive.testzip() is None
        assert content["document_title"].encode() in archive.read("word/document.xml")
    for _ in range(2):
        assert env.client.get(f"/api/v1/conversations/{saved['conversation_id']}").status_code == 200
        asyncio.run(service.execute(task))
    assert counts(env, saved) == prior and len(provider.options) == 1
    login(env.client, "foreign@history.test")
    assert env.client.get(f"/api/v1/conversations/{saved['conversation_id']}").status_code == 404
    assert env.client.get(created.json()["download_url"]).status_code == 404


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_campaign_failure_cancel_ownership_survives_and_never_replays(history_env, monkeypatch, terminal):
    env = history_env
    response = RuntimeError("isolated scripted Provider failure") if terminal == "failed" else json.dumps(plan(), ensure_ascii=False)
    provider, service, task = integrated(env, monkeypatch, full_request(), response)

    async def run():
        active = asyncio.create_task(service.execute(task))
        await asyncio.wait_for(provider.started.wait(), 3)
        if terminal == "cancelled":
            assert env.client.post(f"/api/v1/tasks/{task['id']}/cancel").status_code == 200
        provider.release.set()
        await asyncio.wait_for(active, 5)

    asyncio.run(run())
    saved = env.product.task(task["id"], "tenant-a", env.owner["id"])
    assert saved["status"] == terminal
    assert saved["structured_result"] is None
    assert counts(env, saved) == {"owners": 1, "runs": 1, "debits": 0, "results": 0}
    assert any(c["id"] == saved["conversation_id"] and c["latest_status"] == terminal
               for c in env.client.get("/api/v1/conversations").json())
    history = env.client.get(f"/api/v1/conversations/{saved['conversation_id']}")
    assert history.status_code == 200 and not any(m["role"] == "assistant" for m in history.json()["messages"])
    before = list(provider.options)
    asyncio.run(service.execute(task))
    assert provider.options == before
    assert counts(env, saved) == {"owners": 1, "runs": 1, "debits": 0, "results": 0}
