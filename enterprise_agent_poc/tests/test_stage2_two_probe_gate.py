"""Zero-paid boundary tests; mocked upstream is never formal Provider PASS."""
import asyncio
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.stage2_two_probe_gate import ProbeBudget, Stage2TextGate, LIMITS, PROMPTS, PROFILE
from app.test_text_gate import TestTextGateDenied
from app.security import RuntimeTokenIssuer
from app.domain import RuntimeProfile


def task(probe='social-content-agent'):
    return dict(id=str(uuid4()),run_id=str(uuid4()),tenant_id='stage25-test-tenant',
                agent_id=str(uuid4()),user_id=str(uuid4()),runtime_test_id=str(uuid4()),
                test_revision=str(uuid4()),test_fingerprint='a'*64,_probe=probe,
                input_text=PROMPTS.get(probe,''))


@pytest.fixture
def budget(tmp_path):
    # Explicit isolated authority substitute. No real Owner archive or grant.
    identity={'kind':'FIXTURE_NOT_AUTHORITY','operation':str(uuid4()),'source':'isolated-zero-paid',
              'ledger':str(tmp_path/'budget.sqlite')}
    return ProbeBudget.initialize(tmp_path/'budget.sqlite',identity,lambda:identity)


def count(budget):
    with budget.connect() as c:
        return c.execute('SELECT count(*) FROM attempts').fetchone()[0]


def reserve(budget,t=None,**kw):
    return budget.reserve(t or task(),b'fixture-wire',input_tokens=kw.get('i',5000),output_tokens=kw.get('o',8192))


def complete(budget,n):
    budget.finish(n,'b'*64,(100,50))


def test_two_probes_only_and_reservations_not_refunded(budget):
    a=reserve(budget);complete(budget,a)
    b=reserve(budget,task('wechat-official-account-writing'));complete(budget,b)
    with pytest.raises(TestTextGateDenied):reserve(budget,task('third'))
    assert count(budget)==2
    with budget.connect() as c:
        assert c.execute('SELECT sum(output_reserved) FROM attempts').fetchone()[0]==16384


@pytest.mark.parametrize('i,o',[(65537,8192),(5000,8193),(0,8192),(True,8192),(5000,0)])
def test_per_request_limit_before_consumption(budget,i,o):
    with pytest.raises(TestTextGateDenied):reserve(budget,i=i,o=o)
    assert count(budget)==0


def test_cost_exhausted_before_send(budget,monkeypatch):
    monkeypatch.setitem(LIMITS,'cost_micro_cny',1)
    with pytest.raises(TestTextGateDenied,match='cost_exhausted'):reserve(budget)
    assert count(budget)==0


@pytest.mark.parametrize('limit',['input_total','output_total'])
def test_cumulative_limits_before_send(budget,monkeypatch,limit):
    a=reserve(budget);complete(budget,a)
    monkeypatch.setitem(LIMITS,limit,5000 if limit=='input_total' else 8192)
    with pytest.raises(TestTextGateDenied,match='cumulative_tokens'):
        reserve(budget,task('wechat-official-account-writing'))
    assert count(budget)==1


def test_identical_wire_replay_and_different_task_for_same_probe_rejected(budget):
    t=task();n=reserve(budget,t);complete(budget,n)
    with pytest.raises(TestTextGateDenied):reserve(budget,t)
    with pytest.raises(TestTextGateDenied):reserve(budget,task())
    assert count(budget)==1


def test_four_distinct_turns_per_probe_eight_total_worst_case_reserved(budget):
    for probe in PROMPTS:
        t=task(probe)
        for turn in range(4):
            n=budget.reserve(t,('turn'+str(turn)).encode(),input_tokens=65536,output_tokens=8192)
            complete(budget,n)
        with pytest.raises(TestTextGateDenied,match='http_limit'):
            budget.reserve(t,b'fifth-turn',input_tokens=5000,output_tokens=8192)
    with budget.connect() as c:
        row=c.execute('SELECT count(*),sum(input_reserved),sum(output_reserved),sum(cost_reserved) FROM attempts').fetchone()
    assert tuple(row)==(8,524288,65536,6488064)


