"""Stage 1 isolated control-plane tests; no provider calls / production DB."""
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

from app.agent_catalog import CATALOG
from app.agent_catalog_api import catalog_router
from app.agent_productization import AgentCatalogError, AgentProductization, MODEL_CONFIGS, OVERRIDE_SCHEMA
from app.auth import UserPrincipal
from app.product_store import ProductStore
from app.skill_registry import SkillRegistry, SkillRegistryError
from app.store import POCStore
from test_skill_registry import skill_zip


@pytest.fixture
def catalog(tmp_path, approved_bundle):
    store = POCStore(tmp_path / "catalog.db")
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()
    product.create_user("tenant-a", "platform@example.invalid", "unused", "Platform", "enterprise_admin")
    product.create_user("tenant-b", "enterprise@example.invalid", "unused", "Enterprise", "enterprise_admin")
    registry = SkillRegistry(store, tmp_path / "registry", approved_bundle)
    registry.initialize()
    actor = product.user_by_email("platform@example.invalid")["id"]
    registry.grant_platform_admin(actor)
    control = AgentProductization(store)
    control.initialize()
    return SimpleNamespace(store=store, product=product, registry=registry, control=control, actor=actor)


def new_draft(catalog, **fields):
    template = catalog.control.create_template({"slug": "new-agent", "name": "New Agent"}, catalog.actor)
    template = catalog.control.create_version(template["id"], {"persona": "Synthetic test persona", **fields}, catalog.actor)
    return template["id"], template["versions"][0]["id"]


def version(catalog, template_id):
    return catalog.control.detail(template_id)["versions"][0]


def validated_publish(catalog, template_id, version_id):
    catalog.control.validate(template_id, version_id, catalog.actor)
    return catalog.control.publish(template_id, version_id, catalog.actor, "local_test")


def published_skill(catalog):
    with catalog.store.connection() as conn:
        return dict(conn.execute("SELECT skill_id,id AS skill_version_id FROM skill_versions WHERE status='published' ORDER BY id LIMIT 1").fetchone())


def legacy_snapshot(catalog):
    with catalog.store.connection() as conn:
        return {
            "bindings": [dict(r) for r in conn.execute("SELECT * FROM agent_skill_bindings ORDER BY agent_id,skill_id")],
            "versions": [dict(r) for r in conn.execute("SELECT * FROM skill_versions ORDER BY id")],
            "packages": [dict(r) for r in conn.execute("SELECT * FROM skill_packages ORDER BY id")],
            "instances": [dict(r) for r in conn.execute("SELECT * FROM tenant_agent_instances WHERE agent_id IN (?,?,?) ORDER BY tenant_id,agent_id", tuple(CATALOG))],
            "agents": catalog.product.agents("tenant-a"),
        }


def test_schema_expand_only_and_idempotent(catalog):
    before = legacy_snapshot(catalog)
    catalog.control.initialize()
    assert legacy_snapshot(catalog) == before
    with catalog.store.connection() as conn:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"agent_template_versions", "agent_template_version_skills", "tool_capabilities", "agent_template_version_tools", "agent_template_tests"} <= tables
        assert not {"agent_execution_contexts", "conversation_agent_contexts", "task_agent_contexts"} & tables
        assert all(r["definition_source"] == "legacy" for r in conn.execute("SELECT definition_source FROM agent_templates"))
    migration = (Path(__file__).resolve().parents[1] / "migrations/postgres/008_agent_productization_catalog.sql").read_text()
    assert "DROP " not in migration.upper()
    assert "runtime_profile_ref" not in migration
    assert "credits_policy_ref" not in migration


def test_draft_edit_revision_increment_and_concurrent_create(catalog):
    t, v = new_draft(catalog)
    old = version(catalog, t)["configuration_fingerprint"]
    catalog.control.edit_version(t, v, {"name": "Edited"}, catalog.actor)
    assert version(catalog, t)["name"] == "Edited"
    assert version(catalog, t)["configuration_fingerprint"] != old
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: catalog.control.create_version(t, {"from_version_id": v}, catalog.actor), range(4)))
    assert sorted(r["revision"] for r in catalog.control.detail(t)["versions"]) == [1, 2, 3, 4, 5]


