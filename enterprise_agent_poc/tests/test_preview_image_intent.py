"""Isolated zero-paid-call fixtures, NOT real Provider acceptance."""
import asyncio
import base64
import io
import json
import time
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from app.image_request_contract import ImageRequestError, requested_count
from app.preview_image_intent import AUTO_SIZES, make_plan, stored_plan
from app.platform_mcp.service import PlatformMCPService
from app.security import RuntimePrincipal
from app.storage import storage_provider
from test_preview_image_execution import image_fixture, payload, upload_reference


@pytest.mark.parametrize('text,size,count',[
 ('做个公众号头图','3840x2160',1),('生成一张竖版海报','2160x3840',1),
 ('来一张方形头像图','2048x2048',1),('做个三折页','1536x1024',2),
 ('做一套正反面图','2048x2048',2),('生成一组横版活动图','3840x2160',3),
 ('帮我生成3张教育海报','2160x3840',3),('生成2张图','2048x2048',2),('生成5张图','2048x2048',5),
 ('做3张，第一张招生，第二张课程介绍，第三张品牌宣传','2048x2048',3),
 ('第一张招生，第二张课程，第三张品牌','2048x2048',3),
 ('上传3张参考图，生成1张主视觉','2048x2048',1),
 ('用3张参考图，生成2张横版图片','3840x2160',2),
 ('生成3张，例如第一张也可以有2张照片的构图。','2048x2048',3),
 ('生成一张1080x1920图片','2160x3840',1),('方形海报','2048x2048',1),
 ('公众号配图','3840x2160',1),('9:16的横版海报','2160x3840',1),
 ('2048×2048的竖版海报','2048x2048',1),
 ('做3张每面单独一张','2048x2048',3),
 ('参考图3张，输出1张主视觉','2048x2048',1),
])
def test_auto_size_count_and_complete_prompts(text,size,count):
    plan,note=make_plan(text)
    assert len(plan)==count and all(p['size']==size and p['purpose'] and p['prompt'] for p in plan)
    assert [p['index'] for p in plan]==list(range(count))
    if '三折页' in text:
        assert '三个' in plan[0]['prompt'] and 'PDF' in note
    if '第二张课程介绍' in text:assert plan[1]['purpose']=='课程介绍'


@pytest.mark.parametrize('text',['生成6张图','生成0张图','生成3张，最后要2张','生成2张，第三张品牌','1024x1024，比例9:16'])
def test_real_conflicts_not_silently_dropped(text):
    with pytest.raises(ImageRequestError):make_plan(text)


def test_per_slot_purposes_and_sizes_and_standard_mapping_note():
    plan,_=make_plan('做3张，第一张方形头像，第二张竖版海报，第三张横版封面')
    assert [p['size'] for p in plan]==['2048x2048','2160x3840','3840x2160']
    assert '标准规格' in make_plan('4:3封面图')[1]
    assert len(make_plan('第一张招生第二张课程第三张品牌')[0])==3


def run_batch(executor,actor,message='生成3张横版教育海报',*,failed=(),unknown=(),conversation_id=None,refs=None):
    async def fixture(task,first):
        plans=stored_plan(executor.submission(task['id']),task)
        artifacts=[];failures=[]
        for p in plans:
            i=p['index']
            if i in failed or i in unknown:
                status=503 if i in failed else None
                executor.main.store.log_event(None,'image.request_failed',dict(tenant_id=actor.tenant_id,task_id=task['id'],index=i,provider_status=status))
                failures.append(dict(index=i,purpose=p['purpose'],code='image_provider_or_storage_error',provider_status=status));continue
            raw=io.BytesIO();Image.new('RGB',(256,256),(i*50,60,90)).save(raw,'PNG')
            key=f'generated/{actor.tenant_id}/{uuid4().hex}.png'
            storage_provider(executor.main.settings).put(key,raw.getvalue(),'image/png')
            artifact=dict(index=i,purpose=p['purpose'],storage_key=key,requested_size=p['size'],dimension_match=False)
            artifacts.append(artifact)
            executor.main.store.log_event(None,'image.request',dict(tenant_id=actor.tenant_id,task_id=task['id'],index=i,provider_status=200))
        return dict(results=artifacts,failures=failures,delivery=dict(requested=len(plans),generated=len(artifacts),failed=len(failures),status='partial' if failures else 'completed'))
    executor.call_image=fixture
    task=executor.submit(actor,payload(message=message,conversation_id=conversation_id,attachments=refs or []))
    asyncio.run(executor.execute(task))
    done=executor.main.product_store.task(task['id'],actor.tenant_id,actor.user_id)
    assert done['status']=='completed'
    return done


