"""Revision-owned Grounding Policy contract; no grounded Runtime is invoked."""
import hashlib
import json
import sqlite3
from uuid import uuid4

import pytest

from app.agent_execution import profile
from app.agent_productization import (
    AgentCatalogError, DEFAULTS, MODEL_CONFIGS, TOOL_CAPABILITIES, canonical,
    effective_grounding_policy, normalize_grounding_policy,
)
from test_agent_execution import execution, context
from test_agent_productization import catalog, new_draft, published_skill, version


ENABLED = {"enabled": True, "mode": "claim_audit_v1", "max_corrections": 1}


def _old_fingerprint(conn, row):
    """The exact pre-013 canonical input, independent of the new implementation."""
    config = {key: row[key] for key in DEFAULTS if key != "grounding_policy"}
    config["tenant_override_schema"] = json.loads(config["tenant_override_schema"])
    config["model_config"] = MODEL_CONFIGS.get(config["model_config_id"])
    config["skills"] = [dict(item) for item in conn.execute(
        "SELECT b.skill_id,b.skill_version_id,v.checksum FROM agent_template_version_skills b "
        "JOIN skill_versions v ON v.id=b.skill_version_id WHERE b.agent_template_version_id=? ORDER BY b.skill_id",
        (row["id"],))]
    config["tools"] = [{**dict(item), "capability": TOOL_CAPABILITIES.get(item["tool_capability_id"])}
                       for item in conn.execute(
                           "SELECT tool_capability_id,invocation_requirement FROM agent_template_version_tools "
                           "WHERE agent_template_version_id=? ORDER BY tool_capability_id", (row["id"],))]
    return hashlib.sha256(canonical(config).encode()).hexdigest()


def _draft_context(c, grounding=None):
    fields = {"grounding_policy": grounding} if grounding is not None else {}
    template_id, revision_id = new_draft(c, **fields)
    c.control.bind_skills(template_id, revision_id, [published_skill(c)], c.actor)
    with c.store.connection() as conn:
        conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id,overrides_json) "
                     "VALUES ('tenant-a',?,'configured',?,?,'{}')", (template_id, str(uuid4()), revision_id))
    task = c.product.create_task("tenant-a", c.actor, template_id, "synthetic context only", None,
                                 _test_revision=revision_id, _test_id=str(uuid4()))
    return template_id, revision_id, task, context(c, task)


def test_g1_absent_is_null_and_effectively_disabled(catalog):
    template_id, revision_id = new_draft(catalog)
    saved = version(catalog, template_id)
    assert saved["grounding_policy"] is None
    assert effective_grounding_policy(saved["grounding_policy"]) == {"enabled": False}
    with catalog.store.connection() as conn:
        row = dict(conn.execute("SELECT * FROM agent_template_versions WHERE id=?", (revision_id,)).fetchone())
        assert row["grounding_policy"] is None
        assert row["configuration_fingerprint"] == _old_fingerprint(conn, row)


def test_g2_valid_enabled_policy_and_admin_read(catalog):
    template_id, revision_id = new_draft(catalog, grounding_policy=ENABLED)
    assert version(catalog, template_id)["grounding_policy"] == ENABLED
    with catalog.store.connection() as conn:
        assert json.loads(conn.execute("SELECT grounding_policy FROM agent_template_versions WHERE id=?", (revision_id,)).fetchone()[0]) == ENABLED


@pytest.mark.parametrize("policy", [
    {"enabled": True, "mode": "unknown", "max_corrections": 1},  # G3
    {"enabled": True, "mode": "claim_audit_v1", "max_corrections": 0},  # G4
    {"enabled": True, "mode": "claim_audit_v1", "max_corrections": 2},  # G5
    {**ENABLED, "skip_audit": True},  # G6
    {**ENABLED, "force_publish": True},
    {**ENABLED, "max_model_calls": 99},
    {**ENABLED, "max_corrections": True},  # G7: bool is not int
    {**ENABLED, "enabled": "true"},
    {**ENABLED, "enabled": 1},
    {**ENABLED, "mode": 1},
    "not-an-object",
    {"enabled": False, "mode": "claim_audit_v1"},
])
def test_g3_g7_invalid_policy_rejected(catalog, policy):
    template_id, revision_id = new_draft(catalog)
    with pytest.raises(AgentCatalogError, match="grounding_policy"):
        catalog.control.edit_version(template_id, revision_id, {"grounding_policy": policy}, catalog.actor)


def test_g8_tenant_override_cannot_change_grounding(catalog):
    template_id, revision_id = new_draft(catalog)
    catalog.control.validate(template_id, revision_id, catalog.actor)
    catalog.control.publish(template_id, revision_id, catalog.actor, "local_test")
    with pytest.raises(AgentCatalogError, match="Override"):
        catalog.control.configure_instance(template_id, "tenant-a", revision_id, {"grounding_policy": ENABLED})


def test_f1_f2_historical_fingerprint_and_null_remain_identical(catalog):
    template_id, revision_id = new_draft(catalog)
    catalog.control.bind_skills(template_id, revision_id, [published_skill(catalog)], catalog.actor)
    with catalog.store.connection() as conn:
        before = dict(conn.execute("SELECT * FROM agent_template_versions WHERE id=?", (revision_id,)).fetchone())
        expected = _old_fingerprint(conn, before)
        assert before["configuration_fingerprint"] == expected
    catalog.control.initialize()  # idempotent local 013-equivalent migration
    with catalog.store.connection() as conn:
        after = dict(conn.execute("SELECT * FROM agent_template_versions WHERE id=?", (revision_id,)).fetchone())
        assert after["grounding_policy"] is None
        assert after["configuration_fingerprint"] == expected == catalog.control._fingerprint(conn, after)
    assert version(catalog, template_id)["validation_summary"]["status"] == "pending"


