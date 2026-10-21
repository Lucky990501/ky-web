"""FIXTURE_ONLY: explicitly consented same-USER login, no Provider calls."""
import os
from pathlib import Path
import subprocess
import sys


def test_customer_login_shares_exact_owner_history_without_changing_passwords(tmp_path):
    probe=r'''
import asyncio,json,sys
from pathlib import Path
import httpx
from scripts.customer_preview import create_preview
from app.auth import hash_password,UserPrincipal
from app.preview_customer_auth import provision,bind_existing_customer,share_existing_owner_identity,COOKIE
root=Path(sys.argv[1])/'customer-ai-preview-data';tenant='preview-owner-fixture';owner='fixture-owner'
app=create_preview(root,credentials={},pricing={},local_http=True)
from app import main
with main.store.connection() as c:
 c.execute('insert into tenants values(?,?,?)',(tenant,'FIXTURE company','FIXTURE'))
 c.execute('insert into enterprise_configs values(?,?)',(tenant,'{"brand_name":"FIXTURE shared company"}'))
 c.execute("insert into users(id,tenant_id,email,password_hash,display_name,role) values(?,?,?,?,?,'enterprise_admin')",(owner,tenant,'owner@fixture.invalid',hash_password('FIXTURE_OWNER_PASSWORD'),'Owner'))
 c.execute('insert into credit_accounts(tenant_id,balance) values(?,100000)',(tenant,))
 for a in ['copywriting-agent','campaign-agent','preview-wechat-agent','image-agent']:c.execute("insert into tenant_agent_instances(tenant_id,agent_id,status) values(?,?,'enabled')",(tenant,a))
source=provision(root,operation_id='fixture_source',account='customer@preview.luckio.cn',password_hash=hash_password('FIXTURE_CHANGED_CUSTOMER_PASSWORD'))
with main.store.connection() as c:c.execute('update preview_customer_accounts set initial_password_pending=0')
bound=bind_existing_customer(root,operation_id='fixture_binding',source_operation_id='fixture_source',target_tenant_id=tenant,owner_user_id=owner)
task=main.product_store.create_task(tenant,owner,'copywriting-agent','FIXTURE existing Owner draft',None)
private=main.product_store.create_task(source['tenant_id'],source['user_id'],'copywriting-agent','FIXTURE other Tenant',None)
with main.store.connection() as c:
 before={table:[tuple(r) for r in c.execute('select * from '+table+' order by 1')] for table in ['users','tenants','tasks','conversations','credit_transactions','preview_customer_bindings']}
owner_user=main.product_store.user_by_email('owner@fixture.invalid')
owner_cookie=main.sessions.issue(UserPrincipal(owner,tenant,'enterprise_admin',main.sessions.credential_version(owner_user['password_hash'])))
args=dict(operation_id='fixture_explicit_same_owner',binding_operation_id='fixture_binding',target_tenant_id=tenant,owner_user_id=owner)
assert share_existing_owner_identity(root,**args)['created']
assert not share_existing_owner_identity(root,**args)['created']
with main.store.connection() as c:
 for table,rows in before.items():assert rows==[tuple(r) for r in c.execute('select * from '+table+' order by 1')],table
async def run():
 transport=httpx.ASGITransport(app);headers={'Origin':'http://preview.fixture'}
 async with httpx.AsyncClient(transport=transport,base_url='http://preview.fixture') as client:
  async def login(password='FIXTURE_CHANGED_CUSTOMER_PASSWORD'):
   return await client.post('/preview/api/v1/preview-auth/login',headers=headers,json={'account':'customer@preview.luckio.cn','password':password})
  assert (await login('WRONG_PASSWORD')).status_code==401
  assert (await login('FIXTURE_OWNER_PASSWORD')).status_code==401
  assert (await login()).status_code==200
  login_token=client.cookies.get(COOKIE)
  assert main.sessions.verify(login_token).user_id==bound['user_id']
  for _ in range(2):
   me=(await client.get('/preview/api/v1/me')).json()
   assert me['user_id']==owner and me['tenant_id']==tenant and me['role']=='enterprise_admin',me
   assert client.cookies.get(COOKIE)==login_token  # Never leak the internal Owner token to browser.
  assert (await client.get('/preview/api/v1/tasks/'+task['id'])).status_code==200
  assert (await client.get('/preview/api/v1/tasks/'+private['id'])).status_code==404
  assert (await client.get('/preview/api/v1/enterprise-config')).status_code==200
  assert (await client.get('/preview/api/v1/platform/users')).status_code==403
  for a in ['copywriting-agent','campaign-agent','preview-wechat-agent','image-agent']:
   assert (await client.get('/preview/api/v1/agents/'+a)).status_code==200
  content=json.loads(Path('tests/fixtures/activity_plan_d1.json').read_text())
  doc=await client.post('/preview/api/v1/documents/activity-plan',headers=headers,json={'content':content})
  assert doc.status_code==201,doc.text
  url=doc.json()['download_url'];url=url if url.startswith('/preview') else '/preview'+url
  assert (await client.get(url)).status_code==200
  async with httpx.AsyncClient(transport=transport,base_url='http://preview.fixture',cookies={COOKIE:owner_cookie}) as original:
   assert (await original.get('/preview/api/v1/me')).json()['user_id']==owner
   assert (await original.get(url)).status_code==200
  await client.post('/preview/api/v1/preview-auth/logout',headers=headers,json={})
  assert (await client.get('/preview/api/v1/me')).status_code==401
  assert (await login()).status_code==200
  assert (await client.get('/preview/api/v1/tasks/'+task['id'])).status_code==200
  assert (await client.get(url)).status_code==200
  # Durable revocation survives a fresh auth instance (restart equivalent).
  with main.store.connection() as c:c.execute('update preview_customer_shared_identity set enabled=0')
  assert (await client.get('/preview/api/v1/me')).status_code==401
  from app.preview_customer_auth import PreviewCustomerAuth
  verifier=PreviewCustomerAuth(main,root,local_http=True)
  try:verifier.business_token(client.cookies.get(COOKIE));raise AssertionError('revoked grant accepted')
  except PermissionError:pass
  try:share_existing_owner_identity(root,**args);raise AssertionError('revoked grant silently re-enabled')
  except PermissionError:pass
  with main.store.connection() as c:
   c.execute('update preview_customer_shared_identity set enabled=1')
   c.execute("update users set account_status='disabled' where id=?",(owner,))
  assert (await client.get('/preview/api/v1/me')).status_code==401
  with main.store.connection() as c:c.execute("update users set account_status='enabled' where id=?",(owner,))
  assert (await client.get('/preview/api/v1/me')).json()['user_id']==owner
  with main.store.connection() as c:c.execute('update users set password_hash=? where id=?',(hash_password('FIXTURE_ROTATED_CUSTOMER_PASSWORD'),bound['user_id']))
  assert (await client.get('/preview/api/v1/me')).status_code==401
 print('SHARED_OWNER_FIXTURE_PASS provider_calls=0')
asyncio.run(run())
'''
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'-c',probe,str(tmp_path)],cwd=root,
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(root)},
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=60)
    assert result.returncode==0,result.stdout[-4000:]
    assert 'SHARED_OWNER_FIXTURE_PASS' in result.stdout
