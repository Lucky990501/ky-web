"""Environment-owned parent of the existing NativeSuccessor, not a bypass.

PRIMARY continues to use its immutable DCA/d8a/Config parent. The isolated
realm has a different root, cluster, release pair and self-owned predecessor.
Both flow through the same admission, quality and productization checks.
"""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import os
from pathlib import Path
import re

from scripts.wechat_runtime_test_lifecycle_guard import need, digest

VERSION = 'NATIVE_ENVIRONMENT_PARENT_CONTRACT_V1'
REALM = 'ISOLATED_NATIVE_TEST_CONTEXT'
ROOT = Path('/etc/enterprise-agent-native-isolated-v1')
HOME = Path('/opt/enterprise-agent-native-isolated')
DSN = 'postgresql://native_isolated@127.0.0.1:56432/native_isolated'


def validate_context(context, pair, pair_sha):
    need(set(context) == {'contract', 'realm', 'environment', 'production_authority', 'security', 'state'},
         'PARENT_CONTEXT_SHAPE')
    need(context['contract'] == VERSION and context['realm'] == REALM
         and context['environment'] == 'test' and context['production_authority'] is False,
         'PARENT_CONTEXT_DOMAIN')
    security, state = context['security'], context['state']
    need(set(security) == {'pair_sha256', 'database', 'issuer_sha256', 'schema_sha256', 'predecessor'},
         'PARENT_SECURITY_SHAPE')
    need(security['pair_sha256'] == pair_sha and pair['contract'] == 'INTEGRATED_RUNTIME_RELEASE_PAIR_V2'
         and pair['production_authority'] is False and pair['environment'] == 'test', 'PARENT_RELEASE_PAIR')
    for role, directory in (('application', HOME/'releases'), ('tooling', HOME/'shared/source-qualifications')):
        need(Path(pair[role]['path']).parent == directory, 'PARENT_REALM_SOURCE_LOCATION')
    db = security['database']
    need(set(db) == {'host', 'port', 'name', 'role', 'system_identifier', 'server_version'} and
         (db['host'], db['port'], db['name'], db['role'], db['server_version']) ==
         ('127.0.0.1', 56432, 'native_isolated', 'native_isolated', 160006)
         and re.fullmatch('[0-9]{10,24}', str(db['system_identifier'])), 'PARENT_DATABASE_IDENTITY')
    predecessor = security['predecessor']
    need(set(predecessor) == {'kind', 'system_identifier', 'bootstrap_receipt_sha256'}
         and predecessor['kind'] == 'ISOLATED_EMPTY_DATABASE'
         and predecessor['system_identifier'] == db['system_identifier'], 'PARENT_PREDECESSOR_IDENTITY')
    need(set(state) == {'tenant_id', 'principal_id', 'agent_id', 'revision_id', 'row_pins', 'credit_account'}
         and state['tenant_id'].startswith('native-isolated-')
         and all(isinstance(state[k], str) and state[k] and '*' not in state[k]
                 for k in ('tenant_id', 'principal_id', 'agent_id', 'revision_id')),
         'PARENT_STATE_IDENTITY')
    for pin in (security['issuer_sha256'], security['schema_sha256'], predecessor['bootstrap_receipt_sha256']):
        need(isinstance(pin, str) and re.fullmatch('[a-f0-9]{64}', pin), 'PARENT_REQUIRED_SHA')


def load_context():
    from app.test_tenant_seeding import native_json
    from app.test_runtime_tooling import load_pair, authority_root, ISOLATED_AUTHORITY
    need(os.environ.get('APP_ENV') == 'test' and authority_root() == ISOLATED_AUTHORITY,
         'PARENT_PRIMARY_PRODUCTION_CROSS_REALM_REJECTED')
    require_runtime_environment()
    pair, pair_sha = load_pair()
    approval, _ = native_json(ROOT/'native-approval.v2.json')
    context, sha = native_json(ROOT/'parent-context.v1.json')
    need(approval.get('parent_context_sha256') == sha, 'PARENT_CONTEXT_APPROVAL_PIN')
    validate_context(context, pair, pair_sha)
    issuer = Path(pair['tooling']['path'])/'enterprise_agent_poc/scripts/prepare_exact_admin_authority.py'
    need(hashlib.sha256(issuer.read_bytes()).hexdigest() == context['security']['issuer_sha256'],
         'PARENT_ISSUER_CODE_PIN')
    receipt, receipt_sha = native_json(ROOT/'bootstrap-receipt.v1.json')
    need(receipt_sha == context['security']['predecessor']['bootstrap_receipt_sha256']
         and receipt['contract'] == 'ISOLATED_NATIVE_BOOTSTRAP_V1'
         and receipt['empty_database_verified'] is True and receipt['active_admins'] == 0
         and receipt['pair_sha256'] == pair_sha and receipt['identity'] ==
         {k: context['state'][k] for k in ('tenant_id', 'principal_id', 'agent_id', 'revision_id')},
         'PARENT_BOOTSTRAP_PROVENANCE')
    return context


