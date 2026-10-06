"""Versioned exact015 Runtime-only gates; real private PG, real Bash rollback."""
import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import release_manifest as manifest, schema_015_runtime_only as runtime
from scripts import release_runtime_recovery as recovery, release_migration_transition as transition, migrate
from scripts import rollback_preflight as gate
from tests.test_migration_015_release_contract import schema014, plan
from tests.test_release_migration_transition import schema_012_store
from tests.test_release_switch import switch_harness
from tests.test_post_commit_runtime_recovery import post_entry, post_commit

ROOT = Path(__file__).resolve().parents[1]


def declared():
    return json.loads((ROOT / 'deploy/schema_015_runtime_only.v1.json').read_text())


def sample():
    return {'release_id': 'fixture', 'source_commit': 'a'*40, 'archive_sha256': 'b'*64,
            'selected_files': ['enterprise_agent_poc/pyproject.toml'], 'selected_file_count': 1,
            'build_platform': 'linux', 'runtime_only_release': declared()}


def test_versioned_contract_and_no_source_self_reference():
    value = manifest.validate_manifest_contract(sample())
    assert runtime.declaration(ROOT, value)['migration_action'] == 'NONE'
    assert 'source_commit' not in declared() and 'target_source' not in declared()
    assert recovery.declared(ROOT, value)['exact_predecessor'] == recovery.PREDECESSOR


@pytest.mark.parametrize('field,wrong', [('current_schema','014'), ('target_schema','016'),
    ('pending',1), ('pending',False), ('unknown',1), ('checksum_drift',1),
    ('migration015_blob','0'*40), ('migration_action','APPLY'), ('post_commit_runtime_rollback','DISABLED')])
def test_runtime_only_declaration_fail_closed(field, wrong):
    value = sample()
    value['runtime_only_release'][field] = wrong
    with pytest.raises(manifest.ManifestContractError):
        manifest.validate_manifest_contract(value)


def test_combined_forward_and_runtime_only_forbidden():
    value = sample()
    value['forward_migrations'] = plan()
    with pytest.raises(manifest.ManifestContractError, match='code_only_scope'):
        manifest.validate_manifest_contract(value)


def test_missing_or_wrong_recovery_contract_rejects(tmp_path):
    (tmp_path/'deploy').mkdir()
    (tmp_path/'deploy/schema_015_runtime_only.v1.json').write_text(json.dumps(declared()))
    with pytest.raises((OSError, gate.RollbackBlocked)):
        runtime.declaration(tmp_path, sample())
    contract = recovery.contract(ROOT)
    contract['exact_predecessor']['release_id'] = 'wrong'
    (tmp_path/'deploy/migration_015_recovery.v1.json').write_text(json.dumps(contract))
    with pytest.raises(gate.RollbackBlocked, match='recovery_contract_identity'):
        runtime.declaration(tmp_path, sample())


def test_migration_apply_and_schema_transition_command_rejected_before_io():
    with pytest.raises(transition.MigrationTransitionBlocked, match='runtime_only_migration_forbidden'):
        transition.apply_declared(None, declared())
    with pytest.raises(transition.MigrationTransitionBlocked, match='runtime_only_migration_forbidden'):
        transition.declaration(sample())


@pytest.fixture
def real015(schema014):
    transition.apply_declared(schema014, plan())  # isolated Test fixture only
    return schema014


def test_real_runtime015_pending0_ledger_no_mutation(real015):
    with real015.connection() as conn:
        before = transition._history(conn)
    result = runtime.ledger_snapshot(ROOT, real015.database_url)
    assert result['pending'] == result['unknown'] == result['checksum_drift'] == 0
    assert result['migration_commands_executed'] == 0
    with real015.connection() as conn:
        assert transition._history(conn) == before


def test_real_schema014_rejected(schema014):
    with pytest.raises(gate.RollbackBlocked, match='exact_schema015'):
        runtime.ledger_snapshot(ROOT, schema014.database_url)


def test_real_pending_inventory_rejected(real015, monkeypatch):
    items = migrate.migration_items()
    monkeypatch.setattr(migrate, 'migration_items', lambda: items+[dict(items[-1], version='016')])
    with pytest.raises(gate.RollbackBlocked, match='migration_inventory'):
        runtime.ledger_snapshot(ROOT, real015.database_url)