@pytest.mark.parametrize('field',['run_id','test_revision','test_fingerprint'])
def test_multiturn_identity_conflict_before_send(budget,field):
    t=task();n=reserve(budget,t);complete(budget,n)
    changed={**t,field:'conflict'}
    with pytest.raises(TestTextGateDenied,match='binding_conflict'):
        budget.reserve(changed,b'new-turn',input_tokens=5000,output_tokens=8192)
    assert count(budget)==1


def test_concurrent_process_reentry_has_one_inflight_send(budget):
    def attempt(_):
        try:return reserve(budget)
        except TestTextGateDenied:return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        values=list(pool.map(attempt,range(6)))
    assert sum(x is not None for x in values)==1 and count(budget)==1
    again=ProbeBudget(budget.path,budget.identity,budget.validator)
    with pytest.raises(TestTextGateDenied):reserve(again,task('wechat-official-account-writing'))


def test_initialization_and_missing_state_cannot_reset_budget(budget):
    with pytest.raises(FileExistsError):
        ProbeBudget.initialize(budget.path,budget.identity,budget.validator)
    budget.path.unlink()  # disposable named unit fixture only
    with pytest.raises(FileExistsError):
        ProbeBudget.initialize(budget.path,budget.identity,budget.validator)
    with pytest.raises(FileNotFoundError):reserve(budget)


@pytest.mark.parametrize('usage',[None,(0,50),(999999,50),(100,999999)])
def test_missing_or_excess_response_stops_all_subsequent_requests(budget,usage):
    n=reserve(budget)
    with pytest.raises(TestTextGateDenied):budget.finish(n,'b'*64,usage)
    with pytest.raises(TestTextGateDenied):reserve(budget,task('wechat-official-account-writing'))
    assert count(budget)==1


def request(**updates):
    payload={'model':'deepseek-v4-pro','reasoning':{'effort':'high'},'input':'Synthetic private probe'}
    payload.update(updates);return json.dumps(payload).encode()


def gate_for(budget,t):
    # Real parser, signed loopback context and sole outbound forward() below.
    # Only real authority and Task DB fixture are substituted in this unit lane.
    gate=Stage2TextGate.__new__(Stage2TextGate)
    gate.settings=SimpleNamespace(codex_api_key_env='STAGE2_DEEPSEEK_TEST_API_KEY')
    gate.issuer=RuntimeTokenIssuer('UNIT-FIXTURE-NOT-A-REAL-CREDENTIAL')
    gate.budget=budget;gate.server=None;gate.contexts={}
    gate.task=lambda *a,**kw:t
    return gate


@pytest.mark.parametrize('outcome',['ok','timeout','cancel','exception','http500','truncated','overuse'])
def test_actual_loopback_entry_to_outbound_adapter_stops_on_unknown(budget,monkeypatch,outcome):
    t=task();g=gate_for(budget,t);calls=[];transport_options=[]
    monkeypatch.setenv('STAGE2_DEEPSEEK_TEST_API_KEY','UNIT-INVALID-NOT-A-PROVIDER-KEY')
    def upstream(req):
        calls.append(req)
        assert str(req.url)=='https://api.deepseek.com/responses'
        assert json.loads(req.content)['max_output_tokens']==8192
        if outcome=='timeout':raise httpx.ReadTimeout('UNIT')
        if outcome=='cancel':raise asyncio.CancelledError()
        if outcome=='exception':raise RuntimeError('UNIT')
        if outcome=='http500':return httpx.Response(500,json={'error':'UNIT'})
        if outcome=='truncated':return httpx.Response(200,headers={'content-type':'text/event-stream'},content=b'data: {')
        n=999999 if outcome=='overuse' else 50
        return httpx.Response(200,json={'status':'completed','usage':{'input_tokens':100,'output_tokens':n,
                      'total_tokens':100+n,'output_tokens_details':{'reasoning_tokens':10}}})
    actual_transport=httpx.AsyncHTTPTransport
    def transport(**kw):
        transport_options.append(kw);return httpx.MockTransport(upstream)
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',transport)
    profile=RuntimeProfile.build(tenant_id=t['tenant_id'],agent_id=t['agent_id'],
             model_provider_id='deepseek',model_id='deepseek-v4-pro',reasoning_effort='high',skill_manifest={})
    async def run():
        url,token=await g.bind(profile,t['id'])
        try:
            async with httpx.AsyncClient(transport=actual_transport(retries=0),trust_env=False) as client:
                r=await client.post(url+'/responses',content=request(),headers={'Authorization':'Bearer '+token})
                assert r.status_code==(200 if outcome=='ok' else 400)
            # SDK retry/reentry never results in another outbound send.
            with pytest.raises((TestTextGateDenied,Exception)):
                await g.forward(token,request())
        finally:await g.close()
    asyncio.run(run())
    assert len(calls)==1 and count(budget)==1 and transport_options==[{'retries':0}]


