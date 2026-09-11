from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.agent_catalog import CATALOG
from app.main import app
from app.product_store import ProductStore
from app.skill_registry import NativeSkillArchive, SkillRegistry, SkillRegistryError
from app.skills import SkillDeployment
from app.store import POCStore
from scripts.grant_platform_admin import grant_platform_admin_access


def skill_zip(slug: str, marker: str = "v1") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{slug}/SKILL.md", f"# {slug}\n\n{marker}\n")
        archive.writestr(f"{slug}/agents/openai.yaml", "name: test\n")
        archive.writestr(f"{slug}/references/guide.md", "# Guide\n")
        archive.writestr(f"{slug}/scripts/check.py", "print('ok')\n")
        archive.writestr(f"{slug}/assets/example.txt", "asset\n")
    return output.getvalue()


def registry_fixture(tmp_path):
    store = POCStore(tmp_path / "registry.db")
    store.seed_demo_data()
    ProductStore(store).initialize()
    bundled = tmp_path / "bundled"
    for agent in CATALOG.values():
        for slug, version in agent.skill_manifest.items():
            source = bundled / slug / version
            source.mkdir(parents=True, exist_ok=True)
            (source / "SKILL.md").write_text(f"# {slug}\n", encoding="utf-8")
    registry = SkillRegistry(store, tmp_path / "data", bundled)
    registry.initialize()
    return registry


def test_platform_admin_grant_dry_run_is_strictly_read_only(tmp_path):
    store = POCStore(tmp_path / "grant.db")
    store.seed_demo_data()
    product_store = ProductStore(store)
    product_store.initialize()
    product_store.create_user("tenant-a", "admin@tenant-a.test", "unused", "Tenant A Admin", "enterprise_admin")
    registry = SkillRegistry(store, tmp_path / "registry", tmp_path / "bundled")

    exit_code, result = grant_platform_admin_access(store, registry, "ADMIN@TENANT-A.TEST", False)

    assert exit_code == 0
    assert result["status"] == "dry_run"
    assert result["tenant_id"] == "tenant-a"
    assert result["current_role"] == "enterprise_admin"
    assert not registry.data_root.exists()
    with store.connection() as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_admins'"
        ).fetchone()
    assert table is None


def test_platform_admin_grant_execute_requires_initialized_registry(tmp_path):
    registry = registry_fixture(tmp_path)
    ProductStore(registry._store).create_user(
        "tenant-a", "admin@tenant-a.test", "unused", "Tenant A Admin", "enterprise_admin"
    )
    exit_code, result = grant_platform_admin_access(registry._store, registry, "admin@tenant-a.test", True)
    assert exit_code == 0
    assert result["status"] == "granted"
    assert registry.is_platform_admin(result["user_id"])


def test_native_skill_zip_is_preserved_published_and_bound_to_runtime(tmp_path):
    registry = registry_fixture(tmp_path)
    imported = registry.import_archive("poster-design", "1.1.0", "Poster Design", "upgrade", skill_zip("poster-design", "v1.1"), "user-1")
    assert imported["status"] == "draft"
    assert imported["inspection"]["files"] == [
        "poster-design/SKILL.md",
        "poster-design/agents/openai.yaml",
        "poster-design/assets/example.txt",
        "poster-design/references/guide.md",
        "poster-design/scripts/check.py",
    ]
    assert registry.test_version(imported["id"])["status"] == "passed"
    assert registry.publish(imported["id"], "user-1")["status"] == "published"
    binding = registry.bind_agent("image-agent", "poster-design", "1.1.0", "user-1")
    assert binding["skill_manifest"] == {"poster-design": "1.1.0"}

    target = tmp_path / "codex-home" / "skills"
    SkillDeployment(registry.published_root).deploy(binding["skill_manifest"], target)
    assert "v1.1" in (target / "poster-design" / "SKILL.md").read_text(encoding="utf-8")
    assert (target / "poster-design" / "agents" / "openai.yaml").is_file()


def test_published_version_is_immutable_and_rollback_changes_profile_manifest(tmp_path):
    registry = registry_fixture(tmp_path)
    imported = registry.import_archive("poster-design", "1.1.0", "Poster Design", "upgrade", skill_zip("poster-design", "v1.1"), "user-1")
    registry.publish(imported["id"], "user-1")
    with pytest.raises(SkillRegistryError, match="不可覆盖"):
        registry.import_archive("poster-design", "1.1.0", "Poster Design", "changed", skill_zip("poster-design", "mutated"), "user-1")
    registry.bind_agent("image-agent", "poster-design", "1.1.0", "user-1")
    assert registry.manifest_for_agent("image-agent") == {"poster-design": "1.1.0"}
    registry.bind_agent("image-agent", "poster-design", "1.0.0", "user-1")
    assert registry.manifest_for_agent("image-agent") == {"poster-design": "1.0.0"}


def test_three_agent_types_discover_only_their_published_bundled_skills(tmp_path):
    registry = registry_fixture(tmp_path)
    for agent_id in ("image-agent", "copywriting-agent", "campaign-agent"):
        manifest = registry.manifest_for_agent(agent_id)
        assert manifest == CATALOG[agent_id].skill_manifest
        target = tmp_path / "runtime" / agent_id / "skills"
        SkillDeployment(registry.published_root).deploy(manifest, target)
        assert {item.name for item in target.iterdir()} == set(manifest)
        assert all((target / slug / "SKILL.md").is_file() for slug in manifest)


def test_zip_traversal_and_wrong_root_are_rejected():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("safe-skill/SKILL.md", "# Safe")
        archive.writestr("safe-skill/../escape.py", "bad")
    with pytest.raises(SkillRegistryError, match="不安全路径"):
        NativeSkillArchive.inspect(output.getvalue(), "safe-skill")
    with pytest.raises(SkillRegistryError, match="根目录"):
        NativeSkillArchive.inspect(skill_zip("other-skill"), "safe-skill")

    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("safe-skill/SKILL.md", "# Safe")
        archive.writestr("safe-skill/skill.md", "# Collision")
    with pytest.raises(SkillRegistryError, match="重复文件"):
        NativeSkillArchive.inspect(duplicate.getvalue(), "safe-skill")


def test_runtime_deployment_rejects_manifest_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="不安全路径"):
        SkillDeployment(tmp_path / "registry").deploy({"../escape": "1.0.0"}, tmp_path / "runtime" / "skills")


def test_platform_skill_api_is_hidden_from_enterprise_users_and_accepts_native_zip():
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "admin@tenant-b.test", "password": "ChangeMe!2026"})
        assert client.get("/api/v1/platform/skills").status_code == 403
        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login", json={"account": "admin@tenant-a.test", "password": "ChangeMe!2026"})
        assert client.get("/api/v1/me").json()["is_platform_admin"] is True
        response = client.post(
            "/api/v1/platform/skills/import",
            data={"slug": "registry-api-test", "version": "1.0.0", "name": "Registry API Test", "description": "native"},
            files={"file": ("registry-api-test.zip", skill_zip("registry-api-test"), "application/zip")},
        )
        assert response.status_code == 201
        assert response.json()["status"] == "draft"
        assert "storage_path" not in response.json()
        assert any(item["slug"] == "registry-api-test" for item in client.get("/api/v1/platform/skills").json())
