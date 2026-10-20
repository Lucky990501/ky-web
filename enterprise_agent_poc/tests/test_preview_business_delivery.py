"""FIXTURE_ONLY: faithful missing-image states, no external model or bill."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.activity_plan_runtime import semantic_guard
from app.document_generator import ActivityPlanContent,ActivityPlanDocumentService,DocumentPresentationOptions
from app.preview_activity_pending import annotate_pending_resources,PREVIEW_PLANNING_INSTRUCTION
from app.preview_illustration_provider import IllustrationError
from app.preview_illustration_routes import illustration_request
from app.domain import RuntimeProfile
from app.storage import LocalStorage
from test_activity_plan_runtime import ScriptedProvider,full_request
from test_preview_image_execution import image_fixture
from test_preview_illustrations import setup,completed_task,enable_fixture_provider,picture
from test_preview_qwen_experience_limits import experience


def resource_plan(kind='staff'):
    data=json.loads((Path(__file__).parent/'fixtures/activity_plan_d1.json').read_text(encoding='utf-8'))
    data['activity_items']=[{'phase':'14:00','name':'签到接待','description':'现场将安排工作人员负责签到接待。其他已确认介绍保持不变。','image_requirement':'工作人员接待家长的写实场景'}]
    data['pending_items']=['工作人员人数与排班待确认']
    data['invitation_copy']='欢迎家长参加活动。'
    if kind=='material':data['pending_items']=['设备及物料待确认'];data['activity_items'][0]['description']='现场将提供设备进行互动。保留其他内容。'
    if kind=='service':data['pending_items']=['课程演示服务待确认'];data['activity_items'][0]['description']='将提供课程演示服务。保留其他内容。'
    if kind=='benefit':data['pending_items']=['礼品与预算待确认'];data['activity_items'][0]['description']='现场将赠送礼品。保留其他内容。'
    return ActivityPlanContent.model_validate(data)


@pytest.mark.parametrize('kind',['staff','material','service','benefit'])
def test_exact_pending_clause_conditioned_and_full_guard_revalidated(monkeypatch,kind,tmp_path):
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true');original=resource_plan(kind);violations=semantic_guard(original)
    assert violations
    repaired=annotate_pending_resources(original,violations,(),'preview-fixture')
    assert repaired and not semantic_guard(repaired)
    assert repaired.pending_items==original.pending_items
    for field in ['document_title','activity_theme','activity_time','activity_location','target_audience','invitation_copy']:
        assert getattr(repaired,field)==getattr(original,field)
    assert repaired.activity_items[0].name==original.activity_items[0].name
    assert repaired.activity_items[0].description.endswith(original.activity_items[0].description)
    storage=LocalStorage(tmp_path/'objects');doc=ActivityPlanDocumentService(storage).create('preview-fixture','fixture-user',repaired,DocumentPresentationOptions())
    with ZipFile(BytesIO(storage.get(doc.storage_key))) as z:assert z.testzip() is None and 'word/document.xml' in z.namelist()


@pytest.mark.parametrize('text',['已安排5名工作人员负责接待。','若工作人员未落实，仍将安排专人接待。','现场将安排工作人员赠送1999元课程。'])
def test_historical_claim_price_and_contradiction_remain_blocked(monkeypatch,text):
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true');p=resource_plan();p.activity_items[0].description=text
    violations=semantic_guard(p);assert violations
    assert annotate_pending_resources(p,violations,(),'preview-fixture') is None


def test_only_conflicting_activity_name_can_be_explicitly_pending(monkeypatch):
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true');p=resource_plan('benefit')
    p.activity_items[0].name='现场赠送礼品';p.activity_items[0].description='建议在礼品预算落实后执行，未落实则取消。'
    fixed=annotate_pending_resources(p,semantic_guard(p),(),'preview-fixture')
    assert fixed and fixed.activity_items[0].name=='【待确认提案】现场赠送礼品'
    assert fixed.activity_items[0].description==p.activity_items[0].description and not semantic_guard(fixed)


def test_preview_first_prompt_and_unrepairable_result_not_success(monkeypatch):
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true');p=resource_plan();p.activity_items[0].description='已安排5名工作人员负责接待。'
    provider=ScriptedProvider([json.dumps(p.model_dump(),ensure_ascii=False)])
    profile=RuntimeProfile.build(tenant_id='preview-fixture',agent_id='campaign-agent',model_provider_id='deepseek',model_id='deepseek-flash',reasoning_effort='high',skill_manifest={})
    async def run():
        session=await provider.create_session(profile,'FIXTURE_ONLY');return [e async for e in provider.stream_turn(session,full_request())]
    events=asyncio.run(run());turn=events[-1].turn
    assert turn.status=='failed' and turn.structured_result is None and not turn.text
    assert turn.error=='ACTIVITY_PLAN_VALIDATION_FAILED' and turn.structured_attempt_trace[-1]['format_valid']
    assert len(provider.options)==1
    assert '策划方案不等于资源已经落实' in provider.options[0][0]


@pytest.mark.parametrize('repairable',[True,False])
def test_actual_task_persistence_not_fallback_success(image_fixture,monkeypatch,repairable):
    from app.product_service import TaskService
    from app.service import AgentService
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    executor,actors,_=image_fixture;actor=actors[0];p=resource_plan()
    if not repairable:p.activity_items[0].description='已安排5名工作人员负责接待。'
    runtime=ScriptedProvider([json.dumps(p.model_dump(),ensure_ascii=False)])
    service=TaskService(executor.main.product_store,AgentService(executor.main.store,runtime,executor.main.settings))
    task=executor.main.product_store.create_task(actor.tenant_id,actor.user_id,'campaign-agent',full_request(),None)
    asyncio.run(service.execute(task))
    saved=executor.main.product_store.task(task['id'],actor.tenant_id,actor.user_id)
    assert saved['status']==('completed' if repairable else 'failed')
    if repairable:assert saved['structured_result']['type']=='activity_plan'
    else:assert not saved.get('structured_result') and not saved.get('final_response')


def seven(service,actor):
    experience(service,[actor]);service.authorization['max_requests']=1000;service.authorization['max_total_cny']='200';service.authorization['request_max_cny']='0.20';service.authorization['estimated_request_cny']='0.20'
    service.authorization['agent_max_images_per_task']={'preview-wechat-agent':10,'campaign-agent':10}
    html='<article><h1>FIXTURE_ONLY 原文章</h1><p>'+('教育文章原文。'*60)+'</p>'+''.join(f'<h2>第{i}章</h2><p>原始正文第{i}段。</p>' for i in range(5))+'</article>'
    t=completed_task(service,actor,'wechat',html);p=service.plan(actor,t['id']);assert p['count']==7
    service.confirm(actor,p['id'],p['digest'],True);return t,p


def legacy_four(service,actor,monkeypatch,unknown=False):
    task,plan=seven(service,actor);calls=enable_fixture_provider(service,monkeypatch)
    row=service.row(actor,plan['id']);payload=json.loads(row['payload'])
    async def first():
        for i in range(4):await service.generate_one(actor,plan['id'],row,payload,i,payload['items'][i])
    asyncio.run(first())
    with service.db() as c:
        c.execute("update illustration_requests set status='attempted',http_status=?,request_id=? where plan_id=? and item_index=4",(None if unknown else 503,'FIXTURE_OLD_REJECTION',plan['id']))
        c.execute("update illustration_plans set status='failed',error_code='ILLUSTRATION_PROVIDER_REJECTED' where id=?",(plan['id'],))
    return task,service.get(actor,plan['id']),calls


def test_one_rejection_does_not_stop_other_first_requests(setup,monkeypatch):
    service,actors,_,_=setup;actor=actors[0];_,plan=seven(service,actor)
    calls=enable_fixture_provider(service,monkeypatch,fail_index=2)
    asyncio.run(service.execute(actor,plan['id']))
    result=service.get(actor,plan['id'])
    assert len(calls)==7 and len(result['assets'])==6 and result['status']=='failed'
    assert sum(o['state']=='REJECTED' for o in result['outcomes'])==1
    assert not any(o['state']=='NOT_DISPATCHED' for o in result['outcomes'])
    assert result['recoverable_missing']==1


def test_all_seven_success_complete_html_zip_and_cover(setup,monkeypatch):
    service,actors,_,_=setup;actor=actors[0];_,p=seven(service,actor);calls=enable_fixture_provider(service,monkeypatch)
    asyncio.run(service.execute(actor,p['id']));done=service.get(actor,p['id'])
    assert done['status']=='completed' and len(done['assets'])==len(calls)==7 and done['publication']['cover']
    with ZipFile(BytesIO(service.export_article(actor,p['id'],True))) as z:
        assert len([n for n in z.namelist() if n.startswith('images/')])==7
        assert all(a['filename'].encode() in z.read('article.html') for a in done['assets'] if a.get('role')!='cover')


@pytest.mark.parametrize('initial_saved',[4,6])
def test_missing_only_recovery_preserves_original_assets_and_reservations(setup,monkeypatch,initial_saved):
    service,actors,_,_=setup;actor=actors[0]
    task,p,calls=legacy_four(service,actor,monkeypatch)
    if initial_saved==6:
        row=service.row(actor,p['id']);payload=json.loads(row['payload'])
        async def extra():
            for i in (5,6):await service.generate_one(actor,p['id'],row,payload,i,payload['items'][i])
        asyncio.run(extra());p=service.get(actor,p['id'])
    old_assets=copy.deepcopy(p['assets']);old_requests=service.request_rows(p['id']);before=service.capability()['reserved_requests']
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda _:service.recover(actor,p['id'],p['digest'],True),range(3)))
    new=results[0];assert len({r['id'] for r in results})==1
    assert service.capability()['reserved_requests']==before+1
    assert len(service.request_rows(new['id']))==7
    paid_before=len(calls);asyncio.run(service.execute(actor,new['id']))
    done=service.get(actor,new['id']);assert done['status']=='completed' and len(done['assets'])==7
    assert len(calls)-paid_before==7-initial_saved
    assert all(a in done['assets'] for a in old_assets)
    for i in range(4):assert service.request_rows(p['id'])[i]==old_requests[i]
    assert service.request_rows(p['id'])[4]==old_requests[4]
    assert service.request_rows(new['id'])[5]['id']==old_requests[5]['id']
    before=len(calls);repeat=service.recover(actor,p['id'],p['digest'],True)
    asyncio.run(service.execute(actor,repeat['id']));service.export_article(actor,new['id'],True)
    assert len(calls)==before and service.capability()['reserved_requests']==8
    assert service.source(actor,task['id'])['final_response']==service.source(actor,task['id'])['final_response']


def test_unknown_outcome_not_reposted_and_existing_reservations_not_charged_twice(setup,monkeypatch):
    service,actors,_,_=setup;actor=actors[0];_,p,calls=legacy_four(service,actor,monkeypatch,unknown=True)
    service.authorization['max_requests']=7;service.authorization['max_total_cny']='1.40'
    recovered=service.recover(actor,p['id'],p['digest'],True);before=len(calls)
    asyncio.run(service.execute(actor,recovered['id']));done=service.get(actor,recovered['id'])
    assert len(calls)-before==2 and service.capability()['reserved_requests']==7
    assert len(done['assets'])==6 and done['unresolved_missing']==1 and not done['can_recover_missing']
    assert done['outcomes'][4]['state']=='UNKNOWN'
    with pytest.raises(IllustrationError):service.recover(actor,done['id'],done['digest'],True)


def test_other_actor_cannot_recover_or_read_original_assets(setup,monkeypatch):
    service,actors,_,_=setup;_,p,_=legacy_four(service,actors[0],monkeypatch)
    with pytest.raises(LookupError):service.recover(actors[1],p['id'],p['digest'],True)
    for asset in p['assets']:
        with pytest.raises(LookupError):service.asset(actors[1],asset['id'])


def test_ui_has_explicit_missing_only_recovery_and_no_template_disclaimer():
    js=(Path(__file__).parents[1]/'app/static/preview-illustrations.js').read_text(encoding='utf-8')
    assert '补齐缺失图片' in js and "+'/recover'" in js and 'recover.disabled' in js
    assert 'AI示意图' not in js and 'AI概念示意' not in js
    assert "'role','status'" in js and 'unresolved_missing' in js


def test_activity_missing_picture_word_mapping_and_export_never_regenerates(setup,monkeypatch):
    service,actors,_,_=setup;actor=actors[0];experience(service,[actor])
    task=completed_task(service,actor,'activity');p=service.plan(actor,task['id']);service.confirm(actor,p['id'],p['digest'],True)
    calls=enable_fixture_provider(service,monkeypatch,fail_index=0);asyncio.run(service.execute(actor,p['id']))
    p=service.get(actor,p['id']);assert p['status']=='failed' and len(p['assets'])==1
    successor=service.recover(actor,p['id'],p['digest'],True);asyncio.run(service.execute(actor,successor['id']))
    done=service.get(actor,successor['id']);assert done['status']=='completed' and len(done['assets'])==2
    assert sorted(a['position'] for a in done['assets'])==[0,1]
    content=service.storage.get(done['document']['storage_key']) if 'storage_key' in done['document'] else None
    # Public document metadata intentionally hides its key. Use the real
    # document service's authenticated download contract instead.
    from app.document_generator import ActivityPlanDocumentService
    doc_service=ActivityPlanDocumentService(service.storage)
    doc=doc_service.get(actor.tenant_id,actor.user_id,done['document']['document_id'])
    raw=service.storage.get(doc.storage_key)
    with ZipFile(BytesIO(raw)) as z:
        assert len([n for n in z.namelist() if n.startswith('word/media/')])==2
        assert z.testzip() is None
    before=len(calls);service.finish_export(actor,done['id']);service.get(actor,done['id'])
    assert len(calls)==before


def test_saved_original_word_copy_failure_recovers_without_another_provider_call(setup,monkeypatch):
    import app.preview_illustration_service as module
    service,actors,_,_=setup;actor=actors[0];experience(service,[actor]);task=completed_task(service,actor,'activity')
    p=service.plan(actor,task['id']);service.confirm(actor,p['id'],p['digest'],True);calls=enable_fixture_provider(service,monkeypatch)
    original=module.word_image_copy
    def failed(_):raise IllustrationError('ILLUSTRATION_WORD_COPY_FAILED','FIXTURE_ONLY')
    monkeypatch.setattr(module,'word_image_copy',failed);asyncio.run(service.execute(actor,p['id']))
    assert len(calls)==2 and service.get(actor,p['id'])['unresolved_missing']==2
    monkeypatch.setattr(module,'word_image_copy',original)
    done=service.recover(actor,p['id'],p['digest'],True)
    assert done['status']=='completed' and len(done['assets'])==2 and len(calls)==2


def test_corrupt_old_image_blocks_new_paid_attempt(setup,monkeypatch):
    service,actors,_,_=setup;actor=actors[0];_,p,calls=legacy_four(service,actor,monkeypatch)
    before=len(calls);records=service.request_rows(p['id']);asset=json.loads(records[0]['asset_json'])
    service.storage.put(asset['storage_key'],b'FIXTURE_ONLY_CORRUPT','image/png')
    with pytest.raises(IllustrationError):service.recover(actor,p['id'],p['digest'],True)
    assert len(calls)==before and service.capability()['reserved_requests']==7


def test_recovery_http_origin_and_identity_then_idempotent_double_submit(setup,monkeypatch):
    import httpx
    from app.preview_illustration_service import PREFIX
    service,actors,_,_=setup;actor=actors[0];_,p,calls=legacy_four(service,actor,monkeypatch)
    async def app(scope,receive,send):await illustration_request(service,actor,scope,receive,send)
    scheduled=[];monkeypatch.setattr(service,'schedule',lambda a,i:scheduled.append(i))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
            url=PREFIX+'/plans/'+p['id']+'/recover';body={'digest':p['digest'],'confirmed':True}
            assert (await client.post(url,json=body,headers={'Origin':'http://other'})).status_code==403
            a=await client.post(url,json=body,headers={'Origin':'http://fixture'});assert a.status_code==202
            b=await client.post(url,json=body,headers={'Origin':'http://fixture'});assert b.status_code==202
            assert a.json()['id']==b.json()['id'] and len(calls)==4
    asyncio.run(run());assert service.capability()['reserved_requests']==8
