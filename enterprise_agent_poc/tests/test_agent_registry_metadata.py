"""Agent display metadata is read from the existing registry, not UI names."""

import json

from app.agent_catalog import CATALOG
from test_agent_execution import execution, make_ready
from test_agent_productization import catalog


def test_legacy_registry_exposes_stable_identity_and_display_metadata(catalog):
    rows = {row["id"]: row for row in catalog.product.agents("tenant-a")}
    assert set(CATALOG).issubset(rows)
    for agent_id, definition in CATALOG.items():
        row = rows[agent_id]
        assert row["id"] == definition.id
        assert row["slug"] == definition.slug
        assert row["name"] == definition.name
        assert row["description"] == definition.description
        assert row["icon"] == definition.icon
        assert row["category"]
        assert row["enabled"] is True


def test_productized_registry_rename_preserves_id_slug_and_route(execution):
    template_id, _ = make_ready(
        execution,
        name="公众号编写智能体",
        description="从资料生成适合发布的文章。",
        icon="newspaper",
        category="content",
    )
    before = next(row for row in execution.product.agents("tenant-a") if row["id"] == template_id)
    assert before["name"] == "公众号编写智能体"
    assert before["icon"] == "newspaper"
    assert before["description"] == "从资料生成适合发布的文章。"
    assert before["category"] == "content"
    with execution.store.connection() as conn:
        conn.execute(
            "UPDATE tenant_agent_instances SET overrides_json=? WHERE tenant_id=? AND agent_id=?",
            (json.dumps({"display_name": "公众号运营助手"}), "tenant-a", template_id),
        )
    after = next(row for row in execution.product.agents("tenant-a") if row["id"] == template_id)
    assert after["name"] == "公众号运营助手"
    assert after["id"] == before["id"]
    assert after["slug"] == before["slug"]
    assert after["conversation_path"] == before["conversation_path"]
    assert execution.product.resolve_agent_reference(before["slug"]) == template_id