@pytest.mark.parametrize('followup,count,action',[
 ('再来一版',1,'edit'),('保留风格，再来2张',2,'series'),
 ('人物真实一点',1,'edit'),('只把标题改一下',1,'edit'),
 ('重新生成，但保留版式',1,'edit'),('按刚才那套视觉再来3张',3,'series'),
 ('第2张改一下标题',1,'edit'),
])
def test_followup_parent_prompt_size_history_and_counts(image_fixture,followup,count,action):
    executor,actors,calls=image_fixture;actor=actors[0]
    first=run_batch(executor,actor)
    before=[g['storage_key'] for g in first['generations']]
    req=payload(message=followup,conversation_id=first['conversation_id'])
    preview=executor.prepare(actor,req)
    assert len(preview['output_plan'])==count and preview['action']==action
    assert preview['parent'] and preview['parent']['task_id']==first['id']
    assert '教育海报' in preview['output_plan'][0]['prompt']
    assert preview['output_plan'][0]['size']=='3840x2160'
    req['preview_plan_digest']=preview['plan_digest']
    next_task=executor.submit(actor,req)
    submission=executor.submission(next_task['id'])
    assert len(stored_plan(submission,next_task))==count and submission['quality']=='high'
    cloned=executor.references.resolve(next_task)
    assert len(cloned)==1 and cloned[0]['task_id']==next_task['id']
    assert executor.submit(actor,req)['id']==next_task['id']
    asyncio.run(executor.execute(next_task))
    done=executor.main.product_store.task(next_task['id'],actor.tenant_id,actor.user_id)
    assert done['status']=='completed' and len(done['generations'])==count
    old=executor.main.product_store.task(first['id'],actor.tenant_id,actor.user_id)
    assert [g['storage_key'] for g in old['generations']]==before
    assert executor.budget.summary()['image']['requests']==0 and calls==[]


@pytest.mark.parametrize('followup',['补第3张','只重做失败项','刚才少了一张','继续生成','把剩下的也补齐'])
def test_recovery_only_failed_original_slots(image_fixture,followup):
    executor,actors,calls=image_fixture;actor=actors[0]
    first=run_batch(executor,actor,failed=(2,))
    before=[g['storage_key'] for g in first['generations']]
    prepared=executor.prepare(actor,payload(message=followup,conversation_id=first['conversation_id']))
    assert prepared['action']=='recovery' and prepared['root_output_indices']==[2]
    assert len(prepared['output_plan'])==1 and prepared['output_plan'][0]['purpose']=='重点呈现'
    second=run_batch(executor,actor,followup,conversation_id=first['conversation_id'])
    assert len(second['generations'])==1 and '补齐' in second['final_response']
    with pytest.raises(ImageRequestError,match='preview_image_nothing_missing'):
        executor.prepare(actor,payload(message='继续生成',conversation_id=first['conversation_id']))
    assert [g['storage_key'] for g in executor.main.product_store.task(first['id'],actor.tenant_id,actor.user_id)['generations']]==before


def test_unknown_outcome_completed_and_running_not_retried(image_fixture):
    executor,actors,calls=image_fixture;actor=actors[0]
    first=run_batch(executor,actor,unknown=(1,))
    with pytest.raises(ImageRequestError,match='preview_image_outcome_unknown'):
        executor.prepare(actor,payload(message='补第2张',conversation_id=first['conversation_id']))
    with pytest.raises(ImageRequestError,match='preview_image_nothing_missing'):
        executor.prepare(actor,payload(message='补第1张',conversation_id=first['conversation_id']))
    queued=executor.submit(actor,payload(message='再来一版',conversation_id=first['conversation_id']))
    with pytest.raises(ImageRequestError,match='preview_image_context_busy'):
        executor.prepare(actor,payload(message='再来一版',conversation_id=first['conversation_id']))
    assert queued['status']=='queued' and executor.budget.summary()['image']['requests']==0


def test_project_tenant_user_and_explicit_parent_isolation(image_fixture):
    executor,actors,calls=image_fixture;actor=actors[0]
    a=run_batch(executor,actor,'生成1张竖版教师海报')
    b=run_batch(executor,actor,'生成1张方形风景图')
    for other in actors[1:]:
        with pytest.raises(LookupError):executor.prepare(other,payload(message='再来一版',conversation_id=a['conversation_id']))
    with pytest.raises(ImageRequestError):executor.prepare(actor,payload(message='再来一版'))
    with pytest.raises(ImageRequestError):executor.prepare(actor,payload(message='标题改一下',conversation_id=b['conversation_id'],edit_generation_id=a['generations'][0]['id']))
    p=executor.prepare(actor,payload(message='再来一版',conversation_id=b['conversation_id']))
    assert p['parent']['task_id']==b['id'] and '教师' not in p['creative_brief']
    c=executor.prepare(actor,payload(message='新主题，做1张横版活动图',conversation_id=a['conversation_id']))
    assert c['action']=='initial' and c['parent'] is None


