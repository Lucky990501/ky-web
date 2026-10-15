"""Real private ledger/Task DB and loopback; no external Provider requests."""
import asyncio
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import replace
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import time
import types
from uuid import uuid4

import httpx
import pytest

from app.domain import RuntimeProfile
from app.product_store import ProductStore
from app.security import RuntimeTokenIssuer, TokenError
from app.settings import Settings
from app.skills import SkillDeployment
from app.store import POCStore
from app.test_text_gate import RequestBudget, TestTextGate, TestTextGateDenied
from app.runtime.codex_provider import CodexRuntimeManager


def request():
    return json.dumps({"model":"deepseek-v4-pro","input":"Reply with OK.","reasoning":{"effort":"high"},
                       "max_output_tokens":512,"stream":False}).encode()


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    s = replace(Settings.from_env(),data_dir=tmp_path/'data',database_url=f'sqlite:///{tmp_path}/business.db',
                environment='test',model_provider_id='deepseek',model_id='deepseek-v4-pro',reasoning_effort='high',
                model_base_url='https://api.deepseek.com/',model_wire_api='responses',
                image_provider_id='disabled-test-provider',embedding_provider='local-hash',
                test_text_gate_enabled=True,test_text_gate_state=tmp_path/'budget.sqlite')
    store = POCStore(s.database_url)
    store.initialize()
    ProductStore(store).initialize()
    tenant,user,task_id,run_id = 'isolated-test-a',str(uuid4()),str(uuid4()),str(uuid4())
    with store.connection() as c:
        c.execute('INSERT INTO tenants VALUES(?,?,?)',(tenant,'Synthetic',str(uuid4())))
        c.execute("INSERT INTO users(id,tenant_id,email,password_hash,display_name,role) VALUES(?,?,?,?,?,?)",
                  (user,tenant,'a@example.invalid','not-a-real-password','Synthetic','member'))
        c.execute("INSERT INTO run_traces(run_id,tenant_id,agent_id,conversation_id,status,payload) VALUES(?,?,?,?,?,?)",
                  (run_id,tenant,'copywriting-agent','synthetic-conversation','running','{}'))
        c.execute("INSERT INTO tasks(id,tenant_id,user_id,agent_id,input_text,status,run_id,stage) VALUES(?,?,?,?,?,?,?,?)",
                  (task_id,tenant,user,'copywriting-agent','short input','running',run_id,'generating'))
    task = ProductStore(store).task_for_worker(task_id)
    budget = RequestBudget.initialize(s.test_text_gate_state,s.database_url,tenant,str(uuid4()),time.time()+3600)
    budget.allow(task)
    issuer = RuntimeTokenIssuer('isolated-signing-secret-only')
    gate = TestTextGate(s,issuer)
    profile = RuntimeProfile.build(tenant_id=tenant,agent_id='copywriting-agent',model_provider_id='deepseek',
                                   model_id='deepseek-v4-pro',reasoning_effort='high',skill_manifest={})
    monkeypatch.setenv(s.codex_api_key_env,'fake-upstream-secret-not-a-credential')
    return s,store,task,budget,gate,profile


def reserve_once(args):
    path,url,task = args
    try:
        return RequestBudget(Path(path),url).reserve(task,b'isolated request')
    except TestTextGateDenied:
        return None


@pytest.mark.parametrize('mode',['threads','processes'])
def test_shared_budget_atomic_across_workers(fixture, mode):
    s,_,task,budget,_,_ = fixture
    args = (str(budget.path),s.database_url,task)
    pool = ThreadPoolExecutor(8) if mode=='threads' else ProcessPoolExecutor(4,mp_context=multiprocessing.get_context('fork'))
    with pool:
        results = list(pool.map(reserve_once,[args]*12))
    assert sorted(r for r in results if r is not None) == [1,2]
    # A new object/process startup does not initialize or refund the ledger.
    assert reserve_once(args) is None
    with budget.connect() as c:
        assert c.execute('SELECT spent FROM budget').fetchone()[0] == 2
        assert c.execute('SELECT count(*) FROM attempts').fetchone()[0] == 2


def stub_http(monkeypatch, outcome=200):
    calls=[]
    def handler(req):
        calls.append(req)
        if isinstance(outcome,Exception):
            raise outcome
        return httpx.Response(outcome,json={'output_text':'isolated mocked OK'})
    transport=httpx.MockTransport(handler)
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kw: transport)
    return calls


