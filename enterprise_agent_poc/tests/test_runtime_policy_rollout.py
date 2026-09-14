"""Private files + fake services only: no DB, Redis, model or production IO."""
import json
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from scripts import runtime_policy_rollout as policy

TENANT = "zhiy-e-intelligence"
SLUG = "social-content-agent"
CANARY = "CANARY_SECRET_9db87fb2_never_emit"
ORIGINAL = ("# fixture configuration\r\nAPP_ENV=production\r\n"
            "ENTERPRISE_POC_DATABASE_URL='sqlite:///isolated-unused.db'\r\n"
            f"DEEPSEEK_API_KEY='{CANARY}'\r\n"
            "# preserve this comment and spacing\r\nexport CUSTOM_VALUE = 'untouched bytes'\r\n" +
            "\r\n".join(k + "=" + v for k, v in zip(policy.KEYS, ("false", "", "", ""))) + "\r\n").encode()


@pytest.fixture(autouse=True)
def isolated_readiness_clock(monkeypatch):
    monkeypatch.setattr(policy, "READINESS_INTERVAL_SECONDS", 0)


class FakeServices:
    def __init__(self, env):
        self.env = env
        self.identity = "approved-unchanged-application-identity"
        self.environments = [policy.parse(env.read_bytes()) for _ in policy.SERVICES]
        self.calls = []
        self.inactive = set()
        self.fail_once = set()
        self.always_fail = False
        self.health_failure = False
        self.public_health_failure = False
        self.mismatch = False
        self.policy_mismatch = False
        self.cwd_mismatch = False
        self.identity_change = False
        self.restart_cycle = 0
        self.verification_attempt = 0
        self.verification_counts = {"apply": 0, "rollback": 0}
        self.readiness_delays = {"apply": {}, "rollback": {}}
        self.never_ready = {"apply": set(), "rollback": set()}

    def begin_verification(self):
        self.verification_attempt += 1
        phase = self._phase()
        if phase:
            self.verification_counts[phase] += 1

    def _phase(self):
        if self.restart_cycle == 1:
            return "apply"
        if self.restart_cycle >= 2:
            return "rollback"
        return None

    def _ready(self, gate):
        phase = self._phase()
        if phase is None:
            return True
        return gate not in self.never_ready[phase] and self.verification_attempt > self.readiness_delays[phase].get(gate, 0)

    def inspect(self, *, require_active=True):
        policy.require(not require_active or not self.inactive, "SERVICE_INACTIVE")
        if (self.cwd_mismatch and self.restart_cycle and
                policy.parse(self.env.read_bytes()).get(policy.KEYS[0]) == "true"):
            raise policy.Blocked("SERVICE_APPLICATION_MISMATCH")
        return self.identity, self.environments

    def restart(self, service):
        self.calls.append(service)
        if service in self.fail_once or self.always_fail:
            self.fail_once.discard(service)
            self.inactive.add(service)
            if service == policy.SERVICES[-1]:
                self.restart_cycle += 1
                self.verification_attempt = 0
            raise policy.Blocked("SERVICE_RESTART_FAILED")
        self.inactive.discard(service)
        index = policy.SERVICES.index(service)
        values = policy.parse(self.env.read_bytes())
        if self.mismatch and values.get(policy.KEYS[0]) == "true":
            values["APP_ENV"] = "mismatch"
        if self.policy_mismatch and values.get(policy.KEYS[0]) == "true":
            values[policy.KEYS[1]] = "wrong-tenant"
        self.environments[index] = values
        if self.identity_change and values.get(policy.KEYS[0]) == "true":
            self.identity = "changed-application"
        if service == policy.SERVICES[-1]:
            self.restart_cycle += 1
            self.verification_attempt = 0

    def local_health(self):
        return self._ready("local_health") and not (
            self.health_failure and policy.parse(self.env.read_bytes()).get(policy.KEYS[0]) == "true")

    def public_health(self):
        return self._ready("public_health") and not (
            self.public_health_failure and policy.parse(self.env.read_bytes()).get(policy.KEYS[0]) == "true")

    def mcp_ready(self, port=8091):
        assert port == 8091
        return self._ready("mcp_ready")

    def worker_ready(self):
        return self._ready("worker_ready")

    def health(self):
        return self.local_health() and self.public_health()


class NativeLayoutServices:
    """Real SystemdBoundary path/Artifact checks with isolated process fixtures."""

    def __init__(self, base, proc_root, source, env):
        self.native = policy.SystemdBoundary(base, proc_root=proc_root)
        self.source = source
        self.calls = []
        self.pids = {service: str(4100 + index) for index, service in enumerate(policy.SERVICES)}
        self.workdirs = {service: str(source) for service in policy.SERVICES}
        values = policy.parse(env.read_bytes())
        raw = b"\0".join((key + "=" + value).encode() for key, value in values.items()) + b"\0"
        for service, pid in self.pids.items():
            directory = proc_root / pid
            directory.mkdir(parents=True)
            (directory / "cwd").symlink_to(source, target_is_directory=True)
            (directory / "environ").write_bytes(raw)
        self.native._show = self._show

    @property
    def identity_summary(self):
        return self.native.identity_summary

    def _show(self, service):
        return {"MainPID": self.pids[service], "ActiveState": "active",
                "WorkingDirectory": self.workdirs[service]}

    def inspect(self, *, require_active=True):
        return self.native.inspect(require_active=require_active)

    def restart(self, service):
        self.calls.append(service)
        raise AssertionError("read-only native fixture must not restart services")

    def health(self):
        return True

    def local_health(self):
        return True

    def public_health(self):
        return True

    def mcp_ready(self, port=8091):
        assert port == 8091
        return True

    def worker_ready(self):
        return True


