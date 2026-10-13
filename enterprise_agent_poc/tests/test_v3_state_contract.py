"""V4 state-consumer contracts with real private files, fsync and flock.

Only Root ownership and independent authority are fixture boundaries; these
tests do not create effective approvals, perform Root work or qualify Native
release. All receipt bytes and persistence transitions are actual file I/O.
"""
import copy
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import schema015_successor_binding as binding
from test_p0_lightweight_release_contract import controlled, pair


def raw(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw(value))
    path.chmod(0o600)
    return hashlib.sha256(raw(value)).hexdigest()


def write_states(ctx, *, include_receipts=True):
    """Prepare fixture bytes only, never a Root authority or issuance tool."""
    value, candidate = ctx["value"], ctx["candidate"]
    hashes = {}
    for name, suffix in (("policy", ".policy-approval.v1.json"),
                         ("evidence", ".evidence.v1.json")):
        hashes[name] = write_json(ctx["paths"] / (candidate["release_id"] + suffix), ctx[name])
    ctx["deployment"].update(policy_approval_sha256=hashes["policy"], evidence_sha256=hashes["evidence"])
    hashes["deployment"] = write_json(ctx["paths"] / (candidate["release_id"] + ".approval.v1.json"), ctx["deployment"])
    pins = dict.fromkeys(binding.STATE_PRODUCER_FILES, "8" * 64)
    for name in set(pins) & set(value["successor"]["tooling_pins"]):
        pins[name] = value["successor"]["tooling_pins"][name]
    for action in ("seal-policy", "seal-release-records"):
        names = ("policy",) if action == "seal-policy" else ("deployment", "evidence")
        records = {name: hashes[name] for name in names}
        intent = {"workflow": "V3_ROOT_MANUAL_ATTESTATION_STATE_V2", "state_contract": binding.STATE_CONTRACT,
                  "action": action, "candidate": candidate, "technical_audit_id": "FIXTURE_TECH_" + action,
                  "input_sha256": records, "state": "CONSUMED_FOR_RECORD_SEAL_ONLY",
                  "host": "FIXTURE_HOST", "operator": "FIXTURE_OPERATOR", "observed_at": 15,
                  "independent_decision_references": dict.fromkeys(records, "FIXTURE_INDEPENDENT_REF"),
                  "producer": {"source_commit": candidate["source_commit"],
                               "source_tree": candidate["source_tree"], "code_pins": pins}}
        grant = {"schema_version": 1, "workflow": intent["workflow"],
                 "authority": "PRODUCTION_ROOT_MAINTENANCE_AUTHORITY", "decision": "APPROVED",
                 "principal_id": "FIXTURE_NOT_AUTHENTICATED", "audit_id": intent["technical_audit_id"],
                 "issued_at": 10, "expires_at": 18, "binding_sha256": binding.digest(value),
                 "production_switch_authorized": False, "business_database_write_authorized": False,
                 **{name: intent[name] for name in ("host", "operator", "action", "candidate", "producer",
                                                   "input_sha256", "independent_decision_references")}}
        intent["technical_grant_sha256"] = write_json(ctx["audit"] / (grant["audit_id"] + ".technical-decision.json"), grant)
        intent_hash = write_json(binding.state_paths(ctx["paths"], candidate["release_id"], action, "intent"), intent)
        write_json(ctx["audit"] / (grant["audit_id"] + ".json"), intent)
        for name in names:
            if name != "evidence":
                write_json(ctx["audit"] / ("decision-" + hashes[name] + ".json"), intent)
        completion = {"state": "PREPARED", "action": action, "intent_sha256": intent_hash}
        completion_hash = write_json(binding.state_paths(ctx["paths"], candidate["release_id"], action, "complete"), completion)
        receipt = {"schema_version": 1, "contract_id": binding.STATE_CONTRACT,
                   "scope": "DURABILITY_RECONCILIATION", "state": "VERIFIED_COMMITTED",
                   "action": action, "candidate": candidate, "binding_sha256": binding.digest(value),
                   "intent_sha256": intent_hash, "completion_sha256": completion_hash, "record_sha256": records}
        if include_receipts:
            write_json(binding.state_paths(ctx["paths"], candidate["release_id"], action, "reconciled"), receipt)
    ctx["hashes"] = hashes
    return hashes