@pytest.mark.parametrize('outcome',[200,500,'timeout'])
def test_actual_forward_failures_retries_share_spent_slots(fixture,monkeypatch,outcome):
    _,_,task,budget,gate,profile=fixture
    calls=stub_http(monkeypatch,httpx.ReadTimeout('isolated') if outcome=='timeout' else outcome)
    async def run():
        _,token=await gate.bind(profile,task['id'])
        # Draft, correction/review and retry do not get separate allowances.
        for phase in ('draft','correction_or_review'):
            if outcome=='timeout':
                with pytest.raises(httpx.ReadTimeout): await gate.forward(token,request())
            else:
                result=await gate.forward(token,request())
                assert result[0]==outcome
        with pytest.raises(TestTextGateDenied,match='exhausted'):
            await gate.forward(token,request())
        await gate.close()
    asyncio.run(run())
    assert len(calls)==2
    assert all(str(r.url)=='https://api.deepseek.com/responses' for r in calls)
    with budget.connect() as c: assert c.execute('SELECT spent FROM budget').fetchone()[0]==2


@pytest.mark.parametrize('mutation',['not_allowed','tenant','user','agent','failed_run','cancelled_task','disabled_user'])
def test_actual_task_context_not_client_claims(fixture,mutation):
    _,store,task,_,gate,p=fixture
    with store.connection() as c:
        if mutation=='not_allowed': c.execute('UPDATE tasks SET id=? WHERE id=?',(str(uuid4()),task['id']))
        elif mutation=='tenant': p=replace(p,tenant_id='other-test-tenant')
        elif mutation=='user':
            # Another real user in the SAME tenant cannot inherit this whitelist.
            other=str(uuid4())
            c.execute("INSERT INTO users(id,tenant_id,email,password_hash,display_name,role) VALUES(?,?,?,?,?,?)",
                      (other,task['tenant_id'],'other@example.invalid','test-only','Other','member'))
            c.execute('UPDATE tasks SET user_id=? WHERE id=?',(other,task['id']))
        elif mutation=='agent': p=replace(p,agent_id='campaign-agent')
        elif mutation=='failed_run': c.execute("UPDATE run_traces SET status='failed' WHERE run_id=?",(task['run_id'],))
        elif mutation=='cancelled_task': c.execute("UPDATE tasks SET status='cancelled' WHERE id=?",(task['id'],))
        else: c.execute("UPDATE users SET account_status='disabled' WHERE id=?",(task['user_id'],))
    async def run():
        with pytest.raises(TestTextGateDenied): await gate.bind(p,task['id'])
        await gate.close()
    asyncio.run(run())


@pytest.mark.parametrize('fault',['missing','corrupt','permissions','revoked','expired','inconsistent'])
def test_state_loss_and_unavailability_fail_closed(fixture,fault):
    s,_,task,budget,_,_=fixture
    if fault=='missing': budget.path.unlink()
    elif fault=='corrupt': budget.path.write_bytes(b'not SQLite')
    elif fault=='permissions': budget.path.chmod(0o644)
    else:
        with budget.connect() as c:
            if fault=='revoked': c.execute('UPDATE budget SET revoked=1')
            elif fault=='expired': c.execute('UPDATE budget SET expires_at=0')
            else: c.execute('UPDATE budget SET spent=1')
    with pytest.raises((OSError,sqlite3.Error,TestTextGateDenied)):
        RequestBudget(budget.path,s.database_url).reserve(task,request())


def test_signed_context_cannot_be_forged_or_replayed_for_new_run(fixture,monkeypatch):
    _,store,task,_,gate,p=fixture
    calls=stub_http(monkeypatch)
    async def run():
        _,token=await gate.bind(p,task['id'])
        with pytest.raises(TokenError): await gate.forward(token+'tamper',request())
        with store.connection() as c:
            c.execute("UPDATE tasks SET run_id=NULL WHERE id=?",(task['id'],))
        with pytest.raises(TestTextGateDenied): await gate.forward(token,request())
        await gate.close()
    asyncio.run(run())
    assert calls==[]


@pytest.mark.parametrize('mutation',['model','reasoning','tools','instructions','output','input','history'])
def test_unproven_or_over_budget_input_rejected_before_send(fixture,monkeypatch,mutation):
    _,_,task,_,gate,p=fixture
    calls=stub_http(monkeypatch)
    body=json.loads(request())
    if mutation=='model': body['model']='other'
    elif mutation=='reasoning': body['reasoning']={'effort':'low'}
    elif mutation=='tools': body['tools']=[{'type':'remote_unknown_tool','name':'example'}]
    elif mutation=='instructions': body['instructions']={'file_id':'unknown-remote-material'}
    elif mutation=='output': body['max_output_tokens']=8193
    elif mutation=='input': body['input']='x'*100000
    else: body['input']=[{'type':'item_reference','id':'unknown-server-history'}]
    async def run():
        _,token=await gate.bind(p,task['id'])
        with pytest.raises(TestTextGateDenied): await gate.forward(token,json.dumps(body).encode())
        await gate.close()
    asyncio.run(run())
    assert calls==[]


