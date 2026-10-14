from __future__ import annotations

import asyncio
import json
import re
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from app import main
from app.activity_plan_runtime import (
    PLAN_SCHEMA, SAFE_FALLBACK, chunk_validated_markdown, is_full_activity_plan, parse_plan,
    render_markdown, result_envelope, semantic_guard, validated_envelope,
)
from app.document_generator import ActivityPlanContent, ActivityPlanDocumentService
from app.domain import RuntimeProfile, RuntimeSession, RuntimeStreamEvent, RuntimeTurn
from app.product_service import TaskService
from app.product_store import ProductStore
from app.runtime.codex_provider import CodexRuntimeProvider
from app.service import AgentService
from app.settings import Settings
from app.storage import LocalStorage
from app.store import POCStore


FIXTURES = Path(__file__).parent / "fixtures"
PRODUCTION_P0 = json.loads((FIXTURES / "activity_plan_production_p0.json").read_text(encoding="utf-8"))
PRODUCTION_REQUEST = PRODUCTION_P0["input_text"]
CONFIRMED_BRIEF = "\n活动内容：社区共读；时间：2026年10月20日；地点：社区活动室；对象：家长和孩子。"
SEMANTIC_REQUEST = "请生成完整详细的国庆节活动方案" + CONFIRMED_BRIEF


def plan() -> dict:
    result = json.loads((FIXTURES / "activity_plan_d2.json").read_text(encoding="utf-8"))
    result["document_title"] = "社区教师节完整活动方案"
    result["activity_theme"] = "社区教师节共读活动"
    return result


class ScriptedProvider(CodexRuntimeProvider):
    def __init__(self, responses: list[str], grounding: tuple[str, ...] = ()) -> None:
        super().__init__(None)
        self.responses = list(responses)
        self.options = []
        self.grounding = grounding

    async def create_session(self, profile, developer_instructions):
        self._profiles[profile.id] = profile
        return RuntimeSession("scripted-thread", profile.id)

    async def resume_session(self, profile, thread_id, **kwargs):
        self._profiles[profile.id] = profile
        return RuntimeSession(thread_id, profile.id)

    def startup_events(self, profile):
        return ()

    async def _stream_provider_turn(self, session, profile, message, *, output_schema=None, visible_deltas=True):
        self.options.append((message, output_schema is not None, visible_deltas))
        raw = self.responses.pop(0)
        if isinstance(raw, Exception):
            raise raw
        yield RuntimeStreamEvent.activity("tool_running", "completed")
        if visible_deltas:
            yield RuntimeStreamEvent.visible_delta(raw)
        calls = tuple({"tool": "knowledge_search", "server": "platform", "status": "completed",
                       "output_summary": value, "input_summary": None, "error": None} for value in self.grounding)
        yield RuntimeStreamEvent.completed(RuntimeTurn(session.thread_id, raw, mcp_calls=calls))


def collect(provider: ScriptedProvider, message: str, agent_id: str = "campaign-agent"):
    profile = RuntimeProfile.build(
        tenant_id="tenant-a", agent_id=agent_id, model_provider_id="deepseek",
        model_id="deepseek-v4-pro", reasoning_effort="high", skill_manifest={},
    )

    async def run():
        session = await provider.create_session(profile, "")
        return [event async for event in provider.stream_turn(session, message)]

    return asyncio.run(run())


def full_request() -> str:
    return "请给社区教师节设计完整活动方案，包含主题、时间、地点、宣发、执行和邀约" + CONFIRMED_BRIEF


ORDINARY_FULL_REQUESTS = [
    "帮我做一个国庆节的活动策划案",
    "生成一份国庆活动策划案",
    "帮我制定社区活动方案",
    "做一份活动执行方案",
    "请生成完整详细的活动策划方案",
    PRODUCTION_REQUEST,
]


