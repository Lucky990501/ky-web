"""Real release/recovery call paths with private files and isolated boundaries.

The protected-reader UID/ancestor boundary is an explicit fixture. Qualification,
state parsing, FD9 flock, file/fsync/readback and actual entry dispatch are real.
Database, service and Root operations are never run or claimed by these tests.
"""
import json
import os
from types import SimpleNamespace

import pytest

from scripts import migrate, release_manifest as manifests
from scripts import release_runtime_recovery as recovery
from scripts import rollback_preflight as gate, schema_015_runtime_only as runtime
from scripts import schema015_successor_binding as binding
from test_schema015_successor_binding import pair, schema_boundaries
from test_p0_lightweight_release_contract import controlled
from test_v3_state_contract import stateful


ENTRIES = ("runtime_preflight", "recovery_target", "rollback_preflight")


def boundaries(context, monkeypatch):
    base, root, manifest = (context[name] for name in ("base", "root", "manifest"))
    schema_boundaries((base, root, manifest), monkeypatch)
    (base / "release-current").symlink_to(root)
    monkeypatch.setattr(migrate, "settings", SimpleNamespace(database_url="fixture-not-a-db"))
    monkeypatch.setattr(recovery, "ROOT", root)
    monkeypatch.setattr(binding.time, "time", lambda: 20)
    original_digest = gate.digest
    # Existing fixture evidence uses e*64 for its isolated Schema015 identity.
    # Only the read-only database/schema boundary is doubled; manifest hashes
    # and every state/approval hash still use their real implementation.
    monkeypatch.setattr(gate, "digest", lambda value: "e" * 64 if isinstance(value, list)
                        and value and isinstance(value[0], dict) and "canonical_sha256" in value[0]
                        else original_digest(value))
    history_calls = []
    def history(*args, **kwargs):
        history_calls.append((args, kwargs))
        return ([{"version": f"{i:03}"} for i in range(1, 16)], {"epoch": "productized_v1"},
                "isolated-fixture", ["member_account_status_v1"])
    monkeypatch.setattr(gate, "read_history", history)
    return history_calls


def invoke(context, entry):
    base, root, manifest = (context[name] for name in ("base", "root", "manifest"))
    if entry == "runtime_preflight":
        return runtime.preflight(base, root, manifest)
    if entry == "recovery_target":
        return recovery.main(["target", "--candidate-manifest",
                              str(root.parent / (manifest["release_id"] + ".manifest.json"))])
    return gate.verify(base, root, binding.PREDECESSOR["release_id"],
                       binding.PREDECESSOR["source_commit"], database_url="fixture-not-a-db")


def assert_rejected(context, entry, capsys, pattern="v3_"):
    if entry == "recovery_target":
        assert invoke(context, entry) == 2
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "BLOCKED" and pattern in output["check"]
    else:
        with pytest.raises(binding.BindingBlocked, match=pattern):
            invoke(context, entry)


@pytest.mark.parametrize("entry", ENTRIES)
def test_real_entries_accept_reconciled_state_without_new_framework(stateful, monkeypatch, entry):
    calls = boundaries(stateful, monkeypatch)
    assert manifests.validate_manifest_contract(stateful["manifest"]) == stateful["manifest"]
    outcome = invoke(stateful, entry)
    assert outcome == 0 if entry == "recovery_target" else outcome["read_only"] is True
    assert len(calls) == 1


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("phase", ["intent_before_policy_move", "interrupted", "revoked"])
def test_release_and_recovery_reject_revocation_while_policy_still_exists(
    stateful, monkeypatch, capsys, entry, phase,
):
    calls = boundaries(stateful, monkeypatch)
    rid, paths = stateful["manifest"]["release_id"], stateful["paths"]
    policy = paths / (rid + ".policy-approval.v1.json")
    original = policy.read_bytes()
    audit = binding.state_paths(paths, rid, "revoke-policy", "intent").parent
    if phase == "intent_before_policy_move":
        (audit / (rid + ".revocation-" + "f" * 64)).mkdir()
    elif phase == "interrupted":
        binding.state_paths(paths, rid, "revoke-policy", "intent").write_text('{"state":"REVOKING"}')
    else:
        (audit / (rid + ".revoked.json")).write_text('{"state":"REVOKED"}')
    assert_rejected(stateful, entry, capsys, "v3_policy_revoking_or_revoked")
    assert calls == [] and policy.read_bytes() == original
    assert (stateful["base"] / "release-current").resolve() == stateful["root"]


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("state", ["PENDING", "UNKNOWN", "COMMIT_UNKNOWN", "REVOKING", "REVOKED", "OTHER"])
def test_unsafe_states_rejected_through_real_release_recovery_paths(
    stateful, monkeypatch, capsys, entry, state,
):
    calls = boundaries(stateful, monkeypatch)
    path = binding.state_paths(stateful["paths"], stateful["manifest"]["release_id"],
                               "seal-release-records", "reconciled")
    value = json.loads(path.read_bytes())
    value["state"] = state
    path.write_text(json.dumps(value))
    assert_rejected(stateful, entry, capsys, "v3_state_reconciliation")
    assert calls == []


