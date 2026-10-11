"""Read-only RAG Native startup and recovery interfaces, not a deployer.

The independently approved release operator owns lock, quiesce, pointer and
profile changes. This module never selects Approval, writes DB or cleans data.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

from app import test_runtime_tooling as pair_api

HISTORICAL_GUARD = Path('/etc/enterprise-agent-test-wechat-persistent-config-v1/primary_guard.py')
HISTORICAL_GUARD_SHA = '5ba582e45b55e35aaeae83d5de995db00853ae83df45f0dab58d7d361c0c2c47'
KNOWLEDGE_TABLES = ('knowledge_bases', 'knowledge_documents', 'knowledge_files',
                    'knowledge_chunks', 'knowledge_chunk_embeddings')
ROLES = ('api', 'mcp', 'worker')
require = pair_api.require


def knowledge_state(data):
    require(all(k in data for k in KNOWLEDGE_TABLES), 'RAG_KNOWLEDGE_TABLE_SET')
    return pair_api.digest({k: sorted(data[k], key=pair_api.digest) for k in KNOWLEDGE_TABLES})


def knowledge_baseline(data):
    return {k: sorted(pair_api.digest(row) for row in data[k]) for k in KNOWLEDGE_TABLES}


def validate_knowledge(data, tenant, users, baseline):
    """Strict approved append-only extension; never mutate/clean the snapshot.

    Existing rows must retain their exact hashes. New local-hash RAG rows may
    only belong to the independently approved tenant/users. Secondary-profile
    embeddings are NOT part of this RAG change and remain exact baseline.
    """
    require(set(baseline) == set(KNOWLEDGE_TABLES), 'RAG_BASELINE_TABLE_SET')
    require(tenant == 'internal-test-staging-v1' and bool(users), 'RAG_EXACT_TENANT_SCOPE')
    actors = {r['id']: r['tenant_id'] for r in data['users']}
    require(all(actors.get(u) == tenant for u in users), 'RAG_APPROVED_USER_SCOPE')
    files = {r['id']: r for r in data['knowledge_files']}
    bases = {r['id']: r for r in data['knowledge_bases']}
    chunks = {r['id']: r for r in data['knowledge_chunks']}
    historical = copy.deepcopy(data)
    for table in KNOWLEDGE_TABLES:
        require(isinstance(baseline[table], list) and all(isinstance(h, str) and
            len(h) == 64 and all(c in '0123456789abcdef' for c in h) for h in baseline[table]),
            'RAG_BASELINE_HASH_SHAPE')
        matched = [row for row in data[table] if pair_api.digest(row) in baseline[table]]
        require(sorted(pair_api.digest(r) for r in matched) == sorted(baseline[table]),
                'RAG_BASELINE_DATA_CHANGED')
        historical[table] = matched
        for row in data[table]:
            if pair_api.digest(row) in baseline[table]:
                continue
            require(table != 'knowledge_chunk_embeddings', 'RAG_SECONDARY_PROFILE_CHANGE')
            require(row['tenant_id'] == tenant, 'RAG_CROSS_TENANT')
            if table == 'knowledge_files':
                require(row.get('uploaded_by') in users, 'RAG_CROSS_USER')
            if 'uploaded_by' in row:
                require(row['uploaded_by'] is None or row['uploaded_by'] in users, 'RAG_CROSS_USER')
            if row.get('knowledge_base_id'):
                require(row['knowledge_base_id'] in bases and
                    bases[row['knowledge_base_id']]['tenant_id'] == tenant, 'RAG_BASE_RELATION')
            if table == 'knowledge_chunks':
                require(row['file_id'] in files and files[row['file_id']]['tenant_id'] == tenant,
                        'RAG_CHUNK_FILE_RELATION')
                require(row.get('knowledge_base_id') == files[row['file_id']].get('knowledge_base_id'),
                        'RAG_CHUNK_BASE_RELATION')
                require(files[row['file_id']].get('uploaded_by') in users, 'RAG_CHUNK_OWNER_RELATION')
    require(len(chunks) == len(data['knowledge_chunks']) and
            len(files) == len(data['knowledge_files']) and len(bases) == len(data['knowledge_bases']),
            'RAG_DUPLICATE_KNOWLEDGE_ID')
    return knowledge_state(data), historical


def verify_services(states, application, phase, role=None):
    require(set(states) == set(ROLES) and phase in {'ACTIVATING', 'COMMITTED'}, 'RAG_SERVICE_SNAPSHOT')
    expected = str(Path(application) / 'enterprise_agent_poc')
    for name, state in states.items():
        require(set(state) == {'cwd', 'pid', 'active', 'live_cwd', 'exec_start'}, 'RAG_SERVICE_SHAPE:' + name)
        require(state['cwd'] == expected, 'RAG_SERVICE_PROFILE_SOURCE:' + name)
        module = {'api': 'app.main:app', 'mcp': 'app.platform_mcp.server', 'worker': 'app.worker'}[name]
        require(module in state['exec_start'] and 'RAG_NATIVE_WORKER_IMPORT' not in state['exec_start'],
                'RAG_SERVICE_ENTRY:' + name)
        require(type(state['pid']) is int and state['pid'] >= 0, 'RAG_SERVICE_PID:' + name)
        if state['pid']:
            require(state['active'] in {'active', 'activating'} and state['live_cwd'] == expected,
                    'RAG_MIXED_LIVE_SOURCE:' + name)
        else:
            require(phase == 'ACTIVATING' and state['active'] in {'inactive', 'failed', 'activating'},
                    'RAG_SERVICE_NOT_RUNNING:' + name)
    require(role in (*ROLES, None), 'RAG_SERVICE_ROLE')


def service_snapshot():
    states = {}
    for name in ROLES:
        raw = subprocess.check_output(['/usr/bin/systemctl', 'show', 'enterprise-agent-test-' + name + '.service',
            '-p', 'MainPID', '-p', 'WorkingDirectory', '-p', 'ActiveState', '-p', 'ExecStart'], text=True, timeout=10)
        fields = dict(line.split('=', 1) for line in raw.splitlines())
        pid = int(fields['MainPID'])
        states[name] = {'pid': pid, 'active': fields['ActiveState'], 'cwd': fields['WorkingDirectory'],
            'live_cwd': str((Path('/proc') / str(pid) / 'cwd').resolve()) if pid else None,
            'exec_start': fields['ExecStart']}
    return states


def validate_recovery(record, pair, pair_sha, current_knowledge):
    require(set(record) == {'contract', 'pair_sha256', 'application_source', 'application_tree',
        'predecessor', 'baseline_knowledge_sha256', 'phase', 'business_writes_admitted'}, 'RAG_RELEASE_RECORD_SHAPE')
    require(record['contract'] == 'RAG_EXACT_TEST_RELEASE_STATE_V1' and record['pair_sha256'] == pair_sha
        and (record['application_source'], record['application_tree']) ==
        (pair['application']['source'], pair['application']['tree']) and record['predecessor'] == pair_api.PREDECESSOR
        and record['phase'] in {'ACTIVATING', 'COMMITTED'}, 'RAG_RELEASE_RECORD_IDENTITY')
    require(isinstance(record['baseline_knowledge_sha256'], str) and
        len(record['baseline_knowledge_sha256']) == 64 and all(c in '0123456789abcdef' for c in
        record['baseline_knowledge_sha256']), 'RAG_BASELINE_KNOWLEDGE_PIN')
    require(isinstance(current_knowledge, str) and len(current_knowledge) == 64 and
            all(c in '0123456789abcdef' for c in current_knowledge), 'RAG_CURRENT_KNOWLEDGE_PIN')
    require(type(record['business_writes_admitted']) is bool, 'RAG_WRITE_ADMISSION_SHAPE')
    changed = current_knowledge != record['baseline_knowledge_sha256']
    # Operator must irreversibly seal admission BEFORE starting any role. A
    # later equal hash does not prove that no upload/write ever happened.
    forward = changed or record['business_writes_admitted']
    return {'mode': 'FORWARD_SUCCESSOR_KEEP_DATA' if forward else 'EXACT_D8A_RESTORE_REQUIRES_NATIVE_GATES',
        'target': pair['application'] if forward else pair_api.PREDECESSOR,
        'knowledge_changed': changed, 'preserve_data': True, 'schema_down': False,
        'synthetic_cleanup': False, 'executes_recovery': False,
        'business_writes_admitted': record['business_writes_admitted'],
        'requires_protected_operator': True, 'requires_predecessor_native_gates': not forward,
        'd8a_rag_health': 'KNOWN_UNAVAILABLE_NOT_HEALTHY' if not forward else 'NOT_SELECTED'}


def native_state():
    pair, pair_sha = pair_api.load_pair()
    root = pair_api.authority_root()
    # Do not replay PRIMARY witness/Secret references against an invented clone.
    require(root == pair_api.ROOT, 'RAG_ISOLATED_DATA_CONTRACT_NOT_ISSUED')
    policy, policy_sha = pair_api.native_json(root / 'native-guard-policy.v1.json')
    approval, _ = pair_api.native_json(root / 'native-guard.approval.v1.json')
    require(set(policy) == {'contract', 'pair_sha256', 'historical_code_sha256', 'tenant_id', 'user_ids',
            'knowledge_baseline'}
        and policy['contract'] == 'RAG_NATIVE_D8A_DATA_CONTRACT_V1' and policy['pair_sha256'] == pair_sha,
        'RAG_NATIVE_POLICY')
    require(approval == {'contract': policy['contract'], 'policy_sha256': policy_sha,
        'authorization': 'RAG_NATIVE_GUARD_INDEPENDENTLY_APPROVED', 'production_authority': False}, 'RAG_NATIVE_APPROVAL')
    pins = policy['historical_code_sha256']
    require(isinstance(pins, dict) and pins.get(str(HISTORICAL_GUARD)) == HISTORICAL_GUARD_SHA,
            'RAG_HISTORICAL_GUARD_PIN')
    # The complete approved historical inventory is required, not only the
    # outer wrapper. No caller-selected verifier/module or arbitrary PYTHONPATH.
    require(len(pins) == 46 and all(p.startswith('/etc/enterprise-agent-') or
        p.startswith(str(pair_api.TEST_ROOT / 'shared/source-qualifications') + '/') for p in pins),
        'RAG_HISTORICAL_INVENTORY')
    for raw, pin in pins.items():
        path = Path(raw)
        pair_api.trusted(path, readonly=True)
        require(hashlib.sha256(path.read_bytes()).hexdigest() == pin, 'RAG_HISTORICAL_CODE_DRIFT')
    spec = importlib.util.spec_from_file_location('rag_sealed_d8a_data_guard', HISTORICAL_GUARD)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    _, historical_policy, witness, terminal = old.load_authority()
    old.base.load(); old.base.check_env()
    schema, data = old.base.snapshot()  # existing repeatable-read, READ ONLY collector
    require(old.digest(schema) == pair['schema']['fingerprint'], 'RAG_SCHEMA015_FINGERPRINT')
    knowledge, historical_view = validate_knowledge(data, policy['tenant_id'], policy['user_ids'],
        policy['knowledge_baseline'])
    # New independent policy permits ONLY the append-only knowledge extension.
    # The unchanged historical DATA validator still validates every baseline
    # row and every non-knowledge table. Its source arguments name the data
    # grammar, NOT authority for the successor, which must pass load_pair.
    # This in-memory view does NOT remove data in the DB or authorize cleanup.
    old.validate_snapshot(historical_view, historical_policy, witness, terminal,
        environment='test', source=pair_api.PREDECESSOR['source'], tree=pair_api.PREDECESSOR['tree'])
    from app.store import POCStore
    from app.settings import settings
    require(settings.environment == 'test' and settings.embedding_provider == 'local-hash'
        and settings.embedding_dimension == 128 and not settings.bootstrap_demo_data
        and settings.task_queue == 'redis', 'RAG_RUNTIME_CONFIG')
    store = POCStore(settings.database_url)
    with store.connection() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        require(conn.execute('SHOW server_version').fetchone()['server_version'] == '16.6', 'RAG_PG_VERSION')
        require(conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()['extversion'] == '0.8.6',
                'RAG_VECTOR_VERSION')
        from app.product_store import ProductStore
        ProductStore(store).require_knowledge_embedding_dimension(128, conn)
    record, _ = pair_api.native_json(root / 'release-state.v1.json')
    require(record['baseline_knowledge_sha256'] == knowledge_state(historical_view),
            'RAG_RELEASE_BASELINE_MISMATCH')
    decision = validate_recovery(record, pair, pair_sha, knowledge)
    return pair, pair_sha, record, decision


def guard(role):
    pair, pair_sha, record, decision = native_state()
    require(record['business_writes_admitted'] is True, 'RAG_WRITE_ADMISSION_REQUIRED')
    require((pair_api.TEST_ROOT / 'current').resolve() == Path(pair['application']['path']), 'RAG_CURRENT_SOURCE')
    verify_services(service_snapshot(), pair['application']['path'], record['phase'], role)
    return {'status': 'PASS', 'source': pair['application']['source'], 'tree': pair['application']['tree'],
        'tooling_source': pair['tooling']['source'], 'role': role, 'pair_sha256': pair_sha,
        'schema': '001-015', 'ddl': False, 'business_write': False, 'recovery': decision}


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('command', choices=('guard', 'recovery-plan'))
    parser.add_argument('--role', choices=ROLES)
    args = parser.parse_args()
    if args.command == 'guard':
        require(args.role is not None, 'RAG_SERVICE_ROLE')
        result = guard(args.role)
    else:
        require(args.role is None, 'RAG_RECOVERY_ROLE_FORBIDDEN')
        result = native_state()[3]
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not print driver errors/DSNs, credentials or raw business records.
        print(json.dumps({'status': 'BLOCKED', 'category': str(exc) if type(exc) is RuntimeError and
            str(exc).startswith('RAG_') else 'RAG_NATIVE_INPUT_OR_IO'}))
        raise SystemExit(2)
