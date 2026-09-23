import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.activity_plan_runtime import render_markdown
from app.document_generator import ActivityPlanContent
from scripts import release_verify as verify

CONFIG = {"account": "smoke@example.invalid", "password": "test-only", "tenant_id": "tenant", "user_id": "user"}
PLAN = json.loads((Path(__file__).parent / "fixtures/activity_plan_d2.json").read_text(encoding="utf-8"))
STRUCTURED = {"type": "activity_plan", "version": "1", "data": PLAN}
PLAN_TASK = {"id": "plan-task", "status": "completed", "conversation_id": "plan-conversation",
             "final_response": render_markdown(ActivityPlanContent.model_validate(PLAN)),
             "assistant_message_id": "task:plan-task:assistant", "structured_result": STRUCTURED}


@pytest.mark.parametrize("fault", [None, "http500", "scope", "missing_delta", "error", "cancelled", "bad_delta", "bad_activity", "incomplete", "cancel", "plan_missing_structured", "plan_mismatch", "document500", "download500"])
def test_real_smoke_protocol_http_boundary_only(fault):
    calls = []
    runs = []
    task = {"id": "task", "status": "completed", "conversation_id": "conversation", "final_response": "ok"}
    def handler(request):
        path = request.url.path
        calls.append((request.method, path))
        assert request.url.host == "127.0.0.1" and request.url.port == 18090
        if fault == "http500":
            return httpx.Response(500, text="must-not-log-private-body")
        if path.endswith("/auth/login"):
            return httpx.Response(200, json={"user": {"id": "wrong" if fault == "scope" else "user", "tenant_id": "tenant"}},
                                  headers={"set-cookie": "workbench_session=test; Secure; Path=/"})
        assert request.headers["cookie"] == "workbench_session=test"
        if path.endswith("/me"):
            return httpx.Response(200, json={"user_id": "user", "tenant_id": "tenant"})
        if path.endswith("/agents/campaign-agent"):
            return httpx.Response(200, json={"id": "campaign-agent", "enabled": True})
        if path.endswith("/runs"):
            runs.append(path)
            return httpx.Response(202, json={"id": "task" if len(runs) == 1 else "plan-task"})
        if path.endswith("/events"):
            plan_event = "/plan-task/" in path
            completed = PLAN_TASK if plan_event else task
            events = [("progress", {"stage": "queued"}),
                      ("activity", {"sequence": 1, "stage": "generating", "status": "started"}),
                      ("delta", {"sequence": 1, "text": "ok"}), ("complete", completed)]
            if not plan_event:
                if fault == "missing_delta": events = [e for e in events if e[0] != "delta"]
                if fault in {"error", "cancelled"}: events[-1] = (fault, {})
                if fault == "bad_delta": events[2] = ("delta", {"sequence": 0, "text": ""})
                if fault == "bad_activity": events[1] = ("activity", {"sequence": 1, "status": "invalid"})
                if fault == "incomplete": events.pop()
            elif fault == "plan_mismatch":
                events[-1] = ("complete", {**completed, "assistant_message_id": "wrong"})
            body = "".join(f"event: {event}\ndata: {json.dumps(value)}\n\n" for event, value in events)
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        if path.endswith("/conversations/conversation"):
            return httpx.Response(200, json={"id": "conversation"})
        if path.endswith("/tasks/plan-task"):
            return httpx.Response(200, json={**PLAN_TASK, "structured_result": None} if fault == "plan_missing_structured" else PLAN_TASK)
        if path.endswith("/documents/activity-plan"):
            assert request.method == "POST" and json.loads(request.content)["content"] == PLAN
            return (httpx.Response(500, text="must-not-log-private-body") if fault == "document500" else
                    httpx.Response(201, json={"document_id": "document", "filename": "方案.docx",
                                              "download_url": "/api/v1/documents/activity-plan/document"}))
        if path.endswith("/documents/activity-plan/document"):
            return (httpx.Response(500, text="must-not-log-private-body") if fault == "download500" else
                    httpx.Response(200, content=b"PK\x03\x04docx",
                                   headers={"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}))
        if path.endswith("/cancel") and fault == "cancel":
            return httpx.Response(200, json={**task, "status": "cancelled"})
        return httpx.Response(200, json=task)
    async def run():
        return await verify.technical_smoke(CONFIG, transport=httpx.MockTransport(handler))
    if fault:
        with pytest.raises(verify.GateFailed):
            asyncio.run(run())
        if fault not in {"http500", "scope"}:
            assert ("POST", "/api/v1/tasks/task/cancel") in calls
    else:
        result = asyncio.run(run())
        assert result["status"] == "technical_smoke_passed"
        assert result["events"] == ["activity", "complete", "delta", "progress"]
        assert result["activity_plan_document_export"] == "PASS"
        assert calls[-1] == ("GET", "/api/v1/documents/activity-plan/document")


def test_smoke_timeout_cancels_only_own_task():
    cancelled = []
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(10)
            yield b""
    def handler(request):
        path = request.url.path
        if path.endswith("login"):
            return httpx.Response(200, json={"user": {"id": "user", "tenant_id": "tenant"}}, headers={"set-cookie": "workbench_session=test; Path=/"})
        if path.endswith("me"): return httpx.Response(200, json={"user_id": "user", "tenant_id": "tenant"})
        if path.endswith("campaign-agent"): return httpx.Response(200, json={"id": "campaign-agent", "enabled": True})
        if path.endswith("runs"): return httpx.Response(202, json={"id": "owned-task"})
        if path.endswith("events"): return httpx.Response(200, stream=SlowStream(), headers={"content-type": "text/event-stream"})
        cancelled.append(path)
        return httpx.Response(200, json={"status": "cancelling"})
    async def run():
        await asyncio.wait_for(verify.technical_smoke(CONFIG, transport=httpx.MockTransport(handler)), 0.05)
    with pytest.raises(TimeoutError): asyncio.run(run())
    assert cancelled == ["/api/v1/tasks/owned-task/cancel"]


@pytest.mark.parametrize("fault", [None, "permissions", "symlink", "missing", "fields"])
def test_credentials_explicit_private_file(tmp_path, fault):
    shared = tmp_path / "shared"
    shared.mkdir()
    path = shared / "release-smoke.json"
    if fault != "missing":
        path.write_text(json.dumps({} if fault == "fields" else CONFIG))
        path.chmod(0o644 if fault == "permissions" else 0o600)
    if fault == "symlink":
        target = shared / "target"
        path.rename(target)
        path.symlink_to(target)
    if fault:
        with pytest.raises((verify.GateFailed, OSError)): verify.smoke_config(tmp_path)
    else:
        assert verify.smoke_config(tmp_path) == CONFIG


def test_no_sensitive_error_body_output(monkeypatch, capsys):
    monkeypatch.setattr(verify, "smoke_config", lambda _: (_ for _ in ()).throw(RuntimeError("private-password")))
    assert verify.main(["credentials"]) == 2
    output = capsys.readouterr().out
    assert "private-password" not in output and "RuntimeError" in output
