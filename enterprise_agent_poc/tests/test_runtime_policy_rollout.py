"""Private files + fake services only: no DB, Redis, model or production IO."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

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
        self.mismatch = False
        self.identity_change = False

    def inspect(self, *, require_active=True):
        policy.require(not require_active or not self.inactive, "SERVICE_INACTIVE")
        return self.identity, self.environments

    def restart(self, service):
        self.calls.append(service)
        if service in self.fail_once or self.always_fail:
            self.fail_once.discard(service)
            self.inactive.add(service)
            raise policy.Blocked("SERVICE_RESTART_FAILED")
        self.inactive.discard(service)
        index = policy.SERVICES.index(service)
        values = policy.parse(self.env.read_bytes())
        if self.mismatch and values.get(policy.KEYS[0]) == "true":
            values["APP_ENV"] = "mismatch"
        self.environments[index] = values
        if self.identity_change and values.get(policy.KEYS[0]) == "true":
            self.identity = "changed-application"

    def health(self):
        return not (self.health_failure and policy.parse(self.env.read_bytes()).get(policy.KEYS[0]) == "true")


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
