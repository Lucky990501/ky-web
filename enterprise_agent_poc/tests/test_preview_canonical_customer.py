"""FIXTURE_ONLY: reuse an existing tenant without migrating customer history."""
import os
from pathlib import Path
import subprocess
import sys
import pytest


@pytest.mark.parametrize('pending',[0,1])
def test_binding_existing_customer_preserves_old_data_and_independent_credentials(tmp_path,pending):
    probe=r'''
import asyncio,json,sys
from pathlib import Path
from types import SimpleNamespace
import httpx
from scripts.customer_preview import create_preview
from app.preview_illustration_provider import IllustrationConfig
from app.auth import hash_password,UserPrincipal
from app.preview_customer_auth import provision,bind_existing_customer,COOKIE
from app.preview_credit_target import CUSTOMER_CANONICAL_OPERATION
root=Path(sys.argv[1])/'customer-ai-preview-data';pending=int(sys.argv[2]);tenant='preview-canonical-fixture';owner='fixture-owner'
auth={'approved':True,'authorization_id':'FIXTURE_CANONICAL','pricing_verified':True,'model':'qwen-image-3.0','request_max_cny':'0.20','estimated_request_cny':'0.20','max_total_cny':'200','max_requests':1000,'max_images_per_task':10,'automatic_paid_retries':0,'automatic_after_text':True,'allowed_tenant_ids':[tenant],'task_scoped_tenant_ids':[],
'tenant_task_restrictions':[{'tenant_id':tenant,'user_id':owner,'task_id':'obsolete-fixture','max_images':3,'max_reserved_cny':'0.60'}]}
app=create_preview(root,credentials={'DEEPSEEK_API_KEY':'FIXTURE_ONLY'},pricing={'text_pricing_verified':True},local_http=True,
 illustration_config=IllustrationConfig(key='FIXTURE_ONLY',enabled=True,base_url='https://llm-api.net'),illustration_authorization=auth)
from app import main
with main.store.connection() as c:
 c.execute('insert into tenants values(?,?,?)',(tenant,'FIXTURE existing company','FIXTURE_OWNER'))
 c.execute('insert into enterprise_configs values(?,?)',(tenant,'{"brand_name":"FIXTURE shared brand","rules":"FIXTURE confirmed rules"}'))
 c.execute("insert into users(id,tenant_id,email,password_hash,display_name,role) values(?,?,?,?,?,'enterprise_admin')",(owner,tenant,'owner@fixture.invalid',hash_password('FIXTURE_OWNER_PASSWORD'),'Owner'))
 c.execute('insert into credit_accounts(tenant_id,balance) values(?,99850)',(tenant,))
 for a in ['copywriting-agent','campaign-agent','preview-wechat-agent','image-agent']:c.execute("insert into tenant_agent_instances(tenant_id,agent_id,status) values(?,?,'enabled')",(tenant,a))
source=provision(root,operation_id='fixture_source',account='customer@preview.luckio.cn',password_hash=hash_password('FIXTURE_CUSTOMER_PASSWORD'))
with main.store.connection() as c:c.execute('update preview_customer_accounts set initial_password_pending=? where user_id=?',(pending,source['user_id']))
old_task=main.product_store.create_task(source['tenant_id'],source['user_id'],'copywriting-agent','FIXTURE old private task',None)
owner_task=main.product_store.create_task(tenant,owner,'copywriting-agent','FIXTURE owner task',None)
with main.store.connection() as c:
 c.execute("insert into generations(id,tenant_id,user_id,conversation_id,task_id,storage_key,mime_type,width,height) values('old-customer-image',?,?,'fixture-old',?,'old-customer.png','image/png',128,128)",(source['tenant_id'],source['user_id'],old_task['id']))
 before_tasks=[tuple(r) for r in c.execute('select * from tasks order by id')]
 before_images=[tuple(r) for r in c.execute('select * from generations')]
 owner_config=c.execute('select payload from enterprise_configs where tenant_id=?',(tenant,)).fetchone()[0]
 old_credit_rows=[tuple(r) for r in c.execute('select * from credit_transactions order by id')]
 old_user=c.execute('select * from users where id=?',(source['user_id'],)).fetchone()
 owner_user=main.product_store.user_by_email('owner@fixture.invalid')
 old_cookie=main.sessions.issue(UserPrincipal(source['user_id'],source['tenant_id'],'enterprise_admin',main.sessions.credential_version(old_user['password_hash'])))
 owner_cookie=main.sessions.issue(UserPrincipal(owner,tenant,'enterprise_admin',main.sessions.credential_version(owner_user['password_hash'])))
request=dict(operation_id='fixture_binding',source_operation_id='fixture_source',target_tenant_id=tenant,owner_user_id=owner)
bound=bind_existing_customer(root,**request);again=bind_existing_customer(root,**request)
assert bound['created'] and not again['created'] and bound['user_id']==again['user_id'] and bound['tenant_id']==tenant and bound['user_id']!=owner
assert bound['role']=='member' and bound['credits']['balance']==100000 and again['credits']['added']==0
with main.store.connection() as c:
 assert [tuple(r) for r in c.execute('select * from tasks order by id')]==before_tasks
 assert [tuple(r) for r in c.execute('select * from generations')]==before_images
 assert c.execute('select payload from enterprise_configs where tenant_id=?',(tenant,)).fetchone()[0]==owner_config
 assert c.execute('select count(*) from tenants').fetchone()[0]==2
 assert all(tuple(c.execute('select * from credit_transactions where id=?',(r[0],)).fetchone())==r for r in old_credit_rows)
 new_user=c.execute('select * from users where id=?',(bound['user_id'],)).fetchone()
 assert new_user['password_hash']==old_user['password_hash'] and new_user['tenant_id']==tenant
 assert c.execute('select tenant_id from users where id=?',(source['user_id'],)).fetchone()[0]==source['tenant_id']
 assert c.execute('select amount from credit_transactions where tenant_id=? and reason=?',(tenant,CUSTOMER_CANONICAL_OPERATION)).fetchone()[0]>=150
async def no_runtime(task):pass
main.task_service.execute=no_runtime
async def run():
 transport=httpx.ASGITransport(app);headers={'Origin':'http://preview.fixture'}
 async with httpx.AsyncClient(transport=transport,base_url='http://preview.fixture') as customer:
  response=await customer.post('/preview/api/v1/preview-auth/login',headers=headers,json={'account':'customer@preview.luckio.cn','password':'FIXTURE_CUSTOMER_PASSWORD'})
  assert response.status_code==200 and response.json()['password_change_required']==bool(pending)
  password='FIXTURE_CUSTOMER_PASSWORD'
  if pending:
   response=await customer.post('/preview/api/v1/preview-auth/password',headers=headers,json={'new_password':'FIXTURE_NEW_TARGET_PASSWORD'})
   assert response.status_code==200;password='FIXTURE_NEW_TARGET_PASSWORD'
  me=(await customer.get('/preview/api/v1/me')).json()
  assert me['tenant_id']==tenant and me['user_id']==bound['user_id'] and me['role']=='member'
  for agent in ['copywriting-agent','campaign-agent','preview-wechat-agent','image-agent']:
   assert (await customer.get('/preview/api/v1/agents/'+agent)).status_code==200
   assert main.product_store.agent_enabled(tenant,agent)
  assert (await customer.get('/preview/api/v1/tasks/'+owner_task['id'])).status_code==404
  assert (await customer.get('/preview/api/v1/tasks/'+old_task['id'])).status_code==404
  assert (await customer.get('/preview/api/v1/storage/old-customer.png')).status_code==404
  assert (await customer.get('/preview/api/v1/enterprise-config')).status_code==403
  assert (await customer.get('/preview/api/v1/members')).status_code==403
  cap=await customer.get('/preview/api/v1/preview/illustrations/capability')
  assert cap.status_code==200 and cap.json()['enabled'] and cap.json()['remaining_requests']==1000
  task=await customer.post('/preview/api/v1/agents/copywriting-agent/runs',headers=headers,json={'message':'FIXTURE_ONLY no dispatch'})
  assert task.status_code==202,task.text
  tid=task.json()['id'];assert (await customer.get('/preview/api/v1/tasks/'+tid)).json()['tenant_id']==tenant
  content=json.loads((Path('tests/fixtures/activity_plan_d1.json')).read_text())
  doc=await customer.post('/preview/api/v1/documents/activity-plan',headers=headers,json={'content':content})
  assert doc.status_code==201,doc.text
  doc_url=doc.json()['download_url'];doc_url='/preview'+doc_url if not doc_url.startswith('/preview') else doc_url
  assert (await customer.get(doc_url)).status_code==200
  await customer.post('/preview/api/v1/preview-auth/logout',headers=headers,json={})
  async with httpx.AsyncClient(transport=transport,base_url='http://preview.fixture') as fresh:
   assert (await fresh.post('/preview/api/v1/preview-auth/login',headers=headers,json={'account':'customer@preview.luckio.cn','password':password})).status_code==200
   assert (await fresh.get('/preview/api/v1/me')).json()['tenant_id']==tenant
   assert (await fresh.get('/preview/api/v1/tasks/'+tid)).status_code==200
  async with httpx.AsyncClient(transport=transport,base_url='http://preview.fixture',cookies={COOKIE:owner_cookie}) as original:
   assert (await original.get('/preview/api/v1/me')).json()['user_id']==owner
   assert (await original.get('/preview/api/v1/tasks/'+owner_task['id'])).status_code==200
   assert (await original.get('/preview/api/v1/tasks/'+tid)).status_code==404
   assert (await original.get(doc_url)).status_code==404
   assert (await original.get('/preview/api/v1/enterprise-config')).status_code==200
  async with httpx.AsyncClient(transport=transport,base_url='http://preview.fixture',cookies={COOKIE:old_cookie}) as retired:
   assert (await retired.get('/preview/api/v1/me')).status_code==401
 print('CANONICAL_CUSTOMER_FIXTURE_PASS provider_calls=0')
asyncio.run(run())
'''
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'-c',probe,str(tmp_path),str(pending)],cwd=root,
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(root)},stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=60)
    assert result.returncode==0,result.stdout[-4000:]
    assert 'CANONICAL_CUSTOMER_FIXTURE_PASS' in result.stdout