def test_preflight_digest_and_multi_turn_uses_latest_version(image_fixture):
    executor,actors,calls=image_fixture;actor=actors[0]
    first=run_batch(executor,actor,'生成1张横版教育海报')
    req=payload(message='再来一版',conversation_id=first['conversation_id'])
    req['preview_plan_digest']=executor.prepare(actor,req)['plan_digest']
    second=run_batch(executor,actor,'标题改成欢迎体验',conversation_id=first['conversation_id'])
    with pytest.raises(ImageRequestError) as caught:executor.submit(actor,req)
    assert caught.value.code=='preview_image_context_changed'
    third=executor.prepare(actor,payload(message='人物真实一点',conversation_id=first['conversation_id']))
    assert third['parent']['task_id']==second['id'] and '欢迎体验' in third['creative_brief']


@pytest.mark.parametrize('refs,count',[(0,3),(3,1),(2,3),(5,5)])
def test_canonical_plan_reaches_real_mcp_transport_fixture(image_fixture,monkeypatch,refs,count):
    executor,actors,calls=image_fixture;actor=actors[0]
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    monkeypatch.setenv('ENTERPRISE_POC_DATA_DIR',str(executor.budget.path.parent))
    monkeypatch.setenv(executor.main.settings.image_api_key_env,'FIXTURE_ONLY_NOT_A_REAL_KEY')
    uploads=[upload_reference(executor,actor) for _ in range(refs)]
    request=payload(message=f'上传{refs}张参考图，生成{count}张横版活动图，不要Logo。'+('第一张签到，第二张课堂，第三张咨询' if count==3 else ''),attachments=[dict(type='image',id=r['id'],role='style') for r in uploads])
    prepared=executor.prepare(actor,request)
    task=executor.submit(actor,{**request,'preview_plan_digest':prepared['plan_digest']})
    observed=[];output=io.BytesIO();Image.new('RGB',(256,256),'blue').save(output,'PNG')
    class Client:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            observed.append((url,kwargs))
            return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(output.getvalue()).decode()}]})
    monkeypatch.setattr(httpx,'AsyncClient',Client)
    token=executor.main.token_issuer.issue(RuntimePrincipal(actor.tenant_id,'image-agent','fixture',('image:generate',),int(time.time())+60))
    scope='runtime:'+executor.main.token_issuer.issue_task_scope(actor.tenant_id,task['id'])
    service=PlatformMCPService(executor.main.store,executor.main.token_issuer,executor.main.settings)
    result=asyncio.run(service.image_generation(token,task['input_text'],[],'16:9',execution_scope=scope,reference_images=[r['id'] for r in uploads],size='3840x2160',quality='high'))
    assert len(observed)==count==len(result['results'])==len(stored_plan(executor.submission(task['id']),task))
    for url,args in observed:
        body=args.get('json') or args['data']
        assert str(body['n'])=='1' and body['size']=='3840x2160' and body['quality']=='high'
        assert ('/edits' in url)==bool(refs)
        if refs: assert len(args['files'])==refs
    assert calls==[] and executor.budget.summary()['image']['requests']==0


def test_shared_production_count_contract_untouched():
    # The old stricter Production parser is intentionally not replaced globally.
    with pytest.raises(ImageRequestError):requested_count('生成3张，第一张招生，第二张课程，第三张品牌')


def test_legacy_accepted_contract_and_followup_size():
    task=dict(id='fixture',tenant_id='preview-fixture',user_id='u',input_text='生成2张1024x1024图片')
    plan=stored_plan(dict(size='1024x1024',requested_outputs=2),task)
    assert len(plan)==2 and plan[0]['size']=='1024x1024'
    assert make_plan('标题改一下',inherited='1536x1024')[0][0]['size']=='3840x2160'


def test_frontend_removes_select_and_sends_context_digest():
    from pathlib import Path
    source=(Path(__file__).parents[1]/'app/static/customer-preview.js').read_text(encoding='utf-8')
    assert "select.id='preview-image-size'" not in source
    assert 'conversation_id:context?.conversationId' in source
    assert 'preview_plan_digest:imageConsent.digest' in source
    assert "quality:'high'" in source


def test_preflight_rejects_foreign_reference_before_task_creation(image_fixture):
    executor,actors,calls=image_fixture
    foreign=upload_reference(executor,actors[1])
    with pytest.raises(ImageRequestError):executor.prepare(actors[0],payload(attachments=[dict(type='image',id=foreign['id'])]))
    assert executor.budget.summary()['image']['requests']==0