@pytest.mark.parametrize("payload", [
    {"model_config_id": "arbitrary"}, {"model_id": "arbitrary"}, {"model_provider_id": "other"},
    {"base_url": "https://example.invalid"}, {"api_key": "synthetic"}, {"environment_variable_name": "KEY"},
    {"runtime_provider": "shell"}, {"credit_cost": 0}, {"credit_cost": True}, {"credit_cost": 1.5},
    {"tenant_override_schema": {"model": {"type": "string"}}},
])
def test_invalid_config_rejected(catalog, payload):
    t, v = new_draft(catalog)
    with pytest.raises(AgentCatalogError):
        catalog.control.edit_version(t, v, payload, catalog.actor)


def test_model_allowlist_public_metadata_only(catalog):
    options = catalog.control.options()
    assert options["model_configs"] == list(MODEL_CONFIGS.values())
    serialized = json.dumps(options).lower()
    assert all(key not in serialized for key in ["api_key", "base_url", "env_name", "token"])
    assert options["execution_enabled"] is False


def test_validation_invalidated_by_edit_and_binding_changes(catalog):
    t, v = new_draft(catalog)
    catalog.control.validate(t, v, catalog.actor)
    assert version(catalog, t)["tests"][0]["status"] == "passed"
    catalog.control.edit_version(t, v, {"persona": "Updated"}, catalog.actor)
    assert all(test["status"] == "invalidated" for test in version(catalog, t)["tests"])
    with pytest.raises(AgentCatalogError, match="validation required"):
        catalog.control.publish(t, v, catalog.actor, "local_test")
    catalog.control.validate(t, v, catalog.actor)
    catalog.control.bind_skills(t, v, [published_skill(catalog)], catalog.actor)
    assert all(test["status"] == "invalidated" for test in version(catalog, t)["tests"])
    catalog.control.validate(t, v, catalog.actor)
    catalog.control.bind_tools(t, v, [{"tool_capability_id": "config_get", "invocation_requirement": "optional"}], catalog.actor)
    assert all(test["status"] == "invalidated" for test in version(catalog, t)["tests"])


def test_unchanged_config_keeps_validation(catalog):
    t, v = new_draft(catalog)
    catalog.control.validate(t, v, catalog.actor)
    catalog.control.edit_version(t, v, {"persona": "Synthetic test persona"}, catalog.actor)
    assert version(catalog, t)["tests"][0]["status"] == "passed"


def test_published_revision_and_bindings_immutable_service_and_db(catalog):
    t, v = new_draft(catalog)
    validated_publish(catalog, t, v)
    assert version(catalog, t)["publication_scope"] == "local_test"
    assert version(catalog, t)["production_ready"] is False
    for action in [lambda: catalog.control.edit_version(t, v, {"name": "Changed"}, catalog.actor),
                   lambda: catalog.control.bind_skills(t, v, [], catalog.actor),
                   lambda: catalog.control.bind_tools(t, v, [], catalog.actor)]:
        with pytest.raises(AgentCatalogError, match="immutable"):
            action()
    for sql in ["UPDATE agent_template_versions SET persona='changed' WHERE id=?",
                "DELETE FROM agent_template_versions WHERE id=?",
                "INSERT INTO agent_template_version_tools VALUES (?,'config_get','optional')"]:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"), catalog.store.connection() as conn:
            conn.execute(sql, (v,))
    catalog.control.deprecate(t, v, catalog.actor)
    with pytest.raises(AgentCatalogError):
        catalog.control.edit_version(t, v, {"name": "changed"}, catalog.actor)


def test_validation_fake_cannot_unlock_production_publish(catalog):
    t, v = new_draft(catalog)
    catalog.control.validate(t, v, catalog.actor)
    with pytest.raises(AgentCatalogError, match="Runtime Test Pending"):
        catalog.control.publish(t, v, catalog.actor)
    catalog.control.environment = "production"
    with pytest.raises(AgentCatalogError, match="Runtime Test Pending"):
        catalog.control.publish(t, v, catalog.actor, "local_test")
    assert version(catalog, t)["status"] == "draft"
    assert all(test["test_type"] == "validation" and test["task_id"] is None for test in version(catalog, t)["tests"])


