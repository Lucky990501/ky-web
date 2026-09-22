import io
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from app.product_store import ProductStore
from app.skill_registry import SkillRegistry, SkillRegistryError
from app.store import POCStore
from scripts import release_binding_transition as transition


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "skill_packages"
FROM = {"campaign-planning": "1.2.0", "event-copywriting": "1.0.0"}
TO = {"event-campaign-plan": "1.0.1", "event-copywriting": "1.0.0"}


def package_bytes(slug: str, text: str) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(f"{slug}/SKILL.md", text)
    return stream.getvalue()


def registry_bindings(registry: SkillRegistry) -> dict[str, str]:
    with registry._read_connection() as conn:
        return transition.registry_bindings(conn, "campaign-agent")


def registry_manifest(registry: SkillRegistry) -> dict[str, str]:
    with registry._read_connection() as conn:
        return transition.registry_manifest(conn, "campaign-agent")


def production_like_registry(tmp_path):
    predecessor_bundle = tmp_path / "predecessor-skill-packages"
    shutil.copytree(BUNDLE, predecessor_bundle)
    manifest_path = predecessor_bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    retained = {
        (item["skill_slug"], item["version"])
        for item in manifest["skills"]
        if item["legacy_artifact"]
    }
    removed = [item for item in manifest["skills"] if not item["legacy_artifact"]]
    manifest["skills"] = [item for item in manifest["skills"] if item["legacy_artifact"]]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in removed:
        shutil.rmtree(predecessor_bundle / item["source_identity"]["path"])
        (predecessor_bundle / item["artifact_path"]).unlink()

    store = POCStore(tmp_path / "registry.db")
    store.seed_demo_data()
    ProductStore(store).initialize()
    data_root = tmp_path / "skill-registry"
    predecessor_registry = SkillRegistry(store, data_root, predecessor_bundle)
    predecessor_registry.initialize()
    imported = predecessor_registry.import_archive(
        "campaign-planning",
        "1.2.0",
        "campaign-planning",
        "",
        package_bytes("campaign-planning", "# campaign-planning production fixture 1.2.0\n"),
        "fixture",
    )
    predecessor_registry.publish(imported["id"], "fixture")
    predecessor_registry.bind_agent(
        "campaign-agent", "campaign-planning", "1.2.0", "fixture", allow_new_binding=True
    )
    assert registry_bindings(predecessor_registry) == FROM
    assert registry_manifest(predecessor_registry) == FROM

    registry = SkillRegistry(store, data_root, BUNDLE)
    packages = transition.candidate_packages(BUNDLE)
    required = []
    for slug, version in TO.items():
        entry = packages[(slug, version)]
        required.append(
            {
                "slug": slug,
                "version": version,
                "source_sha256": entry["source_identity"]["files"][0]["sha256"],
                "artifact_sha256": entry["artifact_sha256"],
            }
        )
    transitions = [
        {
            "agent_id": "campaign-agent",
            "from": FROM,
            "to": TO,
            "required": required,
        }
    ]
    return registry, predecessor_registry, packages, transitions


def package_row(registry: SkillRegistry, slug: str, version: str):
    with registry._read_connection() as conn:
        return conn.execute(
            "SELECT v.id,v.skill_id,v.version,v.status,v.checksum,s.slug,"
            "p.storage_path,p.sha256 AS package_sha256,p.size_bytes "
            "FROM skill_versions v JOIN skills s ON s.id=v.skill_id "
            "JOIN skill_packages p ON p.skill_version_id=v.id "
            "WHERE s.slug=? AND v.version=?",
            (slug, version),
        ).fetchone()


def registry_counts(registry: SkillRegistry) -> tuple[int, int]:
    with registry._read_connection() as conn:
        return (
            conn.execute("SELECT COUNT(*) AS n FROM skill_versions").fetchone()["n"],
            conn.execute("SELECT COUNT(*) AS n FROM skill_packages").fetchone()["n"],
        )


