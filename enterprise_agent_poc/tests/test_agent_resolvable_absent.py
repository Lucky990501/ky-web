"""Release abort contract on the marked, isolated PostgreSQL fixture."""
import uuid
import asyncio
from types import SimpleNamespace

import pytest

from app.agent_productization import AgentCatalogError, canonical
from app.agent_release_provenance import AgentReleaseProvenance
from app.agent_execution import ExecutionResolver
from app.agent_reference import resolve_agent_reference
from app.agent_runtime_test import AgentRuntimeTest
from app.runtime.codex_provider import CodexRuntimeProvider
from test_agent_productization_postgres import pg_catalog
from test_agent_productization import published_skill


def provision(c, *, slug="release-rollback-agent", publish=False):
    release = AgentReleaseProvenance(c.control)
    operation_id = str(uuid.uuid4())
    release.begin(operation_id=operation_id, release_identity="fixture-candidate",
                  source_identity="a" * 40, manifest_identity="b" * 64,
                  agent_slug=slug, actor=c.actor)
    template_id = release.create_template(operation_id, {
        "name": "Release Rollback Agent", "slug": slug, "description": "Fixture",
        "icon": "sparkles", "category": "Fixture"}, c.actor)["id"]
    revision_id = release.create_revision(operation_id, template_id, {"persona": "Fixture persona"}, c.actor)["versions"][0]["id"]
    with c.store.connection() as conn:
        fingerprint = conn.execute("SELECT configuration_fingerprint FROM agent_template_versions WHERE id=?", (revision_id,)).fetchone()["configuration_fingerprint"]
    if publish:
        release.seal_identity(operation_id, template_id, revision_id, fingerprint)
        c.control.validate(template_id, revision_id, c.actor)
        c.control.publish(template_id, revision_id, c.actor, "local_test", release_operation_id=operation_id)
    return SimpleNamespace(release=release, operation_id=operation_id,
                           template_id=template_id, revision_id=revision_id, fingerprint=fingerprint, slug=slug)


def abort(p):
    return p.release.abort_resolvable_absent(p.operation_id, p.template_id, p.revision_id, p.fingerprint)


def test_a1_draft_resolvable_absent(pg_catalog):
    p = provision(pg_catalog)
    assert abort(p) == {"status": "aborted", "resolution": "RESOLVABLE_ABSENT"}
    assert abort(p)["status"] == "already_aborted"
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT lifecycle_status,current_published_version_id FROM agent_templates WHERE id=?", (p.template_id,)).fetchone() == {"lifecycle_status": "deprecated", "current_published_version_id": None}
        assert conn.execute("SELECT status FROM agent_template_versions WHERE id=?", (p.revision_id,)).fetchone()["status"] == "deprecated"


def test_a2_a3_a10_a12_published_retained_audited_idempotent_and_unresolvable(pg_catalog):
    p = provision(pg_catalog, publish=True)
    assert abort(p)["resolution"] == "RESOLVABLE_ABSENT"
    assert abort(p)["status"] == "already_aborted"
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT status,configuration_fingerprint FROM agent_template_versions WHERE id=?", (p.revision_id,)).fetchone() == {"status": "deprecated", "configuration_fingerprint": p.fingerprint}
        assert conn.execute("SELECT status FROM agent_release_operations WHERE operation_id=?", (p.operation_id,)).fetchone()["status"] == "aborted"
        assert conn.execute("SELECT COUNT(*) AS n FROM agent_release_artifacts WHERE operation_id=?", (p.operation_id,)).fetchone()["n"] == 3
        assert conn.execute("SELECT COUNT(*) AS n FROM agent_release_events WHERE operation_id=?", (p.operation_id,)).fetchone()["n"] >= 6
        with pytest.raises(LookupError):
            resolve_agent_reference(conn, p.slug, postgres=True)
        with pytest.raises(Exception):
            conn.execute("DELETE FROM agent_template_versions WHERE id=?", (p.revision_id,))


def test_a4_a5_a6_wrong_identity_blocked(pg_catalog):
    p = provision(pg_catalog, publish=True)
    wrong = provision(pg_catalog, slug="other-release-agent")
    for values in ((wrong.operation_id, p.template_id, p.revision_id, p.fingerprint),
                   (p.operation_id, wrong.template_id, p.revision_id, p.fingerprint),
                   (p.operation_id, p.template_id, wrong.revision_id, p.fingerprint),
                   (p.operation_id, p.template_id, p.revision_id, "0" * 64)):
        with pytest.raises(AgentCatalogError, match="MANUAL_RECOVERY"):
            p.release.abort_resolvable_absent(*values)
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT status FROM agent_release_operations WHERE operation_id=?", (p.operation_id,)).fetchone()["status"] == "published"


