"""SP1-SP12: explicitly declared Phase A Skill package without Agent transition."""
import copy
import io
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from app.product_store import ProductStore
from app.agent_productization import AgentProductization
from app.skill_registry import SkillRegistry, SkillRegistryError
from app.store import POCStore
from scripts import release_binding_transition as transition
from scripts import release_verify
from scripts.release_manifest import ManifestContractError, validate_manifest_contract


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "skill_packages"
SLUG = "wechat-official-account-writing"
VERSION = "1.0.0"


def declarations():
    return (
        json.loads((ROOT / "deploy/phase_a_skill_package_staging.json").read_text(encoding="utf-8")),
        json.loads((ROOT / "deploy/phase_a_deferred_wechat_skill.json").read_text(encoding="utf-8")),
    )


def fixture_registry(tmp_path):
    """A production-like FROM Registry: all predecessor packages, no WeChat."""
    old_bundle = tmp_path / "predecessor-bundle"
    shutil.copytree(BUNDLE, old_bundle)
    manifest_path = old_bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    wechat = next(item for item in manifest["skills"] if item["skill_slug"] == SLUG)
    manifest["skills"] = [item for item in manifest["skills"] if item is not wechat]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(old_bundle / wechat["source_identity"]["path"])
    (old_bundle / wechat["artifact_path"]).unlink()

    store = POCStore(tmp_path / "registry.db")
    store.seed_demo_data()
    ProductStore(store).initialize()
    AgentProductization(store).initialize()
    data_root = tmp_path / "skill-registry"
    predecessor = SkillRegistry(store, data_root, old_bundle)
    predecessor.initialize()
    candidate = SkillRegistry(store, data_root, BUNDLE)
    staging, deferred = declarations()
    manifest = {"skill_package_staging": staging, "deferred_skill": deferred}
    packages = transition.candidate_packages(BUNDLE)
    pinned, entry = transition.package_only_declaration(manifest, packages, BUNDLE)
    return predecessor, candidate, manifest, packages, pinned, entry


def bindings(registry):
    with registry._read_connection() as conn:
        return [tuple(row) for row in conn.execute(
            "SELECT agent_id,skill_id,skill_version_id FROM agent_skill_bindings ORDER BY agent_id,skill_id"
        ).fetchall()]


def counts(registry):
    with registry._read_connection() as conn:
        return (conn.execute("SELECT COUNT(*) AS n FROM skill_versions").fetchone()["n"],
                conn.execute("SELECT COUNT(*) AS n FROM skill_packages").fetchone()["n"])


def conflicting_archive():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(f"{SLUG}/SKILL.md", "# wrong immutable package\n")
    return output.getvalue()


def test_sp1_manifest_accepts_package_staging_without_binding_transition():
    staging, deferred = declarations()
    manifest = {"release_id": "phase-a", "source_commit": "1" * 40,
                "archive_sha256": "2" * 64, "selected_files": ["enterprise_agent_poc/pyproject.toml"],
                "selected_file_count": 1, "build_platform": "Linux-x86_64",
                "deferred_skill": deferred, "skill_package_staging": staging}
    assert validate_manifest_contract(manifest) == manifest
    assert "binding_transition" not in manifest
    without_declaration = copy.deepcopy(manifest)
    del without_declaration["skill_package_staging"]
    with pytest.raises(ManifestContractError):
        validate_manifest_contract(without_declaration)


def test_sp2_absent_package_is_staged_exactly_once_and_bindings_unchanged(tmp_path):
    predecessor, candidate, _, packages, pinned, entry = fixture_registry(tmp_path)
    before = bindings(candidate)
    assert transition.package_only_preflight(candidate, pinned, packages)["registry_pre_state"] == "ABSENT"
    result = transition.stage_candidate_packages(candidate, {(SLUG, VERSION): entry}, BUNDLE)
    assert result["staged"] == [{"slug": SLUG, "version": VERSION}]
    assert transition.package_only_state(candidate, pinned, required=True) == "EXACT"
    assert bindings(candidate) == before
    assert predecessor.verify_bootstrap()["status"] == "ok"


def test_sp3_exact_existing_package_is_idempotent(tmp_path):
    _, candidate, _, packages, pinned, entry = fixture_registry(tmp_path)
    transition.stage_candidate_packages(candidate, {(SLUG, VERSION): entry}, BUNDLE)
    before = counts(candidate)
    assert transition.package_only_preflight(candidate, pinned, packages)["registry_pre_state"] == "EXACT"
    result = transition.stage_candidate_packages(candidate, {(SLUG, VERSION): entry}, BUNDLE)
    assert result["staged"] == [] and result["reused"] == [{"slug": SLUG, "version": VERSION}]
    assert counts(candidate) == before


