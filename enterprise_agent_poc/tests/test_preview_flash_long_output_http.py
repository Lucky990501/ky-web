"""FIXTURE_ONLY actual Preview loopback HTTP and SDK-shaped SSE; fresh process."""
import asyncio
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from types import SimpleNamespace

import httpx


def test_final_proxy_dispatch_16384_and_controlled_contract_unchanged(tmp_path,monkeypatch):
    from scripts.customer_preview import prepare,create_preview
    from app.preview_budget import PreviewBudget
    from app.preview_text_guard import PreviewTextGuard
    root=tmp_path/'preview'/'customer-ai-preview-data';root.mkdir(parents=True)
    calls=[]
    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            b=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(b)
            response={'type':'response.completed','response':{'status':'completed','model':'deepseek-flash','output':[{'type':'message','status':'completed','content':[{'type':'output_text','text':'FIXTURE_ONLY complete result'}]}],
                      'usage':{'input_tokens':100,'output_tokens':1000,'output_tokens_details':{'reasoning_tokens':120},'total_tokens':1100}}}
            raw=b'data: '+json.dumps(response).encode()+b'\n\n'
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    prepare(root,18956,18957)
    from app.product_service import TaskService
    async def fake_execute(self,task):pass
    monkeypatch.setattr(TaskService,'execute',fake_execute)
    app=create_preview(root,18956,18957,credentials={'DEEPSEEK_API_KEY':'FIXTURE_ONLY_NOT_KEY'},local_http=True,
        pricing={'text_pricing_verified':True,'text_base_url':f'http://127.0.0.1:{server.server_port}',
                 'text_input_cny_per_million':'2','text_output_cny_per_million':'8'})
    from app import main
    from app.activity_plan_runtime import PLAN_SCHEMA
    from app.preview_long_output import apply_policy
    monkeypatch.setattr(main,'settings',replace(main.settings,model_id='deepseek-flash'))
    assert main.settings.database_url=='sqlite:///'+str(root.resolve()/'preview.sqlite')
    tenant='preview-long-output-fixture'
    with main.store.connection() as c:
        c.execute('insert into tenants values(?,?,?)',(tenant,'FIXTURE_ONLY','FIXTURE_ONLY'))
        c.execute('insert into enterprise_configs values(?,?)',(tenant,'{}'))
        c.execute('insert into credit_accounts(tenant_id,balance) values(?,?)',(tenant,100000))
        for agent in ['campaign-agent','preview-wechat-agent']:
            c.execute("insert into tenant_agent_instances(tenant_id,agent_id,status) values(?,?,'enabled')",(tenant,agent))
    actors=[]
    for i in range(2):
        email=f'long-output-{i}@fixture.invalid'
        main.product_store.create_user(tenant,email,'FIXTURE_ONLY','Fixture','enterprise_admin');actors.append(main.product_store.user_by_email(email))
    meter=PreviewBudget(root);grant=root.parent/'illustration-budget-authorization.json'
    scope={'authorization_id':'FIXTURE_LONG_CONTROL','tenant_id':tenant,'user_id':actors[1]['id'],'agent_id':'preview-wechat-agent','max_requests':4}
    grant.write_text(json.dumps({'controlled_text_acceptance':scope}));grant.chmod(0o600)
    guard=PreviewTextGuard(meter,grant);guard.declare(scope)
    gate=main.manager._test_gate
    async def verify():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://preview.fixture') as client:
            for agent,index in [('campaign-agent',0),('preview-wechat-agent',0),('preview-wechat-agent',1)]:
                task=main.product_store.create_task(tenant,actors[index]['id'],agent,'FIXTURE_ONLY complete business content',None)
                with main.store.connection() as c:c.execute("update tasks set status='running' where id=?",(task['id'],))
                await main.task_service.execute(task)
                _,token=await gate.bind(SimpleNamespace(tenant_id=tenant,agent_id=agent),task['id'])
                b={'model':'deepseek-flash','max_output_tokens':4096,'stream':True,'reasoning':{'effort':'high'},
                   'input':[{'role':'user','content':'FIXTURE_ONLY complete business content'}],
                   'tools':[{'type':'function','name':'mcp__platform__enterprise_config_get','parameters':{'type':'object','properties':{}}}]}
                if agent=='campaign-agent':
                    b['tools']=[];b['text']={'format':{'type':'json_schema','name':'ActivityPlanContent','strict':True,'schema':PLAN_SCHEMA}}
                response=await client.post('/_preview_model/tasks/'+task['id']+'/responses',headers={'Authorization':'Bearer '+token},json=b)
                assert response.status_code==200,response.text
                assert 'response.completed' in response.text
                assert calls[-1]['max_output_tokens']==(4096 if index else 16384)
                assert calls[-1]['reasoning']['effort']==('high' if index else 'low')
                gate.validate_completion(task['id'])
            # Same real bound controlled Task: slots 2-4 allowed, fifth denied
            # before any Provider socket call, even after ordinary long requests.
            for n in range(3):
                response=await client.post('/_preview_model/tasks/'+task['id']+'/responses',headers={'Authorization':'Bearer '+token},json=b)
                assert response.status_code==200
            count=len(calls)
            response=await client.post('/_preview_model/tasks/'+task['id']+'/responses',headers={'Authorization':'Bearer '+token},json=b)
            assert response.status_code==429 and len(calls)==count
    try:asyncio.run(verify())
    finally:server.shutdown();server.server_close()
