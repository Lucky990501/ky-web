"""Explicit, irreversible platform Data Contract advance. No reset/down/delete.

CLI accepts no DSN, release path, identity or declaration overrides. Production
configuration is loaded by the controlled launcher, never printed here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))
SCOPE = 'agent_data_contract'
CONTRACT = 'agent-productized-v1'
EPOCHS = {'legacy_v1': 1, 'productized_v1': 2}
V2_TABLES = ('agent_template_versions', 'agent_template_version_skills',
             'agent_template_version_tools', 'agent_template_tests', 'agent_execution_contexts',
             'conversation_agent_contexts', 'task_agent_contexts')


class EpochBlocked(ValueError):
    """Constant, non-sensitive gate reasons only."""


def require(condition, reason):
    if not condition:
        raise EpochBlocked(reason)


def has_productized_data(conn, *, include_execution=True):
    if conn.execute("SELECT 1 FROM agent_templates WHERE definition_source='productized' LIMIT 1").fetchone():
        return True
    if conn.execute('SELECT 1 FROM tenant_agent_instances WHERE agent_template_version_id IS NOT NULL OR instance_id IS NOT NULL OR overrides_json IS NOT NULL LIMIT 1').fetchone():
        return True
    tables=V2_TABLES if include_execution else V2_TABLES[:4]
    return any(conn.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() for table in tables)


def read_state(conn, *, postgres=True, lock=False):
    if postgres:
        exists = conn.execute("SELECT to_regclass('public.platform_compatibility_state') AS name").fetchone()['name']
    else:
        exists = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='platform_compatibility_state'").fetchone()
    require(bool(exists), 'compatibility_epoch_missing')
    rows = conn.execute('SELECT * FROM platform_compatibility_state' + (' FOR UPDATE' if lock and postgres else '')).fetchall()
    require(len(rows) == 1, 'compatibility_epoch_singleton')
    state = dict(rows[0])
    require(state['scope'] == SCOPE and state['contract_id'] == CONTRACT, 'compatibility_epoch_contract_identity')
    require(state['epoch'] in EPOCHS and state['epoch_rank'] == EPOCHS[state['epoch']], 'compatibility_epoch_invalid')
    if state['epoch'] == 'legacy_v1':
        require(state['advance_origin'] == 'initial_legacy' and all(state[k] is None for k in
            ('advanced_at', 'advanced_by_release_id', 'advanced_by_source_commit')), 'compatibility_epoch_provenance')
    else:
        require(state['advanced_at'] is not None and state['advance_origin'] in
                {'migration_detection', 'controlled_advance'}, 'compatibility_epoch_provenance')
        if state['advance_origin'] == 'controlled_advance':
            import re
            require(isinstance(state['advanced_by_release_id'], str) and re.fullmatch(r'[A-Za-z0-9._-]+', state['advanced_by_release_id'])
                    and isinstance(state['advanced_by_source_commit'], str) and re.fullmatch(r'[0-9a-f]{40}', state['advanced_by_source_commit']),
                    'compatibility_epoch_provenance')
        else:
            require(state['advanced_by_release_id'] is None and state['advanced_by_source_commit'] is None, 'compatibility_epoch_provenance')
    return state


def require_productized_epoch(conn, *, postgres):
    state = read_state(conn, postgres=postgres)
    if postgres:
        # Share lock is held through the INSERT commit; advance uses UPDATE lock.
        row = conn.execute("SELECT epoch,epoch_rank FROM platform_compatibility_state WHERE scope=? FOR SHARE", (SCOPE,)).fetchone()
        require(row is not None and row['epoch'] == 'productized_v1' and row['epoch_rank'] == 2, 'compatibility_epoch_not_advanced')
    else:
        require(state['epoch'] == 'productized_v1', 'compatibility_epoch_not_advanced')


def initialize_local(conn):
    """Minimal SQLite parity, only during explicit control-plane initialization."""
    conn.execute("""CREATE TABLE IF NOT EXISTS platform_compatibility_state (
      scope TEXT PRIMARY KEY CHECK(scope='agent_data_contract'), epoch TEXT NOT NULL,epoch_rank INTEGER NOT NULL,
      contract_id TEXT NOT NULL DEFAULT 'agent-productized-v1' CHECK(contract_id='agent-productized-v1'),
      advanced_at TEXT,advanced_by_release_id TEXT,advanced_by_source_commit TEXT,advance_origin TEXT NOT NULL,
      CHECK((epoch='legacy_v1' AND epoch_rank=1 AND advance_origin='initial_legacy' AND advanced_at IS NULL
        AND advanced_by_release_id IS NULL AND advanced_by_source_commit IS NULL)
        OR(epoch='productized_v1' AND epoch_rank=2 AND advanced_at IS NOT NULL AND advance_origin IN('migration_detection','controlled_advance'))))""")
    if not conn.execute('SELECT 1 FROM platform_compatibility_state').fetchone():
        # Stage 1 has no execution-context tables yet. All available V2 tables count.
        tables = {r['name'] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        has_v2 = bool(conn.execute("SELECT 1 FROM agent_templates WHERE definition_source='productized' LIMIT 1").fetchone())
        has_v2 |= any(conn.execute(f'SELECT 1 FROM {t} LIMIT 1').fetchone() is not None for t in V2_TABLES if t in tables)
        has_v2 |= conn.execute('SELECT 1 FROM tenant_agent_instances WHERE agent_template_version_id IS NOT NULL OR instance_id IS NOT NULL OR overrides_json IS NOT NULL LIMIT 1').fetchone() is not None
        conn.execute("INSERT INTO platform_compatibility_state(scope,epoch,epoch_rank,advanced_at,advance_origin) VALUES (?,?,?,?,?)",
                     (SCOPE, 'productized_v1' if has_v2 else 'legacy_v1', 2 if has_v2 else 1,
                      __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat() if has_v2 else None,
                      'migration_detection' if has_v2 else 'initial_legacy'))
    read_state(conn, postgres=False)
    conn.executescript("""
      CREATE TRIGGER IF NOT EXISTS epoch_no_delete BEFORE DELETE ON platform_compatibility_state
        BEGIN SELECT RAISE(ABORT,'compatibility_epoch_delete_forbidden'); END;
      CREATE TRIGGER IF NOT EXISTS epoch_no_decrease BEFORE UPDATE ON platform_compatibility_state
        WHEN NEW.scope<>OLD.scope OR NEW.contract_id<>OLD.contract_id OR OLD.epoch_rank=2
          OR NOT(NEW.epoch='productized_v1' AND NEW.epoch_rank=2 AND NEW.advance_origin='controlled_advance')
        BEGIN SELECT RAISE(ABORT,'compatibility_epoch_transition_forbidden'); END;
      CREATE TRIGGER IF NOT EXISTS epoch_template_insert BEFORE INSERT ON agent_templates
        WHEN NEW.definition_source='productized' AND NOT EXISTS(SELECT 1 FROM platform_compatibility_state
          WHERE scope='agent_data_contract' AND epoch='productized_v1' AND epoch_rank=2)
        BEGIN SELECT RAISE(ABORT,'compatibility_epoch_not_advanced'); END;
      CREATE TRIGGER IF NOT EXISTS epoch_template_update BEFORE UPDATE ON agent_templates
        WHEN NEW.definition_source='productized' AND NOT EXISTS(SELECT 1 FROM platform_compatibility_state
          WHERE scope='agent_data_contract' AND epoch='productized_v1' AND epoch_rank=2)
        BEGIN SELECT RAISE(ABORT,'compatibility_epoch_not_advanced'); END;
    """)


def workload_status(conn, queue):
    """Read-only drain proof for the repository's pending/processing queue model.

    Unknown queue entries fail closed; Redis processing has no separate TTL
    lease or retry scheduler in V1. A terminal V2 delivery still counts as retry.
    """
    ids = {r['id'] for r in conn.execute('SELECT t.id FROM tasks t JOIN task_agent_contexts c ON c.task_id=t.id')}
    ids |= {r['task_id'] for r in conn.execute('SELECT task_id FROM agent_template_tests WHERE task_id IS NOT NULL')}
    known = {r['id'] for r in conn.execute('SELECT id FROM tasks')}
    active_tasks = conn.execute("SELECT COUNT(*) AS n FROM tasks t JOIN task_agent_contexts c ON c.task_id=t.id WHERE t.status IN ('queued','running')").fetchone()['n']
    active_tests = conn.execute("SELECT COUNT(*) AS n FROM agent_template_tests WHERE test_type='runtime' AND status IN ('queued','running')").fetchone()['n']
    unfinished = conn.execute("SELECT COUNT(*) AS n FROM run_traces r JOIN tasks t ON t.run_id=r.run_id JOIN task_agent_contexts c ON c.task_id=t.id WHERE r.status NOT IN ('completed','failed','cancelled')").fetchone()['n']
    pending = queue._client.lrange(queue.pending, 0, -1)
    processing = queue._client.lrange(queue.processing, 0, -1)
    counts = {'active_productized_tasks': active_tasks, 'active_runtime_tests': active_tests,
              'unfinished_productized_runs': unfinished,
              'recoverable_v2_deliveries': sum(item in ids for item in pending),
              'active_v2_processing_leases': sum(item in ids for item in processing),
              'unknown_queue_entries': sum(item not in known for item in pending+processing)}
    return {'status': 'ROLLBACK_INCOMPLETE' if any(counts.values()) else 'quiesced', **counts}


def require_running_release(source):
    """Production advance checks actual live processes, not just release-current."""
    import subprocess
    for service in ('enterprise-agent-api','enterprise-agent-mcp','enterprise-agent-worker'):
        p=subprocess.run(['systemctl','show','--property=MainPID','--property=ActiveState',service+'.service'],
                         capture_output=True,text=True,timeout=5,check=True)
        properties=dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
        require(properties.get('ActiveState')=='active' and properties.get('MainPID','').isdigit(), 'current_service_not_active')
        pid=int(properties['MainPID'])
        require(pid>0 and Path(f'/proc/{pid}/cwd').resolve(strict=True)==source, 'current_service_release_identity')


def advance(base, trusted_root, database_url, *, policy_enabled=False, running_identity_check=None):
    """Testable core; CLI resolves these inputs exclusively from controlled config."""
    from psycopg import connect
    from psycopg.rows import dict_row
    from scripts import rollback_preflight as gate
    # Exact current application + independent approved floor; never timestamp sorting.
    declaration = gate.read_json(trusted_root / 'deploy/rollback_compatibility.json')
    contract = gate.epoch_contract(declaration)
    floor = next(e for e in contract['epochs'] if e['epoch'] == 'productized_v1')['minimum_target']
    current = base / 'release-current'
    require(current.is_symlink(), 'current_release_not_approved_productized_identity')
    current_source=current.resolve()
    require(current_source.parent.parent==base/'releases' and current_source.name=='enterprise_agent_poc',
            'current_release_not_approved_productized_identity')
    current_manifest=gate.read_json(current_source.parent/f'{current_source.parent.name}.manifest.json')
    current_reference={k:current_manifest[k] for k in ('release_id','source_commit')}
    require(current_source==base/'releases'/current_reference['release_id']/'enterprise_agent_poc',
            'current_release_not_approved_productized_identity')
    approved_current=next(e for e in contract['epochs'] if e['epoch']=='productized_v1')['allowed_targets']
    require(current_reference in approved_current, 'current_release_not_approved_productized_identity')
    gate.verify(base, trusted_root, floor['release_id'], floor['source_commit'], database_url=database_url)
    if current_reference!=floor:
        gate.verify(base,trusted_root,current_reference['release_id'],current_reference['source_commit'],database_url=database_url)
    (running_identity_check or require_running_release)(current_source)
    tooling_manifest=gate.read_json(trusted_root.parent/f'{trusted_root.parent.name}.manifest.json')
    tooling_id,tooling_commit=tooling_manifest['release_id'],tooling_manifest['source_commit']
    with connect(database_url, row_factory=dict_row) as conn:
        state = read_state(conn, lock=True)
        # Revalidate the complete history in the same transaction as advance.
        items = gate.check_sources(trusted_root, contract['schema_migrations'])
        rows = conn.execute('SELECT version,name,checksum,applied_at FROM schema_migrations ORDER BY version').fetchall()
        gate.check_history(rows, items)
        if state['epoch'] == 'productized_v1':
            return {'status': 'already_advanced', 'epoch': state['epoch'], 'epoch_rank': 2}
        require(not policy_enabled, 'runtime_test_policy_must_be_disabled')
        require(not has_productized_data(conn), 'legacy_epoch_productized_data_conflict')
        # V2 evidence is zero and DB guards prevent any new V2 commit until advance.
        conn.execute("UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,advanced_at=CURRENT_TIMESTAMP,advanced_by_release_id=?,advanced_by_source_commit=?,advance_origin='controlled_advance' WHERE scope=?".replace('?', '%s'),
                     (tooling_id, tooling_commit, SCOPE))
        return {'status': 'advanced', 'epoch': 'productized_v1', 'epoch_rank': 2,
                'release_id': tooling_id, 'source_commit': tooling_commit}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('status')
    sub.add_parser('quiescence')
    command = sub.add_parser('advance')
    command.add_argument('--to', required=True, choices=('productized_v1',))
    args = parser.parse_args()
    from app.settings import settings
    from scripts import rollback_preflight as gate
    try:
        if args.operation in {'status','quiescence'}:
            from psycopg import connect
            from psycopg.rows import dict_row
            require(settings.database_url.startswith(('postgresql://', 'postgres://')), 'postgresql_required')
            with connect(settings.database_url, row_factory=dict_row, options='-c default_transaction_read_only=on') as conn:
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                state = read_state(conn)
                require(state['epoch'] != 'legacy_v1' or not has_productized_data(conn), 'legacy_epoch_productized_data_conflict')
                if args.operation == 'quiescence':
                    from app.task_queue import RedisTaskQueue
                    require(settings.task_queue == 'redis', 'redis_queue_required_for_drain_proof')
                    result = workload_status(conn, RedisTaskQueue.from_settings(settings))
                else:
                    result = {'status': 'ok', 'epoch': state['epoch'], 'epoch_rank': state['epoch_rank'], 'read_only': True}
            result['read_only'] = True
            if result['status'] == 'ROLLBACK_INCOMPLETE':
                print(json.dumps(result))
                raise SystemExit(2)
        else:
            result = advance(gate.BASE, ROOT, settings.database_url,
                             policy_enabled=settings.agent_runtime_test_production_enabled)
        print(json.dumps(result))
    except Exception as exc:
        reason = str(exc) if isinstance(exc, (EpochBlocked, gate.RollbackBlocked)) else 'compatibility_epoch_gate'
        print(json.dumps({'status': 'BLOCKED', 'check': reason}))
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
