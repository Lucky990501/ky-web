"""C1-C12: current-epoch bootstrap is restricted to fresh private Linux state."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
import psycopg
from psycopg.rows import dict_row

from scripts import compatibility_epoch as epoch
from scripts import rollback_preflight as gate
from scripts.release_test_environment import IsolatedServices, PROJECT


pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Private Linux PostgreSQL fixture only')


@pytest.fixture
def fresh(monkeypatch):
    # stage2_isolation.bootstrap deliberately exports one private manifest per
    # process; restore it so independent ephemeral clusters never share state.
    before = dict(os.environ)
    monkeypatch.setattr(epoch, '_current_source_identity', lambda: 'f' * 40)
    with IsolatedServices() as services:
        services.migrate()
        subprocess.run([sys.executable, str(PROJECT / 'scripts/stage2_preview.py'),
                        'system-provision', '--config', str(services.manifest)],
                       cwd=PROJECT, env=services._child_env(), check=True,
                       stdout=subprocess.DEVNULL)
        yield services
    os.environ.clear()
    os.environ.update(before)


def state(services):
    with _connect(services) as conn:
        return epoch.read_state(conn)['epoch']


def _connect(services):
    services.validate_manifest()
    return psycopg.connect(services.config['environment']['ENTERPRISE_POC_DATABASE_URL'], row_factory=dict_row)


def invoke(services):
    before = dict(os.environ)
    try:
        for name in services.config['environment']:
            os.environ.pop(name, None)
        return epoch.bootstrap_current(services.manifest)
    finally:
        os.environ.clear()
        os.environ.update(before)


def test_c1_c2_current_fresh_migrations_and_epoch(fresh):
    assert state(fresh) == 'legacy_v1'
    assert invoke(fresh)['status'] == 'bootstrapped'
    assert state(fresh) == 'productized_v1'


def test_c2_production_marker_blocked_before_connect(monkeypatch):
    from scripts import stage2_isolation
    from types import SimpleNamespace
    monkeypatch.setattr(stage2_isolation, 'bootstrap', lambda _: ({'mode': 'redis-postgres'},
                         SimpleNamespace(environment='production')))
    with pytest.raises(epoch.EpochBlocked, match='fresh_bootstrap_nonproduction_required'):
        epoch.bootstrap_current(Path('/nonexistent'))


def test_c3_missing_012_blocks_without_epoch_change(fresh):
    with _connect(fresh) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version='012'")
    with pytest.raises(gate.RollbackBlocked):
        invoke(fresh)
    assert state(fresh) == 'legacy_v1'


def test_c4_extra_pending_or_unknown_migration_blocks(fresh):
    with _connect(fresh) as conn:
        conn.execute("INSERT INTO schema_migrations(version,name,checksum) VALUES ('015','unknown.sql',%s)", ('0'*64,))
    with pytest.raises(gate.RollbackBlocked):
        invoke(fresh)
    assert state(fresh) == 'legacy_v1'


def test_c5_unknown_data_contract_declaration_blocks(fresh, monkeypatch):
    original = gate.read_json
    def invalid(path):
        value = original(path)
        value['supported_data_contracts'] = ['unknown']
        return value
    monkeypatch.setattr(gate, 'read_json', invalid)
    with pytest.raises(gate.RollbackBlocked, match='supported_data_contract_declaration'):
        invoke(fresh)
    assert state(fresh) == 'legacy_v1'


def test_c6_unknown_epoch_blocks(fresh, monkeypatch):
    # Fault-inject a corrupt read result, without weakening the real DB CHECK.
    monkeypatch.setattr(epoch, 'read_state', lambda *args, **kwargs: {'epoch': 'unknown'})
    with pytest.raises(epoch.EpochBlocked, match='fresh_bootstrap_epoch_unknown'):
        invoke(fresh)


def test_c7_populated_business_state_blocks(fresh):
    # Negative-case fault injection into the private DB, never fixture seeding.
    with _connect(fresh) as conn:
        conn.execute("INSERT INTO tenants(id,name) VALUES ('synthetic-fault','Private fixture fault')")
    with pytest.raises(epoch.EpochBlocked, match='fresh_bootstrap_business_state_present'):
        invoke(fresh)
    assert state(fresh) == 'legacy_v1'


def test_c8_already_bootstrapped_is_idempotent(fresh):
    assert invoke(fresh)['status'] == 'bootstrapped'
    with _connect(fresh) as conn:
        previous = dict(conn.execute('SELECT * FROM platform_compatibility_state').fetchone())
    assert invoke(fresh)['status'] == 'already_bootstrapped'
    with _connect(fresh) as conn:
        assert dict(conn.execute('SELECT * FROM platform_compatibility_state').fetchone()) == previous


def test_c9_write_failure_rolls_back(fresh, monkeypatch):
    original = epoch._write_epoch_transition
    def fail_after_write(*args):
        original(*args)
        raise RuntimeError('isolated write fault')
    monkeypatch.setattr(epoch, '_write_epoch_transition', fail_after_write)
    with pytest.raises(RuntimeError, match='isolated write fault'):
        invoke(fresh)
    assert state(fresh) == 'legacy_v1'


def test_c12_target_is_not_caller_supplied():
    result = subprocess.run([sys.executable, str(PROJECT / 'scripts/compatibility_epoch.py'),
                             'bootstrap-current', '--to', 'legacy_v1'],
                            cwd=PROJECT, capture_output=True, text=True)
    assert result.returncode == 2
    assert '--config' in result.stderr