@pytest.fixture
def fixture(tmp_path):
    base = tmp_path / "workbench"
    base.mkdir(mode=0o700)
    (base / "runtime-policy-isolated.marker").write_bytes(b"runtime-policy-local-only")
    (base / "shared").mkdir(mode=0o700)
    env = base / "shared/enterprise-agent.env"
    env.write_bytes(ORIGINAL)
    env.chmod(0o600)
    boundary = FakeServices(env)
    rollout = policy.Rollout(base, boundary, isolated=True)
    return rollout, boundary


@pytest.fixture
def native_layout(tmp_path):
    base = tmp_path / "workbench-native"
    base.mkdir(mode=0o700)
    (base / "runtime-policy-isolated.marker").write_bytes(b"runtime-policy-local-only")
    (base / "shared").mkdir(mode=0o700)
    env = base / "shared/enterprise-agent.env"
    env.write_bytes(ORIGINAL)
    env.chmod(0o600)
    releases = base / "releases"
    releases.mkdir(mode=0o700)
    release_id = "20260914-testrelease"
    release_dir = releases / release_id
    source = release_dir / "enterprise_agent_poc"
    (source / "app").mkdir(parents=True)
    (source / "scripts").mkdir()
    files = {
        "enterprise_agent_poc/app/identity.txt": b"native layout identity\n",
        "enterprise_agent_poc/scripts/identity.py": b"IDENTITY = 'test-release'\n",
    }
    for name, content in files.items():
        path = release_dir / name
        path.write_bytes(content)
        path.chmod(0o644)
    source_commit = "a" * 40
    archive = release_dir / f"{release_id}.tar.gz"
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT,
                      pax_headers={"comment": source_commit}) as output:
        for name in files:
            output.add(release_dir / name, arcname=name, recursive=False)
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = {
        "release_id": release_id,
        "source_commit": source_commit,
        "archive_sha256": archive_sha,
        "selected_files": list(files),
        "selected_file_count": len(files),
        "build_platform": "test-native-layout",
    }
    manifest_path = release_dir / f"{release_id}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    (base / "release-current").symlink_to(source, target_is_directory=True)
    proc_root = base / "proc"
    services = NativeLayoutServices(base, proc_root, source, env)
    rollout = policy.Rollout(base, services, isolated=True)
    return {
        "base": base, "release_id": release_id, "release_dir": release_dir,
        "source": source, "archive": archive, "manifest": manifest,
        "manifest_path": manifest_path, "proc_root": proc_root,
        "services": services, "rollout": rollout,
    }


def tree(base):
    return {str(p.relative_to(base)): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
            for p in base.rglob("*") if p.is_file()}


def apply(rollout, tenant=TENANT, slug=SLUG):
    # Test operator submits an explicit expected SHA; drift tests use the earlier plan.
    return rollout.apply(tenant, slug, expected_config_sha256=policy.sha(rollout.env.read_bytes()))


def journal(rollout):
    return rollout._journals()[0]


def test_status_and_plan_are_pure_read_only(fixture):
    rollout, services = fixture
    before = tree(rollout.base)
    assert rollout.status()["policy_enabled"] is False
    assert rollout.plan(TENANT, SLUG)["status"] == "planned"
    assert tree(rollout.base) == before
    assert not rollout.operations.exists() and not services.calls


def test_apply_four_keys_only_backup_and_identity(fixture):
    rollout, services = fixture
    info = rollout.env.stat()
    result = apply(rollout)
    assert result["status"] == "verified"
    values = policy.parse(rollout.env.read_bytes())
    assert {k: values[k] for k in policy.KEYS} == policy.policy_values(TENANT, SLUG)
    after_opaque = [l for l in rollout.env.read_bytes().splitlines(keepends=True)
                    if not any(k.encode() in l for k in policy.KEYS)]
    before_opaque = [l for l in ORIGINAL.splitlines(keepends=True)
                     if not any(k.encode() in l for k in policy.KEYS)]
    assert after_opaque == before_opaque
    record = journal(rollout)
    backup = Path(record["backup_path"])
    assert backup.read_bytes() == ORIGINAL
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert record["pre_sha256"] == policy.sha(ORIGINAL)
    assert record["size"] == len(ORIGINAL)
    assert record["state"] == "verified" and record["timestamp"]
    assert rollout.env.stat().st_uid == info.st_uid
    assert rollout.env.stat().st_gid == info.st_gid
    assert stat.S_IMODE(rollout.env.stat().st_mode) == 0o600
    assert services.calls == list(policy.SERVICES)
    assert services.identity == record["application_identity"]
    assert not any(word in p.name for p in rollout.base.rglob("*")
                   for word in ("database", "workspace", "redis", "registry"))


@pytest.mark.parametrize("suffix", [
    f"{policy.KEYS[0]}=false\n", f"{policy.KEYS[0]}=TRUE\n",
    f"{policy.KEYS[1]}=tenant-a,,tenant-b\n", f"{policy.KEYS[1]}=tenant-a,tenant-a\n",
    f"{policy.KEYS[2]}=invalid_slug\n", f"{policy.KEYS[3]}=../tenant\n",
    "OPAQUE='broken quote\n", "OPAQUE=line\\\nnext\n", "OPAQUE=$(command)\n",
    "OPAQUE=abc;command\n", "OPAQUE=two words\n", "OPAQUE=\"a\"b\"\n"])