def test_real_loopback_http_reaches_shared_gate(fixture,monkeypatch):
    _,_,task,_,gate,p=fixture
    calls=stub_http(monkeypatch)
    async def run():
        url,token=await gate.bind(p,task['id'])
        port=int(url.rsplit(':',1)[1])
        for n in range(3):
            reader,writer=await asyncio.open_connection('127.0.0.1',port)
            raw=request()
            writer.write(f'POST /responses HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {token}\r\nContent-Length: {len(raw)}\r\n\r\n'.encode()+raw)
            await writer.drain()
            result=await reader.read()
            assert result.startswith(b'HTTP/1.1 200') if n<2 else b'test_text_gate_exhausted' in result
            writer.close(); await writer.wait_closed()
        await gate.close()
    asyncio.run(run())
    assert len(calls)==2


def test_test_only_config_and_default_disabled(fixture):
    s,_,_,_,_,_=fixture
    assert Settings.from_env().test_text_gate_enabled is False
    with pytest.raises(TestTextGateDenied): TestTextGate(replace(s,environment='production'),RuntimeTokenIssuer('isolated-signing-secret-only'))
    manager=CodexRuntimeManager(replace(s,test_text_gate_enabled=False),None,RuntimeTokenIssuer('isolated-signing-secret-only'))
    assert manager._test_gate is None


def test_real_sdk_environment_and_config_are_task_bound(fixture,monkeypatch):
    s,_,task,_,gate,p=fixture
    configs=[]
    class Codex:
        def __init__(self,config): configs.append(config)
        async def __aenter__(self): return self
        async def close(self): pass
    import openai_codex
    monkeypatch.setattr(openai_codex,'AsyncCodex',Codex)
    monkeypatch.setenv('STAGE2_DEEPSEEK_TEST_API_KEY','must-not-reach-SDK')
    manager=CodexRuntimeManager(s,types.SimpleNamespace(deploy=lambda *a:None),gate.issuer)
    async def run():
        await manager.get(p,task_id=task['id'])
        config=configs[0]
        assert config.env['STAGE2_DEEPSEEK_TEST_API_KEY']==''
        assert config.env[s.codex_api_key_env] != os.environ[s.codex_api_key_env]
        path=Path(config.env['CODEX_HOME'])
        assert task['id'] in path.name
        toml=(path/'config.toml').read_text()
        assert 'http://127.0.0.1:' in toml and 'https://api.deepseek.com' not in toml
        assert 'request_max_retries = 0' in toml and 'stream_max_retries = 0' in toml
        assert 'fake-upstream-secret' not in toml
        with pytest.raises((ValueError,TestTextGateDenied,TypeError)): await manager.get(p)
        await manager.close()
    asyncio.run(run())


def test_existing_ledger_cannot_be_reinitialized(fixture):
    s,_,_,budget,_,_=fixture
    with pytest.raises(FileExistsError): RequestBudget.initialize(budget.path,s.database_url,'other',str(uuid4()),time.time()+100)


def test_lost_counter_cannot_reinitialize_same_operation(fixture):
    s,_,_,budget,_,_=fixture
    budget.path.unlink()
    with pytest.raises(FileExistsError):
        RequestBudget.initialize(budget.path,s.database_url,'other',str(uuid4()),time.time()+100)


def test_token_boundary_and_output_budget():
    body=json.loads(request())
    body['input']='x'*20000
    assert TestTextGate.bounded_payload(json.dumps(body).encode())['max_output_tokens']==512
    bound=TestTextGate.request_bound(body)
    assert bound['input_tokens_upper']>20000>1024
    from decimal import Decimal
    assert Decimal(bound['two_slot_cny_upper'])<=10
    body['input']='x'*100000
    with pytest.raises(TestTextGateDenied,match='cost_exceeded'):
        TestTextGate.bounded_payload(json.dumps(body).encode())


