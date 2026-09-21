from __future__ import annotations

import json
from pathlib import Path

from app.agent_catalog import CATALOG
from app import bundled_skills


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "skill_packages" / "event-campaign-plan" / "1.0.1"


def test_event_campaign_plan_is_a_locked_native_package_bound_to_campaign_agent():
    entries = bundled_skills.validate_bundle(ROOT / "skill_packages")
    entry = next(item for item in entries if item["skill_slug"] == "event-campaign-plan" and item["version"] == "1.0.1")
    assert entry["bootstrap_default"] == ["campaign-agent"]
    assert entry["builder_policy"] == bundled_skills.BUILDER_POLICY
    assert not entry["legacy_artifact"]
    original = next(item for item in entries if item["skill_slug"] == "event-campaign-plan" and item["version"] == "1.0.0")
    assert original["bootstrap_default"] == []
    assert CATALOG["campaign-agent"].skill_manifest == {
        "event-campaign-plan": "1.0.1", "event-copywriting": "1.0.0"
    }
    assert "campaign-planning" not in CATALOG["campaign-agent"].skill_manifest


def test_full_plan_contract_has_required_sections_and_structured_fields():
    text = (PACKAGE / "SKILL.md").read_text(encoding="utf-8")
    for value in ("活动主题", "活动时间", "活动地点", "活动对象", "活动宣发途径", "活动说明", "活动邀约文案", "【待确认】"):
        assert value in text
    for field in ("document_title", "activity_theme", "activity_time", "activity_location", "target_audience", "promotion_channels", "activity_items", "invitation_copy", "pending_items"):
        assert f'"{field}"' in text
    assert "| 环节 | 名称 | 说明 | 示意图需求 |" in text


def test_routing_and_optional_tool_policy_cover_fixed_evaluation_cases():
    instructions = CATALOG["campaign-agent"].instructions
    for value in ("完整社区活动", "event-campaign-plan", "event-copywriting", "活动标题", "enterprise_config_get", "knowledge_search", "asset_search"):
        assert value in instructions
    assert "仅在任务涉及具体企业或品牌约束时调用 enterprise_config_get" in instructions
    assert "仅描述“示意图需求”时不得机械调用 asset_search" in instructions
    assert "FACT QUERY BUDGET" in instructions
    assert "resolved_fact_targets" in instructions
    assert "对同一个 FACT_TARGET 默认最多执行一次" in instructions
    assert "无论找到支持证据还是未找到支持证据" in instructions
    assert "不得换同义词、换句式、为了确认或为了提高把握再次搜索" in instructions
    assert "这不是整轮只能搜索一次的机械上限" in instructions
    assert "当前企业资料中未找到该权益依据，需进一步确认" in instructions
    assert "不等于已确认企业绝对不存在" in instructions
    assert "必须先调用 enterprise_config_get，再调用 knowledge_search 和 asset_search" not in instructions


def test_skill_has_minimum_evidence_and_time_grounding_guardrails():
    text = (PACKAGE / "SKILL.md").read_text(encoding="utf-8")
    assert "Minimum Sufficient Evidence" in text
    assert "不用等价或近似 Query 重复检索" in text
    assert "教师节前后，具体日期【待确认】" in text
    assert "不得根据系统当前年份自行补成" in text