def test_parser_fail_closed(fixture, suffix):
    rollout, services = fixture
    if suffix.startswith(tuple(policy.KEYS)) and not suffix.endswith("=false\n"):
        key = suffix.split("=", 1)[0].encode()
        data = b"".join(l for l in ORIGINAL.splitlines(keepends=True) if not l.startswith(key + b"="))
    else:
        data = ORIGINAL
    rollout.env.write_bytes(data + suffix.encode())
    before = tree(rollout.base)
    with pytest.raises(policy.Blocked):
        rollout.plan(TENANT, SLUG)
    assert tree(rollout.base) == before and not services.calls


@pytest.mark.parametrize("tenant,slug", [
    ("", SLUG), ("../tenant", SLUG), ("tenant-a,tenant-b", SLUG),
    (TENANT, ""), (TENANT, "bad_slug"), (TENANT, "slug;command"), (TENANT, "x" * 65)])
def test_target_scope_validation(fixture, tenant, slug):
    rollout, services = fixture
    before = tree(rollout.base)
    with pytest.raises(policy.Blocked):
        apply(rollout, tenant, slug)
    assert tree(rollout.base) == before and not services.calls


@pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink", "mode", "directory_mode", "shared_symlink"])
def test_path_and_permissions_fail_closed(fixture, kind):
    rollout, services = fixture
    if kind == "symlink":
        target = rollout.base / "external"
        target.write_bytes(ORIGINAL)
        target.chmod(0o600)
        rollout.env.unlink()
        rollout.env.symlink_to(target)
    elif kind == "fifo":
        rollout.env.unlink()
        os.mkfifo(rollout.env, 0o600)
    elif kind == "hardlink":
        os.link(rollout.env, rollout.base / "linked")
    elif kind == "mode":
        rollout.env.chmod(0o644)
    elif kind == "directory_mode":
        rollout.env.parent.chmod(0o777)
    else:
        old = rollout.env.parent
        target = rollout.base / "moved-shared"
        old.rename(target)
        old.symlink_to(target, target_is_directory=True)
    with pytest.raises((policy.Blocked, OSError)):
        rollout.status()
    assert not services.calls


def test_apply_cas_keeps_independent_edit(fixture, monkeypatch):
    rollout, services = fixture
    real = rollout._atomic
    external = ORIGINAL + b"# external concurrent edit\n"
    def atomic(path, data, metadata, expected=None, code="CONFIG_CONCURRENT_MODIFICATION"):
        if path == rollout.env:
            rollout.env.write_bytes(external)
        return real(path, data, metadata, expected, code)
    monkeypatch.setattr(rollout, "_atomic", atomic)
    with pytest.raises(policy.Blocked, match="CONFIG_CONCURRENT_MODIFICATION"):
        apply(rollout)
    assert rollout.env.read_bytes() == external and not services.calls
    assert journal(rollout)["state"] == "prepared"
    with pytest.raises(policy.Blocked, match="UNFINISHED_OPERATION"):
        rollout.status()


def test_restore_cas_keeps_independent_edit(fixture):
    rollout, services = fixture
    result = apply(rollout)
    external = rollout.env.read_bytes() + b"# independent edit\n"
    rollout.env.write_bytes(external)
    before = list(services.calls)
    with pytest.raises(policy.Blocked, match="RESTORE_CONCURRENT_MODIFICATION"):
        rollout.restore(result["operation_id"])
    assert rollout.env.read_bytes() == external and services.calls == before


def test_inode_replacement_same_bytes_still_blocks(fixture, monkeypatch):
    rollout, _ = fixture
    original = rollout._read(rollout.env)
    alternate = rollout.env.parent / "independent"
    alternate.write_bytes(ORIGINAL)
    alternate.chmod(0o600)
    os.replace(alternate, rollout.env)
    with pytest.raises(policy.Blocked, match="CONFIG_CONCURRENT_MODIFICATION"):
        rollout._atomic(rollout.env, b"different", original, original)
    assert rollout.env.read_bytes() == ORIGINAL


def test_atomic_same_directory_and_fsync(fixture, monkeypatch):
    rollout, _ = fixture
    replaces, fsyncs = [], []
    real_replace, real_sync = os.replace, os.fsync
    def replace(src, dst):
        assert Path(src).parent == Path(dst).parent
        assert Path(src).stat().st_mode & 0o077 == 0
        replaces.append(Path(dst))
        return real_replace(src, dst)
    def sync(fd):
        fsyncs.append(os.fstat(fd).st_mode)
        return real_sync(fd)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "fsync", sync)
    apply(rollout)
    assert rollout.env in replaces
    assert any(stat.S_ISDIR(m) for m in fsyncs) and any(stat.S_ISREG(m) for m in fsyncs)
    assert not list(rollout.env.parent.glob(".runtime-policy-*"))


@pytest.mark.parametrize("service", policy.SERVICES)
def test_each_restart_failure_exact_restore_even_when_service_inactive(fixture, service):
    rollout, services = fixture
    services.fail_once.add(service)
    with pytest.raises(policy.Blocked, match="POLICY_ROLLOUT_FAILED_ROLLED_BACK"):
        apply(rollout)
    assert rollout.env.read_bytes() == ORIGINAL
    assert journal(rollout)["state"] == "rolled_back"
    assert services.calls == list(policy.SERVICES) * 2
    assert not services.inactive
    assert rollout.status()["policy_enabled"] is False


@pytest.mark.parametrize("failure", ["health_failure", "mismatch"])
def test_health_or_fingerprint_failure_restores_exact_bytes(fixture, failure):
    rollout, services = fixture
    setattr(services, failure, True)
    with pytest.raises(policy.Blocked, match="POLICY_ROLLOUT_FAILED_ROLLED_BACK"):
        apply(rollout)
    assert rollout.env.read_bytes() == ORIGINAL
    assert journal(rollout)["state"] == "rolled_back"
    assert rollout.status()["policy_enabled"] is False