@pytest.mark.parametrize("entry", ENTRIES)
@pytest.mark.parametrize("fault", ["missing", "missing_state", "wrong_version", "fsync", "readback"])
def test_uncertain_commit_never_reaches_database_or_recovery_use(
    stateful, monkeypatch, capsys, entry, fault,
):
    calls = boundaries(stateful, monkeypatch)
    path = binding.state_paths(stateful["paths"], stateful["manifest"]["release_id"],
                               "seal-release-records", "reconciled")
    if fault == "missing":
        path.rename(path.with_suffix(".retained-fixture"))
    elif fault in {"missing_state", "wrong_version"}:
        value = json.loads(path.read_bytes())
        if fault == "missing_state":
            value.pop("state")
        else:
            value["schema_version"] = 999
        path.write_text(json.dumps(value))
    elif fault == "fsync":
        original, inode = os.fsync, path.stat().st_ino
        def fail(fd):
            if os.fstat(fd).st_ino == inode:
                raise OSError("isolated receipt persistence uncertainty")
            return original(fd)
        monkeypatch.setattr(os, "fsync", fail)
    else:
        original, reads = binding.protected_json, []
        def fail(target):
            if target == path:
                reads.append(target)
                if len(reads) > 1:
                    raise OSError("isolated receipt readback uncertainty")
            return original(target)
        monkeypatch.setattr(binding, "protected_json", fail)
    assert_rejected(stateful, entry, capsys)
    assert calls == []


@pytest.mark.parametrize("entry", ENTRIES)
def test_consumer_code_pin_drift_stops_actual_entry_before_db(stateful, monkeypatch, capsys, entry):
    calls = boundaries(stateful, monkeypatch)
    path = stateful["root"] / "scripts/schema015_successor_binding.py"
    path.write_bytes(path.read_bytes() + b"\n# isolated drift\n")
    assert_rejected(stateful, entry, capsys, "successor_tooling_file_identity")
    assert calls == []


def test_v4_manifest_cannot_omit_required_consumer_from_artifact(stateful):
    manifest = stateful["manifest"]
    manifest["selected_files"].remove("enterprise_agent_poc/scripts/release_runtime_recovery.py")
    manifest["selected_file_count"] -= 1
    with pytest.raises(manifests.ManifestContractError, match="successor_manifest_binding"):
        manifests.validate_manifest_contract(manifest)


def test_recovery_generic_io_does_not_disclose_error_details(stateful, monkeypatch, capsys):
    boundaries(stateful, monkeypatch)
    def fail(*args, **kwargs):
        raise OSError("DO_NOT_PROJECT_INTERNAL_PATH_OR_CREDENTIAL")
    monkeypatch.setattr(recovery, "verify_schema", fail)
    assert invoke(stateful, "recovery_target") == 2
    output = capsys.readouterr().out
    assert "runtime_recovery_input_or_io" in output
    assert "DO_NOT_PROJECT" not in output