@pytest.mark.parametrize("message", ORDINARY_FULL_REQUESTS + [
    "帮我做一个活动策划方案", "制定活动执行方案",
    "帮我做一个活动策划案，包含优化活动报名流程的建议",
    "请生成一份完整详细的国庆节活动方案，包含活动主题、时间地点、宣传渠道、执行环节、邀请文案和待确认事项。",
    "帮我设计一场面向家长的体验活动，输出时间流程、人员分工和物料清单。",
    "请策划一次社区共读活动，包含活动目标、执行环节、邀约文案和风险预案。",
    "为邻里筹备一次活动，给出详细议程和岗位分工。",
    "策划一次邻里活动，输出详细时间流程和物料清单。",
    "麻烦为社区组织一场活动，输出执行环节、人员分工和风险预案。",
])
def test_ordinary_full_plan_creation_routes_to_structured_generation(message):
    assert is_full_activity_plan("campaign-agent", message)
    # Current product prerequisites are explicit; retain the original intent
    # samples and test their incomplete-input behavior in test_agent_quality_p0.
    message += CONFIRMED_BRIEF
    source = plan()
    provider = ScriptedProvider([json.dumps(source, ensure_ascii=False)])
    turn = collect(provider, message)[-1].turn
    assert turn.structured_result == {"type": "activity_plan", "version": "1", "data": source}
    assert turn.structured_result_status == "validated_initial"
    assert turn.structured_model_calls == 1
    assert provider.options[0][1:] == (True, False)
    assert provider.options[0][0].startswith(message + "\n\n")


@pytest.mark.parametrize("message", [
    "给我想5个活动标题", "写3条国庆宣传文案", "帮我修改活动邀请语",
    "活动策划案是什么意思", "给我活动方案的几个创意", "普通聊天，你好",
    "这个活动策划案的预算怎么安排", "请修改社区活动方案的时间",
    "完整活动方案是什么", "刚才的完整活动方案有什么亮点",
    "帮我写一条活动策划案宣传文案",
    "如何策划一次家长活动，需要考虑人员分工和物料清单吗？",
    "请问怎样设计一场体验活动的流程和风险预案？",
    "请解释如何组织一次活动，以及人员分工和宣传话术的作用。",
    "请分析这次活动的时间流程和人员分工是否合理。",
    "请为学习习惯活动想5个标题。",
    "请帮我写一段学习习惯活动的邀请文案。",
    "请修改刚才活动的风险预案。",
    "活动已经策划好了，请点评活动目标和人员分工。",
    "他们正在策划一次家长活动，请点评人员分工和风险预案。",
    "帮我设计活动标题，要体现时间流程和人员分工。",
    "请为活动写邀约文案，提到人员分工和活动目标。",
])
def test_partial_requests_definitions_and_followups_keep_text_path(message):
    assert not is_full_activity_plan("campaign-agent", message)
    provider = ScriptedProvider(["普通回复"])
    turn = collect(provider, message)[-1].turn
    assert turn.text == "普通回复" and turn.structured_result is None
    assert turn.structured_model_calls == 0
    assert provider.options[0][1:] == (False, True)


@pytest.mark.parametrize("message", ORDINARY_FULL_REQUESTS)
def test_full_plan_wording_does_not_route_copywriting_agent(message):
    assert not is_full_activity_plan("copywriting-agent", message)
    turn = collect(ScriptedProvider(["普通文案回复"]), message, "copywriting-agent")[-1].turn
    assert turn.structured_result is None and turn.structured_model_calls == 0


def production_semantic_plan(attempt: int) -> dict:
    # 04 supplied exact public excerpts, not a full Production JSON export.
    source = plan()
    source.update(activity_time="【待确认】", activity_location="【待确认】",
                  activity_theme="欢度国庆·礼遇邻里", pending_items=PRODUCTION_P0["pending_items"],
                  invitation_copy=PRODUCTION_P0["raw_attempt_excerpts"][attempt]["invitation_copy"])
    source["activity_items"][0]["description"] = PRODUCTION_P0["raw_attempt_excerpts"][attempt]["activity_description"]
    return source


def test_production_corrected_invitation_is_not_a_gift_commitment():
    source = production_semantic_plan(1)
    assert semantic_guard(ActivityPlanContent.model_validate(source)) == []
    turn = collect(ScriptedProvider([json.dumps(source, ensure_ascii=False)]), full_request())[-1].turn
    assert turn.structured_result == result_envelope(ActivityPlanContent.model_validate(source))
    assert turn.structured_result_status == "validated_initial"
    assert turn.structured_model_calls == 1
    assert validated_envelope(turn.structured_result, turn.text) == turn.structured_result


