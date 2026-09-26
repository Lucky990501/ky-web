import copy
import hashlib
import io
import json
import tarfile
from pathlib import Path
import zipfile

import pytest

from app.product_store import ProductStore
from app.skill_registry import SkillRegistry
from app.store import POCStore
from scripts import build_release, release_binding_transition, rollback_preflight
from scripts.release_manifest import ManifestContractError, validate_manifest_contract


ROOT = Path(__file__).resolve().parents[1]


def transition_declaration():
    return {
        "schema_version": 1,
        "exact_predecessor": {
            "release_id": "20260920-63112f8",
            "source_commit": "6" * 40,
            "archive_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
        },
        "transitions": [
            {
                "agent_id": "campaign-agent",
                "from_bindings": {
                    "campaign-planning": "1.2.0",
                    "event-copywriting": "1.0.0",
                },
                "to_bindings": {
                    "event-campaign-plan": "1.0.1",
                    "event-copywriting": "1.0.0",
                },
                "required_skill_identities": [
                    {
                        "slug": "event-campaign-plan",
                        "version": "1.0.1",
                        "source_sha256": "c" * 64,
                        "artifact_sha256": "d" * 64,
                    },
                    {
                        "slug": "event-copywriting",
                        "version": "1.0.0",
                        "source_sha256": "e" * 64,
                        "artifact_sha256": "f" * 64,
                    },
                ],
            }
        ],
    }


def phase_a_declarations():
    return (
        json.loads((ROOT / "deploy/forward_migrations_013_014.json").read_text(encoding="utf-8")),
        json.loads((ROOT / "deploy/phase_a_deferred_wechat_skill.json").read_text(encoding="utf-8")),
        json.loads((ROOT / "deploy/phase_a_skill_package_staging.json").read_text(encoding="utf-8")),
    )


def test_phase_a_manifest_pins_order_and_deferred_skill_in_canonical_identity(tmp_path):
    forward, deferred, staging = phase_a_declarations()
    result = {"source_commit": "1" * 40, "archive_sha256": "2" * 64,
              "selected_files": ["enterprise_agent_poc/pyproject.toml"], "files": 1,
              "build_platform": "linux-contract-fixture"}
    path = tmp_path / "phase-a.json"
    manifest = build_release.write_manifest(result, "phase-a", path,
                                            forward_migrations=forward, deferred_skill=deferred,
                                            skill_package_staging=staging)
    assert manifest["forward_migrations"]["migrations"][0]["version"] == "013"
    assert manifest["forward_migrations"]["migrations"][1]["version"] == "014"
    before = rollback_preflight.digest(manifest)
    changed = copy.deepcopy(manifest)
    changed["skill_package_staging"]["package"]["artifact_sha256"] = "0" * 64
    assert rollback_preflight.digest(changed) != before


@pytest.mark.parametrize("change", ("order", "hash", "rollback", "scope"))
def test_phase_a_manifest_rejects_unsafe_plan(change, tmp_path):
    forward, deferred, staging = phase_a_declarations()
    if change == "order":
        forward["migrations"].reverse()
    elif change == "hash":
        forward["migrations"][0]["canonical_sha256"] = "invalid"
    elif change == "rollback":
        forward["rollback_strategy"] = "destructive_down"
    else:
        deferred["registry_action"] = "unreviewed_stage"
    result = {"source_commit": "1" * 40, "archive_sha256": "2" * 64,
              "selected_files": ["enterprise_agent_poc/pyproject.toml"], "files": 1,
              "build_platform": "linux-contract-fixture"}
    with pytest.raises(ManifestContractError):
        build_release.write_manifest(result, "phase-a", tmp_path / "blocked.json",
                                     forward_migrations=forward, deferred_skill=deferred,
                                     skill_package_staging=staging)


