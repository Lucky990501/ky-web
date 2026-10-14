"""Isolated engineering tests only: no SSH, live PG, systemd or Provider."""
import ast
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import types

import pytest

HERE = Path(__file__).parent


def module(name, path):
    value = types.ModuleType(name)
    value.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), value.__dict__)
    return value


@pytest.fixture
def c():
    return module("test_only_entry", HERE / "entry.py")


@pytest.fixture
def authority_fixture(c, monkeypatch, tmp_path):
    control = tmp_path / "control"; control.mkdir()
    (control / "entry.py").write_bytes((HERE / "entry.py").read_bytes())
    (control / "existing_recovery_bound.py").write_bytes((HERE / "existing_recovery_bound.py").read_bytes())
    python = tmp_path / "python"; python.write_bytes(b"trusted-runtime")
    value = {"contract": c.CONTRACT, "host": c.HOST, "source": c.SOURCE, "tree": c.TREE,
             "git_parent": c.GIT_PARENT, "runtime_predecessor_source": c.OLD_SOURCE,
             "runtime_predecessor_tree": c.OLD_TREE, "environment": "test", "production_authority": False,
             "operator": "06_CODEX_AUTHORIZED_LUCKY_SUDO",
             "actions": ["install", "backup", "restore-rehearsal", "activate", "recover"],
             "owner_decision_reference": "ISOLATED_FAKE_NO_AUTHORITY",
             "technical_decision_reference": "ISOLATED_FAKE_NO_AUTHORITY", "issued_at": 1, "expires_at": 14401,
             "revoked": False, "automatic_retry": False, "provider_budget": 0,
             "migration_action": "NONE", "restore_exact_test_data": True,
             "code_sha256": {name: c.sha(control / name) for name in ("entry.py", "existing_recovery_bound.py")},
             "python_sha256": c.sha(python), "python_real_path": str(python), "restore_reserve_seconds": 3600}
    monkeypatch.setattr(c, "CONTROL", control); monkeypatch.setattr(c, "PYTHON", python)
    # Test files owned UID1000 cannot be real Root authority; trust IO is stubbed
    # explicitly, while all consumer fields / reads / mutation-order assertions run.
    monkeypatch.setattr(c, "native", lambda *a, **k: None)
    # Explicit non-authoritative fixture bypasses only native ownership IO.
    monkeypatch.setattr(c, "trusted_python", lambda v: c.need(c.sha(python) == v["python_sha256"], "PYTHON_PIN"))
    def write():
        (control / "scope.approved.v1.json").write_text(json.dumps(value))
    write()
    return value, write, control


def test_exact_authority(c, authority_fixture):
    assert c.authority("install", clock=lambda: 2)["source"] == c.SOURCE


@pytest.mark.parametrize("field,value", [
    ("source", "d11c2fb5ddb6203eb5cee23832fafec47a2781f0"), ("tree", "0" * 40),
    ("git_parent", "0" * 40), ("runtime_predecessor_source", "0" * 40),
    ("host", "iv-yekqs6ez9cnu262hhsg0"), ("environment", "production"),
    ("production_authority", True), ("operator", "root-label-is-not-grant"),
    ("issued_at", None), ("expires_at", 1), ("revoked", True),
    ("automatic_retry", True), ("provider_budget", 1), ("migration_action", "UP"),
    ("restore_exact_test_data", False), ("owner_decision_reference", None),
    ("technical_decision_reference", ""), ("restore_reserve_seconds", 0),
])
def test_bad_authority_rejected(c, authority_fixture, field, value):
    doc, write, _ = authority_fixture; doc[field] = value; write()
    with pytest.raises(c.Blocked): c.authority("install", clock=lambda: 2)


def test_fresh_revocation_after_lookup(c, authority_fixture):
    doc, write, control = authority_fixture
    assert c.authority("recover", clock=lambda: 2)
    (control / "REVOKED").touch()
    with pytest.raises(c.Blocked, match="AUTHORITY_REVOKED"):
        c.authority("recover", clock=lambda: 3)


def test_recovery_allowed_in_reserve_not_new_install(c, authority_fixture):
    assert c.authority("recover", clock=lambda: 14399)
    with pytest.raises(c.Blocked, match="RESTORE_RESERVE"):
        c.authority("install", clock=lambda: 14399)


