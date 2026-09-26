"""Opt-in isolated real Provider rehearsal for Release-owned WeChat validation.

Only the private Stage 2 Worker loads the non-production credential. This test
never uses Production policy, tenant, actor, registry or database state.
"""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest


def probe(config_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.stage2_isolation import bootstrap, assert_worker_isolated
    config, settings = bootstrap(config_path)
    from app.main import agent_catalog_control as control, product_store as product
    from app.main import store, skill_registry, task_service
    from app.agent_release_provenance import AgentReleaseProvenance
    from scripts.create_wechat_official_account_agent_v1 import (
        SLUG, NAME, DESCRIPTION, PERSONA, SKILL_VERSION,
    )

    def gate():
        assert_worker_isolated(settings, config["root"], config["snapshot"])

    gate()
    tenant = settings.agent_runtime_test_tenant_id
    actor = product.user_by_email("runtime@stage2.test")["id"]
    assert skill_registry.is_platform_admin(actor)
    with store.connection() as conn:
        assert not conn.execute("SELECT 1 FROM agent_templates WHERE slug=?", (SLUG,)).fetchone()
        skill = conn.execute(
            "SELECT s.id AS skill_id,v.id AS skill_version_id,v.checksum,p.sha256 "
            "FROM skills s JOIN skill_versions v ON v.skill_id=s.id "
            "JOIN skill_packages p ON p.skill_version_id=v.id "
            "WHERE s.slug=? AND v.version=? AND v.status='published'",
            (SLUG, SKILL_VERSION),
        ).fetchone()
        assert skill and skill["checksum"] == skill["sha256"]
    operation_id = str(uuid4())
    release = AgentReleaseProvenance(control)
    release.begin(operation_id=operation_id, release_identity="isolated-phase-b-fixture",
                  source_identity="a" * 40, manifest_identity="b" * 64,
                  agent_slug=SLUG, actor=actor)
    template = release.create_template(operation_id, {
        "name": NAME, "slug": SLUG, "description": DESCRIPTION,
        "icon": "file-text", "category": "公众号内容",
    }, actor)
    template_id = template["id"]
    revision = release.create_revision(operation_id, template_id, {
        "persona": PERSONA, "credit_cost": 5,
        "model_config_id": "codex-deepseek-v4-pro-high",
        "knowledge_requirement": "optional", "enterprise_config_requirement": "optional",
        "asset_requirement": "optional", "output_policy": "text",
    }, actor)["versions"][0]
    revision_id = revision["id"]
    control.bind_skills(template_id, revision_id, [{
        "skill_id": skill["skill_id"], "skill_version_id": skill["skill_version_id"],
    }], actor)
    control.bind_tools(template_id, revision_id, [
        {"tool_capability_id": "config_get", "invocation_requirement": "optional"},
        {"tool_capability_id": "knowledge_search", "invocation_requirement": "optional"},
        {"tool_capability_id": "asset_search", "invocation_requirement": "optional"},
    ], actor)
    fingerprint = control.detail(template_id)["versions"][0]["configuration_fingerprint"]
    release.seal_identity(operation_id, template_id, revision_id, fingerprint)
    control.validate(template_id, revision_id, actor)
    tester = control.runtime_tester
    tester.isolation_guard = gate
    scope = {
        "required": True, "tenant": tenant, "agent_slug": SLUG, "actor": actor,
        "exactly_one_validation_task": True, "skill_slug": SLUG,
        "skill_version": SKILL_VERSION, "skill_package_sha256": skill["sha256"],
        "model_config_id": "codex-deepseek-v4-pro-high", "credit_cost": 5,
    }
    queued = asyncio.run(tester.run_release(
        release_operation_id=operation_id, revision_id=revision_id,
        fingerprint=fingerprint, tenant_id=tenant, agent_slug=SLUG, actor_id=actor,
        release_identity="isolated-phase-b-fixture", source_identity="a" * 40,
        manifest_identity="b" * 64, runtime_test=scope,
    ))
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        with store.connection() as conn:
            result = conn.execute(
                "SELECT status FROM agent_template_tests WHERE id=?", (queued["runtime_test_id"],),
            ).fetchone()
        if result["status"] in {"passed", "failed", "invalidated"}:
            break
        time.sleep(0.5)
    assert result["status"] == "passed", "isolated Release-owned Provider validation failed"
    with store.connection() as conn:
        task = conn.execute("SELECT status,conversation_id,run_id FROM tasks WHERE id=?",
                            (queued["task_id"],)).fetchone()
        charge = conn.execute("SELECT amount FROM credit_transactions WHERE task_id=?",
                              (queued["task_id"],)).fetchall()
        owner = conn.execute(
            "SELECT identity FROM agent_release_artifacts WHERE operation_id=? "
            "AND artifact_type='runtime_validation' AND artifact_id=?",
            (operation_id, queued["task_id"]),
        ).fetchone()
        assert task["status"] == "completed" and task["conversation_id"] and task["run_id"]
        assert len(charge) == 1 and charge[0]["amount"] == -5
        assert owner and owner["identity"]["revision_id"] == revision_id
    published = control.publish(template_id, revision_id, actor, "production",
                                release_operation_id=operation_id)
    assert published["current_published_version_id"] == revision_id
    enabled = control.set_instance_status(template_id, tenant, "enabled",
                                          release_operation_id=operation_id)
    assert enabled["status"] == "enabled"
    assert release.abort_resolvable_absent(operation_id, template_id, revision_id, fingerprint) == {
        "status": "aborted", "resolution": "RESOLVABLE_ABSENT",
    }
    gate()
    print("RELEASE_SCOPED_PROVIDER_AND_ABORT_PASS")


def test_isolated_real_provider_release_owned_validation_and_abort():
    config = os.environ.get("STAGE25_REDIS_E2E_CONFIG")
    if not config:
        pytest.skip("Explicit isolated PG/Redis/API/Worker manifest required")
    env = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG", "PYTHONDONTWRITEBYTECODE")
           if k in os.environ}
    result = subprocess.run([sys.executable, __file__, "--probe", config], env=env,
                            capture_output=True, text=True, timeout=210)
    assert result.returncode == 0, "Isolated Release-owned Provider probe failed"
    assert "RELEASE_SCOPED_PROVIDER_AND_ABORT_PASS" in result.stdout


if __name__ == "__main__":
    assert sys.argv[1] == "--probe"
    probe(sys.argv[2])