@pytest.fixture
def stateful(controlled, monkeypatch):
    base, root, manifest, deployment, evidence, policy, _ = controlled
    value = manifest["runtime_only_release"]
    value.update(schema_version=4, contract_id=binding.STATE_CONTRACT, recovery_contract=binding.STATE_CONTRACT,
                 qualification_profile=binding.STATE_CONTRACT, state_contract=binding.STATE_CONTRACT,
                 security_base_source=binding.STATE_BASE_SOURCE, security_base_tree=binding.STATE_BASE_TREE)
    value["successor"]["parent_source_commit"] = binding.STATE_BASE_SOURCE
    manifest_path = root.parent / "offline-fixture.manifest.json"
    manifest_path.write_bytes(raw(manifest))
    candidate = {"release_id": manifest["release_id"],
                 **{k: value["successor"][k] for k in ("source_commit", "source_tree", "parent_source_commit")},
                 "archive_sha256": manifest["archive_sha256"],
                 "raw_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                 "manifest_sha256": binding.digest(manifest)}
    for record in (policy, deployment, evidence):
        record.update(schema_version=2, state_contract=binding.STATE_CONTRACT,
                      candidate=copy.deepcopy(candidate), binding_sha256=binding.digest(value))
    ctx = {"base": base, "root": root, "manifest": manifest, "value": value,
           "policy": policy, "deployment": deployment, "evidence": evidence, "candidate": candidate,
           "paths": base / "shared/release-qualifications",
           "audit": base / "shared/release-state/v3-record-bootstrap"}
    write_states(ctx)

    def reader(path):
        data = path.read_bytes()
        return json.loads(data, object_pairs_hook=binding._pairs), hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(binding, "protected_json", reader)  # Fake Root seal only.
    lock = base / "release.lock"
    lock.write_bytes(b"")
    lock.chmod(0o600)
    monkeypatch.setattr(binding, "GLOBAL_RELEASE_LOCK", lock)
    original_lstat, original_fstat = Path.lstat, os.fstat
    def trusted(info):
        fields = list(info)
        fields[0] &= ~0o022
        fields[4] = 0
        return os.stat_result(fields)
    monkeypatch.setattr(Path, "lstat", lambda self, *a, **k: trusted(original_lstat(self, *a, **k)))
    monkeypatch.setattr(os, "fstat", lambda fd: trusted(original_fstat(fd)))
    try:
        saved = os.dup(9)
    except OSError:
        saved = None
    fd = os.open(lock, os.O_RDWR)
    os.dup2(fd, 9)
    if fd != 9:
        os.close(fd)
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        yield ctx
    finally:
        os.close(9)
        if saved is not None:
            os.dup2(saved, 9)
            os.close(saved)


def verify(ctx):
    return binding.qualification(ctx["base"], ctx["root"], ctx["manifest"], "e" * 64, now=20)


def test_valid_v4_requires_real_independent_durability_check(stateful, monkeypatch):
    flushed = []
    original = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (flushed.append(fd), original(fd))[1])
    result = verify(stateful)
    assert result["state_contract"] == binding.STATE_CONTRACT
    assert result["state"] == "VERIFIED_COMMITTED"
    assert result["native_acceptance_claim"] is False
    assert len(flushed) == 30  # Also durable verify technical snapshots and immutable audit markers.


@pytest.mark.parametrize("action", ["seal-policy", "seal-release-records"])
@pytest.mark.parametrize("suffix", ["intent", "complete", "reconciled"])
def test_missing_operation_state_fails_closed(stateful, action, suffix):
    binding.state_paths(stateful["paths"], stateful["candidate"]["release_id"], action, suffix).unlink()
    with pytest.raises(binding.BindingBlocked, match="pending_or_commit_unknown"):
        verify(stateful)


@pytest.mark.parametrize("state", ["PENDING", "UNKNOWN", "COMMIT_UNKNOWN", "REVOKING", "REVOKED", "PROPOSED"])
def test_unsafe_receipt_states_rejected(stateful, state):
    path = binding.state_paths(stateful["paths"], stateful["candidate"]["release_id"], "seal-release-records", "reconciled")
    record = json.loads(path.read_bytes())
    record["state"] = state
    write_json(path, record)
    with pytest.raises(binding.BindingBlocked, match="state_reconciliation"):
        verify(stateful)


@pytest.mark.parametrize("kind", ["namespace", "intent", "revoked"])
def test_revoke_intent_before_policy_move_rejects_current_policy(stateful, kind):
    rid = stateful["candidate"]["release_id"]
    if kind == "namespace":
        (stateful["audit"] / (rid + ".revocation-" + "a" * 64)).mkdir()
    elif kind == "intent":
        write_json(binding.state_paths(stateful["paths"], rid, "revoke-policy", "intent"), {"state": "REVOKING"})
    else:
        write_json(stateful["audit"] / (rid + ".revoked.json"), {"state": "REVOKED"})
    assert (stateful["paths"] / (rid + ".policy-approval.v1.json")).is_file()
    with pytest.raises(binding.BindingBlocked, match="revoking_or_revoked"):
        verify(stateful)


@pytest.mark.parametrize("fault", ["fsync", "readback"])
def test_visible_receipt_does_not_prove_durability(stateful, monkeypatch, fault):
    original = binding.protected_json
    seen = set()
    def fail_readback(path):
        if path in seen:
            raise OSError("fixture independent readback failure")
        seen.add(path)
        return original(path)
    if fault == "fsync":
        monkeypatch.setattr(os, "fsync", lambda _: (_ for _ in ()).throw(OSError("fixture fsync failure")))
    else:
        monkeypatch.setattr(binding, "protected_json", fail_readback)
    with pytest.raises((OSError, binding.BindingBlocked)):
        verify(stateful)


@pytest.mark.parametrize("field,value", [("schema_version", 1), ("state_contract", "unknown"),
    ("security_base_source", "f" * 40), ("security_base_tree", "f" * 40)])
