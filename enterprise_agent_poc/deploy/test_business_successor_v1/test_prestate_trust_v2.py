"""Execution-level isolated Registry/restore/first-byte tests, NOT Native PASS.

Uses actual Application Registry and official administrator initializer with a
private SQLite transaction. No SSH, platform grant, live Test PG or Provider.
Native ownership is explicitly stubbed ONLY in non-authoritative trust fixtures.
"""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import types

import pytest
from test_successor import module, dataset

HERE = Path(__file__).parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT))
from app.store import POCStore
from app.skill_registry import SkillRegistry


@pytest.fixture
def c():
    return module("v2_entry", HERE / "entry.py")


@pytest.fixture
def extension():
    return module("v2_registry", HERE / "registry_extension.py")


@pytest.fixture
def registry_fixture(tmp_path, extension):
    store = POCStore(tmp_path / "private-fixture.db")
    # Schema setup is a fixture ONLY, never invoked by the real adapter.
    with store.connection() as conn:
        conn.executescript("""
        CREATE TABLE tenants(id TEXT PRIMARY KEY,name TEXT);
        CREATE TABLE users(id TEXT PRIMARY KEY,email TEXT,tenant_id TEXT,role TEXT,account_status TEXT);
        CREATE TABLE agent_templates(id TEXT PRIMARY KEY);
        """ + SkillRegistry._sqlite_schema())
        conn.execute("INSERT INTO tenants VALUES ('fixture-tenant','fixture-only')")
        conn.execute("INSERT INTO users VALUES ('fixture-user','fixture@invalid.local','fixture-tenant','member','enabled')")
        conn.execute("INSERT INTO agent_templates VALUES ('copywriting-agent')")
    root = tmp_path / "data/application/skill-registry"
    factory = lambda scoped: SkillRegistry(scoped, root, PROJECT / "skill_packages")
    registry = factory(store)
    old_content = (PROJECT / "skill_packages" / extension.SLUG / "1.0.0.zip").read_bytes()
    old = registry._upsert_import(extension.SLUG, "1.0.0", "Unchanged metadata", "Unchanged description",
                                old_content, "fixture-user", published=True)
    with store.connection() as conn:
        conn.execute("INSERT INTO agent_skill_bindings(agent_id,skill_id,skill_version_id) VALUES (?,?,?)",
                     ('copywriting-agent', old['skill_id'], old['id']))
    official = module("actual_official_admin_initializer", PROJECT / "scripts/grant_platform_admin.py")
    content = (PROJECT / "skill_packages" / extension.SLUG / "1.0.1.zip").read_bytes()
    def rows(conn=None):
        if conn is None:
            with store.connection() as connection: return rows(connection)
        return {r["name"]: [dict(x) for x in conn.execute('SELECT * FROM "' + r["name"] + '"')]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}
    before = rows()
    def invoke(authorize=lambda: None, user="fixture-user", payload=content, factory_override=None):
        return extension.register(store, factory_override or factory,
            official.grant_platform_admin_access, payload, user, authorize,
            before_rows=rows, after_rows=rows)
    return types.SimpleNamespace(store=store, root=root, factory=factory, registry=registry,
                                 rows=rows, before=before, invoke=invoke, content=content, old=old)


def file_map(root):
    return {str(p): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    "mode": stat.S_IMODE(p.stat().st_mode), "uid": p.stat().st_uid, "gid": p.stat().st_gid}
            for p in root.rglob("*") if p.is_file()}


def test_old_registry_formal_bootstrap_accepts(registry_fixture, extension, monkeypatch):
    f = registry_fixture
    monkeypatch.setattr(f.registry, "_approved_entries", lambda: [{"skill_slug": extension.SLUG,
        "version": "1.0.0", "artifact_sha256": f.old["checksum"]}])
    assert f.registry.verify_bootstrap()["mode"] == "reuse"