def test_non_published_skill_and_mismatched_fk_rejected(catalog):
    t, v = new_draft(catalog)
    imported = catalog.registry.import_archive("extra-skill", "1.0.0", "Extra", "Synthetic", skill_zip("extra-skill"), catalog.actor)
    draft = {"skill_id": imported["skill_id"], "skill_version_id": imported["id"]}
    with pytest.raises(AgentCatalogError, match="Published"):
        catalog.control.bind_skills(t, v, [draft], catalog.actor)
    binding = published_skill(catalog)
    wrong = {"skill_id": draft["skill_id"], "skill_version_id": binding["skill_version_id"]}
    with pytest.raises(AgentCatalogError):
        catalog.control.bind_skills(t, v, [wrong], catalog.actor)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"), catalog.store.connection() as conn:
        conn.execute("INSERT INTO agent_template_version_skills VALUES (?,?,?)", (v, wrong["skill_id"], wrong["skill_version_id"]))
    catalog.registry.publish(imported["id"], catalog.actor)
    catalog.registry.deprecate(imported["id"])
    with pytest.raises(AgentCatalogError):
        catalog.control.bind_skills(t, v, [draft], catalog.actor)


@pytest.mark.parametrize("binding", [
    {"tool_capability_id": "asset_get", "invocation_requirement": "optional"},
    {"tool_capability_id": "arbitrary_shell", "invocation_requirement": "required"},
    {"tool_capability_id": "config_get", "invocation_requirement": "denied"},
    {"tool_capability_id": "config_get", "invocation_requirement": "optional", "allowed": True},
    {"tool_capability_id": "config_get", "invocation_requirement": "optional", "server_url": "https://example.invalid"},
])
def test_tool_allowlist(catalog, binding):
    t, v = new_draft(catalog)
    with pytest.raises(AgentCatalogError):
        catalog.control.bind_tools(t, v, [binding], catalog.actor)


def test_image_requirement_gate_and_text_image_denial(catalog):
    t, v = new_draft(catalog, output_policy="image_required")
    catalog.control.validate(t, v, catalog.actor)
    with pytest.raises(AgentCatalogError, match="image_generation"):
        catalog.control.publish(t, v, catalog.actor, "local_test")
    catalog.control.bind_tools(t, v, [{"tool_capability_id": "image_generation", "invocation_requirement": "required"}], catalog.actor)
    catalog.control.validate(t, v, catalog.actor)
    assert version(catalog, t)["tests"][0]["status"] == "passed"
    catalog.control.edit_version(t, v, {"output_policy": "text"}, catalog.actor)
    with pytest.raises(AgentCatalogError, match="text output forbids"):
        catalog.control.publish(t, v, catalog.actor, "local_test")


def test_required_capability_and_persona_validation(catalog):
    t, v = new_draft(catalog, knowledge_requirement="required", persona="")
    catalog.control.validate(t, v, catalog.actor)
    errors = json.loads(version(catalog, t)["tests"][0]["result_json"])["errors"]
    assert "Persona required" in errors
    assert any("knowledge_search" in e for e in errors)


def test_capability_metadata_drift_blocks_validation(catalog):
    t, v = new_draft(catalog)
    catalog.control.bind_tools(t, v, [{"tool_capability_id": "config_get", "invocation_requirement": "optional"}], catalog.actor)
    with catalog.store.connection() as conn:
        conn.execute("UPDATE tool_capabilities SET required_scope='unapproved' WHERE id='config_get'")
    catalog.control.validate(t, v, catalog.actor)
    assert version(catalog, t)["tests"][0]["status"] == "failed"
    with pytest.raises(AgentCatalogError, match="metadata drift"):
        catalog.control.publish(t, v, catalog.actor, "local_test")


