"""Narrow lifecycle projection into the unchanged current Native Guard.

All new rows are checked before projection. Original Config/Terminal/Receipt/
seeding and unrelated-table checks are mandatory in the sealed predecessor.
This module never writes DB/source, edits a seal, installs or restarts services.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import sys

from scripts import exact_test_admin_lifecycle as a
from scripts import wechat_runtime_test_lifecycle_guard as g

VERSION = 'WECHAT_RUNTIME_TEST_NATIVE_SUCCESSOR_V1'
PARENT_OVERLAY_SHA = '5ba582e45b55e35aaeae83d5de995db00853ae83df45f0dab58d7d361c0c2c47'


def audit_history(data, scope):
    rows = [r for r in data['execution_events'] if r['event_type'] == a.EVENT]
    rows.sort(key=lambda r: r['id']); history = []; previous = None
    for row in rows:
        p = g.obj(row['payload'])
        identity = ('contract', 'environment', 'application_source', 'application_tree', 'tooling_source',
            'tooling_tree', 'tenant_id', 'principal_id', 'agent_id', 'revision_id', 'run_id')
        g.need(all(p.get(k) == scope[k] for k in identity) and p.get('scope_sha256') == g.digest(scope)
            and p.get('previous_sha256') == previous and row['conversation_id'] is None,
            'NATIVE_EXACT_ADMIN_AUDIT_SCOPE')
        history.append((row, p)); previous = a.event_hash(row)
    return history


def validate_ledger(data, scope, *, recovery=False, for_execution=False):
    a.validate_scope(scope); history = audit_history(data, scope)
    state = 'absent'; ticket = None; admissions = {}; operations = []
    grant = None
    for row, p in history:
        action = p['action']
        if action in {'granted', 'operation_started'}:
            stamp = datetime.fromisoformat(a.timestamp(row['created_at']))
            g.need(a.utc(scope['issued_at']) <= stamp < a.utc(scope['expires_at']), 'NATIVE_ADMIN_EVENT_OUTSIDE_LEASE')
        if action == 'prepared':
            g.need(state == 'absent' and p['revoke_statement_verified'] is True
                and p['original_admin_count'] == 0, 'NATIVE_ADMIN_PREPARE_STATE')
            state = 'prepared'
        elif action == 'granted':
            g.need(state == 'prepared' and p['expires_at'] == scope['expires_at'], 'NATIVE_ADMIN_GRANT_STATE')
            state = 'granted'; grant = p
        elif action == 'operation_started':
            g.need(state == 'granted' and ticket is None and p['operation'] in scope['operations']
                and type(p['process_id']) is int and p['process_id'] > 0, 'NATIVE_ADMIN_OPERATION_STATE')
            # Route/scope check must be independently re-applied by the native
            # reader; a valid chain alone cannot authorize a foreign route.
            expected_paths = {
                'runtime_test': ('POST', f"/api/v1/platform/agents/{scope['agent_id']}/versions/{scope['revision_id']}/test"),
                'publish': ('POST', f"/api/v1/platform/agents/{scope['agent_id']}/versions/{scope['revision_id']}/publish"),
                'configure': ('PUT', f"/api/v1/platform/agents/{scope['agent_id']}/instances/{scope['tenant_id']}"),
                'enable': ('POST', f"/api/v1/platform/agents/{scope['agent_id']}/instances/{scope['tenant_id']}/enable"),
                'catalog_read': ('GET', f"/api/v1/platform/agents/{scope['agent_id']}")}
            g.need((p['method'], p['path']) == expected_paths[p['operation']], 'NATIVE_ADMIN_OPERATION_ROUTE')
            body = {'runtime_test': {'configuration_fingerprint': scope['fingerprint']}, 'publish': {'mode': 'production'},
                'configure': {'agent_template_version_id': scope['revision_id'], 'overrides': {}},
                'enable': None, 'catalog_read': None}[p['operation']]
            g.need(p['body_sha256'] in {g.digest(body), g.digest({})} if body is None
                else p['body_sha256'] == g.digest(body), 'NATIVE_ADMIN_OPERATION_BODY')
            if p['operation'] in {'publish', 'configure', 'enable'}:
                g.need(scope['publication_authorized'], 'NATIVE_INDEPENDENT_PUBLISH_AUTHORITY')
            ticket = p
        elif action == 'operation_finished':
            g.need(state == 'granted' and ticket is not None and p['ticket'] == ticket['ticket']
                and p['operation'] == ticket['operation'], 'NATIVE_ADMIN_OPERATION_COMPLETION')
            operations.append(p)
            if p['operation'] == 'runtime_test' and p['evidence']:
                ev = p['evidence']
                g.need(set(ev) == {'runtime_test_id', 'task_id'} and ev['runtime_test_id'] not in admissions,
                    'NATIVE_RUNTIME_ADMISSION_DUPLICATE')
                admissions[ev['runtime_test_id']] = ev['task_id']
            ticket = None
        elif action == 'operation_abandoned':
            g.need(state == 'granted' and ticket is not None and p['tickets'] == [ticket['ticket']],
                'NATIVE_ADMIN_ABANDONED_STATE')
            ticket = None
        elif action == 'revoked':
            g.need(state in {'prepared', 'granted'} and ticket is None and p['restored_original_admin_count'] == 0,
                'NATIVE_ADMIN_REVOKE_STATE')
            state = 'revoked'
        else: raise g.Blocked('NATIVE_ADMIN_UNKNOWN_ACTION')
    admins = data['platform_admins']
    if state == 'granted':
        g.need(len(admins) == 1 and admins[0]['user_id'] == scope['principal_id']
            and a.membership_hash(admins[0]) == grant['grant_row_sha256'], 'NATIVE_ADMIN_EXACT_MEMBERSHIP')
        g.need(recovery or datetime.now(timezone.utc) < a.utc(scope['expires_at']), 'NATIVE_EXPIRED_ADMIN_NEEDS_RECOVERY')
    else: g.need(not admins, 'NATIVE_UNAPPROVED_ADMIN_MEMBERSHIP')
    if ticket is not None and for_execution:
        # The normal queue may consume before the API response is serialized.
        # Its exact signed admission START is sufficient for A, never for B/C.
        if ticket['operation'] == 'runtime_test':
            extra = [r for r in data['agent_template_tests'] if r['test_type'] == 'runtime'
                and r['id'] not in ticket['pre_test_ids']]
            g.need(len(extra) <= 1, 'NATIVE_INFLIGHT_RUNTIME_RESERVATION_COUNT')
            for row in extra: admissions[row['id']] = row['task_id']
        operations.append({'operation': ticket['operation'], 'status_code': 0, 'inflight': True})
    g.need(ticket is None or recovery or for_execution, 'NATIVE_OPERATION_IN_FLIGHT_NEEDS_RECOVERY')
    return history, admissions, operations, state


def validate_productization(data, scope, runtime_scope, anchors, operations, quality):
    g.need(set(anchors) == {'agent_templates', 'agent_template_versions', 'tenant_agent_instances'}, 'NATIVE_ANCHOR_SET')
    template = g.one(data, 'agent_templates', 'id', scope['agent_id'])
    revision = g.one(data, 'agent_template_versions', 'id', scope['revision_id'])
    instances = [r for r in data['tenant_agent_instances'] if
        (r['tenant_id'], r['agent_id']) == (scope['tenant_id'], scope['agent_id'])]
    g.need(len(instances) == 1, 'NATIVE_INSTANCE_IDENTITY')
    current = {'agent_templates': template, 'agent_template_versions': revision, 'tenant_agent_instances': instances[0]}
    mutable = {'agent_templates': {'current_published_version_id', 'lifecycle_status', 'published_at', 'updated_at', 'updated_by'},
        'agent_template_versions': {'status', 'publication_scope', 'published_at', 'updated_by'},
        'tenant_agent_instances': {'status', 'updated_at', 'overrides_json'}}
    for table, row in current.items():
        before = anchors[table]
        g.need(set(row) == set(before) and all(row[k] == before[k] for k in row if k not in mutable[table]),
            'NATIVE_NON_LIFECYCLE_MUTATION:'+table)
        if g.digest(row) == g.digest(before): continue
        op = 'publish' if table != 'tenant_agent_instances' else 'enable' if row['status'] == 'enabled' else 'configure'
        g.need(scope['publication_authorized'] and runtime_scope['publication_allowed'] and any(quality.values()),
            'NATIVE_PUBLISH_REAL_PASS_AND_AUTHORITY_REQUIRED')
        g.need(any(p['operation'] == op and ((p['status_code'] == 200
            and p['effects'][table] == g.digest(row)) or p.get('inflight') is True) for p in operations),
            'NATIVE_FORMAL_OPERATION_EVIDENCE_REQUIRED:'+table)
    if revision['status'] == 'published':
        g.need(revision['publication_scope'] == 'production' and revision['published_at'] is not None
            and revision['updated_by'] == scope['principal_id']
            and template['current_published_version_id'] == revision['id']
            and template['lifecycle_status'] == 'published' and template['updated_by'] == scope['principal_id'],
            'NATIVE_PUBLISHED_REVISION_RELATION')
    else: g.need(revision['status'] == 'draft' and template['current_published_version_id'] is None,
        'NATIVE_UNAPPROVED_PUBLISH_STATE')
    g.need(instances[0]['status'] in {'configured', 'enabled'} and g.obj(instances[0]['overrides_json']) == {},
        'NATIVE_INSTANCE_LIFECYCLE_STATE')
    if instances[0]['status'] == 'enabled':
        g.need(revision['status'] == 'published' and any(quality.values()), 'NATIVE_ENABLE_QUALITY_REQUIRED')
    return current


def validate_snapshot(data, *, scope, runtime_scope, anchors, parent_pins, predecessor_validate,
                      ordinary_principals, recovery=False, for_execution=False, historical_prepare=None):
    """Pure read-only adapter; caller MUST first validate all native identities.

    anchors must hash to the EXISTING protected pins, never newly sealed current
    published rows. Callback is the separately pinned current full validator,
    not a caller-provided replacement/no-op in the installed native wrapper.
    """
    history, admissions, operations, state = validate_ledger(data, scope, recovery=recovery, for_execution=for_execution)
    g.need((runtime_scope['tenant_id'], runtime_scope['actor_id'], runtime_scope['agent_id'], runtime_scope['revision_id'])
        == (scope['tenant_id'], scope['principal_id'], scope['agent_id'], scope['revision_id']), 'NATIVE_RUNTIME_SCOPE_JOIN')
    actor = g.one(data, 'users', 'id', scope['principal_id'])
    g.need(actor['role'] == scope['original_role'] and a.principal_hash(actor) == scope['principal_sha256'],
        'NATIVE_ORIGINAL_PRINCIPAL_PRESERVED')
    quality = g.validate_records(data, runtime_scope, environment='test', source=g.SOURCE, tree=g.TREE)
    records = [r for r in data['agent_template_tests'] if r['test_type'] == 'runtime']
    g.need({r['id']: r['task_id'] for r in records} == admissions, 'NATIVE_FORMAL_RUNTIME_AUTHORIZATION_REQUIRED')
    for table, row in anchors.items():
        g.need(g.digest(row) in parent_pins[table], 'NATIVE_EXISTING_BASELINE_ANCHOR_REQUIRED:'+table)
    current = validate_productization(data, scope, runtime_scope, anchors, operations, quality)
    cut = copy.deepcopy(data)
    cut['platform_admins'] = []  # ONLY after exact lease/zero-admin validation.
    accepted = {r['id'] for r, _ in history}
    cut['execution_events'] = [r for r in cut['execution_events'] if r['id'] not in accepted]
    cut['agent_template_tests'] = [r for r in cut['agent_template_tests'] if r['id'] not in admissions]
    for table, row in current.items():
        cut[table] = [copy.deepcopy(anchors[table]) if r == row else r for r in cut[table]]
    contexts = set(); historical = []
    for ctx in data['agent_execution_contexts']:
        if ctx['agent_id'] != scope['agent_id'] or g.digest(ctx) in parent_pins.get('agent_execution_contexts', []): continue
        g.need((ctx['tenant_id'], ctx['agent_template_version_id'], ctx['configuration_fingerprint'])
            == (scope['tenant_id'], scope['revision_id'], scope['fingerprint']), 'NATIVE_DYNAMIC_CONTEXT_SCOPE')
        mappings = [r for r in data['task_agent_contexts'] if r['context_id'] == ctx['id']]
        g.need(bool(mappings), 'NATIVE_ORPHAN_CONTEXT')
        is_historical = False
        for mapping in mappings:
            task = g.one(data, 'tasks', 'id', mapping['task_id'])
            g.need((task['tenant_id'], task['agent_id']) == (scope['tenant_id'], scope['agent_id']), 'NATIVE_CONTEXT_TASK_SCOPE')
            policy = g.obj(ctx['tool_policy_snapshot'])
            if policy.get('runtime_test') is True:
                g.need(task['id'] in admissions.values(), 'NATIVE_RUNTIME_CONTEXT_ADMISSION')
            elif policy.get('eligibility_mode') == 'SKILL_ONLY_TEST_QUALIFIED':
                from scripts.wechat_historical_prepare import HistoricalPrepare
                historical_prepare = historical_prepare or HistoricalPrepare.load()
                g.need(type(historical_prepare) is HistoricalPrepare, 'NATIVE_HISTORICAL_PREPARE_VERIFIER')
                historical.append(historical_prepare.verify(data, ctx, task))
                is_historical = True
            else:
                g.need(current['tenant_agent_instances']['status'] == 'enabled' and any(quality.values())
                    and task['user_id'] in ordinary_principals and policy.get('eligibility_mode') is None,
                    'NATIVE_ORDINARY_CHAT_AUTHORIZATION')
        # Historical context/maps remain unchanged in the predecessor's view.
        # Recognition is neither an active action grant nor a quality result.
        if not is_historical: contexts.add(ctx['id'])
    # Only proven new scoped contexts/maps are projected out of immutable
    # pre-runtime table pins; unrelated and original historical rows remain.
    cut['agent_execution_contexts'] = [r for r in cut['agent_execution_contexts'] if r['id'] not in contexts]
    for table in ('task_agent_contexts', 'conversation_agent_contexts'):
        cut[table] = [r for r in cut[table] if r['context_id'] not in contexts]
    result = predecessor_validate(cut)  # Mandatory full current Config/Terminal/V2/seeding checks.
    return {'status': 'PASS', 'contract': VERSION, 'admin_state': state,
        'active_test_platform_admin': len(data['platform_admins']), 'runtime_tests': len(records),
        'passed_runtime_tests': sum(quality.values()), 'predecessor': result, 'db_mutations': 0,
        'historical_prepares': historical}


PARENT_ROOT = Path('/etc/enterprise-agent-test-wechat-persistent-config-v1')


def native_file(path, expected):
    for component in (path, *path.parents):
        info = component.lstat()
        g.need(info.st_uid == info.st_gid == 0 and not stat.S_ISLNK(info.st_mode)
            and not info.st_mode & 0o022, 'NATIVE_SUCCESSOR_PATH_TRUST')
    g.need(path.is_file() and not path.stat().st_mode & 0o222
        and hashlib.sha256(path.read_bytes()).hexdigest() == expected, 'NATIVE_SUCCESSOR_FILE_PIN')


class NativeSuccessor:
    """Installed native entry; defaults refuse until fresh ROOT approvals exist.

    Two independently pinned authority documents: the exact admin approval and
    native-policy approval. Never accepted from a web request or env JSON.
    """
    VERSION = VERSION
    def verify(self, *, recovery=False, for_execution=False):
        from app.test_tenant_seeding import native_json, check_database
        from app.skill_python_runtime import git_identity
        from app.store import POCStore
        g.need(os.environ.get('APP_ENV') == 'test' and os.environ.get(a.REGISTRATION) == 'true'
            and sys.dont_write_bytecode and os.environ.get('PYTHONDONTWRITEBYTECODE') == '1',
            'NATIVE_SUCCESSOR_REQUIRED_ENVIRONMENT')
        scope = a.load_authority()
        approval, _ = native_json(a.ROOT/'native-approval.v1.json')
        g.need(set(approval) == {'contract', 'policy_sha256', 'code_sha256', 'independent_approval'}
            and approval['contract'] == VERSION and approval['independent_approval']
            == 'TEST_ONLY_EXACT_ADMIN_LIFECYCLE_FIX_APPROVED', 'NATIVE_SUCCESSOR_APPROVAL')
        policy, policy_sha = native_json(a.ROOT/'native-policy.v1.json')
        g.need(policy_sha == approval['policy_sha256'], 'NATIVE_SUCCESSOR_POLICY_PIN')
        code = {'scripts/wechat_runtime_native_successor.py', 'scripts/wechat_runtime_test_lifecycle_guard.py',
            'scripts/receipt_row_canonicalization.py', 'scripts/wechat_historical_prepare.py'}
        g.need(set(approval['code_sha256']) == code, 'NATIVE_SUCCESSOR_CODE_SET')
        for relative, expected in approval['code_sha256'].items():
            path = a.PROJECT/relative
            g.need(not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                'NATIVE_SUCCESSOR_CODE_PIN')
        expected = {'contract', 'application_source', 'application_tree', 'files', 'scope_sha256',
            'schema_fingerprint', 'table_set', 'runtime_scope', 'anchors', 'parent_pins', 'ordinary_principals',
            'parent_files', 'import_files'}
        g.need(set(policy) == expected and policy['contract'] == VERSION
            and (policy['application_source'], policy['application_tree'])
            == (scope['application_source'], scope['application_tree'])
            and policy['scope_sha256'] == g.digest(scope), 'NATIVE_SUCCESSOR_POLICY_SCOPE')
        g.need(git_identity(a.PROJECT) == {'source_commit': scope['application_source'], 'source_tree': scope['application_tree']},
            'NATIVE_SUCCESSOR_ACTUAL_SOURCE_TREE')
        actual = {}
        for path in a.PROJECT.parent.rglob('*'):
            if '.git' in path.relative_to(a.PROJECT.parent).parts: continue
            g.need(not path.is_symlink(), 'NATIVE_SUCCESSOR_INSTALLED_SYMLINK_REJECTED')
            if path.is_file():
                actual[path.relative_to(a.PROJECT.parent).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        # Exact map, not PYC filters or a new cache policy. 06 already cleaned
        # the predecessor caches; every new service/probe must retain -B/env=1.
        g.need(actual == policy['files'], 'PERSISTENT_INSTALLED_SOURCE_HASH')
        from scripts.receipt_row_canonicalization import VERSION as codec_version
        g.need(codec_version == 'RECEIPT_ROW_CANONICALIZATION_V2', 'NATIVE_SHARED_RECEIPT_CODEC')
        g.need(policy['parent_files'].get('primary_guard.py') == PARENT_OVERLAY_SHA,
            'NATIVE_CURRENT_PARENT_NOT_TERMINAL_ONLY')
        for name, sha in policy['parent_files'].items():
            g.need(name in {'primary_guard.py', 'policy.v1.json', 'approval.v1.json',
                'current-state-witness.v1.json', 'terminal-task-witness.v1.json'}, 'NATIVE_PARENT_FILE_SET')
            native_file(PARENT_ROOT/name, sha)
        g.need(len(policy['parent_files']) == 5, 'NATIVE_PARENT_WITNESSES_REQUIRED')
        imports = {
            '/etc/enterprise-agent-test-successor-wechat-d8a-seeding-approval-v1/common.py':
                'a40e35dc184746e7ba2f31a32e49054169c113645175aa98a455b26cdca72b09',
            '/etc/enterprise-agent-test-successor-wechat-dca-v1/common.py':
                '0e079b0fd87db36be8efadff63ce129e90e1a5e20dfa27cf4228a83fd1e90d6b'}
        g.need(policy['import_files'] == imports, 'NATIVE_PARENT_IMPORT_IDENTITY')
        for path, sha in imports.items(): native_file(Path(path), sha)
        spec = importlib.util.spec_from_file_location('exact_runtime_sealed_parent', PARENT_ROOT/'primary_guard.py')
        g.need(spec is not None and spec.loader is not None, 'NATIVE_PARENT_MODULE_LOAD')
        parent = importlib.util.module_from_spec(spec); spec.loader.exec_module(parent)
        _old_approval, old_policy, witness, terminal = parent.load_authority()
        parent.base.load(); parent.base.check_env()
        store = POCStore(os.environ['ENTERPRISE_POC_DATABASE_URL'])
        with store.connection() as conn: check_database(conn, store)
        schema, data = parent.base.snapshot()  # Existing consistent READ ONLY collector.
        old_base_policy = parent.read(parent.BASE/'policy.v1.json')
        g.need(policy['schema_fingerprint'] == old_base_policy['schema015_fingerprint']
            and set(policy['table_set']) == set(old_base_policy['table_set'])
            and parent.digest(schema) == policy['schema_fingerprint']
            and set(data) == set(policy['table_set']), 'NATIVE_SUCCESSOR_ACTUAL_SCHEMA')
        original = parent.read(parent.BASE/'predecessor-live-protected.v1.json')['row_hashes']
        g.need(all(policy['parent_pins'].get(t) == values for t, values in original.items()),
            'NATIVE_SUCCESSOR_ORIGINAL_SEAL_NOT_REPINNED')
        return validate_snapshot(data, scope=scope, runtime_scope=policy['runtime_scope'], anchors=policy['anchors'],
            parent_pins=policy['parent_pins'], ordinary_principals=policy['ordinary_principals'], recovery=recovery,
            for_execution=for_execution,
            predecessor_validate=lambda view: parent.validate_snapshot(view, old_policy, witness, terminal,
                environment='test', source=a.BASE_SOURCE, tree=a.BASE_TREE))
