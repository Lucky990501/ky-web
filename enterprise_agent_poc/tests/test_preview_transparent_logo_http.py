"""FIXTURE ONLY: actual Preview storage/thumbnail routes, no Provider calls."""
import os
from pathlib import Path
import subprocess
import sys


def test_legacy_brochure_storage_and_thumbnail_preserve_authorization(tmp_path):
    probe=r'''
import asyncio,io,json,sys
from pathlib import Path
from uuid import uuid4
import httpx
from PIL import Image
from scripts.customer_preview import create_preview
from app.auth import UserPrincipal
from app.storage import storage_provider
root=Path(sys.argv[1])/'customer-ai-preview-data'
app=create_preview(root,credentials={},pricing={},local_http=True)
from app import main
def raster(color):
 b=io.BytesIO();Image.new('RGB',(180,120),color).save(b,'PNG');return b.getvalue()
async def run():
 transport=httpx.ASGITransport(app)
 async with httpx.AsyncClient(transport=transport,base_url='http://preview.fixture') as client:
  me=(await client.get('/preview/api/v1/me')).json();t,u=me['tenant_id'],me['user_id']
  task=main.product_store.create_task(t,u,'image-agent','FIXTURE brochure',None)
  raw,old=raster((25,100,160)),raster('white');key=f'generated/{t}/fixture-composite.png';original=f'generated/{t}/fixture-original.png'
  storage=storage_provider(main.settings);storage.put(key,old,'image/png');storage.put(original,raw,'image/png')
  run_id=str(uuid4());gid=f"task:{task['id']}:generation"
  payload={'mcp_calls':[{'tool':'image_generation','artifacts':[{'storage_key':key,'original_storage_key':original,'layout_mode':'brochure_front','brand_placement':{'policy':'preview-brand-zone-v2'}}]}]}
  with main.store.connection() as c:
   c.execute('update tasks set run_id=? where id=?',(run_id,task['id']))
   c.execute('insert into run_traces(run_id,conversation_id,tenant_id,agent_id,status,payload) values(?,?,?,?,?,?)',(run_id,'fixture',t,'image-agent','completed',json.dumps(payload)))
   c.execute('insert into generations(id,tenant_id,user_id,conversation_id,task_id,storage_key,mime_type,width,height) values(?,?,?,?,?,?,?,?,?)',(gid,t,u,'fixture',task['id'],key,'image/png',180,120))
  url='/preview/api/v1/storage/'+key;thumb='/preview/api/v1/preview/image-display/generations/'+gid
  response=await client.get(url);assert response.status_code==200 and response.content==raw
  assert response.headers['content-type']=='image/png' and 'no-store' in response.headers['cache-control']
  response=await client.get(thumb);assert response.status_code==200
  with Image.open(io.BytesIO(response.content)) as picture:assert picture.getpixel((50,50))[0]<35
  assert storage.get(key)==old and storage.get(original)==raw
  async with httpx.AsyncClient(transport=transport,base_url='http://preview.fixture') as foreign:
   await foreign.get('/preview/api/v1/me')
   assert (await foreign.get(url)).status_code==404 and (await foreign.get(thumb)).status_code==404
  main.product_store.create_user(t,'other@fixture.invalid','FIXTURE_NOT_SECRET','Other','enterprise_admin')
  user=main.product_store.user_by_email('other@fixture.invalid')
  cookie=main.sessions.issue(UserPrincipal(user['id'],t,user['role'],main.sessions.credential_version(user['password_hash'])))
  async with httpx.AsyncClient(transport=transport,base_url='http://preview.fixture',cookies={'workbench_preview_session':cookie}) as other:
   assert (await other.get(url)).status_code==404 and (await other.get(thumb)).status_code==404
  with main.store.connection() as c:c.execute('update generations set deleted_at=CURRENT_TIMESTAMP where id=?',(gid,))
  assert (await client.get(url)).status_code==404 and (await client.get(thumb)).status_code==404
  import sqlite3
  with sqlite3.connect(root/'preview-budget.sqlite') as c:assert c.execute('select count(*) from requests').fetchone()[0]==0
 print('TRANSPARENT_LOGO_HTTP_FIXTURE_PASS provider_calls=0')
asyncio.run(run())
'''
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([sys.executable,'-c',probe,str(tmp_path)],cwd=root,
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(root)},
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=60)
    assert result.returncode==0,result.stdout[-4000:]
    assert 'TRANSPARENT_LOGO_HTTP_FIXTURE_PASS' in result.stdout