def test_instance_and_current_revision_template_identity_fk(catalog):
    t, v = new_draft(catalog)
    other = catalog.control.create_template({"name": "Other", "slug": "other-agent"}, catalog.actor)["id"]
    with pytest.raises(sqlite3.IntegrityError, match="identity mismatch"), catalog.store.connection() as conn:
        conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,agent_template_version_id) VALUES ('tenant-a',?,'configured',?)", (other, v))
    with pytest.raises(sqlite3.IntegrityError, match="identity mismatch"), catalog.store.connection() as conn:
        conn.execute("UPDATE agent_templates SET current_published_version_id=? WHERE id=?", (v, other))


def test_clone_rejects_skill_that_has_since_been_deprecated(catalog):
    t, v = new_draft(catalog)
    extra = catalog.registry.import_archive("extra-skill", "1.0.0", "Extra", "", skill_zip("extra-skill"), catalog.actor)
    catalog.registry.publish(extra["id"], catalog.actor)
    catalog.control.bind_skills(t, v, [{"skill_id": extra["skill_id"], "skill_version_id": extra["id"]}], catalog.actor)
    catalog.registry.deprecate(extra["id"])
    with pytest.raises(AgentCatalogError, match="non-Published"):
        catalog.control.create_version(t, {"from_version_id": v}, catalog.actor)
    assert len(catalog.control.detail(t)["versions"]) == 1


def test_instance_fixed_revision_allowlist_no_runtime_enable(catalog):
    before = legacy_snapshot(catalog)
    t, v = new_draft(catalog)
    validated_publish(catalog, t, v)
    row = catalog.control.configure_instance(t, "tenant-a", v, {"display_name": "Synthetic", "business_context_note": "Synthetic"})
    assert row["status"] == "configured" and row["execution_enabled"] is False
    assert row["agent_template_version_id"] == v
    assert row["instance_id"]
    for key in ["model", "skills", "tools", "credit_cost", "output_policy", "runtime", "required_capabilities"]:
        with pytest.raises(AgentCatalogError, match="Override"):
            catalog.control.configure_instance(t, "tenant-a", v, {key: "changed"})
    catalog.control.create_version(t, {"from_version_id": v}, catalog.actor)
    with catalog.store.connection() as conn:
        assert conn.execute("SELECT agent_template_version_id FROM tenant_agent_instances WHERE agent_id=?", (t,)).fetchone()["agent_template_version_id"] == v
    catalog.control.deprecate(t, v, catalog.actor)
    with pytest.raises(AgentCatalogError, match="Published"):
        catalog.control.configure_instance(t, "tenant-b", v, {})
    assert legacy_snapshot(catalog) == before


def test_catalog_seed_does_not_overwrite_productized_and_legacy_unchanged(catalog):
    before = legacy_snapshot(catalog)
    t, v = new_draft(catalog)
    catalog.control.edit_version(t, v, {"name": "Edited productized"}, catalog.actor)
    catalog.product.initialize()
    assert version(catalog, t)["name"] == "Edited productized"
    assert legacy_snapshot(catalog) == before
    with pytest.raises(AgentCatalogError, match="Legacy"):
        catalog.control.create_version("image-agent", {}, catalog.actor)
    # Defense in depth: even a future explicitly imported identity is guarded.
    with catalog.store.connection() as conn:
        conn.execute("UPDATE agent_templates SET definition_source='productized',name='Controlled import' WHERE id='image-agent'")
        conn.execute("INSERT INTO tenants(id,name,poc_api_key) VALUES ('new-local-tenant','Synthetic','synthetic-unused')")
    catalog.product._seed_agent_catalog()
    with catalog.store.connection() as conn:
        assert conn.execute("SELECT name FROM agent_templates WHERE id='image-agent'").fetchone()["name"] == "Controlled import"
        assert conn.execute("SELECT 1 FROM tenant_agent_instances WHERE tenant_id='new-local-tenant' AND agent_id='image-agent'").fetchone() is None


