"""Component/real private POSIX files only; Root, actors and reports are doubles.

NEVER an Owner decision, actual Root attestation, installation or Native proof.
The immutable 7dc Linux archive fixture is genuine; the approval/report fixtures
are explicitly fictional and live only inside pytest's private temporary root.
"""
import copy
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import stat
from types import SimpleNamespace

import pytest

from scripts import v3_trusted_record_bootstrap as tool
from scripts import schema015_successor_binding as binding
from scripts.runtime_policy_rollout import Rollout, Image
from test_p0_lightweight_release_contract import controlled
from test_schema015_successor_binding import pair
from test_v3_state_frozen_reader_rejection import frozen_readers


@pytest.fixture
def historical_artifact_binding(frozen_readers, monkeypatch):
    # Only the historical archive-inspection tests consume the captured7dc
    # contract. Current consumers and the real d906 restore target stay intact.
    monkeypatch.setattr(tool, "binding", frozen_readers["schema015_successor_binding"])


def raw(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


@pytest.fixture
def artifact():
    # Explicit genuine archive input; missing input is an ERROR, not a skip or
    # permission to synthesize a substitute release package.
    root = Path(os.environ["V3_BOOTSTRAP_ARTIFACT_FIXTURE"])
    rid = tool.EXACT["release_id"]
    manifest_raw = (root / (rid + ".manifest.json")).read_bytes()
    return root / (rid + ".tar.gz"), manifest_raw, json.loads(manifest_raw)


class LocalStore:
    """Private current-UID files/real flock, never tool.Store or Production Root."""
    def __init__(self, base):
        self.base = base
        (base / "runtime-policy-isolated.marker").write_bytes(b"runtime-policy-local-only")
        self.writer = Rollout(base, boundary=object(), isolated=True)
        self.qualifications = base / "shared/release-qualifications"
        self.audit = base / "shared/release-state/v3-record-bootstrap"
        self.fail_name = None
        self.writes = []

    def read(self, path):
        data = self.writer._read(path).data
        return json.loads(data, object_pairs_hook=binding._pairs), data

    def create(self, path, data):
        if path.name == self.fail_name:
            raise OSError("fixture publication interrupted")
        tool.need(not path.exists() and not path.is_symlink(), "RECORD_ALREADY_EXISTS_OR_CONSUMED")
        self.writer._atomic(path, data, Image(b"", 0, 0, 0o600, os.geteuid(), os.getegid()))
        assert self.read(path)[1] == data
        self.writes.append(path)

    @contextmanager
    def lock(self):
        fd = os.open(self.base / "fixture-global.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise tool.Blocked("GLOBAL_RELEASE_LOCK_BUSY") from None
            yield
        finally:
            os.close(fd)

    def prepare_directories(self):
        self.qualifications.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.audit.mkdir(mode=0o700, parents=True, exist_ok=True)

    path = tool.Store.path


@pytest.fixture
def package(controlled, artifact, tmp_path, monkeypatch):
    _, _, manifest = artifact
    now = 50
    monkeypatch.setattr(tool.time, "time", lambda: now)
    policy, deployment, evidence = (copy.deepcopy(controlled[i]) for i in (5, 3, 4))
    for value in (policy, deployment, evidence):
        value.update(candidate=copy.deepcopy(tool.EXACT), binding_sha256=tool.BINDING_SHA)
    for name, check in evidence["checks"].items():
        check["source_commit"] = tool.EXACT["source_commit"]
        if name == "runtime_startup_health":
            for service in check["measurements"]["services"].values():
                service.update(source_commit=tool.EXACT["source_commit"], source_tree=tool.EXACT["source_tree"])
        if name == "artifact_backup_integrity":
            check["measurements"]["candidate_archive_sha256"] = tool.EXACT["archive_sha256"]
    reports = {n: ("FICTIONAL_COMPONENT_REPORT_NOT_NATIVE:" + n).encode() for n in binding.P0_CHECKS}
    for name, check in evidence["checks"].items():
        check["report_sha256"] = tool.sha(reports[name])
    deployment.update(policy_approval_sha256=tool.sha(raw(policy)), evidence_sha256=tool.sha(raw(evidence)))
    store = LocalStore(tmp_path)
    # Root metadata is explicitly doubled; actual private bytes/atomic writes
    # are exercised. Do not turn this reader into a CLI trust bypass.
    monkeypatch.setattr(binding, "protected_json", lambda path: (store.read(path)[0], tool.sha(store.read(path)[1])))
    producer = {"source_commit": "f" * 40, "source_tree": "e" * 40,
                "code_pins": {name: "1" * 64 for name in tool.CODE_FILES}}
    return {"store": store, "manifest": manifest, "policy": policy, "deployment": deployment,
            "evidence": evidence, "reports": reports, "producer": producer, "now": now}


def arguments(p, action="seal-policy", **changes):
    names = ("deployment", "evidence") if action == "seal-release-records" else ("policy",)
    records = {name: p[name] for name in names}
    record_raw = {name: raw(value) for name, value in records.items()}
    inputs = {name: tool.sha(value) for name, value in record_raw.items()}
    if action == "seal-release-records":
        inputs.update({"report:" + name: tool.sha(data) for name, data in p["reports"].items()})
    grant = {"schema_version": 1, "workflow": tool.VERSION, "authority": "PRODUCTION_ROOT_MAINTENANCE_AUTHORITY",
             "decision": "APPROVED", "principal_id": "FICTIONAL_MAINTAINER_NOT_AUTHORIZED",
             "audit_id": "FICTIONAL_TECH_" + action, "issued_at": 10, "expires_at": 100,
             "host": tool.HOST, "operator": "FICTIONAL_OPERATOR_NOT_AUTHORIZED", "action": action,
             "candidate": copy.deepcopy(tool.EXACT), "binding_sha256": tool.BINDING_SHA,
             "producer": copy.deepcopy(p["producer"]), "input_sha256": inputs,
             "independent_decision_references": dict.fromkeys(inputs, "FICTIONAL_CHANNEL_NOT_APPROVED"),
             "production_switch_authorized": False, "business_database_write_authorized": False}
    grant.update(changes)
    return {"store": p["store"], "action": action, "grant": grant, "grant_raw": raw(grant),
            "records": records, "record_raw": record_raw, "grant_sha": tool.sha(raw(grant)),
            "producer": p["producer"], "operator": "FICTIONAL_OPERATOR_NOT_AUTHORIZED", "host": tool.HOST,
            "now": p["now"], "manifest": p["manifest"], "schema_fingerprint": "e" * 64,
            "reports": p["reports"], "attest": lambda *_: None}


def sealed_policy(p):
    tool.seal(**arguments(p))


def test_independently_pinned_policy_bytes_are_not_synthesized(package):
    args = arguments(package)
    result = tool.seal(**args)
    store = package["store"]
    assert store.path("policy").read_bytes() == args["record_raw"]["policy"]
    assert result["deployment_executed"] is False
    assert not store.path("deployment").exists() and not store.path("evidence").exists()
    audit = store.read(store.audit / (args["grant"]["audit_id"] + ".json"))[0]
    assert audit["production_switch_authorized"] is False
    assert audit["input_sha256"]["policy"] == tool.sha(args["record_raw"]["policy"])
    assert store.read(store.audit / (args["grant"]["audit_id"] + ".technical-decision.json"))[1] == args["grant_raw"]


@pytest.mark.parametrize("action", ["seal-policy", "seal-release-records"])
def test_default_validation_writes_nothing_and_claims_no_authority(package, action):
    if action == "seal-release-records":
        sealed_policy(package)
    previous = list(package["store"].writes)
    result = tool.seal(**arguments(package, action), publish=False)
    assert result["writes"] == 0 and result["human_authority_verification"] == "NOT_PERFORMED"
    assert package["store"].writes == previous


def test_release_records_only_after_independent_policy_full_consumer_validation(package):
    sealed_policy(package)
    store = package["store"]
    tool.seal(**arguments(package, "seal-release-records"))
    assert store.writes[-3:] == [store.path("evidence"), store.path("deployment"),
                                tool.operation_path(store, "seal-release-records", "complete")]
    assert tool.operation_state(store, "seal-release-records")["state"] == "VERIFIED_COMMITTED"
    result = binding._p0_qualification(store.qualifications, package["manifest"]["runtime_only_release"],
        tool.EXACT, package["deployment"], package["evidence"], tool.sha(raw(package["evidence"])), "e" * 64, now=50)
    assert result["status"] == "p0_controlled_qualification_verified"
    assert result["native_acceptance_claim"] is False


@pytest.mark.parametrize("field,value", [
    ("authority", "PROJECT_OWNER_POLICY_AUTHORITY"), ("decision", "PENDING"), ("workflow", "V2"),
    ("schema_version", True), ("host", "other-host"), ("operator", "other-root"),
    ("action", "INSTALL_AND_SWITCH"), ("production_switch_authorized", True),
    ("business_database_write_authorized", True), ("expires_at", 50), ("issued_at", 51),
    ("expires_at", 100000), ("binding_sha256", "0" * 64), ("input_sha256", {}),
    ("independent_decision_references", {}), ("producer", {}), ("candidate", {}),
])
def test_independent_technical_scope_and_identity_strict(package, field, value):
    with pytest.raises(tool.Blocked):
        tool.seal(**arguments(package, **{field: value}))
    assert package["store"].writes == []


@pytest.mark.parametrize("fault", ["wrong_pin", "missing_pin", "raw_drift", "record_drift", "wrong_host", "wrong_operator"])
def test_external_pin_raw_bytes_and_live_identity_not_self_authorized(package, fault):
    args = arguments(package)
    if fault == "wrong_pin": args["grant_sha"] = "0" * 64
    if fault == "missing_pin": args["grant_sha"] = None
    if fault == "raw_drift": args["grant_raw"] += b" "
    if fault == "record_drift": args["record_raw"]["policy"] = b"{}"
    if fault == "wrong_host": args["host"] = "other-host"
    if fault == "wrong_operator": args["operator"] = "other-operator"
    with pytest.raises(tool.Blocked): tool.seal(**args)
    assert package["store"].writes == []


@pytest.mark.parametrize("field,value", [
    ("authority", "ROOT_UID_IS_NOT_OWNER"), ("decision", "PROPOSED"), ("accepted_risks", []),
    ("candidate", {}), ("predecessor", {}), ("binding_sha256", "0" * 64),
    ("test_environment_id", "*"), ("production_environment_id", "marked-private-test"),
    ("issuer_uid", True), ("audit_id", ""), ("expires_at", 50),
])
def test_owner_decision_exact_fields_risks_expiry_no_default_acceptance(package, field, value):
    package["policy"][field] = value
    with pytest.raises(tool.Blocked): tool.seal(**arguments(package))
    assert package["store"].writes == []


@pytest.mark.parametrize("name", tuple(binding.P0_CHECKS))
def test_actual_reports_must_all_match_independently_approved_bytes(package, name):
    sealed_policy(package)
    args = arguments(package, "seal-release-records")
    args["reports"] = dict(package["reports"], **{name: b"tampered report"})
    with pytest.raises(tool.Blocked, match="ACTUAL_REPORT_HASH_DRIFT"):
        tool.seal(**args)
    assert not package["store"].path("deployment").exists()


@pytest.mark.parametrize("fault", ["missing_report", "no_reports", "incomplete_evidence", "no_policy", "wrong_deployment", "expiry_after_tty"])
def test_incomplete_packet_or_expiry_at_publication_fail_closed(package, fault, monkeypatch):
    if fault != "no_policy": sealed_policy(package)
    args = arguments(package, "seal-release-records")
    if fault == "missing_report": args["reports"] = {n: v for n, v in package["reports"].items() if n != "pg_permissions"}
    if fault == "no_reports": args["reports"] = None
    if fault == "incomplete_evidence": package["evidence"]["checks"].pop("post_commit_runtime_recovery"); args = arguments(package, "seal-release-records")
    if fault == "wrong_deployment": package["deployment"]["policy_audit_id"] = "wrong"; args = arguments(package, "seal-release-records")
    if fault == "expiry_after_tty": args["attest"] = lambda *_: monkeypatch.setattr(tool.time, "time", lambda: 100)
    with pytest.raises((tool.Blocked, binding.BindingBlocked, FileNotFoundError)): tool.seal(**args)
    assert not package["store"].path("deployment").exists()


def test_repeat_consumption_rejected_even_new_technical_audit(package):
    sealed_policy(package)
    original = package["store"].path("policy").read_bytes()
    with pytest.raises(tool.Blocked): tool.seal(**arguments(package))
    with pytest.raises(tool.Blocked, match="ALREADY_CONSUMED"):
        tool.seal(**arguments(package, audit_id="FICTIONAL_SECOND_AUDIT"))
    assert package["store"].path("policy").read_bytes() == original


def test_interrupted_publication_never_retries_approved_decision(package):
    store = package["store"]
    store.fail_name = store.path("policy").name
    with pytest.raises(OSError): tool.seal(**arguments(package))
    assert not store.path("policy").exists()
    assert (store.audit / ("decision-" + tool.sha(raw(package["policy"])) + ".json")).exists()
    store.fail_name = None
    with pytest.raises(tool.Blocked, match="ALREADY_CONSUMED"):
        tool.seal(**arguments(package, audit_id="FICTIONAL_RETRY_AUDIT"))


def test_evidence_publication_interruption_cannot_authorize_deployment(package):
    sealed_policy(package)
    store = package["store"]
    store.fail_name = store.path("deployment").name
    with pytest.raises(OSError): tool.seal(**arguments(package, "seal-release-records"))
    assert store.path("evidence").exists() and not store.path("deployment").exists()
    with pytest.raises(tool.Blocked): tool.seal(**arguments(package, "seal-release-records"))


def test_interruption_before_technical_snapshot_cannot_replay_with_new_grant(package):
    store = package["store"]
    args = arguments(package)
    store.fail_name = args["grant"]["audit_id"] + ".technical-decision.json"
    with pytest.raises(OSError): tool.seal(**args)
    assert not store.path("policy").exists()
    store.fail_name = None
    with pytest.raises(tool.Blocked, match="SOURCE_OPERATION_ALREADY_CONSUMED"):
        tool.seal(**arguments(package, audit_id="FICTIONAL_NEW_GRANT_AFTER_EARLY_INTERRUPT"))


def test_actual_private_flock_serializes_and_no_record_overwrite(package):
    store = package["store"]
    with store.lock(), pytest.raises(tool.Blocked, match="GLOBAL_RELEASE_LOCK_BUSY"):
        tool.seal(**arguments(package))
    assert store.writes == []


@pytest.mark.parametrize("mode,uid,exists,accepted", [
    (0o755, 0, False, True), (0o700, 0, False, True), (0o777, 0, True, False),
    (0o1777, 0, True, True), (0o1777, 0, False, False), (0o1777, 1000, True, False),
    (0o775, 0, True, False), (0o755, 1000, True, False),
])
def test_shared_system_lock_parent_is_safe_only_with_root_inode_or_protected_directory(mode, uid, exists, accepted):
    info = SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=uid)
    assert tool.lock_parent_valid(info, exists) is accepted


def test_independent_revocation_withdraws_policy_and_preserves_immutable_history(package):
    sealed_policy(package)
    tool.seal(**arguments(package, "seal-release-records"))
    store = package["store"]
    before = store.path("policy").read_bytes()
    tool.seal(**arguments(package, "revoke-policy"))
    assert not store.path("policy").exists()
    assert (store.audit / (tool.EXACT["release_id"] + ".withdrawn-policy.json")).read_bytes() == before
    with pytest.raises(FileNotFoundError):
        binding._p0_qualification(store.qualifications, package["manifest"]["runtime_only_release"], tool.EXACT,
            package["deployment"], package["evidence"], tool.sha(raw(package["evidence"])), "e" * 64, now=50)
    with pytest.raises(tool.Blocked): tool.seal(**arguments(package, audit_id="AFTER_REVOCATION"))


def test_failed_tty_attestation_never_creates_an_authority_record(package):
    args = arguments(package)
    def refuse(*_): raise tool.Blocked("INDEPENDENT_HUMAN_VERIFICATION_NOT_ATTESTED")
    args["attest"] = refuse
    with pytest.raises(tool.Blocked): tool.seal(**args)
    assert package["store"].writes == []


@pytest.mark.parametrize("fault", ["same_audit", "technical_before_owner"])
def test_technical_sealing_is_not_an_owner_approval(package, fault):
    changes = {"audit_id": package["policy"]["audit_id"]} if fault == "same_audit" else {"issued_at": 9}
    with pytest.raises(tool.Blocked, match="DECISIONS_MUST_BE_SEPARATE"):
        tool.seal(**arguments(package, **changes))
    assert package["store"].writes == []


def test_cli_without_root_refuses_before_any_file_or_actor_acceptance(monkeypatch, capsys):
    monkeypatch.setattr(tool.os, "geteuid", lambda: 1000)
    assert tool.main(["seal-policy", "--git-repository", "/fixture", "--archive", "/fixture.tar.gz",
                      "--manifest", "/fixture.manifest.json", "--seal"]) == 2
    assert "EXACT_PRODUCTION_ROOT_REQUIRED" in capsys.readouterr().out


def test_cli_rejects_argument_injection_without_echoing_caller_value(capsys):
    with pytest.raises(SystemExit):
        tool.main(["ACTUAL_SECRET_CANARY_NOT_ALLOWED"])
    output = capsys.readouterr()
    assert "ACTUAL_SECRET_CANARY_NOT_ALLOWED" not in output.out + output.err


def test_actual_tty_function_cannot_be_replaced_by_piped_yes(monkeypatch):
    monkeypatch.setattr(tool.os, "geteuid", lambda: 1000)
    with pytest.raises(tool.Blocked, match="ROOT_TTY_REQUIRED"):
        tool.tty_attest("0" * 64, {})


def test_tool_has_no_install_switch_or_root_base_override():
    script = Path(tool.__file__).read_text()
    assert "--base" not in script and "--force" not in script and "extractall" not in script
    assert "systemctl" not in script and "psycopg" not in script and "urlopen" not in script
    assert "INSTALL_AND_SWITCH" not in script


def test_genuine_7dc_archive_all_files_and_seven_pins_not_a_new_build(artifact, historical_artifact_binding):
    archive, manifest_raw, _ = artifact
    repo = Path(os.environ["V3_BOOTSTRAP_GIT_REPOSITORY"])
    reader = tool.git_reader(repo, tool.EXACT["source_commit"], tool.EXACT["source_tree"], tool.EXACT["parent_source_commit"])
    manifest, inventory = tool.inspect_artifact(archive, manifest_raw, blob_reader=reader)
    assert manifest["source_commit"] == tool.EXACT["source_commit"]
    assert len(inventory["files"]) == 145 and len(inventory["directories"]) == 26
    assert inventory["permanent_files"] == 147 and inventory["permanent_directories"] == 27
    assert len(inventory["code_pins"]) == 7
    assert set(inventory["installed_modes"].values()) == {0o644, 0o755}


@pytest.mark.parametrize("fault", ["manifest", "archive", "source_bytes", "executable_mode"])
def test_actual_artifact_identity_drift_rejected(artifact, tmp_path, fault, historical_artifact_binding):
    archive, manifest_raw, _ = artifact
    reader = tool.git_reader(Path(os.environ["V3_BOOTSTRAP_GIT_REPOSITORY"]), tool.EXACT["source_commit"],
                             tool.EXACT["source_tree"], tool.EXACT["parent_source_commit"])
    if fault == "manifest": manifest_raw += b" "
    if fault == "archive":
        changed = tmp_path / "drift.tar.gz"; changed.write_bytes(archive.read_bytes() + b" "); archive = changed
    original_reader = reader
    if fault == "source_bytes": reader = lambda p: (b"bad", original_reader(p)[1])
    if fault == "executable_mode": reader = lambda p: (original_reader(p)[0], 0o755 if original_reader(p)[1] == 0o644 else 0o644)
    with pytest.raises(tool.Blocked): tool.inspect_artifact(archive, manifest_raw, blob_reader=reader)


@pytest.mark.parametrize("field", ["source", "tree", "parent"])
def test_real_git_identity_wrong_source_tree_parent_rejected(field):
    values = {"source": tool.EXACT["source_commit"], "tree": tool.EXACT["source_tree"], "parent": tool.EXACT["parent_source_commit"]}
    values[field] = "0" * 40
    with pytest.raises((tool.Blocked, subprocess.CalledProcessError)):
        tool.git_reader(Path(os.environ["V3_BOOTSTRAP_GIT_REPOSITORY"]), values["source"], values["tree"], values["parent"])


@pytest.mark.parametrize("reply", ["exact", "refuse", "wrong_pin", "eof"])
def test_tty_attest_real_controlling_pty_flush_fail_closed_and_fd_close(monkeypatch, reply):
    """Actual PTY I/O; only effective-Root identity is a local fixture.

    Calls the exact current function, never seal/reconcile or an authority writer.
    Parent-generated acknowledgements are explicitly synthetic isolated input.
    """
    import errno
    import hashlib
    import pty
    import select
    import signal
    import time

    monkeypatch.setattr(tool.os, "geteuid", lambda: 0)
    grant = "a" * 64
    inputs = {"policy": "b" * 64}
    challenge = "VERIFIED-INDEPENDENT-DECISIONS " + grant + " " + binding.digest(inputs)
    payload = {"exact": (challenge + "\n").encode(), "refuse": b"REFUSED\n",
               "wrong_pin": ("VERIFIED-INDEPENDENT-DECISIONS " + "0" * 64 + " " + binding.digest(inputs) + "\n").encode(),
               "eof": b"\x04"}[reply]
    pid, master = pty.fork()
    if pid == 0:
        before = set(os.listdir("/proc/self/fd"))
        try:
            result = {"success": True, "record": tool.tty_attest(grant, inputs)}
        except Exception as error:
            result = {"success": False, "error": str(error)}
        result["fds_closed"] = set(os.listdir("/proc/self/fd")) == before
        os.write(1, ("FIXTURE_RESULT=" + json.dumps(result) + "\n").encode())
        os._exit(0)
    data, sent, reaped = b"", False, False
    deadline = time.monotonic() + 5
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if not ready:
                continue
            try:
                piece = os.read(master, 8192)
            except OSError as error:
                if error.errno == errno.EIO:
                    break
                raise
            if not piece:
                break
            data += piece
            if not sent and challenge.encode() in data:
                os.write(master, payload)
                sent = True
            if b"FIXTURE_RESULT=" in data and data.endswith(b"\r\n"):
                break
        finish = time.monotonic() + 1
        while time.monotonic() < finish:
            if os.waitpid(pid, os.WNOHANG)[0] == pid:
                reaped = True
                break
            time.sleep(0.01)
        assert reaped, "controlling PTY child timed out"
    finally:
        os.close(master)
        if not reaped:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
    lines = [line for line in data.decode().splitlines() if line.startswith("FIXTURE_RESULT=")]
    assert len(lines) == 1
    observed = json.loads(lines[0].split("=", 1)[1])
    assert sent and observed["fds_closed"]
    if reply == "exact":
        assert observed["success"]
        assert observed["record"]["acknowledgement_sha256"] == hashlib.sha256(challenge.encode()).hexdigest()
        assert observed["record"]["device"] == "/dev/tty"
    else:
        assert not observed["success"]
        assert "INDEPENDENT_HUMAN_VERIFICATION_NOT_ATTESTED" in observed["error"]


def test_tty_attest_missing_controlling_terminal_rejects():
    """A real new session has no /dev/tty; no fake tty file or approval fallback."""
    code = """
from scripts import v3_trusted_record_bootstrap as tool
tool.os.geteuid = lambda: 0  # effective-Root fixture only
try:
    tool.tty_attest('a' * 64, {})
except OSError:
    print('NO_CONTROLLING_TTY_REJECTED')
else:
    raise AssertionError('missing controlling terminal accepted')
"""
    result = subprocess.run([__import__('sys').executable, "-c", code],
                            start_new_session=True, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert result.stdout.strip() == "NO_CONTROLLING_TTY_REJECTED"


def test_tty_attest_output_open_failure_closes_input(monkeypatch):
    import builtins
    from io import StringIO
    stream = StringIO()
    def fail_output(path, mode, **kwargs):
        assert path == "/dev/tty"
        if mode == "r":
            return stream
        raise OSError("isolated output open failure")
    monkeypatch.setattr(tool.os, "geteuid", lambda: 0)
    monkeypatch.setattr(builtins, "open", fail_output)
    with pytest.raises(OSError, match="isolated output open failure"):
        tool.tty_attest("a" * 64, {})
    assert stream.closed