def test_f3_f4_enabled_changes_fingerprint_but_key_order_does_not(catalog):
    template_id, revision_id = new_draft(catalog)
    original = version(catalog, template_id)["configuration_fingerprint"]
    reversed_keys = {key: ENABLED[key] for key in reversed(ENABLED)}
    catalog.control.edit_version(template_id, revision_id, {"grounding_policy": reversed_keys}, catalog.actor)
    enabled_hash = version(catalog, template_id)["configuration_fingerprint"]
    assert enabled_hash != original
    catalog.control.edit_version(template_id, revision_id, {"grounding_policy": ENABLED}, catalog.actor)
    assert version(catalog, template_id)["configuration_fingerprint"] == enabled_hash
    catalog.control.edit_version(template_id, revision_id, {"grounding_policy": {"enabled": False}}, catalog.actor)
    assert version(catalog, template_id)["configuration_fingerprint"] == original


def test_f5_f6_published_revision_requires_new_revision(catalog):
    template_id, revision_id = new_draft(catalog)
    catalog.control.validate(template_id, revision_id, catalog.actor)
    catalog.control.publish(template_id, revision_id, catalog.actor, "local_test")
    with pytest.raises(AgentCatalogError):
        catalog.control.edit_version(template_id, revision_id, {"grounding_policy": ENABLED}, catalog.actor)
    with pytest.raises(sqlite3.IntegrityError), catalog.store.connection() as conn:
        conn.execute("UPDATE agent_template_versions SET grounding_policy=? WHERE id=?", (canonical(ENABLED), revision_id))
    with pytest.raises(sqlite3.IntegrityError), catalog.store.connection() as conn:
        conn.execute("UPDATE agent_template_versions SET status='deprecated',deprecated_at=CURRENT_TIMESTAMP,grounding_policy=? WHERE id=?",
                     (canonical(ENABLED), revision_id))
    catalog.control.create_version(template_id, {"from_version_id": revision_id, "grounding_policy": ENABLED}, catalog.actor)
    versions = catalog.control.detail(template_id)["versions"]
    assert versions[0]["grounding_policy"] == ENABLED and versions[1]["grounding_policy"] is None
    assert versions[0]["configuration_fingerprint"] != versions[1]["configuration_fingerprint"]


@pytest.mark.parametrize("enabled", [False, True])
def test_e1_e3_snapshot_from_draft_revision_without_runtime(execution, enabled):
    c = execution
    template_id, revision_id, task, snapshot = _draft_context(c, ENABLED if enabled else None)
    policy = json.loads(snapshot["tool_policy_snapshot"])
    assert policy["grounding"] == (ENABLED if enabled else {"enabled": False})
    assert profile(snapshot).grounding_policy == policy["grounding"]
    assert snapshot["agent_template_version_id"] == revision_id
    assert snapshot["configuration_fingerprint"] == version(c, template_id)["configuration_fingerprint"]
    assert snapshot["id"] == context(c, task)["id"]
    assert c.product.task_for_worker(task["id"])["status"] == "queued"


def test_e4_later_revision_does_not_change_old_task_snapshot(execution):
    c = execution
    template_id, revision_id, task, snapshot = _draft_context(c)
    c.control.validate(template_id, revision_id, c.actor)
    c.control.publish(template_id, revision_id, c.actor, "local_test")
    c.control.create_version(template_id, {"from_version_id": revision_id, "grounding_policy": ENABLED}, c.actor)
    new_id = version(c, template_id)["id"]
    c.control.validate(template_id, new_id, c.actor)
    c.control.publish(template_id, new_id, c.actor, "local_test")
    assert context(c, task) == snapshot
    assert json.loads(snapshot["tool_policy_snapshot"])["grounding"] == {"enabled": False}
    assert version(c, template_id)["grounding_policy"] == ENABLED


def test_e5_e6_request_and_tenant_cannot_override(execution):
    c = execution
    template_id, revision_id, task, snapshot = _draft_context(c, ENABLED)
    with pytest.raises(TypeError):
        c.product.create_task("tenant-a", c.actor, template_id, "x", None,
                              grounding_policy={"enabled": False})
    with pytest.raises(AgentCatalogError):
        c.control.configure_instance(template_id, "tenant-a", revision_id,
                                     {"grounding_policy": {"enabled": False}})
    assert json.loads(context(c, task)["tool_policy_snapshot"])["grounding"] == ENABLED


def test_historical_context_without_key_resolves_disabled(execution):
    _, _, _, snapshot = _draft_context(execution)
    historical = dict(snapshot)
    policy = json.loads(historical["tool_policy_snapshot"])
    del policy["grounding"]
    historical["tool_policy_snapshot"] = canonical(policy)
    assert profile(historical).grounding_policy == {"enabled": False}
    assert effective_grounding_policy(None) == {"enabled": False}
    assert normalize_grounding_policy({"enabled": False}) is None
