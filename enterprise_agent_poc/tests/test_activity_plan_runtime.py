from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.activity_plan_runtime import (
    SAFE_FALLBACK, chunk_validated_markdown, is_full_activity_plan, parse_plan, render_markdown, semantic_guard,
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
    return "请给社区教师节设计完整活动方案，包含主题、时间、地点、宣发、执行和邀约"


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


def test_rg10_rg11_message_association_and_sse_compatibility(tmp_path, monkeypatch):
    monkeypatch.setenv("ENTERPRISE_POC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENTERPRISE_POC_DATABASE_URL", f"sqlite:///{tmp_path / 'product.db'}")
    monkeypatch.setenv("ENTERPRISE_POC_OBJECT_STORAGE_DIR", str(tmp_path / "objects"))
    store = POCStore(tmp_path / "product.db")
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()
    product.create_user("tenant-a", "bridge-member@tenant-a.test", main.hash_password("ChangeMe!2026"), "成员", "member")
    member = product.user_by_email("bridge-member@tenant-a.test")
    runtime = ScriptedProvider(["五个标题", json.dumps(plan(), ensure_ascii=False)])
    service = TaskService(product, AgentService(store, runtime, Settings.from_env()))
    first = product.create_task("tenant-a", member["id"], "campaign-agent", "给我想5个社区教师节活动标题", None)
    asyncio.run(service.execute(first))
    first_saved = product.task(first["id"], "tenant-a", member["id"])
    second = product.create_task("tenant-a", member["id"], "campaign-agent", full_request(), first_saved["conversation_id"])
    asyncio.run(service.execute(second))
    second_saved = product.task(second["id"], "tenant-a", member["id"])
    assert first_saved["structured_result"] is None
    assert second_saved["structured_result"]["type"] == "activity_plan"
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
    task = product.create_task("tenant-a", member["id"], "campaign-agent", full_request(), None)
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