def test_expired_recovery_refused(c, authority_fixture):
    with pytest.raises(c.Blocked, match="EXPIRED"):
        c.authority("recover", clock=lambda: 14401)


def test_first_code_changed_rejected(c, authority_fixture):
    _, _, control = authority_fixture
    (control / "entry.py").write_bytes(b"malicious-first-code")
    with pytest.raises(c.Blocked, match="CODE_PIN"):
        c.authority("install", clock=lambda: 2)


def env():
    return {"APP_ENV": "test", "ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA": "false",
            "ENTERPRISE_POC_DATABASE_URL": "postgresql://enterprise_agent_test:unused@127.0.0.1:55432/enterprise_agent_test",
            "REDIS_URL": "redis://enterprise_agent_test:unused@127.0.0.1:56379/0",
            "ENTERPRISE_POC_MODEL_PROVIDER_ID": "disabled-test-provider",
            "ENTERPRISE_POC_MODEL_BASE_URL": "http://127.0.0.1:9/disabled",
            "ENTERPRISE_POC_IMAGE_PROVIDER_ID": "disabled-test-provider",
            "ENTERPRISE_POC_WECHAT_NETWORK_ALLOWED": "false"}


def test_exact_test_config(c):
    c.env_check(env())


@pytest.mark.parametrize("field,value", [
    ("APP_ENV", "production"), ("ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA", "true"),
    ("ENTERPRISE_POC_DATABASE_URL", "postgresql://x:y@101.96.193.9:5432/enterprise_agent"),
    ("REDIS_URL", "redis://x:y@127.0.0.1:6379/0"),
    ("ENTERPRISE_POC_IMAGE_PROVIDER_ID", "image-gateway"),
    ("ENTERPRISE_POC_WECHAT_NETWORK_ALLOWED", "true"),
])
def test_bad_env(c, field, value):
    data = env(); data[field] = value
    with pytest.raises(c.Blocked): c.env_check(data)


def dataset(c):
    data = {name: [] for name in c.MUTABLE}
    data.update(users=[{"id": c.USER, "tenant_id": c.TENANT}],
                enterprise_configs=[{"tenant_id": c.TENANT, "payload": {"wechat_account": {"opaque_ref": "test-only"}}}],
                agent_template_versions=[{"id": "draft1", "status": "draft"}])
    data["credit_accounts"] = [{"tenant_id": c.TENANT, "balance": 178}]
    return data, {"schema_sha256": c.digest({"015": "exact"}), "rows": c.row_pins(data),
                  "credit_accounts": copy.deepcopy(data["credit_accounts"])}


def test_data_preserve_old_secret_and_seeding(c):
    data, witness = dataset(c)
    assert c.data_guard({"015": "exact"}, data, witness)


def test_bounded_task_child_and_one_credit_debit(c):
    data, witness = dataset(c)
    data["tasks"].append({"id": "t1", "tenant_id": c.TENANT, "user_id": c.USER, "agent_id": "image-agent"})
    data["task_events"].append({"id": "e1", "task_id": "t1"})
    data["credit_transactions"].append({"id": "task:t1:charge", "task_id": "t1", "tenant_id": c.TENANT})
    data["credit_accounts"][0]["balance"] -= 20
    assert c.data_guard({"015": "exact"}, data, witness)


@pytest.mark.parametrize("mutate", [
    lambda d, c: d["users"][0].update(id="other"),
    lambda d, c: d["enterprise_configs"][0]["payload"]["wechat_account"].update(opaque_ref="changed"),
    lambda d, c: d["agent_template_versions"][0].update(status="published"),
    lambda d, c: d["tasks"].append({"id": "x", "tenant_id": "other", "user_id": c.USER, "agent_id": "image-agent"}),
    lambda d, c: d["tasks"].append({"id": "x", "tenant_id": c.TENANT, "user_id": "other", "agent_id": "image-agent"}),
    lambda d, c: d["task_events"].append({"id": "e1", "task_id": "unowned"}),
    lambda d, c: d["execution_events"].append({"id": 2, "event_type": "tenant_secret.rotate", "payload": {"tenant_id": c.TENANT}}),
    lambda d, c: d["credit_accounts"][0].update(balance=-1),
])
def test_bad_data_rejected(c, mutate):
    data, witness = dataset(c); mutate(data, c)
    with pytest.raises(c.Blocked): c.data_guard({"015": "exact"}, data, witness)


