"""Test-only lifecycle checks for d8a's FORMAL Runtime Test path.

A (execution admission) is independent of B (persisted result) and C
(publication qualification). No SQL writer, Provider call, table exemption,
native graph replacement or mutable-witness minting is implemented here.
Only an independently sealed native wrapper may supply a root-approved scope.
Exact current PRIMARY wrapper integration remains gated on 06's snapshot.
"""
from __future__ import annotations

import hashlib
import json

VERSION = 'WECHAT_FORMAL_RUNTIME_TEST_LIFECYCLE_GUARD_V1'
SOURCE = 'd8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7'
TREE = '6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5'
BASE_TOOLING_SOURCE = '7af76f1a27cc15d197566835f57f4b7fd6dc1ee7'
BASE_TOOLING_TREE = '765a0621edef321a90f02d9fbfb366168e179f55'
FIELDS = frozenset(('id', 'agent_template_version_id', 'configuration_fingerprint',
                    'test_type', 'task_id', 'status', 'result_json', 'created_at'))
TERMINAL = frozenset(('passed', 'failed', 'invalidated'))
EDGES = {'queued': {'queued', 'running', 'failed', 'invalidated'},
         'running': {'running', 'passed', 'failed', 'invalidated'},
         'passed': {'passed', 'invalidated'}, 'failed': {'failed', 'invalidated'},
         'invalidated': {'invalidated'}}


class Blocked(RuntimeError):
    pass


def need(value, code):
    if not value:
        raise Blocked(code)


def digest(row):
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), default=str).encode()).hexdigest()


def obj(value):
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        raise Blocked('RUNTIME_LIFECYCLE_JSON_REJECTED') from None
    need(type(result) is dict, 'RUNTIME_LIFECYCLE_JSON_REJECTED')
    return result


def one(data, table, key, identity):
    matches = [row for row in data[table] if row[key] == identity]
    need(len(matches) == 1, 'RUNTIME_LIFECYCLE_RELATION:' + table)
    return matches[0]


def check_scope(scope, *, environment, source, tree):
    need(set(scope) == {'contract', 'environment', 'source', 'tree', 'tenant_id',
        'agent_id', 'agent_slug', 'revision_id', 'fingerprint', 'actor_id',
        'actor_sha256', 'maximum_runtime_tests', 'publication_allowed', 'skill_id',
        'skill_revision_id', 'skill_sha256', 'model_config_id'}, 'RUNTIME_SCOPE_SHAPE')
    need(scope['contract'] == VERSION and environment == scope['environment'] == 'test',
        'RUNTIME_EXACT_TEST_ENVIRONMENT')
    need((source, tree) == (scope['source'], scope['tree']) == (SOURCE, TREE),
        'RUNTIME_EXACT_APPLICATION_IDENTITY')
    need(scope['agent_slug'] == 'wechat-official-account-writing', 'RUNTIME_AGENT_SLUG')
    need(all(isinstance(scope[key], str) and scope[key] and '*' not in scope[key]
        for key in ('tenant_id', 'agent_id', 'revision_id', 'actor_id')), 'RUNTIME_EXACT_SCOPE_REQUIRED')
    need(type(scope['maximum_runtime_tests']) is int and 1 <= scope['maximum_runtime_tests'] <= 8
        and type(scope['publication_allowed']) is bool, 'RUNTIME_AUTHORIZATION_DOMAIN')
    need(scope['model_config_id'] == 'codex-deepseek-v4-pro-high', 'RUNTIME_APPROVED_MODEL_CONFIG')
    for key in ('actor_sha256', 'fingerprint', 'skill_sha256'):
        need(len(scope[key]) == 64 and all(c in '0123456789abcdef' for c in scope[key]),
            'RUNTIME_AUTHORIZATION_DIGEST')


def identities(data, scope):
    actor = one(data, 'users', 'id', scope['actor_id'])
    need(actor['tenant_id'] == scope['tenant_id'] and actor['account_status'] == 'enabled'
        and digest(actor) == scope['actor_sha256'], 'RUNTIME_ACTOR_AUTHORITY')
    template = one(data, 'agent_templates', 'id', scope['agent_id'])
    revision = one(data, 'agent_template_versions', 'id', scope['revision_id'])
    need(template['slug'] == scope['agent_slug'] and template['definition_source'] == 'productized'
        and revision['agent_template_id'] == template['id']
        and revision['configuration_fingerprint'] == scope['fingerprint']
        and revision['model_config_id'] == scope['model_config_id'], 'RUNTIME_AGENT_REVISION_IDENTITY')
    bindings = [r for r in data['agent_template_version_skills'] if r['agent_template_version_id'] == revision['id']]
    need(len(bindings) == 1 and (bindings[0]['skill_id'], bindings[0]['skill_version_id'])
        == (scope['skill_id'], scope['skill_revision_id']), 'RUNTIME_EXACT_SKILL_BINDING')
    skill = one(data, 'skill_versions', 'id', scope['skill_revision_id'])
    package = one(data, 'skill_packages', 'skill_version_id', scope['skill_revision_id'])
    need(skill['skill_id'] == scope['skill_id'] and skill['status'] == 'published'
        and skill['checksum'] == package['sha256'] == scope['skill_sha256'], 'RUNTIME_EXACT_SKILL_REVISION')
    return actor, template, revision


