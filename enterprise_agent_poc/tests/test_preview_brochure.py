"""Owner's actual failed wording, isolated Tasks/HTTP only, zero paid calls."""
import io
import asyncio
import base64
import time
import httpx
import pytest
from PIL import Image,ImageDraw
from app.preview_image_intent import make_plan,stored_plan,action_for
from app.preview_image_layout import overlay_preview_logo,logo_geometry
from app.image_request_contract import ImageRequestError
from app.platform_mcp.service import PlatformMCPService
from app.security import RuntimePrincipal
from test_preview_image_execution import image_fixture,payload
from test_preview_image_intent import run_batch


@pytest.mark.parametrize('words',[
 '生成两个图','两张图','两幅图','一个正面一个反面','正面一张，反面一张',
 '正反面分别生成','三折页正反两面分别输出','第一张正面，第二张反面',
 '一张正面一张反面','一正一反','正反面各一张','不是这个意思，我要分两张图，不要合成一张',
])
def test_natural_two_outputs(words):
    plan,_=make_plan(words,base='教育主题三折页：看见孩子天赋，点亮成长未来')
    assert len(plan)==2 and [p['layout_mode'] for p in plan]==['brochure_front','brochure_back']
    assert all(p['size']=='1536x1024' and p['aspect_ratio']=='3:2' for p in plan)
    assert '三个折页面板' in plan[0]['prompt'] and '右侧封面' in plan[0]['prompt']


@pytest.mark.parametrize('words',[
 '我的意思是生成两个图，一个正面一个反面。','我说的是两个图','不是这个意思，我要分两张图，不要合成一张。',
 '分开出','正反面分开出','一张正面一张反面',
])
def test_actual_two_turn_correction_plan_and_history(image_fixture,words):
    executor,actors,calls=image_fixture;actor=actors[0]
    first=run_batch(executor,actor,'设计一个教育主题三折页，主题看见孩子天赋，点亮成长未来。')
    request=payload(message=words,conversation_id=first['conversation_id'])
    prepared=executor.prepare(actor,request)
    assert prepared['action']=='clarify' and len(prepared['output_plan'])==2
    assert all('看见孩子天赋' in p['prompt'] and '最新明确张数' in p['prompt'] for p in prepared['output_plan'])
    task=executor.submit(actor,{**request,'preview_plan_digest':prepared['plan_digest']})
    asyncio.run(executor.execute(task))
    saved=executor.main.product_store.task(task['id'],actor.tenant_id,actor.user_id)
    assert saved['status']=='completed' and len(saved['generations'])==2
    assert [p['purpose'] for p in stored_plan(executor.submission(task['id']),task)]==['三折页正面展开图','三折页反面展开图']
    assert len(executor.main.product_store.conversation_detail(actor.tenant_id,actor.user_id,first['conversation_id'])['generations'])==4
    assert calls==[] and executor.budget.summary()['image']['requests']==0


@pytest.mark.parametrize('words',['另一面也生成出来','补一张反面','还缺背面'])
def test_unplanned_back_is_one_new_output_not_retry_of_front(image_fixture,words):
    executor,actors,calls=image_fixture;actor=actors[0]
    first=run_batch(executor,actor,'生成1张教育三折页正面，主题看见孩子天赋。')
    next_task=executor.submit(actor,payload(message=words,conversation_id=first['conversation_id']))
    sub=executor.submission(next_task['id']);plan=stored_plan(sub,next_task)
    assert sub['action']=='side_completion' and len(plan)==1 and plan[0]['layout_mode']=='brochure_back'
    assert '看见孩子天赋' in plan[0]['prompt'] and sub['references'][0]['role']=='style'
    asyncio.run(executor.execute(next_task));assert len(executor.main.product_store.task(first['id'],actor.tenant_id,actor.user_id)['generations'])==1


def test_planned_failed_back_uses_recovery_and_never_replays_front(image_fixture):
    executor,actors,calls=image_fixture;actor=actors[0]
    first=run_batch(executor,actor,'教育三折页正反面分别输出',failed=(1,))
    prepared=executor.prepare(actor,payload(message='另一面也生成出来',conversation_id=first['conversation_id']))
    assert prepared['action']=='recovery' and prepared['root_output_indices']==[1]
    assert prepared['output_plan'][0]['layout_mode']=='brochure_back'
    unknown=run_batch(executor,actor,'教育三折页正反面分别输出',unknown=(1,))
    with pytest.raises(ImageRequestError,match='preview_image_outcome_unknown'):
        executor.prepare(actor,payload(message='还缺背面',conversation_id=unknown['conversation_id']))


