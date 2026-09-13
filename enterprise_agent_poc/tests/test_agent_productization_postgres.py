"""Real PostgreSQL 16 integration, restricted to a marked local temp cluster.

Set STAGE1_POSTGRES_ROOT to an explicitly provisioned ky-web-stage1-postgres.*
directory. This suite never accepts a network DSN or production credentials.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
import json
import os
import shutil
import uuid

import psycopg
from psycopg import sql
import pytest

from app.agent_productization import AgentCatalogError, AgentProductization
from app.product_store import ProductStore
from app.skill_registry import SkillRegistry
from app.store import POCStore
from scripts import migrate
from test_agent_productization import new_draft, published_skill, validated_publish, version


pytestmark = pytest.mark.skipif(not os.environ.get("STAGE1_POSTGRES_ROOT"), reason="isolated PostgreSQL cluster not provided")


@pytest.fixture
def pg_catalog(tmp_path, approved_bundle, monkeypatch):
    root = Path(os.environ["STAGE1_POSTGRES_ROOT"]).resolve()
    assert root.parent == Path("/private/tmp") and root.name.startswith("ky-web-stage1-postgres.")
    assert root.stat().st_uid == os.getuid()
    assert (root / "stage1-isolated.marker").read_text().strip() == "ky-web-stage1-local-only"
    socket = root / "socket"
    assert socket.stat().st_mode & 0o077 == 0
    port = int(os.environ.get("STAGE1_POSTGRES_PORT", "54329"))
    admin_args = {"dbname": "postgres", "host": str(socket), "port": port, "user": "stage1_fixture"}
    name = "stage1_" + uuid.uuid4().hex
    with psycopg.connect(**admin_args, autocommit=True) as admin:
        assert 160000 <= int(admin.execute("SHOW server_version_num").fetchone()[0]) < 170000
        assert Path(admin.execute("SHOW data_directory").fetchone()[0]).resolve() == root / "cluster"
        assert admin.execute("SHOW listen_addresses").fetchone()[0] == ""
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    query = urlencode({"host": str(socket), "port": port, "user": "stage1_fixture"})
    store = POCStore(f"postgresql:///{name}?{query}")
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    for path in migrate.migration_files()[:7]:
        shutil.copy(path, baseline / path.name)
    with monkeypatch.context() as patch:
        patch.setattr(migrate, "MIGRATIONS", baseline)
        assert migrate.up(store) == 0
    store.seed_demo_data()  # Code-defined synthetic data, never production rows.
    product = ProductStore(store)
    product.initialize()
    product.create_user("tenant-a", "fixture@example.invalid", "unused", "Synthetic", "enterprise_admin")
    actor = product.user_by_email("fixture@example.invalid")["id"]
    registry = SkillRegistry(store, tmp_path / "registry", approved_bundle)
    registry.initialize()
    with store.connection() as conn:
        old_templates = [dict(r) for r in conn.execute("SELECT * FROM agent_templates ORDER BY id").fetchall()]
        old_instances = [dict(r) for r in conn.execute("SELECT * FROM tenant_agent_instances ORDER BY tenant_id,agent_id").fetchall()]
        old_bindings = [dict(r) for r in conn.execute("SELECT * FROM agent_skill_bindings ORDER BY agent_id,skill_id").fetchall()]
        old_skills = [dict(r) for r in conn.execute("SELECT * FROM skills ORDER BY id").fetchall()]
        old_packages = [dict(r) for r in conn.execute("SELECT * FROM skill_packages ORDER BY id").fetchall()]
    assert migrate.up(store) == 0  # Real runner applies expand-only 008 and 009.
    control = AgentProductization(store)
    return SimpleNamespace(store=store, product=product, registry=registry, control=control, actor=actor,
                           old_templates=old_templates, old_instances=old_instances, old_bindings=old_bindings,
                           old_skills=old_skills, old_packages=old_packages)


def test_postgres_migration_order_status_and_schema(pg_catalog, capsys):
    assert migrate.status(pg_catalog.store) == 0
    status = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert [r["version"] for r in status["migrations"]] == [f"{i:03}" for i in range(1, 11)]
    assert status["pending"] == status["checksum_mismatch"] == 0
    assert all(r["status"] == "applied" for r in status["migrations"])
    with pg_catalog.store.connection() as conn:
        tables = {r["table_name"] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()}
        assert {"agent_template_versions", "agent_template_version_skills", "tool_capabilities", "agent_template_version_tools", "agent_template_tests"} <= tables
        assert {"agent_execution_contexts", "conversation_agent_contexts", "task_agent_contexts"} <= tables
        columns = {r["column_name"] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='agent_templates'").fetchall()}
        assert {"definition_source", "category", "lifecycle_status", "current_published_version_id", "created_at", "updated_at", "published_at", "created_by", "updated_by"} <= columns
        instance_columns = {r["column_name"] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='tenant_agent_instances'").fetchall()}
        assert {"instance_id", "agent_template_version_id", "overrides_json", "created_at", "updated_at"} <= instance_columns
    # Direct 008 repeat and runner repeat: neither duplicate data nor history.
    with pg_catalog.store.connection() as conn:
        conn.execute(migrate.migration_files()[-1].read_text())
    assert migrate.up(pg_catalog.store) == 0
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"] == 10


def test_postgres_all_new_foreign_keys_enforced(pg_catalog):
    t, v = new_draft(pg_catalog)
    binding = published_skill(pg_catalog)
    checks = [
        ("UPDATE agent_templates SET created_by='missing-user' WHERE id=?", (t,)),
        ("UPDATE agent_templates SET updated_by='missing-user' WHERE id=?", (t,)),
        ("UPDATE agent_templates SET current_published_version_id='missing-version' WHERE id=?", (t,)),
        ("UPDATE agent_template_versions SET agent_template_id='missing-template' WHERE id=?", (v,)),
        ("UPDATE agent_template_versions SET created_by='missing-user' WHERE id=?", (v,)),
        ("UPDATE agent_template_versions SET updated_by='missing-user' WHERE id=?", (v,)),
        ("INSERT INTO agent_template_version_skills VALUES (?,'missing-skill','missing-version')", (v,)),
        ("INSERT INTO agent_template_version_skills VALUES ('missing-revision',?,?)", (binding["skill_id"], binding["skill_version_id"])),
        ("INSERT INTO agent_template_version_skills VALUES (?,?,'missing-version')", (v, binding["skill_id"])),
        ("INSERT INTO agent_template_version_tools VALUES (?,'missing-tool','optional')", (v,)),
        ("INSERT INTO agent_template_version_tools VALUES ('missing-revision','config_get','optional')", ()),
        ("INSERT INTO agent_template_tests(id,agent_template_version_id,configuration_fingerprint,test_type,status) VALUES ('missing-parent','missing-version','x','validation','passed')", ()),
        ("INSERT INTO agent_template_tests(id,agent_template_version_id,configuration_fingerprint,test_type,status,task_id) VALUES ('missing-task',?,'x','runtime','passed','missing-task')", (v,)),
        ("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,agent_template_version_id) VALUES ('tenant-a',?,'configured','missing-version')", (t,)),
    ]
    for statement, params in checks:
        with pytest.raises(psycopg.errors.ForeignKeyViolation), pg_catalog.store.connection() as conn:
            conn.execute(statement, params)
    with pg_catalog.store.connection() as conn:
        constraints = conn.execute("SELECT convalidated FROM pg_constraint WHERE contype='f' AND conrelid IN ('agent_templates'::regclass,'agent_template_versions'::regclass,'agent_template_version_skills'::regclass,'agent_template_version_tools'::regclass,'agent_template_tests'::regclass,'tenant_agent_instances'::regclass)").fetchall()
        assert constraints and all(row["convalidated"] for row in constraints)


def test_postgres_skill_and_template_composite_fk(pg_catalog):
    t, v = new_draft(pg_catalog)
    with pg_catalog.store.connection() as conn:
        skills = conn.execute("SELECT skill_id,id FROM skill_versions ORDER BY id LIMIT 2").fetchall()
    with pytest.raises(psycopg.errors.ForeignKeyViolation), pg_catalog.store.connection() as conn:
        conn.execute("INSERT INTO agent_template_version_skills VALUES (?,?,?)", (v, skills[0]["skill_id"], skills[1]["id"]))
    other = pg_catalog.control.create_template({"name": "Other", "slug": "other-agent"}, pg_catalog.actor)["id"]
    for statement, params in [
        ("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,agent_template_version_id) VALUES ('tenant-a',?,'configured',?)", (other, v)),
        ("UPDATE agent_templates SET current_published_version_id=? WHERE id=?", (v, other)),
    ]:
        with pytest.raises(psycopg.errors.ForeignKeyViolation), pg_catalog.store.connection() as conn:
            conn.execute(statement, params)


def test_postgres_published_revision_trigger_and_controlled_deprecate(pg_catalog):
    t, v = new_draft(pg_catalog)
    validated_publish(pg_catalog, t, v)
    for statement in ["UPDATE agent_template_versions SET persona='changed' WHERE id=?",
                      "UPDATE agent_template_versions SET status='draft' WHERE id=?",
                      "DELETE FROM agent_template_versions WHERE id=?"]:
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"), pg_catalog.store.connection() as conn:
            conn.execute(statement, (v,))
    pg_catalog.control.deprecate(t, v, pg_catalog.actor)
    assert version(pg_catalog, t)["status"] == "deprecated"
    with pytest.raises(AgentCatalogError):
        pg_catalog.control.configure_instance(t, "tenant-b", v, {})


def test_postgres_binding_immutable_insert_update_delete_move(pg_catalog):
    t, v = new_draft(pg_catalog)
    binding = published_skill(pg_catalog)
    pg_catalog.control.bind_skills(t, v, [binding], pg_catalog.actor)
    pg_catalog.control.bind_tools(t, v, [{"tool_capability_id": "config_get", "invocation_requirement": "optional"}], pg_catalog.actor)
    validated_publish(pg_catalog, t, v)
    next_revision = pg_catalog.control.create_version(t, {"from_version_id": v}, pg_catalog.actor)["versions"][0]["id"]
    statements = [
        ("INSERT INTO agent_template_version_skills VALUES (?,?,?)", (v, binding["skill_id"], binding["skill_version_id"])),
        ("DELETE FROM agent_template_version_skills WHERE agent_template_version_id=?", (v,)),
        ("UPDATE agent_template_version_skills SET agent_template_version_id=? WHERE agent_template_version_id=?", (next_revision, v)),
        ("UPDATE agent_template_version_skills SET agent_template_version_id=? WHERE agent_template_version_id=?", (v, next_revision)),
        ("INSERT INTO agent_template_version_tools VALUES (?,'knowledge_search','optional')", (v,)),
        ("DELETE FROM agent_template_version_tools WHERE agent_template_version_id=?", (v,)),
        ("UPDATE agent_template_version_tools SET invocation_requirement='required' WHERE agent_template_version_id=?", (v,)),
        ("UPDATE agent_template_version_tools SET agent_template_version_id=? WHERE agent_template_version_id=?", (v, next_revision)),
    ]
    for statement, params in statements:
        with pytest.raises(psycopg.errors.RaiseException, match="immutable"), pg_catalog.store.connection() as conn:
            conn.execute(statement, params)
    assert len(pg_catalog.control.detail(t)["versions"][1]["skills"]) == 1


def test_postgres_revision_unique_and_concurrent_allocation(pg_catalog):
    t, v = new_draft(pg_catalog)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: pg_catalog.control.create_version(t, {"from_version_id": v}, pg_catalog.actor), range(6)))
    revisions = pg_catalog.control.detail(t)["versions"]
    assert sorted(r["revision"] for r in revisions) == list(range(1, 8))
    with pytest.raises(psycopg.errors.UniqueViolation), pg_catalog.store.connection() as conn:
        conn.execute("UPDATE agent_template_versions SET revision=1 WHERE id=?", (revisions[0]["id"],))


def test_postgres_draft_edit_transaction_rollback(pg_catalog, monkeypatch):
    t, v = new_draft(pg_catalog)
    before = version(pg_catalog, t)
    def fail(*args):
        raise RuntimeError("synthetic rollback test")
    monkeypatch.setattr(pg_catalog.control, "_refresh_fingerprint", fail)
    with pytest.raises(RuntimeError):
        pg_catalog.control.edit_version(t, v, {"name": "Rolled back"}, pg_catalog.actor)
    assert version(pg_catalog, t) == before


def test_postgres_legacy_and_five_registry_bindings_unchanged(pg_catalog):
    t, v = new_draft(pg_catalog)
    pg_catalog.control.bind_skills(t, v, [published_skill(pg_catalog)], pg_catalog.actor)
    validated_publish(pg_catalog, t, v)
    pg_catalog.product.initialize()
    pg_catalog.registry.initialize()
    with pg_catalog.store.connection() as conn:
        for old in pg_catalog.old_templates:
            actual = dict(conn.execute("SELECT * FROM agent_templates WHERE id=?", (old["id"],)).fetchone())
            assert {key: actual[key] for key in old} == old
            assert actual["definition_source"] == "legacy"
        for old in pg_catalog.old_instances:
            actual = dict(conn.execute("SELECT * FROM tenant_agent_instances WHERE tenant_id=? AND agent_id=?", (old["tenant_id"], old["agent_id"])).fetchone())
            assert {key: actual[key] for key in old} == old
        assert [dict(r) for r in conn.execute("SELECT * FROM agent_skill_bindings ORDER BY agent_id,skill_id").fetchall()] == pg_catalog.old_bindings
        assert len(pg_catalog.old_bindings) == 5
        assert [dict(r) for r in conn.execute("SELECT * FROM skills ORDER BY id").fetchall()] == pg_catalog.old_skills
        assert [dict(r) for r in conn.execute("SELECT * FROM skill_packages ORDER BY id").fetchall()] == pg_catalog.old_packages
    assert {a["id"] for a in pg_catalog.product.agents("tenant-a")} == {"image-agent", "copywriting-agent", "campaign-agent"}
    with pytest.raises(LookupError):
        pg_catalog.product.create_task("tenant-a", pg_catalog.actor, t, "Must not execute", None)


def test_postgres_existing_agent_http_contract_and_productized_not_runnable(pg_catalog, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.auth import UserPrincipal
    import app.main as main

    t, v = new_draft(pg_catalog)
    validated_publish(pg_catalog, t, v)
    executed = []

    async def fake_execute(task):
        executed.append(task["agent_id"])

    monkeypatch.setattr(main, "product_store", pg_catalog.product)
    monkeypatch.setattr(main, "task_service", SimpleNamespace(execute=fake_execute))
    assert main.settings.task_queue == "local"
    # Use the actual existing handlers without the global application lifespan;
    # only runtime execution is replaced, all persistence stays in this temp DB.
    http_app = FastAPI()
    http_app.router.routes.extend(route for route in main.app.routes
                                 if getattr(route, "path", "") in {"/api/v1/agents", "/api/v1/agents/{agent_id}/runs"})
    with TestClient(http_app, raise_server_exceptions=False) as client:
        client.cookies.set("workbench_session", main.sessions.issue(UserPrincipal(pg_catalog.actor, "tenant-a", "enterprise_admin")))
        response = client.get("/api/v1/agents")
        assert response.status_code == 200
        assert {item["id"] for item in response.json()} == {"image-agent", "copywriting-agent", "campaign-agent"}
        for agent_id in ("image-agent", "copywriting-agent", "campaign-agent"):
            response = client.post(f"/api/v1/agents/{agent_id}/runs", json={"message": "Synthetic HTTP contract only"})
            assert response.status_code == 202
            assert response.json()["agent_id"] == agent_id
        # Stage 2 rejects unknown or unenabled productized IDs with 404.
        unknown = client.post("/api/v1/agents/not-a-catalog-agent/runs", json={"message": "Must not execute"})
        productized = client.post(f"/api/v1/agents/{t}/runs", json={"message": "Must not execute"})
        assert unknown.status_code == productized.status_code == 404
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE agent_id=?", (t,)).fetchone()["n"] == 0
    assert executed == ["image-agent", "copywriting-agent", "campaign-agent"]