def execution_admission(data, scope, *, environment, source, tree, authenticated_actor_id):
    """A: native isolation callback; NEVER requires a previous Runtime PASS.

    This checks lifecycle eligibility, NOT Provider network/budget/credentials.
    The unchanged formal API still performs signed-session/platform-admin,
    model, Skill integrity, readiness and queue checks before calling executor.
    """
    check_scope(scope, environment=environment, source=source, tree=tree)
    need(authenticated_actor_id == scope['actor_id'], 'RUNTIME_AUTHENTICATED_ACTOR_REQUIRED')
    _, _, revision = identities(data, scope)
    need(revision['status'] == 'draft', 'RUNTIME_DRAFT_REQUIRED')
    admins = data['platform_admins']
    need(len(admins) == 1 and admins[0]['user_id'] == scope['actor_id'], 'RUNTIME_PLATFORM_ADMIN_REQUIRED')
    # Grant provenance/expiry MUST also pass the separate formal-admin adapter;
    # presence in this table does not certify an authorized Grant operation.
    instances = [r for r in data['tenant_agent_instances'] if
        (r['tenant_id'], r['agent_id']) == (scope['tenant_id'], scope['agent_id'])]
    need(len(instances) == 1 and instances[0]['status'] == 'configured'
        and instances[0]['agent_template_version_id'] == scope['revision_id'], 'RUNTIME_CONFIGURED_INSTANCE')
    need(any(r['test_type'] == 'validation' and r['status'] == 'passed' and r['task_id'] is None
        and (r['agent_template_version_id'], r['configuration_fingerprint'])
        == (scope['revision_id'], scope['fingerprint']) for r in data['agent_template_tests']),
        'RUNTIME_CONFIGURATION_VALIDATION_REQUIRED')
    need(sum(r['test_type'] == 'runtime' for r in data['agent_template_tests'])
        < scope['maximum_runtime_tests'], 'RUNTIME_TEST_SCOPE_LIMIT')
    return {'execution_eligible': True, 'publish_eligible': False, 'provider_authorized': False}


def check_transition(before, after):
    """The sealed observer may check executor transitions, never authorize PASS."""
    need(set(before) == set(after) == FIELDS, 'RUNTIME_TEST_ROW_SCHEMA')
    immutable = FIELDS - {'status', 'result_json'}
    need(all(before[k] == after[k] for k in immutable), 'RUNTIME_TEST_IDENTITY_MUTATION')
    need(before['status'] in EDGES and after['status'] in EDGES[before['status']],
        'RUNTIME_TEST_ILLEGAL_TRANSITION')
    if before['status'] in TERMINAL and before['status'] == after['status']:
        need(before['result_json'] == after['result_json'], 'RUNTIME_TERMINAL_EVIDENCE_MUTATION')