def test_production_unconfirmed_gift_requires_real_targeted_correction():
    source, fixed = production_semantic_plan(0), production_semantic_plan(1)
    assert any(v.dependency_kind == "benefit" for v in semantic_guard(ActivityPlanContent.model_validate(source)))
    events = collect(ScriptedProvider([json.dumps(source, ensure_ascii=False),
                                      json.dumps(fixed, ensure_ascii=False)]), full_request())
    turn = events[-1].turn
    # The existing conditional-disclosure rule already accepts the first
    # invitation; only the description was in conflict. Keep the established
    # targeted merge: the model cannot rewrite an unflagged field on retry.
    expected = {**fixed, "invitation_copy": source["invitation_copy"]}
    assert turn.structured_result["data"] == expected
    assert turn.structured_result_status == "validated_after_retry"
    assert turn.structured_model_calls == 2
    assert "".join(event.text for event in events if event.kind == "delta") == render_markdown(ActivityPlanContent.model_validate(expected))
    assert all("可获得印章" not in event.text for event in events if event.kind == "delta")


@pytest.mark.parametrize("copy", [
    "国庆佳节将至，诚邀您参加“欢度国庆·礼遇邻里”主题活动。",
    "国庆佳节即将来临，诚邀参加“礼遇邻里”主题活动。",
    "国庆佳节将近，欢迎参加“礼遇邻里”主题活动。",
    "国庆佳节将到来，欢迎参加“礼遇邻里”主题活动。",
    "现场将开展“欢度国庆·礼遇邻里”主题活动。",
    '我们将组织"礼遇邻里"主题活动。',
    "活动主题为“礼遇邻里”，将举行邻里交流。",
    "主题：‘礼遇邻里’，将举行邻里交流。",
])
def test_temporal_language_and_named_theme_are_not_unconfirmed_benefits(copy):
    source = plan()
    source.update(invitation_copy=copy, pending_items=["礼品与权益预算待确认"])
    assert semantic_guard(ActivityPlanContent.model_validate(source)) == []


@pytest.mark.parametrize("copy", [
    "现场将提供礼品。", "现场会赠送奖品。", "参加即可领取礼品。",
    "礼品已准备，活动将开始。", "国庆佳节将至，现场将赠送课程礼品。",
    "现场将开展“礼遇邻里”主题活动，并提供礼品。",
    "活动主题为“礼遇邻里”，现场将提供礼遇礼包。",
    "现场将提供“礼遇礼包”。", "礼遇已安排。",
    "现场将提供“礼遇礼包”主题活动。",
    "现场将安排“礼遇礼盒”主题活动。",
    "活动主题为“礼遇礼包”，现场将提供。",
    "现场将开展“礼遇·赠送礼品”主题活动。",
    "礼遇详情将另行通知，但现场仍将赠送课程礼品。",
    "礼品明天将至。", "礼品将近100件，现场提供。",
])
def test_real_unconfirmed_gifts_remain_rejected(copy):
    source = plan()
    source.update(invitation_copy=copy, pending_items=["礼品与权益预算待确认"])
    assert any(v.type == "pending_dependency_committed" and v.dependency_kind == "benefit"
               for v in semantic_guard(ActivityPlanContent.model_validate(source)))


@pytest.mark.parametrize("copy", [
    "现场将提供价值1999元课程。", "课程售价999元。", "活动价格199元。",
    "可享优惠100元。", "现场将赠送200元礼品。", "活动主题为“免费99元课程”，将举办体验。",
])
def test_unverified_prices_discounts_and_benefits_still_reject(copy):
    source = plan()
    source["invitation_copy"] = copy
    assert any(v.type == "unverified_enterprise_benefit" for v in semantic_guard(ActivityPlanContent.model_validate(source)))


def test_fixed_contract_has_identical_fields_and_four_column_rows():
    source = PRODUCTION_P0["scripted_plan"]
    expected = {"document_title", "activity_theme", "activity_time", "activity_location", "target_audience",
                "promotion_channels", "activity_items", "invitation_copy", "pending_items"}
    assert set(source) == set(PLAN_SCHEMA["required"]) == set(PLAN_SCHEMA["properties"]) == set(ActivityPlanContent.model_fields) == expected
    assert PLAN_SCHEMA["additionalProperties"] is False
    assert PLAN_SCHEMA["properties"]["activity_items"]["items"]["additionalProperties"] is False
    for key in expected:
        assert parse_plan(json.dumps({k: v for k, v in source.items() if k != key})) is None
    assert parse_plan(json.dumps({**source, "risk": "must not add a new field"})) is None
    for key in ("phase", "name", "description", "image_requirement"):
        assert parse_plan(json.dumps({**source, "activity_items": [{k: v for k, v in source["activity_items"][0].items() if k != key}]})) is None
    parsed = ActivityPlanContent.model_validate(source)
    text = render_markdown(parsed)
    assert "| 环节 | 名称 | 说明 | 示意图需求 |" in text
    assert validated_envelope(result_envelope(parsed), text) == result_envelope(parsed)
    assert validated_envelope(result_envelope(parsed), text + "\n未经验证的附加正文") is None
    assert validated_envelope(None, text) is None
    assert validated_envelope({**result_envelope(parsed), "version": 1}, text) is None
    assert validated_envelope({**result_envelope(parsed), "version": "2"}, text) is None
    assert validated_envelope({**result_envelope(parsed), "type": "copywriting"}, text) is None
    assert parse_plan(json.dumps({**source, "activity_items": [
        {**source["activity_items"][0], "budget": "unapproved field"}
    ]})) is None


