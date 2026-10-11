"""Real PG015/storage/finalization/API tests, deterministic Runtime, zero network."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
import pytest
from PIL import Image

from app import main
from app.auth import SessionIssuer, UserPrincipal
from app.domain import RuntimeTurn
from app.product_service import TaskService
from app.product_store import ProductStore, ResultPersistenceError
from app.runtime.codex_provider import CodexRuntimeProvider
from app.service import AgentService
from app.settings import settings
from app.storage import storage_provider
from app.store import POCStore
from app.tool_dependencies import reference_image_dependency
from scripts import migrate
from test_run_trace import FakeRuntime
from test_tool_dependency_supersession import observed, correction_pair

pytestmark = pytest.mark.skipif(not os.environ.get('STAGE1_POSTGRES_ROOT'), reason='independent marked PG16.6 required')


def png(index=0):
    buffer = BytesIO()
    Image.new('RGB', (16+index, 12+index), (index*40, 100, 80)).save(buffer, 'PNG')
    return buffer.getvalue()


class ImagesRuntime(FakeRuntime):
    def __init__(self, calls, status='completed'):
        self.calls, self.status, self.turn_count = calls, status, 0
    async def run_turn(self, session, message):
        self.turn_count += 1
        return RuntimeTurn(session.thread_id, '已生成；正文中的 https://untrusted.invalid/fake.png 不是产物。',
                           status=self.status, mcp_calls=tuple(self.calls))


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def deny(*a, **kw):
        pytest.fail('REAL_PROVIDER_FORBIDDEN')
    async def async_deny(*a, **kw):
        deny()
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', deny)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', async_deny)


@pytest.fixture
def pg(tmp_path, monkeypatch):
    root = Path(os.environ['STAGE1_POSTGRES_ROOT']).resolve()
    assert root.parent == Path('/private/tmp') and root.name.startswith('ky-web-stage1-postgres.image-multi-')
    assert (root/'stage1-isolated.marker').read_text() == 'ky-web-stage1-local-only'
    assert root.stat().st_uid == os.getuid() and (root/'socket').stat().st_mode & 0o077 == 0
    args = dict(host=str(root/'socket'), port=54329, user='stage1_fixture')
    name = 'stage1_images_'+uuid4().hex
    with psycopg.connect(dbname='postgres', **args, autocommit=True) as conn:
        assert conn.execute('SHOW server_version_num').fetchone()[0] == '160006'
        assert conn.execute('SHOW listen_addresses').fetchone()[0] == ''
        assert Path(conn.execute('SHOW data_directory').fetchone()[0]) == root/'cluster'
        conn.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
    store = POCStore(f'postgresql:///{name}?'+urlencode(args))
    try:
        assert len(migrate.migration_files()) == 15 and migrate.up(store) == 0
        store.seed_demo_data()
        product = ProductStore(store)
        product.initialize()
        for tenant, email in [('tenant-a','owner@fixture.test'),('tenant-a','other@fixture.test'),('tenant-b','foreign@fixture.test')]:
            product.create_user(tenant,email,'unused-fixture-hash',email,'member')
        actor = product.user_by_email('owner@fixture.test')
        cfg = replace(settings, environment='test', database_url=store.database_url,
                      object_storage_provider='local', object_storage_dir=tmp_path/'objects',
                      bootstrap_demo_data=False, task_queue='local', secure_cookies=False)
        for key, value in [('settings',cfg),('store',store),('product_store',product),('sessions',SessionIssuer('image-multi-fixture-session-key'))]:
            monkeypatch.setattr(main,key,value)
        yield SimpleNamespace(store=store, product=product, actor=actor, cfg=cfg, root=root, db=name)
    finally:
        with psycopg.connect(dbname='postgres', **args, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


def setup(pg, n, *, fail=False):
    calls, keys = [], []
    for index in range(n):
        key = f'generated/tenant-a/{uuid4()}.png'
        storage_provider(pg.cfg).put(key,png(index),'image/png')
        result = {'structuredContent': {'results':[{'storage_key':key,'requested_size':f'{16+index}x{12+index}'}]}}
        item = observed({'prompt':f'fixture-image-{index}','n':1}, result=result, call_id=f'image-call-{index}')
        item['artifact'] = CodexRuntimeProvider._image_artifact(result)
        calls.append(item)
        keys.append(key)
    runtime = ImagesRuntime(calls, status='failed' if fail else 'completed')
    task = pg.product.create_task('tenant-a',pg.actor['id'],'image-agent','生成测试图集',None)
    service = TaskService(pg.product,AgentService(pg.store,runtime,pg.cfg))
    return task, runtime, service, keys


def rows(pg, task):
    with pg.store.connection() as conn:
        return [dict(x) for x in conn.execute('SELECT * FROM generations WHERE task_id=? ORDER BY id',(task['id'],))]


def no_result_or_charge(pg, task):
    with pg.store.connection() as conn:
        for table in ('generations','task_results','credit_transactions'):
            assert conn.execute('SELECT count(*) n FROM '+table+' WHERE task_id=?',(task['id'],)).fetchone()['n'] == 0


@pytest.mark.parametrize('n',[1,2,3])
def test_real_pg_order_task_sse_history_download_and_replay(pg,n):
    task,runtime,service,keys = setup(pg,n)
    asyncio.run(service.execute(task))
    result = pg.product.task(task['id'],'tenant-a',pg.actor['id'])
    assert result['status'] == 'completed' and len(rows(pg,task)) == n
    assert [x['storage_key'] for x in result['generations']] == keys
    assert result['generation'] == result['generations'][0]
    assert result['generation']['id'] == f"task:{task['id']}:generation"
    for record in rows(pg,task):
        assert (record['tenant_id'],record['user_id'],record['conversation_id']) == ('tenant-a',pg.actor['id'],result['conversation_id'])
    trace = pg.store.run_trace(result['run_id'],'tenant-a')
    ids = [x['id'] for x in result['generations']]
    assert trace['payload']['generation_ids'] == ids
    assert trace['payload']['artifact_saved'] and trace['payload']['final_response_persisted']
    with pg.store.connection() as conn:
        metadata = json.loads(conn.execute('SELECT result_json FROM task_results WHERE task_id=?',(task['id'],)).fetchone()['result_json'])
        assert metadata['generation_ids'] == ids and metadata['generation_id'] == ids[0]
    async def api():
        cookie = main.sessions.issue(UserPrincipal(pg.actor['id'],'tenant-a','member'))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://fixture.test',cookies={'workbench_session':cookie}) as client:
            public = await client.get('/api/v1/tasks/'+task['id'])
            assert public.status_code == 200
            gallery = public.json()['generations']
            sse = await client.get('/api/v1/tasks/'+task['id']+'/events')
            assert sse.status_code == 200 and 'event: complete' in sse.text
            terminal = json.loads(sse.text.split('event: complete\ndata: ')[1].split('\n\n')[0])
            history = await client.get('/api/v1/conversations/'+result['conversation_id'])
            assert history.status_code == 200
            assistant = next(x for x in history.json()['messages'] if x['id'] == result['assistant_message_id'])
            assert terminal['generations'] == gallery == assistant['generations'] == history.json()['generations']
            assert terminal['generation'] == gallery[0] == assistant['generation']
            for index, image in enumerate(gallery):
                downloaded = await client.get(image['content_url'])
                assert downloaded.status_code == 200 and downloaded.content == png(index)
            for email in ('other@fixture.test','foreign@fixture.test'):
                user = pg.product.user_by_email(email)
                client.cookies.set('workbench_session', main.sessions.issue(UserPrincipal(user['id'],user['tenant_id'],'member')))
                assert (await client.get('/api/v1/tasks/'+task['id'])).status_code == 404
                assert (await client.get('/api/v1/conversations/'+result['conversation_id'])).status_code == 404
                for image in gallery:
                    assert (await client.get(image['content_url'])).status_code == 404
    asyncio.run(api())
    # Duplicate completed events/re-delivery don't read missing objects or rerun.
    for key in keys:
        storage_provider(pg.cfg).delete(key)
    with ThreadPoolExecutor(max_workers=2) as executor:
        replays = list(executor.map(lambda _:service._persist_result(result,trace),range(2)))
    assert all(x['replayed'] and x['generation_ids'] == ids for x in replays)
    asyncio.run(service.execute(result))
    assert runtime.turn_count == 1 and len(rows(pg,task)) == n
    with pg.store.connection() as conn:
        assert conn.execute('SELECT count(*) n FROM credit_transactions WHERE task_id=?',(task['id'],)).fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) n FROM messages WHERE id=?',(result['assistant_message_id'],)).fetchone()['n'] == 1
        assert conn.execute("SELECT count(*) n FROM task_events WHERE task_id=? AND stage='completed'",(task['id'],)).fetchone()['n'] == 1
    if n == 3:
        evidence = dict(database=pg.db,postgres='16.6',schema='001-015',task_id=task['id'],run_id=result['run_id'],
                        conversation_id=result['conversation_id'],generation_ids=ids,records=rows(pg,task),
                        result_metadata=metadata,api_sse_history_equal=True,charge_count=1,provider_calls=0)
        (pg.root/'multi-image-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2,default=str))


def test_real_pg_transaction_failure_after_first_image_and_worker_recovery(pg):
    task,runtime,service,keys = setup(pg,3)
    with pg.store.connection() as conn:
        conn.execute("CREATE FUNCTION fail_second_image() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.storage_key='"+keys[1]+"' THEN RAISE EXCEPTION 'fixture interruption'; END IF; RETURN NEW; END $$")
        conn.execute('CREATE TRIGGER image_failure BEFORE INSERT ON generations FOR EACH ROW EXECUTE FUNCTION fail_second_image()')
    asyncio.run(service.execute(task))
    failed = pg.product.task_for_worker(task['id'])
    assert failed['status'] == 'failed' and failed['stage'] == 'result_persistence_failed'
    no_result_or_charge(pg,task)
    with pg.store.connection() as conn:
        assert not conn.execute("SELECT 1 FROM messages WHERE id=?",(f"task:{task['id']}:assistant",)).fetchone()
        conn.execute('DROP TRIGGER image_failure ON generations')
        conn.execute('DROP FUNCTION fail_second_image()')
    # A new Worker/TaskService consumes the durable failed trace, not the model.
    recovered = TaskService(pg.product,AgentService(pg.store,runtime,pg.cfg))
    asyncio.run(recovered.execute(failed))
    complete = pg.product.task(task['id'],'tenant-a',pg.actor['id'])
    assert complete['status'] == 'completed' and [x['storage_key'] for x in complete['generations']] == keys
    assert runtime.turn_count == 1 and len(rows(pg,task)) == 3


@pytest.mark.parametrize('invalid',['missing','foreign','corrupt','prose_only','unauthorized_server','wrong_task_proof','wrong_tenant_proof'])
def test_unverified_artifact_set_rejected_atomically(pg,invalid):
    task,runtime,service,keys = setup(pg,3)
    if invalid == 'missing':
        storage_provider(pg.cfg).delete(keys[1])
    elif invalid == 'foreign':
        runtime.calls[1]['artifact']['storage_key'] = 'generated/tenant-b/foreign.png'
    elif invalid == 'corrupt':
        storage_provider(pg.cfg).put(keys[1],b'not-a-real-image','image/png')
    elif invalid == 'unauthorized_server':
        runtime.calls[1]['server'] = 'unapproved-server'
    elif invalid in {'wrong_task_proof','wrong_tenant_proof'}:
        runtime.calls[1]['image_dependency'] = reference_image_dependency(
            'tenant-b' if invalid == 'wrong_tenant_proof' else 'tenant-a',
            'foreign-task' if invalid == 'wrong_task_proof' else task['id'], 'fixture-reference')
        runtime.calls[1]['result_contract_valid'] = True
    else:
        runtime.calls[1].pop('artifact')
        runtime.calls[1]['output_summary'] = str({'storage_key':keys[1]})
    asyncio.run(service.execute(task))
    assert pg.product.task_for_worker(task['id'])['status'] == 'failed'
    no_result_or_charge(pg,task)
    assert runtime.turn_count == 1


@pytest.mark.parametrize('n',[1,2,3])
def test_runtime_failure_never_persists_success_images(pg,n):
    task,runtime,service,_ = setup(pg,n,fail=True)
    asyncio.run(service.execute(task))
    assert pg.product.task_for_worker(task['id'])['status'] == 'failed'
    no_result_or_charge(pg,task)
    asyncio.run(service.execute(pg.product.task_for_worker(task['id'])))
    assert runtime.turn_count == 1


def test_active_retry_and_same_object_dedup_use_existing_dependency_contract(pg):
    task,runtime,service,keys = setup(pg,2)
    first,second = correction_pair()
    second['artifact'] = runtime.calls[0]['artifact']
    runtime.calls = [first,second,runtime.calls[1],
                     {**observed({'prompt':'same object returned'},call_id='repeat-object'), 'artifact':runtime.calls[1]['artifact']}]
    asyncio.run(service.execute(task))
    saved = pg.product.task(task['id'],'tenant-a',pg.actor['id'])
    assert saved['status'] == 'completed' and [x['storage_key'] for x in saved['generations']] == keys
    assert len(rows(pg,task)) == 2
    trace = pg.store.run_trace(saved['run_id'],'tenant-a')['payload']
    assert trace['mcp_calls'][0]['superseded_by'] and trace['required_tool_calls_completed']


def test_duplicate_attempt_identity_rejected_by_unchanged_retry_guard(pg):
    task,runtime,service,_ = setup(pg,2)
    runtime.calls[1]['attempt_id'] = runtime.calls[0]['attempt_id']
    asyncio.run(service.execute(task))
    saved = pg.product.task_for_worker(task['id'])
    assert saved['status'] == 'failed'
    payload = pg.store.run_trace(saved['run_id'],'tenant-a')['payload']
    assert not payload['required_tool_calls_completed']
    assert any(x.get('lineage_error') == 'duplicate_attempt_identity' for x in payload['mcp_calls'])
    no_result_or_charge(pg,task)


def test_saved_evidence_not_caller_supplied_images_and_cross_run_rejected(pg):
    task,runtime,service,_ = setup(pg,2)
    # Delay only the store finalization to inspect actual durable runtime proof.
    original = pg.product.complete_task_success
    pg.product.complete_task_success = lambda *a,**kw: (_ for _ in ()).throw(ResultPersistenceError('fixture pause'))
    asyncio.run(service.execute(task))
    pg.product.complete_task_success = original
    failed = pg.product.task_for_worker(task['id'])
    trace = pg.store.run_trace(failed['run_id'],'tenant-a')
    with pytest.raises(ResultPersistenceError):
        original(failed,run_id=trace['run_id'],conversation_id=trace['conversation_id'],response=trace['payload']['final_result'],
                 trace_payload={**trace['payload'],'artifact_available':True},image_artifacts=[{'storage_key':'generated/tenant-a/forged.png'}])
    with pytest.raises(ResultPersistenceError):
        original(failed,run_id='foreign-run',conversation_id=trace['conversation_id'],response=trace['payload']['final_result'],
                 trace_payload={**trace['payload'],'artifact_available':True},image_artifacts=[])
    no_result_or_charge(pg,task)


def test_historical_scalar_projection_and_no_unpersisted_backfill(pg):
    task,runtime,service,_ = setup(pg,1)
    asyncio.run(service.execute(task))
    result = pg.product.task(task['id'],'tenant-a',pg.actor['id'])
    with pg.store.connection() as conn:
        metadata = json.loads(conn.execute('SELECT result_json FROM task_results WHERE task_id=?',(task['id'],)).fetchone()['result_json'])
        metadata.pop('generation_ids')
        conn.execute('UPDATE task_results SET result_json=? WHERE task_id=?',(json.dumps(metadata),task['id']))
    old = pg.product.task(task['id'],'tenant-a',pg.actor['id'])
    assert old['generation'] == result['generation'] and old['generations'] == [old['generation']]
    with pg.store.connection() as conn:
        conn.execute('DELETE FROM generations WHERE task_id=?',(task['id'],))  # isolated historical-gap fixture only
    assert pg.product.task(task['id'],'tenant-a',pg.actor['id'])['generations'] == []
    assert pg.product.conversation_detail('tenant-a',pg.actor['id'],result['conversation_id'])['generations'] == []
    assert not rows(pg,task)
