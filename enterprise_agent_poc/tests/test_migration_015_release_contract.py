"""Real PG forward 015 transaction; separate marked Stage-1 cluster only."""
import copy
import json
import os
from pathlib import Path

import pytest

from scripts import migrate, release_migration_transition as transition
from tests.test_release_migration_transition import schema_012_store, declared as legacy_plan

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not os.environ.get('STAGE1_POSTGRES_ROOT'), reason='isolated Stage-1 PostgreSQL required')


@pytest.fixture
def schema014(schema_012_store, monkeypatch):
    transition.apply_declared(schema_012_store, legacy_plan())
    monkeypatch.setattr(migrate, 'MIGRATIONS', ROOT / 'migrations/postgres')
    return schema_012_store


def plan():
    return json.loads((ROOT / 'deploy/forward_migrations_015.json').read_text())


def test_015_exact_plan_apply_verify_and_revision_fingerprints(schema014):
    before = transition.read_only_plan(schema014, plan())
    assert before['schema'] == '014' and before['pending_versions'] == ['015']
    with schema014.connection() as conn:
        ledger = transition._history(conn)
    result = transition.apply_declared(schema014, plan())
    assert result['schema'] == '015' and result['applied'] == ['015'] and result['schema_rollback'] is False
    after = transition.read_only_verify(schema014, plan())
    assert after['schema'] == '015' and not after['pending_versions']
    assert after['fingerprints'] == before['fingerprints']
    with schema014.connection() as conn:
        assert transition._history(conn)[:14] == ledger
        assert conn.execute("SELECT to_regclass('public.chat_image_attachments') AS name").fetchone()['name']


def test_015_verify_rejects_schema014_and_reapply_rejected(schema014):
    with pytest.raises(transition.MigrationTransitionBlocked, match='unexpected_pending_or_unknown_migration'):
        transition.read_only_verify(schema014, plan())
    transition.apply_declared(schema014, plan())
    with pytest.raises(transition.MigrationTransitionBlocked, match='unexpected_pending_or_unknown_migration'):
        transition.apply_declared(schema014, plan())


def test_015_checksum_mismatch_blocks_before_ddl(schema014):
    changed = copy.deepcopy(plan())
    changed['migrations'][0]['canonical_sha256'] = '0' * 64
    from scripts.release_manifest import ManifestContractError
    with pytest.raises(ManifestContractError): transition.read_only_plan(schema014, changed)
    with schema014.connection() as conn:
        assert transition._history(conn)[-1]['version'] == '014'


def test_015_unknown_migration_inventory_blocks_before_ddl(schema014, monkeypatch):
    items = migrate.migration_items()
    monkeypatch.setattr(migrate, 'migration_items', lambda: items + [{**items[-1], 'version': '016'}])
    with pytest.raises(transition.MigrationTransitionBlocked, match='unexpected_candidate_migration_set'):
        transition.read_only_plan(schema014, plan())