def test_v4_contract_drift_rejected(stateful, field, value):
    stateful["value"][field] = value
    with pytest.raises(binding.BindingBlocked):
        verify(stateful)


def test_wrong_code_pin_rejected_before_state_read(stateful):
    stateful["value"]["successor"]["tooling_pins"][binding.TOOLING_FILES[0]] = "f" * 64
    with pytest.raises(binding.BindingBlocked, match="tooling_file_identity"):
        verify(stateful)


def test_inherited_fd_must_match_exact_lock_inode(stateful):
    unrelated = stateful["base"] / "unrelated.lock"
    unrelated.write_bytes(b"")
    fd = os.open(unrelated, os.O_RDWR)
    os.dup2(fd, 9)
    if fd != 9:
        os.close(fd)
    with pytest.raises(binding.BindingBlocked, match="global_lock_identity"):
        verify(stateful)


def test_new_record_cannot_be_consumed_as_old_v3(stateful):
    old = copy.deepcopy(stateful["value"])
    for key in ("state_contract", "security_base_source", "security_base_tree"):
        old.pop(key)
    old.update(schema_version=3, contract_id=binding.P0_CONTRACT, recovery_contract=binding.P0_CONTRACT,
               qualification_profile=binding.P0_CONTRACT)
    old["successor"]["parent_source_commit"] = binding.P0_SOURCE
    with pytest.raises(binding.BindingBlocked, match="policy_approval_fields"):
        binding._p0_qualification(stateful["paths"], old, stateful["candidate"], stateful["deployment"],
                                 stateful["evidence"], stateful["hashes"]["evidence"], "e" * 64, now=20)


def test_all_eighteen_gates_remain_required(stateful):
    assert len(binding.P0_CHECKS) == 18
    stateful["evidence"]["checks"].pop("pg_transactions")
    write_states(stateful)
    with pytest.raises(binding.BindingBlocked, match="required_real_checks"):
        verify(stateful)


@pytest.mark.parametrize("fault", ["grant_missing", "grant_hash", "marker_missing", "marker_hash",
                                   "producer_empty_pins", "intent_workflow", "intent_scope"])
def test_immutable_grant_and_audit_association_required(stateful, fault):
    rid, action = stateful["candidate"]["release_id"], "seal-policy"
    intent_path = binding.state_paths(stateful["paths"], rid, action, "intent")
    intent = json.loads(intent_path.read_bytes())
    grant_path = stateful["audit"] / (intent["technical_audit_id"] + ".technical-decision.json")
    marker_path = stateful["audit"] / (intent["technical_audit_id"] + ".json")
    if fault == "grant_missing": grant_path.unlink()
    elif fault == "grant_hash": grant_path.write_bytes(grant_path.read_bytes() + b" ")
    elif fault == "marker_missing": marker_path.unlink()
    elif fault == "marker_hash": marker_path.write_bytes(marker_path.read_bytes() + b" ")
    else:
        if fault == "producer_empty_pins": intent["producer"]["code_pins"] = {}
        elif fault == "intent_workflow": intent["workflow"] = "V3_ROOT_MANUAL_ATTESTATION_V1"
        else: intent.pop("state_contract")
        write_json(intent_path, intent)
    with pytest.raises(binding.BindingBlocked):
        verify(stateful)


def test_consumption_holds_actual_lock_until_parent_operation_finishes(stateful):
    verify(stateful)
    contender = subprocess.run([sys.executable, "-I", "-B", "-c",
        "import fcntl,os,sys; f=os.open(sys.argv[1],os.O_RDWR); "
        "\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
        "except BlockingIOError: print('REVOKE_BLOCKED_DURING_CONSUMPTION'); sys.exit(0)\n"
        "sys.exit(9)", str(binding.GLOBAL_RELEASE_LOCK)], capture_output=True, text=True, timeout=5)
    assert contender.returncode == 0
    assert contender.stdout.strip() == "REVOKE_BLOCKED_DURING_CONSUMPTION"
    # Function completion does not unlock the inherited open-file description.
    binding.require_release_lock()


def test_revoke_first_causes_competing_consumer_lock_rejection(stateful):
    fcntl.flock(9, fcntl.LOCK_UN)
    holder = subprocess.Popen([sys.executable, "-I", "-B", "-c",
        "import fcntl,os,sys; f=os.open(sys.argv[1],os.O_RDWR); "
        "fcntl.flock(f,fcntl.LOCK_EX); print('LOCKED',flush=True); sys.stdin.readline()",
        str(binding.GLOBAL_RELEASE_LOCK)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "LOCKED"
        with pytest.raises(binding.BindingBlocked, match="global_lock_not_owned"):
            verify(stateful)
    finally:
        holder.communicate("release\n", timeout=5)
    # The following completed revoke remains a permanent deny even after its
    # transaction has released the lock; policy bytes still exist in fixture.
    write_json(stateful["audit"] / (stateful["candidate"]["release_id"] + ".revoked.json"), {"state": "REVOKED"})
    with pytest.raises(binding.BindingBlocked, match="revoking_or_revoked"):
        verify(stateful)