def test_new_registry_actual_import_test_publish_and_old_binding(registry_fixture, extension, monkeypatch):
    f = registry_fixture
    files = file_map(f.root)
    observed = []
    def factory(scoped):
        r = f.factory(scoped)
        original = r.import_archive
        def importing(*a):
            observed.append(r.is_platform_admin("fixture-user"))
            return original(*a)
        r.import_archive = importing
        return r
    proof = f.invoke(factory_override=factory)
    assert observed == [True]
    assert f.rows()["platform_admins"] == []
    assert f.rows()["agent_skill_bindings"] == f.before["agent_skill_bindings"]
    assert extension.validate_delta(f.before, f.rows(), proof)
    assert extension.validate_files(files, file_map(f.root), proof, f.root, f.content)
    assert f.registry.test_version(f.old["id"])["status"] == "passed"
    assert f.registry.version(f.old["id"])["status"] == "published"
    monkeypatch.setattr(f.registry, "_approved_entries", lambda: [{"skill_slug": extension.SLUG,
        "version": "1.0.1", "artifact_sha256": extension.ZIP_SHA}])
    result = f.registry.verify_bootstrap()
    assert result["mode"] == "reuse" and result["bindings"][0]["version"] == "1.0.0"
    assert proof["runtime_test"] == "NOT_EXECUTED" and proof["provider_calls"] == 0


@pytest.mark.parametrize("boundary", [1, 2, 3, 4, 5])
def test_missing_or_stale_authority_rolls_back_entire_sql(registry_fixture, boundary):
    f = registry_fixture
    calls = []
    def authorize():
        calls.append(True)
        if len(calls) == boundary: raise RuntimeError("ACTUAL_SCOPE_ABSENT_EXPIRED_OR_REVOKED_FIXTURE")
    with pytest.raises(RuntimeError, match="ACTUAL_SCOPE"):
        f.invoke(authorize)
    assert f.rows() == f.before and f.rows()["platform_admins"] == []
    # File writes by formal Registry are deliberately preserved on SQL rollback;
    # real REGISTRY_ATTEMPTED prevents replay, never silently delete partials.
    if boundary >= 4: assert list(f.root.rglob("1.0.1"))


@pytest.mark.parametrize("condition", ["disabled", "unknown-user", "wrong-hash", "duplicate"])
def test_invalid_actor_hash_or_duplicate_no_relaxation(registry_fixture, condition):
    f = registry_fixture
    if condition == "disabled":
        with f.store.connection() as conn: conn.execute("UPDATE users SET account_status='disabled'")
    if condition == "duplicate": f.invoke()
    before = f.rows()
    with pytest.raises(RuntimeError):
        f.invoke(user="not-an-admin" if condition == "unknown-user" else "fixture-user",
                 payload=b"wrong" if condition == "wrong-hash" else f.content)
    assert f.rows() == before and not f.rows()["platform_admins"]


@pytest.mark.parametrize("change", ["old-bytes", "new-bytes", "extra-file"])
def test_exact_registry_file_whitelist(registry_fixture, extension, change):
    f = registry_fixture; before = file_map(f.root); proof = f.invoke(); after = file_map(f.root)
    if change == "old-bytes": after[next(iter(before))]["sha256"] = "0" * 64
    elif change == "new-bytes": after[proof["package"]["storage_path"]]["sha256"] = "0" * 64
    else: after[str(f.root / "unapproved") ] = {"sha256": "0" * 64}
    with pytest.raises(RuntimeError, match="REGISTRY"):
        extension.validate_files(before, after, proof, f.root, f.content)


def test_registry_view_exact_old_without_mutation(c, extension, registry_fixture):
    f = registry_fixture; proof = f.invoke(); data = f.rows()
    baseline = {"rows": c.row_pins(f.before), "credit_accounts": []}
    original = copy.deepcopy(data)
    view = extension.project_original(data, {"registration": proof}, baseline)
    assert c.row_pins(view) == baseline["rows"] and data == original
    assert len(data["skill_versions"]) == 2 and len(view["skill_versions"]) == 1