@pytest.mark.parametrize("gate,delay", [
    ("local_health", 2),
    ("mcp_ready", 3),
    ("worker_ready", 4),
    ("public_health", 2),
])
def test_delayed_service_readiness_eventually_applies(fixture, gate, delay):
    rollout, services = fixture
    services.readiness_delays["apply"][gate] = delay
    result = apply(rollout)
    assert result["status"] == "verified"
    assert result["gates"][gate] is True
    assert journal(rollout)["state"] == "verified"


def test_api_connection_refused_then_recovers(fixture):
    rollout, services = fixture
    services.readiness_delays["apply"]["local_health"] = 3
    assert apply(rollout)["status"] == "verified"


def test_services_become_ready_at_different_attempts(fixture):
    rollout, services = fixture
    services.readiness_delays["apply"].update(
        local_health=1, public_health=3, mcp_ready=2, worker_ready=4)
    result = apply(rollout)
    assert result["status"] == "verified"
    assert services.verification_attempt == 5


@pytest.mark.parametrize("gate,stage", [
    ("local_health", "local_health"),
    ("worker_ready", "worker_ready"),
])
def test_readiness_timeout_rolls_back_and_records_stage(fixture, gate, stage):
    rollout, services = fixture
    services.never_ready["apply"].add(gate)
    with pytest.raises(policy.Blocked, match="POLICY_ROLLOUT_FAILED_ROLLED_BACK"):
        apply(rollout)
    record = journal(rollout)
    assert record["state"] == "rolled_back"
    assert record["original_failure_stage"] == stage
    assert record["original_failure_code"] == "SERVICE_READINESS_TIMEOUT"
    assert record["rollback_failure_stage"] is None
    assert rollout.env.read_bytes() == ORIGINAL
    assert services.verification_counts["apply"] == policy.READINESS_MAX_ATTEMPTS


@pytest.mark.parametrize("failure,stage,code,cli_code", [
    ("cwd_mismatch", "application_identity", "SERVICE_APPLICATION_MISMATCH",
     "CRITICAL_CONFIG_ROLLBACK_FAILED"),
    ("mismatch", "process_fingerprint", "CONFIG_FINGERPRINT_MISMATCH",
     "POLICY_ROLLOUT_FAILED_ROLLED_BACK"),
    ("policy_mismatch", "policy_semantics", "POLICY_SEMANTICS_MISMATCH",
     "POLICY_ROLLOUT_FAILED_ROLLED_BACK"),
])
def test_non_retryable_verification_failure_blocks_immediately(fixture, failure, stage, code, cli_code):
    rollout, services = fixture
    setattr(services, failure, True)
    with pytest.raises(policy.Blocked, match=cli_code):
        apply(rollout)
    record = journal(rollout)
    assert record["state"] == ("rollback_failed" if failure == "cwd_mismatch" else "rolled_back")
    assert record["original_failure_stage"] == stage
    assert record["original_failure_code"] == code
    assert services.verification_counts["apply"] == 1


def test_apply_failure_exact_restore_waits_for_delayed_recovery(fixture):
    rollout, services = fixture
    services.mismatch = True
    services.readiness_delays["rollback"].update(local_health=2, worker_ready=3)
    with pytest.raises(policy.Blocked, match="POLICY_ROLLOUT_FAILED_ROLLED_BACK"):
        apply(rollout)
    record = journal(rollout)
    assert rollout.env.read_bytes() == ORIGINAL
    assert record["state"] == "rolled_back"
    assert record["original_failure_stage"] == "process_fingerprint"
    assert record["rollback_failure_stage"] is None
    assert [item["state"] for item in record["transition_timestamps"]][-1] == "rolled_back"


def test_rollback_readiness_timeout_is_critical_and_auditable(fixture):
    rollout, services = fixture
    services.mismatch = True
    services.never_ready["rollback"].add("worker_ready")
    with pytest.raises(policy.Blocked, match="CRITICAL_CONFIG_ROLLBACK_FAILED"):
        apply(rollout)
    record = journal(rollout)
    assert rollout.env.read_bytes() == ORIGINAL
    assert record["state"] == "rollback_failed"
    assert record["original_failure_stage"] == "process_fingerprint"
    assert record["original_failure_code"] == "CONFIG_FINGERPRINT_MISMATCH"
    assert record["rollback_failure_stage"] == "worker_ready"
    assert record["rollback_failure_code"] == "SERVICE_READINESS_TIMEOUT"


def test_legacy_rollback_failed_current_pre_recovers_without_env_replace(fixture, monkeypatch):
    rollout, services = fixture
    services.always_fail = True
    with pytest.raises(policy.Blocked, match="CRITICAL_CONFIG_ROLLBACK_FAILED"):
        apply(rollout)
    record = journal(rollout)
    path = rollout.operations / (record["operation_id"] + ".json")
    legacy = {key: value for key, value in record.items() if key in policy.LEGACY_JOURNAL_FIELDS}
    legacy["state"] = "rollback_failed"
    path.write_bytes(json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode())
    path.chmod(0o600)
    assert journal(rollout).get("original_failure_stage", "unavailable") == "unavailable"
    before = rollout.env.read_bytes(), rollout.env.stat().st_ino, rollout.env.stat().st_mtime_ns
    real_atomic, env_replacements = rollout._atomic, []

    def atomic(path, *args, **kwargs):
        if path == rollout.env:
            env_replacements.append(path)
        return real_atomic(path, *args, **kwargs)

    monkeypatch.setattr(rollout, "_atomic", atomic)
    services.always_fail = False
    result = rollout.restore(record["operation_id"])
    after = rollout.env.read_bytes(), rollout.env.stat().st_ino, rollout.env.stat().st_mtime_ns
    recovered = journal(rollout)
    assert result["status"] == "rolled_back" and result["config_rewritten"] is False
    assert before == after and not env_replacements
    assert len(list(rollout.operations.glob("*.backup"))) == 1
    assert recovered["state"] == "rolled_back" and recovered["journal_version"] == 2
    assert recovered["original_failure_stage"] is None
    assert recovered["rollback_failure_stage"] is None