def test_historical_task_never_reclassified(c):
    data, _ = dataset(c)
    data["tasks"].append({"id": "old", "tenant_id": c.TENANT, "user_id": c.USER, "status": "failed"})
    witness = {"schema_sha256": c.digest({}), "rows": c.row_pins(data), "credit_accounts": data["credit_accounts"]}
    data["tasks"][0]["status"] = "completed"
    with pytest.raises(c.Blocked, match="HISTORICAL"):
        c.data_guard({}, data, witness)


def test_schema_drift(c):
    data, witness = dataset(c)
    with pytest.raises(c.Blocked, match="SCHEMA"):
        c.data_guard({"017": "not-authorized"}, data, witness)


def test_native_symlink_rejected(c, tmp_path):
    target = tmp_path / "x"; target.write_text("x")
    path = tmp_path / "link"; path.symlink_to(target)
    with pytest.raises(c.Blocked): c.native(path)


def test_record_exclusive_and_durable(c, tmp_path):
    path = tmp_path / "receipt.json"
    c.record(path, {"x": 1})
    assert c.read(path) == {"x": 1}
    with pytest.raises(FileExistsError): c.record(path, {"x": 2})
    assert c.read(path) == {"x": 1}


def test_old_install_prefix_has_no_migration_or_cleanup(c, monkeypatch, tmp_path):
    path = tmp_path / "formal.py"
    path.write_text("def prepare(env):\n files={'original': 'value'}\n snapshot={'sha256':'exact'}\n migration=boom()\n cleanup_everything()\n")
    monkeypatch.setattr(c, "native", lambda *a, **k: None)
    fn = c.extract_function(path, c.sha(path), "prepare", object(), prefix=True)
    assert fn({}) == ({"original": "value"}, {"sha256": "exact"})


def test_wrong_function_pin_before_any_byte_execution(c, monkeypatch, tmp_path):
    path = tmp_path / "formal.py"
    path.write_text("raise AssertionError('NOT_ALLOWED_TO_EXECUTE')\ndef units(*args): return 1")
    monkeypatch.setattr(c, "native", lambda *a, **k: None)
    with pytest.raises(c.Blocked, match="CODE_PIN"):
        c.extract_function(path, "0" * 64, "units", object())


def test_actual_sealed_switch_body_uses_only_exact_pair(c, monkeypatch, tmp_path):
    # Real unchanged installed-function byte text, not an invented success stub.
    path = tmp_path / "formal.py"
    path.write_text("def switch_pointer(target):\n c.need(target in (c.RELEASE,c.OLD_RELEASE),'POINTER_TARGET')\n temp=c.R/'.current-successor-pending';c.need(not temp.exists() and not temp.is_symlink(),'PENDING_POINTER')\n temp.symlink_to(target);os.replace(temp,c.R/'current')\n")
    monkeypatch.setattr(c, "native", lambda *a, **k: None)
    old = tmp_path / "old"; old.mkdir()
    new = tmp_path / "new"; new.mkdir()
    (tmp_path / "current").symlink_to(old)
    fn = c.extract_function(path, c.sha(path), "switch_pointer", types.SimpleNamespace(
        R=tmp_path, RELEASE=new, OLD_RELEASE=old, need=c.need))
    fn(new); assert (tmp_path / "current").resolve() == new
    fn(old); assert (tmp_path / "current").resolve() == old
    with pytest.raises(c.Blocked, match="POINTER_TARGET"): fn(tmp_path / "production")


def test_no_secret_backend_import_or_seed_call(c):
    raw = (HERE / "entry.py").read_text()
    tree = ast.parse(raw)
    imports = [x.module or "" for x in ast.walk(tree) if isinstance(x, ast.ImportFrom)]
    assert not any("tenant_secret_backend" in x or "test_tenant_seeding" in x for x in imports)
    assert "tooling-context.py" not in raw and "exact-test-tenant-cleanup" not in raw
    assert "EXACT_DCA_RESTORED" not in raw


