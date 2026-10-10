"""Offline reader/API components; NOT CREATE_DRAFT, Native or model E2E."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from app.agent_execution import ExecutionResolver
from app.agent_productization import AgentProductization, DEFAULTS, canonical
from app.auth import UserPrincipal
from app.product_store import ProductStore
from app.store import POCStore
from app.wechat_draft_api import wechat_draft_router
from app.wechat_prepare_reader import WechatPrepareReader, PrepareReadError, digest


@pytest.fixture
def prepared(tmp_path):
    store = POCStore(tmp_path/'reader.db')
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()
    product.create_user('tenant-a', 'preview@example.invalid', 'unused-fixture-hash', 'Synthetic member', 'member')
    user = product.user_by_email('preview@example.invalid')['id']
    project = Path(__file__).parents[1]
    # Use the existing schema/SQLite adapters, not a proposed Migration016.
    schema = (project/'migrations/postgres/005_skill_registry_v1.sql').read_text().split('CREATE TABLE IF NOT EXISTS agent_skill_bindings')[0]
    with store.connection() as conn:
        conn.executescript(schema.replace('now()', 'CURRENT_TIMESTAMP'))
    AgentProductization(store).initialize()
    ExecutionResolver(store, None, None, SimpleNamespace()).initialize_local()
    agent, revision, skill_id, skill_revision, instance, context, task, run, conversation, invocation = [str(uuid.uuid4()) for _ in range(10)]
    message = f'task:{task}:assistant'
    skill = dict(id=skill_revision, skill_id=skill_id, slug='wechat-html-draft', version='1.0.0', checksum='c'*64)
    def insert(conn, table, row):
        conn.execute(f"INSERT INTO {table}({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
    with store.connection() as conn:
        insert(conn, 'skills', dict(id=skill_id, slug=skill['slug'], name='Synthetic Skill'))
        insert(conn, 'skill_versions', dict(id=skill_revision, skill_id=skill_id, version='1.0.0', status='published', checksum=skill['checksum']))
        insert(conn, 'skill_packages', dict(id=str(uuid.uuid4()), skill_version_id=skill_revision, storage_path='fixture-only', sha256=skill['checksum'], size_bytes=1))
        insert(conn, 'agent_templates', dict(id=agent, name='Fixture', slug='wechat-official-account-writing', description='', icon='', status='enabled', default_runtime_profile='fixture', credit_cost=1, skill_manifest='{}'))
        fields = {key: canonical(value) if isinstance(value, dict) else value for key, value in DEFAULTS.items()}
        fields.update(id=revision, agent_template_id=agent, revision=1, status='draft', configuration_fingerprint='d'*64)
        insert(conn, 'agent_template_versions', fields)
        insert(conn, 'agent_template_version_skills', dict(agent_template_version_id=revision, skill_id=skill_id, skill_version_id=skill_revision))
        # Reader fixture only: no quality evidence is created or claimed here.
        conn.execute("UPDATE agent_template_versions SET status='published' WHERE id=?", (revision,))
        conn.execute('UPDATE agent_templates SET current_published_version_id=? WHERE id=?', (revision, agent))
        insert(conn, 'tenant_agent_instances', dict(tenant_id='tenant-a', agent_id=agent, instance_id=instance, agent_template_version_id=revision, status='enabled'))
        insert(conn, 'agent_execution_contexts', dict(id=context, tenant_id='tenant-a', agent_id=agent, instance_id=instance,
            agent_template_version_id=revision, definition_source='productized', configuration_fingerprint='d'*64,
            persona_snapshot='Fixture', runtime_provider='codex', model_config_id='fixture', model_provider_id_snapshot='fixture',
            model_id_snapshot='fixture', reasoning_level_snapshot='high', skill_manifest_snapshot='{}',
            tool_policy_snapshot=json.dumps(dict(skill_refs=[skill])), knowledge_requirement='none', asset_requirement='none',
            enterprise_config_requirement='none', output_policy='text', credit_cost=1, profile_hash_version='v2', runtime_profile_id='v2-reader'))
        insert(conn, 'conversations', dict(id=conversation, tenant_id='tenant-a', agent_id=agent, runtime_profile_id='v2-reader', runtime_thread_id='fixture-only', runtime_version='fixture'))
        insert(conn, 'conversation_agent_contexts', dict(conversation_id=conversation, context_id=context))
        insert(conn, 'conversation_owners', dict(conversation_id=conversation, user_id=user))
        insert(conn, 'tasks', dict(id=task, tenant_id='tenant-a', user_id=user, agent_id=agent, conversation_id=conversation, input_text='Synthetic PREPARE', status='completed', stage='completed', run_id=run))
        insert(conn, 'task_agent_contexts', dict(task_id=task, context_id=context))
        insert(conn, 'messages', dict(id=message, conversation_id=conversation, role='assistant', content='Synthetic preview text'))
        insert(conn, 'task_results', dict(task_id=task, final_response='Synthetic preview text', result_json=json.dumps(dict(assistant_message_id=message))))
        insert(conn, 'run_traces', dict(run_id=run, conversation_id=conversation, tenant_id='tenant-a', agent_id=agent, status='completed', payload=json.dumps(dict(execution_context_id=context))))
    data = tmp_path/'data'
    workspace = data/'runtime'/'tenant-a'/agent/'v2-reader'/'workspace'/'tasks'/task/invocation
    (workspace/'run').mkdir(parents=True)
    html = b'<section id="article"><p>Synthetic prepared preview</p></section>'
    (workspace/'run/prepared.html').write_bytes(html)
    (workspace/'verification.json').write_text(json.dumps(dict(offline=True, wechat_calls=0, visual_verified=False)))
    artifacts = []
    for name, mime in [('run/prepared.html', 'text/html'), ('verification.json', 'application/json')]:
        raw = (workspace/name).read_bytes()
        artifacts.append(dict(ref=f'workspace:tasks/{task}/{invocation}/{name}', mime_type=mime, size_bytes=len(raw), sha256=digest(raw)))
    receipt = dict(contract='SKILL_REVISION_RUNTIME_DISPATCH_V1', id=invocation, tenant_id='tenant-a',
        agent_id=agent, task_id=task, run_id=run, skill_key='wechat-html-draft', revision=skill_revision,
        version='1.0.0', artifact_identity=skill['checksum'], action='PREPARE', status='completed', exit_status=0,
        artifact_refs=artifacts)
    def save_receipt(value):
        (workspace/'receipt.json').write_text(json.dumps(value))
        with store.connection() as conn:
            conn.execute("DELETE FROM execution_events WHERE event_type='skill.execution'")
            conn.execute("INSERT INTO execution_events(conversation_id,event_type,payload) VALUES (?,'skill.execution',?)", (conversation, json.dumps(value)))
    save_receipt(receipt)
    principal = UserPrincipal(user, 'tenant-a', 'member')
    reader = WechatPrepareReader(store, data)
    app = FastAPI()
    def current(token):
        if token != 'synthetic-session':
            raise HTTPException(401, 'AUTH_REQUIRED')
        return principal
    app.include_router(wechat_draft_router(reader, current))
    with TestClient(app) as client:
        client.cookies.set('workbench_session', 'synthetic-session')
        yield SimpleNamespace(**locals())


def read(f):
    return f.reader.read(f.principal, f.agent, f.message)


def test_owner_preview_is_identity_bound_and_non_authorizing(prepared):
    result = read(prepared)
    assert result['state'] == 'PREVIEW_ONLY'
    assert not result['action_authorized'] and not result['upload_bundle_verified']
    assert result['article_version'] == read(prepared)['article_version']
    assert 'workspace:' not in json.dumps(result) and str(prepared.data) not in json.dumps(result)


@pytest.mark.parametrize('tenant,user', [('tenant-b', None), ('tenant-a', 'another-user')])
def test_cross_owner_rejected(prepared, tenant, user):
    with pytest.raises(PrepareReadError):
        prepared.reader.read(UserPrincipal(user or prepared.user, tenant, 'member'), prepared.agent, prepared.message)


@pytest.mark.parametrize('sql', [
    "UPDATE users SET account_status='disabled'",
    "UPDATE conversation_owners SET deleted_at=CURRENT_TIMESTAMP",
    "UPDATE messages SET role='user'",
    "UPDATE tasks SET status='failed'",
    "UPDATE tasks SET run_id='wrong-run'",
    "UPDATE run_traces SET status='running'",
    "UPDATE run_traces SET tenant_id='tenant-b'",
    "UPDATE run_traces SET payload='{}'",
    "UPDATE task_results SET result_json='{}'",
    "UPDATE tenant_agent_instances SET status='disabled'",
    "UPDATE agent_template_versions SET status='deprecated'",
    "UPDATE agent_templates SET current_published_version_id=NULL",
    "UPDATE skill_packages SET sha256='wrong'",
    "UPDATE conversations SET runtime_profile_id='wrong-profile'",
])
def test_broken_persistent_association_rejected(prepared, sql):
    with prepared.store.connection() as conn:
        conn.execute(sql)
    with pytest.raises(PrepareReadError):
        read(prepared)


@pytest.mark.parametrize('field,value', [('tenant_id','tenant-b'), ('agent_id','wrong'), ('run_id','wrong'),
    ('revision','wrong'), ('version','latest'), ('artifact_identity','wrong'), ('exit_status',1),
    ('action','CREATE_DRAFT'), ('contract','unknown')])
def test_receipt_identity_rejected(prepared, field, value):
    receipt = copy.deepcopy(prepared.receipt)
    receipt[field] = value
    prepared.save_receipt(receipt)
    with pytest.raises(PrepareReadError):
        read(prepared)


@pytest.mark.parametrize('field,value', [('ref','workspace:../outside.html'), ('mime_type','text/plain'), ('sha256','0'*64), ('size_bytes',True)])
def test_artifact_identity_rejected(prepared, field, value):
    receipt = copy.deepcopy(prepared.receipt)
    receipt['artifact_refs'][0][field] = value
    prepared.save_receipt(receipt)
    with pytest.raises(PrepareReadError):
        read(prepared)


@pytest.mark.parametrize('change', ['html', 'disk_receipt', 'ambiguous', 'large', 'missing'])
def test_file_and_ambiguity_fail_closed(prepared, change):
    if change == 'html':
        (prepared.workspace/'run/prepared.html').write_text('tampered')
    elif change == 'disk_receipt':
        (prepared.workspace/'receipt.json').write_text('{}')
    elif change == 'large':
        (prepared.workspace/'run/prepared.html').write_bytes(b'x'*(256*1024+1))
    elif change == 'missing':
        (prepared.workspace/'verification.json').unlink()
    else:
        with prepared.store.connection() as conn:
            conn.execute("INSERT INTO execution_events(conversation_id,event_type,payload) VALUES (?,'skill.execution',?)", (prepared.conversation,json.dumps(prepared.receipt)))
    with pytest.raises(PrepareReadError):
        read(prepared)


def test_article_version_changes_when_message_changes(prepared):
    original = read(prepared)['article_version']
    with prepared.store.connection() as conn:
        conn.execute("UPDATE messages SET content='Changed synthetic version' WHERE id=?", (prepared.message,))
    assert original != read(prepared)['article_version']


def test_linked_artifact_is_rejected_even_with_matching_hash(prepared):
    target = prepared.tmp_path/'outside.html'
    target.write_bytes(prepared.html)
    link = prepared.workspace/'run/prepared.html'
    link.unlink()
    link.symlink_to(target)
    with pytest.raises(PrepareReadError):
        read(prepared)


def test_linked_parent_is_rejected(prepared):
    run_dir = prepared.workspace/'run'
    target = prepared.tmp_path/'outside-run'
    run_dir.rename(target)
    run_dir.symlink_to(target, target_is_directory=True)
    with pytest.raises(PrepareReadError):
        read(prepared)


def test_duplicate_receipt_keys_rejected(prepared):
    raw = (prepared.workspace/'receipt.json').read_text()
    (prepared.workspace/'receipt.json').write_text('{"action":"CREATE_DRAFT",'+raw[1:])
    with pytest.raises(PrepareReadError):
        read(prepared)


def test_api_preview_pending_and_no_task_creation(prepared):
    url = f'/api/v1/agents/{prepared.agent}/messages/{prepared.message}/wechat-draft'
    response = prepared.client.get(url+'/prepare')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert prepared.client.get(url+'/availability').json()['state'] == 'BACKEND_PENDING'
    for _ in range(2):
        response = prepared.client.post(url, headers={'origin':'http://testserver', 'sec-fetch-site':'same-origin', 'x-workbench-action':'CREATE_DRAFT'}, json={'userConfirmed':True, 'connected':True, 'enabled':True})
        assert response.status_code == 409 and response.json()['can_create_draft'] is False
        assert response.json()['code'] == 'CREATE_DRAFT_SCHEMA_APPROVAL_REQUIRED'
    with prepared.store.connection() as conn:
        assert conn.execute('SELECT COUNT(*) AS n FROM tasks').fetchone()['n'] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM execution_events WHERE event_type<>'skill.execution'").fetchone()['n'] == 0


@pytest.mark.parametrize('headers', [{}, {'origin':'https://evil.invalid'},
    {'origin':'http://testserver','sec-fetch-site':'same-origin'},
    {'origin':'http://testserver','sec-fetch-site':'cross-site','x-workbench-action':'CREATE_DRAFT'},
    {'origin':'https://testserver','sec-fetch-site':'same-origin','x-workbench-action':'CREATE_DRAFT'}])
def test_csrf_rejected(prepared, headers):
    url = f'/api/v1/agents/{prepared.agent}/messages/{prepared.message}/wechat-draft'
    assert prepared.client.post(url, headers=headers, json={}).status_code == 403


def test_unauthenticated_api_rejected(prepared):
    prepared.client.cookies.clear()
    url = f'/api/v1/agents/{prepared.agent}/messages/{prepared.message}/wechat-draft'
    for method, suffix in [('get','/prepare'),('get','/availability'),('post','')]:
        assert getattr(prepared.client,method)(url+suffix).status_code == 401


def test_api_does_not_echo_sensitive_error(prepared, monkeypatch):
    monkeypatch.setattr(prepared.reader, '_read', lambda *a: (_ for _ in ()).throw(RuntimeError('PRIVATE-CREDENTIAL-TEXT')))
    url = f'/api/v1/agents/{prepared.agent}/messages/{prepared.message}/wechat-draft/prepare'
    response = prepared.client.get(url)
    assert response.status_code == 404 and 'PRIVATE' not in response.text


def test_existing_schema_result_slot_is_overwritten_and_running_task_is_requeued(prepared):
    # Demonstrate the actual existing persistence/restart behavior, not a claim
    # that a generic result blob is a safe Action journal. No external call.
    with prepared.store.connection() as conn:
        conn.execute("UPDATE tasks SET status='running',stage='draft_submit_intent' WHERE id=?", (prepared.task,))
        conn.execute("UPDATE task_results SET result_json=? WHERE task_id=?", (json.dumps({'operation_state':'UNKNOWN'}), prepared.task))
    recovered = prepared.product.recoverable_tasks()
    assert len(recovered) == 1 and recovered[0]['stage'] == 'queued'
    prepared.product.set_task(prepared.task,'tenant-a','failed','failed','fixture',response='failure')
    with prepared.store.connection() as conn:
        result = json.loads(conn.execute('SELECT result_json FROM task_results WHERE task_id=?',(prepared.task,)).fetchone()['result_json'])
    assert 'operation_state' not in result
