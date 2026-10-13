"""Joint actual producer/consumer calls, real private atomic I/O and flock.

Root ownership, issuer identity and the release artifact are explicit isolated
fixtures, never effective authority. No Root command, service, PG or network is
used. Authorization/state functions and POSIX locking are not replaced.
"""
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from scripts import schema015_successor_binding as binding
from scripts import v3_trusted_record_bootstrap as tool
from test_schema015_successor_binding import pair
from test_p0_lightweight_release_contract import controlled
from test_v3_trusted_record_bootstrap import LocalStore, arguments, raw
from test_v3_state_consumer_entry import boundaries, invoke


class RootMetadataFixturePath(type(Path())):
    """Only the lock metadata boundary; real path/inode and flock remain used."""
    def lstat(self, *args, **kwargs):
        fields = list(super().lstat(*args, **kwargs))
        fields[0] &= ~0o022
        fields[4] = 0
        return os.stat_result(fields)


class LockMetadataFixtureOS:
    def __init__(self, path):
        self.path = path

    def __getattr__(self, name):
        return getattr(os, name)

    def fstat(self, fd):
        value = os.fstat(fd)
        if self.path.exists() and value.st_ino == self.path.stat().st_ino:
            fields = list(value)
            fields[4] = 0
            return os.stat_result(fields)
        return value


class JointStore(LocalStore):
    @contextmanager
    def lock(self):
        import fcntl
        try:
            saved = os.dup(9)
        except OSError:
            saved = None
        fd = os.open(self.base / "fixture-global.lock", os.O_CREAT | os.O_RDWR, 0o600)
        attached = False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise tool.Blocked("GLOBAL_RELEASE_LOCK_BUSY") from None
            if fd != 9:
                os.dup2(fd, 9)
                os.close(fd)
            attached = True
            binding.require_release_lock()
            yield
        finally:
            os.close(9 if attached else fd)
            if saved is not None:
                os.dup2(saved, 9)
                os.close(saved)


