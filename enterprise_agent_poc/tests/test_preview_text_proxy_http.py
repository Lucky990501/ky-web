"""Isolated subprocess -> actual Preview ASGI proxy -> MockTransport ONLY."""
import os
from pathlib import Path
import subprocess
import sys


def test_three_agent_preview_proxy_http_fixture(tmp_path):
    app=Path(__file__).resolve().parents[1]
    probe=r'''
import asyncio,json,sys,importlib.metadata
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import httpx
from scripts.customer_preview import create_preview

root=Path(sys.argv[1])/'customer-ai-preview-data'
app=create_preview(root,local_http=True,credentials={'DEEPSEEK_API_KEY':'FIXTURE_ONLY_NOT_A_KEY'},
    pricing={'text_pricing_verified':True,'text_input_cny_per_million':9,'text_output_cny_per_million':27,
             'text_base_url':'https://fixture.invalid'})
from app import main
from app.preview_budget import PreviewBudget
calls=[]
outputs=['FIXTURE_ONLY 请补充招生主题和目标人群。','FIXTURE_ONLY 完整招生口播。',
         'FIXTURE_ONLY 完整活动JSON由独立结构化链路回归验证。','# FIXTURE_ONLY 教育文章\n\n导语和完整正文。']
def supplier(request):
    assert request.url.host=='fixture.invalid'
    value=json.loads(request.content);assert value['max_output_tokens']==8192
    assert value['model']=='deepseek-v4-pro' and value['reasoning']['effort']=='high'
    assert len(request.content)>65536
    assert all(x['name'].startswith('mcp__platform__') for x in value['tools'])
    calls.append(value)
    return httpx.Response(200,json={'id':'fixture-'+str(len(calls)),'object':'response','status':'completed',
        'output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':outputs[len(calls)-1]}]}],
        'usage':{'input_tokens':100,'output_tokens':6000,'total_tokens':6100}})
httpx.AsyncHTTPTransport=lambda **kwargs:httpx.MockTransport(supplier)
async def run():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://127.0.0.1') as client:
        await client.get('/preview/')
        actor=(await client.get('/preview/api/v1/me')).json()
        for agent,rounds in [('copywriting-agent',2),('campaign-agent',1),('preview-wechat-agent',1)]:
            task=str(uuid4())
            with main.store.connection() as conn:
                conn.execute('insert into tasks(id,tenant_id,user_id,agent_id,input_text,status) values(?,?,?,?,?,?)',
                    (task,actor['tenant_id'],actor['user_id'],agent,'FIXTURE_ONLY','running'))
            url,token=await main.manager._test_gate.bind(SimpleNamespace(tenant_id=actor['tenant_id'],agent_id=agent),task)
            history=[{'role':'user','content':'FIXTURE_ONLY 招生主题和人群需求'}]
            for turn in range(rounds):
                payload={'model':'deepseek-v4-pro','reasoning':{'effort':'high'},'instructions':'FIXTURE_ONLY',
                    'input':history+[{'type':'function_call','name':'mcp__platform__enterprise_config_get','call_id':'fixture-call','arguments':'{}'},
                                     {'type':'function_call_output','call_id':'fixture-call','output':'FIXTURE_ONLY '+('x'*70000)}],
                    'tools':[{'type':'function','name':'mcp__platform__enterprise_config_get','parameters':{'type':'object'}}]}
                response=await client.post(url+'/responses',headers={'Authorization':'Bearer '+token},json=payload)
                assert response.status_code==200,(response.status_code,response.text)
                value=response.json();assert value['status']=='completed'
                text=value['output'][0]['content'][0]['text']
                history += [{'role':'assistant','content':text},{'role':'user','content':'FIXTURE_ONLY 主题亲子阅读，面向小学家长。'}]
            with main.store.connection() as conn:conn.execute("update tasks set status='completed' where id=?",(task,))
    assert len(calls)==4 and len(calls[1]['input'])==5
    assert PreviewBudget(root).summary()['text']['requests']==4
    try:version=importlib.metadata.version('openai-codex')
    except importlib.metadata.PackageNotFoundError:version='NOT_INSTALLED_LOCAL_FIXTURE'
    if os.environ.get('REQUIRE_PREVIEW_SDK_VERSION'):assert version=='0.147.0'
    print('FIXTURE_ONLY_PROXY_HTTP_PASS calls=4 actual_provider_calls=0 sdk='+version)
import os
asyncio.run(run())
'''
    env={**os.environ,'PYTHONPATH':str(app)+os.pathsep+os.environ.get('PYTHONPATH',''),'PYTHONDONTWRITEBYTECODE':'1'}
    result=subprocess.run([sys.executable,'-c',probe,str(tmp_path)],cwd=app,env=env,text=True,
                          stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=50)
    assert result.returncode==0,result.stdout[-4000:]
    assert 'FIXTURE_ONLY_PROXY_HTTP_PASS' in result.stdout