def test_release_owned_publish_requires_operation_and_seal_freezes_draft(pg_catalog):
    p = provision(pg_catalog)
    p.release.seal_identity(p.operation_id, p.template_id, p.revision_id, p.fingerprint)
    pg_catalog.control.validate(p.template_id, p.revision_id, pg_catalog.actor)
    with pytest.raises(AgentCatalogError, match="sealed"):
        pg_catalog.control.edit_version(p.template_id, p.revision_id, {"persona": "changed"}, pg_catalog.actor)
    with pytest.raises(AgentCatalogError, match="MANUAL_RECOVERY"):
        pg_catalog.control.publish(p.template_id, p.revision_id, pg_catalog.actor, "local_test")
    assert pg_catalog.control.publish(p.template_id, p.revision_id, pg_catalog.actor,
                                      "local_test", release_operation_id=p.operation_id)


def test_a6_skill_package_identity_drift_blocks(pg_catalog):
    p = provision(pg_catalog)
    binding = published_skill(pg_catalog)
    pg_catalog.control.bind_skills(p.template_id, p.revision_id, [binding], pg_catalog.actor)
    with pg_catalog.store.connection() as conn:
        fingerprint = conn.execute("SELECT configuration_fingerprint FROM agent_template_versions WHERE id=?", (p.revision_id,)).fetchone()["configuration_fingerprint"]
    p.release.seal_identity(p.operation_id, p.template_id, p.revision_id, fingerprint)
    with pg_catalog.store.connection() as conn:
        conn.execute("UPDATE skill_packages SET sha256=? WHERE skill_version_id=?", ("0" * 64, binding["skill_version_id"]))
    with pytest.raises(AgentCatalogError, match="MANUAL_RECOVERY"):
        p.release.abort_resolvable_absent(p.operation_id, p.template_id, p.revision_id, fingerprint)


def test_unknown_artifact_or_external_instance_blocks(pg_catalog):
    p = provision(pg_catalog, publish=True)
    with pg_catalog.store.connection() as conn:
        p.release._artifact(conn, p.operation_id, "skill_binding", str(uuid.uuid4()), {})
    with pytest.raises(AgentCatalogError, match="MANUAL_RECOVERY"):
        abort(p)
    other = provision(pg_catalog, slug="external-instance-agent", publish=True)
    with pg_catalog.store.connection() as conn:
        conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id,overrides_json) VALUES ('tenant-a',?,'configured',?,?,'{}')", (other.template_id, str(uuid.uuid4()), other.revision_id))
    with pytest.raises(AgentCatalogError, match="MANUAL_RECOVERY"):
        abort(other)


def test_a7_real_task_blocks_without_erasing_history(pg_catalog):
    p = provision(pg_catalog, publish=True)
    task_id = str(uuid.uuid4())
    with pg_catalog.store.connection() as conn:
        conn.execute("INSERT INTO tasks(id,tenant_id,user_id,agent_id,input_text,status) VALUES (?,'tenant-a',?,?,'business','completed')", (task_id, pg_catalog.actor, p.template_id))
    with pytest.raises(AgentCatalogError, match="MANUAL_RECOVERY"):
        abort(p)
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone()
        assert conn.execute("SELECT status FROM agent_release_operations WHERE operation_id=?", (p.operation_id,)).fetchone()["status"] == "published"


def test_a7_real_conversation_without_task_blocks(pg_catalog):
    p = provision(pg_catalog, publish=True)
    conversation_id = str(uuid.uuid4())
    with pg_catalog.store.connection() as conn:
        conn.execute("INSERT INTO conversations(id,tenant_id,agent_id,runtime_profile_id,runtime_thread_id,runtime_version) VALUES (?,'tenant-a',?,'fixture','business-thread','fixture')", (conversation_id, p.template_id))
    with pytest.raises(AgentCatalogError, match="MANUAL_RECOVERY"):
        abort(p)


