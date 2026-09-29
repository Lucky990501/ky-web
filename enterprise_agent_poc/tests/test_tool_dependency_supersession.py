"""DS1--DS14: real MCP/SDK adapter + isolated product finalization, no network."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from openai_codex.generated.v2_all import McpToolCallResult

from app.domain import RuntimeSession
from app.platform_mcp.service import PlatformMCPService
from app.runtime.codex_provider import CodexRuntimeProvider
from app.security import RuntimePrincipal, RuntimeTokenIssuer
from app.service import AgentService
from app.tool_dependencies import (
    ToolInputValidationError, attempt_observation, input_failure, now,
    resolve_dependencies,
)
from test_run_trace import FakeRuntime, product_task_fixture


def observed(arguments, *, tool="image_generation", result=None, scope="turn-1",
             call_id="call-1", status="completed"):
    return {
        "server": "platform", "tool": tool, "status": status, "error": None,
        "input_summary": None, "output_summary": None,
        **attempt_observation(arguments, result, scope=scope, tool_call_id=call_id,
                              created_at=now(), completed_at=now()),
    }


def correction_pair(*, tool="image_generation"):
    args = {"requirement": "image-A", "format": "invalid"}
    receipt = input_failure(args, ToolInputValidationError("invalid format", repairable_fields=("format",)))
    first = observed(args, tool=tool, result={"structuredContent": receipt}, status="failed")
    second = observed({**args, "format": "valid", "retry_of": receipt["retry_of"]},
                      tool=tool, call_id="call-2")
    return first, second


class ReplayRuntime(FakeRuntime):
    def __init__(self, mode):
        self.mode = mode
        self.turn_count = 0
        self.gateway_calls = 0
        self.downloads = 0

    async def run_turn(self, session, message):
        self.turn_count += 1
        from app.platform_mcp import server
        items = []

        async def invoke(args):
            raw = await self.mcp.call_tool("image_generation", args)
            if isinstance(raw, (tuple, list)) and len(raw) == 2 and isinstance(raw[1], dict):
                content, structured = raw
                result = McpToolCallResult(content=[x.model_dump() for x in content], structured_content=structured)
                receipt = None
            else:
                # Reproduce SDK loss of MCP isError; the structured receipt
                # must still classify the first attempt as failed.
                assert hasattr(raw, "model_dump"), (type(raw), [type(x) for x in raw])
                result = McpToolCallResult.model_validate(raw.model_dump(by_alias=True))
                receipt = raw.structuredContent
            items.append(SimpleNamespace(id=f"call-{len(items)+1}", server="platform",
                                         tool="image_generation", arguments=args,
                                         result=result, status="completed", error=None))
            return receipt

        base = {"prompt": "隔离回放：国庆宣传海报", "references": [], "aspect_ratio": "3:4"}
        if self.mode == "single":
            await invoke({**base, "aspect_ratio": "4:5"})
        elif self.mode == "independent-fail":
            await invoke({**base, "prompt": "image-A", "aspect_ratio": "4:5"})
            await invoke({**base, "prompt": "image-B"})
        elif self.mode == "independent-success":
            await invoke({**base, "prompt": "image-A", "aspect_ratio": "4:5"})
            await invoke({**base, "prompt": "image-B", "aspect_ratio": "1:1"})
        else:
            failure = await invoke(base)
            assert self.gateway_calls == 0 and self.downloads == 0
            if self.mode != "no-retry":
                ratio = "invalid-again" if self.mode == "retry-fail" else "4:5"
                await invoke({**base, "aspect_ratio": ratio, "retry_of": failure["retry_of"]})
        result = SimpleNamespace(items=items, usage=None, final_response="完整海报回复",
                                 status="completed", error=None, duration_ms=1, turn_id="isolated-turn")
        return CodexRuntimeProvider(None)._runtime_turn_from_result(session, SimpleNamespace(), result)


def replay(tmp_path, monkeypatch, mode="correction"):
    runtime = ReplayRuntime(mode)
    store, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, "image-agent", runtime)
    from app.platform_mcp import server
    issuer = RuntimeTokenIssuer("isolated-retry-supersession-secret-long-enough")
    token = issuer.issue(RuntimePrincipal("tenant-a", "image-agent", "profile-a",
                                         ("image:generate",), int(time.time()) + 60))
    monkeypatch.setattr(server, "service", PlatformMCPService(store, issuer, service._agents._settings))
    monkeypatch.setattr(server, "_bearer_from_context", lambda _: token)
    runtime.mcp = server.create_mcp()

    class GatewayStub:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, *, headers, json):
            assert url == "https://image-api.luckio.cn/api/v1/images/generate"
            assert json["size"] in {"1024x1024", "1024x1536"}
            runtime.gateway_calls += 1
            return httpx.Response(200, json={"code": 0, "request_id": "stub-request",
                "data": {"image_url": "https://stub.invalid/image.png", "file_name": "stub.png"}})

        async def get(self, url):
            assert url == "https://stub.invalid/image.png"
            runtime.downloads += 1
            return httpx.Response(200, content=b"isolated-stored-image",
                                  headers={"content-type": "image/png"},
                                  request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "AsyncClient", GatewayStub)
    asyncio.run(service.execute(task))
    saved = product.task_for_worker(task["id"])
    trace = store.run_trace(saved["run_id"], "tenant-a")
    return runtime, store, product, saved, trace, service


def test_DS1_single_legal_image_passes(tmp_path, monkeypatch):
    runtime, _, _, saved, trace, _ = replay(tmp_path, monkeypatch, "single")
    assert saved["status"] == trace["status"] == "completed", trace["payload"].get("error")
    assert runtime.gateway_calls == 1


def test_DS2_DS3_DS4_original_case_retains_failed_attempt_and_explicit_lineage(tmp_path, monkeypatch):
    _, _, _, saved, trace, _ = replay(tmp_path, monkeypatch)
    payload = trace["payload"]
    first, second = payload["mcp_calls"]
    assert saved["status"] == trace["status"] == "completed"
    assert first["status"] == "failed" and first["failure_category"] == "argument_validation_error"
    assert first["validation_error"] == "argument_validation_error"
    assert first["created_at"] and first["completed_at"]
    assert first["supersession_status"] == "superseded"
    assert first["superseded_by"] == second["attempt_id"]
    assert second["retry_parent"] == first["attempt_id"]
    assert first["logical_dependency_id"] == second["logical_dependency_id"]
    assert first["request_fingerprint"] != second["request_fingerprint"]
    assert payload["tool_calls_completed"] is False
    assert payload["required_tool_calls_completed"] is True
    assert len(payload["logical_tool_dependencies"]) == 1
    assert payload["logical_tool_dependencies"][0]["status"] == "satisfied"


@pytest.mark.parametrize("mode,expected,count", [
    ("independent-fail", "failed", 1), ("independent-success", "completed", 2),
])
def test_DS5_DS6_two_independent_image_requirements(tmp_path, monkeypatch, mode, expected, count):
    runtime, _, _, saved, trace, _ = replay(tmp_path, monkeypatch, mode)
    assert saved["status"] == expected and runtime.gateway_calls == count
    assert len(trace["payload"]["logical_tool_dependencies"]) == 2


def test_DS7_same_tool_different_requirement_without_lineage_never_merges():
    first, second = correction_pair()
    second.pop("retry_of")
    _, dependencies, required = resolve_dependencies([first, second])
    assert len(dependencies) == 2 and not required["image_generation"]["satisfied"]


def test_identical_modern_arguments_do_not_imply_same_requirement_or_retry():
    first = observed({"prompt": "same image"}, status="failed")
    second = observed({"prompt": "same image"}, call_id="call-2")
    _, dependencies, required = resolve_dependencies([first, second])
    assert len(dependencies) == 2 and not required["image_generation"]["satisfied"]


def test_only_active_success_attempt_can_supply_final_artifact():
    from app.product_service import TaskService
    first, second = correction_pair()
    first["artifact"] = {"storage_key": "generated/tenant-a/failed.png"}
    second["artifact"] = {"storage_key": "generated/tenant-a/success.png"}
    calls, dependencies, _ = resolve_dependencies([first, second])
    trace = {"payload": {"mcp_calls": calls, "logical_tool_dependencies": dependencies}}
    assert TaskService._image_storage_key(trace) == "generated/tenant-a/success.png"


@pytest.mark.parametrize("mode", ["no-retry", "retry-fail"])
def test_DS8_DS9_missing_or_failed_retry_fails_task(tmp_path, monkeypatch, mode):
    runtime, store, _, saved, trace, _ = replay(tmp_path, monkeypatch, mode)
    assert saved["status"] == "failed" and runtime.gateway_calls == 0
    assert trace["payload"]["required_tool_calls_completed"] is False
    with store.connection() as c:
        assert c.execute("SELECT count(*) n FROM task_results WHERE task_id=?", (saved["id"],)).fetchone()["n"] == 0


def test_DS10_DS11_DS12_DS14_persistence_sse_credit_and_legacy_image(tmp_path, monkeypatch):
    runtime, store, product, saved, trace, service = replay(tmp_path, monkeypatch)
    public = product.task(saved["id"], "tenant-a", saved["user_id"])
    assert public["assistant_message_id"] == f"task:{saved['id']}:assistant"
    assert public["final_response"] == "完整海报回复"
    assert public["generation"]["task_id"] == saved["id"]
    assert public["generation"]["storage_key"] == trace["payload"]["mcp_calls"][1]["artifact"]["storage_key"]
    assert "artifact" not in trace["payload"]["mcp_calls"][0]
    assert service._persist_result(saved, trace)["replayed"] is True
    asyncio.run(service.execute(saved))
    assert runtime.turn_count == runtime.gateway_calls == runtime.downloads == 1
    with store.connection() as c:
        for table in ["task_results", "generations", "credit_transactions"]:
            assert c.execute(f"SELECT count(*) n FROM {table} WHERE task_id=?", (saved["id"],)).fetchone()["n"] == 1
        charge = c.execute("SELECT amount FROM credit_transactions WHERE task_id=?", (saved["id"],)).fetchone()
        assert charge["amount"] == -20
        assert c.execute("SELECT count(*) n FROM messages WHERE id=?", (public["assistant_message_id"],)).fetchone()["n"] == 1
    from app import main
    monkeypatch.setattr(main, "product_store", product)
    monkeypatch.setattr(main, "current_user", lambda _: SimpleNamespace(tenant_id="tenant-a", user_id=saved["user_id"], role="member"))

    async def sse():
        response = await main.stream_task_events(saved["id"], workbench_session="isolated")
        return "".join([x.decode() if isinstance(x, bytes) else x async for x in response.body_iterator])

    body = asyncio.run(sse())
    assert "event: complete\n" in body and "event: error\n" not in body


@pytest.mark.parametrize("tool", ["knowledge_search", "asset_search", "document_export", "custom_skill_tool"])
def test_DS13_generic_non_image_input_repair_and_shared_grounding_gate(tool):
    from app.domain import RuntimeTurn
    calls, dependencies, required = resolve_dependencies(list(correction_pair(tool=tool)))
    assert len(dependencies) == 1 and required[tool]["satisfied"]
    turn = RuntimeTurn("thread", "final", mcp_calls=tuple(calls))
    assert CodexRuntimeProvider._grounding_required_tools_satisfied(SimpleNamespace(required_tools=(tool,)), [turn])
    trace = AgentService._completed_trace({"artifacts": {}}, turn)
    assert trace["required_tool_calls_completed"] is True


@pytest.mark.parametrize("change", ["requirement", "scope", "server", "tool", "unknown-token", "forward", "duplicate-token", "duplicate-attempt"])
def test_changed_or_ambiguous_lineage_is_fail_closed(change):
    first, second = correction_pair()
    calls = [first, second]
    if change == "requirement":
        second["argument_fingerprints"]["requirement"] = "different"
    elif change == "scope":
        second["execution_scope"] = "another-turn"
    elif change == "unknown-token":
        second["retry_of"] = "not-a-receipt"
    elif change == "forward":
        calls.reverse()
    elif change == "duplicate-token":
        calls.insert(1, {**first, "attempt_id": "another-attempt"})
    elif change == "duplicate-attempt":
        second["attempt_id"] = first["attempt_id"]
    else:
        second[change] = "different"
    _, _, required = resolve_dependencies(calls)
    assert not all(x["satisfied"] for x in required.values())


@pytest.mark.parametrize("category", ["provider_timeout", "provider_5xx", "provider_policy_rejection", "permanent_dependency_error", "unknown"])
def test_provider_or_permanent_failure_does_not_allow_changed_input_supersession(category):
    first, second = correction_pair()
    first["failure_category"] = category
    _, _, required = resolve_dependencies([first, second])
    assert not required["image_generation"]["satisfied"]


def test_receipt_with_provider_side_effect_or_wrong_identity_is_rejected():
    for field, bad in [("provider_invoked", True), ("request_fingerprint", "wrong")]:
        args = {"format": "bad"}
        receipt = input_failure(args, ToolInputValidationError("invalid", repairable_fields=("format",)))
        receipt["_tool_dependency"][field] = bad
        call = observed(args, result={"structuredContent": receipt})
        assert call["failure_category"] == "unverified_retry_receipt"
        assert "retry_token" not in call


def test_validation_error_arguments_and_receipt_remain_content_free():
    args = {"prompt": "PRIVATE_ENTERPRISE_PROMPT", "format": "bad"}
    receipt = input_failure(args, ToolInputValidationError("invalid format", repairable_fields=("format",)))
    attempt = observed(args, result={"structuredContent": receipt})
    assert "PRIVATE_ENTERPRISE_PROMPT" not in json.dumps(receipt) + json.dumps(attempt)
    assert attempt["argument_fingerprints"] and attempt["provider_invoked"] is False
