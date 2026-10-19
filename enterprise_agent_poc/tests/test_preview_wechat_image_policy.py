"""Fixture-only ten-image article planning and per-request aspect ratio."""
import asyncio
import base64
from decimal import Decimal
import io
import json
from zipfile import ZipFile

import httpx
from PIL import Image
import pytest

from test_preview_image_execution import image_fixture
from test_preview_illustrations import setup,completed_task,picture
from test_preview_qwen_experience_limits import experience
from app.preview_illustration_provider import IllustrationProvider,IllustrationConfig,IllustrationError,validated_image


def article(chapters):
    return '<article><h1>Fixture文章</h1><p>'+('真实素材。'*65)+'</p>'+''.join(f'<h2>章节{i}</h2><p>Fixture正文{i}。</p>' for i in range(chapters))+'</article>'


def authorize(service,actors):
    experience(service,actors)
    service.authorization['request_max_cny']='0.20'
    service.authorization['agent_max_images_per_task']['preview-wechat-agent']=10


@pytest.mark.parametrize('chapters,count',[(2,3),(6,7),(9,10),(12,10)])
def test_heading_planning_up_to_ten_at_four_by_three(setup,chapters,count):
    service,actors,_,_=setup;authorize(service,actors)
    task=completed_task(service,actors[0],'wechat',article(chapters))
    plan=service.plan(actors[0],task['id'])
    assert plan['count']==count and plan['can_generate']
    assert Decimal(plan['maximum_cost_cny'])==Decimal('.20')*count
    assert all(i['size']=='1024x768' for i in plan['items'])
    assert len({i['position'] for i in plan['items']})==count
    assert service.plan_limits('activity')[0]==10
    assert service.plan(actors[0],task['id'])['id']==plan['id']


def test_ten_allowed_eleven_cannot_be_confirmed(setup):
    service,actors,_,_=setup;authorize(service,actors)
    task=completed_task(service,actors[0],'wechat',article(9));plan=service.plan(actors[0],task['id'])
    with service.db() as c:
        payload=json.loads(c.execute('select payload from illustration_plans where id=?',(plan['id'],)).fetchone()[0])
        payload['items'].append({**payload['items'][-1],'position':100,'label':'FIXTURE十一张'})
        c.execute('update illustration_plans set payload=? where id=?',(json.dumps(payload),plan['id']))
    assert service.get(actors[0],plan['id'])['blocker_code']=='ILLUSTRATION_TASK_LIMIT'
    with pytest.raises(IllustrationError) as error:service.confirm(actors[0],plan['id'],plan['digest'],True)
    assert error.value.code=='ILLUSTRATION_TASK_LIMIT' and service.capability()['reserved_requests']==0


@pytest.mark.parametrize('user_request,size',[('图片比例16:9','1024x576'),('配图用9：16竖版','576x1024'),('1:1配图','1024x1024'),('未指定比例','1024x768')])
def test_explicit_user_ratio_wins_not_ratio_in_model_prose(setup,user_request,size):
    service,actors,_,_=setup;authorize(service,actors)
    task=completed_task(service,actors[0],'wechat',article(2)+'<p>模型正文提及图片比例1:1</p>')
    with service.main.store.connection() as c:c.execute('update tasks set input_text=? where id=?',(user_request,task['id']))
    plan=service.plan(actors[0],task['id'])
    assert all(i['size']==size for i in plan['items'])


def test_existing_planned_article_executes_new_default_and_history_stays_free(setup,monkeypatch):
    service,actors,old,budget=setup;authorize(service,actors);actor=actors[0]
    task=completed_task(service,actor,'wechat',article(2));plan=service.plan(actor,task['id'])
    with service.db() as c:
        payload=json.loads(c.execute('select payload from illustration_plans where id=?',(plan['id'],)).fetchone()[0])
        for i in payload['items']:i.pop('size')
        c.execute('update illustration_plans set payload=? where id=?',(json.dumps(payload),plan['id']))
    calls=[]
    async def generate(prompt,record_http,*,size):
        calls.append(size);record_http(200,'FIXTURE_ONLY')
        output=io.BytesIO();Image.new('RGB',(1024,768),(len(calls)*50,80,90)).save(output,format='PNG')
        return output.getvalue(),validated_image(output.getvalue())
    monkeypatch.setattr(service.provider,'generate',generate)
    service.confirm(actor,plan['id'],plan['digest'],True);asyncio.run(service.execute(actor,plan['id']))
    done=service.get(actor,plan['id']);assert done['status']=='completed'
    assert calls==['1024x768']*3 and all(a['dimension_match'] and a['requested_size']=='1024x768' for a in done['assets'])
    with ZipFile(io.BytesIO(service.export_article(actor,plan['id'],True))) as z:assert len([n for n in z.namelist() if n.startswith('images/')])==3
    service.get(actor,plan['id']);service.confirm(actor,plan['id'],plan['digest'],True)
    assert len(calls)==3 and old==[] and budget.summary()['image']['requests']==0


@pytest.mark.parametrize('size',[None,'1024x768','1024x576'])
def test_actual_adapter_http_body_preserves_request_override_and_activity_default(monkeypatch,size):
    seen=[];client=httpx.AsyncClient;raw=picture()
    def respond(request):
        seen.append(json.loads(request.content));return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(raw).decode()}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client(**{**kwargs,'transport':httpx.MockTransport(respond)}))
    provider=IllustrationProvider(IllustrationConfig(key='FIXTURE_ONLY',enabled=True))
    asyncio.run(provider.generate('FIXTURE',lambda *a:None,**({'size':size} if size else {})))
    assert len(seen)==1 and seen[0]['size']==(size or '1024x1024')
    assert seen[0]['model']=='qwen-image-3.0' and seen[0]['watermark'] is True


def test_unknown_size_rejected_before_provider_transport(monkeypatch):
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:pytest.fail('No HTTP allowed'))
    with pytest.raises(IllustrationError) as error:
        asyncio.run(IllustrationProvider(IllustrationConfig(key='FIXTURE_ONLY',enabled=True)).generate('FIXTURE',lambda *a:None,size='9999x9999'))
    assert error.value.code=='ILLUSTRATION_SIZE_UNSUPPORTED'
