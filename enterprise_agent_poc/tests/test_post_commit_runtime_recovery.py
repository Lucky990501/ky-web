"""015 exact recovery gate faults; no Provider / DB / host mutations."""
import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import release_runtime_recovery as recovery, rollback_preflight as gate
from scripts.release_manifest import ManifestContractError, validate_forward_migrations
from tests.test_release_switch import switch_harness

ROOT = Path(__file__).resolve().parents[1]


def test_exact_015_manifest_plan_and_blob():
    value = recovery.contract(ROOT)
    assert value["recovery_mode"] == "PREDECESSOR_ON_SCHEMA_015"
    assert value["exact_predecessor"] == recovery.PREDECESSOR
    validate_forward_migrations(json.loads((ROOT / "deploy/forward_migrations_015.json").read_text()))


def test_current_schema015_predecessor_has_one_exact_immutable_identity():
    exact = recovery.contract(ROOT)["exact_predecessor"]
    assert exact == {
        "release_id": "20261006-519c649-reference-image-oss-v1",
        "source_commit": "519c649bd61d3bf3b1a6708410b9b7102eccb7a2",
        "source_tree": "e867a4f9922f0f73e71e818e2993666d842559f2",
        "archive_sha256": "6d6ca0612dfc2d52e96c8775594ea90a5d697afd02c4fb229367e0c58db6ca11",
        "raw_manifest_sha256": "bd60c630c4fd7b9b2382d13d042ae6398c19e7da6de2cd5d1916f17a6cab3d4e",
        "manifest_sha256": "b159797b4596620f6de4df092ac6a1fc344d7921c51e331b7908d5ef87b2e271",
    }
    assert recovery.contract(ROOT)["compatibility_evidence"]["report_sha256"] == (
        "0d19e8ae96f88ee06f1ab8a31495bbf9a79fb838cc84c90c8c634999f8d61ba1")


@pytest.mark.parametrize("fault", ["predecessor", "source", "tree", "archive", "raw", "migration", "forward", "mode", "feature", "scope"])
def test_contract_tamper_fail_closed(tmp_path, fault):
    root = tmp_path / "app"
    (root / "deploy").mkdir(parents=True)
    value = recovery.contract(ROOT)
    if fault in {"predecessor", "source", "tree", "archive", "raw"}:
        key = {"predecessor": "release_id", "source": "source_commit", "tree": "source_tree",
               "archive": "archive_sha256", "raw": "raw_manifest_sha256"}[fault]
        value["exact_predecessor"][key] = "wrong"
    elif fault == "migration": value["migration"]["canonical_sha256"] = "0" * 64
    elif fault == "forward": value["forward_schema_predecessor_compatible"] = False
    elif fault == "mode": value["recovery_mode"] = "RESTORE_SCHEMA014"
    elif fault == "feature": value["feature_source"] = "f" * 40
    else: value["extra"] = "wildcard"
    (root / "deploy/migration_015_recovery.v1.json").write_text(json.dumps(value))
    with pytest.raises(gate.RollbackBlocked, match="recovery_contract_identity"):
        recovery.contract(root)


@pytest.mark.parametrize("fault", ["version", "from", "target", "hash", "extra", "rollback"])
def test_manifest_future_or_unsafe_plan_rejected(fault):
    value = json.loads((ROOT / "deploy/forward_migrations_015.json").read_text())
    if fault == "version": value["schema_version"] = 3
    elif fault == "from": value["from_schema"] = "013"
    elif fault == "target": value["target_schema"] = "016"
    elif fault == "hash": value["migrations"][0]["canonical_sha256"] = "0" * 64
    elif fault == "extra": value["migrations"].append(copy.deepcopy(value["migrations"][0]))
    else: value["rollback_strategy"] = "schema_down"
    with pytest.raises(ManifestContractError): validate_forward_migrations(value)


@pytest.fixture
def post_commit(tmp_path, monkeypatch):
    """Identity/DB boundaries injected here; separate native rehearsal runs real gates."""
    base = tmp_path
    root = base / "releases/current/enterprise_agent_poc"
    root.mkdir(parents=True)
    (base / "release-current").symlink_to(root, target_is_directory=True)
    manifest = {"release_id": "current", "source_commit": "f" * 40}
    value = {"phase": "POST_COMMIT_HEALTH", "release_id": "current", "source_commit": "f" * 40,
             "manifest_sha256": gate.digest(manifest), "recovery_contract_sha256": gate.digest(recovery.contract(ROOT)),
             "predecessor": recovery.PREDECESSOR, "bindings": {"campaign-agent": {"event-campaign-plan": "1.0.1"}}}
    path = recovery.receipt_path(base, "current")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(value))
    from scripts import release_verify
    monkeypatch.setattr(recovery, "declared", lambda *a: recovery.contract(ROOT))
    monkeypatch.setattr(recovery, "contract", lambda *a, original=recovery.contract(ROOT): original)
    monkeypatch.setattr(recovery, "approval", lambda *a: {})
    monkeypatch.setattr(recovery, "verify_schema", lambda *a: {"applied_versions": [f"{i:03}" for i in range(1, 16)], "pending": 0})
    monkeypatch.setattr(release_verify, "registry_for", lambda *a: None)
    monkeypatch.setattr(release_verify, "bindings", lambda *a: value["bindings"])
    monkeypatch.setattr(release_verify, "service_state", lambda *a: {r: {"pid": 0, "active": "failed"} for r in ("api", "mcp", "worker")})
    return base, root, manifest, value, path