def test_systemd_boundary_mcp_readiness_uses_expected_port(monkeypatch, tmp_path):
    boundary = policy.SystemdBoundary(tmp_path)
    calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(policy.socket, "create_connection",
                        lambda address, timeout: calls.append((address, timeout)) or Connection())
    assert boundary.mcp_ready(8123) is True
    assert calls == [(('127.0.0.1', 8123), 2)]


def test_systemd_boundary_worker_ready_is_current_pid_scoped(monkeypatch, tmp_path):
    boundary = policy.SystemdBoundary(tmp_path)
    boundary.service_pids["enterprise-agent-worker.service"] = "4321"
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"worker ready\n")

    monkeypatch.setattr(policy.subprocess, "run", run)
    assert boundary.worker_ready() is True
    args, kwargs = calls[0]
    assert "_SYSTEMD_UNIT=enterprise-agent-worker.service" in args
    assert "_PID=4321" in args and "--grep=worker ready" in args
    assert kwargs["capture_output"] is True and kwargs["timeout"] == 3


@pytest.mark.parametrize("status,retryable", [(503, True), (401, False), (404, False)])
def test_systemd_boundary_health_retries_only_transient_http(monkeypatch, tmp_path, status, retryable):
    boundary = policy.SystemdBoundary(tmp_path)

    def open_url(*args, **kwargs):
        raise policy.urllib.error.HTTPError("redacted", status, "redacted", None, None)

    monkeypatch.setattr(policy.urllib.request, "urlopen", open_url)
    if retryable:
        assert boundary.local_health() is False
    else:
        with pytest.raises(policy.Blocked, match="LOCAL_HEALTH_HTTP_FAILED"):
            boundary.local_health()


def test_systemd_boundary_mcp_permission_error_is_not_retryable(monkeypatch, tmp_path):
    boundary = policy.SystemdBoundary(tmp_path)

    def connect(*args, **kwargs):
        raise PermissionError(policy.errno.EACCES, "redacted")

    monkeypatch.setattr(policy.socket, "create_connection", connect)
    with pytest.raises(policy.Blocked, match="MCP_READINESS_QUERY_FAILED"):
        boundary.mcp_ready()


@pytest.mark.parametrize("field,value", [
    ("original_failure_stage", "free-form secret text"),
    ("original_failure_code", CANARY),
    ("original_failure_at", "not-a-timestamp"),
])
def test_v2_journal_failure_fields_remain_strict(fixture, field, value):
    rollout, _ = fixture
    apply(rollout)
    record = journal(rollout)
    record["original_failure_stage"] = "local_health"
    record["original_failure_code"] = "LOCAL_HEALTH_NOT_READY"
    record["original_failure_at"] = policy.utc_now()
    record[field] = value
    path = rollout.operations / (record["operation_id"] + ".json")
    path.write_bytes(json.dumps(record, sort_keys=True, separators=(",", ":")).encode())
    path.chmod(0o600)
    with pytest.raises(policy.Blocked, match="INVALID_JOURNAL"):
        rollout._journals()


def test_critical_rollback_failure_and_explicit_recovery(fixture):
    rollout, services = fixture
    services.always_fail = True
    with pytest.raises(policy.Blocked, match="CRITICAL_CONFIG_ROLLBACK_FAILED"):
        apply(rollout)
    record = journal(rollout)
    assert record["state"] == "rollback_failed"
    assert rollout.env.read_bytes() == ORIGINAL
    with pytest.raises(policy.Blocked, match="UNFINISHED_OPERATION"):
        apply(rollout)
    services.always_fail = False
    assert rollout.restore(record["operation_id"])["status"] == "rolled_back"
    assert rollout.status()["policy_enabled"] is False


def test_manual_restore_and_duplicate_no_restart(fixture):
    rollout, services = fixture
    result = apply(rollout)
    assert rollout.restore(result["operation_id"])["status"] == "rolled_back"
    assert rollout.env.read_bytes() == ORIGINAL
    before = tree(rollout.base), list(services.calls)
    assert rollout.restore(result["operation_id"])["status"] == "already_restored"
    assert before == (tree(rollout.base), services.calls)


def test_duplicate_apply_verifies_without_restart(fixture):
    rollout, services = fixture
    apply(rollout)
    before = tree(rollout.base), list(services.calls)
    assert apply(rollout)["status"] == "already_applied"
    assert before == (tree(rollout.base), services.calls)
    services.environments[0]["APP_ENV"] = "unexpected"
    with pytest.raises(policy.Blocked, match="CONFIG_FINGERPRINT_MISMATCH"):
        apply(rollout)
    assert before[1] == services.calls


@pytest.mark.parametrize("state", ["prepared", "config_replaced", "services_restarting", "rollback_failed"])
def test_unfinished_journal_blocks_status_plan_apply(fixture, state):
    rollout, services = fixture
    result = apply(rollout)
    record = journal(rollout)
    rollout._write_journal(record, state)
    before = tree(rollout.base), list(services.calls)
    for action in (rollout.status, lambda: rollout.plan(TENANT, SLUG), lambda: apply(rollout)):
        with pytest.raises(policy.Blocked, match="UNFINISHED_OPERATION"):
            action()
    assert before == (tree(rollout.base), services.calls)
    assert rollout.restore(result["operation_id"])["status"] == "rolled_back"