def activity_stages(events) -> list[str]:
    return [event.text for event in events if event.kind == "activity"]


def test_rs1_simple_request_keeps_multiple_raw_token_deltas_without_plan_stages():
    class MultiTokenProvider(ScriptedProvider):
        async def _stream_provider_turn(self, session, profile, message, *, output_schema=None, visible_deltas=True):
            for token in ("五", "个", "标题"):
                yield RuntimeStreamEvent.visible_delta(token)
            yield RuntimeStreamEvent.completed(RuntimeTurn(session.thread_id, "五个标题"))

    events = collect(MultiTokenProvider([]), "给我想5个社区教师节活动标题")
    assert [event.text for event in events if event.kind == "delta"] == ["五", "个", "标题"]
    assert not any(stage.startswith(("structured_validating", "semantic_validating", "semantic_correcting"))
                   for stage in activity_stages(events))


def test_rs2_full_plan_real_stage_order_without_unneeded_correction():
    events = collect(ScriptedProvider([json.dumps(plan(), ensure_ascii=False)]), full_request())
    stages = activity_stages(events)
    expected = ["full_plan_generating:started", "full_plan_generating:completed",
                "structured_validating:started", "structured_validating:completed",
                "semantic_validating:started", "semantic_validating:completed",
                "result_rendering:started", "result_rendering:completed"]
    positions = [stages.index(stage) for stage in expected]
    assert positions == sorted(positions)
    assert not any(stage.startswith("semantic_correcting:") for stage in stages)


def test_rs3_correction_stage_only_when_a_second_semantic_model_call_occurs():
    source = plan()
    source["pending_items"] = ["工作人员未确认"]
    source["invitation_copy"] = "工作人员将进行一对一跟进。"
    fixed = {**source, "invitation_copy": "如工作人员确认到位，可安排一对一跟进。"}
    events = collect(ScriptedProvider([
        json.dumps(source, ensure_ascii=False), json.dumps(fixed, ensure_ascii=False)
    ]), full_request())
    stages = activity_stages(events)
    assert stages.count("semantic_correcting:started") == 1
    assert stages.count("semantic_correcting:completed") == 1
    assert stages.count("structured_validating:started") == 2
    assert stages.count("semantic_validating:started") == 2


def test_rs4_safe_before_visible_and_rs5_validated_chunks_and_rs6_reconstruction():
    events = collect(ScriptedProvider([json.dumps(plan(), ensure_ascii=False)]), full_request())
    first_delta = next(index for index, event in enumerate(events) if event.kind == "delta")
    guard_completed = next(index for index, event in enumerate(events)
                           if event.text == "semantic_validating:completed")
    assert first_delta > guard_completed
    chunks = [event.text for event in events if event.kind == "delta"]
    assert len(chunks) > 1
    assert max(map(len, chunks)) <= 120
    assert "".join(chunks) == events[-1].turn.text


def test_rs7_table_rows_and_unicode_reconstruct_exactly():
    markdown = render_markdown(ActivityPlanContent.model_validate(plan()))
    chunks = chunk_validated_markdown(markdown, max_chars=80)
    assert len(chunks) > 1
    assert max(map(len, chunks)) <= 80
    assert "".join(chunks) == markdown
    assert "| 环节 | 名称 | 说明 | 示意图需求 |" in "".join(chunks)


def test_rg1_simple_request_and_other_agents_keep_text_path():
    simple = "给我想5个社区教师节活动标题"
    assert not is_full_activity_plan("campaign-agent", simple)
    events = collect(ScriptedProvider(["五个活动标题"]), simple)
    assert events[-1].turn.text == "五个活动标题"
    assert events[-1].turn.structured_result is None
    assert not is_full_activity_plan("copywriting-agent", full_request())