def test_sp4_existing_wrong_zip_identity_blocks_without_binding_change(tmp_path):
    _, candidate, _, packages, pinned, _ = fixture_registry(tmp_path)
    before = bindings(candidate)
    candidate._upsert_import(SLUG, VERSION, SLUG, "conflict", conflicting_archive(), None, published=True)
    with pytest.raises(transition.TransitionBlocked, match="SKILL_PACKAGE_IDENTITY_CONFLICT"):
        transition.package_only_preflight(candidate, pinned, packages)
    assert bindings(candidate) == before


def test_sp5_wrong_bundled_manifest_identity_blocks_before_stage(tmp_path):
    _, candidate, manifest, packages, _, _ = fixture_registry(tmp_path)
    before = counts(candidate)
    manifest["skill_package_staging"]["package"]["bundled_manifest_sha256"] = "0" * 64
    manifest["deferred_skill"]["bundled_manifest_sha256"] = "0" * 64
    with pytest.raises(transition.TransitionBlocked, match="SKILL_PACKAGE_IDENTITY_CONFLICT"):
        transition.package_only_declaration(manifest, packages, BUNDLE)
    assert counts(candidate) == before


def test_sp6_sp7_staging_does_not_create_agent_binding_or_workspace_visibility(tmp_path):
    _, candidate, _, _, pinned, entry = fixture_registry(tmp_path)
    before = bindings(candidate)
    transition.stage_candidate_packages(candidate, {(SLUG, VERSION): entry}, BUNDLE)
    assert bindings(candidate) == before
    assert release_verify.phase_a_absence(candidate, {"slug": SLUG})["agent"] == "ABSENT"
    product = ProductStore(candidate._store)
    assert all(agent["slug"] != SLUG for agent in product.agents("tenant-a"))
    with pytest.raises(LookupError):
        product.resolve_agent_reference(SLUG)
    assert transition.package_only_state(candidate, pinned, required=True) == "EXACT"


def test_sp8_sp9_predecessor_and_candidate_registry_initialization(tmp_path):
    predecessor, candidate, _, _, pinned, entry = fixture_registry(tmp_path)
    predecessor.initialize()
    with pytest.raises(SkillRegistryError, match="missing bundled version"):
        candidate.initialize()
    transition.stage_candidate_packages(candidate, {(SLUG, VERSION): entry}, BUNDLE)
    predecessor.initialize()
    candidate.initialize()
    assert transition.package_only_state(candidate, pinned, required=True) == "EXACT"


def test_sp10_failure_before_stage_leaves_registry_unchanged(tmp_path, monkeypatch):
    _, candidate, _, packages, pinned, entry = fixture_registry(tmp_path)
    before = counts(candidate), bindings(candidate)
    transition.package_only_preflight(candidate, pinned, packages)
    def fail(*args, **kwargs):
        raise SkillRegistryError("injected pre-stage failure")
    monkeypatch.setattr(candidate, "_upsert_import", fail)
    with pytest.raises(SkillRegistryError, match="injected pre-stage failure"):
        transition.stage_candidate_packages(candidate, {(SLUG, VERSION): entry}, BUNDLE)
    assert (counts(candidate), bindings(candidate)) == before
    assert transition.package_only_state(candidate, pinned) == "ABSENT"


def test_sp11_post_stage_failure_keeps_proven_forward_safe_dormant_package(tmp_path):
    predecessor, candidate, _, _, pinned, entry = fixture_registry(tmp_path)
    before = bindings(candidate)
    transition.stage_candidate_packages(candidate, {(SLUG, VERSION): entry}, BUNDLE)
    # Package-only rollback never deletes a published immutable version.
    assert transition.package_only_state(candidate, pinned) == "EXACT"
    assert release_verify.phase_a_absence(candidate, {"slug": SLUG})["agent"] == "ABSENT"
    predecessor.initialize()
    assert bindings(candidate) == before
    assert all(agent["slug"] != SLUG for agent in ProductStore(candidate._store).agents("tenant-a"))
    assert transition.package_only_state(candidate, pinned, required=True) == "EXACT"


def test_sp12_package_only_declaration_rejects_binding_transition():
    staging, deferred = declarations()
    manifest = {"skill_package_staging": staging, "deferred_skill": deferred,
                "binding_transition": {"agents": []}}
    with pytest.raises(transition.TransitionBlocked, match="package_only_binding_transition_forbidden"):
        transition.package_only_declaration(manifest, transition.candidate_packages(BUNDLE), BUNDLE)
