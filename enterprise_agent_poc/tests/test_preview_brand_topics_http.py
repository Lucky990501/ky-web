"""Actual Preview HTTP/Task persistence with a fake Runtime; no Provider calls."""
import os
from pathlib import Path
import subprocess
import sys

def test_three_agents_ask_before_runtime_and_images_then_resume_same_conversation(tmp_path):
    probe=r'''
import asyncio,json,sqlite3,sys
from pathlib import Path
from uuid import uuid4
import httpx
from scripts.customer_preview import prepare
root=Path(sys.argv[1])/'customer-ai-preview-data';prepare(root,18920,18921)
from app.runtime.codex_provider import CodexRuntimeProvider
from app.domain import RuntimeSession,RuntimeTurn,RuntimeStreamEvent
seen=[];generated=[]
async def create(self,profile,instructions,**options):
 seen.append(instructions);return RuntimeSession('fixture-'+uuid4().hex,profile.id)
async def resume(self,profile,thread,developer_instructions=None,**options):
 seen.append(developer_instructions);return RuntimeSession(thread,profile.id)
async def stream(self,session,message):
 generated.append(message)
 yield RuntimeStreamEvent.completed(RuntimeTurn(session.thread_id,'FIXTURE_ONLY confirmed content'))
CodexRuntimeProvider.create_session=create;CodexRuntimeProvider.resume_session=resume;CodexRuntimeProvider.stream_turn=stream
from scripts.customer_preview import create_preview
app=create_preview(root,credentials={'DEEPSEEK_API_KEY':'FIXTURE_ONLY'},pricing={'text_pricing_verified':True},local_http=True)
from app import main,preview_brand_topics as brand
async def run():
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://preview.fixture') as client:
  me=(await client.get('/preview/api/v1/me')).json();brand.TENANT=me['tenant_id']
  async def submit(agent,message,cid=None):
   body={'message':message}
   if cid:body['conversation_id']=cid
   response=await client.post('/preview/api/v1/agents/'+agent+'/runs',headers={'Origin':'http://preview.fixture'},json=body)
   assert response.status_code==202,response.text
   tid=response.json()['task_id'] if 'task_id' in response.json() else response.json()['id']
   for _ in range(100):
    task=(await client.get('/preview/api/v1/tasks/'+tid)).json()
    if task['status'] not in ['running','queued']:break
    await asyncio.sleep(.02)
   assert task['status']=='completed',task
   return main.product_store.task(tid,me['tenant_id'],me['user_id'])
  results=[]
  for agent in ['copywriting-agent','campaign-agent','preview-wechat-agent']:
   task=await submit(agent,'按易明教育写一份完整内容，原素材要保留')
   assert brand.PREFIX in task['final_response']
   trace=main.store.run_trace(task['run_id'],me['tenant_id'])['payload']
   assert trace['structured_result_status']=='needs_brand_topic' and trace['structured_model_calls']==0
   results.append(task)
  assert generated==[] and all(brand.policy() in s for s in seen)
  with sqlite3.connect(root/'illustrations.sqlite') as c:assert c.execute('select count(*) from illustration_plans').fetchone()[0]==0
  second=await submit('copywriting-agent','2',results[0]['conversation_id'])
  assert len(generated)==1 and second['conversation_id']==results[0]['conversation_id']
  third=await submit('copywriting-agent','第二段改短一点',second['conversation_id'])
  assert len(generated)==2 and third['conversation_id']==second['conversation_id']
  with sqlite3.connect(root/'preview-budget.sqlite') as c:assert c.execute('select count(*) from requests').fetchone()[0]==0
 print('BRAND_TOPIC_HTTP_FIXTURE_PASS provider_calls=0')
asyncio.run(run())
'''
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'-c',probe,str(tmp_path)],cwd=root,
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(root)},stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=90)
    assert result.returncode==0,result.stdout[-4000:]
    assert 'BRAND_TOPIC_HTTP_FIXTURE_PASS' in result.stdout
