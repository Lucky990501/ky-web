"""Installed RuntimeProvider -> SDK/CLI -> real MCP -> real Admission.

Only upstream model output and authority are explicit isolated substitutes.
This exercises two simulated Runtime tasks, never paid Provider qualification.
"""
import asyncio,json,os,socket,subprocess,sys,time,shlex
from pathlib import Path
from types import SimpleNamespace
import httpx,pytest
from app.stage2_two_probe_gate import Stage2TextGate,ProbeBudget,PROMPTS,LIMITS
from app.test_text_gate import TestTextGateDenied
from app.security import RuntimeTokenIssuer
from app.store import POCStore
from app.domain import RuntimeProfile
from app.runtime.codex_provider import CodexRuntimeManager,CodexRuntimeProvider
from test_stage2_two_probe_gate import task

def sse(output,number):
    rid='fixture-response-'+str(number)
    base={'id':rid,'object':'response','model':'deepseek-v4-pro','created_at':1,'status':'in_progress','output':[]}
    events=[{'type':'response.created','response':base},
            {'type':'response.output_item.added','output_index':0,'item':{**output,'status':'in_progress'}}]
    if output['type']=='function_call':
        events += [{'type':'response.function_call_arguments.delta','item_id':output['id'],'output_index':0,'delta':output['arguments']},
                   {'type':'response.function_call_arguments.done','item_id':output['id'],'output_index':0,'arguments':output['arguments']}]
    else:
        part=output['content'][0]
        events += [{'type':'response.content_part.added','item_id':output['id'],'output_index':0,'content_index':0,'part':{**part,'text':''}},
                   {'type':'response.output_text.delta','item_id':output['id'],'output_index':0,'content_index':0,'delta':part['text']},
                   {'type':'response.output_text.done','item_id':output['id'],'output_index':0,'content_index':0,'text':part['text']},
                   {'type':'response.content_part.done','item_id':output['id'],'output_index':0,'content_index':0,'part':part}]
    events += [{'type':'response.output_item.done','output_index':0,'item':output},
               {'type':'response.completed','response':{**base,'status':'completed','output':[output],
                'usage':{'input_tokens':100,'output_tokens':20,'total_tokens':120,'output_tokens_details':{'reasoning_tokens':5}}}}]
    return ''.join('event: '+v['type']+'\ndata: '+json.dumps({**v,'sequence_number':i})+'\n\n' for i,v in enumerate(events)).encode()

