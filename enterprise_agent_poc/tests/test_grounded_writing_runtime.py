"""R1-R16: safe-before-visible guard with a scripted, zero-network provider."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import pytest

from app.domain import RuntimeProfile, RuntimeSession, RuntimeStreamEvent, RuntimeTurn
from app.grounded_writing_runtime import SAFE_RESPONSE, apply_targeted_correction, parse_audit
from app.runtime.codex_provider import CodexRuntimeProvider
from app.service import AgentService, GenerationCancelled
from test_agent_execution import execution, context
from test_agent_productization import catalog, new_draft, published_skill


ENABLED = {"enabled": True, "mode": "claim_audit_v1", "max_corrections": 1}
UNSAFE = "活动已于9月12日举办，现场有百人参与。"
SAFE = "欢迎关注本次活动。\n\n详情以官方通知为准。"
CORRECTED = "活动时间【待确认】，欢迎关注。"


def draft(article=SAFE):
    return json.dumps({"fact_ledger": {"supported_facts": [], "user_intents": ["活动文案"],
            "allowed_general_statements": [], "unknowns": ["活动时间"], "event_state": "unknown"},
            "article": article}, ensure_ascii=False)


def audit_pass():
    return json.dumps({"grounded": True, "violations": []})


def audit_fail(article=UNSAFE, category="time"):
    return json.dumps({"grounded": False, "violations": [{"category": category, "location": article,
            "source_support": False, "reason": "原始材料无支持"}]}, ensure_ascii=False)


def correction(article=UNSAFE, replacement=CORRECTED):
    return json.dumps({"replacements": [{"location": article, "replacement": replacement}]}, ensure_ascii=False)


class ScriptedGroundedProvider(CodexRuntimeProvider):
    def __init__(self, steps, *, hold_call=None):
        super().__init__(None)
        self.steps = list(steps)
        self.calls = []
        self.hold_call = hold_call
        self.cancel_count = 0

    async def create_session(self, profile, developer_instructions):
        self._profiles[profile.id] = profile
        thread = "audit-thread" if not profile.tool_scopes and not profile.skill_manifest else "main-thread"
        # A synthetic productized draft has no required scopes, but its skill
        # binding still distinguishes the main thread from the tool-less audit.
        if developer_instructions.startswith("你是独立的事实审计器"):
            thread = "audit-thread"
        else:
            thread = "main-thread"
        return RuntimeSession(thread, profile.id)

    async def _collect_hidden_turn(self, session, profile, prompt, *, material_sink=None):
        self.calls.append((session.thread_id, profile, prompt))
        if len(self.calls) == self.hold_call:
            await asyncio.Event().wait()
        item = self.steps.pop(0)
        if isinstance(item, Exception):
            raise item
        return RuntimeTurn(session.thread_id, item)

    async def _stream_provider_turn(self, session, profile, message, **kwargs):
        yield RuntimeStreamEvent.visible_delta("raw-")
        yield RuntimeStreamEvent.visible_delta("stream")
        yield RuntimeStreamEvent.completed(RuntimeTurn(session.thread_id, "raw-stream"))

    async def cancel_turn(self, session):
        self.cancel_count += 1
        return True

    def startup_events(self, profile):
        return ()


def profile(*, enabled=True, tenant="tenant-a"):
    base = RuntimeProfile.build(tenant_id=tenant, agent_id="synthetic-writing", model_provider_id="deepseek",
                                model_id="deepseek-v4-pro", reasoning_effort="high", skill_manifest={"synthetic": "1"})
    return replace(base, profile_hash_version="v2", grounding_policy=ENABLED if enabled else {"enabled": False})


async def collect(provider, *, enabled=True):
    session = await provider.create_session(profile(enabled=enabled), "Agent instructions")
    return [event async for event in provider.stream_turn(session, "请写活动文案，具体时间未知。")]


def result(events):
    chunks = [event.text for event in events if event.kind == "delta"]
    turn = next(event.turn for event in events if event.kind == "completed")
    return "".join(chunks), turn


def test_r1_off_preserves_raw_streaming():
    events = asyncio.run(collect(ScriptedGroundedProvider([]), enabled=False))
    assert result(events)[0] == "raw-stream"
    assert [event.text for event in events if event.kind == "delta"] == ["raw-", "stream"]


def test_r2_r3_normal_path_hidden_until_audit_and_two_calls():
    provider = ScriptedGroundedProvider([draft(), audit_pass()])
    events = asyncio.run(collect(provider))
    first_delta = next(i for i, event in enumerate(events) if event.kind == "delta")
    assert any(event.text == "grounding_auditing:completed" for event in events[:first_delta])
    assert result(events)[0] == SAFE
    telemetry = result(events)[1].grounding_telemetry
    assert telemetry["generation_calls"] == telemetry["audit_calls"] == 1
    assert telemetry["initial_pass"] is telemetry["final_pass"] is True
    assert provider.calls[1][0] == "audit-thread"
    assert provider.calls[1][1].tool_scopes == () and provider.calls[1][1].skill_manifest == {}


def test_r4_r5_r13_r14_targeted_correction_only_after_reaudit():
    provider = ScriptedGroundedProvider([draft(UNSAFE), audit_fail(), correction(), audit_pass()])
    events = asyncio.run(collect(provider))
    visible, turn = result(events)
    assert visible == CORRECTED == turn.text
    assert UNSAFE not in visible
    assert len(provider.calls) == 4
    assert turn.grounding_telemetry["generation_calls"] == 2
    assert turn.grounding_telemetry["audit_calls"] == 2
    assert turn.grounding_telemetry["correction_invoked"] is True
    assert turn.grounding_telemetry["final_pass"] is True
    assert parse_audit(audit_pass(), CORRECTED).grounded
    assert apply_targeted_correction(correction(), UNSAFE, parse_audit(audit_fail(), UNSAFE)) == CORRECTED


def test_r6_second_audit_failure_hides_both_drafts():
    provider = ScriptedGroundedProvider([draft(UNSAFE), audit_fail(), correction(), audit_fail(CORRECTED, "action")])
    visible, turn = result(asyncio.run(collect(provider)))
    assert visible == turn.text == SAFE_RESPONSE
    assert UNSAFE not in visible and CORRECTED not in visible
    assert len(provider.calls) == 4
    assert turn.grounding_telemetry["final_pass"] is False
    assert turn.grounding_telemetry["violation_categories"] == ["action", "time"]


@pytest.mark.parametrize("hold_call,stage", [(1, "grounded_drafting"), (2, "grounding_auditing"),
                                             (3, "grounding_correcting"), (4, "grounding_revalidating")])
def test_r8_r10_stop_during_hidden_phase(hold_call, stage, execution):
    provider = ScriptedGroundedProvider([draft(UNSAFE), audit_fail(), correction(), audit_pass()], hold_call=hold_call)
    service = execution.tasks._agents
    service._runtime = provider
    stopped = False
    deltas = []

    def activity(actual_stage, status):
        nonlocal stopped
        if actual_stage == stage and status == "started":
            stopped = True

    async def scenario():
        session = await provider.create_session(profile(), "Agent instructions")
        with pytest.raises(GenerationCancelled):
            await asyncio.wait_for(service._run_turn(
                session, "input", lambda chunk: deltas.append(chunk), lambda: stopped,
                activity, grounding_enabled=True,
            ), timeout=2)

    asyncio.run(scenario())
    assert provider.cancel_count == 1
    assert len(provider.calls) == hold_call
    assert deltas == []


def test_r11_r12_provider_or_parse_failure_closes_without_draft():
    for steps in ([RuntimeError("SECRET draft provider failure")], [draft(UNSAFE), "not JSON"]):
        provider = ScriptedGroundedProvider(steps)
        visible, turn = result(asyncio.run(collect(provider)))
        assert visible == turn.text == SAFE_RESPONSE
        assert "SECRET" not in json.dumps(turn.grounding_telemetry)
        assert UNSAFE not in visible
        assert len(provider.calls) <= 2


def test_r13_invalid_correction_never_gets_fifth_call():
    provider = ScriptedGroundedProvider([draft(UNSAFE), audit_fail(), '{"replacements":[]}'])
    visible, turn = result(asyncio.run(collect(provider)))
    assert visible == SAFE_RESPONSE
    assert len(provider.calls) == 3
    assert turn.grounding_telemetry["audit_calls"] == 1


def test_r16_grounded_trace_excludes_tool_material_summaries():
    trace = {"artifacts": {"design_brief": None, "enterprise_context_used": None,
                           "knowledge_context_used": [], "reference_assets": [], "image_prompt": None}}
    turn = RuntimeTurn("thread", "核实后的正文", mcp_calls=({
        "server": "platform", "tool": "knowledge_search", "status": "completed",
        "dependency_id": "hash", "input_summary": "PRIVATE_SOURCE_QUERY",
        "output_summary": "PRIVATE_SOURCE_MATERIAL", "result_is_error": False,
    },), grounding_telemetry={"grounding_enabled": True, "final_pass": True})
    completed = AgentService._completed_trace(trace, turn)
    serialized = json.dumps(completed, ensure_ascii=False)
    assert "PRIVATE_SOURCE_QUERY" not in serialized
    assert "PRIVATE_SOURCE_MATERIAL" not in serialized
    assert completed["required_tool_calls_completed"] is True


def test_required_tool_failure_cannot_publish_article():
    provider = ScriptedGroundedProvider([draft(SAFE), audit_pass()])
    guarded = replace(profile(), required_tools=("knowledge_search",))

    async def scenario():
        session = await provider.create_session(guarded, "Agent instructions")
        return [event async for event in provider.stream_turn(session, "input")]

    visible, turn = result(asyncio.run(scenario()))
    assert visible == turn.text == SAFE_RESPONSE
    assert turn.grounding_telemetry["final_pass"] is False
    assert len(provider.calls) == 2


def test_cancel_at_render_boundary_emits_no_delta(execution):
    provider = ScriptedGroundedProvider([draft(), audit_pass()])
    execution.tasks._agents._runtime = provider
    stopped = False
    deltas = []

    def activity(stage, status):
        nonlocal stopped
        if stage == "grounded_rendering" and status == "started":
            stopped = True

    async def scenario():
        session = await provider.create_session(profile(), "Agent instructions")
        with pytest.raises(GenerationCancelled):
            await execution.tasks._agents._run_turn(
                session, "input", lambda chunk: deltas.append(chunk), lambda: stopped,
                activity, grounding_enabled=True,
            )

    asyncio.run(scenario())
    assert deltas == [] and len(provider.calls) == 2


def _draft_task(c):
    template_id, revision_id = new_draft(c, grounding_policy=ENABLED)
    c.control.bind_skills(template_id, revision_id, [published_skill(c)], c.actor)
    with c.store.connection() as conn:
        conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id,overrides_json) "
                     "VALUES ('tenant-a',?,'configured',?,?,'{}')", (template_id, str(uuid4()), revision_id))
    task = c.product.create_task("tenant-a", c.actor, template_id, "请写活动文案，具体时间未知。", None,
                                 _test_revision=revision_id, _test_id=str(uuid4()))
    return task


def test_r7_r15_r16_product_persists_only_safe_text_and_metadata(execution):
    c = execution
    task = _draft_task(c)
    provider = ScriptedGroundedProvider([draft(UNSAFE), audit_fail(), correction(), audit_pass()])
    c.tasks._agents._runtime = provider
    asyncio.run(c.tasks.execute(task))
    saved = c.product.task_for_worker(task["id"])
    assert saved["status"] == "completed", (saved["stage"], saved.get("error_code"), len(provider.calls))
    trace = c.store.run_trace(saved["run_id"], "tenant-a")
    assert trace["payload"]["grounding"]["generation_calls"] == 2
    assert trace["payload"]["grounding"]["audit_calls"] == 2
    assert trace["payload"]["final_result"] == CORRECTED
    assert trace["payload"]["artifacts"]["design_brief"] is None
    serialized = json.dumps(trace["payload"], ensure_ascii=False)
    assert UNSAFE not in serialized and "原始材料无支持" not in serialized
    assert c.store.run_trace(saved["run_id"], "tenant-b") is None
    with c.store.connection() as conn:
        assistant = conn.execute("SELECT content FROM messages WHERE id=?", (f"task:{task['id']}:assistant",)).fetchone()
        assert assistant["content"] == CORRECTED
    delta = [json.loads(event["message"])["text"] for event in c.product.task_events_since(task["id"], "tenant-a", c.actor)
             if event["stage"] == "delta"]
    assert "".join(delta) == CORRECTED
    snapshot = context(c, task)
    assert snapshot["tenant_id"] == "tenant-a"
    assert json.loads(snapshot["tool_policy_snapshot"])["grounding"] == ENABLED
    assert snapshot["configuration_fingerprint"]


def test_product_persists_only_fail_closed_response(execution):
    c = execution
    task = _draft_task(c)
    c.tasks._agents._runtime = ScriptedGroundedProvider(
        [draft(UNSAFE), audit_fail(), correction(), audit_fail(CORRECTED, "result")]
    )
    asyncio.run(c.tasks.execute(task))
    saved = c.product.task_for_worker(task["id"])
    assert saved["status"] == "completed"
    trace = c.store.run_trace(saved["run_id"], "tenant-a")["payload"]
    assert trace["grounding"]["final_pass"] is False
    assert trace["final_result"] == SAFE_RESPONSE
    serialized = json.dumps(trace, ensure_ascii=False)
    assert UNSAFE not in serialized and CORRECTED not in serialized
    with c.store.connection() as conn:
        assistant = conn.execute("SELECT content FROM messages WHERE id=?", (f"task:{task['id']}:assistant",)).fetchone()
        assert assistant["content"] == SAFE_RESPONSE