def persisted_record(data, scope, test):
    """B: exact joins and formal executor evidence, including legal failures."""
    from app.agent_execution import completion_evidence
    need(set(test) == FIELDS and test['test_type'] == 'runtime' and test['status'] in EDGES,
        'RUNTIME_TEST_ROW_SCHEMA')
    need((test['agent_template_version_id'], test['configuration_fingerprint'])
        == (scope['revision_id'], scope['fingerprint']), 'RUNTIME_TEST_REVISION_SCOPE')
    task = one(data, 'tasks', 'id', test['task_id'])
    need((task['tenant_id'], task['user_id'], task['agent_id'])
        == (scope['tenant_id'], scope['actor_id'], scope['agent_id']), 'RUNTIME_TASK_OWNERSHIP')
    mapping = one(data, 'task_agent_contexts', 'task_id', task['id'])
    context = one(data, 'agent_execution_contexts', 'id', mapping['context_id'])
    need((context['tenant_id'], context['agent_id'], context['agent_template_version_id'],
          context['configuration_fingerprint']) == (scope['tenant_id'], scope['agent_id'],
          scope['revision_id'], scope['fingerprint']) and context['runtime_provider'] == 'codex'
        and (context['model_config_id'], context['model_provider_id_snapshot'], context['model_id_snapshot'],
            context['reasoning_level_snapshot']) == (scope['model_config_id'], 'deepseek', 'deepseek-v4-pro', 'high')
        and context['profile_hash_version'] == 'v2', 'RUNTIME_CONTEXT_IDENTITY')
    policy = obj(context['tool_policy_snapshot'])
    need(policy.get('runtime_test') is True and policy.get('eligibility_mode') != 'SKILL_ONLY_TEST_QUALIFIED',
        'FORMAL_RUNTIME_TEST_CONTEXT_REQUIRED')
    instance = one(data, 'tenant_agent_instances', 'instance_id', context['instance_id'])
    need((instance['tenant_id'], instance['agent_id'], instance['agent_template_version_id'])
        == (scope['tenant_id'], scope['agent_id'], scope['revision_id']), 'RUNTIME_INSTANCE_IDENTITY')
    result = obj(test['result_json'])
    trace = None
    if task['run_id'] is not None:
        trace = one(data, 'run_traces', 'run_id', task['run_id'])
        need((trace['tenant_id'], trace['agent_id'], trace['conversation_id'])
            == (task['tenant_id'], task['agent_id'], task['conversation_id']), 'RUNTIME_RUN_OWNERSHIP')
        conversation = one(data, 'conversations', 'id', task['conversation_id'])
        owners = [r for r in data['conversation_owners'] if r['conversation_id'] == task['conversation_id']]
        # d8a creates ownership during successful final-result persistence.
        # A failed new Run can legitimately have no owner yet; a conflicting
        # existing owner is still forbidden, and successful Runs require one.
        need(len(owners) <= 1 and all(r['user_id'] == scope['actor_id'] for r in owners)
            and (task['status'] != 'completed' or len(owners) == 1), 'RUNTIME_CONVERSATION_OWNERSHIP')
        association = one(data, 'conversation_agent_contexts', 'conversation_id', task['conversation_id'])
        need((conversation['tenant_id'], conversation['agent_id'], conversation['runtime_profile_id'])
            == (scope['tenant_id'], scope['agent_id'], context['runtime_profile_id'])
            and association['context_id'] == context['id'],
            'RUNTIME_CONVERSATION_OWNERSHIP')
    if test['status'] == 'queued':
        need(task['status'] == 'queued' and task['run_id'] is None
            and result == {'runtime_test_status': 'queued'}, 'RUNTIME_QUEUED_STATE')
        return False
    if test['status'] == 'running':
        need(task['status'] in {'running', 'completed', 'failed', 'cancelled'}
            and result.get('worker_started') is True and type(result.get('worker_pid')) is int
            and result['worker_pid'] > 0 and set(result) == {'worker_started', 'worker_pid'},
            'RUNTIME_RUNNING_STATE')
        return False  # A completed Task awaiting finished() is NOT a test PASS.
    need(task['status'] in {'completed', 'failed', 'cancelled'}, 'RUNTIME_TERMINAL_TASK_REQUIRED')
    if test['status'] == 'failed' and task.get('error_code') == 'enqueue_failed':
        need(trace is None and result == {'runtime_test_status': 'queued'}, 'RUNTIME_ENQUEUE_FAILURE_EVIDENCE')
        return False
    if test['status'] == 'failed' and task.get('error_code') == 'worker_interrupted':
        need(result.get('worker_interrupted') is True and type(result.get('worker_pid')) is int
            and result['worker_pid'] > 0 and set(result) == {'worker_interrupted', 'worker_pid'},
            'RUNTIME_INTERRUPTION_EVIDENCE')
        return False
    need(trace is not None or task['status'] in {'failed', 'cancelled'}, 'RUNTIME_TERMINAL_RUN_REQUIRED')
    payload = obj(trace['payload']) if trace is not None else {}
    manifest = obj(context['skill_manifest_snapshot'])
    discovered = {r.get('skill') for r in payload.get('lifecycle_events', []) if r.get('event') == 'skill_discovered'}
    skills_ok = set(manifest) <= discovered
    passed = (trace is not None and task['status'] == trace['status'] == 'completed'
        and payload.get('execution_context_id') == context['id'] and completion_evidence(payload)
        and skills_ok and (context['output_policy'] != 'image_required' or payload.get('artifact_completed') is True))
    expected = {'status': 'passed' if passed else 'failed', 'task_id': task['id'],
        'version_id': context['agent_template_version_id'], 'configuration_fingerprint': scope['fingerprint'],
        'context_id': context['id'], 'instance_id': context['instance_id'], 'run_id': task['run_id'],
        'conversation_id': task['conversation_id'], 'runtime_profile_id': context['runtime_profile_id'],
        'skill_discovery_passed': skills_ok, 'execution_chain': 'codex-runtime-persisted' if passed else 'codex-runtime-failed',
        'worker_started': True, 'worker_pid': result.get('worker_pid'),
        **{k: payload.get(k) for k in ('runtime_completed', 'required_tool_calls_completed',
            'final_response_received', 'final_response_persisted', 'artifact_completed')}}
    need(type(result.get('worker_pid')) is int and result['worker_pid'] > 0
        and result == expected and test['status'] == expected['status'], 'RUNTIME_RESULT_EVIDENCE_MISMATCH')
    if passed:
        stored = one(data, 'task_results', 'task_id', task['id'])
        need(isinstance(stored['final_response'], str) and bool(stored['final_response'].strip())
            and stored['final_response'] == payload.get('final_result'),
            'RUNTIME_FINAL_RESPONSE_PERSISTENCE_REQUIRED')
        need(any(r['conversation_id'] == task['conversation_id'] and r['role'] == 'assistant'
            and r['content'] == stored['final_response'] for r in data['messages']),
            'RUNTIME_ASSISTANT_MESSAGE_PERSISTENCE_REQUIRED')
        charge = one(data, 'credit_transactions', 'task_id', task['id'])
        need((charge['tenant_id'], charge['user_id'], charge['amount'], charge['reason'])
            == (scope['tenant_id'], scope['actor_id'], -context['credit_cost'], scope['agent_slug']),
            'RUNTIME_COMPLETION_CREDIT_ASSOCIATION')
    return passed