def test_phase_a_package_only_manifest_requires_explicit_matching_staging(tmp_path):
    forward, deferred, staging = phase_a_declarations()
    result = {"source_commit": "1" * 40, "archive_sha256": "2" * 64,
              "selected_files": ["enterprise_agent_poc/pyproject.toml"], "files": 1,
              "build_platform": "linux-contract-fixture"}
    with pytest.raises(ManifestContractError, match="skill_package_staging"):
        build_release.write_manifest(result, "phase-a", tmp_path / "missing.json",
                                     forward_migrations=forward, deferred_skill=deferred)
    wrong = copy.deepcopy(staging)
    wrong["package"]["source_sha256"] = "0" * 64
    with pytest.raises(ManifestContractError, match="skill_package_staging"):
        build_release.write_manifest(result, "phase-a", tmp_path / "mismatch.json",
                                     forward_migrations=forward, deferred_skill=deferred,
                                     skill_package_staging=wrong)


def release_fixture(tmp_path, *, transition=None, release_id="fixture-release", commit="1" * 40):
    base = tmp_path / "controlled"
    directory = base / "releases" / release_id
    source = directory / "enterprise_agent_poc"
    source.mkdir(parents=True)
    member = source / "pyproject.toml"
    member.write_text("# immutable fixture\n", encoding="utf-8")
    archive_path = directory / f"{release_id}.tar.gz"
    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit}) as archive:
        archive.add(member, arcname="enterprise_agent_poc/pyproject.toml", recursive=False)
    result = {
        "source_commit": commit,
        "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "selected_files": ["enterprise_agent_poc/pyproject.toml"],
        "files": 1,
        "build_platform": "linux-contract-fixture",
    }
    manifest_path = directory / f"{release_id}.manifest.json"
    manifest = build_release.write_manifest(result, release_id, manifest_path, transition)
    return base, source, manifest_path, manifest, commit


def test_legacy_manifest_remains_supported_by_rollback_identity_gate(tmp_path):
    base, source, _, _, commit = release_fixture(tmp_path)
    resolved, _ = rollback_preflight.release_identity(base, "fixture-release", commit)
    assert resolved == source


def test_builder_transition_manifest_passes_rollback_identity_gate(tmp_path):
    base, source, _, manifest, commit = release_fixture(tmp_path, transition=transition_declaration())
    assert "binding_transition" in manifest
    resolved, checked = rollback_preflight.release_identity(base, "fixture-release", commit)
    assert resolved == source and checked == manifest


def test_unknown_manifest_field_remains_fail_closed(tmp_path):
    base, _, path, manifest, commit = release_fixture(tmp_path, transition=transition_declaration())
    manifest["random_field"] = "forbidden"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(rollback_preflight.RollbackBlocked, match="manifest_fields"):
        rollback_preflight.release_identity(base, "fixture-release", commit)


def test_malformed_binding_transition_is_rejected_by_builder_and_rollback_gate(tmp_path):
    malformed = transition_declaration()
    del malformed["transitions"][0]["from_bindings"]
    result = {
        "source_commit": "1" * 40,
        "archive_sha256": "2" * 64,
        "selected_files": ["enterprise_agent_poc/pyproject.toml"],
        "files": 1,
        "build_platform": "linux-contract-fixture",
    }
    with pytest.raises(ManifestContractError, match="binding_transition"):
        build_release.write_manifest(result, "fixture-release", tmp_path / "blocked.json", malformed)

    base, _, path, manifest, commit = release_fixture(tmp_path / "forced")
    manifest["binding_transition"] = malformed
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(rollback_preflight.RollbackBlocked, match="binding_transition"):
        rollback_preflight.release_identity(base, "fixture-release", commit)


def test_binding_transition_is_inside_canonical_manifest_identity(tmp_path):
    base, _, path, manifest, commit = release_fixture(tmp_path, transition=transition_declaration())
    expected = {
        "archive_sha256": manifest["archive_sha256"],
        "manifest_sha256": rollback_preflight.digest(manifest),
    }
    rollback_preflight.release_identity(base, "fixture-release", commit, expected)

    changed = copy.deepcopy(manifest)
    changed["binding_transition"]["transitions"][0]["from_bindings"]["campaign-planning"] = "1.3.0"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(rollback_preflight.RollbackBlocked, match="approved_manifest_identity"):
        rollback_preflight.release_identity(base, "fixture-release", commit, expected)