def test_recovery_preserves_actual_registry_and_new_business_rows(c, extension, registry_fixture, monkeypatch, tmp_path):
    f = registry_fixture; f.invoke()
    schema = {"015": "fixture-only"}
    data = f.rows(); data.update({k: [] for k in c.MUTABLE})
    data["credit_accounts"] = [{"tenant_id": c.TENANT, "balance": 100}]
    witness = {"schema_sha256": c.digest(schema), "rows": c.row_pins(data), "credit_accounts": copy.deepcopy(data["credit_accounts"])}
    data["tasks"].append({"id": "accepted", "tenant_id": c.TENANT, "user_id": c.USER, "agent_id": "image-agent"})
    c.OP = tmp_path / "op"; c.OP.mkdir()
    monkeypatch.setattr(c, "authority", lambda *a: {})
    monkeypatch.setattr(c, "stopped", lambda: None)
    monkeypatch.setattr(c, "snapshot", lambda: (schema, data))
    monkeypatch.setattr(c, "active_witness", lambda: witness)
    monkeypatch.setattr(c, "registry_witness", lambda: {"fixture": True})
    monkeypatch.setattr(c, "run", lambda *a, **k: pytest.fail("normal recovery must not pg_restore"))
    monkeypatch.setattr(c, "runtime_call", lambda *a, **k: pytest.fail("normal recovery must not flush Redis"))
    recovery = module("v2_recovery", HERE / c.RECOVERY_FILE)
    original = copy.deepcopy(data); original_rows = f.rows(); files = file_map(f.root)
    assert recovery.restore(c)["status"] == "BUSINESS_AND_REGISTRY_PRESERVED"
    assert data == original and f.rows() == original_rows and file_map(f.root) == files
    assert c.read(c.OP / "DATA_PRESERVED.json")["row_pins"] == c.row_pins(data)


def test_mutated_immutable_registry_rejected_by_active_guard(c, registry_fixture):
    f = registry_fixture; f.invoke(); data = f.rows()
    witness = {"rows": c.row_pins(data), "schema_sha256": c.digest({}), "credit_accounts": []}
    data["skill_versions"][0]["checksum"] = "wrong"
    with pytest.raises(c.Blocked, match="PROTECTED_DATA_DRIFT"): c.data_guard({}, data, witness)


def test_positive_rehearsal_producer_filename_consumed_by_backup_gate(c, monkeypatch, tmp_path):
    c.OP = tmp_path / "op"; c.OP.mkdir(); c.CONTROL = tmp_path / "control"; c.CONTROL.mkdir()
    c.BACKUP_ROOT = tmp_path / "backup"; c.BACKUP_ROOT.mkdir()
    (c.CONTROL / c.RECOVERY_FILE).write_bytes((HERE / c.RECOVERY_FILE).read_bytes())
    selected = c.BACKUP_ROOT / "fixture.gpg"; selected.write_bytes(b"fixture-not-actual-backup")
    data, baseline = dataset(c)
    fresh = {"captured_at": 2, "row_pins": c.row_pins(data), "schema_sha256": baseline["schema_sha256"],
             "metadata_pins": {}, "backup_members": {"fixture": {"path": str(selected), "sha256": c.sha(selected)}}}
    c.record(c.OP / "fresh-backup.json", fresh)
    old = tmp_path / "d8a"; old.mkdir(); c.ROOT = tmp_path; c.OLD_RELEASE = old
    (tmp_path / "current").symlink_to(old, target_is_directory=True)
    monkeypatch.setattr(c, "authority", lambda *a: {"issued_at": 1, "restore_entry_sha256": c.sha(c.CONTROL / c.RECOVERY_FILE)})
    monkeypatch.setattr(c, "native", lambda *a, **k: None)  # UID1000 fixture, not Root proof
    monkeypatch.setattr(c, "stopped", lambda: None)
    monkeypatch.setattr(c, "run", lambda *a, **k: b"")
    monkeypatch.setattr(c, "install_compatible_d8a_dropins", lambda: None)
    monkeypatch.setattr(c, "formal", lambda *a: lambda *a: None)
    monkeypatch.setattr(c, "old_state", lambda **kw: {"fixture_not_native": True})
    monkeypatch.setattr(c, "snapshot", lambda: ({"015": "exact"}, data))
    monkeypatch.setattr(c, "metadata_pins", lambda: {})
    assert c.rehearse()["source"] == c.OLD_SOURCE
    assert c.backup_gate()["restore_entry"] == str(c.CONTROL / c.RECOVERY_FILE)
    assert not list(c.CONTROL.glob("*recovery-bound*"))