def validate_records(data, scope, *, environment, source, tree):
    check_scope(scope, environment=environment, source=source, tree=tree)
    identities(data, scope)
    records = [row for row in data['agent_template_tests'] if row['test_type'] == 'runtime']
    need(len(records) <= scope['maximum_runtime_tests'], 'RUNTIME_TEST_SCOPE_LIMIT')
    need(len({r['task_id'] for r in records}) == len(records)
        and len({r['id'] for r in records}) == len(records), 'RUNTIME_TEST_DUPLICATE_ASSOCIATION')
    for mapping in data['task_agent_contexts']:
        ctx = one(data, 'agent_execution_contexts', 'id', mapping['context_id'])
        if ctx['agent_id'] == scope['agent_id'] and obj(ctx['tool_policy_snapshot']).get('runtime_test') is True:
            need(any(r['task_id'] == mapping['task_id'] for r in records), 'RUNTIME_ORPHAN_TEST_CONTEXT')
    return {row['id']: persisted_record(data, scope, row) for row in records}


def publication_eligibility(data, scope, *, resolver, connection):
    """C: independent root publication authority AND unchanged product quality."""
    quality = validate_records(data, scope, environment='test', source=SOURCE, tree=TREE)
    need(scope['publication_allowed'] is True, 'RUNTIME_PUBLISH_AUTHORIZATION_REQUIRED')
    need(any(quality.values()), 'RUNTIME_REAL_PASS_REQUIRED_FOR_PUBLISH')
    _, _, revision = identities(data, scope)
    need(resolver._runtime_passed(connection, revision), 'PRODUCT_RUNTIME_QUALITY_REQUIRED')
    return True  # NOT a publish/enable write; formal APIs still enforce gates.


def require_formal_admin_adapter(adapter):
    """d8a exposes Grant, but no formal audited Revoke/lease API.

    Never compensate with SQL or pretend an in-memory lease is a formal
    permission operation. A reviewed successor is needed for that gap.
    """
    need(adapter is not None, 'FORMAL_ADMIN_GRANT_REVOKE_ADAPTER_UNAVAILABLE')
    raise Blocked('FORMAL_ADMIN_ADAPTER_NOT_QUALIFIED_FOR_D8A')


def live_preflight(native_adapter, *, expected_adapter_sha256):
    """Opt-in, separately sealed 06 wrapper only; no live operations by default.

    Never call the historical Terminal-only guard as the current predecessor.
    The adapter MUST preserve Config/Terminal/V2/seeding checks, verify actual
    Schema/approval/source/tree, and support full lifecycle + admin revoke.
    No adapter has been qualified or installed by this local draft.
    """
    need(native_adapter is not None and expected_adapter_sha256 is not None,
        'APPROVED_DYNAMIC_GUARD_BASELINE_NOT_ATTESTED')
    raise Blocked('CURRENT_NATIVE_LIFECYCLE_ADAPTER_NOT_QUALIFIED')