def test_transition_contract_requires_target_identity_coverage():
    malformed = transition_declaration()
    malformed["transitions"][0]["required_skill_identities"].pop()
    manifest = {
        "release_id": "fixture-release",
        "source_commit": "1" * 40,
        "archive_sha256": "2" * 64,
        "selected_files": ["enterprise_agent_poc/pyproject.toml"],
        "selected_file_count": 1,
        "build_platform": "linux-contract-fixture",
        "binding_transition": malformed,
    }
    with pytest.raises(ManifestContractError, match="binding_transition"):
        validate_manifest_contract(manifest)


def test_m6r_fresh_producer_consumer_manifest_integration_and_tamper_boundary(tmp_path):
    base, _, predecessor_path, predecessor, _ = release_fixture(
        tmp_path,
        release_id="fixture-predecessor",
        commit="6" * 40,
    )
    predecessor_sha = hashlib.sha256(predecessor_path.read_bytes()).hexdigest()
    packages = release_binding_transition.candidate_packages(ROOT / "skill_packages")
    transition = transition_declaration()
    transition["exact_predecessor"] = {
        "release_id": predecessor["release_id"],
        "source_commit": predecessor["source_commit"],
        "archive_sha256": predecessor["archive_sha256"],
        "manifest_sha256": predecessor_sha,
    }
    for identity in transition["transitions"][0]["required_skill_identities"]:
        entry = packages[(identity["slug"], identity["version"])]
        identity["source_sha256"] = entry["source_identity"]["files"][0]["sha256"]
        identity["artifact_sha256"] = entry["artifact_sha256"]

    base, source, manifest_path, manifest, commit = release_fixture(
        tmp_path,
        transition=transition,
        release_id="fixture-candidate",
        commit="1" * 40,
    )
    raw_manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    canonical_identity = rollback_preflight.digest(manifest)
    expected_identity = {
        "archive_sha256": manifest["archive_sha256"],
        "manifest_sha256": canonical_identity,
    }

    assert set(manifest) == {
        "release_id",
        "source_commit",
        "archive_sha256",
        "selected_files",
        "selected_file_count",
        "build_platform",
        "binding_transition",
    }
    assert raw_manifest_sha and manifest["binding_transition"] == transition
    assert manifest["binding_transition"]["exact_predecessor"] == transition["exact_predecessor"]
    resolved, checked = rollback_preflight.release_identity(base, "fixture-candidate", commit, expected_identity)
    assert resolved == source and checked == manifest

    transitions = release_binding_transition.declaration(manifest, predecessor, predecessor_sha)
    store = POCStore(tmp_path / "registry.db")
    store.seed_demo_data()
    ProductStore(store).initialize()
    registry = SkillRegistry(store, tmp_path / "registry-data", ROOT / "skill_packages")
    registry.initialize()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("campaign-planning/SKILL.md", "# campaign-planning fixture 1.2.0\n")
    imported = registry.import_archive(
        "campaign-planning", "1.2.0", "campaign-planning", "", stream.getvalue(), "fixture"
    )
    registry.publish(imported["id"], "fixture")
    registry.unbind_agent("campaign-agent", "event-campaign-plan")
    registry.bind_agent(
        "campaign-agent", "campaign-planning", "1.2.0", "fixture", allow_new_binding=True
    )
    result = release_binding_transition.preflight(registry, transitions, packages)
    assert result["status"] == "DECLARED_BINDING_TRANSITION_READY"
    assert result["transitions"][0]["from_bindings"] == transition["transitions"][0]["from_bindings"]

    tampered = copy.deepcopy(manifest)
    tampered["binding_transition"]["transitions"][0]["from_bindings"]["campaign-planning"] = "9.9.9"
    manifest_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(rollback_preflight.RollbackBlocked, match="approved_manifest_identity"):
        rollback_preflight.release_identity(base, "fixture-candidate", commit, expected_identity)