def test_secret_never_output_or_journal(fixture, capsys):
    rollout, services = fixture
    factory = lambda: rollout
    assert policy.main(["status"], factory=factory) == 0
    assert policy.main(["plan", "--tenant", TENANT, "--slug", SLUG], factory=factory) == 0
    assert policy.main(["apply", "--tenant", TENANT, "--slug", SLUG, "--expected-config-sha256", policy.sha(rollout.env.read_bytes())], factory=factory) == 0
    result = capsys.readouterr()
    assert CANARY not in result.out + result.err
    assert CANARY not in json.dumps(rollout._journals())
    assert CANARY.encode() in rollout.env.read_bytes()
    services.always_fail = True
    assert policy.main(["restore", "--operation-id", journal(rollout)["operation_id"]], factory=factory) == 3
    result = capsys.readouterr()
    assert CANARY not in result.out + result.err + json.dumps(rollout._journals())


def test_application_change_blocks_restore_without_switching(fixture):
    rollout, services = fixture
    result = apply(rollout)
    services.identity = "external-release-switch"
    before = rollout.env.read_bytes(), list(services.calls)
    with pytest.raises(policy.Blocked, match="APPLICATION_IDENTITY_CHANGED"):
        rollout.restore(result["operation_id"])
    assert before == (rollout.env.read_bytes(), services.calls)
    assert services.identity == "external-release-switch"


def test_backup_corruption_blocks_restore(fixture):
    rollout, services = fixture
    result = apply(rollout)
    Path(journal(rollout)["backup_path"]).write_bytes(b"corrupted")
    before = list(services.calls)
    with pytest.raises(policy.Blocked, match="BACKUP_IDENTITY_MISMATCH"):
        rollout.restore(result["operation_id"])
    assert services.calls == before


def test_no_uncontrolled_root_or_production_fake_boundary(fixture):
    rollout, services = fixture
    with pytest.raises(policy.Blocked, match="PRODUCTION_ROOT_REQUIRED"):
        policy.Rollout(rollout.base, services)
    (rollout.base / "runtime-policy-isolated.marker").unlink()
    with pytest.raises(policy.Blocked, match="INVALID_TEST_MARKER"):
        policy.Rollout(rollout.base, services, isolated=True)


def test_cli_has_no_env_path_key_force_or_ignore(fixture):
    rollout, _ = fixture
    for arg in ("--env-file", "--root", "--key", "--force", "--ignore"):
        with pytest.raises(SystemExit):
            policy.main(["status", arg, "anything"], factory=lambda: rollout)


def test_pure_fingerprint_matches_settings_contract():
    from app.settings import RUNTIME_CONFIG_ENV_NAMES, safe_runtime_config_snapshot
    from scripts.verify_runtime_config import RUNTIME_CONFIG_ENV_NAMES as pure_names
    assert RUNTIME_CONFIG_ENV_NAMES == pure_names
    for values in ({}, policy.parse(ORIGINAL), policy.policy_values(TENANT, SLUG)):
        assert safe_runtime_config_snapshot(values) == policy.safe_runtime_config_snapshot(values)


