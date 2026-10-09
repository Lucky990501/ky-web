"""Sealed Test-only permission service; no public Grant/Revoke HTTP endpoint.

One root-approved identity/Agent/Revision/run, existing platform_admins and
execution_events only. SQL lives here behind native authority and transactions,
never in an operator scratch script. No users.role update or schema creation.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import UUID

from scripts.wechat_runtime_test_lifecycle_guard import Blocked, digest, need

CONTRACT = 'EXACT_TEST_ADMIN_GRANT_REVOKE_V1'
ROOT = Path('/etc/enterprise-agent-test-exact-admin-v1')
PROJECT = Path(__file__).resolve().parents[1]
BASE_SOURCE = 'd8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7'
BASE_TREE = '6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5'
OPS = frozenset(('runtime_test', 'publish', 'configure', 'enable', 'catalog_read'))
EVENT = 'exact_test_admin.lifecycle'
REGISTRATION = 'ENTERPRISE_POC_EXACT_TEST_ADMIN_REQUIRED'


def utc(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    need(stamp.tzinfo is not None, 'EXACT_ADMIN_TIMEZONE_REQUIRED')
    return stamp.astimezone(timezone.utc)


def timestamp(value):
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    # SQLite isolated fixtures use UTC CURRENT_TIMESTAMP without a TZ suffix.
    # Actual PG timestamptz is aware; the native snapshot adapter must preserve it.
    if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat(timespec='microseconds')


def event_hash(row):
    normalized = dict(row); normalized['created_at'] = timestamp(row['created_at'])
    return digest(normalized)


def membership_hash(row):
    return digest({'user_id': row['user_id'], 'granted_at': timestamp(row['granted_at'])})


def principal_hash(row):
    from scripts.receipt_row_canonicalization import row_sha256
    normalized = dict(row); normalized['created_at'] = timestamp(row['created_at'])
    return row_sha256('users', normalized)


def validate_scope(scope):
    keys = {'contract', 'environment', 'base_source', 'base_tree', 'application_source',
        'application_tree', 'tooling_source', 'tooling_tree', 'tenant_id', 'principal_id',
        'principal_sha256', 'original_role', 'agent_id', 'agent_slug', 'revision_id',
        'fingerprint', 'run_id', 'operations', 'issued_at', 'expires_at', 'production_authority',
        'publication_authorized'}
    need(type(scope) is dict and set(scope) == keys, 'EXACT_ADMIN_SCOPE_SHAPE')
    need(scope['contract'] == CONTRACT and scope['environment'] == 'test'
        and scope['production_authority'] is False
        and (scope['base_source'], scope['base_tree']) == (BASE_SOURCE, BASE_TREE)
        and scope['original_role'] == 'member'
        and scope['agent_slug'] == 'wechat-official-account-writing'
        and scope['operations'] == sorted(OPS), 'EXACT_ADMIN_SCOPE_DOMAIN')
    need(type(scope['publication_authorized']) is bool, 'EXACT_ADMIN_PUBLISH_AUTHORITY_DOMAIN')
    for key in ('application_source', 'application_tree', 'tooling_source', 'tooling_tree'):
        need(isinstance(scope[key], str) and re.fullmatch('[a-f0-9]{40}', scope[key]), 'EXACT_ADMIN_SOURCE_DOMAIN')
    need(scope['application_source'] != BASE_SOURCE, 'EXACT_ADMIN_REQUEST_GATE_SUCCESSOR_REQUIRED')
    for key in ('principal_sha256', 'fingerprint'):
        need(isinstance(scope[key], str) and re.fullmatch('[a-f0-9]{64}', scope[key]), 'EXACT_ADMIN_DIGEST_DOMAIN')
    for key in ('principal_id', 'agent_id', 'revision_id', 'run_id'):
        need(str(UUID(scope[key])) == scope[key], 'EXACT_ADMIN_UUID_REQUIRED')
    need(isinstance(scope['tenant_id'], str) and re.fullmatch('[A-Za-z0-9_-]{1,128}', scope['tenant_id']),
        'EXACT_ADMIN_REGISTERED_TENANT_REQUIRED')
    duration = (utc(scope['expires_at']) - utc(scope['issued_at'])).total_seconds()
    need(0 < duration <= 3600, 'EXACT_ADMIN_LEASE_MAX_ONE_HOUR')
    return scope


def load_authority():
    """Root files are the only registration input; never request/env identity."""
    from app.test_tenant_seeding import native_json
    from app.skill_python_runtime import git_identity
    approval, _ = native_json(ROOT/'approval.v1.json')
    need(set(approval) == {'contract', 'scope_sha256', 'code_sha256', 'independent_approval'},
        'EXACT_ADMIN_APPROVAL_SHAPE')
    need(approval['contract'] == CONTRACT and approval['independent_approval'] == {
        'authority': 'PRIMARY_TEST_RELEASE_CONTROL',
        'authorization': 'TEST_ONLY_EXACT_ADMIN_LIFECYCLE_FIX_APPROVED',
        'production_authority': False}, 'EXACT_ADMIN_INDEPENDENT_APPROVAL_REQUIRED')
    scope, scope_sha = native_json(ROOT/'scope.v1.json')
    need(scope_sha == approval['scope_sha256'], 'EXACT_ADMIN_SCOPE_PIN')
    validate_scope(scope)
    identity = git_identity(PROJECT)
    need(identity == {'source_commit': scope['application_source'], 'source_tree': scope['application_tree']}
        and (scope['tooling_source'], scope['tooling_tree'])
        == (scope['application_source'], scope['application_tree']), 'EXACT_ADMIN_SOURCE_TREE_BINDING')
    dirty = subprocess.run(['git', '--no-optional-locks', '-C', str(PROJECT.parent), 'status',
        '--porcelain', '--untracked-files=no'], check=True, capture_output=True, timeout=10).stdout
    need(not dirty, 'EXACT_ADMIN_DIRTY_SOURCE_REJECTED')
    files = {'scripts/exact_test_admin_lifecycle.py', 'scripts/run_exact_test_admin_lifecycle.py',
        'app/test_exact_admin_gate.py', 'app/main.py'}
    need(set(approval['code_sha256']) == files, 'EXACT_ADMIN_CODE_SET')
    for relative, expected in approval['code_sha256'].items():
        path = PROJECT/relative
        need(not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
            'EXACT_ADMIN_CODE_PIN')
    return scope


def operation(scope, method, path, body):
    prefix = '/api/v1/platform/agents/' + scope['agent_id']
    revision = prefix + '/versions/' + scope['revision_id']
    routes = {('POST', revision+'/test'): 'runtime_test', ('POST', revision+'/publish'): 'publish',
        ('PUT', prefix+'/instances/'+scope['tenant_id']): 'configure',
        ('POST', prefix+'/instances/'+scope['tenant_id']+'/enable'): 'enable',
        ('GET', prefix): 'catalog_read'}
    op = routes.get((method, path))
    need(op in scope['operations'], 'EXACT_ADMIN_OPERATION_NOT_ALLOWED')
    if op in ('publish', 'configure', 'enable'):
        need(scope['publication_authorized'], 'EXACT_ADMIN_SEPARATE_PUBLICATION_AUTHORITY_REQUIRED')
    if op == 'runtime_test':
        need(body == {'configuration_fingerprint': scope['fingerprint']}, 'EXACT_ADMIN_RUNTIME_FINGERPRINT')
    elif op == 'publish':
        need(body == {'mode': 'production'}, 'EXACT_ADMIN_PUBLICATION_MODE')
    elif op == 'configure':
        need(body == {'agent_template_version_id': scope['revision_id'], 'overrides': {}}, 'EXACT_ADMIN_INSTANCE_SCOPE')
    else:
        need(body in ({}, None), 'EXACT_ADMIN_UNEXPECTED_BODY')
    return op


class ExactTestAdmin:
    """Existing registry membership model with a narrowly scoped formal writer.

    Native DB/source/gate checks precede writes. Recovery never grants, invokes
    a model, changes roles, or deletes anyone else's membership.
    """
    def __init__(self, store, *, environment, authority_loader=load_authority, gate_probe=None):
        self.store, self.environment = store, environment
        self.authority_loader, self.gate_probe = authority_loader, gate_probe

    def authority(self):
        need(self.environment == 'test', 'EXACT_ADMIN_PRODUCTION_REJECTED')
        return validate_scope(self.authority_loader())

    def _database(self, conn):
        from app.test_tenant_seeding import check_database
        check_database(conn, self.store)

    @contextmanager
    def _transaction(self):
        with self.store.connection() as conn:
            self._database(conn)
            if self.store.is_postgres:
                conn.execute('LOCK TABLE platform_admins IN SHARE ROW EXCLUSIVE MODE')
                conn.execute('LOCK TABLE execution_events IN SHARE ROW EXCLUSIVE MODE')
            else:  # Only isolated tests substitute _database; native rejects SQLite.
                conn.execute('BEGIN IMMEDIATE')
            yield conn

    def _subject(self, conn, scope, *, strict=True):
        rows = conn.execute('SELECT * FROM users WHERE id=? AND tenant_id=?',
            (scope['principal_id'], scope['tenant_id'])).fetchall()
        need(len(rows) == 1, 'EXACT_ADMIN_PRINCIPAL_TENANT_MISMATCH')
        row = dict(rows[0])
        if strict:
            need(row['role'] == scope['original_role'] and row['account_status'] == 'enabled'
                and principal_hash(row) == scope['principal_sha256'], 'EXACT_ADMIN_ORIGINAL_PRINCIPAL_DRIFT')
        return row

    def _binding(self, conn, scope):
        row = conn.execute('SELECT v.configuration_fingerprint,t.slug FROM agent_template_versions v '
            'JOIN agent_templates t ON t.id=v.agent_template_id WHERE v.id=? AND t.id=?',
            (scope['revision_id'], scope['agent_id'])).fetchone()
        need(row and (row['configuration_fingerprint'], row['slug'])
            == (scope['fingerprint'], scope['agent_slug']), 'EXACT_ADMIN_AGENT_REVISION_DRIFT')

    def _history(self, conn, scope):
        rows = conn.execute('SELECT * FROM execution_events WHERE event_type=? ORDER BY id', (EVENT,)).fetchall()
        history = []; previous = None
        for item in rows:
            row = dict(item); payload = json.loads(row['payload'])
            if payload.get('run_id') != scope['run_id']: continue
            identity = {k: scope[k] for k in ('contract', 'environment', 'application_source', 'application_tree',
                'tooling_source', 'tooling_tree', 'tenant_id', 'principal_id', 'agent_id', 'revision_id', 'run_id')}
            need(all(payload.get(k) == v for k, v in identity.items()) and payload.get('scope_sha256') == digest(scope)
                and payload.get('previous_sha256') == previous and row['conversation_id'] is None,
                'EXACT_ADMIN_AUDIT_CHAIN_REJECTED')
            need(payload.get('action') in {'prepared', 'granted', 'operation_started', 'operation_finished',
                'operation_abandoned', 'revoked'}, 'EXACT_ADMIN_AUDIT_ACTION_REJECTED')
            history.append((row, payload)); previous = event_hash(row)
        return history

    def _audit(self, conn, scope, action, history, **details):
        payload = {k: scope[k] for k in ('contract', 'environment', 'application_source', 'application_tree',
            'tooling_source', 'tooling_tree', 'tenant_id', 'principal_id', 'agent_id', 'revision_id', 'run_id')}
        payload.update(action=action, scope_sha256=digest(scope),
            previous_sha256=event_hash(history[-1][0]) if history else None, **details)
        conn.execute('INSERT INTO execution_events(conversation_id,event_type,payload) VALUES (NULL,?,?)',
            (EVENT, json.dumps(payload, ensure_ascii=False, sort_keys=True)))

    def _matching(self, scope, principal_id, tenant_id, run_id):
        need((principal_id, tenant_id, run_id) == (scope['principal_id'], scope['tenant_id'], scope['run_id']),
            'EXACT_ADMIN_CALLER_SCOPE_MISMATCH')

    def prepare(self, *, principal_id, tenant_id, run_id):
        scope = self.authority(); self._matching(scope, principal_id, tenant_id, run_id)
        need(utc(scope['issued_at']) <= datetime.now(timezone.utc) < utc(scope['expires_at']), 'EXACT_ADMIN_LEASE_EXPIRED')
        need(isinstance(self.gate_probe, FixedLoopbackCapabilityProbe) and self.gate_probe(scope) == {'contract': CONTRACT,
            'run_id': scope['run_id'], 'source': scope['application_source'], 'tree': scope['application_tree'],
            'gate_required': True}, 'EXACT_ADMIN_EFFECTIVE_REQUEST_GATE_NOT_PROVEN')
        with self._transaction() as conn:
            self._subject(conn, scope); self._binding(conn, scope)
            need(not conn.execute('SELECT * FROM platform_admins').fetchall(), 'EXACT_ADMIN_INITIAL_BASELINE_NOT_EMPTY')
            history = self._history(conn, scope)
            need(not history, 'EXACT_ADMIN_SINGLE_USE_RUN_CONSUMED')
            if self.store.is_postgres:
                hazards = conn.execute("SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid IN "
                    "('public.platform_admins'::regclass,'public.execution_events'::regclass) AND NOT tgisinternal) AS triggers, "
                    "EXISTS (SELECT 1 FROM pg_class WHERE oid IN ('public.platform_admins'::regclass,"
                    "'public.execution_events'::regclass) AND (relrowsecurity OR relforcerowsecurity)) AS rls, "
                    "EXISTS (SELECT 1 FROM pg_constraint WHERE contype='f' AND "
                    "confrelid='public.platform_admins'::regclass) AS inbound_fk").fetchone()
                need(dict(hazards) == {'triggers': False, 'rls': False, 'inbound_fk': False},
                    'EXACT_ADMIN_ROW_DELETE_SEMANTICS_NOT_PROVEN')
            # Execute exact revoke statement with a false predicate BEFORE Grant.
            # This tests executable DELETE/INSERT privilege without a row change.
            conn.execute('SAVEPOINT exact_admin_revoke_preflight')
            deleted = conn.execute('DELETE FROM platform_admins WHERE user_id=? AND 1=0', (scope['principal_id'],))
            need(deleted.rowcount == 0, 'EXACT_ADMIN_REVOKE_PREFLIGHT_CHANGED_ROWS')
            conn.execute('INSERT INTO execution_events(conversation_id,event_type,payload) '
                'SELECT NULL,?,? WHERE 1=0', (EVENT, '{}'))
            conn.execute('ROLLBACK TO SAVEPOINT exact_admin_revoke_preflight')
            conn.execute('RELEASE SAVEPOINT exact_admin_revoke_preflight')
            self._audit(conn, scope, 'prepared', history, revoke_statement_verified=True, original_admin_count=0)
        return {'status': 'REVOKE_PROVEN_BEFORE_GRANT', 'run_id': run_id}

    def grant(self, *, principal_id, tenant_id, run_id):
        scope = self.authority(); self._matching(scope, principal_id, tenant_id, run_id)
        need(datetime.now(timezone.utc) < utc(scope['expires_at']), 'EXACT_ADMIN_LEASE_EXPIRED')
        with self._transaction() as conn:
            self._subject(conn, scope); self._binding(conn, scope)
            history = self._history(conn, scope)
            need([p['action'] for _, p in history] == ['prepared'], 'EXACT_ADMIN_REVOKE_NOT_PREPARED_OR_RUN_CONSUMED')
            need(not conn.execute('SELECT * FROM platform_admins').fetchall(), 'EXACT_ADMIN_FOREIGN_ADMIN_PRESENT')
            conn.execute('INSERT INTO platform_admins(user_id) VALUES (?)', (scope['principal_id'],))
            row = dict(conn.execute('SELECT * FROM platform_admins WHERE user_id=?', (scope['principal_id'],)).fetchone())
            self._audit(conn, scope, 'granted', history, grant_row_sha256=membership_hash(row), expires_at=scope['expires_at'])
        return {'status': 'GRANTED', 'run_id': run_id}

    def _lease(self, conn, scope, history):
        grants = [p for _, p in history if p['action'] == 'granted']
        need(len(grants) == 1 and not any(p['action'] == 'revoked' for _, p in history), 'EXACT_ADMIN_NO_ACTIVE_LEASE')
        rows = conn.execute('SELECT * FROM platform_admins').fetchall()
        own = [dict(r) for r in rows if r['user_id'] == scope['principal_id']]
        need(len(own) == 1 and membership_hash(own[0]) == grants[0]['grant_row_sha256'], 'EXACT_ADMIN_OWNED_MEMBERSHIP_DRIFT')
        return rows

    def begin_operation(self, principal, method, path, body, run_id):
        scope = self.authority(); self._matching(scope, principal.user_id, principal.tenant_id, run_id)
        need(datetime.now(timezone.utc) < utc(scope['expires_at']), 'EXACT_ADMIN_LEASE_EXPIRED')
        op = operation(scope, method, path, body)
        with self._transaction() as conn:
            need(datetime.now(timezone.utc) < utc(scope['expires_at']), 'EXACT_ADMIN_LEASE_EXPIRED')
            self._subject(conn, scope); self._binding(conn, scope); history = self._history(conn, scope)
            need(len(self._lease(conn, scope, history)) == 1, 'EXACT_ADMIN_FOREIGN_ADMIN_PRESENT')
            need(not outstanding(history), 'EXACT_ADMIN_OPERATION_ALREADY_IN_FLIGHT')
            ticket = hashlib.sha256((scope['run_id']+':'+str(history[-1][0]['id'])).encode()).hexdigest()
            self._audit(conn, scope, 'operation_started', history, operation=op, ticket=ticket,
                method=method, path=path, body_sha256=digest(body), process_id=os.getpid(),
                pre_test_ids=[r['id'] for r in conn.execute('SELECT id FROM agent_template_tests WHERE '
                    'agent_template_version_id=? AND test_type=?', (scope['revision_id'], 'runtime')).fetchall()])
        return ticket

    def finish_operation(self, ticket, *, status_code, evidence=None):
        scope = self.authority()
        with self._transaction() as conn:
            history = self._history(conn, scope)
            need(outstanding(history) == {ticket}, 'EXACT_ADMIN_OPERATION_TICKET_MISMATCH')
            start = next(p for _, p in history if p['action'] == 'operation_started' and p['ticket'] == ticket)
            evidence = evidence or {}
            if start['operation'] == 'runtime_test':
                tests = [dict(r) for r in conn.execute('SELECT x.id,x.task_id FROM agent_template_tests x '
                    'JOIN tasks t ON t.id=x.task_id WHERE x.agent_template_version_id=? AND x.test_type=? '
                    'AND t.tenant_id=? AND t.user_id=? AND t.agent_id=?',
                    (scope['revision_id'], 'runtime', scope['tenant_id'], scope['principal_id'], scope['agent_id'])).fetchall()
                    if r['id'] not in start['pre_test_ids']]
                need(len(tests) <= 1, 'EXACT_ADMIN_RUNTIME_RESERVATION_COUNT')
                if tests:
                    if evidence:
                        need((evidence['runtime_test_id'], evidence['task_id']) == (tests[0]['id'], tests[0]['task_id']),
                            'EXACT_ADMIN_RUNTIME_RESPONSE_ASSOCIATION')
                    evidence = {'runtime_test_id': tests[0]['id'], 'task_id': tests[0]['task_id']}
            effects = {}
            for table, key, identity in (('agent_templates', 'id', scope['agent_id']),
                ('agent_template_versions', 'id', scope['revision_id'])):
                row = conn.execute('SELECT * FROM '+table+' WHERE '+key+'=?', (identity,)).fetchone()
                need(row is not None, 'EXACT_ADMIN_OPERATION_EFFECT_IDENTITY')
                effects[table] = digest(dict(row))
            rows = conn.execute('SELECT * FROM tenant_agent_instances WHERE tenant_id=? AND agent_id=?',
                (scope['tenant_id'], scope['agent_id'])).fetchall()
            need(len(rows) == 1, 'EXACT_ADMIN_OPERATION_INSTANCE_IDENTITY')
            effects['tenant_agent_instances'] = digest(dict(rows[0]))
            self._audit(conn, scope, 'operation_finished', history, ticket=ticket,
                operation=start['operation'], status_code=status_code, evidence=evidence, effects=effects)

    def revoke(self, *, principal_id, tenant_id, run_id, dead_operation_proof=None):
        scope = self.authority(); self._matching(scope, principal_id, tenant_id, run_id)
        with self._transaction() as conn:
            history = self._history(conn, scope)
            revoked = any(p['action'] == 'revoked' for _, p in history)
            if revoked:
                need(not conn.execute('SELECT * FROM platform_admins WHERE user_id=?', (scope['principal_id'],)).fetchall(),
                    'EXACT_ADMIN_POST_REVOKE_MEMBERSHIP_REAPPEARED')
            elif any(p['action'] == 'granted' for _, p in history):
                self._lease(conn, scope, history)
                tickets = outstanding(history)
                if tickets:
                    need(dead_operation_proof is load_dead_operation_proof and dead_operation_proof(scope, history) == tickets,
                        'EXACT_ADMIN_OPERATION_IN_FLIGHT_RECOVERY_PROOF_REQUIRED')
                    starts = [(row, p) for row, p in history if p['action'] == 'operation_started'
                        and p['ticket'] in tickets]
                    need(len(tickets) == len(starts) == 1, 'EXACT_ADMIN_RECOVERY_OPERATION_AMBIGUOUS')
                    start_row, start = starts[0]
                    details = {}
                    if start['operation'] == 'runtime_test':
                        self._subject(conn, scope); self._binding(conn, scope)
                        if self.store.is_postgres:
                            # Stable facts across the existing revoke transaction;
                            # the dead API cannot race a late reservation commit.
                            conn.execute('LOCK TABLE '+','.join(RESERVATION_TABLES)+' IN SHARE MODE')
                        data = {table: [dict(r) for r in conn.execute('SELECT * FROM '+table)]
                            for table in RESERVATION_TABLES}
                        details['recovered_admission'] = recovery_reservation(data, scope, start_row, start)
                    # Fact admission, abandonment, owned membership DELETE and
                    # revoked remain one transaction. No finish/HTTP status/PASS
                    # is fabricated, and this adds no execution permission.
                    self._audit(conn, scope, 'operation_abandoned', history, tickets=sorted(tickets), **details)
                    history = self._history(conn, scope)
                removed = conn.execute('DELETE FROM platform_admins WHERE user_id=?', (scope['principal_id'],))
                need(removed.rowcount == 1, 'EXACT_ADMIN_REVOKE_CAS_FAILED')
                self._audit(conn, scope, 'revoked', history, restored_original_admin_count=0)
            else:
                need(not conn.execute('SELECT * FROM platform_admins WHERE user_id=?', (scope['principal_id'],)).fetchall(),
                    'EXACT_ADMIN_UNOWNED_MEMBERSHIP_REJECTED')
                if history:
                    self._audit(conn, scope, 'revoked', history, restored_original_admin_count=0, grant_never_committed=True)
            remaining = len(conn.execute('SELECT * FROM platform_admins').fetchall())
            restored_subject = self._subject(conn, scope, strict=False)
        need(remaining == 0, 'EXACT_ADMIN_FOREIGN_ADMIN_REMAINS')
        need(restored_subject['role'] == scope['original_role'], 'EXACT_ADMIN_ORIGINAL_ROLE_NOT_RESTORED')
        return {'status': 'REVOKED', 'active_test_platform_admin': 0, 'run_id': run_id}


def outstanding(history):
    active = set()
    for _, payload in history:
        if payload['action'] == 'operation_started': active.add(payload['ticket'])
        elif payload['action'] == 'operation_finished': active.discard(payload['ticket'])
        elif payload['action'] == 'operation_abandoned': active.difference_update(payload['tickets'])
    return active


RESERVATION_TABLES = ('agent_template_tests', 'tasks', 'task_agent_contexts',
    'agent_execution_contexts', 'tenant_agent_instances', 'run_traces', 'conversations',
    'conversation_agent_contexts', 'conversation_owners', 'task_results', 'messages',
    'credit_transactions')


def recovery_reservation(data, scope, start_row, start):
    """Existing committed reservation fact, never a quality/execution grant.

    Shared by the protected writer and independent read-only ledger replay.
    The caller has already checked source/scope/hash chain and dead proof.
    Do not filter out foreign rows to make an ambiguous reservation look unique.
    """
    from scripts import wechat_runtime_test_lifecycle_guard as g
    need(start['operation'] == operation(scope, start['method'], start['path'],
        {'configuration_fingerprint': scope['fingerprint']}) == 'runtime_test',
        'EXACT_ADMIN_RECOVERY_RUNTIME_OPERATION')
    need(start['body_sha256'] == digest({'configuration_fingerprint': scope['fingerprint']}),
        'EXACT_ADMIN_RECOVERY_RUNTIME_OPERATION')
    pre = start.get('pre_test_ids')
    need(type(pre) is list and all(type(value) is str for value in pre)
        and len(set(pre)) == len(pre), 'EXACT_ADMIN_RECOVERY_PRE_TEST_IDS')
    tests = [r for r in data['agent_template_tests'] if r['test_type'] == 'runtime']
    need(all(sum(r['id'] == identity and r['agent_template_version_id'] == scope['revision_id']
        for r in tests) == 1 for identity in pre), 'EXACT_ADMIN_RECOVERY_PRE_TEST_IDS')
    extra = [r for r in tests if r['id'] not in pre]
    need(len(extra) <= 1, 'EXACT_ADMIN_RUNTIME_RESERVATION_COUNT')
    started = utc(timestamp(start_row['created_at']))
    need(utc(scope['issued_at']) <= started < utc(scope['expires_at']),
        'EXACT_ADMIN_RECOVERY_START_OUTSIDE_LEASE')
    if not extra:
        return {}  # No committed write to admit; revoke still restores authority.
    test = extra[0]
    created = utc(timestamp(test['created_at']))
    need(started <= created < utc(scope['expires_at']) and created <= datetime.now(timezone.utc),
        'EXACT_ADMIN_RECOVERY_RESERVATION_OUTSIDE_LEASE')
    # Reuse the existing formal Task/Run/context/status/result validator. In
    # particular a failed row is not accepted merely because status=failed.
    g.persisted_record(data, dict(tenant_id=scope['tenant_id'], actor_id=scope['principal_id'],
        agent_id=scope['agent_id'], revision_id=scope['revision_id'], fingerprint=scope['fingerprint'],
        model_config_id='codex-deepseek-v4-pro-high'), test)
    task = g.one(data, 'tasks', 'id', test['task_id'])
    mapping = g.one(data, 'task_agent_contexts', 'task_id', task['id'])
    return {'runtime_test_id': test['id'], 'task_id': task['id'], 'task_run_id': task['run_id'],
        'context_id': mapping['context_id'], 'reservation_created_at': timestamp(test['created_at']),
        'operation_ticket': start['ticket'], 'pre_test_ids_sha256': digest(pre)}


def load_dead_operation_proof(scope, history):
    """A fresh independent ROOT recovery attestation, never a user boolean.

    06 must quiesce/attest exact API process before installing this proof.
    No proof is created by the permission service or an ordinary API request.
    """
    from app.test_tenant_seeding import native_json
    proof, _ = native_json(ROOT/'dead-operation-proof.v1.json')
    need(set(proof) == {'contract', 'run_id', 'application_source', 'application_tree',
        'last_audit_sha256', 'tickets', 'quiesced_process_ids', 'observed_at', 'independent_approval'},
        'EXACT_ADMIN_RECOVERY_PROOF_SHAPE')
    tickets = outstanding(history)
    process_ids = {p['process_id'] for _, p in history if p['action'] == 'operation_started' and p['ticket'] in tickets}
    age = (datetime.now(timezone.utc)-utc(proof['observed_at'])).total_seconds()
    need(proof['contract'] == CONTRACT and proof['run_id'] == scope['run_id']
        and (proof['application_source'], proof['application_tree'])
        == (scope['application_source'], scope['application_tree'])
        and proof['last_audit_sha256'] == event_hash(history[-1][0])
        and set(proof['tickets']) == tickets and set(proof['quiesced_process_ids']) == process_ids
        and 0 <= age <= 60 and proof['independent_approval'] == 'EXACT_API_PROCESS_QUIESCENCE_ATTESTED_BY_06',
        'EXACT_ADMIN_RECOVERY_PROOF_REJECTED')
    return tickets


class FixedLoopbackCapabilityProbe:
    """Only registered Test API; no caller-selected URL/credentials principal."""
    def __init__(self, existing_session):
        self._session = existing_session

    def _request(self, scope):
        import urllib.request
        request = urllib.request.Request('http://127.0.0.1:18100/api/v1/internal/test/exact-admin-capability',
            headers={'Cookie': 'workbench_session='+self._session, 'X-Exact-Test-Admin-Run-Id': scope['run_id']})
        # No proxy/env/redirect fallback; never send the session to another host.
        class RejectRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise Blocked('EXACT_ADMIN_CAPABILITY_REDIRECT_REJECTED')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RejectRedirect())
        with opener.open(request, timeout=5) as response:
            raw = response.read(4097)
        need(len(raw) <= 4096, 'EXACT_ADMIN_CAPABILITY_RESPONSE_LIMIT')
        return json.loads(raw)

    def __call__(self, scope):
        return self._request(scope)
