"""Isolated PostgreSQL coverage for Phase B stage and exact abort."""
import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts import release_agent_productization as phase_b
from scripts import release_verify
from app.agent_execution import ExecutionResolver
from app.agent_productization import canonical
from app.agent_runtime_test import AgentRuntimeTest
from app.runtime.codex_provider import CodexRuntimeProvider
from test_agent_productization_postgres import pg_catalog


ROOT = Path(__file__).resolve().parents[1]


class ClosedPolicy:
    def status(self):
        return {"policy_enabled": False, "policy_tenant_count": 0,
                "policy_slug_count": 0, "runtime_test_tenant_configured": False}


@pytest.fixture(autouse=True)
def historical014_inventory(tmp_path, monkeypatch):
    """This module proves frozen historical Phase B, not current015 release."""
    from scripts import migrate
    historical = tmp_path / 'historical-phase-b-schema014'
    historical.mkdir()
    files = migrate.migration_files()[:14]
    assert len(files) == 14 and files[-1].name == '014_agent_release_provenance.sql'
    for path in files:
        shutil.copy2(path, historical / path.name)
    # Autouse executes before the imported pg_catalog fixture applies migrations.
    monkeypatch.setattr(migrate, 'MIGRATIONS', historical)
    yield


@pytest.fixture
def transition(pg_catalog, approved_bundle, monkeypatch):
    c = pg_catalog
    with c.store.connection() as conn:
        assert conn.execute('SELECT MAX(version) AS version FROM schema_migrations').fetchone()['version'] == '014'
        assert conn.execute("SELECT to_regclass('public.chat_image_attachments') AS name").fetchone()['name'] is None
    c.registry.grant_platform_admin(c.actor)
    declaration = json.loads((ROOT / "deploy/phase_b_agent_productization.json").read_text())
    declaration["runtime_test"]["tenant"] = "tenant-a"
    declaration["runtime_test"]["actor"] = c.actor
    # The separate manifest tests enforce the actual Production-approved
    # literals. This fixture exercises only the control-plane state machine.
    monkeypatch.setattr(phase_b, "validate_manifest_contract", lambda _: None)
    manifest = {"release_id": "isolated-phase-b", "source_commit": "a" * 40,
                "agent_productization_transition": declaration}
    return phase_b.Transition(manifest, control=c.control, registry=c.registry,
                              store=c.store, bundle_root=approved_bundle,
                              rollout=ClosedPolicy()), c


def test_phase_b_stage_is_release_owned_and_abort_resolvably_absent(transition):
    t, c = transition
    before = release_verify.bindings(c.registry)
    assert t.preflight()["status"] == "preflight_passed"
    staged = t.stage()
    assert staged["status"] == "staged"
    assert staged["operation_id"] == t.operation_id
    assert t.identity()["revision_id"] == staged["revision_id"]
    assert release_verify.phase_b_binding_projection(
        c.registry, t.manifest, release_verify.bindings(c.registry), "state",
    ) == before
    with pytest.raises(phase_b.ProductizationBlocked, match="AGENT_NOT_ABSENT"):
        t.preflight()
    assert t.abort() == {"status": "aborted", "resolution": "RESOLVABLE_ABSENT"}
    assert release_verify.phase_b_binding_projection(
        c.registry, t.manifest, release_verify.bindings(c.registry), "restored",
    ) == before
    with c.store.connection() as conn:
        row = conn.execute("SELECT status FROM agent_release_operations WHERE operation_id=?",
                           (t.operation_id,)).fetchone()
        assert row["status"] == "aborted"


def test_phase_b_published_enabled_then_abort_restores_absent(transition):
    t, c = transition
    staged = t.stage()
    settings = SimpleNamespace(environment="development", model_provider_id="deepseek",
                               model_id="deepseek-v4-pro", reasoning_effort="high")
    resolver = ExecutionResolver(c.store, c.registry, c.control, settings, "tenant-a")
    c.product.execution_resolver = resolver
    c.control.execution_resolver = resolver
    tester = AgentRuntimeTest(
        resolver, c.product,
        SimpleNamespace(_agents=SimpleNamespace(_runtime=CodexRuntimeProvider(None))),
    )
    tester.isolation_guard = lambda: None
    tester.enqueue = lambda _task_id: None
    scope = t.scope
    queued = asyncio.run(tester.run_release(
        release_operation_id=t.operation_id, revision_id=staged["revision_id"],
        fingerprint=staged["configuration_fingerprint"], tenant_id="tenant-a",
        agent_slug=phase_b.SLUG, actor_id=c.actor,
        release_identity=t.manifest["release_id"],
        source_identity=t.manifest["source_commit"],
        manifest_identity=phase_b.digest(t.manifest), runtime_test=scope,
    ))
    task_id, test_id = queued["task_id"], queued["runtime_test_id"]
    conversation_id, run_id = str(uuid4()), str(uuid4())
    with c.store.connection() as conn:
        context = conn.execute(
            "SELECT c.id,c.runtime_profile_id FROM task_agent_contexts m "
            "JOIN agent_execution_contexts c ON c.id=m.context_id WHERE m.task_id=?",
            (task_id,),
        ).fetchone()
        conn.execute(
            "INSERT INTO conversations(id,tenant_id,agent_id,runtime_profile_id,runtime_thread_id,runtime_version) "
            "VALUES (?,'tenant-a',?,?,?,'fixture')",
            (conversation_id, staged["agent_id"], context["runtime_profile_id"], "test-thread"),
        )
        conn.execute("INSERT INTO conversation_agent_contexts(conversation_id,context_id) VALUES (?,?)",
                     (conversation_id, context["id"]))
        conn.execute("INSERT INTO conversation_owners(conversation_id,user_id,title) VALUES (?,?,'Runtime Test')",
                     (conversation_id, c.actor))
        trace = {"execution_context_id": context["id"], "runtime_completed": True,
                 "required_tool_calls_completed": True, "final_response_received": True,
                 "final_response_persisted": True, "artifact_required": False}
        conn.execute(
            "INSERT INTO run_traces(run_id,conversation_id,tenant_id,agent_id,status,payload) "
            "VALUES (?,?,'tenant-a',?,'completed',?)",
            (run_id, conversation_id, staged["agent_id"], canonical(trace)),
        )
    c.product.set_task(task_id, "tenant-a", "completed", "complete", "fixture",
                       run_id=run_id, conversation_id=conversation_id, response="ok")
    with c.store.connection() as conn:
        conn.execute("UPDATE agent_template_tests SET status='passed',result_json=? WHERE id=?",
                     (canonical({"execution_chain": "codex-runtime-persisted",
                                 "skill_discovery_passed": True}), test_id))
        conn.execute(
            "INSERT INTO credit_transactions(id,tenant_id,user_id,task_id,amount,reason) "
            "VALUES (?,'tenant-a',?,?,?,'wechat-official-account-writing')",
            ("task:" + task_id + ":charge", c.actor, task_id, -5),
        )
    assert t.publish()["status"] == "published_disabled"
    assert t.enable()["status"] == "enabled"
    assert t.verify()["status"] == "verified"
    assert t.abort() == {"status": "aborted", "resolution": "RESOLVABLE_ABSENT"}
