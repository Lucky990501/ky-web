"""Versioned pilot gate; isolated SQLite/loopback and mocked upstream ONLY."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time
import types
from uuid import uuid4

import httpx
import pytest

from app.agent_productization import AgentProductization
from app.domain import RuntimeProfile
from app.product_store import ProductStore
from app.security import RuntimeTokenIssuer
from app.settings import Settings
from app.store import POCStore
from app.test_text_gate import RequestBudget, TestTextGate, TestTextGateDenied


def request(text='Reply OK.'):
    return json.dumps({'model':'deepseek-v4-pro','input':text,'reasoning':{'effort':'high'},
                       'max_output_tokens':512,'stream':False}).encode()


def synthetic_task(store,identity,*,agent='copywriting-agent',user=None):
    user=user or identity['member_id']
    task={'id':str(uuid4()),'tenant_id':'zhiy-e-intelligence','user_id':user,
          'agent_id':agent,'status':'running','run_id':str(uuid4()),'input_text':'isolated synthetic input'}
    with store.connection() as c:
        c.execute("INSERT INTO run_traces(run_id,tenant_id,agent_id,conversation_id,status,payload) VALUES(?,?,?,?,?,?)",
                  (task['run_id'],task['tenant_id'],agent,str(uuid4()),'running','{}'))
        c.execute("INSERT INTO tasks(id,tenant_id,user_id,agent_id,input_text,status,run_id,stage) VALUES(?,?,?,?,?,?,?,?)",
                  (task['id'],task['tenant_id'],user,agent,task['input_text'],'running',task['run_id'],'generating'))
    return task


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    tmp_path.chmod(0o700)
    s=replace(Settings.from_env(),data_dir=tmp_path/'data',database_url=f'sqlite:///{tmp_path}/business.db',
        object_storage_dir=tmp_path/'objects',environment='test',model_provider_id='deepseek',model_id='deepseek-v4-pro',reasoning_effort='high',
        model_base_url='https://api.deepseek.com/',model_wire_api='responses',
        image_provider_id='image-gateway',image_model_id='gpt-image-2.5-sunburst-c',embedding_provider='local-hash',
        test_text_gate_enabled=True,test_text_gate_state=tmp_path/'budget.sqlite')
    store=POCStore(s.database_url); store.initialize(); ProductStore(store).initialize()
    AgentProductization(store,'test').initialize()
    member,operator,agent,revision=[str(uuid4()) for _ in range(4)]
    with store.connection() as c:
        c.execute('INSERT INTO tenants VALUES(?,?,?)',('zhiy-e-intelligence','isolated fixture','ISOLATED-NOT-A-CREDENTIAL-'+str(uuid4())))
        c.execute('INSERT INTO enterprise_configs(tenant_id,payload) VALUES(?,?)',('zhiy-e-intelligence','{}'))
        for user,role in ((member,'member'),(operator,'enterprise_admin')):
            c.execute('INSERT INTO users(id,tenant_id,email,password_hash,display_name,role) VALUES(?,?,?,?,?,?)',
                (user,'zhiy-e-intelligence',user+'@example.invalid','NOT_REAL','isolated fixture',role))
    import app.test_text_gate as module
    # Explicit isolated authority reader substitute; never installed or real authorization.
    monkeypatch.setattr(module,'read_technical_scope',lambda path:({'operation_id':operation,'expires_at':expiry},'c'*64))
    operation=str(uuid4()); expiry=time.time()+3600
    budget=RequestBudget.initialize(s.test_text_gate_state,s.database_url,'zhiy-e-intelligence',operation,
        expiry,member_id=member,operator_id=operator,wechat_agent_id=agent,revision_id=revision,
        fingerprint='a'*64,owner_approved_at=time.time()-1,owner_event_sha256='b'*64,scope_sha256='c'*64,context_path=tmp_path/'NOT_REAL_AUTHORITY.json')
    with budget.connect() as c: identity=budget.scope(c)
    task=synthetic_task(store,identity); budget.allow(task)
    gate=TestTextGate(s,RuntimeTokenIssuer('isolated-not-a-real-signing-secret'))
    profile=RuntimeProfile.build(tenant_id=task['tenant_id'],agent_id=task['agent_id'],
        model_provider_id='deepseek',model_id='deepseek-v4-pro',reasoning_effort='high',skill_manifest={})
    monkeypatch.setenv(s.codex_api_key_env,'ISOLATED_INVALID_UPSTREAM_KEY')
    return s,store,task,budget,gate,profile


def stats(budget):
    with budget.connect() as c:
        return dict(c.execute("SELECT count(*) AS n,COALESCE(sum(input_debit),0) AS i,COALESCE(sum(output_debit),0) AS o FROM attempts").fetchone())


def creation_entry(fixture):
    s,store,task,budget,_,_=fixture
    with store.connection() as c:
        c.execute('INSERT INTO credit_accounts(tenant_id,balance) VALUES(?,64)',(task['tenant_id'],))
        c.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status) VALUES(?,?,'enabled')",
                  (task['tenant_id'],task['agent_id']))
    return ProductStore(store,test_gate_settings=s)


def test_creation_entry_counts_queued_and_startup_failed_tasks_without_sdk_claim(fixture):
    _,store,initial,budget,_,_=fixture
    entry=creation_entry(fixture)
    created=[entry.create_task(initial['tenant_id'],initial['user_id'],initial['agent_id'],'Fixture only',None)
             for _ in range(5)]
    entry.set_task(created[0]['id'],initial['tenant_id'],'failed','failed','Fixture startup failure')
    # Five queued/failed new Tasks + the original fixture Task fill the six cap.
    with pytest.raises(PermissionError):
        ProductStore(store,test_gate_settings=fixture[0]).create_task(
            initial['tenant_id'],initial['user_id'],initial['agent_id'],'No seventh Task',None)
    with store.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM tasks').fetchone()['n']==6
    with budget.connect() as c:
        assert c.execute('SELECT count(*) FROM allowed_tasks').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM attempts').fetchone()[0]==0


def test_creation_entry_concurrent_calls_cannot_overbook_six(fixture):
    _,store,initial,_,_,_=fixture
    entry=creation_entry(fixture)
    def create(n):
        try:
            return entry.create_task(initial['tenant_id'],initial['user_id'],initial['agent_id'],
                                     'Isolated concurrent '+str(n),None)['id']
        except PermissionError:
            return None
    with ThreadPoolExecutor(12) as pool:
        results=list(pool.map(create,range(16)))
    assert len([r for r in results if r])==5
    with store.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM tasks').fetchone()['n']==6


def test_creation_entry_cross_tenant_and_operator_ordinary_requests_write_nothing(fixture):
    _,store,initial,budget,_,_=fixture
    entry=creation_entry(fixture)
    with budget.connect() as c:identity=budget.scope(c)
    for tenant,user in (('another-tenant',initial['user_id']),
                        (initial['tenant_id'],identity['operator_id'])):
        with pytest.raises(PermissionError):
            entry.create_task(tenant,user,initial['agent_id'],'Rejected before insert',None)
    with store.connection() as c:
        assert c.execute('SELECT count(*) AS n FROM tasks').fetchone()['n']==1


def test_byte_bound_covers_canonical_nested_function_argument_expansion():
    # Compact numeric arrays acquire spaces in the documented DSML serializer.
    args={'values':list(range(2000)),'text':'中文\\n"quoted"'}
    body={'model':'deepseek-v4-pro','max_output_tokens':512,'input':[
        {'type':'function_call','call_id':'f1','name':'example',
         'arguments':json.dumps(args,ensure_ascii=False,separators=(',',':'))}]}
    upper=TestTextGate.request_bound(body)['input_tokens_upper']
    rendered=''.join(str(k)+json.dumps(v,ensure_ascii=False) for k,v in args.items()).encode()
    assert upper>=len(rendered)+4096


def test_full_context_growth_is_counted_and_oversized_envelope_never_reserves(fixture):
    _,_,_,budget,gate,_=fixture
    body={'model':'deepseek-v4-pro','max_output_tokens':512,'reasoning':{'effort':'high'},
          'input':'plain','instructions':'unchanged','tools':[]}
    original=TestTextGate.request_bound(body)['input_tokens_upper']
    body['tools']=[{'type':'function','name':'complete_schema','parameters':
                   {'type':'object','properties':{'history':{'description':'history '*1000}}}}]
    assert TestTextGate.request_bound(body)['input_tokens_upper']>original+7000
    body['input']='x'*140000
    payload=gate.bounded_payload(json.dumps(body).encode())
    upper=gate.request_bound(payload)
    with pytest.raises(TestTextGateDenied,match='tokens_exhausted'):
        budget.reserve(fixture[2],json.dumps(payload).encode(),input_tokens=upper['input_tokens_upper'],
                       output_tokens=upper['output_tokens_upper'])
    assert stats(budget)['n']==0


def namespace_payload():
    return {'model':'deepseek-v4-pro','reasoning':{'effort':'high'},'max_output_tokens':512,
        'instructions':'Original instructions','input':'Do not change F:\\folder\\file or /opt/a/b.',
        'tools':[{'type':'namespace','name':'mcp__platform','description':'Original namespace description',
            'tools':[{'type':'function','name':'enterprise_config_get','description':'Original function description',
                      'parameters':{'type':'object','properties':{'path':{'type':'string'}},'additionalProperties':False}}]}]}


def test_namespace_mapping_preserves_complete_schema_and_original_input():
    import copy
    original=namespace_payload();before=copy.deepcopy(original)
    payload,mapping=TestTextGate.provider_tools(original)
    assert original==before and payload['input']==before['input']
    assert payload['tools']==[{**original['tools'][0]['tools'][0],
                              'name':'mcp__platform__enterprise_config_get'}]
    assert mapping=={'mcp__platform__enterprise_config_get':('mcp__platform','enterprise_config_get')}
    assert 'Original instructions' in payload['instructions']
    assert 'Original namespace description' in payload['instructions']


def test_namespace_collisions_and_unknown_identity_refused_without_dropping_tools():
    body=namespace_payload()
    body['tools'].append({'type':'function','name':'mcp__platform__enterprise_config_get','parameters':{}})
    with pytest.raises(TestTextGateDenied,match='collision'):TestTextGate.provider_tools(body)
    body=namespace_payload();body['input']=[{'type':'function_call','namespace':'not_registered',
        'name':'enterprise_config_get','call_id':'c1','arguments':'{}'}]
    with pytest.raises(TestTextGateDenied,match='unknown_tool_namespace'):TestTextGate.provider_tools(body)


def test_namespace_history_and_choice_keep_arguments_and_call_id():
    body=namespace_payload();arguments='{"literal":"mcp__platform","path":"F:\\\\raw"}'
    body['input']=[{'type':'function_call','namespace':'mcp__platform','name':'enterprise_config_get',
        'call_id':'original_call','arguments':arguments},
        {'type':'function_call_output','call_id':'original_call','output':'Original output'}]
    body['tool_choice']={'type':'function','namespace':'mcp__platform','name':'enterprise_config_get'}
    mapped,_=TestTextGate.provider_tools(body)
    assert mapped['input'][0]=={'type':'function_call','name':'mcp__platform__enterprise_config_get',
                               'call_id':'original_call','arguments':arguments}
    assert mapped['input'][1]==body['input'][1]
    assert mapped['tool_choice']=={'type':'function','name':'mcp__platform__enterprise_config_get'}


def test_namespace_sse_restores_added_done_and_completed_without_completing_truncation():
    mapping={'mcp__platform__enterprise_config_get':('mcp__platform','enterprise_config_get')}
    call={'type':'function_call','name':'mcp__platform__enterprise_config_get','arguments':'{}','call_id':'c1'}
    events=[{'type':'response.output_item.added','item':call},
            {'type':'response.output_item.done','item':call},
            {'type':'response.completed','response':{**completed(),'output':[call]}}]
    raw=b''.join(b'data: '+json.dumps(event).encode()+b'\n\n' for event in events)
    mapped=TestTextGate.sdk_tools('text/event-stream',raw,mapping)
    output=[json.loads(block[6:]) for block in mapped.decode().split('\n\n') if block]
    for event in output:
        item=event.get('item') or event['response']['output'][0]
        assert item['namespace']=='mcp__platform' and item['name']=='enterprise_config_get'
        assert item['call_id']=='c1' and item['arguments']=='{}'
    partial=TestTextGate.sdk_tools('text/event-stream',raw[:-1],mapping)
    assert not partial.endswith(b'\n\n') and TestTextGate.confirmed_usage(200,'text/event-stream',partial) is None


def test_actual_loopback_namespace_wire_hash_and_restored_sdk_response(fixture,monkeypatch):
    _,_,task,budget,gate,profile=fixture
    captured=[]
    def handler(req):
        captured.append(req)
        p=json.loads(req.content)
        assert p['tools'][0]['type']=='function'
        assert p['tools'][0]['name']=='mcp__platform__enterprise_config_get'
        call={'type':'function_call','name':'mcp__platform__enterprise_config_get','arguments':'{}','call_id':'fixture_call'}
        event={'type':'response.output_item.done','item':call}
        terminal={'type':'response.completed','response':{**completed(),'output':[call]}}
        return httpx.Response(200,headers={'content-type':'text/event-stream'},
            content=b'data: '+json.dumps(event).encode()+b'\n\n' +b'data: '+json.dumps(terminal).encode()+b'\n\n')
    actual_transport=httpx.AsyncHTTPTransport
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kw:httpx.MockTransport(handler))
    async def run():
        url,credential=await gate.bind(profile,task['id'])
        async with httpx.AsyncClient(transport=actual_transport(retries=0),trust_env=False) as client:
            result=await client.post(url+'/responses',content=json.dumps(namespace_payload()).encode(),
                                     headers={'Authorization':'Bearer '+credential})
        await gate.close()
        assert result.status_code==200
        first=json.loads(result.content.split(b'\n\n')[0][6:])['item']
        assert (first['name'],first['namespace'],first['call_id'])==('enterprise_config_get','mcp__platform','fixture_call')
    asyncio.run(run())
    assert len(captured)==1
    with budget.connect() as c:
        row=c.execute('SELECT * FROM attempts').fetchone()
        assert row['request_sha']==hashlib.sha256(captured[0].content).hexdigest()
        assert row['settled_sha'] is not None and row['input_debit']==100


def test_twenty_http_slots_survive_restart_and_cannot_reset(fixture):
    s,_,task,budget,_,_=fixture
    for n in range(20): budget.reserve(task,str(n).encode(),input_tokens=1,output_tokens=1)
    with pytest.raises(TestTextGateDenied,match='http_exhausted'):
        RequestBudget(budget.path,s.database_url).reserve(task,b'21',input_tokens=1,output_tokens=1)
    assert stats(budget)=={'n':20,'i':20,'o':20}
    with pytest.raises(FileExistsError):
        RequestBudget.initialize(budget.path,s.database_url,'zhiy-e-intelligence',str(uuid4()),time.time()+60,
            member_id=str(uuid4()),operator_id=str(uuid4()),wechat_agent_id=str(uuid4()),revision_id=str(uuid4()),
            fingerprint='a'*64,owner_approved_at=time.time()-1,owner_event_sha256='b'*64,scope_sha256='c'*64,context_path=budget.path.parent/'NOT_REAL_AUTHORITY.json')


def test_global_concurrent_token_reservation(fixture):
    s,_,task,budget,_,_=fixture
    def send(n):
        try: return RequestBudget(budget.path,s.database_url).reserve(task,str(n).encode(),input_tokens=80000,output_tokens=8192)
        except TestTextGateDenied: return None
    with ThreadPoolExecutor(8) as pool: result=list(pool.map(send,range(12)))
    assert len([x for x in result if x is not None])==1
    assert stats(budget)=={'n':1,'i':80000,'o':8192}


def test_only_successful_usage_settles_tokens_never_http_or_task_slots(fixture):
    _,_,task,budget,_,_=fixture
    for n in range(20):
        slot=budget.reserve(task,str(n).encode(),input_tokens=100000,output_tokens=8192)
        budget.settle(slot,'d'*64,input_tokens=100,output_tokens=50)
    assert stats(budget)=={'n':20,'i':2000,'o':1000}
    with budget.connect() as c:
        assert c.execute('SELECT count(*) FROM allowed_tasks').fetchone()[0]==1


def test_unknown_usage_keeps_all_reservations_and_replay_is_denied(fixture):
    _,_,task,budget,_,_=fixture
    slot=budget.reserve(task,b'one',input_tokens=100000,output_tokens=8192)
    with pytest.raises(TestTextGateDenied,match='tokens_exhausted'):
        budget.reserve(task,b'two',input_tokens=40000,output_tokens=1)
    with pytest.raises(TestTextGateDenied,match='paid_replay'):
        budget.reserve(task,b'one',input_tokens=1,output_tokens=1)
    with pytest.raises(TestTextGateDenied,match='usage_not_bounded'):
        budget.settle(slot,'e'*64,input_tokens=100001,output_tokens=1)
    assert stats(budget)=={'n':1,'i':100000,'o':8192}


def test_output_including_reasoning_pool_is_reserved(fixture):
    _,_,task,budget,_,_=fixture
    for n in range(8): budget.reserve(task,str(n).encode(),input_tokens=1,output_tokens=8192)
    with pytest.raises(TestTextGateDenied,match='tokens_exhausted'):
        budget.reserve(task,b'9',input_tokens=1,output_tokens=1)
    assert stats(budget)['o']==65536


def test_six_customer_tasks_and_separate_single_runtime_test(fixture):
    _,store,_,budget,_,_=fixture
    with budget.connect() as c: identity=budget.scope(c)
    for _ in range(5): budget.allow(synthetic_task(store,identity))
    with pytest.raises(TestTextGateDenied,match='task_limit'):
        budget.allow(synthetic_task(store,identity))
    rt=synthetic_task(store,identity,agent=identity['wechat_agent_id'],user=identity['operator_id'])
    rt.update(runtime_test_id=str(uuid4()),test_revision=identity['revision_id'],test_fingerprint=identity['fingerprint'])
    budget.allow(rt); budget.allow(rt)
    rt2={**rt,'id':str(uuid4()),'run_id':str(uuid4())}
    with pytest.raises(TestTextGateDenied,match='task_limit'): budget.allow(rt2)
    for key,value in (('test_revision',str(uuid4())),('test_fingerprint','e'*64),('user_id',identity['member_id'])):
        with pytest.raises(TestTextGateDenied,match='runtime_identity'): budget.allow({**rt,key:value})


@pytest.mark.parametrize('field,value',[('tenant_id','another-tenant'),('user_id','another-user'),('agent_id','unapproved-agent'),('run_id','changed-run'),('status','failed')])
def test_wrong_identity_or_failed_task_denied(fixture,field,value):
    _,_,task,budget,_,_=fixture
    with pytest.raises(TestTextGateDenied): budget.reserve({**task,field:value},b'bad',input_tokens=1,output_tokens=1)
    assert stats(budget)['n']==0


@pytest.mark.parametrize('fault',['revoked','expired','anchor_drift','missing_state'])
def test_dynamic_scope_failures_cannot_create_a_fresh_budget(fixture,monkeypatch,fault):
    _,_,task,budget,_,_=fixture
    if fault=='revoked':
        with budget.connect() as c:c.execute('UPDATE budget SET revoked=1 WHERE id=1')
    elif fault=='expired':
        import app.test_text_gate as module
        monkeypatch.setattr(module,'time',types.SimpleNamespace(time=lambda:time.time()+90000))
    elif fault=='anchor_drift':
        budget.anchor_path.chmod(0o600); budget.anchor_path.write_text('{}'); budget.anchor_path.chmod(0o400)
    else: budget.path.unlink()
    with pytest.raises((TestTextGateDenied,FileNotFoundError)):
        budget.reserve(task,b'bad',input_tokens=1,output_tokens=1)


def test_three_images_and_duplicate_item_never_reach_fourth_attempt(fixture):
    _,store,_,budget,gate,_=fixture
    with budget.connect() as c: identity=budget.scope(c)
    a=synthetic_task(store,identity,agent='image-agent'); e=synthetic_task(store,identity,agent='image-agent')
    def wire(prompt):return json.dumps({'prompt':prompt,'model':gate.settings.image_model_id,
        'provider':{'sort':'success_rate'},'size':'1536x1024','n':1,'output_format':'jpeg','response_format':'b64_json'}).encode()
    for task,item in ((a,0),(a,1),(e,0)):
        gate.reserve_image(task['tenant_id'],task['id'],'image-agent',wire(str(item)),item=item)
    with pytest.raises(TestTextGateDenied,match='http_exhausted'):
        gate.reserve_image(e['tenant_id'],e['id'],'image-agent',wire('fourth'),item=1)
    assert stats(budget)['n']==3


def test_replayed_image_item_is_denied_even_with_changed_prompt(fixture):
    _,store,_,budget,_,_=fixture
    with budget.connect() as c: identity=budget.scope(c)
    task=synthetic_task(store,identity,agent='image-agent');budget.allow(task)
    budget.reserve(task,b'first',kind='image',item=0)
    with pytest.raises(TestTextGateDenied,match='paid_replay'):
        budget.reserve(task,b'changed',kind='image',item=0)


@pytest.mark.parametrize('mutation',['previous','opaque','image','output','unknown'])
def test_final_envelope_denied_before_reservation(fixture,mutation):
    _,_,_,budget,gate,_=fixture
    body=json.loads(request())
    if mutation=='previous':body['previous_response_id']='opaque-history'
    elif mutation=='opaque':body['input']=[{'type':'item_reference','id':'unknown'}]
    elif mutation=='image':body['input']=[{'role':'user','content':[{'type':'input_image','image_url':'https://invalid'}]}]
    elif mutation=='output':body['max_output_tokens']=8193
    else:body['unproven_new_wire_field']=True
    with pytest.raises(TestTextGateDenied):gate.bounded_payload(json.dumps(body).encode())
    assert stats(budget)['n']==0


def completed(input_tokens=100,output_tokens=50):
    return {'status':'completed','usage':{'input_tokens':input_tokens,'output_tokens':output_tokens,
        'total_tokens':input_tokens+output_tokens,'output_tokens_details':{'reasoning_tokens':30}}}


def test_sse_and_json_usage_require_complete_unique_terminal_response():
    obj=completed();raw=json.dumps({'type':'response.completed','response':obj}).encode()
    event=b'event: response.completed\ndata: '+raw+b'\n\n'
    assert TestTextGate.confirmed_usage(200,'text/event-stream',event)==(100,50)
    assert TestTextGate.confirmed_usage(200,'application/json',json.dumps(obj).encode())==(100,50)
    for body in (event[:-2],event+event,b'data: {bad}\n\n',event+b'data: {"type":"response.failed"}\n\n'):
        # A valid event without final separator is incomplete, even if JSON parses.
        assert TestTextGate.confirmed_usage(200,'text/event-stream',body) is None
    obj['usage']['total_tokens']=999
    assert TestTextGate.confirmed_usage(200,'application/json',json.dumps(obj).encode()) is None


@pytest.mark.parametrize('outcome',['success','http500','timeout','broken_sse','false_zero_usage'])
def test_actual_loopback_to_mocked_upstream_has_one_attempt_and_exact_hash(fixture,monkeypatch,outcome):
    _,_,task,budget,gate,profile=fixture
    calls=[]
    def handler(req):
        calls.append(req)
        if outcome=='timeout': raise httpx.ReadTimeout('synthetic')
        if outcome=='http500':return httpx.Response(500,json={'error':'synthetic'})
        if outcome=='broken_sse':return httpx.Response(200,headers={'content-type':'text/event-stream'},content=b'data: {')
        if outcome=='false_zero_usage':return httpx.Response(200,json={'usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0}})
        return httpx.Response(200,json=completed())
    real_transport=httpx.AsyncHTTPTransport
    transport=httpx.MockTransport(handler)
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kw:transport)
    async def run():
        url,token=await gate.bind(profile,task['id'])
        # Actual parser/framing/loopback entry; client itself uses a real socket.
        async with httpx.AsyncClient(transport=real_transport(retries=0),trust_env=False) as client:
            response=await client.post(url+'/responses',content=request(),headers={'Authorization':'Bearer '+token})
            assert response.status_code==(200 if outcome in ('success','broken_sse','false_zero_usage') else 500 if outcome=='http500' else 400)
        await gate.close()
    # Do not replace the loopback client's transport with the upstream Mock.
    asyncio.run(run())
    assert len(calls)==1
    with budget.connect() as c:
        attempt=dict(c.execute('SELECT * FROM attempts').fetchone())
    assert attempt['request_sha']==hashlib.sha256(calls[0].content).hexdigest()
    assert str(calls[0].url)=='https://api.deepseek.com/responses'
    assert (attempt['settled_sha'] is not None)==(outcome=='success')


@pytest.mark.parametrize('bad',['reference','retry'])
def test_actual_mcp_rejects_unapproved_paid_paths_before_http(fixture,monkeypatch,bad):
    from app.platform_mcp.service import PlatformMCPService
    s,store,_,_,gate,_=fixture
    service=PlatformMCPService(store,gate.issuer,s)
    async def run():
        with pytest.raises(TestTextGateDenied,match='retry_or_edit_denied'):
            await service.image_generation('not-used','fixture',[],'3:2',
                **({'reference_images':['unapproved']} if bad=='reference' else {'retry_of':'unapproved'}))
    asyncio.run(run())


def test_actual_mcp_sender_records_exact_three_wires_and_stops_before_fourth(fixture,monkeypatch):
    import base64
    import io
    from PIL import Image
    from app.platform_mcp.service import PlatformMCPService
    from app.security import RuntimePrincipal
    s,store,_,budget,gate,_=fixture
    with budget.connect() as c: identity=budget.scope(c)
    image=io.BytesIO();Image.new('RGB',(16,16),'white').save(image,format='JPEG')
    calls=[]
    def handler(req):
        calls.append(req)
        assert str(req.url)=='https://llm-api.net/v1/images/generations'
        assert json.loads(req.content)['n']==1
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(image.getvalue()).decode()}]})
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kw:httpx.MockTransport(handler))
    monkeypatch.setenv(s.image_api_key_env,'ISOLATED_INVALID_IMAGE_KEY')
    service=PlatformMCPService(store,gate.issuer,s)
    token=gate.issuer.issue(RuntimePrincipal('zhiy-e-intelligence','image-agent','isolated-profile',
        ('image:generate',),int(time.time())+300))
    for count in (2,1,1):
        task=synthetic_task(store,identity,agent='image-agent')
        text='生成两张独立图片，1536x1024。' if count==2 else '生成一张图片，1536x1024。'
        with store.connection() as c:c.execute('UPDATE tasks SET input_text=? WHERE id=?',(text,task['id']))
        scope='runtime:'+gate.issuer.issue_task_scope(task['tenant_id'],task['id'])
        if len(calls)<3:
            result=asyncio.run(service.image_generation(token,'isolated image',[],'3:2',size='1536x1024',execution_scope=scope))
            assert len(result['results'])==count
        else:
            with pytest.raises(Exception):
                asyncio.run(service.image_generation(token,'isolated fourth',[],'3:2',size='1536x1024',execution_scope=scope))
    assert len(calls)==3
    with budget.connect() as c:
        recorded=[r[0] for r in c.execute("SELECT request_sha FROM attempts WHERE kind='image' ORDER BY id")]
    assert recorded==[hashlib.sha256(req.content).hexdigest() for req in calls]


def test_real_scope_reader_rejects_fixture_and_never_grants_authority(tmp_path):
    from app.test_text_gate import read_technical_scope
    p=tmp_path/'FIXTURE_NOT_AUTHORITY.json';p.write_text('{"status":"APPROVED"}')
    with pytest.raises((TestTextGateDenied,FileNotFoundError)):
        read_technical_scope(p)
