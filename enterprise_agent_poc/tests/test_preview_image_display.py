"""FIXTURE_ONLY thumbnail authorization/integrity. Never calls a Provider."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import time
from uuid import uuid4
from zipfile import ZipFile
from PIL import Image
import pytest
from app.preview_image_display import PreviewImageDisplay
from app.preview_illustration_provider import IllustrationError
from test_preview_image_execution import image_fixture
from test_preview_illustrations import setup,completed_task


@pytest.fixture
def saved(setup):
    service,actors,calls,budget=setup;actor=actors[0]
    task=completed_task(service,actor,'wechat');plan=service.plan(actor,task['id'])
    out=io.BytesIO();Image.new('RGBA',(1600,1200),(25,130,200,125)).save(out,format='PNG');raw=out.getvalue()
    identifier=uuid4().hex;key=f'assets/{actor.tenant_id}/fixture-{identifier}.png';service.storage.put(key,raw,'image/png')
    asset={'id':identifier,'position':0,'role':'body','label':'FIXTURE illustration','filename':'fixture.png','storage_key':key,'url':'/api/v1/preview/illustrations/assets/'+identifier,'sha256':hashlib.sha256(raw).hexdigest(),'mime_type':'image/png','width':1600,'height':1200}
    with service.db() as c:
        c.execute("insert into illustration_requests(id,plan_id,item_index,ceiling,status,http_status,created,asset_json) values(?,?,0,'0.1','saved',200,?,?)",(uuid4().hex,plan['id'],time.time(),json.dumps(asset)))
        c.execute("update illustration_plans set status='completed' where id=?",(plan['id'],))
    return service,actors,PreviewImageDisplay(service.main,service,service.root),asset,plan,raw,calls,budget


def test_thumbnail_is_private_reusable_derivative_preserves_original_and_exports(saved):
    service,actors,display,asset,plan,raw,calls,budget=saved;actor=actors[0]
    before=service.export_article(actor,plan['id']);package=service.export_article(actor,plan['id'],True)
    with service.db() as c:ledger=[tuple(r) for r in c.execute('select * from illustration_requests')]
    first=display.render(actor,'illustrations',asset['id']);files=list(display.root.rglob('*.webp'));assert len(files)==1
    stamp=files[0].stat().st_mtime_ns
    assert display.render(actor,'illustrations',asset['id'])==first and files[0].stat().st_mtime_ns==stamp
    with Image.open(io.BytesIO(first)) as image:assert image.format=='WEBP' and image.size==(720,540) and 'A' in image.getbands()
    assert service.asset(actor,asset['id'])[0]==raw
    assert service.export_article(actor,plan['id'])==before
    entries=lambda data:{n:ZipFile(io.BytesIO(data)).read(n) for n in ZipFile(io.BytesIO(data)).namelist()}
    assert entries(service.export_article(actor,plan['id'],True))==entries(package)
    with service.db() as c:assert ledger==[tuple(r) for r in c.execute('select * from illustration_requests')]
    assert calls==[] and budget.summary()['image']['requests']==0


@pytest.mark.parametrize('other',[1,2])
def test_cached_thumbnail_still_checks_tenant_and_user(saved,other):
    service,actors,display,asset,*_=saved
    display.render(actors[0],'illustrations',asset['id'])
    with pytest.raises((LookupError,PermissionError)):display.render(actors[other],'illustrations',asset['id'])


def test_tampered_original_rejected_even_when_thumbnail_exists(saved):
    service,actors,display,asset,*_=saved
    display.render(actors[0],'illustrations',asset['id'])
    service.storage.put(asset['storage_key'],b'FIXTURE corrupted original','image/png')
    with pytest.raises(IllustrationError):display.render(actors[0],'illustrations',asset['id'])


def test_concurrent_read_only_generation_thumbnail_and_deleted_access(saved):
    service,actors,display,asset,plan,raw,*_=saved;actor=actors[0]
    task=service.main.product_store.create_task(actor.tenant_id,actor.user_id,'image-agent','FIXTURE existing saved result',None)
    gid=str(uuid4())
    with service.main.store.connection() as c:c.execute('insert into generations(id,tenant_id,user_id,conversation_id,task_id,storage_key,mime_type,width,height) values(?,?,?,?,?,?,?,?,?)',(gid,actor.tenant_id,actor.user_id,task['conversation_id'] or 'fixture',task['id'],asset['storage_key'],'image/png',1600,1200))
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:display.render(actor,'generations',gid),range(2)))
    assert results[0]==results[1] and service.storage.get(asset['storage_key'])==raw
    with pytest.raises(LookupError):display.render(actors[2],'generations',gid)
    with service.main.store.connection() as c:c.execute('update generations set deleted_at=CURRENT_TIMESTAMP where id=?',(gid,))
    with pytest.raises(LookupError):display.render(actor,'generations',gid)
    with pytest.raises(LookupError):display.render(actor,'generations','../invalid')
