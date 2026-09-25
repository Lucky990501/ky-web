"""Isolated configuration and package checks; never touches a shared Registry."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from app.agent_catalog import CATALOG
from app.agent_execution import profile
from app.agent_productization import AgentProductization
from app.bundled_skills import validate_bundle
from app.product_store import ProductStore
from app.skill_registry import NativeSkillArchive, SkillRegistry
from app.store import POCStore
from scripts.create_wechat_official_account_agent_v1 import (
    DESCRIPTION, NAME, PERSONA, SKILL_VERSION, SLUG, create_draft,
)
from test_agent_execution import context, execution
from test_agent_productization import catalog


BUNDLE = Path(__file__).resolve().parents[1] / "skill_packages"
EVAL_SET = Path(__file__).resolve().parents[1] / "evals/wechat_official_account_grounding_v1.json"
SIMPLIFIED_EVAL_SET = Path(__file__).resolve().parents[1] / "evals/wechat_official_account_simplified_v1.json"


def _isolated_control(tmp_path):
    store = POCStore(tmp_path / "wechat-agent.db")
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()
    product.create_user("tenant-a", "wechat-admin@example.invalid", "unused", "Admin", "enterprise_admin")
    actor = product.user_by_email("wechat-admin@example.invalid")["id"]
    registry = SkillRegistry(store, tmp_path / "skill-registry", BUNDLE)
    registry.initialize()
    control = AgentProductization(store)
    control.initialize()
    with store.connection() as conn:
        conn.execute(
            "UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,"
            "advanced_at=CURRENT_TIMESTAMP,advanced_by_release_id='isolated-wechat-fixture',"
            "advanced_by_source_commit=?,advance_origin='controlled_advance' "
            "WHERE scope='agent_data_contract'",
            ("f" * 40,),
        )
    return store, registry, control, actor


def test_skill_package_identity_and_native_archive():
    entries = validate_bundle(BUNDLE)
    entry = next(x for x in entries if (x["skill_slug"], x["version"]) == (SLUG, SKILL_VERSION))
    source = (BUNDLE / entry["source_identity"]["path"] / "SKILL.md").read_bytes()
    artifact = (BUNDLE / entry["artifact_path"]).read_bytes()
    assert hashlib.sha256(source).hexdigest() == entry["source_identity"]["files"][0]["sha256"]
    assert hashlib.sha256(artifact).hexdigest() == entry["artifact_sha256"]
    assert entry["bootstrap_default"] == []
    assert NativeSkillArchive.inspect(artifact, SLUG)["sha256"] == entry["artifact_sha256"]


def test_productized_draft_metadata_binding_and_existing_agents_unchanged(tmp_path):
    store, registry, control, actor = _isolated_control(tmp_path)
    before = {agent: registry.manifest_for_agent(agent) for agent in CATALOG}
    draft = create_draft(control, actor)
    version = draft["versions"][0]
    assert draft["slug"] == SLUG
    assert draft["name"] == NAME
    assert draft["description"] == DESCRIPTION
    assert "公众号文章初稿" in draft["description"]
    assert "发布前需人工核对事实" in draft["description"]
    assert not any(claim in draft["description"] for claim in ("可直接发布", "严格保真", "保证事实准确"))
    assert draft["definition_source"] == "productized"
    assert version["status"] == "draft"  # No Production publish or enable.
    assert version["credit_cost"] == 5
    assert version["model_config_id"] == "codex-deepseek-v4-pro-high"
    assert version["persona"] == PERSONA
    assert version["grounding_policy"] is None
    assert version["validation_summary"] == {"status": "passed", "errors": []}
    assert [(item["slug"], item["version"]) for item in version["skills"]] == [(SLUG, SKILL_VERSION)]
    assert {item["tool_capability_id"]: item["invocation_requirement"] for item in version["tools"]} == {
        "config_get": "optional", "knowledge_search": "optional", "asset_search": "optional"
    }
    with store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM agent_template_versions WHERE agent_template_id=?", (draft["id"],)).fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM tenant_agent_instances WHERE agent_id=?", (draft["id"],)).fetchone()["n"] == 0
    assert {agent: registry.manifest_for_agent(agent) for agent in CATALOG} == before


def test_draft_execution_context_keeps_grounding_disabled(execution):
    draft = create_draft(execution.control, execution.actor)
    revision = draft["versions"][0]
    with execution.store.connection() as conn:
        conn.execute(
            "INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id,overrides_json) "
            "VALUES ('tenant-a',?,'configured',?,?,'{}')",
            (draft["id"], str(uuid4()), revision["id"]),
        )
    task = execution.product.create_task(
        "tenant-a", execution.actor, draft["id"], "isolated context check", None,
        _test_revision=revision["id"], _test_id=str(uuid4()),
    )
    snapshot = context(execution, task)
    assert snapshot["agent_template_version_id"] == revision["id"]
    assert snapshot["configuration_fingerprint"] == revision["configuration_fingerprint"]
    assert json.loads(snapshot["tool_policy_snapshot"])["grounding"] == {"enabled": False}
    assert profile(snapshot).grounding_policy == {"enabled": False}
    assert json.loads(snapshot["skill_manifest_snapshot"]) == {SLUG: SKILL_VERSION}


def test_skill_quality_instructions_cover_requested_scenarios():
    skill = (BUNDLE / SLUG / SKILL_VERSION / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---\nname: wechat-official-account-writing\ndescription:")
    assert skill.count("\n---\n") == 1
    for scenario in ("活动报道", "预热", "案例", "行业观点", "知识科普", "课程", "品牌故事", "产品服务"):
        assert scenario in skill
    for guard in ("【待确认】", "标题党", "2–5", "Markdown", "CTA", "导语", "小标题"):
        assert guard in skill
    for case_specific in ("知野智能", "教师节", "读书卡", "亲子共读", "开学一周", "某个客户", "青禾阅读中心", "社区公告模板", "错题记录"):
        assert case_specific not in skill
    assert "5 积分" not in skill  # Points belong to configurable Agent metadata.


def test_lightweight_fact_rules_do_not_enable_strict_audit():
    for rule in ("具体人数", "金额", "客户名称", "日期", "地点", "业绩", "效果数据", "【待确认】"):
        assert rule in PERSONA
    assert "claim_audit_v1" not in PERSONA
    assert "Claim Permission" not in PERSONA
    assert "Fact Ledger" not in PERSONA
    for boundary in ("可以创作观点", "常见", "现场情节", "现有功能", "未来安排"):
        assert boundary in PERSONA


def test_simplified_quality_set_covers_core_and_unseen_requests():
    cases = json.loads(SIMPLIFIED_EVAL_SET.read_text(encoding="utf-8"))["cases"]
    assert [case["id"] for case in cases] == ["V1", "V2", "V3", "V4", "V5", "V6", "V7", "V9", "A", "B", "C", "D"]
    assert all(case["prompt"].strip() and case["check"].strip() for case in cases)


def test_fixed_grounding_evaluation_set_covers_regressions_and_unseen_neighbors():
    cases = json.loads(EVAL_SET.read_text(encoding="utf-8"))["cases"]
    ids = [case["id"] for case in cases]
    assert ids == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "G1", "G2", "G3", "G4", "G5"]
    assert all(case["prompt"].strip() and case["check"].strip() for case in cases)
    assert "交换读书卡" in cases[1]["prompt"]
    assert "时间、地点和报名方式尚未确定" in cases[2]["prompt"]
    assert "没有客户效果数据" in cases[3]["prompt"]
