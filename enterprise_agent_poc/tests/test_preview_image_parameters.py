"""Owner-spec parameter adapter fixtures; never a real Provider PASS."""
import asyncio
import base64
import io
import time
from email.parser import BytesParser
from email.policy import default

import httpx
import pytest
from PIL import Image

from app.preview_image_parameters import SIZES,QUALITIES,preview_plan,quality_value
from app.image_request_contract import pixel_size
from app.platform_mcp.service import PlatformMCPService
from app.security import RuntimePrincipal
from test_preview_image_execution import image_fixture,payload,upload_reference


@pytest.mark.parametrize('size',SIZES)
def test_owner_sizes_are_exact_preview_values(size):
    plan=preview_plan('生成1张风景图片，不要Logo',size=size)
    assert plan[0]['size']==size
    if max(map(int,size.split('x')))>1536:
        with pytest.raises(ValueError):pixel_size(size)  # Production default unchanged.


@pytest.mark.parametrize('quality',QUALITIES)
def test_quality_enum(quality):assert quality_value(quality)==quality


@pytest.mark.parametrize('quality',['auto','ultra',1,[],{}])
def test_unknown_explicit_quality_fails(quality):
    with pytest.raises(ValueError):quality_value(quality)


def test_no_silent_alias_conflict_or_unlisted_resolution():
    assert quality_value(None) is None
    for message,size in [('生成4K海报',None),('生成1024x1024图片','2048x2048'),('生成9:16海报','3840x2160'),('生成图','4096x4096')]:
        with pytest.raises(ValueError):preview_plan(message,size=size)


def wire_references(executor,actor,count):
    """Distinct synthetic bytes and Chinese names expose multipart order loss."""
    from app.storage import storage_provider
    refs=[]
    for index in range(count):
        source=io.BytesIO();Image.new('RGB',(256,256),(index*40,100,150)).save(source,format='PNG')
        content=source.getvalue();key=f'chat-uploads/{actor.tenant_id}/wire-{index}.png'
        storage_provider(executor.main.settings).put(key,content,'image/png')
        refs.append(executor.main.product_store.create_chat_image_attachment(
            actor.tenant_id,actor.user_id,key,f'场景{index+1}.png','image/png',len(content),256,256))
    return refs


@pytest.mark.parametrize('count',[2,3,5])
def test_multiple_uploads_order_roles_single_use_and_no_schema_change(image_fixture,count):
    executor,actors,calls=image_fixture;actor=actors[0]
    refs=wire_references(executor,actor,count)
    attachments=[{'type':'image','id':r['id'],'role':'subject' if i==0 else 'style'} for i,r in enumerate(refs)]
    task=executor.submit(actor,payload(message='生成1张风景图片，不要Logo',attachments=attachments,quality='high',size='2048x2048'))
    rows=executor.references.resolve(task,[r['id'] for r in refs]);assert [r['id'] for r in rows]==[r['id'] for r in refs]
    assert rows[0]['role']=='subject' and rows[1]['role']=='style'
    assert executor.submission(task['id'])['quality']=='high'
    assert executor.references.claimed(actor,refs[-1]['id'])
    with pytest.raises(ValueError):executor.references.resolve(task,[refs[-1]['id']])
    with executor.main.store.connection() as conn:
        assert conn.execute('select count(*) from chat_image_attachments where task_id=?',(task['id'],)).fetchone()[0]==1
        conn.execute("update tasks set status='failed' where id=?",(task['id'],))
    with pytest.raises(ValueError):executor.submit(actor,payload(attachments=[attachments[-1]]))
    assert calls==[] and executor.budget.summary()['image']['requests']==0


def test_duplicate_and_foreign_secondary_upload_rejected(image_fixture):
    executor,actors,calls=image_fixture;mine=upload_reference(executor,actors[0]);other=upload_reference(executor,actors[1])
    for refs in [[mine,mine],[mine,other]]:
        with pytest.raises(ValueError):executor.submit(actors[0],payload(attachments=[{'type':'image','id':r['id']} for r in refs]))
    assert calls==[]


def test_unverified_multi_wire_is_paused_before_task_or_budget(image_fixture,monkeypatch):
    executor,actors,calls=image_fixture
    monkeypatch.setattr('app.preview_image_parameters.MULTI_REFERENCE_PAUSED',True)
    refs=[upload_reference(executor,actors[0]) for _ in range(2)]
    with pytest.raises(ValueError,match='HTTP400'):
        executor.submit(actors[0],payload(attachments=[{'type':'image','id':r['id']} for r in refs]))
    assert executor.capability()['max_reference_images']==1 and calls==[]
    assert executor.budget.summary()['image']['requests']==0