def test_dropin_keeps_old_guard_files_and_no_seed_override(c):
    raw = c.drop_text("api")
    assert str(c.RELEASE) in raw and "BOOTSTRAP_DEMO_DATA=false" in raw
    assert "SEEDING_POLICY_REQUIRED=false" not in raw
    assert "persistent-config-v1" not in raw


def test_bad_role_rejected(c):
    with pytest.raises(c.Blocked): c.drop_text("postgresql")


def test_stopped_recovery_does_not_require_live_apps(c, monkeypatch):
    monkeypatch.setattr(c, "run", lambda *a, **k: b"MainPID=0\nActiveState=inactive\n")
    c.stopped()
    monkeypatch.setattr(c, "run", lambda *a, **k: b"MainPID=42\nActiveState=active\n")
    with pytest.raises(c.Blocked, match="APP_NOT_STOPPED"): c.stopped()


def test_restore_archive_no_cross_root(c, tmp_path):
    recovery = module("test_recovery", HERE / "existing_recovery_bound.py")
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as a:
        info = tarfile.TarInfo("../production/secret"); info.size = 1
        a.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(c.Blocked, match="RESTORE_DATA_MEMBER_SCOPE"):
        recovery.restore_files(c, raw.getvalue())


def test_recovery_authority_refreshed_before_mutation(c, monkeypatch, tmp_path):
    monkeypatch.setattr(c, "OP", tmp_path)
    calls = []
    def grant(action):
        calls.append(action)
        if len(calls) > 1: raise c.Blocked("AUTHORITY_REVOKED")
    monkeypatch.setattr(c, "authority", grant)
    monkeypatch.setattr(c, "backup_gate_for_recovery", lambda: {})
    monkeypatch.setattr(c, "formal", lambda name: lambda *a: calls.append("stopped"))
    monkeypatch.setattr(c, "stopped", lambda: None)
    monkeypatch.setattr(c, "run", lambda *a, **k: pytest.fail("restore mutation after revoked grant"))
    with pytest.raises(c.Blocked, match="REVOKED"): c.recover()
    assert not (tmp_path / "RECOVER_ATTEMPTED.json").exists()


def test_consumed_restore_cannot_retry(c, monkeypatch, tmp_path):
    (tmp_path / "RECOVER_ATTEMPTED.json").write_text("{}")
    monkeypatch.setattr(c, "OP", tmp_path)
    monkeypatch.setattr(c, "authority", lambda *a: {})
    with pytest.raises(c.Blocked, match="ALREADY_ATTEMPTED"): c.recover()


def test_query_never_claims_native_pass(c):
    raw = (HERE / "entry.py").read_text()
    assert "TEST_NATIVE_PASS" not in raw and "PRODUCTION_RELEASE_READY" not in raw
    assert 'retry_permitted": False' in raw


def test_real_gpg_encrypted_readback(c, monkeypatch, tmp_path):
    recovery = module("private_recovery", HERE / "existing_recovery_bound.py")
    home = tmp_path / "gpg"; home.mkdir(mode=0o700)
    monkeypatch.setenv("GNUPGHOME", str(home))
    key = tmp_path / "test-only-no-real-secret.key"; key.write_text("isolated-fixture-not-a-production-key\n")
    key.chmod(0o600); monkeypatch.setattr(c, "KEY", key)
    cipher = recovery.crypt(c, b"ISOLATED_BACKUP_FIXTURE_NO_CUSTOMER_DATA")
    assert b"ISOLATED_BACKUP_FIXTURE" not in cipher
    assert recovery.crypt(c, cipher, decrypt=True) == b"ISOLATED_BACKUP_FIXTURE_NO_CUSTOMER_DATA"


def test_actual_file_restore_preserves_new_outputs(c, monkeypatch, tmp_path):
    recovery = module("private_recovery", HERE / "existing_recovery_bound.py")
    root = tmp_path / "test"; (root / "data").mkdir(parents=True)
    op = root / "evidence"; op.mkdir()
    paths = (root / "data/application", root / "data/object-storage")
    for path in paths: path.mkdir(mode=0o700)
    (paths[0] / "old.txt").write_text("before")
    (paths[1] / "old.txt").write_text("object-before")
    monkeypatch.setattr(recovery, "ROOT", root)
    monkeypatch.setattr(recovery, "DATA_ROOTS", paths)
    monkeypatch.setattr(c, "OP", op)
    raw = recovery.file_archive(c)
    (paths[0] / "old.txt").write_text("changed-by-test")
    (paths[1] / "new-result.txt").write_text("preserve-not-delete")
    recovery.restore_files(c, raw)
    assert (paths[0] / "old.txt").read_text() == "before"
    assert not (paths[1] / "new-result.txt").exists()
    assert (op / "data-before-recovery/object-storage/new-result.txt").read_text() == "preserve-not-delete"
    with pytest.raises(c.Blocked, match="ALREADY_STARTED"):
        recovery.restore_files(c, raw)


