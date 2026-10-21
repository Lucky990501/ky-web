"""FIXTURE_ONLY fixed-customer login; isolated SQLite, no model or Provider calls."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_actual_fixed_account_login_relogin_isolation_and_provision_idempotency(tmp_path):
    probe=r'''
import asyncio,json,sys,time
from pathlib import Path
import httpx
from scripts.customer_preview import create_preview
from app.auth import hash_password,SessionIssuer,UserPrincipal
from app.preview_customer_auth import provision,COOKIE,SETUP_COOKIE
root=Path(sys.argv[1])/'customer-ai-preview-data'
app=create_preview(root,local_http=True,credentials={},pricing={})
from app import main
from app.preview_illustration_service import PreviewIllustrationService
from types import SimpleNamespace
with main.store.connection() as c:
 for i in range(50):c.execute('insert into tenants values(?,?,?)',(f'preview-existing-{i}','FIXTURE',f'FIXTURE_{i}'))
 c.execute("insert into users(id,tenant_id,email,password_hash,display_name,role) values('other-user','preview-existing-0','other@fixture.invalid',?,'FIXTURE','enterprise_admin')",(hash_password('FIXTURE_OTHER_PASSWORD'),))
 c.execute("insert into credit_accounts(tenant_id,balance) values('preview-existing-0',100000)")
 c.execute("insert into tenant_agent_instances(tenant_id,agent_id,status) values('preview-existing-0','copywriting-agent','enabled')")
other=main.product_store.create_task('preview-existing-0','other-user','copywriting-agent','FIXTURE_PRIVATE',None)
initial='FIXTURE_INITIAL_123456';permanent='FIXTURE_NEW_PASSWORD_123456'
grant={'operation_id':'fixture_customer_v1','account':'customer@preview.luckio.cn','password_hash':hash_password(initial)}
one=provision(root,**grant);two=provision(root,**grant)
assert one['created'] and not two['created'] and one['tenant_id']==two['tenant_id'] and two['credits']['already_applied']
tenant=one['tenant_id'];user_id=one['user_id']
with main.store.connection() as c:
 assert c.execute('select count(*) from tenants').fetchone()[0]==51
 assert c.execute('select balance from credit_accounts where tenant_id=?',(tenant,)).fetchone()[0]==100000
 assert c.execute('select count(*) from credit_transactions where tenant_id=?',(tenant,)).fetchone()[0]==1
 assert c.execute("select count(*) from tenant_agent_instances where tenant_id=? and status='enabled'",(tenant,)).fetchone()[0]==4
 for agent in ['campaign-agent','copywriting-agent','preview-wechat-agent','image-agent']:
  assert main.product_store.agent_enabled(tenant,agent)
 # History Fixture; no dispatch. New browser must recover exactly this saved record.
 own=main.product_store.create_task(tenant,user_id,'copywriting-agent','FIXTURE_CUSTOMER_TASK',None)
 c.execute("update tasks set status='completed',stage='completed' where id=?",(own['id'],))
 c.execute("insert into task_results(task_id,final_response,result_json) values(?,?,?)",(own['id'],'FIXTURE saved body','{}'))
 c.execute("insert into generations(id,tenant_id,user_id,conversation_id,task_id,provider,model,storage_key,mime_type,width,height) values('fixture-private-image',?,?,'fixture-conversation',?,'FIXTURE','FIXTURE','fixture-not-downloaded.png','image/png',128,128)",(tenant,user_id,own['id']))
 own_task=own['id']
def snapshot():
 with main.store.connection() as c:
  return {t:[tuple(r) for r in c.execute('select * from '+t)] for t in ['tasks','task_results','generations','credit_transactions']}
saved=snapshot()
async def run():
 transport=httpx.ASGITransport(app)
 origin={'Origin':'http://preview.fixture'}
 async with httpx.AsyncClient(transport=transport,base_url='http://preview.fixture') as a:
  assert (await a.get('/preview/')).status_code==303
  assert (await a.get('/preview/api/v1/me')).status_code==401
  assert (await a.get('/preview/login')).status_code==200
  assert (await a.post('/preview/api/v1/preview-auth/login',json={'account':grant['account'],'password':initial})).status_code==403
  bad=await a.post('/preview/api/v1/preview-auth/login',headers=origin,json={'account':grant['account'],'password':'wrong'})
  assert bad.status_code==401 and COOKIE not in a.cookies
  login=await a.post('/preview/api/v1/preview-auth/login',headers=origin,json={'account':grant['account'],'password':initial})
  assert login.status_code==200 and login.json()['password_change_required']
  assert 'httponly' in login.headers['set-cookie'].lower() and 'path=/preview/' in login.headers['set-cookie'].lower()
  setup=a.cookies.get(SETUP_COOKIE)
  assert (await a.get('/preview/api/v1/me')).status_code==401
  # Setup authority cannot be copied into a business cookie.
  async with httpx.AsyncClient(transport=transport,base_url='http://preview.fixture',cookies={COOKIE:setup}) as attacker:
   assert (await attacker.get('/preview/api/v1/me')).status_code==401
  change=await a.post('/preview/api/v1/preview-auth/password',headers=origin,json={'new_password':permanent})
  assert change.status_code==200 and COOKIE in a.cookies
  me=(await a.get('/preview/api/v1/me')).json();assert me['tenant_id']==tenant and me['user_id']==user_id
  assert (await a.get('/preview/api/v1/tasks/'+other['id'])).status_code==404
  assert (await a.get('/preview/api/v1/tasks/'+own_task)).status_code==200
  await a.post('/preview/api/v1/preview-auth/logout',headers=origin,json={})
  assert (await a.get('/preview/api/v1/me')).status_code==401
 async with httpx.AsyncClient(transport=transport,base_url='http://preview.fixture') as b:
  assert (await b.post('/preview/api/v1/preview-auth/login',headers=origin,json={'account':grant['account'],'password':initial})).status_code==401
  result=await b.post('/preview/api/v1/preview-auth/login',headers=origin,json={'account':grant['account'],'password':permanent})
  assert result.status_code==200 and not result.json()['password_change_required']
  assert (await b.get('/preview/api/v1/me')).json()['tenant_id']==tenant
  assert (await b.get('/preview/api/v1/tasks/'+own_task)).status_code==200
  other_user=main.product_store.user_by_email('other@fixture.invalid')
  other_token=main.sessions.issue(UserPrincipal(other_user['id'],other_user['tenant_id'],'enterprise_admin',main.sessions.credential_version(other_user['password_hash'])))
  async with httpx.AsyncClient(transport=transport,base_url='http://preview.fixture',cookies={COOKIE:other_token}) as legacy:
   assert (await legacy.get('/preview/api/v1/tasks/'+own_task)).status_code==404
   assert (await legacy.get('/preview/api/v1/storage/fixture-not-downloaded.png')).status_code==404
   assert (await legacy.get('/preview/api/v1/me')).json()['tenant_id']=='preview-existing-0'
  full_user=main.product_store.user_by_email(grant['account'])
  expired=main.sessions.issue(UserPrincipal(user_id,tenant,'enterprise_admin',main.sessions.credential_version(full_user['password_hash'])),-1)
  b.cookies.clear();b.cookies.set(COOKIE,expired,domain='preview.fixture',path='/preview/')
  assert (await b.get('/preview/api/v1/me')).status_code==401
  result=await b.post('/preview/api/v1/preview-auth/login',headers=origin,json={'account':grant['account'],'password':permanent})
  assert result.status_code==200 and (await b.get('/preview/api/v1/me')).json()['tenant_id']==tenant
  assert snapshot()==saved
  with main.store.connection() as c:assert c.execute('select count(*) from tenants').fetchone()[0]==51
  # Explicit Qwen membership, never all anonymous visitors.
  service=object.__new__(PreviewIllustrationService);service.authorization={'allowed_tenant_ids':[tenant]}
  assert service.actor_allowed(SimpleNamespace(tenant_id=tenant))
  assert not service.actor_allowed(SimpleNamespace(tenant_id='preview-existing-0'))
  assert not main.skill_registry.is_platform_admin(user_id)
  for i in range(6):
   assert (await b.post('/preview/api/v1/preview-auth/login',headers=origin,json={'account':'missing@preview.luckio.cn','password':'wrong'})).status_code==401
  assert (await b.post('/preview/api/v1/preview-auth/login',headers=origin,json={'account':'missing@preview.luckio.cn','password':'wrong'})).status_code==429
  with main.store.connection() as c:c.execute("update users set account_status='disabled' where id=?",(user_id,))
  assert (await b.get('/preview/api/v1/me')).status_code==401
  assert (await b.post('/preview/api/v1/preview-auth/login',headers=origin,json={'account':grant['account'],'password':permanent})).status_code==401
 print('FIXED_CUSTOMER_FIXTURE_PASS provider_calls=0')
asyncio.run(run())
'''
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'-c',probe,str(tmp_path)],cwd=root,
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(root)},
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=60)
    assert result.returncode==0,result.stdout[-4500:]
    assert 'FIXED_CUSTOMER_FIXTURE_PASS' in result.stdout