def test_standalone_import_does_not_import_settings_or_touch_database():
    root = Path(__file__).resolve().parents[1]
    code = "from scripts import runtime_policy_rollout; import sys; assert 'app.settings' not in sys.modules; assert 'app.product_store' not in sys.modules"
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True,
                            env={"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0 and not result.stdout and not result.stderr


def test_missing_keys_seed_policy_without_rewriting_opaque_bytes(fixture):
    rollout, services = fixture
    data = b"# exact original prefix\nAPP_ENV=production\nOPAQUE='unchanged'\n"
    rollout.env.write_bytes(data)
    services.environments = [policy.parse(data) for _ in policy.SERVICES]
    apply(rollout)
    assert rollout.env.read_bytes().startswith(data)


def test_new_application_while_rollout_is_critical_not_false_success(fixture):
    rollout, services = fixture
    services.identity_change = True
    with pytest.raises(policy.Blocked, match="CRITICAL_CONFIG_ROLLBACK_FAILED"):
        apply(rollout)
    assert journal(rollout)["state"] == "rollback_failed"


def test_all_three_process_fingerprints_are_reported(fixture):
    rollout, _ = fixture
    gates = apply(rollout)["gates"]
    assert set(gates["process_fingerprints"]) == set(policy.SERVICES)
    assert set(gates["process_fingerprints"].values()) == {gates["shared_fingerprint"]}
    assert gates["config_matches"] and gates["policy_matches"] and gates["health_verified"]


def test_interrupt_after_replace_requires_explicit_restore(fixture, monkeypatch):
    rollout, services = fixture
    real = rollout._restart_verify
    def interrupted(*args):
        raise KeyboardInterrupt
    monkeypatch.setattr(rollout, "_restart_verify", interrupted)
    with pytest.raises(KeyboardInterrupt):
        apply(rollout)
    record = journal(rollout)
    assert record["state"] == "services_restarting" and rollout.env.read_bytes() != ORIGINAL
    assert not services.calls
    with pytest.raises(policy.Blocked, match="UNFINISHED_OPERATION"):
        rollout.plan(TENANT, SLUG)
    monkeypatch.setattr(rollout, "_restart_verify", real)
    assert rollout.restore(record["operation_id"])["status"] == "rolled_back"
    assert rollout.env.read_bytes() == ORIGINAL


def test_prepared_before_replace_can_explicitly_restore(fixture, monkeypatch):
    rollout, _ = fixture
    real = rollout._atomic
    def interrupted(path, *args, **kwargs):
        if path == rollout.env:
            raise KeyboardInterrupt
        return real(path, *args, **kwargs)
    monkeypatch.setattr(rollout, "_atomic", interrupted)
    with pytest.raises(KeyboardInterrupt):
        apply(rollout)
    record = journal(rollout)
    assert record["state"] == "prepared" and rollout.env.read_bytes() == ORIGINAL
    monkeypatch.setattr(rollout, "_atomic", real)
    assert rollout.restore(record["operation_id"])["status"] == "rolled_back"


def test_original_read_only_mode_restored(fixture):
    rollout, _ = fixture
    rollout.env.chmod(0o400)
    result = apply(rollout)
    assert stat.S_IMODE(rollout.env.stat().st_mode) == 0o400
    assert rollout.restore(result["operation_id"])["exact_bytes_restored"]
    assert stat.S_IMODE(rollout.env.stat().st_mode) == 0o400


def test_replaces_existing_scope_instead_of_appending(fixture):
    rollout, services = fixture
    original = ORIGINAL.replace((policy.KEYS[1] + "=\r\n").encode(),
                                (policy.KEYS[1] + "=other-tenant\r\n").encode())
    rollout.env.write_bytes(original)
    services.environments = [policy.parse(original) for _ in policy.SERVICES]
    apply(rollout)
    assert policy.parse(rollout.env.read_bytes())[policy.KEYS[1]] == TENANT


def test_parent_identity_change_even_with_same_file_inode_blocks(fixture):
    rollout, _ = fixture
    original = rollout._read(rollout.env)
    old = rollout.base / "old-shared"
    rollout.env.parent.rename(old)
    rollout.env.parent.mkdir(mode=0o700)
    (old / rollout.env.name).rename(rollout.env)
    assert rollout.env.stat().st_ino == original.inode
    with pytest.raises(policy.Blocked, match="CONFIG_CONCURRENT_MODIFICATION"):
        rollout._atomic(rollout.env, b"replacement", original, original)
    assert rollout.env.read_bytes() == ORIGINAL


def test_plan_to_apply_config_change_blocks_without_side_effects(fixture):
    rollout, services = fixture
    planned = rollout.plan(TENANT, SLUG)
    external = ORIGINAL + b"# change between commands\n"
    rollout.env.write_bytes(external)
    before = tree(rollout.base)
    with pytest.raises(policy.Blocked, match="CONFIG_CONCURRENT_MODIFICATION"):
        rollout.apply(TENANT, SLUG, expected_config_sha256=planned["config_sha256"])
    assert tree(rollout.base) == before and not services.calls


def test_cli_apply_requires_plan_sha(fixture):
    rollout, _ = fixture
    with pytest.raises(SystemExit):
        policy.main(["apply", "--tenant", TENANT, "--slug", SLUG], factory=lambda: rollout)


def test_invalid_cli_argument_does_not_echo_canary(fixture, capsys):
    rollout, _ = fixture
    with pytest.raises(SystemExit):
        policy.main(["status", "--unknown", CANARY], factory=lambda: rollout)
    result = capsys.readouterr()
    assert CANARY not in result.out + result.err


def test_interrupt_during_manual_restore_is_recoverable(fixture, monkeypatch):
    rollout, _ = fixture
    operation = apply(rollout)["operation_id"]
    real = rollout._restart_verify
    def interrupted(*args):
        raise KeyboardInterrupt
    monkeypatch.setattr(rollout, "_restart_verify", interrupted)
    with pytest.raises(KeyboardInterrupt):
        rollout.restore(operation)
    assert journal(rollout)["state"] == "services_restarting"
    assert rollout.env.read_bytes() == ORIGINAL
    with pytest.raises(policy.Blocked, match="UNFINISHED_OPERATION"):
        rollout.status()
    monkeypatch.setattr(rollout, "_restart_verify", real)
    assert rollout.restore(operation)["status"] == "rolled_back"


def _repoint(path, target):
    path.unlink()
    path.symlink_to(target, target_is_directory=True)


def _rewrite_manifest(case, **changes):
    value = dict(case["manifest"])
    value.update(changes)
    case["manifest_path"].write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def test_native_release_layout_status_passes_read_only(native_layout):
    case = native_layout
    before = tree(case["base"])
    result = case["rollout"].status()
    assert result["status"] == "verified"
    assert result["application"] == {
        "release_id": case["release_id"],
        "source_directory": "enterprise_agent_poc",
        "source_commit": "a" * 40,
        "archive_sha256": case["manifest"]["archive_sha256"],
        "manifest_sha256": policy.digest(case["manifest"]),
        "selected_file_count": 2,
        "build_platform": "test-native-layout",
    }
    assert result["gates"]["service_states"] == dict.fromkeys(policy.SERVICES, "active")
    assert result["gates"]["health_verified"] is True
    assert tree(case["base"]) == before
    assert not case["services"].calls and not case["rollout"].operations.exists()


def test_native_release_layout_status_output_is_secret_free(native_layout, capsys):
    rollout = native_layout["rollout"]
    assert policy.main(["status"], factory=lambda: rollout) == 0
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload["application"]["release_id"] == native_layout["release_id"]
    assert payload["application"]["source_commit"] == "a" * 40
    assert CANARY not in output.out + output.err
    assert str(rollout.env) not in output.out


def test_native_release_layout_plan_reports_exact_safe_diff(native_layout, capsys):
    case = native_layout
    before = tree(case["base"])
    assert policy.main(["plan", "--tenant", TENANT, "--slug", SLUG],
                       factory=lambda: case["rollout"]) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert CANARY not in output.out + output.err
    assert str(case["rollout"].env) not in output.out
    rendered = policy.replace_policy(ORIGINAL, policy.policy_values(TENANT, SLUG))
    assert result["status"] == "planned"
    assert result["original_sha256"] == policy.sha(ORIGINAL) == result["config_sha256"]
    assert result["predicted_sha256"] == policy.sha(rendered)
    assert result["current_policy"] == {
        "enabled": False, "allowed_tenant_count": 0, "allowed_slug_count": 0,
        "runtime_test_tenant_configured": False,
    }
    assert result["planned_policy"] == {
        "enabled": True, "allowed_tenant_count": 1, "allowed_slug_count": 1,
        "runtime_test_tenant_configured": True,
    }
    assert result["four_field_diff"] == [
        {"key": key, "changed": True} for key in policy.KEYS
    ]
    assert result["non_target_bytes_preserved"] is True
    assert tree(case["base"]) == before and not case["services"].calls


@pytest.mark.parametrize("mutation", [
    "release_current_to_release_dir",
    "source_directly_in_releases",
    "nested_source",
    "wrong_source_basename",
    "source_symlink_escape",
    "wrong_release_parent",
    "missing_manifest",
    "manifest_in_source",
    "manifest_wrong_parent",
    "manifest_symlink",
    "release_id_mismatch",
    "unsafe_build_platform",
    "source_commit_mismatch",
    "manifest_archive_identity_mismatch",
    "archive_checksum_mismatch",
    "archive_symlink",
    "source_byte_tamper",
    "source_mode_tamper",
    "unmanifested_source_file",
    "missing_source_file",
])
def test_native_release_identity_mutations_block_read_only(native_layout, mutation):
    case = native_layout
    base, release_dir, source = case["base"], case["release_dir"], case["source"]
    if mutation == "release_current_to_release_dir":
        _repoint(base / "release-current", release_dir)
    elif mutation == "source_directly_in_releases":
        flat_source = release_dir.parent / source.name
        source.rename(flat_source)
        _repoint(base / "release-current", flat_source)
    elif mutation == "nested_source":
        nested = release_dir / "nested"
        nested.mkdir()
        nested_source = nested / source.name
        source.rename(nested_source)
        _repoint(base / "release-current", nested_source)
    elif mutation == "wrong_source_basename":
        wrong_source = release_dir / "workbench_app"
        source.rename(wrong_source)
        _repoint(base / "release-current", wrong_source)
    elif mutation == "source_symlink_escape":
        outside = base / "outside-source"
        source.rename(outside)
        source.symlink_to(outside, target_is_directory=True)
    elif mutation == "wrong_release_parent":
        outside = base / "outside-releases"
        outside.mkdir()
        moved = outside / case["release_id"]
        release_dir.rename(moved)
        _repoint(base / "release-current", moved / source.name)
    elif mutation == "missing_manifest":
        case["manifest_path"].unlink()
    elif mutation == "manifest_in_source":
        case["manifest_path"].rename(source / case["manifest_path"].name)
    elif mutation == "manifest_wrong_parent":
        case["manifest_path"].rename(release_dir.parent / case["manifest_path"].name)
    elif mutation == "manifest_symlink":
        outside = base / "outside-manifest.json"
        case["manifest_path"].rename(outside)
        case["manifest_path"].symlink_to(outside)
    elif mutation == "release_id_mismatch":
        _rewrite_manifest(case, release_id="20260914-other")
    elif mutation == "unsafe_build_platform":
        _rewrite_manifest(case, build_platform=CANARY + " secret")
    elif mutation == "source_commit_mismatch":
        _rewrite_manifest(case, source_commit="b" * 40)
    elif mutation == "manifest_archive_identity_mismatch":
        _rewrite_manifest(case, archive_sha256="0" * 64)
    elif mutation == "archive_checksum_mismatch":
        case["archive"].write_bytes(case["archive"].read_bytes() + b"tamper")
    elif mutation == "archive_symlink":
        outside = base / "outside-archive.tar.gz"
        case["archive"].rename(outside)
        case["archive"].symlink_to(outside)
    elif mutation == "source_byte_tamper":
        path = source / "app/identity.txt"
        path.write_bytes(path.read_bytes() + b"tamper\n")
    elif mutation == "source_mode_tamper":
        (source / "scripts/identity.py").chmod(0o755)
    elif mutation == "unmanifested_source_file":
        (source / "app/unmanifested.txt").write_text("not approved\n", encoding="utf-8")
    else:
        (source / "app/identity.txt").unlink()
    before = tree(base)
    with pytest.raises(policy.Blocked, match="APPLICATION_IDENTITY_INVALID"):
        case["rollout"].status()
    assert tree(base) == before and not case["services"].calls


def test_native_release_process_cwd_mismatch_blocks(native_layout):
    case = native_layout
    service = policy.SERVICES[0]
    cwd = case["proc_root"] / case["services"].pids[service] / "cwd"
    _repoint(cwd, case["release_dir"])
    with pytest.raises(policy.Blocked, match="SERVICE_APPLICATION_MISMATCH"):
        case["rollout"].status()
    assert not case["services"].calls


def test_native_release_systemd_working_directory_mismatch_blocks(native_layout):
    case = native_layout
    case["services"].workdirs[policy.SERVICES[1]] = str(case["release_dir"])
    with pytest.raises(policy.Blocked, match="SERVICE_APPLICATION_MISMATCH"):
        case["rollout"].status()
    assert not case["services"].calls


def test_native_release_missing_working_directory_blocks(native_layout):
    case = native_layout
    case["services"].workdirs[policy.SERVICES[2]] = ""
    with pytest.raises(policy.Blocked, match="SERVICE_APPLICATION_MISMATCH"):
        case["rollout"].status()
    assert not case["services"].calls