@pytest.mark.parametrize('words',['这个logo位置不协调，而且画面尺寸有点变形，优化一下。','继续改刚才那个','基于上一版调一下logo','这个比例不对','做成更像真实三折页'])
def test_layout_optimization_keeps_latest_face_and_context(image_fixture,words):
    executor,actors,calls=image_fixture;actor=actors[0]
    first=run_batch(executor,actor,'教育三折页，主题看见孩子天赋，点亮成长未来。')
    prepared=executor.prepare(actor,payload(message=words,conversation_id=first['conversation_id']))
    plan=prepared['output_plan'];assert prepared['action']=='edit' and len(plan)==1
    assert plan[0]['layout_mode']=='brochure_back' and plan[0]['size']=='1536x1024'
    assert '看见孩子天赋' in plan[0]['prompt']
    if 'logo' in words:assert plan[0]['logo_optimized']


@pytest.mark.parametrize('mode',['brochure_front','brochure_back'])
def test_original_logo_contained_in_reserved_brand_header_without_stretch(mode):
    source=io.BytesIO();Image.new('RGB',(1536,1024),'lightgray').save(source,'PNG')
    logo=Image.new('RGBA',(240,80),(0,0,0,0));ImageDraw.Draw(logo).rectangle((4,4,235,75),fill=(200,30,50,230))
    raw=io.BytesIO();logo.save(raw,'PNG');content,where=overlay_preview_logo(source.getvalue(),raw.getvalue(),mode)
    assert where['height']+where['y']<1024*.16
    assert abs(where['logo_width']/where['logo_height']-3)<.05
    with Image.open(io.BytesIO(content)) as result:
        assert result.size==(1536,1024)
        assert result.getpixel((700,700))[:3]==(211,211,211)
    if mode=='brochure_front':assert where['x']>1024
    else:assert where['x']<512


def test_brochure_exception_does_not_change_standard_poster_sizes_or_production_logo():
    assert make_plan('竖版教育海报')[0][0]['size']=='2160x3840'
    assert make_plan('1张三折页，明确要求9:16')[0][0]['size']=='2160x3840'
    assert make_plan('一张方形海报')[0][0]['size']=='2048x2048'
    assert action_for('新项目，生成两张教育图')=='initial'
    assert make_plan('改成一张竖版海报',base='原来是教育三折页')[0][0]['layout_mode']=='poster'


@pytest.mark.parametrize('mode',['brochure_front','brochure_back'])
def test_provider_painted_brand_is_removed_only_in_reserved_header(mode):
    source=Image.new('RGB',(1536,1024),'lightgray');draw=ImageDraw.Draw(source)
    left=1024 if mode=='brochure_front' else 0
    draw.rectangle((left+10,10,left+500,160),fill='blue')
    body=io.BytesIO();source.save(body,'PNG')
    logo=Image.new('RGBA',(240,80),(0,0,0,0));ImageDraw.Draw(logo).rectangle((4,4,235,75),fill='red')
    raw=io.BytesIO();logo.save(raw,'PNG')
    content,where=overlay_preview_logo(body.getvalue(),raw.getvalue(),mode)
    with Image.open(io.BytesIO(content)) as result:
        assert result.getpixel((left+450,80))[:3]==(255,255,255)
        assert result.getpixel((left+450,200))[:3]==(211,211,211)
        assert result.getpixel((700,700))[:3]==(211,211,211)
    assert where['owned_header'] and where['policy']=='preview-brand-zone-v2'


def test_canonical_brochure_two_real_mcp_fixture_posts_and_original_logo(image_fixture,monkeypatch):
    executor,actors,calls=image_fixture;actor=actors[0]
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true');monkeypatch.setenv('ENTERPRISE_POC_DATA_DIR',str(executor.budget.path.parent))
    monkeypatch.setenv(executor.main.settings.image_api_key_env,'FIXTURE_ONLY_NOT_A_REAL_KEY')
    with executor.main.store.connection() as c:c.execute('update enterprise_configs set payload=? where tenant_id=?',('{"brand_logo":{"fixture":true}}',actor.tenant_id))
    logo=io.BytesIO();im=Image.new('RGBA',(240,80),(0,0,0,0));ImageDraw.Draw(im).rectangle((5,5,234,74),fill='red');im.save(logo,'PNG')
    monkeypatch.setattr('app.platform_mcp.service.BrandLogoService.current_bytes',lambda *args:logo.getvalue())
    request=payload(message='设计教育主题三折页，生成两个图，一个正面一个反面。主题看见孩子天赋，点亮成长未来。')
    task=executor.submit(actor,request);plan=stored_plan(executor.submission(task['id']),task)
    captured=[];output=io.BytesIO();Image.new('RGB',(1536,1024),'white').save(output,'PNG')
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
    assert len(captured)==len(result['results'])==len(plan)==2
    assert all(b['size']=='1536x1024' and str(b['n'])=='1' for b in captured)
    assert [r['layout_mode'] for r in result['results']]==['brochure_front','brochure_back']
    assert result['results'][0]['brand_placement']['x']>1024 and result['results'][1]['brand_placement']['x']<512
    assert all(r['dimension_match'] and r['brand_logo_applied'] for r in result['results'])
    assert executor.budget.summary()['image']['requests']==0 and calls==[]