def require_runtime_environment():
    # Match the formal Revision identity without supplying a Provider key or
    # an external endpoint. Reject a bad model identity BEFORE startup can
    # create a partial configured instance. This is not a fake Runtime PASS.
    expected = {'ENTERPRISE_POC_MODEL_PROVIDER_ID': 'deepseek',
        'ENTERPRISE_POC_MODEL_ID': 'deepseek-v4-pro', 'ENTERPRISE_POC_REASONING_EFFORT': 'high',
        'ENTERPRISE_POC_CODEX_API_KEY_ENV': 'DEEPSEEK_API_KEY',
        'ENTERPRISE_POC_MODEL_BASE_URL': 'http://127.0.0.1:9/disabled',
        'ENTERPRISE_POC_WECHAT_NETWORK_ALLOWED': 'false', 'ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA': 'false',
        'ENTERPRISE_POC_TASK_QUEUE': 'redis', 'REDIS_URL': 'redis://127.0.0.1:56479/0',
        'ENTERPRISE_POC_MCP_URL': 'http://127.0.0.1:28101/mcp'}
    need(all(os.environ.get(k) == v for k, v in expected.items()), 'PARENT_RUNTIME_ENVIRONMENT_IDENTITY')
    need(not any(os.environ.get(k) for k in ('DEEPSEEK_API_KEY', 'OPENAI_API_KEY', 'GATEWAY_API_TOKEN',
        'OSS_ACCESS_KEY_ID', 'OSS_ACCESS_KEY_SECRET', 'EMBEDDING_API_KEY')), 'PARENT_ZERO_EXTERNAL_CREDENTIALS')


def check_database(conn, store, context):
    need(store.is_postgres and os.environ.get('APP_ENV') == 'test'
         and os.environ.get('ENTERPRISE_POC_DATABASE_URL') == DSN, 'PARENT_DATABASE_TARGET')
    row = dict(conn.execute("SELECT host(inet_server_addr()) AS host,inet_server_port() AS port,"
        "current_database() AS name,current_user AS role,current_setting('server_version_num')::int AS server_version,"
        "(SELECT system_identifier::text FROM pg_control_system()) AS system_identifier").fetchone())
    need(row == context['security']['database'], 'PARENT_LIVE_CLUSTER_IDENTITY')


def snapshot(store, context):
    """The formal catalog/row collector, with no imports of historical seals.

    Schema definitions include triggers/functions/RLS and migration checksums;
    the READ ONLY repeatable snapshot also bounds retention and relation names.
    """
    from psycopg import sql
    with store.connection() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        check_database(conn, store, context)
        queries = {
            'tables': "SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY 1",
            'columns': "SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid),a.attidentity,a.attgenerated FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='public' AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2",
            'constraints': "SELECT t.relname,c.conname,c.contype,pg_get_constraintdef(c.oid,true),c.convalidated,c.condeferrable,c.condeferred FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' ORDER BY 1,2",
            'indexes': "SELECT t.relname,ic.relname,pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid JOIN pg_class ic ON ic.oid=i.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' ORDER BY 1,2",
            'triggers': "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid,true),t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY 1,2",
            'functions': "SELECT p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e') ORDER BY 1,2",
            'extensions': "SELECT extname,extversion FROM pg_extension ORDER BY 1",
            'ledger': 'SELECT version,name,checksum FROM schema_migrations ORDER BY version'}
        schema = {name: [dict(r) for r in conn.execute(query)] for name, query in queries.items()}
        data = {}
        for row in schema['tables']:
            table = row['relname']
            need(re.fullmatch('[a-z][a-z0-9_]*', table), 'PARENT_TABLE_NAME')
            # POCStore's PG adapter accepts strings; identifiers were bounded.
            data[table] = [r['row'] for r in conn.execute(
                'SELECT to_jsonb(t) AS row FROM "'+table+'" t ORDER BY to_jsonb(t)::text LIMIT 10001')]
            need(len(data[table]) <= 10000, 'PARENT_RETENTION_REVIEW_REQUIRED')
        return schema, data