def test_provider_rejection_fields_never_expose_body_credentials_or_paths():
    from app.preview_image_parameters import provider_error_fields
    response=httpx.Response(400,json={'error':{'code':'invalid_request','type':'invalid_request_error','param':'size','message':'unsupported size; sk-fixture-secret https://private.invalid /opt/private data:image/png;base64,private'}})
    safe=provider_error_fields(response)
    assert safe=={'category':'SIZE_REJECTED','code':'invalid_request','type':'invalid_request_error','param':'size'}
    hostile=httpx.Response(400,json={'error':{'code':'sk-private','param':'/opt/private','message':'private'}})
    assert provider_error_fields(hostile)=={'category':'UNCLASSIFIED'}


@pytest.mark.parametrize('count,size,quality',[(0,'3840x2160','high'),(1,'2048x2048','medium'),(2,'2160x3840','low'),(3,'2048x2048','high'),(5,'1024x1024','high')])
def test_real_service_fixture_multipart_quality_pixels_and_mismatch(image_fixture,monkeypatch,count,size,quality):
    executor,actors,calls=image_fixture;actor=actors[0]
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    monkeypatch.setenv('ENTERPRISE_POC_DATA_DIR',str(executor.budget.path.parent))
    monkeypatch.setenv(executor.main.settings.image_api_key_env,'FIXTURE_ONLY_NOT_A_KEY')
    refs=wire_references(executor,actor,count)
    task=executor.submit(actor,payload(message='生成1张抽象风景图片，不要Logo',attachments=[{'type':'image','id':r['id'],'role':'style'} for r in refs],size=size,quality=quality))
    observed=[];output=io.BytesIO();Image.new('RGB',(512,512),'blue').save(output,format='PNG')
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            observed.append((url,kwargs))
            return httpx.Response(200,json={'request_id':'FIXTURE_ONLY','data':[{'b64_json':base64.b64encode(output.getvalue()).decode()}]})
    monkeypatch.setattr(httpx,'AsyncClient',Client)
    token=executor.main.token_issuer.issue(RuntimePrincipal(actor.tenant_id,'image-agent','fixture',('image:generate',),int(time.time())+60))
    signed='runtime:'+executor.main.token_issuer.issue_task_scope(actor.tenant_id,task['id'])
    service=PlatformMCPService(executor.main.store,executor.main.token_issuer,executor.main.settings)
    ratio=preview_plan(task['input_text'],size=size)[0]['aspect_ratio']
    result=asyncio.run(service.image_generation(token,task['input_text'],[],ratio,execution_scope=signed,reference_images=[r['id'] for r in refs],size=size,quality=quality))
    assert len(observed)==1
    url,request=observed[0];body=request.get('json') or request.get('data')
    assert body['quality']==quality and body['size']==size
    assert body['model']=='gpt-image-2.5-sunburst-c'  # Never adopt example's other model.
    if count>1:
        assert url.endswith('/images/edits') and 'json' not in request and 'image' not in body
        assert body['n']=='1' and body['response_format']=='b64_json'
        assert request['headers']['Accept']=='application/json'
        assert 'Content-Type' not in request['headers']  # httpx must set boundary.
        assert isinstance(request['files'],list) and len(request['files'])==count
        from app.storage import storage_provider
        for (field,file),ref in zip(request['files'],refs):
            assert field=='image'
            assert file==(ref['filename'],storage_provider(executor.main.settings).get(ref['storage_key']),ref['mime_type'])
        # Exercise the real HTTP serializer without opening any connection.
        wire=httpx.Request('POST',url,**request)
        content=wire.read()
        assert wire.headers['content-type'].startswith('multipart/form-data; boundary=')
        multipart=BytesParser(policy=default).parsebytes(
            ('Content-Type: '+wire.headers['content-type']+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+content)
        parts=list(multipart.iter_parts())
        images=[p for p in parts if p.get_param('name',header='content-disposition')=='image']
        assert len(images)==count
        assert not any(p.get_param('name',header='content-disposition')=='image[]' for p in parts)
        for part,ref in zip(images,refs):
            assert part.get_filename()==ref['filename']
            assert part.get_content_type()==ref['mime_type']
            assert part.get_payload(decode=True)==storage_provider(executor.main.settings).get(ref['storage_key'])
    elif count==1:assert 'files' in request and url.endswith('/images/edits')
    else:assert url.endswith('/images/generations') and 'image' not in body
    artifact=result['results'][0]
    assert artifact['reference_images_used']==[r['id'] for r in refs] and artifact['requested_quality']==quality
    assert artifact['actual_size']=='512x512' and not artifact['dimension_match'] and result['delivery']['status']=='partial'
    assert calls==[]  # Mocked HTTP only, no real Provider invocation.