@pytest.fixture
def verifier_fixture(tmp_path, monkeypatch):
    v = module("v2_first_verifier", HERE / "verified_entry.py")
    v.CONTROL = tmp_path
    for name in v.CODE_FILES: (tmp_path / name).write_bytes((HERE / name).read_bytes())
    # Explicitly stub native ownership only; real pin/compile ordering executes.
    monkeypatch.setattr(v, "protected", lambda *a: None)
    monkeypatch.setattr(v.sys, "flags", types.SimpleNamespace(isolated=True, no_site=True))
    monkeypatch.setattr(v.sys, "executable", "/usr/bin/python3.10")
    binary = Path(v.sys.executable).resolve()
    manifest = tmp_path / "system-python-trust.v2.json"
    manifest.write_text(json.dumps({"stdlib_source_and_extension_pins": {}}))
    value = {"issued_at": 1, "expires_at": 9999999999, "revoked": False,
        "python_real_path": str(binary), "python_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "system_python_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "code_sha256": {n: hashlib.sha256((tmp_path / n).read_bytes()).hexdigest() for n in v.CODE_FILES}}
    def write(): (tmp_path / "scope.approved.v1.json").write_text(json.dumps(value))
    write()
    return v, value, write


def test_pin_rejects_candidate_before_any_execution(verifier_fixture):
    v, _, _ = verifier_fixture
    marker = v.CONTROL / "MALICIOUS_MARKER"
    (v.CONTROL / "entry.py").write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").touch()")
    with pytest.raises(RuntimeError, match="CODE_PIN_BEFORE_COMPILE"): v.verify("prepare")
    assert not marker.exists()


def test_actual_verified_source_compiles_only_after_closure(verifier_fixture, monkeypatch):
    v, value, write = verifier_fixture
    code = b"def main(): return {'fixture': 'SOURCE_BYTES_COMPILED_NOT_CACHED'}\n"
    (v.CONTROL / "entry.py").write_bytes(code)
    value["code_sha256"]["entry.py"] = hashlib.sha256(code).hexdigest(); write()
    monkeypatch.setattr(v.sys, "argv", ["verified_entry.py", "prepare"])
    assert v.main()["fixture"] == "SOURCE_BYTES_COMPILED_NOT_CACHED"


@pytest.mark.parametrize("failure", ["pyc", "pin-set", "expired", "revoked", "interpreter"])
def test_first_verifier_fail_closed(verifier_fixture, failure):
    v, value, write = verifier_fixture
    if failure == "pyc": (v.CONTROL / "entry.pyc").write_bytes(b"not-listed")
    if failure == "pin-set": value["code_sha256"].pop("entry.py")
    if failure == "expired": value["expires_at"] = 1
    if failure == "revoked": value["revoked"] = True
    if failure == "interpreter": value["python_sha256"] = "0" * 64
    write()
    with pytest.raises(RuntimeError): v.verify("prepare")


def test_native_path_rejects_mutable_or_symlink_before_pin(c, tmp_path):
    mutable = tmp_path / "mutable.py"; mutable.write_text("not-root")
    link = tmp_path / "alias.py"; link.symlink_to(mutable)
    for path in (mutable, link):
        with pytest.raises(c.Blocked, match="ROOT_ANCESTOR_TRUST"): c.pinned(path, c.sha(mutable))


