"""One owned validation attempt on the marked, isolated PostgreSQL fixture."""
import asyncio
from types import SimpleNamespace

import pytest

from app.agent_execution import ExecutionResolver
from app.agent_productization import AgentCatalogError
from app.agent_runtime_test import AgentRuntimeTest
from app.runtime.codex_provider import CodexRuntimeProvider
from test_agent_productization import published_skill
from test_agent_productization_postgres import pg_catalog
from test_agent_resolvable_absent import provision


@pytest.fixture
def release_tester(pg_catalog):
    c = pg_catalog
    p = provision(c, slug="release-runtime-agent")
    binding = published_skill(c)
    c.control.bind_skills(p.template_id, p.revision_id, [binding], c.actor)
    with c.store.connection() as conn:
        revision = conn.execute(
            "SELECT configuration_fingerprint FROM agent_template_versions WHERE id=?",
            (p.revision_id,),
        ).fetchone()
        skill = conn.execute(
            "SELECT s.slug,v.version,p.sha256 FROM skill_versions v "
            "JOIN skills s ON s.id=v.skill_id "
            "JOIN skill_packages p ON p.skill_version_id=v.id WHERE v.id=?",
            (binding["skill_version_id"],),
        ).fetchone()
    p.fingerprint = revision["configuration_fingerprint"]
    p.release.seal_identity(p.operation_id, p.template_id, p.revision_id, p.fingerprint)
    c.control.validate(p.template_id, p.revision_id, c.actor)
    c.registry.grant_platform_admin(c.actor)
    settings = SimpleNamespace(environment="development", model_provider_id="deepseek",
                               model_id="deepseek-v4-pro", reasoning_effort="high")
    resolver = ExecutionResolver(c.store, c.registry, c.control, settings, "tenant-a")
    c.product.execution_resolver = resolver
    c.control.execution_resolver = resolver
    tasks = SimpleNamespace(_agents=SimpleNamespace(_runtime=CodexRuntimeProvider(None)))
    tester = AgentRuntimeTest(resolver, c.product, tasks)
    tester.isolation_guard = lambda: None
    queued = []
    tester.enqueue = queued.append
    scope = {
        "required": True, "tenant": "tenant-a", "agent_slug": p.slug,
        "actor": c.actor, "exactly_one_validation_task": True,
        "skill_slug": skill["slug"], "skill_version": skill["version"],
        "skill_package_sha256": skill["sha256"],
        "model_config_id": "codex-deepseek-v4-pro-high", "credit_cost": 1,
    }
    args = dict(release_operation_id=p.operation_id, revision_id=p.revision_id,
                fingerprint=p.fingerprint, tenant_id="tenant-a", agent_slug=p.slug,
                actor_id=c.actor, release_identity="fixture-candidate",
                source_identity="a" * 40, manifest_identity="b" * 64,
                runtime_test=scope)
    return c, p, tester, queued, args


def test_release_owned_one_task_context_and_evidence(release_tester):
    c, p, tester, queued, args = release_tester
    result = asyncio.run(tester.run_release(**args))
    assert queued == [result["task_id"]]
    with c.store.connection() as conn:
        artifact = conn.execute(
            "SELECT identity FROM agent_release_artifacts WHERE operation_id=? "
            "AND artifact_type='runtime_validation' AND artifact_id=?",
            (p.operation_id, result["task_id"]),
        ).fetchone()
        assert artifact and artifact["identity"]["revision_id"] == p.revision_id
        assert artifact["identity"]["fingerprint"] == p.fingerprint
        assert conn.execute(
            "SELECT 1 FROM task_agent_contexts WHERE task_id=?", (result["task_id"],)
        ).fetchone()
        reservation = conn.execute(
            "SELECT evidence FROM agent_release_events WHERE operation_id=? "
            "AND event_type='runtime_validation_reserved'", (p.operation_id,),
        ).fetchone()
        assert reservation["evidence"]["credit_cost"] == 1
        assert reservation["evidence"]["tenant_id"] == "tenant-a"
    with pytest.raises(AgentCatalogError, match="already attempted"):
        asyncio.run(tester.run_release(**args))


def test_rt14_rt16_validation_only_aborts_as_resolvably_absent(release_tester):
    c, p, tester, _, args = release_tester
    result = asyncio.run(tester.run_release(**args))
    c.product.set_task(result["task_id"], "tenant-a", "failed", "fixture_failed", "fixture")
    with c.store.connection() as conn:
        conn.execute("UPDATE agent_template_tests SET status='failed' WHERE id=?",
                     (result["runtime_test_id"],))
    assert p.release.abort_resolvable_absent(
        p.operation_id, p.template_id, p.revision_id, p.fingerprint,
    ) == {"status": "aborted", "resolution": "RESOLVABLE_ABSENT"}
    with c.store.connection() as conn:
        assert conn.execute("SELECT status FROM agent_template_tests WHERE id=?",
                            (result["runtime_test_id"],)).fetchone()["status"] == "invalidated"


def test_rt15_unowned_business_task_blocks_auto_abort(release_tester):
    c, p, tester, _, args = release_tester
    result = asyncio.run(tester.run_release(**args))
    c.product.set_task(result["task_id"], "tenant-a", "failed", "fixture_failed", "fixture")
    with c.store.connection() as conn:
        conn.execute("UPDATE agent_template_tests SET status='failed' WHERE id=?",
                     (result["runtime_test_id"],))
        conn.execute(
            "INSERT INTO tasks(id,tenant_id,user_id,agent_id,input_text,status,stage) "
            "VALUES (?,?,?,?,'ordinary business usage','completed','complete')",
            ("unowned-business-task", "tenant-a", c.actor, p.template_id),
        )
    with pytest.raises(AgentCatalogError, match="MANUAL_RECOVERY_REQUIRED"):
        p.release.abort_resolvable_absent(
            p.operation_id, p.template_id, p.revision_id, p.fingerprint,
        )


@pytest.mark.parametrize("change", [
    {"release_operation_id": None},
    {"release_operation_id": "00000000-0000-0000-0000-000000000000"},
    {"tenant_id": "tenant-b"},
    {"agent_slug": "wrong-agent"},
    {"revision_id": "wrong-revision"},
    {"fingerprint": "0" * 64},
    {"manifest_identity": "0" * 64},
])
def test_wrong_release_scope_blocks_before_task(release_tester, change):
    c, p, tester, queued, args = release_tester
    with pytest.raises(AgentCatalogError):
        asyncio.run(tester.run_release(**(args | change)))
    assert queued == []
    with c.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE agent_id=?", (p.template_id,)).fetchone()["n"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM agent_release_events WHERE operation_id=? "
            "AND event_type='runtime_validation_reserved'", (p.operation_id,),
        ).fetchone()["n"] == 0