def test_a8_owned_terminal_runtime_validation_is_retired_not_deleted(pg_catalog):
    p = provision(pg_catalog)
    p.release.seal_identity(p.operation_id, p.template_id, p.revision_id, p.fingerprint)
    pg_catalog.control.validate(p.template_id, p.revision_id, pg_catalog.actor)
    pg_catalog.registry.grant_platform_admin(pg_catalog.actor)
    settings = SimpleNamespace(environment="development", model_provider_id="deepseek",
                               model_id="deepseek-v4-pro", reasoning_effort="high")
    resolver = ExecutionResolver(pg_catalog.store, pg_catalog.registry, pg_catalog.control, settings, "tenant-a")
    pg_catalog.product.execution_resolver = resolver
    pg_catalog.control.execution_resolver = resolver
    tasks = SimpleNamespace(_agents=SimpleNamespace(_runtime=CodexRuntimeProvider(None)))
    tester = AgentRuntimeTest(resolver, pg_catalog.product, tasks)
    tester.isolation_guard = lambda: None
    jobs = []
    tester.enqueue = jobs.append
    queued = asyncio.run(tester.run(p.template_id, p.revision_id, pg_catalog.actor,
                                   p.fingerprint, release_operation_id=p.operation_id))
    test_id, task = queued["runtime_test_id"], pg_catalog.product.task_for_worker(queued["task_id"])
    assert jobs == [task["id"]]
    with pg_catalog.store.connection() as conn:
        context = conn.execute("SELECT c.id,c.runtime_profile_id FROM task_agent_contexts m JOIN agent_execution_contexts c ON c.id=m.context_id WHERE m.task_id=?", (task["id"],)).fetchone()
        conversation_id = str(uuid.uuid4())
        conn.execute("INSERT INTO conversations(id,tenant_id,agent_id,runtime_profile_id,runtime_thread_id,runtime_version) VALUES (?,'tenant-a',?,?,?,'fixture')", (conversation_id, p.template_id, context["runtime_profile_id"], "test-thread"))
        conn.execute("INSERT INTO conversation_agent_contexts(conversation_id,context_id) VALUES (?,?)", (conversation_id, context["id"]))
        conn.execute("INSERT INTO conversation_owners(conversation_id,user_id,title) VALUES (?,?,'Runtime Test')", (conversation_id, pg_catalog.actor))
        conn.execute("INSERT INTO messages(id,conversation_id,role,content) VALUES (?,?,'user','test')", (f"task:{task['id']}:user", conversation_id))
    pg_catalog.product.set_task(task["id"], "tenant-a", "failed", "test_failed", "Synthetic terminal test", conversation_id=conversation_id)
    assert any(item["id"] == conversation_id for item in pg_catalog.product.conversations("tenant-a", pg_catalog.actor))
    with pg_catalog.store.connection() as conn:
        conn.execute("UPDATE agent_template_tests SET status='failed' WHERE id=?", (test_id,))
        assert conn.execute("SELECT 1 FROM agent_release_artifacts WHERE operation_id=? AND artifact_type='runtime_validation' AND artifact_id=?", (p.operation_id, task["id"])).fetchone()
    assert abort(p)["status"] == "aborted"
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT status FROM agent_template_tests WHERE id=?", (test_id,)).fetchone()["status"] == "invalidated"
        assert conn.execute("SELECT 1 FROM tasks WHERE id=?", (task["id"],)).fetchone()
        assert conn.execute("SELECT 1 FROM agent_execution_contexts WHERE agent_id=?", (p.template_id,)).fetchone()
    assert pg_catalog.product.task(task["id"], "tenant-a", pg_catalog.actor) is None
    assert pg_catalog.product.conversation_detail("tenant-a", pg_catalog.actor, conversation_id) is None
    assert all(item["id"] != conversation_id for item in pg_catalog.product.conversations("tenant-a", pg_catalog.actor))