@pytest.fixture
def activation_fixture(c, monkeypatch, tmp_path):
    op = tmp_path / "op"; op.mkdir()
    c.record(op / "QUIESCE_ATTEMPTED.json", {"fixture": True})
    monkeypatch.setattr(c, "OP", op)
    monkeypatch.setattr(c, "ROOT", tmp_path)
    role_paths = {role: tmp_path / (role + ".conf") for role in c.ROLES}
    monkeypatch.setattr(c, "drops", lambda: role_paths)
    calls = []
    monkeypatch.setattr(c, "authority", lambda action: calls.append("grant:" + action) or {})
    monkeypatch.setattr(c, "old_state", lambda **kw: {"fixture": "no-real-root"})
    monkeypatch.setattr(c, "stopped", lambda: calls.append("stopped"))
    monkeypatch.setattr(c, "backup_gate", lambda: calls.append("real-backup-proof-stub"))
    def formal(name):
        def invoke(*args): calls.append(name + ":" + ",".join(str(a) for a in args))
        return invoke
    monkeypatch.setattr(c, "formal", formal)
    monkeypatch.setattr(c, "run", lambda *a, **k: calls.append("reload"))
    monkeypatch.setattr(c, "health", lambda: {"status": "PASS", "kind": "FIXTURE_NOT_NATIVE"})
    return op, calls


def test_activation_control_flow_commit_readback(c, activation_fixture):
    op, calls = activation_fixture
    assert c.activate()["kind"] == "FIXTURE_NOT_NATIVE"
    assert c.read(op / "COMMITTED.json")["source"] == c.SOURCE
    assert calls.index("real-backup-proof-stub") < next(i for i, x in enumerate(calls) if x.startswith("switch_pointer:"))
    with pytest.raises(c.Blocked, match="NO_RETRY"): c.activate()


def test_backup_failure_no_pointer_mutation(c, activation_fixture, monkeypatch):
    op, calls = activation_fixture
    def fail(): raise c.Blocked("RESTORE_NOT_PROVEN")
    monkeypatch.setattr(c, "backup_gate", fail)
    with pytest.raises(c.Blocked, match="RESTORE_NOT_PROVEN"): c.activate()
    assert not any(x.startswith("switch_pointer:") for x in calls)
    assert not (op / "ACTIVATE_ATTEMPTED.json").exists()


def test_health_failure_not_success_or_auto_retry(c, activation_fixture, monkeypatch):
    op, calls = activation_fixture
    def fail(): raise c.Blocked("ACTUAL_HEALTH_FAILURE_FIXTURE")
    monkeypatch.setattr(c, "health", fail)
    with pytest.raises(c.Blocked, match="HEALTH_FAILURE"): c.activate()
    assert (op / "FAILED_QUERY_OR_EXACT_RECOVER.json").exists()
    assert not (op / "COMMITTED.json").exists()
    assert sum(x.startswith("switch_pointer:") for x in calls) == 1
    with pytest.raises(c.Blocked, match="NO_RETRY"): c.activate()


def test_commit_fsync_failure_not_pass(c, activation_fixture, monkeypatch):
    op, _ = activation_fixture
    original = c.record
    def write(path, value, mode=0o400):
        if path.name == "COMMITTED.json": raise OSError("isolated-durability-failure")
        return original(path, value, mode)
    monkeypatch.setattr(c, "record", write)
    with pytest.raises(OSError): c.activate()
    assert not (op / "COMMITTED.json").exists()
    assert (op / "FAILED_QUERY_OR_EXACT_RECOVER.json").exists()