@pytest.mark.parametrize('change',[{'model':'other'},{'max_output_tokens':8193},
    {'input':'x'*65536},{'previous_response_id':'opaque'},
    {'tools':[{'type':'web_search'}]}])
def test_invalid_input_rejected_before_real_outbound_method(budget,monkeypatch,change):
    g=gate_for(budget,task());g.contexts['fixture']=('stage25-test-tenant',str(uuid4()),str(uuid4()),str(uuid4()),str(uuid4()))
    from app.security import RuntimePrincipal
    ctx=g.contexts['fixture'];import time
    token=g.issuer.issue(RuntimePrincipal(ctx[0],ctx[2],'fixture',('test-text:request',),int(time.time())+60))
    def forbidden(**kw):raise AssertionError('OUTBOUND_TRANSPORT_MUST_NOT_BE_CREATED')
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',forbidden)
    monkeypatch.setenv('STAGE2_DEEPSEEK_TEST_API_KEY','UNIT-INVALID')
    with pytest.raises(TestTextGateDenied):asyncio.run(g.forward(token,request(**change)))
    assert count(budget)==0


def test_ledger_audit_has_hash_and_counts_but_no_secret_or_prompt(budget):
    n=reserve(budget);complete(budget,n)
    with budget.connect() as c:
        row=dict(c.execute('SELECT * FROM attempts').fetchone())
    assert row['request_sha']==hashlib.sha256(b'fixture-wire').hexdigest()
    assert row['input_reserved']==5000 and row['cost_reserved']==5000*9+8192*27
    assert 'fixture-wire' not in json.dumps(row) and 'Authorization' not in json.dumps(row)


def test_actual_runtime_constructor_and_sdk_config_connect_gate(tmp_path,monkeypatch):
    import tomllib
    import app.stage2_two_probe_gate as module
    from app.runtime.codex_provider import CodexRuntimeManager
    identity={'kind':'FIXTURE_NOT_AUTHORITY','ledger':str(tmp_path/'budget.sqlite')}
    monkeypatch.setattr(module,'authority',lambda path:identity)
    monkeypatch.setenv('STAGE2_TWO_PROBE_AUTHORITY',str(tmp_path/'FIXTURE_ONLY'))
    settings=SimpleNamespace(environment='test',test_text_gate_enabled=False,
        agent_runtime_test_tenant_id='stage25-test-tenant',model_provider_id='deepseek',
        model_id='deepseek-v4-pro',reasoning_effort='high',model_wire_api='responses',
        model_base_url='https://api.deepseek.com/',embedding_provider='local-hash',
        database_url='sqlite:///'+str(tmp_path/'private.db'),data_dir=tmp_path,
        codex_api_key_env='STAGE2_DEEPSEEK_TEST_API_KEY',platform_mcp_url='http://127.0.0.1:9/mcp')
    manager=CodexRuntimeManager(settings,SimpleNamespace(deploy=lambda *a:None),
                               RuntimeTokenIssuer('UNIT-NOT-A-REAL-KEY'))
    assert isinstance(manager._test_gate,Stage2TextGate)
    profile=RuntimeProfile.build(tenant_id='stage25-test-tenant',agent_id=str(uuid4()),
        model_provider_id='deepseek',model_id='deepseek-v4-pro',reasoning_effort='high',skill_manifest={})
    home,_,_,_=manager._prepare_profile(profile,task_id=str(uuid4()),gate_url='http://127.0.0.1:12345')
    whole=tomllib.loads((home/'config.toml').read_text())
    assert whole['web_search']=='disabled'
    config=whole['model_providers']['deepseek']
    assert config['base_url']=='http://127.0.0.1:12345'
    assert config['request_max_retries']==config['stream_max_retries']==0
    assert config['env_key']=='STAGE2_DEEPSEEK_TEST_API_KEY'