def test_post_commit_crashed_runtime_recoverable(post_commit):
    base, root, manifest, _, _ = post_commit
    assert recovery.preflight(base, root, manifest)["status"] == "post_commit_runtime_rollback_preflight_passed"


def test_schema014_does_not_authorize_post_commit_recovery(post_commit, monkeypatch):
    monkeypatch.setattr(recovery, "verify_schema", lambda *a: {"applied_versions": [f"{i:03}" for i in range(1, 15)], "pending": 1})
    with pytest.raises(gate.RollbackBlocked, match="post_commit_requires_schema_015"):
        recovery.preflight(*post_commit[:3])


@pytest.mark.parametrize("field", ["phase", "source_commit", "manifest_sha256", "recovery_contract_sha256", "predecessor"])
def test_unknown_receipt_blocks(post_commit, field):
    base, root, manifest, value, path = post_commit
    value[field] = "unknown"
    path.write_text(json.dumps(value))
    with pytest.raises(gate.RollbackBlocked, match="release_commit_receipt_identity"):
        recovery.preflight(base, root, manifest)


def test_unknown_current_pointer_blocks(post_commit):
    base, root, manifest, _, _ = post_commit
    (base / "release-current").unlink()
    (base / "release-current").symlink_to(root.parent)
    with pytest.raises(gate.RollbackBlocked, match="unknown_current_release"):
        recovery.preflight(base, root, manifest)


def test_mixed_bindings_blocks(post_commit, monkeypatch):
    from scripts import release_verify
    monkeypatch.setattr(release_verify, "bindings", lambda *a: {"foreign": {}})
    with pytest.raises(gate.RollbackBlocked, match="UNKNOWN_BINDING_STATE"):
        recovery.preflight(*post_commit[:3])


def test_live_foreign_cwd_blocks(post_commit, monkeypatch):
    from scripts import release_verify
    monkeypatch.setattr(release_verify, "service_state", lambda *a: {"worker": {"pid": 123, "active": "active", "cwd": "/foreign"}})
    with pytest.raises(gate.RollbackBlocked, match="unknown_current_service_worker"):
        recovery.preflight(*post_commit[:3])


@pytest.mark.parametrize('state', [{'error':'service_unreadable'}, {'active':'active','pid':0}])
def test_unreadable_or_inconsistent_service_not_assumed_crashed(post_commit, monkeypatch, state):
    from scripts import release_verify
    monkeypatch.setattr(release_verify, 'service_state', lambda *a: {'worker': state})
    with pytest.raises(gate.RollbackBlocked, match='unknown_current_service_state_worker'):
        recovery.preflight(*post_commit[:3])


def test_restored_cwd_mismatch_never_claims_recovery(tmp_path, monkeypatch):
    from scripts import release_verify as rv
    base = tmp_path
    prior = base / 'releases' / recovery.PREDECESSOR['release_id'] / 'enterprise_agent_poc'
    prior.mkdir(parents=True)
    manifest = prior.parent / (recovery.PREDECESSOR['release_id'] + '.manifest.json')
    manifest.write_text(json.dumps(recovery.PREDECESSOR))
    (base / 'release-current').symlink_to(prior)
    monkeypatch.setattr(rv, 'binding_gate', lambda *a: {'campaign-agent': {}})
    states = {role: {'active': 'active', 'pid': 123, 'cwd': str(prior), 'module_ok': True,
                     'exe': str((base / 'venv/bin/python').resolve())} for role in ('api','mcp','worker')}
    states['worker']['cwd'] = '/wrong-restored-runtime'
    monkeypatch.setattr(rv, 'service_state', lambda *a: states)
    with pytest.raises(rv.GateFailed, match='worker_runtime'):
        rv.verify_state(base, tmp_path / 'unused-candidate.json', manifest, tmp_path / 'unused-snapshot', rollback=True)


def test_unapproved_current_identity_cannot_open_rollback_target(tmp_path, monkeypatch):
    for key in ('RELEASE_EXPECTED_SOURCE_COMMIT','RELEASE_EXPECTED_ARCHIVE_SHA256',
                'RELEASE_EXPECTED_RAW_MANIFEST_SHA256','RELEASE_EXPECTED_CANONICAL_MANIFEST_SHA256'):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(gate.RollbackBlocked, match='known_current_approval_required'):
        recovery.approval(tmp_path, tmp_path, {'release_id': 'unknown', 'source_commit': 'f'*40})