def test_rg2_rg3_full_plan_contract_and_deterministic_markdown():
    source = plan()
    provider = ScriptedProvider([json.dumps(source, ensure_ascii=False)])
    events = collect(provider, full_request())
    final = events[-1].turn
    assert final.structured_result == {"type": "activity_plan", "version": "1", "data": source}
    assert final.text == render_markdown(ActivityPlanContent.model_validate(source))
    assert "社区教师节" in final.text
    assert provider.options == [(provider.options[0][0], True, False)]
    assert events[0].text == "full_plan_generating:started"
    assert len([event for event in events if event.kind == "delta"]) > 1
    assert "".join(event.text for event in events if event.kind == "delta") == final.text


def test_rg4_fenced_or_explained_json_needs_one_correction_or_fails_safe():
    source = json.dumps(plan(), ensure_ascii=False)
    assert parse_plan(f"```json\n{source}\n```") is None
    assert parse_plan("解释：" + source) is None
    corrected = collect(ScriptedProvider([f"```json\n{source}\n```", source]), full_request())[-1].turn
    assert corrected.structured_result is not None
    failed = collect(ScriptedProvider(["说明：" + source, "仍非 JSON"]), full_request())[-1].turn
    assert failed.structured_result is None
    assert "暂无法生成" in failed.text
    assert failed.structured_diagnostic["code"] == "format_validation_failed_after_retry"
    assert failed.structured_model_calls == 2
    assert [step["stage"] for step in failed.structured_attempt_trace] == [
        "INITIAL", "FORMAT_VALIDATION", "SEMANTIC_VALIDATION", "CORRECTION",
        "FORMAT_VALIDATION", "REVALIDATION", "FINAL",
    ]


def test_schema_rejection_uses_only_one_raw_json_retry():
    source = json.dumps(plan(), ensure_ascii=False)
    provider = ScriptedProvider([RuntimeError("output_schema unsupported"), source])
    turn = collect(provider, full_request())[-1].turn
    assert turn.structured_result is not None
    assert turn.structured_model_calls == 2
    assert [option[1] for option in provider.options] == [True, False]


def test_targeted_correction_preserves_non_conflict_fields_and_revalidates():
    source = plan()
    source["pending_items"] = ["工作人员未确认"]
    source["invitation_copy"] = "工作人员将进行一对一跟进。"
    corrected = {**source, "document_title": "模型擅自改写的标题",
                 "invitation_copy": "如工作人员确认到位，可安排一对一跟进。"}
    turn = collect(ScriptedProvider([
        json.dumps(source, ensure_ascii=False), json.dumps(corrected, ensure_ascii=False)
    ]), full_request())[-1].turn
    assert turn.structured_result["data"]["document_title"] == source["document_title"]
    assert turn.structured_result["data"]["invitation_copy"] == corrected["invitation_copy"]
    assert turn.structured_result_status == "validated_after_retry"
    assert turn.structured_attempt_trace[-1]["correction_result"] == "PASS"


def test_new_semantic_violation_after_retry_fails_safe_without_content_trace():
    source = plan()
    source["pending_items"] = ["工作人员未确认"]
    source["invitation_copy"] = "工作人员将进行一对一跟进。"
    corrected = {**source, "invitation_copy": "如工作人员确认到位，可安排一对一跟进。现场将赠送价值1999元课程。"}
    turn = collect(ScriptedProvider([
        json.dumps(source, ensure_ascii=False), json.dumps(corrected, ensure_ascii=False)
    ]), full_request())[-1].turn
    assert turn.structured_result is None
    assert turn.structured_result_status == "semantic_guard_failed_after_retry"
    assert turn.structured_model_calls == 2
    assert turn.structured_attempt_trace[-1]["correction_result"] == "FAIL"
    assert {item["type"] for item in turn.structured_attempt_trace[-2]["violations"]} == {
        "unverified_enterprise_benefit"
    }
    assert "1999" not in json.dumps(turn.structured_attempt_trace, ensure_ascii=False)


