"""Current015 must be explicit; historical014 never automatically accepts it."""
import copy
import json
from pathlib import Path
import shutil

import pytest

from scripts import rollback_preflight as gate
from scripts.current_schema_contract import current_schema

ROOT = Path(__file__).resolve().parents[1]


def historical():
    return gate.epoch_contract(gate.read_json(ROOT / 'deploy/rollback_compatibility.json'))['schema_migrations']


def test_current015_explicit_and_historical014_unchanged():
    lock = historical()
    value = current_schema(ROOT, lock)
    assert value['latest_schema'] == '015' and len(value['schema_migrations']) == 15
    assert len(lock) == 14 and lock[-1]['version'] == '014'
    with pytest.raises(gate.RollbackBlocked, match='source_migration_set'):
        gate.check_sources(ROOT, lock)


@pytest.mark.parametrize('fault', ['latest', 'checksum', 'history', 'unknown_field', 'version_type'])
def test_current015_contract_tamper_blocks(tmp_path, fault):
    (tmp_path / 'deploy').mkdir()
    value = gate.read_json(ROOT / 'deploy/current_schema_015.v1.json')
    if fault == 'latest': value['latest_schema'] = '016'
    elif fault == 'checksum': value['additional_migration']['canonical_sha256'] = '0' * 64
    elif fault == 'history': value['historical_schema_lock_sha256'] = '0' * 64
    elif fault == 'version_type': value['schema_version'] = True
    else: value['extra'] = 'automatic-latest'
    (tmp_path / 'deploy/current_schema_015.v1.json').write_text(json.dumps(value))
    with pytest.raises(gate.RollbackBlocked, match='current_schema_015_contract_identity'):
        current_schema(tmp_path, historical())


def test_historical014_fixture_remains_exact_and_cannot_be_current015(tmp_path):
    (tmp_path / 'deploy').mkdir()
    shutil.copy2(ROOT / 'deploy/current_schema_015.v1.json', tmp_path / 'deploy/current_schema_015.v1.json')
    sql = tmp_path / 'migrations/postgres'
    sql.mkdir(parents=True)
    for item in historical(): shutil.copy2(ROOT / 'migrations/postgres' / item['filename'], sql / item['filename'])
    assert len(gate.check_sources(tmp_path, historical())) == 14
    with pytest.raises(gate.RollbackBlocked, match='current_schema_015_blob'):
        current_schema(tmp_path, historical())


def test_unknown_future_inventory_is_not_current015(tmp_path):
    shutil.copytree(ROOT / 'deploy', tmp_path / 'deploy')
    shutil.copytree(ROOT / 'migrations', tmp_path / 'migrations')
    (tmp_path / 'migrations/postgres/016_synthetic_unknown.sql').write_text('SELECT 1;')
    with pytest.raises(gate.RollbackBlocked, match='source_migration_set'):
        current_schema(tmp_path, historical())


def test_historical_pin_change_cannot_be_smuggled_into_current_contract():
    lock = copy.deepcopy(historical())
    lock[0]['canonical_sha256'] = '0' * 64
    with pytest.raises(gate.RollbackBlocked, match='current_schema_historical_lock_identity'):
        current_schema(ROOT, lock)