@pytest.mark.parametrize('case',['complete','overlimit','tool_timeout','mcp_error','mcp_timeout'])
def test_two_runtime_tasks_with_skill_mcp_and_every_http_audited(tmp_path,monkeypatch,case):
    import app.stage2_two_probe_gate as module
    from uuid import uuid4
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    skill=tmp_path/'SKILL.md';skill.write_text('FIXTURE Skill: use enterprise_config_get then finish. No external calls.')
    witness=tmp_path/'mcp-witness.jsonl'
    server=tmp_path/'mcp_fixture.py'
    server.write_text("from mcp.server.fastmcp import FastMCP\nfrom mcp.types import ToolAnnotations\nfrom pathlib import Path\nimport json,time\n"
       +f"m=FastMCP('FixtureOnly',host='127.0.0.1',port={port},stateless_http=True,json_response=True)\n"
       +f"w=Path({str(witness)!r})\n"
       +"@m.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,openWorldHint=False))\ndef read_skill()->str:\n with w.open('a') as f:f.write('read_skill\\n')\n return Path("+repr(str(skill))+").read_text()\n"
       +"@m.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,openWorldHint=False))\ndef enterprise_config_get()->str:\n with w.open('a') as f:f.write('enterprise_config_get\\n')\n"
       +(" raise RuntimeError('EXPLICIT SYNTHETIC TOOL ERROR')\n" if case=='mcp_error' else " time.sleep(2)\n" if case=='mcp_timeout' else '')
       +" return json.dumps({'brand_name':'EXPLICIT SYNTHETIC FIXTURE'})\n"
       +"m.run(transport='streamable-http')\n")
    log=(tmp_path/'mcp.log').open('w');child=subprocess.Popen([sys.executable,'-B',str(server)],stdout=log,stderr=log)
    identity={'kind':'FIXTURE_NOT_AUTHORITY','ledger':str(tmp_path/'budget.sqlite'),'source':'SDK_SIMULATION_NOT_PROVIDER_PASS'}
    budget=ProbeBudget.initialize(Path(identity['ledger']),identity,lambda:identity)
    monkeypatch.setattr(module,'authority',lambda _:identity)
    monkeypatch.setenv('STAGE2_TWO_PROBE_AUTHORITY',str(tmp_path/'FIXTURE_NOT_AUTHORITY'))
    monkeypatch.setenv('STAGE2_DEEPSEEK_TEST_API_KEY','UNIT-INVALID-NEVER-A-REAL-KEY')
    settings=SimpleNamespace(environment='test',test_text_gate_enabled=False,agent_runtime_test_tenant_id='stage25-test-tenant',
       model_provider_id='deepseek',model_id='deepseek-v4-pro',reasoning_effort='high',model_wire_api='responses',
       model_base_url='https://api.deepseek.com/',embedding_provider='local-hash',codex_api_key_env='STAGE2_DEEPSEEK_TEST_API_KEY',
       database_url='sqlite:///'+str(tmp_path/'tasks.sqlite'),data_dir=tmp_path,platform_mcp_url=f'http://127.0.0.1:{port}/mcp')
    tasks=[task(probe) for probe in PROMPTS]
    store=POCStore(settings.database_url)
    with store.connection() as c:
        for sql in ['CREATE TABLE tasks(id TEXT PRIMARY KEY,tenant_id TEXT,user_id TEXT,agent_id TEXT,run_id TEXT,status TEXT,input_text TEXT)',
          'CREATE TABLE run_traces(run_id TEXT,tenant_id TEXT,agent_id TEXT,status TEXT)',
          'CREATE TABLE users(id TEXT,tenant_id TEXT,account_status TEXT,email TEXT)',
          'CREATE TABLE agent_templates(id TEXT,slug TEXT)',
          'CREATE TABLE agent_template_tests(task_id TEXT,test_type TEXT,id TEXT,agent_template_version_id TEXT,configuration_fingerprint TEXT)']:c.execute(sql)
        for t in tasks:
            c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?)',(t['id'],t['tenant_id'],t['user_id'],t['agent_id'],t['run_id'],'running',t['input_text']))
            c.execute('INSERT INTO run_traces VALUES(?,?,?,?)',(t['run_id'],t['tenant_id'],t['agent_id'],'running'))
            c.execute('INSERT INTO users VALUES(?,?,?,?)',(t['user_id'],t['tenant_id'],'enabled','runtime@stage2.test'))
            c.execute('INSERT INTO agent_templates VALUES(?,?)',(t['agent_id'],t['_probe']))
            c.execute('INSERT INTO agent_template_tests VALUES(?,?,?,?,?)',(t['id'],'runtime',t['runtime_test_id'],t['test_revision'],t['test_fingerprint']))
    calls=[];rounds={};current=[]
    def upstream(req):
        payload=json.loads(req.content);calls.append(payload)
        t=current[0];number=rounds.get(t['id'],0)+1;rounds[t['id']]=number
        assert payload['max_output_tokens']==8192 and req.url==httpx.URL('https://api.deepseek.com/responses')
        if case=='tool_timeout' and number==2:raise httpx.ReadTimeout('EXPLICIT SYNTHETIC TIMEOUT')
        if number<=2 or case=='overlimit':
            suffix='exec_command' if number==1 else 'enterprise_config_get'
            name=next(x['name'] for x in payload['tools'] if x.get('name','').endswith(suffix))
            item={'type':'function_call','id':'fc'+str(number),'status':'completed','call_id':str(uuid4()),'name':name,'arguments':json.dumps({'cmd':'cat '+shlex.quote(str(skill)),'max_output_tokens':256}) if number==1 else '{}'}
        else:item={'type':'message','id':'msg'+str(number),'status':'completed','role':'assistant','content':[{'type':'output_text','text':'SIMULATED RUNTIME COMPLETE','annotations':[]}]}
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=sse(item,len(calls)))
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **_:httpx.MockTransport(upstream))
    issuer=RuntimeTokenIssuer('EXPLICIT-FIXTURE-NOT-A-REAL-KEY')
    manager=CodexRuntimeManager(settings,SimpleNamespace(deploy=lambda *_:None),issuer);provider=CodexRuntimeProvider(manager)
    if case=='mcp_timeout':
        prepare=manager._prepare_profile
        def prepare_with_tool_deadline(*args,**kwargs):
            result=prepare(*args,**kwargs)
            with (result[0]/'config.toml').open('a') as f:f.write('\ntool_timeout_sec = 1\n')
            return result
        monkeypatch.setattr(manager,'_prepare_profile',prepare_with_tool_deadline)
    async def run():
        deadline=time.monotonic()+20
        while True:
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.2):break
            except OSError:
                assert child.poll() is None and time.monotonic()<deadline;await asyncio.sleep(.1)
        try:
            for t in tasks:
                current[:]=[t]
                profile=RuntimeProfile.build(tenant_id=t['tenant_id'],agent_id=t['agent_id'],model_provider_id='deepseek',model_id='deepseek-v4-pro',reasoning_effort='high',skill_manifest={})
                workspace=manager._paths(profile)[1];workspace.mkdir(parents=True,exist_ok=True)
                subprocess.run(['git','init','--quiet',str(workspace)],check=True)
                session=await provider.create_session(profile,'EXPLICIT SYNTHETIC: read_skill and enterprise_config_get through MCP.',task_id=t['id'])
                if case=='complete':
                    async with asyncio.timeout(45):result=await provider.run_turn(session,t['input_text'])
                    assert result.status=='completed' and 'SIMULATED RUNTIME COMPLETE' in result.text
                    with store.connection() as c:
                        c.execute("UPDATE tasks SET status='completed' WHERE id=?",(t['id'],))
                        c.execute("UPDATE run_traces SET status='completed' WHERE run_id=?",(t['run_id'],))
                else:
                    with pytest.raises(Exception):
                        async with asyncio.timeout(45):await provider.run_turn(session,t['input_text'])
                    break
        finally:await manager.close()
    try:asyncio.run(run())
    finally:
        child.terminate();child.wait(timeout=10);log.close()
    with budget.connect() as c:
        rows=[dict(x) for x in c.execute('SELECT * FROM attempts')]
        denials=[dict(x) for x in c.execute('SELECT * FROM denials')]
    assert len(rows)==len(calls)==(6 if case=='complete' else 4 if case=='overlimit' else 2)
    assert sum(x['input_reserved'] for x in rows)<=LIMITS['input_total']
    assert sum(x['cost_reserved'] for x in rows)<=LIMITS['cost_micro_cny']
    if case!='tool_timeout':assert witness.exists(), 'MCP did not execute'
    events=witness.read_text().splitlines() if witness.exists() else []
    outputs=[x.get('output','') for p in calls for x in p['input'] if x.get('type')=='function_call_output']
    assert any('FIXTURE Skill:' in str(x) for x in outputs),json.dumps(outputs)
    assert events.count('enterprise_config_get')>=(2 if case=='complete' else 0)
    if case=='complete':assert all(x['state']=='COMPLETE' for x in rows) and len({x['task_id'] for x in rows})==2
    else:
        with pytest.raises(TestTextGateDenied):budget.reserve(tasks[0],b'AFTER-FAILURE',input_tokens=5000,output_tokens=8192)
        with pytest.raises(TestTextGateDenied):manager._test_gate.task(tasks[1]['tenant_id'],tasks[1]['id'],tasks[1]['agent_id'])
    trace={'kind':'REAL_SDK_MCP_WITH_EXPLICIT_SIMULATED_UPSTREAM_NOT_PAID_STAGE2','case':case,
        'runtime_tasks_completed':2 if case=='complete' else 0,'http_sends':len(rows),
        'mcp_calls':events,'skill_read_via_real_sdk_sandbox':True,
        'input_reserved':sum(x['input_reserved'] for x in rows),'output_reserved':sum(x['output_reserved'] for x in rows),
        'cost_reserved_micro_cny':sum(x['cost_reserved'] for x in rows),'attempts':rows,'denials':denials,'real_provider_calls':0}
    (tmp_path/'MULTITURN_AUDIT.json').write_text(json.dumps(trace,indent=2))