def test_rg5_pending_staff_dependency_is_corrected_only_in_conflict_field():
    source = plan()
    source["pending_items"] = ["工作人员未确认"]
    source["invitation_copy"] = "工作人员将进行一对一跟进。"
    issues = semantic_guard(ActivityPlanContent.model_validate(source))
    assert any(item.field == "invitation_copy" for item in issues)
    fixed = {**source, "invitation_copy": "如工作人员确认到位，可安排一对一跟进，具体以确认结果为准。"}
    result = collect(ScriptedProvider([json.dumps(source, ensure_ascii=False), json.dumps(fixed, ensure_ascii=False)]), full_request())[-1].turn
    assert result.structured_result["data"] == fixed


def test_rg6_pending_material_dependency_requires_conditional_language():
    source = plan()
    source["pending_items"] = ["手作物料尚未采购"]
    source["invitation_copy"] = "现场设有手作区。"
    assert any(item.type == "pending_dependency_committed" for item in semantic_guard(ActivityPlanContent.model_validate(source)))
    fixed = {**source, "invitation_copy": "拟设置手作区，具体以物料确认后为准。"}
    result = collect(ScriptedProvider([json.dumps(source, ensure_ascii=False), json.dumps(fixed, ensure_ascii=False)]), full_request())[-1].turn
    assert result.structured_result["data"] == fixed


def test_conditional_material_readiness_and_safe_fallback_are_not_false_positives():
    source = plan()
    source["pending_items"] = ["心愿卡物料采购与数量【待确认】"]
    source["invitation_copy"] = (
        "若心愿卡与手作材料已备齐，还可参与寄语墙与感恩手作工坊。"
        "若材料未到位，相关环节将调整为口头寄语或绘画致谢。"
    )
    source["activity_items"][0]["description"] = (
        "若心愿卡物料已采购到位，参与者现场领取心愿卡并写下感谢。"
    )
    assert semantic_guard(ActivityPlanContent.model_validate(source)) == []


def test_negative_condition_cannot_hide_continued_material_commitment():
    source = plan()
    source["pending_items"] = ["手作物料尚未采购"]
    source["invitation_copy"] = "若手作物料未到位，现场仍将提供手作材料。"
    assert any(item.type == "pending_dependency_committed" for item in
               semantic_guard(ActivityPlanContent.model_validate(source)))


def test_pending_date_disclosure_and_verified_course_description_do_not_cross_trigger():
    source = plan()
    source["pending_items"] = [
        "活动具体日期、时段与场地地址", "教师专属礼遇具体内容待确认",
        "实际参与讲师及课程演示的具体安排",
    ]
    source["invitation_copy"] = "活动具体日期、地点与礼遇内容将另行通知，敬请关注官方通知。"
    source["activity_items"][0]["description"] = "参与者获得活动参与感，下一步进入主会场环节。"
    source["activity_items"].append({**source["activity_items"][0],
        "description": "介绍已核实的秋季课程分层教学方式，提供轻量学习体验，不承诺提分。"})
    assert semantic_guard(ActivityPlanContent.model_validate(source)) == []


def test_disclosure_does_not_mask_a_separate_benefit_promise():
    source = plan()
    source["pending_items"] = ["教师专属礼遇内容待确认"]
    source["invitation_copy"] = "礼遇详情将另行通知，但现场仍将赠送课程礼品。"
    assert any(item.dependency_kind == "benefit" for item in
               semantic_guard(ActivityPlanContent.model_validate(source)))


def test_rg7_proposed_activity_does_not_require_enterprise_grounding():
    source = plan()
    source["activity_items"][0]["description"] = "建议设置教师心愿卡互动。"
    assert semantic_guard(ActivityPlanContent.model_validate(source)) == []


def test_rg8_rg9_enterprise_benefit_requires_matching_tool_evidence():
    source = plan()
    source["invitation_copy"] = "现场将提供价值1999元课程。"
    parsed = ActivityPlanContent.model_validate(source)
    assert any(item.type == "unverified_enterprise_benefit" for item in semantic_guard(parsed))
    assert semantic_guard(parsed, ("企业资料：价值1999元课程，已确认可用于活动。",)) == []
    failed = collect(ScriptedProvider([json.dumps(source, ensure_ascii=False)] * 2), full_request())[-1].turn
    assert failed.structured_result is None
    verified = collect(ScriptedProvider([json.dumps(source, ensure_ascii=False)], ("价值1999元课程",)), full_request())[-1].turn
    assert verified.structured_result["data"] == source