def test_formal_cli_rejects_extra_tests_before_provision_or_upstream(monkeypatch):
    import sys
    from scripts import release_test_environment as entry
    monkeypatch.setattr(sys,'argv',['entry','run','--credential-file','NOT-A-KEY',
        '--admission-directory','FIXTURE-ONLY','--','tests/test_extra_paid_task.py'])
    monkeypatch.setattr(entry,'IsolatedServices',lambda *a:pytest.fail('NO_FIXTURE_OR_PROVIDER_START'))
    with pytest.raises(RuntimeError,match='Only the two approved'):
        entry.main()


@pytest.mark.parametrize('failure',['http500','truncated','timeout'])
def test_installed_sdk_cli_hits_this_gate_once_no_hidden_retries(budget,tmp_path,monkeypatch,failure):
    from openai_codex import AsyncCodex, CodexConfig
    from app.runtime.codex_provider import CodexRuntimeManager
    import app.stage2_two_probe_gate as module
    t=task();gate=gate_for(budget,t);calls=[];denials=[]
    real_forward=gate.forward
    async def recorded_forward(*a):
        try:return await real_forward(*a)
        except TestTextGateDenied as exc:
            denials.append(str(exc));raise
    gate.forward=recorded_forward
    def upstream(req):
        calls.append(req)
        if failure=='http500':return httpx.Response(500,json={'error':{'message':'UNIT-ISOLATED'}})
        if failure=='timeout':raise httpx.ReadTimeout('UNIT-ISOLATED')
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=b'data: {')
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kw:httpx.MockTransport(upstream))
    monkeypatch.setattr(module,'authority',lambda path:budget.identity)
    monkeypatch.setenv('STAGE2_TWO_PROBE_AUTHORITY',str(tmp_path/'FIXTURE-NOT-AUTHORITY'))
    settings=SimpleNamespace(environment='test',test_text_gate_enabled=False,
        agent_runtime_test_tenant_id='stage25-test-tenant',model_provider_id='deepseek',
        model_id='deepseek-v4-pro',reasoning_effort='high',model_wire_api='responses',
        model_base_url='https://api.deepseek.com/',embedding_provider='local-hash',
        database_url='sqlite:///'+str(tmp_path/'private.db'),data_dir=tmp_path,
        codex_api_key_env='STAGE2_DEEPSEEK_TEST_API_KEY',platform_mcp_url='http://127.0.0.1:9/mcp')
    monkeypatch.setenv(settings.codex_api_key_env,'UNIT-NOT-A-REAL-KEY')
    manager=CodexRuntimeManager(settings,SimpleNamespace(deploy=lambda *a:None),gate.issuer)
    profile=RuntimeProfile.build(tenant_id=t['tenant_id'],agent_id=t['agent_id'],
        model_provider_id='deepseek',model_id='deepseek-v4-pro',reasoning_effort='high',skill_manifest={})
    async def run():
        url,credential=await gate.bind(profile,t['id'])
        home,workspace,_,_=manager._prepare_profile(profile,task_id=t['id'],gate_url=url)
        # Only MCP is omitted in this installed SDK retry diagnostic.
        # No production Task/Native acceptance is inferred from it.
        config=(home/'config.toml').read_text().split('[mcp_servers.platform]')[0]
        (home/'config.toml').write_text(config)
        env={k:'' for k in os.environ if any(x in k.upper() for x in ('KEY','TOKEN','SECRET','PASSWORD','PROXY'))}
        env.update(CODEX_HOME=str(home),PYTHONDONTWRITEBYTECODE='1')
        env[settings.codex_api_key_env]=credential
        try:
            async with AsyncCodex(CodexConfig(cwd=str(workspace),env=env)) as codex:
                thread=await codex.thread_start(base_instructions='Synthetic Admission diagnostic.',ephemeral=True)
                turn=await thread.turn('Reply OK. Do not execute tools.')
                with pytest.raises(RuntimeError):
                    async with asyncio.timeout(30):await turn.run()
        finally:await gate.close();await manager.close()
    asyncio.run(run())
    assert len(calls)==1 and count(budget)==1, {'denials':denials,'reserved':count(budget)}
    assert json.loads(calls[0].content)['max_output_tokens']==8192