def test_shared_runtime_programs_are_demoted_before_execution(c, monkeypatch):
    captured = []
    monkeypatch.setattr(c.os, "geteuid", lambda: 0)
    monkeypatch.setattr(c.subprocess, "run", lambda a, **kw: captured.append(a) or types.SimpleNamespace(returncode=0, stdout=b""))
    c.run([c.APPLICATION_PYTHON, "-B", "app.py"])
    c.run([c.PG / "pg_dump"])
    assert all(a[:4] == ["/usr/sbin/runuser", "-u", "lucky", "--"] for a in captured)


def test_git_does_not_execute_user_config_or_path(c, monkeypatch):
    captured = []
    monkeypatch.setattr(c.subprocess, "run", lambda a, **kw: captured.append((a, kw)) or types.SimpleNamespace(returncode=0, stdout=b""))
    c.run(["git", "status"])
    assert captured[0][0][0] == "/usr/bin/git"
    assert "core.fsmonitor=false" in captured[0][0] and captured[0][1]["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"


def test_root_control_has_no_third_party_or_application_import(c):
    for name in ("entry.py", c.RECOVERY_FILE, "verified_entry.py"):
        tree = ast.parse((HERE / name).read_bytes())
        imports = [x.module for x in ast.walk(tree) if isinstance(x, ast.ImportFrom)]
        imports += [a.name for x in ast.walk(tree) if isinstance(x, ast.Import) for a in x.names]
        assert not any(n and n.split('.')[0] in ("psycopg", "redis", "mcp", "app") for n in imports)
    assert "-I -S -B" in c.drop_text("api")
    assert c.RECOVERY_FILE in c.CODE_FILES and c.RECOVERY_FILE == "existing_recovery_bound.py"


def test_old_application_guard_never_imported_as_root(c, monkeypatch):
    monkeypatch.setattr(c.os, "geteuid", lambda: 0)
    with pytest.raises(c.Blocked, match="NO_PRIVILEGED_APPLICATION_IMPORT"): c.old_guard()


def test_actual_os_python310_loads_control_without_site_or_candidate_cache():
    # Actual unprivileged OS3.10 execution, no Root context/main or mutations.
    code = ("import pathlib,json; p=pathlib.Path(" + repr(str(HERE)) + "); "
            "names=['entry.py','existing_recovery_bound.py','verified_entry.py']; "
            "[exec(compile((p/n).read_bytes(),str(p/n),'exec'),"
            "{'__name__':'isolated_engineering_only','__file__':str(p/n)}) for n in names]; "
            "print(json.dumps({'loaded':names}))")
    result = subprocess.run(['/usr/bin/python3.10', '-I', '-S', '-B', '-c', code],
                            capture_output=True, check=True)
    assert len(json.loads(result.stdout)['loaded']) == 3


def test_missing_real_scope_is_not_created(c, monkeypatch, tmp_path):
    monkeypatch.setattr(c, "CONTROL", tmp_path)
    with pytest.raises(FileNotFoundError): c.authority("registry")
    assert not list(tmp_path.iterdir())


def test_partial_rehearsal_can_not_roll_back_data_after_activation(c, monkeypatch, tmp_path):
    c.OP = tmp_path
    c.record(tmp_path / "REHEARSAL_ATTEMPTED.json", {})
    c.record(tmp_path / "ACTIVATE_ATTEMPTED.json", {})
    monkeypatch.setattr(c, "authority", lambda *a: {})
    monkeypatch.setattr(c, "stopped", lambda: None)
    r = module("recovery_repair_fixture", HERE / c.RECOVERY_FILE)
    with pytest.raises(c.Blocked, match="REPAIR_ONLY_FAILED_PREACTIVATION"):
        r.restore(c, repair=True)


def test_expired_scope_still_allows_readonly_original_query(verifier_fixture):
    v, value, write = verifier_fixture
    value['expires_at'] = 1; value['revoked'] = True; write()
    assert v.verify('query')['revoked']
    with pytest.raises(RuntimeError): v.verify('prepare')


def test_original_assertions_not_deleted_or_weakened():
    path = 'enterprise_agent_poc/deploy/test_business_successor_v1/test_successor.py'
    if os.environ.get('DE29_ORIGINAL_TEST_BYTES'):
        # Windows-managed worktree .git references cannot be resolved by WSL
        # Git. The independently exported exact de29 blob is SHA-bound here.
        old = Path(os.environ['DE29_ORIGINAL_TEST_BYTES']).read_bytes()
    else:
        old = subprocess.run(['git', '-C', str(HERE.parents[2]), 'show',
            'de29b8a2250cac5382cefa095750f2d80b389ee0:' + path], check=True, capture_output=True).stdout
    assert hashlib.sha256(old).hexdigest() == '892df491082bb15f2fab59e802afe8e5d5771b9916955a627ca5b8e37a9a7f9f'
    def tests(raw):
        return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(raw).body
                if isinstance(n, ast.FunctionDef) and n.name.startswith('test_')}
    assert tests(old) == tests((HERE / 'test_successor.py').read_bytes())