def full_request():
    body=json.loads(request())
    body.update(instructions='System instructions '*1000,reasoning={'effort':'high','summary':'auto'},
                input=[{'role':role,'content':[{'type':'input_text','text':role+' 中文 history'}]}
                       for role in ('system','developer','user')]+[
                    {'type':'reasoning','content':[{'type':'reasoning_text','text':'prior reasoning'}],'summary':[]},
                    {'type':'function_call','call_id':'call-1','name':'knowledge_search',
                     'arguments':json.dumps({'query':'unique fact','count':2,'nested':{'a':['中',1]}})},
                    {'type':'function_call_output','call_id':'call-1','output':'local knowledge result'},
                    {'role':'assistant','content':[{'type':'output_text','text':'prior answer','annotations':[]}]},
                    {'role':'user','content':'Now revise using all facts.'}],
                tools=[{'type':'function','name':'knowledge_search','description':'search',
                        'parameters':{'type':'object','properties':{'query':{'type':'string'}}}}],
                text={'format':{'type':'json_schema','name':'answer',
                                'schema':{'type':'object','properties':{'body':{'type':'string'}}}}})
    return body


def test_complete_history_tools_system_schema_bound_and_final_cap():
    body=full_request()
    body.pop('max_output_tokens')
    result=TestTextGate.bounded_payload(json.dumps(body).encode())
    assert result['input']==body['input'] and result['tools']==body['tools']
    assert result['instructions']==body['instructions'] and result['max_output_tokens']==8192
    bound=TestTextGate.request_bound(result)
    assert bound['input_tokens_upper']>len(json.dumps(result).encode())
    for field in ('instructions','tools','input','text'):
        smaller=dict(result)
        smaller[field]=[] if field in {'input','tools'} else '' if field=='instructions' else {}
        assert TestTextGate.request_bound(smaller)['input_tokens_upper']<bound['input_tokens_upper']


@pytest.mark.parametrize('opaque',[
    {'type':'message','role':'user','content':[{'type':'input_image','image_url':'https://example.invalid/image'}]},
    {'type':'message','role':'user','content':[{'type':'input_file','file_id':'remote'}]},
    {'type':'reasoning','encrypted_content':'opaque'},
    {'type':'web_search_call','id':'remote-search'},
    {'type':'function_call','call_id':'1','name':'search','arguments':'not-json'},
    {'type':'function_call','call_id':'1','name':'search','arguments':'[]'},
    {'type':'unknown_future_input','content':'cannot price'},
])
def test_unbounded_context_rejected_before_reservation(fixture,monkeypatch,opaque):
    _,_,task,budget,gate,profile=fixture
    calls=stub_http(monkeypatch)
    body=full_request()
    body['input'].append(opaque)
    async def run():
        _,token=await gate.bind(profile,task['id'])
        with pytest.raises(TestTextGateDenied,match='bound_not_proven'):
            await gate.forward(token,json.dumps(body).encode())
        await gate.close()
    asyncio.run(run())
    assert calls==[]
    with budget.connect() as c: assert c.execute('SELECT spent FROM budget').fetchone()[0]==0


def test_full_envelopes_keep_shared_two_request_ledger(fixture,monkeypatch):
    _,_,task,budget,gate,profile=fixture
    calls=stub_http(monkeypatch,500)
    body=full_request()
    async def run():
        _,token=await gate.bind(profile,task['id'])
        for _ in range(2):
            assert (await gate.forward(token,json.dumps(body).encode()))[0]==500
        with pytest.raises(TestTextGateDenied,match='exhausted'):
            await gate.forward(token,json.dumps(body).encode())
        await gate.close()
    asyncio.run(run())
    assert len(calls)==2
    from decimal import Decimal
    cost=sum(Decimal(TestTextGate.request_bound(json.loads(r.content))['cny_upper']) for r in calls)
    assert cost<=10
    with budget.connect() as c:
        assert c.execute('SELECT spent FROM budget').fetchone()[0]==2
        import hashlib
        assert c.execute('SELECT request_sha FROM attempts WHERE slot=1').fetchone()[0]==hashlib.sha256(calls[0].content).hexdigest()


@pytest.mark.parametrize('fault',['long_system','tools','many_messages','output','context','remote_history','nan'])
def test_full_request_budget_overages_never_spend_or_send(fixture,monkeypatch,fault):
    _,_,task,budget,gate,profile=fixture
    calls=stub_http(monkeypatch)
    body=full_request()
    if fault=='long_system': body['instructions']='x'*100000
    elif fault=='tools': body['tools'][0]['description']='x'*100000
    elif fault=='many_messages': body['input']=[{'role':'user','content':''}]*1200
    elif fault=='output': body['max_output_tokens']=8193
    elif fault=='context': body['input']=[{'role':'user','content':''}]*3000
    elif fault=='remote_history': body['previous_response_id']='opaque-context'
    else: body['tools'][0]['parameters']['value']=float('nan')
    async def run():
        _,token=await gate.bind(profile,task['id'])
        with pytest.raises((TestTextGateDenied,ValueError)):
            await gate.forward(token,json.dumps(body).encode())
        await gate.close()
    asyncio.run(run())
    assert calls==[]
    with budget.connect() as c: assert c.execute('SELECT spent FROM budget').fetchone()[0]==0