def test_a2_production_scope_published_revision_with_owned_validation_retained(pg_catalog):
    p = provision(pg_catalog)
    p.release.seal_identity(p.operation_id, p.template_id, p.revision_id, p.fingerprint)
    pg_catalog.control.validate(p.template_id, p.revision_id, pg_catalog.actor)
    pg_catalog.registry.grant_platform_admin(pg_catalog.actor)
    settings = SimpleNamespace(environment="development", model_provider_id="deepseek",
                               model_id="deepseek-v4-pro", reasoning_effort="high")
    resolver = ExecutionResolver(pg_catalog.store, pg_catalog.registry, pg_catalog.control, settings, "tenant-a")
    pg_catalog.product.execution_resolver = resolver
    pg_catalog.control.execution_resolver = resolver
    tester = AgentRuntimeTest(resolver, pg_catalog.product,
                              SimpleNamespace(_agents=SimpleNamespace(_runtime=CodexRuntimeProvider(None))))
    tester.isolation_guard = lambda: None
    tester.enqueue = lambda task_id: None
    queued = asyncio.run(tester.run(p.template_id, p.revision_id, pg_catalog.actor,
                                   p.fingerprint, release_operation_id=p.operation_id))
    task_id, test_id = queued["task_id"], queued["runtime_test_id"]
    conversation_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
    with pg_catalog.store.connection() as conn:
        context = conn.execute("SELECT c.id,c.runtime_profile_id FROM task_agent_contexts m JOIN agent_execution_contexts c ON c.id=m.context_id WHERE m.task_id=?", (task_id,)).fetchone()
        conn.execute("INSERT INTO conversations(id,tenant_id,agent_id,runtime_profile_id,runtime_thread_id,runtime_version) VALUES (?,'tenant-a',?,?,?,'fixture')", (conversation_id, p.template_id, context["runtime_profile_id"], "test-thread"))
        conn.execute("INSERT INTO conversation_agent_contexts(conversation_id,context_id) VALUES (?,?)", (conversation_id, context["id"]))
        conn.execute("INSERT INTO conversation_owners(conversation_id,user_id,title) VALUES (?,?,'Runtime Test')", (conversation_id, pg_catalog.actor))
        trace = {"execution_context_id": context["id"], "runtime_completed": True,
                 "required_tool_calls_completed": True, "final_response_received": True,
                 "final_response_persisted": True, "artifact_required": False}
        conn.execute("INSERT INTO run_traces(run_id,conversation_id,tenant_id,agent_id,status,payload) VALUES (?,?,'tenant-a',?,'completed',?)", (run_id, conversation_id, p.template_id, canonical(trace)))
    pg_catalog.product.set_task(task_id, "tenant-a", "completed", "complete", "Synthetic Runtime Test", run_id=run_id, conversation_id=conversation_id, response="ok")
    with pg_catalog.store.connection() as conn:
        conn.execute("UPDATE agent_template_tests SET status='passed',result_json=? WHERE id=?", (canonical({"execution_chain": "codex-runtime-persisted", "skill_discovery_passed": True}), test_id))
    pg_catalog.control.publish(p.template_id, p.revision_id, pg_catalog.actor, "production", release_operation_id=p.operation_id)
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT status,publication_scope FROM agent_template_versions WHERE id=?", (p.revision_id,)).fetchone() == {"status": "published", "publication_scope": "production"}
        mapped = {row["artifact_id"] for row in conn.execute("SELECT artifact_id FROM agent_release_artifacts WHERE operation_id=? AND artifact_type='runtime_validation'", (p.operation_id,))}
        actual = {row["id"] for row in conn.execute("SELECT id FROM tasks WHERE agent_id=?", (p.template_id,))}
        assert mapped == actual == {task_id}
    assert abort(p)["status"] == "aborted"
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT status,publication_scope,configuration_fingerprint FROM agent_template_versions WHERE id=?", (p.revision_id,)).fetchone() == {"status": "deprecated", "publication_scope": "production", "configuration_fingerprint": p.fingerprint}
        assert conn.execute("SELECT status FROM agent_template_tests WHERE id=?", (test_id,)).fetchone()["status"] == "invalidated"


def test_a9_commit_point_blocks(pg_catalog):
    p = provision(pg_catalog, publish=True)
    assert p.release.commit_provision(p.operation_id, p.template_id, p.revision_id, p.fingerprint)["status"] == "committed"
    assert p.release.commit_provision(p.operation_id, p.template_id, p.revision_id, p.fingerprint)["status"] == "already_committed"
    with pytest.raises(AgentCatalogError, match="MANUAL_RECOVERY"):
        abort(p)


def test_a11_a13_a14_unrelated_agent_and_read_paths(pg_catalog):
    before = pg_catalog.product.agents("tenant-a")
    p = provision(pg_catalog, publish=True)
    p.release.catalog.configure_instance(p.template_id, "tenant-a", p.revision_id, {}, release_operation_id=p.operation_id)
    with pg_catalog.store.connection() as conn:
        conn.execute("UPDATE tenant_agent_instances SET status='enabled' WHERE agent_id=?", (p.template_id,))
    pg_catalog.product.execution_resolver = SimpleNamespace(
        _runtime_passed=lambda conn, version: True,
        _ready=lambda conn, tenant, version: None,
        _skills=lambda conn, revision: ({}, []),
    )
    assert p.template_id in {agent["id"] for agent in pg_catalog.product.agents("tenant-a")}
    assert abort(p)["status"] == "aborted"
    assert pg_catalog.product.agents("tenant-a") == before
    with pytest.raises(LookupError):
        pg_catalog.product.resolve_agent_reference(p.slug)
    resolver = ExecutionResolver(pg_catalog.store, pg_catalog.registry, pg_catalog.control,
                                 SimpleNamespace(environment="development"), "tenant-a")
    pg_catalog.control.execution_resolver = resolver
    pg_catalog.product.execution_resolver = resolver
    with pg_catalog.store.connection() as conn:
        with pytest.raises(LookupError):
            resolver.resolve(conn, "tenant-a", pg_catalog.actor, p.template_id)
        assert conn.execute("SELECT status FROM tenant_agent_instances WHERE agent_id=?", (p.template_id,)).fetchone()["status"] == "disabled"
    with pytest.raises(LookupError):
        pg_catalog.product.create_task("tenant-a", pg_catalog.actor, p.template_id, "business", None)
    with pytest.raises(AgentCatalogError):
        pg_catalog.control.set_instance_status(p.template_id, "tenant-a", "enabled")
