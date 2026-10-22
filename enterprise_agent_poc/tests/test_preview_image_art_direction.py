"""Prompt/transport fixtures only: no real images or visual-quality claim."""
import asyncio
import base64
import io
import time

import httpx
from PIL import Image
import pytest

from app.preview_image_art_direction import instructions
from app.preview_image_intent import make_plan,stored_plan
from app.platform_mcp.service import PlatformMCPService
from app.security import RuntimePrincipal
from test_preview_image_execution import image_fixture,payload


@pytest.mark.parametrize('message,count',[
    ('生成一张招生竖版海报',1),('做一个三折页',2),
    ('做3张，第一张招生，第二张课程，第三张品牌',3),
    ('做一张卡通风格的横版图',1),('只把标题改成看见成长，其他保持不变',1),
])
def test_new_plans_have_effective_design_guidance_without_changing_intent(message,count):
    plan,_=make_plan(message)
    assert len(plan)==count
    for item in plan:
        assert message in item['prompt'] and item['prompt'].count('# Preview图片设计指导')==1
        assert item['prompt_version']=='preview-image-intent-v2-art-direction'
        assert len(item['prompt'])<20000
    text=instructions()
    for boundary in ['稳定网格','品牌主色','自然纪实','不把所有任务强制改成一种风格','只在允许修改的区域执行','不擅自删掉必需内容','原始透明PNG','不加白色底板','不得编造']:
        assert boundary in text


def test_stored_history_plan_is_not_rewritten_by_new_default():
    task={'id':'old','tenant_id':'preview-fixture','user_id':'fixture'}
    item={'index':0,'purpose':'原用途','prompt':'原始已授权Prompt','size':'2048x2048','aspect_ratio':'1:1','prompt_version':'preview-image-intent-v1'}
    submission={**task,'task_id':'old','output_plan':[item],'requested_outputs':1}
    assert stored_plan(submission,task)==[item]
    assert instructions() not in item['prompt']


def test_saved_plan_guidance_reaches_existing_provider_payload_fixture(image_fixture,monkeypatch):
    executor,actors,calls=image_fixture;actor=actors[0]
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    monkeypatch.setenv('ENTERPRISE_POC_DATA_DIR',str(executor.budget.path.parent))
    monkeypatch.setenv(executor.main.settings.image_api_key_env,'FIXTURE_ONLY_NOT_A_REAL_KEY')
    task=executor.submit(actor,payload(message='生成1张方形品牌海报，不要Logo。',use_enterprise_context=False))
    plan=stored_plan(executor.submission(task['id']),task)
    output=io.BytesIO();Image.new('RGB',(2048,2048),'white').save(output,'PNG')
    captured=[]
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            captured.append(kwargs.get('json') or kwargs.get('data'))
            return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(output.getvalue()).decode()}]})
    monkeypatch.setattr(httpx,'AsyncClient',Client)
    token=executor.main.token_issuer.issue(RuntimePrincipal(actor.tenant_id,'image-agent','fixture',('image:generate',),int(time.time())+60))
    scope='runtime:'+executor.main.token_issuer.issue_task_scope(actor.tenant_id,task['id'])
    result=asyncio.run(PlatformMCPService(executor.main.store,executor.main.token_issuer,executor.main.settings).image_generation(token,task['input_text'],[],plan[0]['aspect_ratio'],size=plan[0]['size'],quality='high',execution_scope=scope,reference_images=[]))
    assert len(captured)==len(result['results'])==1
    assert instructions() in captured[0]['prompt'] and captured[0]['quality']=='high'
    assert captured[0]['model']==executor.main.settings.image_model_id
    assert captured[0]['size']=='2048x2048' and int(captured[0]['n'])==1
    assert calls==[] and executor.budget.summary()['image']['requests']==0