def test_cross_directory_manifest_alias_blocks(tmp_path, monkeypatch):
    root = tmp_path / 'releases/actual/enterprise_agent_poc'
    root.mkdir(parents=True)
    for key in ('RELEASE_EXPECTED_ARCHIVE_SHA256','RELEASE_EXPECTED_RAW_MANIFEST_SHA256',
                'RELEASE_EXPECTED_CANONICAL_MANIFEST_SHA256'):
        monkeypatch.setenv(key, 'a'*64)
    monkeypatch.setenv('RELEASE_EXPECTED_SOURCE_COMMIT','f'*40)
    with pytest.raises(gate.RollbackBlocked, match='known_current_release_path'):
        recovery.approval(tmp_path, root, {'release_id':'different','source_commit':'f'*40})


def test_entry_lock_commit_and_recovery_boundary():
    source = (ROOT / "deploy/release_switch.sh").read_text()
    assert "--rollback-runtime" in source
    assert source.index("flock -n -E 75 9") < source.index("scripts/release_runtime_recovery.py preflight")
    assert source.index("verify_release final-state state") < source.index("scripts/release_runtime_recovery.py receipt") < source.rindex("trap - ERR")
    entry = source[source.index('if [[ "$mode" == "--rollback-runtime" ]]; then\n  trap - ERR'):source.index('if [[ "$declared_forward" == yes ]]; then\n  printf')]
    assert "POST_COMMIT_RUNTIME_ROLLBACK_FAILED" in entry and "verify_release evidence evidence" in entry
    assert entry.count("if ! rollback;") == 1
    assert "migrate.py" not in entry and "smoke" not in entry
    assert all(stage in source for stage in recovery.STAGES)


@pytest.fixture
def post_entry(switch_harness):
    """Real Bash/lock/symlink with host/schema admission boundary injected.

    The native rehearsal separately runs immutable/schema admission unchanged.
    These tests prove shell failure handling never recurses or claims success.
    """
    h = switch_harness
    python = h['base'] / 'venv/bin/python'
    text = python.read_text()
    injection = '''if args and args[0].endswith("release_runtime_recovery.py"):
    import fcntl, importlib.util
    with open(os.environ["RELEASE_LOCK_FILE"],"a+") as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: pass
        else: raise AssertionError("recovery lock not held")
    if args[1]=="target": print("old")
    elif args[1]=="capture":
        spec=importlib.util.spec_from_file_location("rv","scripts/release_verify.py")
        rv=importlib.util.module_from_spec(spec);spec.loader.exec_module(rv)
        path=Path(args[args.index("--snapshot")+1])
        path.write_text(json.dumps(rv.bindings(rv.registry_for(rv.ROOT.parent.parent.parent))))
    else: print(json.dumps({"status":"isolated_admission_boundary"}))
    sys.exit(0)
'''
    text = text.replace('if args and args[0] == "scripts/rollback_preflight.py":', injection + '\nif args and args[0] == "scripts/rollback_preflight.py":')
    text = text.replace('if fault == "preflight_cwd":', 'if os.environ.get("RESTORED_CWD_FAULT") and current.parent.name == "old":\n            result["worker"]["cwd"]="wrong-restored-runtime"\n        if fault == "preflight_cwd":')
    python.write_text(text); python.chmod(0o755)
    (h['base'] / 'release-current').unlink()
    (h['base'] / 'release-current').symlink_to(h['candidate'])
    return h


def test_post_commit_entry_restores_under_one_lock(post_entry):
    h = post_entry
    result = subprocess.run(['bash',str(h['script']),'candidate','--rollback-runtime'], env=h['env'], capture_output=True,text=True,timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'POST_COMMIT_RUNTIME_ROLLBACK_PASS' in result.stdout
    assert (h['base'] / 'release-current').resolve() == h['old']
    assert not list(h['base'].glob('.release-switch.*'))


@pytest.mark.parametrize('fault', ['health','cwd'])
def test_post_commit_failed_recovery_retains_evidence_and_does_not_retry(post_entry, fault):
    h = post_entry
    env = {**h['env'], ('SWITCH_TEST_RECOVERY_FAIL' if fault == 'health' else 'RESTORED_CWD_FAULT'): '1'}
    result = subprocess.run(['bash',str(h['script']),'candidate','--rollback-runtime'],env=env,capture_output=True,text=True,timeout=60)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'POST_COMMIT_RUNTIME_ROLLBACK_FAILED' in result.stderr
    assert 'POST_COMMIT_RUNTIME_ROLLBACK_PASS' not in result.stdout
    snapshots = list(h['base'].glob('.release-switch.*'))
    assert len(snapshots) == 1 and (snapshots[0] / 'recovery-evidence.json').is_file()
    assert h['events'].read_text().count('systemctl:restart') == 1