@pytest.fixture
def joint(controlled, monkeypatch):
    base, root, manifest, deployment, evidence, policy, _ = controlled
    value = manifest["runtime_only_release"]
    value.update(schema_version=4, contract_id=binding.STATE_CONTRACT, recovery_contract=binding.STATE_CONTRACT,
                 qualification_profile=binding.STATE_CONTRACT, state_contract=binding.STATE_CONTRACT,
                 security_base_source=binding.STATE_BASE_SOURCE, security_base_tree=binding.STATE_BASE_TREE)
    value["successor"]["parent_source_commit"] = binding.STATE_BASE_SOURCE
    manifest_path = root.parent / "offline-fixture.manifest.json"
    manifest_path.write_bytes(raw(manifest))
    exact = {"release_id": manifest["release_id"],
             **{key: value["successor"][key] for key in ("source_commit", "source_tree", "parent_source_commit")},
             "archive_sha256": manifest["archive_sha256"], "manifest_sha256": binding.digest(manifest),
             "raw_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()}
    for name in ("EXACT", "BINDING_SHA", "STATEFUL_CONTEXT", "VERSION"):
        monkeypatch.setattr(tool, name, getattr(tool, name))  # Restore after real configure_candidate.
    tool.configure_candidate(exact, binding.digest(value))
    for record in (policy, deployment, evidence):
        record.update(schema_version=2, state_contract=binding.STATE_CONTRACT,
                      candidate=copy.deepcopy(exact), binding_sha256=binding.digest(value))
    reports = {name: ("FICTIONAL_COMPONENT_REPORT_NOT_NATIVE:" + name).encode() for name in binding.P0_CHECKS}
    for name, check in evidence["checks"].items():
        check["report_sha256"] = tool.sha(reports[name])
    deployment.update(policy_approval_sha256=tool.sha(raw(policy)), evidence_sha256=tool.sha(raw(evidence)))
    producer = {"source_commit": exact["source_commit"], "source_tree": exact["source_tree"],
                "code_pins": dict.fromkeys(tool.CODE_FILES, "8" * 64)}
    for name in set(producer["code_pins"]) & set(value["successor"]["tooling_pins"]):
        producer["code_pins"][name] = value["successor"]["tooling_pins"][name]
    store = JointStore(base)
    lock = base / "fixture-global.lock"
    lock.touch(mode=0o600)
    monkeypatch.setattr(binding, "GLOBAL_RELEASE_LOCK", RootMetadataFixturePath(lock))
    monkeypatch.setattr(binding, "os", LockMetadataFixtureOS(lock))
    monkeypatch.setattr(binding, "protected_json", lambda path: (store.read(path)[0], tool.sha(store.read(path)[1])))
    monkeypatch.setattr(tool.time, "time", lambda: 50)
    return {"base": base, "root": root, "manifest": manifest, "value": value, "candidate": exact,
            "store": store, "paths": store.qualifications, "audit": store.audit,
            "policy": policy, "deployment": deployment, "evidence": evidence,
            "producer": producer, "reports": reports, "now": 50}


def coordinate(ctx, args, **changes):
    values = {name: args[name] for name in ("grant_sha", "producer", "operator", "host", "now", "manifest", "schema_fingerprint")}
    values.update(publish=True, attest=lambda *_: None)
    values.update(changes)
    return tool.reconcile(ctx["store"], args["action"], **values)


def prepare_policy(ctx):
    args = arguments(ctx)
    assert tool.seal(**args)["status"] == "PREPARED_REQUIRES_DURABILITY_RECONCILIATION"
    coordinate(ctx, args)
    return args


def prepare_all(ctx):
    prepare_policy(ctx)
    args = arguments(ctx, "seal-release-records")
    tool.seal(**args)
    coordinate(ctx, args)
    return args


def consume(ctx):
    return binding.qualification(ctx["base"], ctx["root"], ctx["manifest"], "e" * 64, now=50)


@pytest.mark.parametrize("field", ["source_commit", "source_tree"])
def test_wrong_producer_identity_rejected_before_state_or_authority_writes(joint, field):
    args = arguments(joint)
    args["producer"] = {**args["producer"], field: "9" * 40}
    before = list(joint["store"].writes)
    with pytest.raises(tool.Blocked, match="STATEFUL_PRODUCER_SOURCE_DRIFT"):
        tool.seal(**args)
    with pytest.raises(tool.Blocked, match="STATEFUL_PRODUCER_SOURCE_DRIFT"):
        coordinate(joint, args)
    assert joint["store"].writes == before


def child_result(call):
    """A separate process, not threads or a mocked flock success result."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(read_fd)
            try:
                os.close(9)  # Do not inherit the parent's release-lock ownership.
            except OSError:
                pass
            try:
                result = {"ok": True, "value": call()}
            except Exception as exc:
                result = {"ok": False, "type": type(exc).__name__, "code": str(exc)}
            os.write(write_fd, json.dumps(result).encode())
        finally:
            os.close(write_fd)
            os._exit(0)
    os.close(write_fd)
    with os.fdopen(read_fd, "rb") as stream:
        result = json.loads(stream.read())
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    return result


def test_actual_seal_is_not_authority_until_exact_reconciliation_and_recovery_accepts(joint, monkeypatch):
    prepare_policy(joint)
    args = arguments(joint, "seal-release-records")
    tool.seal(**args)
    with joint["store"].lock(), pytest.raises(binding.BindingBlocked, match="pending_or_commit_unknown"):
        consume(joint)
    before = {path: path.read_bytes() for path in joint["store"].qualifications.iterdir()}
    coordinate(joint, args)
    assert all(path.read_bytes() == value for path, value in before.items())
    with joint["store"].lock():
        assert consume(joint)["state"] == "VERIFIED_COMMITTED"
        history = boundaries(joint, monkeypatch)
        assert invoke(joint, "recovery_target") == 0
        assert len(history) == 1


@pytest.mark.parametrize("fault", ["directory_fsync", "record_readback"])
def test_postreplace_unknown_requires_explicit_existing_operation_reconciliation(joint, monkeypatch, fault):
    prepare_policy(joint)
    store, args = joint["store"], arguments(joint, "seal-release-records")
    deployment, previous_fsync, previous_read = store.path("deployment"), os.fsync, store.read
    with monkeypatch.context() as patch:
        if fault == "directory_fsync":
            def fail(fd):
                if deployment.exists() and stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise OSError("isolated post-replace fsync uncertainty")
                return previous_fsync(fd)
            patch.setattr(os, "fsync", fail)
        else:
            def fail(path):
                if path == deployment:
                    raise OSError("isolated post-replace readback uncertainty")
                return previous_read(path)
            patch.setattr(store, "read", fail)
        with pytest.raises(tool.CommitUnknown):
            tool.seal(**args)
    original_bytes = deployment.read_bytes()
    assert original_bytes == args["record_raw"]["deployment"]
    assert not tool.operation_path(store, args["action"], "reconciled").exists()
    with store.lock(), pytest.raises(binding.BindingBlocked, match="pending_or_commit_unknown"):
        consume(joint)
    previous = list(store.writes)
    with pytest.raises(tool.Blocked):
        tool.seal(**args)
    with pytest.raises(tool.Blocked, match="RECONCILIATION_INTENT_DRIFT"):
        coordinate(joint, args, grant_sha="0" * 64)
    assert store.writes == previous
    assert coordinate(joint, args)["decision_reissued"] is False
    assert deployment.read_bytes() == original_bytes
    assert set(store.writes) - set(previous) == {
        tool.operation_path(store, args["action"], "complete"),
        tool.operation_path(store, args["action"], "reconciled"),
    }
    with store.lock():
        assert consume(joint)["state"] == "VERIFIED_COMMITTED"
    after = list(store.writes)
    coordinate(joint, args)
    assert store.writes == after
    with pytest.raises(tool.Blocked):
        tool.seal(**arguments(joint, "seal-release-records", audit_id="FICTIONAL_SECOND_RELEASE_DECISION"))
    assert store.writes == after


def test_revoke_audit_interruption_retry_preserves_bytes_and_never_reactivates(joint):
    release_args = prepare_all(joint)
    store, args = joint["store"], arguments(joint, "revoke-policy")
    original_policy = store.path("policy").read_bytes()
    store.fail_name = args["grant"]["audit_id"] + ".json"
    with pytest.raises(tool.CommitUnknown):
        tool.seal(**args)
    with store.lock(), pytest.raises(binding.BindingBlocked, match="revoking_or_revoked"):
        consume(joint)
    store.fail_name = None
    tool.seal(**args)
    assert (store.audit / (tool.EXACT["release_id"] + ".withdrawn-policy.json")).read_bytes() == original_policy
    assert not store.path("policy").exists()
    previous = list(store.writes)
    tool.seal(**args)
    assert store.writes == previous
    with pytest.raises(tool.Blocked, match="REVOKED_OR_REVOKING_CANNOT_RECONCILE"):
        coordinate(joint, release_args)
    with store.lock(), pytest.raises(binding.BindingBlocked, match="revoking_or_revoked"):
        consume(joint)


@pytest.mark.parametrize("fault", ["directory_fsync", "receipt_readback"])
def test_reconciliation_receipt_uncertainty_requires_independent_trusted_io(joint, monkeypatch, fault):
    prepare_policy(joint)
    store, args = joint["store"], arguments(joint, "seal-release-records")
    tool.seal(**args)
    original_records = {path: path.read_bytes() for path in store.qualifications.iterdir()}
    receipt = tool.operation_path(store, args["action"], "reconciled")
    previous_fsync, previous_read = os.fsync, store.read
    with monkeypatch.context() as patch:
        if fault == "directory_fsync":
            def fail(fd):
                if receipt.exists() and stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise OSError("isolated reconciliation receipt fsync uncertainty")
                return previous_fsync(fd)
            patch.setattr(os, "fsync", fail)
        else:
            def fail(path):
                if path == receipt:
                    raise OSError("isolated reconciliation receipt readback uncertainty")
                return previous_read(path)
            patch.setattr(store, "read", fail)
        with pytest.raises(tool.CommitUnknown):
            coordinate(joint, args)
        assert receipt.exists()  # Visibility is not durable reconciliation.
        with store.lock(), pytest.raises(binding.BindingBlocked, match="pending_or_commit_unknown"):
            consume(joint)
    # The sole existing operation is independently re-read/fsynced; its grant
    # and authority bytes are not reissued, replaced, deleted or returned to an
    # unconsumed state. Consumer also checks durability, not receipt visibility.
    before = list(store.writes)
    assert coordinate(joint, args)["decision_reissued"] is False
    assert store.writes == before
    assert all(path.read_bytes() == value for path, value in original_records.items())
    with store.lock():
        assert consume(joint)["state"] == "VERIFIED_COMMITTED"


def test_consumer_then_concurrent_revoke_cannot_interrupt_authorization_use(joint):
    prepare_all(joint)
    store, revoke = joint["store"], arguments(joint, "revoke-policy")
    with store.lock():
        assert consume(joint)["state"] == "VERIFIED_COMMITTED"
        result = child_result(lambda: tool.seal(**revoke))
        assert result == {"ok": False, "type": "Blocked", "code": "GLOBAL_RELEASE_LOCK_BUSY"}
        assert not list(store.audit.glob(tool.EXACT["release_id"] + ".revocation-*"))
        # This models authority use while the same real kernel lease is held.
        assert consume(joint)["state"] == "VERIFIED_COMMITTED"
    tool.seal(**revoke)
    with store.lock(), pytest.raises(binding.BindingBlocked, match="revoking_or_revoked"):
        consume(joint)


def test_revoke_process_before_policy_move_blocks_later_consumer_and_exact_retry(joint, monkeypatch):
    prepare_all(joint)
    store, revoke = joint["store"], arguments(joint, "revoke-policy")
    policy = store.path("policy")
    original = policy.read_bytes()
    actual_rename = os.rename
    def fail_before_move(source, target, *args, **kwargs):
        if source == policy:
            raise OSError("isolated interruption after durable revocation namespace")
        return actual_rename(source, target, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, "rename", fail_before_move)
        result = child_result(lambda: tool.seal(**revoke))
    assert result["type"] == "CommitUnknown" and result["ok"] is False
    assert policy.read_bytes() == original
    assert len(list(store.audit.glob(tool.EXACT["release_id"] + ".revocation-*"))) == 1
    with store.lock(), pytest.raises(binding.BindingBlocked, match="revoking_or_revoked"):
        consume(joint)
    tool.seal(**revoke)
    with store.lock(), pytest.raises(binding.BindingBlocked, match="revoking_or_revoked"):
        consume(joint)