def validate_state(data, context, admitted_tasks):
    """Called only AFTER the shared native lifecycle verifier has proved A/B/C.

    No mutable table exemptions: every baseline row remains, and each new row
    must join the exact admitted task/trace/conversation. No history is repinned.
    """
    state = context['state']; pins = state['row_pins']
    need(set(data) == set(pins), 'PARENT_TABLE_SET')
    tasks = {r['id']: r for r in data['tasks'] if r['id'] in admitted_tasks}
    need(set(tasks) == set(admitted_tasks), 'PARENT_ADMISSION_TASK_SET')
    runs = {r['run_id'] for r in tasks.values() if r.get('run_id')}
    conversations = {r['conversation_id'] for r in tasks.values() if r.get('conversation_id')}
    for task in tasks.values():
        need((task['tenant_id'], task['user_id'], task['agent_id']) ==
             (state['tenant_id'], state['principal_id'], state['agent_id']), 'PARENT_TASK_OWNER')
    # Formal completed tasks persist two deterministic message identities and
    # one exact charge. These are data effects, not Runtime quality approval.
    messages = {}
    charges = []
    for task in tasks.values():
        if task['status'] != 'completed':
            continue
        result = [r for r in data['task_results'] if r['task_id'] == task['id']]
        need(len(result) == 1, 'PARENT_COMPLETION_RESULT')
        messages['task:'+task['id']+':user'] = ('user', task['conversation_id'], task['input_text'][:12000])
        messages['task:'+task['id']+':assistant'] = ('assistant', task['conversation_id'], result[0]['final_response'][:12000])
        own = [r for r in data['credit_transactions'] if r['task_id'] == task['id']]
        version = [r for r in data['agent_template_versions'] if r['id'] == state['revision_id']]
        need(len(own) == len(version) == 1 and own[0]['amount'] == -version[0]['credit_cost']
            and own[0]['id'] == 'task:'+task['id']+':charge', 'PARENT_COMPLETION_CHARGE')
        charges.extend(own)
    def message(row):
        return row['id'] in messages and (row['role'], row['conversation_id'], row['content']) == messages[row['id']]
    allowed = {
        'tasks': lambda r: r['id'] in tasks,
        'run_traces': lambda r: r['run_id'] in runs and r['conversation_id'] in conversations,
        'task_events': lambda r: r['task_id'] in tasks,
        'task_results': lambda r: r['task_id'] in tasks,
        'conversations': lambda r: r['id'] in conversations and r['agent_id'] == state['agent_id'],
        'conversation_owners': lambda r: r['conversation_id'] in conversations and r['user_id'] == state['principal_id'],
        'messages': message,
        'credit_transactions': lambda r: r in charges and r['user_id'] == state['principal_id'],
        'execution_events': lambda r: r['conversation_id'] in conversations,
    }
    checked = copy.deepcopy(data)
    if charges:
        baseline = state['credit_account']
        accounts = checked['credit_accounts']
        need(len(accounts) == 1 and digest(baseline) in pins['credit_accounts']
             and accounts[0]['tenant_id'] == baseline['tenant_id'] == state['tenant_id']
             and accounts[0]['balance'] == baseline['balance']+sum(r['amount'] for r in charges)
             and set(accounts[0]) == set(baseline), 'PARENT_CREDIT_BALANCE_EFFECT')
        from scripts.exact_test_admin_lifecycle import utc
        need(utc(accounts[0]['updated_at']) >= utc(baseline['updated_at']), 'PARENT_CREDIT_CHRONOLOGY')
        checked['credit_accounts'] = [baseline]
    for table, rows in checked.items():
        before = Counter(pins[table]); after = Counter(digest(r) for r in rows)
        need(not before-after, 'PARENT_BASELINE_MUTATION:'+table)
        extra = after-before
        for row in rows:
            if extra[digest(row)]:
                need(table in allowed and allowed[table](row), 'PARENT_UNAPPROVED_ROW:'+table)
                need('tenant_id' not in row or row['tenant_id'] == state['tenant_id'], 'PARENT_CROSS_TENANT')
                need('user_id' not in row or row['user_id'] == state['principal_id'], 'PARENT_CROSS_USER')
    return {'status': 'PASS', 'contract': VERSION, 'realm': REALM, 'history_resealed': False}
