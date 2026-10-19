"""Owner-authorized 1000/.20/200 contract; FIXTURE_ONLY, no real spend."""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest
from test_preview_image_execution import image_fixture
from test_preview_illustrations import setup,completed_task,enable_fixture_provider,picture
from test_preview_qwen_experience_limits import experience,activity
from app.preview_illustration_provider import IllustrationError,validated_image


def approve(service,actors):
    experience(service,actors)
    service.authorization.update(max_requests=1000,max_total_cny='200.00',request_max_cny='0.20',
                                 estimated_request_cny='0.20',automatic_after_text=True)
    service.authorization['agent_max_images_per_task']['preview-wechat-agent']=10


@pytest.mark.parametrize('kind,count',[('activity',7),('activity',10),('wechat',4)])
def test_automatic_whole_plan_reservation_once_no_history_generation(setup,monkeypatch,kind,count):
    service,actors,old,budget=setup;approve(service,actors);actor=actors[0]
    task=activity(service,actor,count) if kind=='activity' else completed_task(service,actor,'wechat')
    calls=enable_fixture_provider(service,monkeypatch)
    async def run():
        first=service.auto_after_text(actor,task['id'])
        service.auto_after_text(actor,task['id'])
        with service.db() as c:
            reserved=c.execute('select ceiling,status from illustration_requests').fetchall()
        assert len(reserved)==count and sum((Decimal(r[0]) for r in reserved),Decimal(0))==Decimal('.20')*count
        await asyncio.gather(*list(service.jobs))
        return first
    plan=asyncio.run(run());done=service.current(actor,task['id'])
    assert done['status']=='completed' and len(calls)==count
    if kind=='wechat':
        assert done['html'].count('<img ')==3 and done['publication']['cover']['role']=='cover'
        service.export_article(actor,plan['id'],True)
    else:assert done['document']['download_url']
    before=service.capability()['reserved_requests']
    for _ in range(3):service.current(actor,task['id']);service.plan(actor,task['id'])
    assert service.capability()['reserved_requests']==before and len(calls)==count
    assert service.task_restriction(actor,task['id']) is None
    assert old==[] and budget.summary()['text']['requests']==0


def test_activity_eleven_is_explicitly_rejected_before_any_reservation(setup):
    service,actors,_,_=setup;approve(service,actors)
    task=activity(service,actors[0],11);plan=service.auto_after_text(actors[0],task['id'])
    assert plan['status']=='skipped' and plan['error_code']=='ILLUSTRATION_TASK_LIMIT'
    assert service.capability()['reserved_requests']==0


@pytest.mark.parametrize('allowlist',[None,[],['preview-other'],['preview-fixture-a',None],['not-a-preview-tenant']])
def test_unconfirmed_anonymous_or_missing_malformed_list_fails_closed(setup,allowlist):
    service,actors,_,_=setup;approve(service,actors)
    if allowlist is None:service.authorization.pop('allowed_tenant_ids')
    else:service.authorization['allowed_tenant_ids']=allowlist
    assert not service.actor_allowed(actors[0])
    assert not service.capability(actors[0])['enabled']
    task=completed_task(service,actors[0],'wechat')
    result=service.auto_after_text(actors[0],task['id'])
    assert result['status']=='skipped' and result['error_code']=='ILLUSTRATION_TENANT_NOT_AUTHORIZED'
    assert service.capability()['reserved_requests']==0


def test_new_unlisted_visitor_is_not_implicitly_added(setup):
    service,actors,_,_=setup;approve(service,actors)
    before=json.dumps(service.authorization,sort_keys=True)
    assert not service.actor_allowed(SimpleNamespace(tenant_id='preview-future-visitor'))
    assert before==json.dumps(service.authorization,sort_keys=True)
    for actor in actors:assert service.actor_allowed(actor)


def test_two_tenants_share_global_four_but_each_stays_at_two(setup,monkeypatch):
    service,actors,_,_=setup;approve(service,actors)
    # Two users in tenant A plus one in tenant B exercise tenant, not user limits.
    owners=[actors[0],actors[2],actors[1]]
    tasks=[activity(service,a,7) for a in owners]
    active=Counter();peaks=Counter();global_active=0;global_peak=0;calls=[]
    async def generate(actor,identifier,row,payload,index,item):
        nonlocal global_active,global_peak
        active[actor.tenant_id]+=1;global_active+=1
        peaks[actor.tenant_id]=max(peaks[actor.tenant_id],active[actor.tenant_id]);global_peak=max(global_peak,global_active)
        calls.append((identifier,index));await asyncio.sleep(.01)
        active[actor.tenant_id]-=1;global_active-=1
    monkeypatch.setattr(service,'generate_one',generate)
    monkeypatch.setattr(service,'finish_export',lambda *a:None)
    async def run():
        for actor,task in zip(owners,tasks):service.auto_after_text(actor,task['id'])
        await asyncio.gather(*list(service.jobs))
    asyncio.run(run())
    assert global_peak==4 and set(peaks.values())=={2}
    assert len(calls)==21 and len(set(calls))==21
    assert service.capability()['reserved_requests']==21


@pytest.mark.parametrize('same_task',[False,True])
def test_atomic_parallel_reservation_never_exceeds_money_or_count(setup,same_task):
    service,actors,_,_=setup;approve(service,actors);actor=actors[0]
    tasks=[activity(service,actor,7),activity(service,actor,7)]
    plans=[service.plan(actor,t['id']) for t in tasks]
    with service.db() as c:
        for i in range(993):c.execute('insert into illustration_requests(id,plan_id,item_index,ceiling,status,created) values(?,?,?,?,?,?)',
            (f'old-{i}','OLD_PLAN',i,'0.20','failed',i))
        old=[tuple(r) for r in c.execute("select * from illustration_requests where plan_id='OLD_PLAN' order by id")]
    def reserve(index):
        p=plans[0] if same_task else plans[index]
        try:return service.confirm(actor,p['id'],p['digest'],True)['status']
        except IllustrationError as e:return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(reserve,[0,1]))
    assert outcomes.count('queued')==(2 if same_task else 1)
    if not same_task:assert outcomes.count('ILLUSTRATION_BUDGET_EXHAUSTED')==1
    cap=service.capability();assert cap['reserved_requests']==1000 and cap['remaining_requests']==0
    assert Decimal(cap['reserved_ceiling_cny'])==Decimal('200.00')
    with service.db() as c:assert old==[tuple(r) for r in c.execute("select * from illustration_requests where plan_id='OLD_PLAN' order by id")]