def test_previous_to_only_staging_does_not_satisfy_candidate_startup_contract(tmp_path):
    registry, _, packages, _ = production_like_registry(tmp_path)
    entry = packages[("event-campaign-plan", "1.0.1")]
    registry._upsert_import(
        "event-campaign-plan",
        "1.0.1",
        "event-campaign-plan",
        "old release-path staging boundary",
        (BUNDLE / entry["artifact_path"]).read_bytes(),
        None,
        published=True,
    )
    assert package_row(registry, "event-campaign-plan", "1.0.1") is not None
    assert package_row(registry, "event-campaign-plan", "1.0.0") is None
    with pytest.raises(SkillRegistryError, match="missing bundled version"):
        registry.initialize()
    assert registry_bindings(registry) == FROM


def test_r1_release_transition_cli_stages_missing_candidate_packages(tmp_path, monkeypatch, capsys):
    registry, _, packages, transitions = production_like_registry(tmp_path)
    predecessor = {
        "release_id": "fixture-predecessor",
        "source_commit": "6" * 40,
        "archive_sha256": "a" * 64,
    }
    predecessor_path = tmp_path / "fixture-predecessor.manifest.json"
    predecessor_path.write_text(json.dumps(predecessor), encoding="utf-8")
    candidate = {
        "binding_transition": {
            "schema_version": 1,
            "exact_predecessor": {
                **predecessor,
                "manifest_sha256": transition.sha256(predecessor_path.read_bytes()),
            },
            "transitions": [
                {
                    "agent_id": "campaign-agent",
                    "from_bindings": FROM,
                    "to_bindings": TO,
                    "required_skill_identities": transitions[0]["required"],
                }
            ],
        }
    }
    candidate_path = tmp_path / "fixture-candidate.manifest.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    monkeypatch.setenv("ENTERPRISE_POC_DATABASE_URL", registry._store.database_url)

    assert transition.main(
        [
            "--candidate-manifest",
            str(candidate_path),
            "--predecessor-manifest",
            str(predecessor_path),
            "--bundle-root",
            str(BUNDLE),
            "--data-dir",
            str(tmp_path),
            "--apply",
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "binding_transition_applied"
    assert {tuple(item.values()) for item in output["package_staging"]["staged"]} == {
        key for key, entry in packages.items() if not entry["legacy_artifact"]
    }
    assert registry_bindings(registry) == TO
    registry.initialize()


def test_r1_r2_r3_missing_package_is_staged_verified_before_binding_and_startup_passes(
    tmp_path, monkeypatch
):
    registry, _, packages, transitions = production_like_registry(tmp_path)
    assert package_row(registry, "event-campaign-plan", "1.0.1") is None
    with pytest.raises(SkillRegistryError, match="missing bundled version"):
        registry.initialize()

    events = []
    original_upsert = registry._upsert_import
    original_initialize = registry.initialize

    def traced_upsert(*args, **kwargs):
        assert registry_bindings(registry) == FROM
        result = original_upsert(*args, **kwargs)
        events.append("stage")
        return result

    def traced_initialize():
        assert package_row(registry, "event-campaign-plan", "1.0.1") is not None
        assert registry_bindings(registry) == FROM
        events.append("startup_verify")
        return original_initialize()

    monkeypatch.setattr(registry, "_upsert_import", traced_upsert)
    monkeypatch.setattr(registry, "initialize", traced_initialize)
    result = transition.stage_and_apply(registry, transitions, packages, BUNDLE, rollback=False)

    assert events[-1] == "startup_verify" and set(events[:-1]) == {"stage"}
    assert {tuple(item.values()) for item in result["package_staging"]["staged"]} == set(packages) - {
        key for key, entry in packages.items() if entry["legacy_artifact"]
    }
    assert registry_bindings(registry) == TO
    assert registry_manifest(registry) == TO
    row = package_row(registry, "event-campaign-plan", "1.0.1")
    registry._verify_package(row, expected=packages[("event-campaign-plan", "1.0.1")]["artifact_sha256"])
    original_initialize()


def test_r4_existing_exact_candidate_package_staging_is_idempotent(tmp_path):
    registry, _, packages, _ = production_like_registry(tmp_path)
    first = transition.stage_candidate_packages(registry, packages, BUNDLE)
    before = package_row(registry, "event-campaign-plan", "1.0.1")
    counts_before = registry_counts(registry)
    second = transition.stage_candidate_packages(registry, packages, BUNDLE)
    after = package_row(registry, "event-campaign-plan", "1.0.1")

    assert {tuple(item.values()) for item in first["staged"]} == set(packages) - {
        key for key, entry in packages.items() if entry["legacy_artifact"]
    }
    assert second["staged"] == []
    assert dict(before) == dict(after)
    assert registry_counts(registry) == counts_before
    assert registry_bindings(registry) == FROM


def test_r5_conflicting_existing_version_blocks_with_binding_unchanged(tmp_path):
    registry, _, packages, _ = production_like_registry(tmp_path)
    conflict = registry._upsert_import(
        "event-campaign-plan",
        "1.0.1",
        "event-campaign-plan",
        "conflict fixture",
        package_bytes("event-campaign-plan", "# conflicting identity\n"),
        None,
        published=True,
    )
    conflict_checksum = conflict["checksum"]

    with pytest.raises(transition.TransitionBlocked, match="CANDIDATE_SKILL_PACKAGE_IDENTITY_MISMATCH"):
        transition.stage_candidate_packages(registry, packages, BUNDLE)
    assert registry_bindings(registry) == FROM
    assert registry_manifest(registry) == FROM
    assert package_row(registry, "event-campaign-plan", "1.0.1")["checksum"] == conflict_checksum


def test_r6_staging_failure_precedes_code_switch_and_leaves_from_active(tmp_path, monkeypatch):
    registry, _, packages, transitions = production_like_registry(tmp_path)

    def fail_staging(*args, **kwargs):
        raise SkillRegistryError("injected package staging failure")

    monkeypatch.setattr(registry, "_upsert_import", fail_staging)
    with pytest.raises(SkillRegistryError, match="injected package staging failure"):
        transition.stage_and_apply(registry, transitions, packages, BUNDLE, rollback=False)
    assert registry_bindings(registry) == FROM
    assert registry_manifest(registry) == FROM

    switch = (ROOT / "deploy" / "release_switch.sh").read_text(encoding="utf-8")
    apply = switch.index("scripts/release_binding_transition.py --candidate-manifest", switch.index("trap 'fail_release' ERR"))
    code_switch = switch.index('ln -sfn "$release_root" "$current_link"')
    service_start = switch.index("systemctl restart enterprise-agent-mcp.service")
    assert apply < code_switch < service_start


def test_r7_health_rollback_restores_from_and_retains_inactive_staged_package(tmp_path):
    registry, predecessor_registry, packages, transitions = production_like_registry(tmp_path)
    transition.stage_and_apply(registry, transitions, packages, BUNDLE, rollback=False)
    assert registry_bindings(registry) == TO

    result = transition.stage_and_apply(registry, transitions, packages, BUNDLE, rollback=True)
    assert result["status"] == "binding_transition_rolled_back"
    assert registry_bindings(registry) == FROM
    assert registry_manifest(registry) == FROM
    row = package_row(registry, "event-campaign-plan", "1.0.1")
    assert row is not None and row["status"] == "published"
    assert all(item["version"] != "1.0.1" for item in predecessor_registry.verify_bootstrap()["versions"] if item["skill_slug"] == "event-campaign-plan")


def test_r8_legacy_release_without_transition_remains_a_noop(tmp_path, capsys):
    candidate = tmp_path / "legacy.manifest.json"
    predecessor = tmp_path / "predecessor.manifest.json"
    candidate.write_text(json.dumps({"release_id": "legacy"}), encoding="utf-8")
    predecessor.write_text(json.dumps({"release_id": "predecessor"}), encoding="utf-8")

    result = transition.main(
        [
            "--candidate-manifest",
            str(candidate),
            "--predecessor-manifest",
            str(predecessor),
            "--bundle-root",
            str(BUNDLE),
            "--data-dir",
            str(tmp_path),
            "--apply",
        ]
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "NO_DECLARED_BINDING_TRANSITION"