@pytest.mark.parametrize("message_request", ORDINARY_FULL_REQUESTS + [SEMANTIC_REQUEST])
def test_rg10_rg11_message_association_and_sse_compatibility(tmp_path, monkeypatch, message_request):
    monkeypatch.setenv("ENTERPRISE_POC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENTERPRISE_POC_DATABASE_URL", f"sqlite:///{tmp_path / 'product.db'}")
    monkeypatch.setenv("ENTERPRISE_POC_OBJECT_STORAGE_DIR", str(tmp_path / "objects"))
    store = POCStore(tmp_path / "product.db")
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()
    product.create_user("tenant-a", "bridge-member@tenant-a.test", main.hash_password("ChangeMe!2026"), "成员", "member")
    member = product.user_by_email("bridge-member@tenant-a.test")
    source = (PRODUCTION_P0["scripted_plan"] if message_request == PRODUCTION_REQUEST
              else production_semantic_plan(1) if message_request == SEMANTIC_REQUEST else plan())
    runtime = ScriptedProvider(["五个标题", json.dumps(source, ensure_ascii=False)])
    service = TaskService(product, AgentService(store, runtime, Settings.from_env()))
    first = product.create_task("tenant-a", member["id"], "campaign-agent", "给我想5个社区教师节活动标题", None)
    asyncio.run(service.execute(first))
    first_saved = product.task(first["id"], "tenant-a", member["id"])
    second = product.create_task("tenant-a", member["id"], "campaign-agent", message_request + CONFIRMED_BRIEF, first_saved["conversation_id"])
    asyncio.run(service.execute(second))
    second_saved = product.task(second["id"], "tenant-a", member["id"])
    assert first_saved["structured_result"] is None
    assert second_saved["structured_result"]["type"] == "activity_plan"
    assert second_saved["structured_result"]["data"] == source
    assert second_saved["final_response"] == render_markdown(ActivityPlanContent.model_validate(source))
    assert second_saved["assistant_message_id"] == f"task:{second['id']}:assistant"
    assert first_saved["assistant_message_id"] != second_saved["assistant_message_id"]
    assert product.conversation_detail("tenant-a", member["id"], second_saved["conversation_id"])["messages"][-1]["id"] == second_saved["assistant_message_id"]
    trace = store.run_trace(second_saved["run_id"], "tenant-a")["payload"]
    assert trace["structured_result_status"] == "validated_initial"
    assert trace["total_model_calls_for_structured_result"] == 1
    assert [step["stage"] for step in trace["structured_attempt_trace"]] == [
        "INITIAL", "FORMAT_VALIDATION", "SEMANTIC_VALIDATION", "FINAL",
    ]

    monkeypatch.setattr(main, "product_store", product)
    monkeypatch.setattr(main, "document_service", ActivityPlanDocumentService(LocalStorage(tmp_path / "objects")))
    token = main.sessions.issue(main.UserPrincipal(member["id"], "tenant-a", "member", main.sessions.credential_version(member["password_hash"])))
    client = TestClient(main.app)
    try:
        client.cookies.set("workbench_session", token)
        with client.stream("GET", f"/api/v1/tasks/{second['id']}/events") as response:
            body = "".join(response.iter_text())
        assert response.status_code == 200
        assert all(f"event: {event}" in body for event in ("progress", "activity", "delta", "complete"))
        event_names = set(re.findall(r"^event: ([a-z]+)$", body, re.MULTILINE))
        assert event_names <= {"progress", "activity", "delta", "complete", "error", "cancelled"}
        assert "full_plan_generating" in body and "structured_validating" in body
        assert "semantic_validating" in body and "result_rendering" in body
        assert "created_at" in body
        complete = json.loads(body.split("event: complete\ndata: ", 1)[1].split("\n\n", 1)[0])
        assert complete["assistant_message_id"] == second_saved["assistant_message_id"]
        assert complete["structured_result"] == second_saved["structured_result"]
        sse_deltas = [json.loads(value)["text"] for value in re.findall(r"event: delta\ndata: (.+?)\n\n", body)]
        assert len(sse_deltas) > 1
        assert "".join(sse_deltas) == second_saved["final_response"]
        task_response = client.get(f"/api/v1/tasks/{second['id']}")
        assert task_response.status_code == 200
        assert task_response.json()["structured_result"] == complete["structured_result"]
        history = client.get(f"/api/v1/conversations/{second_saved['conversation_id']}")
        assert history.status_code == 200
        message = history.json()["messages"][-1]
        assert message["id"] == complete["assistant_message_id"]
        # The existing frontend restores exportability via the message's Task,
        # rather than embedding trusted plan JSON in the history response.
        restored = client.get(f"/api/v1/tasks/{message['task_id']}").json()
        assert restored["assistant_message_id"] == message["id"]
        assert restored["structured_result"] == complete["structured_result"]
        created = client.post("/api/v1/documents/activity-plan", json={"content": restored["structured_result"]["data"]})
        assert created.status_code == 201
        downloaded = client.get(created.json()["download_url"])
        assert downloaded.status_code == 200
        with ZipFile(BytesIO(downloaded.content)) as archive:
            assert archive.testzip() is None
            xml = ElementTree.fromstring(archive.read("word/document.xml"))
        text = "".join(node.text or "" for node in xml.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
        for value in source.values():
            entries = value if isinstance(value, list) else [value]
            for entry in entries:
                values = entry.values() if isinstance(entry, dict) else [entry]
                for field in values:
                    assert all(line in text for line in field.splitlines() if line.strip())
        assert str(tmp_path) not in json.dumps(created.json()) + body + history.text
        assert "storage_key" not in created.json()
        assert product.task(first["id"], "tenant-a", member["id"]) == first_saved
    finally:
        client.close()


def test_semantic_failure_after_one_retry_completes_without_exportable_result(tmp_path, monkeypatch):
    monkeypatch.setenv("ENTERPRISE_POC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENTERPRISE_POC_DATABASE_URL", f"sqlite:///{tmp_path / 'product.db'}")
    store = POCStore(tmp_path / "product.db")
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()
    product.create_user("tenant-a", "unsafe-member@tenant-a.test", main.hash_password("ChangeMe!2026"), "成员", "member")
    member = product.user_by_email("unsafe-member@tenant-a.test")
    source = plan()
    source["pending_items"] = ["工作人员未确认"]
    source["invitation_copy"] = "工作人员将进行一对一跟进。"
    runtime = ScriptedProvider([json.dumps(source, ensure_ascii=False)] * 2)
    service = TaskService(product, AgentService(store, runtime, Settings.from_env()))
    task = product.create_task("tenant-a", member["id"], "campaign-agent", ORDINARY_FULL_REQUESTS[0] + CONFIRMED_BRIEF, None)
    asyncio.run(service.execute(task))
    saved = product.task(task["id"], "tenant-a", member["id"])
    trace = store.run_trace(saved["run_id"], "tenant-a")["payload"]
    assert saved["status"] == "completed"
    assert saved["structured_result"] is None
    assert "暂无法生成" in saved["final_response"]
    assert trace["structured_result_status"] == "semantic_guard_failed_after_retry"
    assert trace["total_model_calls_for_structured_result"] == 2
    assert trace["structured_attempt_trace"][-1]["correction_result"] == "FAIL"
    events = product.task_events_since(task["id"], "tenant-a", member["id"], 0)
    visible = [json.loads(event["message"])["text"] for event in events if event["stage"] == "delta"]
    assert visible == [SAFE_FALLBACK]
    assert "工作人员将" not in "".join(visible)
    assert not any("result_rendering" in event["message"] for event in events if event["stage"] == "activity")


def test_rg12_validated_result_posts_to_document_generator(tmp_path, monkeypatch):
    source = plan()
    result = collect(ScriptedProvider([json.dumps(source, ensure_ascii=False)]), full_request())[-1].turn
    assert result.structured_result is not None
    monkeypatch.setattr(main, "document_service", ActivityPlanDocumentService(LocalStorage(tmp_path / "objects")))
    store = POCStore(tmp_path / "auth.db")
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()
    product.create_user("tenant-a", "doc-member@tenant-a.test", main.hash_password("ChangeMe!2026"), "成员", "member")
    member = product.user_by_email("doc-member@tenant-a.test")
    monkeypatch.setattr(main, "product_store", product)
    token = main.sessions.issue(main.UserPrincipal(member["id"], "tenant-a", "member", main.sessions.credential_version(member["password_hash"])))
    client = TestClient(main.app)
    try:
        client.cookies.set("workbench_session", token)
        created = client.post("/api/v1/documents/activity-plan", json={"content": result.structured_result["data"]})
        assert created.status_code == 201
        downloaded = client.get(created.json()["download_url"])
        assert downloaded.status_code == 200
        assert downloaded.content.startswith(b"PK")
    finally:
        client.close()