def test_five_bindings_and_native_integrity_unchanged(catalog):
    before = legacy_snapshot(catalog)
    assert len(before["bindings"]) == 5
    with catalog.store.connection() as conn:
        packages = [dict(r) for r in conn.execute("SELECT * FROM skill_packages")]
    hashes = {p["storage_path"]: hashlib.sha256(Path(p["storage_path"]).read_bytes()).hexdigest() for p in packages}
    t, v = new_draft(catalog)
    catalog.control.bind_skills(t, v, [published_skill(catalog)], catalog.actor)
    validated_publish(catalog, t, v)
    catalog.registry.initialize()
    assert legacy_snapshot(catalog) == before
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == sha for p, sha in hashes.items())
    with pytest.raises(SkillRegistryError):
        catalog.registry.import_archive("poster-design", "1.0.0", "Changed", "", skill_zip("poster-design", "changed"), catalog.actor)


@pytest.fixture
def api(catalog, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "product_store", catalog.product)
    monkeypatch.setattr(main, "skill_registry", catalog.registry)
    app = FastAPI()
    app.include_router(catalog_router(catalog.control, main.require_platform_admin))

    @app.exception_handler(AgentCatalogError)
    async def error(_, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    client = TestClient(app)
    enterprise = catalog.product.user_by_email("enterprise@example.invalid")
    cookies = {"platform": main.sessions.issue(UserPrincipal(catalog.actor, "tenant-a", "enterprise_admin")),
               "enterprise": main.sessions.issue(UserPrincipal(enterprise["id"], "tenant-b", "enterprise_admin"))}
    return client, cookies


def test_platform_admin_api_and_ordinary_enterprise_403(catalog, api):
    client, cookies = api
    assert client.get("/api/v1/platform/agents").status_code == 401
    client.cookies.set("workbench_session", cookies["enterprise"])
    for method, path, body in [("GET", "", None), ("GET", "/options", None), ("POST", "", {"name": "Test", "slug": "test"}),
                               ("GET", "/missing", None), ("POST", "/missing/versions", {}),
                               ("PATCH", "/missing/versions/missing", {}), ("PUT", "/missing/versions/missing/skills", {"bindings": []}),
                               ("PUT", "/missing/versions/missing/tools", {"bindings": []}), ("POST", "/missing/versions/missing/validation", None),
                               ("POST", "/missing/versions/missing/test", None), ("POST", "/missing/versions/missing/publish", {"mode": "local_test"}),
                               ("POST", "/missing/versions/missing/deprecate", None),
                               ("PUT", "/missing/instances/tenant-b", {"agent_template_version_id": "missing"})]:
        assert client.request(method, "/api/v1/platform/agents"+path, json=body).status_code == 403
    client.cookies.set("workbench_session", cookies["platform"])
    response = client.post("/api/v1/platform/agents", json={"name": "API Agent", "slug": "api-agent"})
    assert response.status_code == 201
    tid = response.json()["id"]
    assert client.get(f"/api/v1/platform/agents/{tid}").status_code == 200
    response = client.post(f"/api/v1/platform/agents/{tid}/versions", json={"persona": "Synthetic"})
    assert response.status_code == 201
    vid = response.json()["versions"][0]["id"]
    path = f"/api/v1/platform/agents/{tid}/versions/{vid}"
    assert client.patch(path, json={"credit_cost": 3}).status_code == 200
    assert client.put(path+"/tools", json={"bindings": []}).status_code == 200
    assert client.put(path+"/skills", json={"bindings": [published_skill(catalog)]}).status_code == 200
    assert client.post(path+"/validation").status_code == 200
    assert client.post(path+"/publish", json={"mode": "production"}).status_code == 409
    assert client.post(path+"/publish", json={"mode": "local_test"}).status_code == 200
    assert client.patch(path, json={"name": "Changed"}).status_code == 409


def test_platform_agents_uses_existing_static_entry():
    from app.main import app
    client = TestClient(app)  # No lifespan / external services needed for static route.
    response = client.get("/platform/agents")
    assert response.status_code == 200
    assert response.content == client.get("/platform/skills").content
    assert b"workbench.js" in response.content
