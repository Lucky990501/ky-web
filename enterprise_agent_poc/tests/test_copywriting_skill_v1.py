"""Offline package/binding guards, not evidence of real Model quality."""
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from app.agent_catalog import CATALOG
from app.agent_productization import AgentCatalogError, AgentProductization
from app.bundled_skills import BUILDER_POLICY, deterministic_zip, sha256, validate_bundle
from app.domain import RuntimeProfile
from app.product_store import ProductStore
from app.skill_registry import NativeSkillArchive, SkillRegistry, SkillRegistryError
from app.skills import SkillDeployment
from app.store import POCStore


ROOT = Path(__file__).resolve().parents[1]
SLUG = "short-video-reality-talk"
VERSION = "1.0.0"


@pytest.fixture
def copywriting_bundle(tmp_path):
    target = tmp_path / "bundle"
    shutil.copytree(ROOT / "skill_packages", target)
    return target


@pytest.fixture(autouse=True)
def offline_isolation(monkeypatch, tmp_path):
    """Also safe under --noconftest for targeted Windows package verification."""
    import socket
    original = POCStore.connection
    def connection(store):
        assert store.database_path is not None, "Network DB forbidden in offline checks"
        assert store.database_path.resolve().is_relative_to(tmp_path.resolve())
        return original(store)
    def network_forbidden(*args, **kwargs):
        raise AssertionError("Offline check attempted a network/Provider call")
    monkeypatch.setattr(POCStore, "connection", connection)
    monkeypatch.setattr(socket.socket, "connect", network_forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", network_forbidden)


@pytest.fixture
def registered(tmp_path, copywriting_bundle):
    store = POCStore(tmp_path / "copywriting.db")
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()
    registry = SkillRegistry(store, tmp_path / "registry", copywriting_bundle)
    registry.initialize()
    return store, registry


def test_native_package_identity_and_deterministic_artifact():
    entry = next(e for e in validate_bundle(ROOT / "skill_packages")
                 if (e["skill_slug"], e["version"]) == (SLUG, VERSION))
    source = (ROOT / "skill_packages" / entry["source_identity"]["path"] / "SKILL.md").read_bytes()
    artifact = (ROOT / "skill_packages" / entry["artifact_path"]).read_bytes()
    assert entry["builder_policy"] == BUILDER_POLICY
    assert entry["bootstrap_default"] == ["copywriting-agent"]
    assert entry["source_identity"]["files"] == [
        {"path": "SKILL.md", "sha256": sha256(source), "git_mode": "100644"}]
    assert artifact == deterministic_zip(SLUG, VERSION, {"SKILL.md": (source, "100644")})
    assert sha256(artifact) == entry["artifact_sha256"]
    assert NativeSkillArchive.inspect(artifact, SLUG)["file_count"] == 1
    with zipfile.ZipFile(ROOT / "skill_packages" / entry["artifact_path"]) as archive:
        assert archive.read(f"{SLUG}/SKILL.md") == source


def test_skill_is_self_contained_and_uses_supported_frontmatter():
    text = (ROOT / "skill_packages" / SLUG / VERSION / "SKILL.md").read_text(encoding="utf-8")
    header = text.split("---", 2)[1]
    assert {line.split(":", 1)[0] for line in header.strip().splitlines()} == {"name", "description"}
    assert f"name: {SLUG}" in header
    assert "wechat-html-draft" not in text
    assert "wechat-official-account-writing" not in text
    assert "三个不同版本" in text and "同一会话" in text


def test_bootstrap_binding_is_existing_copywriting_agent_only(registered):
    store, registry = registered
    assert registry.manifest_for_agent("copywriting-agent") == {SLUG: VERSION}
    assert CATALOG["copywriting-agent"].skill_manifest == {SLUG: VERSION}
    registry.verify_bootstrap()
    with store.connection() as conn:
        agents = [r["id"] for r in conn.execute("SELECT id FROM agent_templates")]
        assert set(agents) == set(CATALOG)
        rows = conn.execute(
            "SELECT v.version,v.status FROM skill_versions v JOIN skills s ON s.id=v.skill_id "
            "WHERE s.slug IN ('marketing-copywriting','social-copywriting')").fetchall()
        assert len(rows) == 2
        assert all((r["version"], r["status"]) == ("1.0.0", "published") for r in rows)


def test_native_profile_deploys_exact_bound_package(registered, tmp_path):
    _, registry = registered
    manifest = registry.manifest_for_agent("copywriting-agent")
    profile = RuntimeProfile.build(tenant_id="tenant-a", agent_id="copywriting-agent",
        model_provider_id="deepseek", model_id="deepseek-v4-pro", reasoning_effort="high",
        skill_manifest=manifest)
    target = tmp_path / "codex-home" / "skills"
    SkillDeployment(registry.published_root).deploy(profile.skill_manifest, target)
    assert {p.name for p in target.iterdir()} == {SLUG}
    assert (target / SLUG / "SKILL.md").read_bytes() == (
        ROOT / "skill_packages" / SLUG / VERSION / "SKILL.md").read_bytes()
    # This is filesystem/profile proof; actual skills/list is a separate live gate.


def test_existing_published_identity_cannot_be_overwritten(registered):
    _, registry = registered
    artifact = (ROOT / "skill_packages" / SLUG / f"{VERSION}.zip").read_bytes()
    with pytest.raises(SkillRegistryError, match="不可覆盖"):
        registry.import_archive(SLUG, VERSION, "文案创作", "copywriting", artifact, "fixture-actor")


def test_agent_keeps_text_only_optional_tool_policy():
    agent = CATALOG["copywriting-agent"]
    assert agent.id == "copywriting-agent" and agent.slug == "copywriting"
    assert agent.credit_cost == 3 and not agent.allows_image_generation
    assert f"${SLUG}" in agent.instructions
    assert "三个有实质差异" in agent.instructions
    assert "仅在" in agent.instructions
    assert "必须先调用" not in agent.instructions
    assert "claim_audit_v1" not in agent.instructions


@pytest.mark.parametrize("agent_id,expected", [
    ("image-agent", {"poster-design": "1.0.0"}),
    ("campaign-agent", {"event-campaign-plan": "1.0.1", "event-copywriting": "1.0.0"}),
])
def test_other_agent_bindings_unchanged(registered, agent_id, expected):
    _, registry = registered
    assert CATALOG[agent_id].skill_manifest == expected
    assert registry.manifest_for_agent(agent_id) == expected


def test_legacy_agent_revision_gate_is_not_bypassed(registered):
    store, _ = registered
    control = AgentProductization(store)
    control.initialize()
    with pytest.raises(AgentCatalogError, match="Legacy Agent is read-only"):
        control.create_version("copywriting-agent", {"persona": "must not bypass"}, "fixture-actor")


def test_live_cases_keep_multi_turn_and_three_styles():
    cases = json.loads((ROOT / "evals/copywriting-skill-v1.json").read_text(encoding="utf-8"))
    assert cases["model"] == {"provider": "deepseek", "model": "deepseek-v4-pro", "reasoning_effort": "high"}
    assert cases["budget"] == {"text_provider_requests": 5, "image_provider_requests": 0}
    assert cases["agent_id"] == "copywriting-agent"
    assert cases["skill"] == {"slug": SLUG, "version": VERSION}
    assert cases["cases"][1]["conversation"] == cases["cases"][0]["conversation"]
    assert cases["cases"][1]["kind"] == "followup"
    assert len(cases["cases"]) == 4