@pytest.mark.parametrize('mutation', ['none', 'wrong-actor', 'old-row-drift'])
def test_committed_registry_reconciles_original_attempt_only(c, extension, registry_fixture, monkeypatch, tmp_path, mutation):
    f = registry_fixture
    original_files = file_map(f.root)
    proof = f.invoke()
    data = f.rows(); before = copy.deepcopy(f.before)
    c.ROOT = f.root.parents[2]; c.USER = 'fixture-user'
    c.OP = tmp_path / 'operation'; c.OP.mkdir()
    c.CONTROL = tmp_path / 'control'; c.CONTROL.mkdir()
    c.RELEASE = tmp_path / '894'; c.OLD_RELEASE = tmp_path / 'd8a'; c.OLD_RELEASE.mkdir()
    (c.ROOT / 'current').symlink_to(c.OLD_RELEASE, target_is_directory=True)
    package = c.RELEASE / 'enterprise_agent_poc/skill_packages' / extension.SLUG / '1.0.1.zip'
    package.parent.mkdir(parents=True); package.write_bytes(f.content)
    (c.CONTROL / 'registry_extension.py').write_bytes((HERE / 'registry_extension.py').read_bytes())
    (c.CONTROL / 'scope.approved.v1.json').write_text('{"fixture_not_authority":true}')
    c.record(c.OP / 'REGISTRY_ATTEMPTED.json', {'scope_sha256': c.sha(c.CONTROL / 'scope.approved.v1.json')})
    c.record(c.OP / 'prepare.json', {'rows': c.row_pins(before), 'schema_sha256': c.digest({}),
        'registry_files': original_files, 'credit_accounts': []})
    grant = {'code_sha256': {'registry_extension.py': c.sha(c.CONTROL / 'registry_extension.py')}}
    monkeypatch.setattr(c, 'authority', lambda *a: grant)
    monkeypatch.setattr(c, 'stopped', lambda: None)
    monkeypatch.setattr(c, 'source_identity', lambda *a: None)
    monkeypatch.setattr(c, 'native', lambda *a, **k: None)
    monkeypatch.setattr(c.os, 'chown', lambda *a: None)  # fixture cannot acquire Root
    monkeypatch.setattr(c, 'snapshot', lambda: ({}, data))
    monkeypatch.setattr(c, 'runtime_call', lambda *a, **k: pytest.fail('reconcile cannot grant/import/retry'))
    if mutation == 'wrong-actor':
        next(r for r in data['skill_versions'] if r['id'] == proof['version']['id'])['created_by'] = 'other'
    if mutation == 'old-row-drift': data['users'][0]['role'] = 'enterprise_admin'
    if mutation != 'none':
        with pytest.raises(c.Blocked): c.registry_reconcile()
        assert not (c.OP / 'registry-witness.v2.json').exists()
    else:
        assert c.registry_reconcile()['bindings'] == 'UNCHANGED'
        assert c.registry_witness()['registration']['reconciled']
        assert c.registry_reconcile()['status'] == 'EXISTING_REGISTRY_WITNESS_VERIFIED'
    assert f.rows() == {**before, 'skill_versions': f.rows()['skill_versions'], 'skill_packages': f.rows()['skill_packages']}