def test_early_resume_no_database_mutation(c, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(c, "OP", tmp_path)
    monkeypatch.setattr(c, "drops", lambda: {})
    monkeypatch.setattr(c, "authority", lambda *a: calls.append("grant") or {})
    monkeypatch.setattr(c, "old_state", lambda **kw: {"status": "PASS", "kind": "FIXTURE"})
    monkeypatch.setattr(c, "stopped", lambda: None)
    monkeypatch.setattr(c, "formal", lambda name: lambda *a: calls.append(name))
    monkeypatch.setattr(c, "run", lambda *a, **k: pytest.fail("No DB restore permitted here"))
    assert c.resume_unchanged()["status"] == "D8A_UNCHANGED_RESUMED"
    assert calls.count("units") == 4


def test_credit_limit_tamper_not_balance_exception(c):
    data, witness = dataset(c)
    data["credit_accounts"][0]["unapproved_limit"] = 999999
    with pytest.raises(c.Blocked, match="IDENTITY_OR_LIMIT"):
        c.data_guard({"015": "exact"}, data, witness)


def test_concurrent_operation_lock_rejected(c, monkeypatch, tmp_path):
    path = tmp_path / "test-only.lock"; path.touch()
    monkeypatch.setattr(c, "LOCK", path)
    monkeypatch.setattr(c, "native", lambda *a, **k: None)
    with c.locked():
        with pytest.raises(BlockingIOError):
            with c.locked(): pytest.fail("concurrent switch admitted")
    with c.locked(): pass


def test_registry_no_auto_publish_before_quiesce(c):
    tree = ast.parse((HERE / "entry.py").read_text())
    fn = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "quiesce")
    raw = ast.unparse(fn)
    assert raw.index("registry_preflight()") < raw.index("formal('units')('stop'")
    preflight = ast.unparse(next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == "registry_preflight"))
    assert "verify_bootstrap" in preflight and "import_archive" not in preflight and "publish(" not in preflight


def test_selected_backup_must_match_restore_report(c, monkeypatch, tmp_path):
    control = tmp_path / "control"; control.mkdir()
    backup = tmp_path / "backups/business-894-successor-v1"; backup.mkdir(parents=True)
    member = backup / "database.gpg"; member.write_bytes(b"ISOLATED_CIPHER_PLACEHOLDER")
    restore = control / "existing-recovery-bound.py"; restore.write_text("# fixture no-op never executed")
    report = tmp_path / "report.json"
    chosen = {"database": {"path": str(member), "sha256": c.sha(member)}}
    report.write_text(json.dumps({"result": "PASS", "operation": str(c.OP), "backup_members": {"wrong": "BACKUP_A"}}))
    value = {"scope": c.CONTRACT, "operation": str(c.OP), "source": c.OLD_SOURCE, "tree": c.OLD_TREE,
        "restore_source": c.OLD_SOURCE, "captured_at": 2, "quiescent_apps": True, "schema": "001-015",
        "pg_version": "16.6", "redis_version": "7.0.15", "actual_pg_restore": True, "actual_redis_restore": True,
        "files_restored": True, "hashes_match": True, "api_mcp_worker_restore_verified": True,
        "original_data_preserved": True, "restore_report_path": str(report), "restore_report_sha256": c.sha(report),
        "backup_members": chosen, "restore_entry": str(restore), "restore_entry_sha256": c.sha(restore)}
    (control / "backup-restored.verified.v1.json").write_text(json.dumps(value))
    monkeypatch.setattr(c, "CONTROL", control); monkeypatch.setattr(c, "ROOT", tmp_path)
    monkeypatch.setattr(c, "native", lambda *a, **k: None)
    monkeypatch.setattr(c, "authority", lambda *a: {"issued_at": 1, "restore_entry_sha256": c.sha(restore)})
    with pytest.raises(c.Blocked, match="BACKUP_PROOF_EXACT_SET"): c.backup_gate()


def test_old_service_restore_before_evidence_true_flags(c):
    fn = next(x for x in ast.parse((HERE / "entry.py").read_text()).body
              if isinstance(x, ast.FunctionDef) and x.name == "rehearse")
    raw = ast.unparse(fn)
    assert raw.index("old_state(topology=True)") < raw.index("'actual_pg_restore': True")
    assert raw.index("REHEARSAL_ACTUAL_DATA_DRIFT") < raw.index("'original_data_preserved': True")
