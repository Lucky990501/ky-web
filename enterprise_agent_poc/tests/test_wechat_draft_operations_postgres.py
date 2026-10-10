"""Schema016/atomic journal tests on a marked socket-only PostgreSQL 16.6.

All business/quality/WeChat data here are synthetic fixtures, NOT model or live
WeChat acceptance. No remote DSN, provider client or real secret is used.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import copy
import io
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
from urllib.parse import urlencode
import uuid

from PIL import Image
import psycopg
from psycopg import sql
import pytest

from app.agent_productization import DEFAULTS, canonical, AgentProductization
from app.auth import UserPrincipal
from app.product_store import ProductStore
from app.store import POCStore
from app.wechat_draft_operations import DraftOperations, DraftOperationError, TASK_MARKER
from app.wechat_prepare_reader import WechatPrepareReader, digest, identity
from app.wechat_prepare_action import WechatPrepareAdapter
from scripts import migrate

pytestmark=pytest.mark.skipif(not os.environ.get('STAGE1_POSTGRES_ROOT'),reason='isolated PG16.6 required')


@pytest.fixture
def pg_action(tmp_path,monkeypatch):
    root=Path(os.environ['STAGE1_POSTGRES_ROOT']).resolve()
    assert root.parent==Path('/private/tmp') and root.name.startswith('ky-web-stage1-postgres.')
    assert root.stat().st_uid==os.getuid() and (root/'stage1-isolated.marker').read_text().strip()=='ky-web-stage1-local-only'
    args=dict(dbname='postgres',host=str(root/'socket'),port=int(os.environ.get('STAGE1_POSTGRES_PORT','54329')),user='stage1_fixture')
    database_name='stage1_draft_'+uuid.uuid4().hex
    with psycopg.connect(**args,autocommit=True) as admin:
        assert admin.execute('SHOW server_version_num').fetchone()[0]=='160006'
        assert admin.execute('SHOW listen_addresses').fetchone()[0]==''
        assert Path(admin.execute('SHOW data_directory').fetchone()[0]).resolve()==root/'cluster'
        admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(database_name)))
    store=POCStore('postgresql:///'+database_name+'?'+urlencode({k:v for k,v in args.items() if k!='dbname'}))
    before_dir=tmp_path/'001-015';before_dir.mkdir()
    for path in migrate.migration_files()[:15]:shutil.copy(path,before_dir/path.name)
    with monkeypatch.context() as patch:
        patch.setattr(migrate,'MIGRATIONS',before_dir);assert migrate.up(store)==0
    store.seed_demo_data();product=ProductStore(store);product.initialize()
    product.create_user('tenant-a','draft-fixture@example.invalid','unused-fixture-hash','Synthetic','member')
    user=product.user_by_email('draft-fixture@example.invalid')['id']
    agent,revision,skill_id,skill_revision,instance,context,task,run,conversation,invocation=[str(uuid.uuid4()) for _ in range(10)]
    message=f'task:{task}:assistant'
    skill=dict(id=skill_revision,skill_id=skill_id,slug='wechat-html-draft',version='1.0.0',checksum='c'*64)
    def insert(conn,table,row):
        conn.execute(f"INSERT INTO {table}({','.join(row)}) VALUES ({','.join('?' for _ in row)})",tuple(row.values()))
    with store.connection() as conn:
        AgentProductization(store)._seed_capabilities(conn)
        conn.execute("UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,advance_origin='controlled_advance',advanced_at=CURRENT_TIMESTAMP,advanced_by_release_id='draft-isolated-fixture',advanced_by_source_commit=? WHERE scope='agent_data_contract'",('f'*40,))
        insert(conn,'skills',dict(id=skill_id,slug=skill['slug'],name='Synthetic Skill'))
        insert(conn,'skill_versions',dict(id=skill_revision,skill_id=skill_id,version='1.0.0',status='published',checksum=skill['checksum']))
        insert(conn,'skill_packages',dict(id=str(uuid.uuid4()),skill_version_id=skill_revision,storage_path='synthetic-only',sha256=skill['checksum'],size_bytes=1))
        insert(conn,'agent_templates',dict(id=agent,name='Synthetic Draft',slug='wechat-official-account-writing',description='',icon='',status='enabled',default_runtime_profile='fixture',credit_cost=1,skill_manifest='{}'))
        fields={k:canonical(v) if isinstance(v,dict) else v for k,v in DEFAULTS.items()}
        fields.update(id=revision,agent_template_id=agent,revision=1,status='draft',configuration_fingerprint='d'*64)
        insert(conn,'agent_template_versions',fields)
        insert(conn,'agent_template_version_skills',dict(agent_template_version_id=revision,skill_id=skill_id,skill_version_id=skill_revision))
        conn.execute("UPDATE agent_template_versions SET status='published' WHERE id=?",(revision,))
        conn.execute('UPDATE agent_templates SET current_published_version_id=? WHERE id=?',(revision,agent))
        insert(conn,'tenant_agent_instances',dict(tenant_id='tenant-a',agent_id=agent,instance_id=instance,agent_template_version_id=revision,status='enabled'))
        policy=dict(runtime_test=False,skill_refs=[skill],scopes=['skills:execute','wechat:draft:create'],
            bindings=[dict(tool_capability_id=k,invocation_requirement='optional') for k in ('skill_action_execute','wechat_create_draft_authorize')])
        insert(conn,'agent_execution_contexts',dict(id=context,tenant_id='tenant-a',agent_id=agent,instance_id=instance,agent_template_version_id=revision,
            definition_source='productized',configuration_fingerprint='d'*64,persona_snapshot='Synthetic fixture',runtime_provider='codex',
            model_config_id='fixture',model_provider_id_snapshot='fixture',model_id_snapshot='fixture',reasoning_level_snapshot='high',
            skill_manifest_snapshot='{}',tool_policy_snapshot=json.dumps(policy),knowledge_requirement='none',asset_requirement='none',
            enterprise_config_requirement='none',output_policy='text',credit_cost=1,profile_hash_version='v2',runtime_profile_id='v2-draft'))
        insert(conn,'conversations',dict(id=conversation,tenant_id='tenant-a',agent_id=agent,runtime_profile_id='v2-draft',runtime_thread_id='synthetic-only',runtime_version='fixture'))
        insert(conn,'conversation_agent_contexts',dict(conversation_id=conversation,context_id=context))
        insert(conn,'conversation_owners',dict(conversation_id=conversation,user_id=user))
        insert(conn,'tasks',dict(id=task,tenant_id='tenant-a',user_id=user,agent_id=agent,conversation_id=conversation,input_text='Synthetic PREPARE',status='completed',stage='completed',run_id=run))
        insert(conn,'task_agent_contexts',dict(task_id=task,context_id=context))
        insert(conn,'messages',dict(id=message,conversation_id=conversation,role='assistant',content='Synthetic preview'))
        insert(conn,'task_results',dict(task_id=task,final_response='Synthetic preview',result_json=json.dumps(dict(assistant_message_id=message))))
        insert(conn,'run_traces',dict(run_id=run,conversation_id=conversation,tenant_id='tenant-a',agent_id=agent,status='completed',payload=json.dumps(dict(execution_context_id=context))))
        before={t:[dict(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY 1')] for t in ('users','tasks','run_traces','messages','tenant_agent_instances','task_results')}
    assert migrate.up(store)==0
    with store.connection() as conn:
        assert before=={t:[dict(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY 1')] for t in before}
    data=tmp_path/'data';workspace=data/'runtime'/'tenant-a'/agent/'v2-draft'/'workspace'/'tasks'/task/invocation
    (workspace/'run').mkdir(parents=True);(workspace/'assets').mkdir()
    stream=io.BytesIO();Image.new('RGB',(20,20),'white').save(stream,format='PNG')
    (workspace/'assets/cover.png').write_bytes(stream.getvalue())
    (workspace/'run/prepared.html').write_text('<section id="article"><p>Synthetic article</p></section>')
    (workspace/'verification.json').write_text(json.dumps(dict(offline=True,wechat_calls=0,visual_verified=False)))
    (workspace/'article.json').write_text(json.dumps(dict(title='Synthetic title',digest='Synthetic digest',cover='assets/cover.png')))
    account_identity=identity(dict(tenant_id='tenant-a',environment='test',appid='wx0000000000000000'))
    relative=f'tasks/{task}/{invocation}';artifacts=[]
    for name,mime in [('run/prepared.html','text/html'),('verification.json','application/json')]:
        raw=(workspace/name).read_bytes();artifacts.append(dict(ref='workspace:'+relative+'/'+name,mime_type=mime,size_bytes=len(raw),sha256=digest(raw)))
    artifacts.append(WechatPrepareAdapter().upload_manifest(workspace,relative,dict(id=task,run_id=run,tenant_id='tenant-a',user_id=user,agent_id=agent),
        dict(id=skill_revision,checksum=skill['checksum']),account_identity,'test'))
    receipt=dict(contract='SKILL_REVISION_RUNTIME_DISPATCH_V1',id=invocation,tenant_id='tenant-a',agent_id=agent,task_id=task,run_id=run,
        skill_key='wechat-html-draft',revision=skill_revision,version='1.0.0',artifact_identity=skill['checksum'],action='PREPARE',status='completed',exit_status=0,artifact_refs=artifacts)
    (workspace/'receipt.json').write_text(json.dumps(receipt))
    store.log_event(conversation,'skill.execution',receipt)
    principal=UserPrincipal(user,'tenant-a','member');reader=WechatPrepareReader(store,data)
    bundle=reader.bundle(principal,agent,message);binding=bundle['binding']
    binding.update(secret_version=1,capability_identity='a'*64)
    operations=DraftOperations(store)
    try:yield SimpleNamespace(**locals())
    finally:
        with psycopg.connect(**args,autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database_name)))


def create(f):return f.operations.create(f.binding,lambda conn:None)[0]


def test_016_preserves_data_is_idempotent_and_one_new_table(pg_action):
    f=pg_action
    assert migrate.up(f.store)==0
    with f.store.connection() as conn:
        conn.execute(migrate.migration_files()[15].read_text())
        assert conn.execute('SELECT count(*) AS n FROM schema_migrations').fetchone()['n']==16
    row=create(f)
    assert row['action_task_id']!=f.task and row['action_run_id']!=f.run
    with f.store.connection() as conn:
        assert conn.execute('SELECT status FROM tasks WHERE id=?',(f.task,)).fetchone()['status']=='completed'
        assert conn.execute('SELECT run_id FROM tasks WHERE id=?',(row['action_task_id'],)).fetchone()['run_id']==row['action_run_id']


def test_pg_concurrent_confirmation_creates_exactly_one_task_run_operation(pg_action):
    f=pg_action
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows=list(pool.map(lambda _:create(f),range(16)))
    assert len({r['id'] for r in rows})==len({r['action_task_id'] for r in rows})==1
    with f.store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM wechat_draft_operations').fetchone()['n']==1
        assert conn.execute('SELECT count(*) AS n FROM tasks WHERE input_text=?',(TASK_MARKER,)).fetchone()['n']==1


def test_pg_failure_during_operation_insert_rolls_back_action_task_and_run(pg_action):
    f=pg_action;b=dict(f.binding,source_run_id='wrong')
    with pytest.raises(psycopg.Error):f.operations.create(b,lambda conn:None)
    with f.store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM tasks WHERE input_text=?',(TASK_MARKER,)).fetchone()['n']==0
        assert conn.execute('SELECT count(*) AS n FROM run_traces').fetchone()['n']==1


@pytest.mark.parametrize('field,value',[('tenant_id','tenant-b'),('user_id','wrong'),('agent_id','wrong'),('agent_revision_id','wrong'),('skill_revision_id','wrong'),('source_message_id','wrong'),('source_task_id','wrong'),('source_run_id','wrong'),('context_id','wrong')])
def test_pg_association_rejects_bad_identity(pg_action,field,value):
    with pytest.raises((psycopg.Error,DraftOperationError)):
        pg_action.operations.create(dict(pg_action.binding,**{field:value}),lambda conn:None)


@pytest.mark.parametrize('change',["tenant_id='tenant-b'","secret_version=2","binding_json='{}'","article_version='"+'b'*64+"'","state='CONFIRMED'","state='VERIFYING'"])
def test_pg_identity_and_state_guards(pg_action,change):
    f=pg_action;row=create(f)
    with pytest.raises(psycopg.Error),f.store.connection() as conn:
        conn.execute('UPDATE wechat_draft_operations SET '+change+',revision=revision+1 WHERE id=?',(row['id'],))


def test_intent_committed_before_permit_and_unknown_never_reclaims(pg_action):
    f=pg_action;row=create(f);claim=f.operations.claim(row['id']);lease=claim['lease_id']
    key=f.operations.intent(row['id'],lease,'draft/add','e'*64,lambda:None)
    other=DraftOperations(POCStore(f.store.database_url))
    assert other.by_task(row['action_task_id'])['intent_json'][0]['id']==key
    f.operations.unknown(row['id'],lease)
    assert other.claim(row['id']) is None
    with pytest.raises(DraftOperationError):f.operations.intent(row['id'],lease,'draft/add','e'*64,lambda:None)
    assert other.by_task(row['action_task_id'])['state']=='UNKNOWN'


def test_worker_crash_expired_lease_and_redelivery(pg_action):
    f=pg_action;row=create(f);claim=f.operations.claim(row['id'])
    assert f.operations.claim(row['id']) is None
    f.operations.intent(row['id'],claim['lease_id'],'material/add_material','e'*64,lambda:None)
    with f.store.connection() as conn:
        conn.execute("UPDATE wechat_draft_operations SET revision=revision+1,lease_expires_at=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE id=?",(row['id'],))
    assert f.operations.claim(row['id']) is None
    assert f.operations.by_task(row['action_task_id'])['state']=='UNKNOWN'


def test_before_intent_crash_can_reclaim_without_duplicate_operation(pg_action):
    f=pg_action;row=create(f);claim=f.operations.claim(row['id'])
    with f.store.connection() as conn:
        conn.execute("UPDATE wechat_draft_operations SET revision=revision+1,lease_expires_at=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE id=?",(row['id'],))
    assert f.operations.claim(row['id'])['lease_id']!=claim['lease_id']
    assert create(f)['id']==row['id']


def test_known_media_readback_confirms_without_repeat_draft_add(pg_action):
    f=pg_action;row=create(f);claim=f.operations.claim(row['id']);lease=claim['lease_id']
    key=f.operations.intent(row['id'],lease,'draft/add','e'*64,lambda:None)
    f.operations.acknowledge(row['id'],lease,key,{'media_id':'MOCK_ONLY_media'})
    f.operations.unknown(row['id'],lease)
    f.operations.request_readback(row['id'],lambda r:None)
    read=f.operations.claim(row['id']);assert read['state']=='VERIFYING'
    with pytest.raises(DraftOperationError):f.operations.intent(row['id'],read['lease_id'],'draft/add','e'*64,lambda:None)
    f.operations.confirm(row['id'],read['lease_id'],'f'*64)
    assert f.operations.by_task(row['action_task_id'])['state']=='CONFIRMED'
    assert f.operations.claim(row['id']) is None


def test_no_media_unknown_cannot_reconcile_or_delete(pg_action):
    f=pg_action;row=create(f);claim=f.operations.claim(row['id'])
    f.operations.intent(row['id'],claim['lease_id'],'draft/add','e'*64,lambda:None)
    f.operations.unknown(row['id'],claim['lease_id'])
    with pytest.raises(DraftOperationError):f.operations.request_readback(row['id'],lambda r:None)
    with pytest.raises(psycopg.Error),f.store.connection() as conn:conn.execute('DELETE FROM wechat_draft_operations WHERE id=?',(row['id'],))


def test_secret_rotation_does_not_create_second_operation(pg_action):
    f=pg_action;row=create(f)
    same,_=f.operations.create(dict(f.binding,secret_version=2,capability_identity='b'*64),lambda conn:None)
    assert same['id']==row['id'] and same['secret_version']==1


def test_bundle_bytes_and_owner_are_verified(pg_action):
    f=pg_action
    assert f.reader.read(f.principal,f.agent,f.message)['upload_bundle_verified']
    with pytest.raises(PermissionError):f.reader.bundle(UserPrincipal(f.user,'tenant-b','member'),f.agent,f.message)
    (f.workspace/'assets/cover.png').write_bytes(b'corrupted')
    with pytest.raises(PermissionError):f.reader.bundle(f.principal,f.agent,f.message)


def test_pg_restart_preserves_unknown_intent_and_blocks_redelivery(pg_action):
    import subprocess
    f=pg_action;row=create(f);claim=f.operations.claim(row['id'])
    f.operations.intent(row['id'],claim['lease_id'],'draft/add','e'*64,lambda:None)
    f.operations.unknown(row['id'],claim['lease_id'])
    before=f.operations.by_task(row['action_task_id'])
    root=Path(os.environ['STAGE1_POSTGRES_ROOT']).resolve()
    assert root.name.startswith('ky-web-stage1-postgres.wechat016-')
    pg=Path('/home/lucky/.cache/enterprise-agent-test-runtime/postgresql-16.6/bin/pg_ctl')
    subprocess.run([str(pg),'-D',str(root/'cluster'),'-m','fast','-w','restart'],check=True,stdout=subprocess.DEVNULL)
    assert f.operations.by_task(row['action_task_id'])==before
    assert f.operations.claim(row['id']) is None


@pytest.fixture
def execution(pg_action,monkeypatch):
    """Real PG/API + synthetic authority/quality/Secret boundaries, explicitly mocked."""
    from cryptography.fernet import Fernet
    from app.tenant_secret_backend import ProtectedTenantSecretBackend
    from app.tenant_secret_reference import TenantSecretReferences
    from app.wechat_draft_execution import WechatDraftExecution
    from app.security import RuntimeTokenIssuer
    f=pg_action
    settings=SimpleNamespace(environment='test',data_dir=f.data,tenant_secret_key_file=None,wechat_network_allowed=True)
    executor=WechatDraftExecution(f.product,RuntimeTokenIssuer('synthetic-action-signing-key-only'),settings)
    codec=Fernet(Fernet.generate_key())
    backend=ProtectedTenantSecretBackend(f.tmp_path/'test-secrets','test',None,codec_loader=lambda:codec)
    ref=backend.save('tenant-a','wx0000000000000000','synthetic-secret-only')
    backend.record_verification('tenant-a',ref,'wx0000000000000000')  # mocked connection result, not E2E
    account=dict(account_display_name='Synthetic',wechat_app_id='wx0000000000000000',wechat_app_secret_ref=ref)
    monkeypatch.setattr(f.store,'enterprise_config',lambda tenant:dict(wechat_account=account))
    executor.secrets=TenantSecretReferences('test',backend=backend)
    executor._capability=lambda binding:({'max_requests':16},'a'*64)
    # Quality PASS is a test double, never a fabricated formal model record.
    f.product.execution_resolver=SimpleNamespace(check_context=lambda conn,ctx:None,_runtime_passed=lambda conn,v:True)
    return SimpleNamespace(f=f,executor=executor,backend=backend,account=account,reference=ref)


def test_formal_api_confirmation_duplicate_csrf_and_no_secret_return(execution):
    from fastapi import FastAPI,HTTPException
    from fastapi.testclient import TestClient
    from app.wechat_draft_api import wechat_draft_router
    e=execution;f=e.f;app=FastAPI();queued=[]
    def current(cookie):
        if cookie!='synthetic-session':raise HTTPException(401)
        return f.principal
    app.include_router(wechat_draft_router(f.reader,current,e.executor,queued.append))
    path=f'/api/v1/agents/{f.agent}/messages/{f.message}/wechat-draft'
    headers={'origin':'http://testserver','sec-fetch-site':'same-origin','x-workbench-action':'CREATE_DRAFT'}
    with TestClient(app) as client:
        client.cookies.set('workbench_session','synthetic-session')
        assert client.post(path,json={}).status_code==403
        available=client.get(path+'/availability').json();assert available['state']=='READY'
        body=dict(article_version=available['article_version'])
        assert client.post(path,headers=headers,json=dict(body,tenant_id='tenant-b')).status_code==422
        first=client.post(path,headers=headers,json=body);second=client.post(path,headers=headers,json=body)
        assert first.status_code==second.status_code==202 and first.json()==second.json()
        assert len(set(queued))==1
        assert 'synthetic-secret-only' not in first.text and 'secret_ref' not in first.text
        assert client.get(path+'/status').json()['prior_receipt']['operation_id']==first.json()['operation_id']
        row=f.operations.by_task(first.json()['task_id']);claim=f.operations.claim(row['id'])
        intent=f.operations.intent(row['id'],claim['lease_id'],'draft/add','e'*64,lambda:None)
        f.operations.acknowledge(row['id'],claim['lease_id'],intent,{'media_id':'MOCK_known_media'})
        f.operations.unknown(row['id'],claim['lease_id'])
        assert client.post(path+'/reconcile').status_code==403
        readback=client.post(path+'/reconcile',headers=headers)
        assert readback.status_code==202 and readback.json()['state']=='VERIFYING'
        assert readback.json()['task_id']==first.json()['task_id'] and len(set(queued))==1


@pytest.mark.parametrize('reason',['rotation','revoke','unpublished','disabled','no_quality','article_changed','wrong_environment','wrong_version','wrong_user','missing_capability'])
def test_execution_reauthorization_rejects_changes(execution,reason,monkeypatch):
    e=execution;f=e.f
    row=create(f)
    if reason=='rotation':e.backend.save('tenant-a',e.account['wechat_app_id'],'synthetic-rotated-secret',expected=e.reference)
    if reason=='revoke':e.backend.revoke('tenant-a')
    if reason=='unpublished':
        with f.store.connection() as conn:conn.execute("UPDATE agent_template_versions SET status='deprecated' WHERE id=?",(f.revision,))
    if reason=='disabled':
        with f.store.connection() as conn:conn.execute("UPDATE tenant_agent_instances SET status='disabled' WHERE instance_id=?",(f.instance,))
    if reason=='no_quality':f.product.execution_resolver._runtime_passed=lambda *a:False
    if reason=='article_changed':
        with f.store.connection() as conn:conn.execute("UPDATE messages SET content='changed' WHERE id=?",(f.message,))
    if reason=='wrong_environment':e.executor.settings.environment='production'
    if reason=='wrong_version':row['binding_json']['article_version']='e'*64
    if reason=='wrong_user':row['user_id']='wrong-user'
    if reason=='missing_capability':e.executor._capability=lambda b:(_ for _ in ()).throw(DraftOperationError())
    with pytest.raises(PermissionError):e.executor.reauthorize(row)


def test_worker_uses_action_route_never_agent_model(execution,monkeypatch):
    import asyncio
    from app.product_service import TaskService
    from app import skill_dispatch_config
    e=execution;f=e.f;row=create(f);calls=[]
    def dispatch(bearer,scope,operation,bundle,executor,**kwargs):
        assert executor.tokens.verify_task_scope(scope,'tenant-a')==row['action_task_id']
        assert executor.tokens.verify(bearer,'skills:execute').execution_context_id==f.context
        key=executor.operations.intent(row['id'],operation['lease_id'],'draft/add','e'*64,lambda:None)
        calls.append(key)
        raise TimeoutError('MOCK_RESPONSE_LOST')
    monkeypatch.setattr(skill_dispatch_config,'from_settings',lambda *a:SimpleNamespace(execute_draft_operation=dispatch))
    service=TaskService(f.product,SimpleNamespace(run=lambda *a:pytest.fail('NO_MODEL_ALLOWED')))
    service.wechat_draft_execution=e.executor
    task=f.product.task_for_worker(row['action_task_id'])
    asyncio.run(service.execute(task));asyncio.run(service.execute(task))
    assert len(calls)==1
    assert f.operations.by_task(row['action_task_id'])['state']=='UNKNOWN'
    assert f.product.task_for_worker(row['action_task_id'])['status']=='failed'


@pytest.mark.parametrize('fault',['none','cover_response_lost','draft_response_lost','readback_mismatch'])
def test_real_skill_child_with_pg_journal_and_mock_http(execution,monkeypatch,fault):
    """Actual Skill code and child process; only HTTP/installed seals are doubles."""
    import subprocess
    import sys
    from app import skill_dispatch_config,skill_dispatch
    from app.skill_dispatch import SkillActionDispatcher,ActionRegistration,RuntimeEntry
    e=execution;f=e.f;project=Path(__file__).resolve().parents[1]
    packages=f.tmp_path/'registry/packages';packages.mkdir(parents=True)
    package=packages/'synthetic.zip';package.write_bytes(b'synthetic-registry-boundary-only')
    registry=SimpleNamespace(packages_root=packages,data_root=packages.parent,
        version=lambda _:dict(f.skill,status='published',storage_path=str(package)),test_version=lambda _:None)
    runtime=SimpleNamespace(resolve=lambda *a:RuntimeEntry(Path(sys.executable),project/'skill_sources/wechat-html-draft/1.0.0',
                            f.tmp_path/'mock-runtime','SYNTHETIC_RUNTIME_BOUNDARY',project.parent))
    registration=ActionRegistration('wechat-html-draft','1.0.0','c'*64,'CREATE_DRAFT','wechat:draft:create',
        'scripts/wechat_draft.py',('--check',),WechatPrepareAdapter(),runtime,lambda *a:None,False)
    dispatcher=SkillActionDispatcher(f.store,e.executor.tokens,registry,f.data,[registration])
    monkeypatch.setattr(skill_dispatch_config,'from_settings',lambda *a:dispatcher)
    log=f.tmp_path/'mock-http-events.json';payload=f.tmp_path/'mock-draft-payload.json'
    mock_mode=f.tmp_path/'mock-mode';mock_mode.write_text(fault)
    wrapper=f.tmp_path/'mock_http_child.py'
    # Copied locked dependencies are test-local, never installed into app/runtime.
    deps=os.environ.get('PYTHONPATH','').split(os.pathsep)
    wrapper.write_text('''import sys,json,runpy
from pathlib import Path
sys.path[:0]=DEPS
import requests
calls=Path(LOG);stored=Path(PAYLOAD);mode=Path(MODE)
class Response:
 status_code=200
 def __init__(self,value):self.value=value
 def json(self):return self.value
def mocked(method,url,**kwargs):
 endpoint=url.split('/cgi-bin/')[-1]
 assert url.startswith('https://api.weixin.qq.com/cgi-bin/')
 events=json.loads(calls.read_text()) if calls.exists() else []
 events.append(endpoint);calls.write_text(json.dumps(events))
 fault=mode.read_text()
 if endpoint=='token':return Response({'access_token':'SYNTHETIC_TOKEN_ONLY'})
 if endpoint=='material/add_material':
  if fault=='cover_response_lost':raise requests.Timeout('SYNTHETIC_SECRET_MUST_NOT_LEAK')
  return Response({'media_id':'MOCK_cover'})
 if endpoint=='media/uploadimg':return Response({'url':'https://mmbiz.qpic.cn/MOCK_image/0'})
 if endpoint=='draft/add':
  stored.write_text(kwargs['data'].decode())
  if fault=='draft_response_lost':raise requests.Timeout('SYNTHETIC_SECRET_MUST_NOT_LEAK')
  return Response({'media_id':'MOCK_draft'})
 if endpoint=='draft/get':
  article=json.loads(stored.read_text())['articles'][0]
  if fault=='readback_mismatch':article['title']='MISMATCH'
  return Response({'news_item':[article]})
 raise AssertionError('NETWORK_NOT_MOCKED')
requests.request=mocked
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
'''.replace('DEPS',repr(deps)).replace('LOG',repr(str(log))).replace('PAYLOAD',repr(str(payload))).replace('MODE',repr(str(mock_mode))))
    original=subprocess.Popen
    def launch(argv,**kwargs):
        assert Path(argv[3]).name=='wechat_draft_journal_child.py'
        return original([argv[0],'-B',str(wrapper),*argv[3:]],**kwargs)
    monkeypatch.setattr(skill_dispatch.subprocess,'Popen',launch)
    row=create(f);task=f.product.task_for_worker(row['action_task_id'])
    e.executor._execute(task)
    operation=f.operations.by_task(row['action_task_id'])
    events=json.loads(log.read_text()) if log.exists() else []
    assert events,operation
    if fault=='none':
        assert operation['state']=='CONFIRMED',operation
        assert events==['token','material/add_material','draft/add','draft/get']
        assert operation['verification_json']['matched'] is True
    else:
        assert operation['state']=='UNKNOWN',operation
        e.executor._execute(task)
        assert json.loads(log.read_text())==events  # no auto resend
        if fault=='readback_mismatch':
            assert operation['draft_media_id']=='MOCK_draft'
            mock_mode.write_text('none')
            f.operations.request_readback(row['id'],e.executor.reauthorize)
            e.executor._execute(f.product.task_for_worker(row['action_task_id']))
            assert f.operations.by_task(row['action_task_id'])['state']=='CONFIRMED'
            assert json.loads(log.read_text())==events+['token','draft/get']
    assert 'SYNTHETIC_SECRET_MUST_NOT_LEAK' not in json.dumps(operation,default=str)


def test_real_redis_redelivery_and_commit_enqueue_gap_keep_one_operation(execution):
    import subprocess
    import time
    from app.task_queue import RedisTaskQueue
    from app.product_service import TaskExecutionNotAuthorized
    e=execution;f=e.f;row=create(f)
    root=Path(os.environ['STAGE1_POSTGRES_ROOT'])
    redis_root=root/('redis-'+uuid.uuid4().hex);redis_root.mkdir(mode=0o700)
    socket=redis_root/'redis.sock'
    process=subprocess.Popen(['/home/lucky/.cache/enterprise-agent-test-runtime/redis-7.4.2/bin/redis-server',
        '--port','0','--unixsocket',str(socket),'--unixsocketperm','700','--save','','--appendonly','no',
        '--dir',str(redis_root)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        queue=RedisTaskQueue('unix://'+str(socket),'wechat016-synthetic')
        for _ in range(100):
            try:
                if queue.ping():break
            except Exception:time.sleep(.03)
        assert queue.ping()
        # Process died after PG commit but before LPUSH. Existing startup repair.
        assert f.operations.recoverable_task_ids()==[row['action_task_id']]
        for task_id in f.operations.recoverable_task_ids():queue.enqueue(task_id)
        assert queue.reserve(1)==row['action_task_id']
        claim=f.operations.claim(row['id'])
        queue.recover_processing()
        assert queue.reserve(1)==row['action_task_id']
        with pytest.raises(TaskExecutionNotAuthorized):e.executor._execute(f.product.task_for_worker(row['action_task_id']))
        assert queue._client.llen(queue.processing)==1  # not acknowledged/lost
        f.operations.intent(row['id'],claim['lease_id'],'draft/add','e'*64,lambda:None)
        with f.store.connection() as conn:
            conn.execute("UPDATE wechat_draft_operations SET revision=revision+1,lease_expires_at=CURRENT_TIMESTAMP-INTERVAL '1 second' WHERE id=?",(row['id'],))
        e.executor._execute(f.product.task_for_worker(row['action_task_id']))
        queue.acknowledge(row['action_task_id'])
        assert queue._client.llen(queue.processing)==0
        assert f.operations.by_task(row['action_task_id'])['state']=='UNKNOWN'
        assert f.operations.recoverable_task_ids()==[]
        assert f.product.task_for_worker(row['action_task_id'])['status']=='failed'
    finally:
        process.terminate();process.wait(timeout=10)