@pytest.mark.parametrize('mutation',['valid','tenant','actor','input','missing_runtime','failed_previous'])
def test_actual_task_sql_scope_and_previous_task_guard(budget,tmp_path,mutation):
    from app.store import POCStore
    store=POCStore('sqlite:///'+str(tmp_path/'task-boundary.sqlite'))
    one,two=task(),task('wechat-official-account-writing')
    with store.connection() as c:
        for sql in [
            'CREATE TABLE tasks(id TEXT PRIMARY KEY,tenant_id TEXT,user_id TEXT,agent_id TEXT,run_id TEXT,status TEXT,input_text TEXT)',
            'CREATE TABLE run_traces(run_id TEXT,tenant_id TEXT,agent_id TEXT,status TEXT)',
            'CREATE TABLE users(id TEXT,tenant_id TEXT,account_status TEXT,email TEXT)',
            'CREATE TABLE agent_templates(id TEXT,slug TEXT)',
            'CREATE TABLE agent_template_tests(task_id TEXT,test_type TEXT,id TEXT,agent_template_version_id TEXT,configuration_fingerprint TEXT)']:
            c.execute(sql)
        for t in (one,two):
            c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?)',(t['id'],t['tenant_id'],t['user_id'],t['agent_id'],t['run_id'],'running',t['input_text']))
            c.execute('INSERT INTO run_traces VALUES(?,?,?,?)',(t['run_id'],t['tenant_id'],t['agent_id'],'running'))
            c.execute('INSERT INTO users VALUES(?,?,?,?)',(t['user_id'],t['tenant_id'],'enabled','runtime@stage2.test'))
            c.execute('INSERT INTO agent_templates VALUES(?,?)',(t['agent_id'],t['_probe']))
            c.execute('INSERT INTO agent_template_tests VALUES(?,?,?,?,?)',(t['id'],'runtime',t['runtime_test_id'],t['test_revision'],t['test_fingerprint']))
        if mutation=='tenant':c.execute("UPDATE run_traces SET tenant_id='other'")
        if mutation=='actor':c.execute("UPDATE users SET email='not-approved@example.invalid'")
        if mutation=='input':c.execute("UPDATE tasks SET input_text='not-approved'")
        if mutation=='missing_runtime':c.execute('DELETE FROM agent_template_tests')
    g=Stage2TextGate.__new__(Stage2TextGate);g.budget=budget;g.store=store
    chosen=one
    if mutation=='failed_previous':
        n=reserve(budget,one);complete(budget,n)
        with store.connection() as c:c.execute("UPDATE tasks SET status='failed' WHERE id=?",(one['id'],))
        chosen=two
    if mutation=='valid':
        assert g.task(one['tenant_id'],one['id'],one['agent_id'])['_probe']==one['_probe']
    else:
        with pytest.raises(TestTextGateDenied):g.task(chosen['tenant_id'],chosen['id'],chosen['agent_id'])


def test_actual_authority_reader_rejects_unprotected_fixture_before_budget(tmp_path):
    from app.stage2_two_probe_gate import authority
    (tmp_path/'request.json').write_text('{}');(tmp_path/'owner.json').write_text('{}')
    with pytest.raises(TestTextGateDenied):authority(tmp_path)
    assert not (tmp_path/'stage2-two-probe.sqlite').exists()


def test_two_actual_loopback_probe_requests_then_third_send_is_blocked(budget,monkeypatch):
    calls=[]
    def upstream(req):
        calls.append(req)
        return httpx.Response(200,json={'status':'completed','usage':{'input_tokens':100,'output_tokens':50,
            'total_tokens':150,'output_tokens_details':{'reasoning_tokens':10}}})
    actual_transport=httpx.AsyncHTTPTransport
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kw:httpx.MockTransport(upstream))
    monkeypatch.setenv('STAGE2_DEEPSEEK_TEST_API_KEY','UNIT-INVALID-NOT-A-KEY')
    async def run():
        for index,probe in enumerate(('social-content-agent','wechat-official-account-writing','extra')):
            t=task(probe);g=gate_for(budget,t)
            profile=RuntimeProfile.build(tenant_id=t['tenant_id'],agent_id=t['agent_id'],
                model_provider_id='deepseek',model_id='deepseek-v4-pro',reasoning_effort='high',skill_manifest={})
            url,token=await g.bind(profile,t['id'])
            try:
                async with httpx.AsyncClient(transport=actual_transport(retries=0),trust_env=False) as client:
                    response=await client.post(url+'/responses',content=request(),headers={'Authorization':'Bearer '+token})
                    assert response.status_code==(200 if index<2 else 400)
            finally:await g.close()
    asyncio.run(run())
    assert len(calls)==count(budget)==2
