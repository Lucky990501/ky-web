"""Offline exact-pair gates. Boundary doubles are NOT Native qualification.

No host, database, authority issuer or service is contacted. Fixture records
below live only in pytest temporary directories; they never grant authority.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import release_manifest as manifests, release_runtime_recovery as recovery
from scripts import rollback_preflight as gate, schema_015_runtime_only as runtime
from scripts import schema015_successor_binding as binding

ROOT = Path(__file__).resolve().parents[1]


def offline(source="a" * 40):
    value = json.loads((ROOT / "deploy/schema_015_runtime_only.v1.json").read_text())
    value.update(schema_version=2, contract_id=binding.CONTRACT_ID,
                 recovery_contract=binding.CONTRACT_ID, data_contract="member_account_status_v1",
                 exact_predecessor=copy.deepcopy(binding.PREDECESSOR),
                 successor={"source_commit": source, "source_tree": "b" * 40,
                            "parent_source_commit": binding.PREDECESSOR["source_commit"],
                            "tooling_pins": dict.fromkeys(binding.TOOLING_FILES, "c" * 64)})
    return value


@pytest.fixture
def pair(tmp_path):
    root = tmp_path / "releases/offline-fixture/enterprise_agent_poc"
    root.mkdir(parents=True)
    value = offline()
    for relative in binding.TOOLING_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("isolated fixture for " + relative).encode())
        value["successor"]["tooling_pins"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"release_id": "offline-fixture", "source_commit": value["successor"]["source_commit"],
                "archive_sha256": "d" * 64, "build_platform": "linux-contract-fixture",
                "selected_files": ["enterprise_agent_poc/" + p for p in binding.TOOLING_FILES],
                "selected_file_count": len(binding.TOOLING_FILES), "runtime_only_release": value}
    raw = root.parent / "offline-fixture.manifest.json"
    raw.write_text(json.dumps(manifest))
    return tmp_path, root, manifest


@pytest.fixture
def qualified(pair, monkeypatch):
    base, root, manifest = pair
    value = manifest["runtime_only_release"]
    candidate = {"release_id": manifest["release_id"],
                 **{k: value["successor"][k] for k in ("source_commit", "source_tree", "parent_source_commit")},
                 "archive_sha256": manifest["archive_sha256"],
                 "raw_manifest_sha256": hashlib.sha256((root.parent / "offline-fixture.manifest.json").read_bytes()).hexdigest(),
                 "manifest_sha256": gate.digest(manifest)}
    evidence = {"schema_version": 1, "scope": "ISOLATED_NATIVE_SCHEMA015_EXACT_PAIR",
                "candidate": candidate, "predecessor": copy.deepcopy(binding.PREDECESSOR),
                "binding_sha256": binding.digest(value), "schema_fingerprint": "e" * 64,
                "schema": "015", "pending": 0, "migration_action": "NONE",
                "data_contract": "member_account_status_v1", "checks": dict.fromkeys(binding.CHECKS, "PASS")}
    approval = {"schema_version": 1, "scope": "SCHEMA015_EXACT_PAIR_RELEASE_RECOVERY",
                "issuer_uid": 0, "audit_id": "FIXTURE-NOT-AN-APPROVAL", "issued_at": 10, "expires_at": 100,
                "binding_sha256": binding.digest(value), "candidate": copy.deepcopy(candidate),
                "predecessor": copy.deepcopy(binding.PREDECESSOR), "evidence_sha256": "f" * 64}
    seen = []
    def fixture_reader(path):
        seen.append(path)
        assert path.parent == base / "shared/release-qualifications"
        return (approval, "0" * 64) if path.name.endswith(".approval.v1.json") else (evidence, "f" * 64)
    # Explicit trust-reader boundary injection, not a real Root seal.
    monkeypatch.setattr(binding, "protected_json", fixture_reader)
    return base, root, manifest, approval, evidence, seen


def test_v2_exact562_is_distinct_from_immutable_519_contract(pair):
    base, root, manifest = pair
    assert binding.declaration(root, manifests.validate_manifest_contract(manifest)) == manifest["runtime_only_release"]
    assert recovery.declared(root, manifest)["exact_predecessor"] == binding.PREDECESSOR
    assert recovery.exact_predecessor(root, manifest) == binding.PREDECESSOR
    assert recovery.contract(ROOT)["exact_predecessor"] == recovery.PREDECESSOR
    assert recovery.PREDECESSOR["source_commit"] == "519c649bd61d3bf3b1a6708410b9b7102eccb7a2"
    assert recovery.PREDECESSOR != binding.PREDECESSOR


@pytest.mark.parametrize("key", tuple(binding.PREDECESSOR))
def test_all_predecessor_identity_components_exact(key):
    value = offline()
    value["exact_predecessor"][key] = "0" * (40 if key in {"source_commit", "source_tree"} else 64)
    with pytest.raises(binding.BindingBlocked, match="fixed_contract"):
        binding.validate(value)


@pytest.mark.parametrize("key,wrong", [("current_schema", "014"), ("target_schema", "016"),
    ("pending", 1), ("pending", False), ("unknown", 1), ("checksum_drift", 1),
    ("migration015_blob", "0" * 40), ("migration_action", "APPLY"),
    ("data_contract", "legacy"), ("recovery_mode", "RESTORE_SCHEMA014"),
    ("schema_version", True), ("post_commit_runtime_rollback", "DISABLED")])
def test_schema_migration_floor_and_version_never_loosened(key, wrong):
    value = offline()
    value[key] = wrong
    with pytest.raises(binding.BindingBlocked):
        binding.validate(value)


@pytest.mark.parametrize("fault", ["extra", "short_source", "short_tree", "short_parent", "no_pin", "wrong_pin", "self"])
def test_successor_requires_complete_exact_identity(fault):
    value = offline()
    own = value["successor"]
    if fault == "extra": value["approved"] = True
    elif fault == "no_pin": own["tooling_pins"].pop(binding.TOOLING_FILES[0])
    elif fault == "wrong_pin": own["tooling_pins"][binding.TOOLING_FILES[0]] = "short"
    elif fault == "self": own["source_commit"] = binding.PREDECESSOR["source_commit"]
    else: own[{"short_source": "source_commit", "short_tree": "source_tree", "short_parent": "parent_source_commit"}[fault]] = "abc123"
    with pytest.raises(binding.BindingBlocked):
        binding.validate(value)


def test_rebind_is_per_exact_successor_not_e551_wildcard():
    first, final = offline(), offline("f" * 40)
    binding.validate(first)
    binding.validate(final)
    assert binding.digest(first) != binding.digest(final)
    assert first["exact_predecessor"] == final["exact_predecessor"]


@pytest.mark.parametrize("fault", ["manifest_source", "tooling_bytes", "tooling_symlink", "missing_file"])
def test_declaration_and_tooling_installation_reject_drift(pair, fault):
    _, root, manifest = pair
    path = root / binding.TOOLING_FILES[0]
    if fault == "manifest_source": manifest["source_commit"] = "e" * 40
    elif fault == "tooling_bytes": path.write_bytes(b"changed")
    else:
        path.unlink()
        if fault == "tooling_symlink": path.symlink_to(ROOT / binding.TOOLING_FILES[0])
    with pytest.raises(binding.BindingBlocked):
        binding.declaration(root, manifest)


@pytest.mark.parametrize("fault", ["missing_tooling", "source", "forward", "binding"])
def test_manifest_remains_strict_code_only(pair, fault):
    _, _, manifest = pair
    if fault == "missing_tooling":
        manifest["selected_files"].pop()
        manifest["selected_file_count"] -= 1
    elif fault == "source": manifest["source_commit"] = "e" * 40
    else: manifest["forward_migrations" if fault == "forward" else "binding_transition"] = {}
    with pytest.raises(manifests.ManifestContractError):
        manifests.validate_manifest_contract(manifest)


def test_exact_qualification_is_read_only_and_has_no_issuer(qualified):
    base, root, manifest, _, _, seen = qualified
    result = binding.qualification(base, root, manifest, "e" * 64, now=20)
    assert result["status"] == "exact_pair_qualification_verified"
    assert len(seen) == 2
    assert not (base / "shared").exists()


@pytest.mark.parametrize("section,field", [("candidate", "source_commit"), ("candidate", "source_tree"),
    ("candidate", "parent_source_commit"), ("candidate", "archive_sha256"),
    ("candidate", "raw_manifest_sha256"), ("candidate", "manifest_sha256"),
    ("predecessor", "source_commit"), ("predecessor", "archive_sha256"),
    ("predecessor", "manifest_sha256")])
def test_wrong_approval_pair_and_artifact_rejected(qualified, section, field):
    base, root, manifest, approval, _, _ = qualified
    approval[section][field] = "wrong"
    with pytest.raises(binding.BindingBlocked, match="approval_binding"):
        binding.qualification(base, root, manifest, "e" * 64, now=20)


@pytest.mark.parametrize("field,wrong", [("expires_at", 20), ("issued_at", 21),
    ("issued_at", False), ("issued_at", 0), ("issuer_uid", 1000), ("issuer_uid", False),
    ("audit_id", ""), ("scope", "TEST_ONLY_OTHER"), ("binding_sha256", "0" * 64),
    ("evidence_sha256", "0" * 64)])
def test_missing_expired_or_wrong_approval_stays_blocked(qualified, field, wrong):
    base, root, manifest, approval, _, _ = qualified
    approval[field] = wrong
    with pytest.raises(binding.BindingBlocked):
        binding.qualification(base, root, manifest, "e" * 64, now=20)


@pytest.mark.parametrize("check", binding.CHECKS)
def test_component_pass_never_substitutes_for_stage2_native_or_recovery(qualified, check):
    base, root, manifest, _, evidence, _ = qualified
    evidence["checks"][check] = "NOT_VERIFIED"
    with pytest.raises(binding.BindingBlocked, match="native_evidence_required"):
        binding.qualification(base, root, manifest, "e" * 64, now=20)


@pytest.mark.parametrize("field,wrong", [("schema", "014"), ("pending", 1), ("pending", False),
    ("schema_fingerprint", "0" * 64), ("migration_action", "APPLY"),
    ("data_contract", "old"), ("scope", "FIXTURE_PASS")])
def test_unknown_schema_and_data_contract_evidence_rejected(qualified, field, wrong):
    base, root, manifest, _, evidence, _ = qualified
    evidence[field] = wrong
    with pytest.raises(binding.BindingBlocked, match="native_evidence_required"):
        binding.qualification(base, root, manifest, "e" * 64, now=20)


def schema_boundaries(pair, monkeypatch):
    base, root, manifest = pair
    (root / "deploy/rollback_compatibility.json").write_bytes((ROOT / "deploy/rollback_compatibility.json").read_bytes())
    monkeypatch.setattr(gate, "release_identity", lambda *a: (root, manifest))
    monkeypatch.setattr(gate, "check_sources", lambda *a: a[1])
    monkeypatch.setattr(recovery, "predecessor", lambda *a: (root, manifest))
    monkeypatch.setattr(gate, "read_history", lambda *a, **kw: (
        [{"version": f"{i:03}"} for i in range(1, 16)], {"epoch": "productized_v1"},
        "fixture", ["member_account_status_v1"]))
    monkeypatch.setattr(runtime, "ledger_snapshot", lambda *a: {"ledger_sha256": "a" * 64})
    return base, root, manifest


def test_v2_recovery_target562_and_no_down_contract(pair, monkeypatch):
    base, root, manifest = schema_boundaries(pair, monkeypatch)
    monkeypatch.setattr(binding, "qualification", lambda *a: {})
    result = recovery.verify_schema(base, root, binding.PREDECESSOR["release_id"],
                                    binding.PREDECESSOR["source_commit"], database_url="fixture-not-a-db")
    assert result["target_source_commit"] == binding.PREDECESSOR["source_commit"]
    assert result["pending"] == 0 and not result["old_runner_invoked"]


def test_missing_qualification_blocks_before_db_and_pointer_mutation(pair, monkeypatch):
    base, root, manifest = schema_boundaries(pair, monkeypatch)
    prior = base / "releases/prior/enterprise_agent_poc"
    prior.mkdir(parents=True)
    (base / "release-current").symlink_to(prior)
    called = []
    monkeypatch.setattr(gate, "read_history", lambda *a, **kw: called.append("db"))
    with pytest.raises((OSError, binding.BindingBlocked)):
        recovery.verify_schema(base, root, binding.PREDECESSOR["release_id"],
                               binding.PREDECESSOR["source_commit"], database_url="fixture-not-a-db")
    assert called == [] and (base / "release-current").resolve() == prior
    assert not (base / "shared/release-state").exists()


@pytest.mark.parametrize("rid,source", [(recovery.PREDECESSOR["release_id"], recovery.PREDECESSOR["source_commit"]),
    (binding.PREDECESSOR["release_id"], "f" * 40), ("unknown", "f" * 40)])
def test_no_arbitrary_rollback_target(pair, monkeypatch, rid, source):
    base, root, _ = schema_boundaries(pair, monkeypatch)
    with pytest.raises(gate.RollbackBlocked, match="rollback_target_below_member_account_status_floor"):
        recovery.verify_schema(base, root, rid, source, database_url="fixture-not-a-db")


def test_v2_receipt_and_postcommit_restore_keep_exact562_binding(pair, monkeypatch):
    from scripts import release_verify, migrate
    base, root, manifest = pair
    monkeypatch.setattr(recovery, "verify_schema", lambda *a: {"applied_versions": [f"{i:03}" for i in range(1, 16)], "pending": 0})
    monkeypatch.setattr(recovery, "approval", lambda *a: {})
    monkeypatch.setattr(runtime, "ledger_snapshot", lambda *a: {"ledger_sha256": "a" * 64})
    monkeypatch.setattr(migrate, "settings", SimpleNamespace(database_url="fixture-not-a-db"))
    monkeypatch.setattr(release_verify, "binding_gate", lambda *a: {"campaign-agent": {"skill": "1.0.0"}})
    monkeypatch.setattr(release_verify, "registry_for", lambda *a: None)
    monkeypatch.setattr(release_verify, "bindings", lambda *a: {"campaign-agent": {"skill": "1.0.0"}})
    monkeypatch.setattr(release_verify, "service_state", lambda *a: {r: {"pid": 0, "active": "failed"} for r in ("api", "mcp", "worker")})
    (base / "release-current").symlink_to(root)
    recovery.receipt(base, root, manifest, base / "fixture-snapshot")
    result = recovery.preflight(base, root, manifest)
    assert result["exact_predecessor"] == binding.PREDECESSOR["release_id"]
    path = recovery.receipt_path(base, manifest["release_id"])
    record = json.loads(path.read_text())
    assert record["predecessor"] == binding.PREDECESSOR and record["schema_rollback"] is False
    record["predecessor"] = recovery.PREDECESSOR
    path.write_text(json.dumps(record))
    with pytest.raises(gate.RollbackBlocked, match="release_commit_receipt_identity"):
        recovery.preflight(base, root, manifest)


def test_duplicate_json_keys_not_accepted():
    with pytest.raises(binding.BindingBlocked, match="duplicate_key"):
        json.loads('{"issued_at":1,"issued_at":2}', object_pairs_hook=binding._pairs)


def test_real_untrusted_tmp_directory_cannot_supply_protected_qualification(tmp_path):
    path = tmp_path / "record.json"
    path.write_text("{}")
    with pytest.raises(binding.BindingBlocked, match="trusted_ancestor"):
        binding.protected_json(path)


@pytest.mark.parametrize("fault", ["owner", "mode", "links", "size", "symlink"])
def test_nofollow_descriptor_root_seal_and_resources(tmp_path, monkeypatch, fault):
    path = tmp_path / "record.json"
    path.write_text("{}")
    # The synthetic descriptor metadata belongs only to this Guard call.
    # pytest may remove successful tmp_path directories before its monkeypatch
    # fixture finalizer; shutil must see the real os.fstat at that point.
    with monkeypatch.context() as scoped:
        actual = Path.lstat
        def trusted_parents(p):
            observed = actual(p)
            if p != path:
                return SimpleNamespace(st_mode=0o40755, st_uid=0)
            return observed
        scoped.setattr(Path, "lstat", trusted_parents)
        closed = []
        close = os.close
        scoped.setattr(os, "close", lambda fd: (closed.append(fd), close(fd))[-1])
        if fault == "symlink":
            target = tmp_path / "target.json"
            path.rename(target)
            path.symlink_to(target)
            with pytest.raises(OSError): binding.protected_json(path)
            assert closed == []  # open(NO_FOLLOW) rejected before descriptor creation
            return
        scoped.setattr(os, "fstat", lambda fd: SimpleNamespace(
            st_mode=0o100644 if fault == "mode" else 0o100600,
            st_uid=1000 if fault == "owner" else 0, st_nlink=2 if fault == "links" else 1,
            st_size=262145 if fault == "size" else 2))
        with pytest.raises(binding.BindingBlocked, match="root_seal"):
            binding.protected_json(path)
        assert len(closed) == 1