@pytest.mark.parametrize('fault', ['unknown', 'schema016', 'checksum'])
def test_real_unknown_future_checksum_rejected(real015, fault):
    with real015.connection() as conn:
        if fault == 'checksum':
            conn.execute("UPDATE schema_migrations SET checksum=%s WHERE version='015'", ('0'*64,))
        else:
            conn.execute('INSERT INTO schema_migrations(version,name,checksum) VALUES (%s,%s,%s)',
                         ('016' if fault == 'schema016' else '999', 'unapproved.sql', '0'*64))
    with pytest.raises(gate.RollbackBlocked):
        runtime.ledger_snapshot(ROOT, real015.database_url)


def test_old_forward_contract_stays_014_to015():
    value = plan()
    manifest.validate_forward_migrations(value)
    assert value['from_schema'] == '014' and value['target_schema'] == '015'
    assert value['migrations'][0]['version'] == '015'


@pytest.mark.parametrize('fault', ['none', 'migration', 'ledger', 'boolean'])
def test_postcommit_runtime_only_receipt_fail_closed(post_commit, monkeypatch, fault):
    base, root, value, receipt, path = post_commit
    value['runtime_only_release'] = declared()
    receipt.update(manifest_sha256=gate.digest(value), release_mode='RUNTIME_ONLY',
                   current_schema='015', target_schema='015', migration_action='NONE',
                   migration_commands_executed=0, migration_ledger_sha256='a'*64,
                   runtime_only_contract_sha256=gate.digest(declared()))
    monkeypatch.setattr(runtime, 'ledger_snapshot', lambda *args: {'ledger_sha256': 'a'*64})
    if fault == 'migration': receipt['migration_commands_executed'] = 1
    elif fault == 'ledger': receipt['migration_ledger_sha256'] = 'b'*64
    elif fault == 'boolean': receipt['migration_commands_executed'] = False
    path.write_text(json.dumps(receipt))
    if fault == 'none':
        assert recovery.preflight(base, root, value)['status'] == 'post_commit_runtime_rollback_preflight_passed'
    else:
        with pytest.raises(gate.RollbackBlocked, match='runtime_only_commit_receipt_identity'):
            recovery.preflight(base, root, value)


def test_runtime_only_real_postcommit_bash_rollback_no_migration(post_entry):
    h = post_entry
    path = h['candidate'].parent/'candidate.manifest.json'
    value = json.loads(path.read_text())
    value['runtime_only_release'] = declared()
    path.write_text(json.dumps(value))
    python = h['base']/'venv/bin/python'
    text = python.read_text()
    injected = '''if args and args[0].endswith("schema_015_runtime_only.py"):
    print(json.dumps({"status":"runtime_only_schema015_verified","migration_commands_executed":0}))
    sys.exit(0)
if args and args[0].endswith("release_migration_transition.py"):
    raise AssertionError("Runtime-only attempted migration command")
'''
    text = text.replace('if args and args[0].endswith("release_runtime_recovery.py"):',
                        injected+'\nif args and args[0].endswith("release_runtime_recovery.py"):')
    python.write_text(text)
    python.chmod(0o755)
    result = subprocess.run(['bash', str(h['script']), 'candidate', '--rollback-runtime'],
                            env=h['env'], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    assert 'POST_COMMIT_RUNTIME_ROLLBACK_PASS' in result.stdout
    assert '"migration_commands_executed":0' in result.stdout
    assert (h['base']/'release-current').resolve() == h['old']


def test_same015_shell_branch_cannot_call_migration_or_commit_before_final_state():
    text = (ROOT/'deploy/release_switch.sh').read_text()
    assert '[[ "$declared_forward" == no ]]' in text
    assert 'if [[ "$declared_forward" == yes ]]; then\n  printf' in text
    assert text.index('flock -n -E 75 9') < text.index('scripts/schema_015_runtime_only.py preflight')
    assert text.index('verify_release final-state state') < text.index('scripts/release_runtime_recovery.py receipt')