def test_dense_dsml_arguments_and_unicode_are_counted():
    body=full_request()
    original=TestTextGate.request_bound(body)['input_tokens_upper']
    call=next(i for i in body['input'] if i.get('type')=='function_call')
    call['arguments']=json.dumps({str(i):'中\n\t"😀' for i in range(100)})
    bound=TestTextGate.request_bound(body)
    assert bound['input_tokens_upper']>original+128*90
    assert TestTextGate.bounded_payload(json.dumps(body,ensure_ascii=False).encode())['input']==body['input']


def test_reservation_survives_interruption_before_http_start(fixture,monkeypatch):
    _,_,task,budget,gate,p=fixture
    class Interrupted:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): raise RuntimeError('isolated interruption before network')
        async def __aexit__(self,*args): pass
    monkeypatch.setattr(httpx,'AsyncClient',Interrupted)
    async def run():
        _,token=await gate.bind(p,task['id'])
        with pytest.raises(RuntimeError): await gate.forward(token,request())
        await gate.close()
    asyncio.run(run())
    with budget.connect() as c:
        assert c.execute('SELECT spent FROM budget').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM attempts').fetchone()[0]==1


def test_task_service_passes_real_current_task_identity(fixture):
    from app.product_service import TaskService
    s,store,task,_,_,_=fixture
    with store.connection() as c:
        c.execute("UPDATE tasks SET status='queued',run_id=NULL WHERE id=?",(task['id'],))
    task=ProductStore(store).task_for_worker(task['id'])
    captured={}
    class Agents:
        _settings=s
        _store=store
        async def run(self,*args,**kwargs):
            captured.update(kwargs)
            raise RuntimeError('intentional local stop before Runtime')
    asyncio.run(TaskService(ProductStore(store),Agents()).execute(task))
    assert captured['task_id']==task['id']
    assert ProductStore(store).task_for_worker(task['id'])['status']=='failed'


def test_new_and_resumed_sessions_use_current_task_scope(fixture):
    from app.runtime.codex_provider import CodexRuntimeProvider
    s,_,task,_,gate,p=fixture
    manager=CodexRuntimeManager(s,types.SimpleNamespace(deploy=lambda *a:None),gate.issuer)
    received=[]
    class Codex:
        async def thread_start(self,**kwargs): return types.SimpleNamespace(id='isolated-thread')
        async def thread_resume(self,thread_id,**kwargs): return types.SimpleNamespace(id=thread_id)
    async def get(profile,*,task_id=None):
        received.append(task_id)
        return Codex()
    manager.get=get
    provider=CodexRuntimeProvider(manager)
    next_task=str(uuid4())
    async def run():
        first=await provider.create_session(p,'isolated',task_id=task['id'])
        second=await provider.resume_session(p,first.thread_id,task_id=next_task)
        assert provider._task_ids[second.thread_id]==next_task
        await manager.close()
    asyncio.run(run())
    assert received==[task['id'],next_task]


def test_grounding_audit_inherits_current_task_not_a_new_budget(fixture):
    from test_grounded_writing_runtime import ScriptedGroundedProvider,draft,audit_pass,profile as grounded_profile
    _,_,task,_,_,_=fixture
    class Grounded(ScriptedGroundedProvider):
        async def create_session(self,p,instructions,*,task_id=None):
            self.received.append(task_id)
            session=await super().create_session(p,instructions)
            self._task_ids[session.thread_id]=task_id
            return session
    provider=Grounded([draft(),audit_pass()])
    provider.received=[]
    provider._manager=types.SimpleNamespace(_test_gate=object())
    async def run():
        session=await provider.create_session(grounded_profile(),'main',task_id=task['id'])
        events=[e async for e in provider.stream_turn(session,'write short text')]
        assert events[-1].turn.grounding_telemetry['final_pass']
    asyncio.run(run())
    assert provider.received==[task['id'],task['id']]


def test_image_calls_remain_disabled_before_auth_or_http(fixture):
    from app.platform_mcp.service import PlatformMCPService
    s,store,_,_,gate,_=fixture
    service=PlatformMCPService(store,gate.issuer,s)
    async def run():
        with pytest.raises(PermissionError,match='image_disabled'):
            await service.image_generation('not-a-token','not-called',[],'1:1')
    asyncio.run(run())
