"""FIXTURE_ONLY real loopback HTTP, actual Preview gate, no external Provider.

Run in a fresh process: application settings and client metering are singletons.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import uuid4

import httpx

def test_actual_preview_http_four_dispatches_no_fifth_and_config_failure(tmp_path,monkeypatch):
    from scripts.customer_preview import prepare,create_preview
    from app.preview_text_guard import PreviewTextGuard
    from app.preview_budget import PreviewBudget
    root=tmp_path/'fixture-preview'/'customer-ai-preview-data';root.mkdir(parents=True)
    calls=[];mutex=threading.Lock()
    class FixtureProvider(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path=='/responses'
            assert self.headers.get('Authorization')=='Bearer FIXTURE_ONLY_NOT_A_KEY'
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            with mutex:calls.append({'max_output_tokens':body['max_output_tokens'],'tool_names':[t.get('name') for t in body['tools']]})
            response=json.dumps({'id':'FIXTURE_ONLY_RESPONSE','object':'response','status':'completed','output':[]}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),FixtureProvider)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    prepare(root,18956,18957)
    from app.product_service import TaskService
    from app.runtime.codex_provider import CodexRuntimeProvider
    bound_tasks=[]
    async def fixture_session(self,*args,**kwargs):
        bound_tasks.append(kwargs.get('task_id'));return 'FIXTURE_ONLY_SESSION'
    async def fixture_execute(self,task):
        from app import main
        await main.runtime.create_session('FIXTURE_ONLY_PROFILE','FIXTURE_ONLY instructions')
        await main.runtime.resume_session('FIXTURE_ONLY_PROFILE','FIXTURE_ONLY thread')
    monkeypatch.setattr(TaskService,'execute',fixture_execute)
    monkeypatch.setattr(CodexRuntimeProvider,'create_session',fixture_session)
    monkeypatch.setattr(CodexRuntimeProvider,'resume_session',fixture_session)
    app=create_preview(root,18956,18957,credentials={'DEEPSEEK_API_KEY':'FIXTURE_ONLY_NOT_A_KEY'},local_http=True,
        pricing={'text_pricing_verified':True,'text_base_url':f'http://127.0.0.1:{server.server_port}',
                 'text_input_cny_per_million':'2','text_output_cny_per_million':'20'})
    from app import main
    assert main.settings.database_url=='sqlite:///'+str(root.resolve()/'preview.sqlite')
    tenant='preview-fixture-http';user=str(uuid4());ordinary=str(uuid4())
    with main.store.connection() as c:
        c.execute('insert into tenants values(?,?,?)',(tenant,'FIXTURE_ONLY','FIXTURE_ONLY_NOT_A_SECRET'))
        c.execute('insert into enterprise_configs values(?,?)',(tenant,'{}'))
        c.execute("insert into tenant_agent_instances(tenant_id,agent_id,status) values(?,?,'enabled')",(tenant,'preview-wechat-agent'))
        c.execute('insert into credit_accounts(tenant_id,balance) values(?,?)',(tenant,100))
    main.product_store.create_user(tenant,user+'@fixture.invalid','FIXTURE_ONLY_NOT_HASH','Fixture','enterprise_admin')
    actor=main.product_store.user_by_email(user+'@fixture.invalid');user=actor['id']
    main.product_store.create_user(tenant,ordinary+'@fixture.invalid','FIXTURE_ONLY_NOT_HASH','Other','enterprise_admin')
    other=main.product_store.user_by_email(ordinary+'@fixture.invalid')
    task=main.product_store.create_task(tenant,user,'preview-wechat-agent','FIXTURE_ONLY',None)
    scope={'authorization_id':'FIXTURE_HTTP_FOUR','tenant_id':tenant,'user_id':user,'agent_id':'preview-wechat-agent'}
    grant=root.parent/'illustration-budget-authorization.json';grant.write_text(json.dumps({'controlled_text_acceptance':{**scope,'max_requests':4}}));grant.chmod(0o600)
    guard=PreviewTextGuard(PreviewBudget(root),grant);guard.declare(scope)
    gate=main.manager._test_gate;profile=SimpleNamespace(tenant_id=tenant,agent_id='preview-wechat-agent')
    async def verify():
        await main.task_service.execute(task)
        assert bound_tasks==[task['id'],task['id']]
        url,token=await gate.bind(profile,task['id']);route='/_preview_model/tasks/'+task['id']+'/responses'
        body={'model':main.settings.model_id,'input':[{'role':'user','content':'FIXTURE_ONLY article'}],
              'tools':[{'type':'function','name':'mcp__platform__'+name,'parameters':{'type':'object','properties':{}}} for name in ('enterprise_config_get','knowledge_search','asset_search')]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture.local') as client:
            statuses=await asyncio.gather(*(client.post(route,headers={'Authorization':'Bearer '+token},json=body) for _ in range(8)))
            assert sorted(r.status_code for r in statuses)==[200]*4+[429]*4
            assert len(calls)==4 and all(r['tool_names']==['mcp__platform__enterprise_config_get'] for r in calls)
            grant.write_text('{')
            assert (await client.post(route,headers={'Authorization':'Bearer '+token},json=body)).status_code==429
            assert (await client.post('/_preview_model/responses',headers={'Authorization':'Bearer '+token},json=body)).status_code==403
            assert len(calls)==4
            ordinary_task=main.product_store.create_task(tenant,other['id'],'preview-wechat-agent','FIXTURE_ONLY other user',None)
            _,ordinary_token=await gate.bind(profile,ordinary_task['id'])
            r=await client.post('/_preview_model/tasks/'+ordinary_task['id']+'/responses',headers={'Authorization':'Bearer '+ordinary_token},json=body)
            assert r.status_code==200 and len(calls)==5
            assert 'mcp__platform__knowledge_search' in calls[-1]['tool_names']
            assert (await client.post(route,headers={'Authorization':'Bearer '+ordinary_token},json=body)).status_code==429
            assert len(calls)==5
            # Revoke after a reservation but BEFORE HTTP: no additional socket IO.
            scope['authorization_id']='FIXTURE_HTTP_REVOKE_BEFORE_IO'
            guard.declare(scope);grant.write_text(json.dumps({'controlled_text_acceptance':{**scope,'max_requests':4}}))
            new_task=main.product_store.create_task(tenant,user,'preview-wechat-agent','FIXTURE_ONLY revoke',None)
            _,new_token=await gate.bind(profile,new_task['id'])
            original_reserve=gate.guard.reserve
            def revoke_after_reserve(task,cost):
                result=original_reserve(task,cost)
                grant.write_text(json.dumps({'controlled_text_acceptance':{**scope,'max_requests':0}}))
                return result
            monkeypatch.setattr(gate.guard,'reserve',revoke_after_reserve)
            r=await client.post('/_preview_model/tasks/'+new_task['id']+'/responses',headers={'Authorization':'Bearer '+new_token},json=body)
            assert r.status_code==429 and len(calls)==5
        assert guard.budget.summary()['text']['requests']==6  # refused after reserve is retained, not refunded
        with main.store.connection() as c:c.execute("update tasks set status='failed' where id=?",(task['id'],))
        if os.environ.get('PREVIEW_VERIFY_SDK_BOOTSTRAP')=='1':
            # Actual installed SDK/App Server, no turn/model request. The
            # systemd verifier denies every non-loopback network destination.
            scope['authorization_id']='FIXTURE_ONLY_SDK_START_NO_TURN';guard.declare(scope)
            grant.write_text(json.dumps({'controlled_text_acceptance':{**scope,'max_requests':4}}))
            sdk_task=main.product_store.create_task(tenant,user,'preview-wechat-agent','FIXTURE_ONLY SDK bootstrap',None)
            sdk_profile=main.agents.profile_for(tenant,'preview-wechat-agent')
            await main.manager.get(sdk_profile,task_id=sdk_task['id'])
            home,_=main.manager._paths(sdk_profile)
            config=(home/('test-task-'+sdk_task['id'])/'config.toml').read_text()
            assert '/_preview_model/tasks/'+sdk_task['id'] in config
            assert len(calls)==5 and guard.budget.summary()['text']['requests']==6
            await main.manager.close()
    try:asyncio.run(verify())
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
