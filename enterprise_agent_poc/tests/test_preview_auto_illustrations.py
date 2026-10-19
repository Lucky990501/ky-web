"""Automatic completion hooks, bounded concurrency and degradation: Fixture only."""
import asyncio
import json
from pathlib import Path

import pytest
from test_preview_image_execution import image_fixture
from test_preview_illustrations import setup,completed_task,enable_fixture_provider,picture
from test_preview_qwen_experience_limits import experience,activity
from app.preview_illustration_provider import IllustrationError,validated_image


def authorize(service,actors):
    experience(service,actors);service.authorization['automatic_after_text']=True
    service.authorization['request_max_cny']='0.20'
    service.authorization['agent_max_images_per_task']['preview-wechat-agent']=10


@pytest.mark.parametrize('kind',['activity','wechat'])
def test_completion_automatically_generates_without_confirmation_and_history_is_readonly(setup,monkeypatch,kind):
    service,actors,old,budget=setup;authorize(service,actors);actor=actors[0]
    task=completed_task(service,actor,kind);calls=enable_fixture_provider(service,monkeypatch)
    async def run():
        accepted=service.auto_after_text(actor,task['id']);assert accepted['status']=='queued'
        service.auto_after_text(actor,task['id'])  # Duplicate callback never reserves again.
        await asyncio.gather(*list(service.jobs))
    asyncio.run(run());done=service.current(actor,task['id'])
    assert done['status']=='completed' and len(calls)==done['count']
    count=service.capability()['reserved_requests']
    service.current(actor,task['id']);service.get(actor,done['id'])
    assert service.capability()['reserved_requests']==count
    assert service.main.product_store.task(task['id'],actor.tenant_id,actor.user_id)['status']=='completed'
    if kind=='wechat':
        assert done['html'].count('<img ')==done['count']-1 and done['publication']['cover'] and len(service.export_article(actor,done['id'],True))>100
    else:assert done['document']['download_url']
    assert old==[] and budget.summary()['image']['requests']==0


@pytest.mark.parametrize('blocked',['tenant','count','money'])
def test_unavailable_images_do_not_fail_text_or_reserve(setup,blocked):
    service,actors,old,budget=setup;authorize(service,actors);actor=actors[0]
    task=completed_task(service,actor)
    if blocked=='tenant':service.authorization['allowed_tenant_ids']=[]
    elif blocked=='count':service.authorization['max_requests']=1
    else:service.authorization['max_total_cny']='0.20'
    result=service.auto_after_text(actor,task['id'])
    assert result['status']=='skipped' and service.capability()['reserved_requests']==0
    assert service.main.product_store.task(task['id'],actor.tenant_id,actor.user_id)['status']=='completed'
    assert old==[]


def test_bounded_parallelism_partial_images_visible_and_failure_stops_new_batches(setup,monkeypatch):
    service,actors,old,budget=setup;authorize(service,actors);actor=actors[0]
    task=activity(service,actor,7);calls=[];running=0;peak=0
    async def generate(prompt,record_http,**kwargs):
        nonlocal running,peak
        index=len(calls);calls.append(index);running+=1;peak=max(peak,running)
        await asyncio.sleep(.03)
        if index==1:
            running-=1;record_http(503,'FIXTURE');raise IllustrationError('ILLUSTRATION_PROVIDER_REJECTED','FIXTURE')
        running-=1;record_http(200,'FIXTURE');raw=picture('blue' if index==0 else 'orange');return raw,validated_image(raw)
    monkeypatch.setattr(service.provider,'generate',generate)
    async def run():
        result=service.auto_after_text(actor,task['id'])
        while len(calls)<3:await asyncio.sleep(.005)
        partial=service.get(actor,result['id']);assert len(partial['assets'])==1 and partial['status']=='generating'
        await asyncio.gather(*list(service.jobs))
        done=service.get(actor,result['id']);assert done['status']=='failed' and len(done['assets'])==2
        assert service.main.product_store.task(task['id'],actor.tenant_id,actor.user_id)['status']=='completed'
    asyncio.run(run());assert peak==2 and len(calls)==3


@pytest.mark.parametrize('label,terms',[
    ('签到接待',['签到','接待台','动作']),('公开课',['教师','学生','座位']),
    ('互动抽奖',['主持','互动','礼品']),('咨询答疑',['顾问','一对一','倾听']),('舞台开场',['舞台','人物'])])
def test_activity_prompts_specific_photographic_human_and_fact_safe(setup,label,terms):
    service,_,_,_=setup
    prompt=service.prompt({'环节':label,'说明':'真实需求','示意图需求':'展示该活动环节','目标人群':'高中生与家长'})
    for term in terms:assert term in prompt
    for term in ['摄影质感','人物','中国本土教育','不是真实活动照片','不要绘制品牌Logo','不要卡通插画']:
        assert term in prompt


def test_frontend_no_paid_generation_controls_and_preview_only_old_panel_guard():
    root=Path(__file__).resolve().parents[1]
    js=(root/'app/static/preview-illustrations.js').read_text(encoding='utf-8')
    for forbidden in ['window.confirm','确认生成文章配图','查看自动配图计划',"+'/generate'"]:
        assert forbidden not in js
    assert '配图生成中' in js and '部分配图未生成' in js
    assert '当前配图未执行：缺少当前Tenant的图片授权或预算' in js
    workbench=(root/'app/static/workbench.js').read_text(encoding='utf-8')
    assert 'function mountIllustrationBriefs(main,article,briefs,messageId){\n  if(window.WORKBENCH_PREVIEW_BASE)return;' in workbench
