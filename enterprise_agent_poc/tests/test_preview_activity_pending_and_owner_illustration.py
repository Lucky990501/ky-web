"""FIXTURE_ONLY. No real model, image call, authorization or customer material."""
import asyncio
import copy
from io import BytesIO
import json
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import pytest

from app.activity_plan_runtime import semantic_guard
from app.document_generator import ActivityPlanContent,ActivityPlanDocumentService,DocumentPresentationOptions
from app.domain import RuntimeProfile
from app.preview_activity_pending import annotate_pending_benefit
from app.preview_illustration_provider import IllustrationError
from app.storage import LocalStorage
from test_activity_plan_runtime import ScriptedProvider,plan,full_request
from test_preview_illustrations import setup,completed_task
from test_preview_image_execution import image_fixture


def pending_plan():
    data=plan();data['pending_items']=['礼品种类及预算待确认']
    data['activity_items'][-1]['description']='参与者到达活动区。现场将提供礼品并安排权益领取。活动结束收集反馈。'
    return ActivityPlanContent.model_validate(data)


def test_only_flagged_clause_becomes_explicit_pending_without_other_changes(monkeypatch):
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    original=pending_plan();before=copy.deepcopy(original.model_dump());issues=semantic_guard(original)
    repaired=annotate_pending_benefit(original,issues,(),'preview-fixture')
    assert repaired is not None and semantic_guard(repaired)==[] and original.model_dump()==before
    after=repaired.model_dump();changed=after['activity_items'][-1]['description']
    assert '【待确认提案' in changed and '现场将提供礼品并安排权益领取' in changed
    assert changed.startswith('参与者到达活动区。') and changed.endswith('活动结束收集反馈。')
    after['activity_items'][-1]['description']=before['activity_items'][-1]['description']
    assert after==before


@pytest.mark.parametrize('kind',['not_preview','other_tenant','staff','invented_price','contradictory_condition'])
def test_outside_exact_branch_stays_closed(monkeypatch,kind):
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','false' if kind=='not_preview' else 'true')
    data=pending_plan().model_dump();tenant='tenant-a' if kind=='other_tenant' else 'preview-fixture'
    if kind=='staff':data['pending_items']=['工作人员待确认'];data['activity_items'][-1]['description']='现场将安排工作人员服务。'
    if kind=='invented_price':data['activity_items'][-1]['description']='现场将赠送价值1999元课程。'
    if kind=='contradictory_condition':data['activity_items'][-1]['description']='若礼品未到位，现场仍将赠送礼品。'
    original=ActivityPlanContent.model_validate(data)
    assert annotate_pending_benefit(original,semantic_guard(original),(),tenant) is None


def test_runtime_uses_one_model_result_revalidates_and_exports_docx(monkeypatch,tmp_path):
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    original=pending_plan();provider=ScriptedProvider([json.dumps(original.model_dump(),ensure_ascii=False)])
    profile=RuntimeProfile.build(tenant_id='preview-fixture',agent_id='campaign-agent',model_provider_id='deepseek',model_id='deepseek-v4-pro',reasoning_effort='high',skill_manifest={})
    async def run():
        session=await provider.create_session(profile,'FIXTURE_ONLY')
        return [e async for e in provider.stream_turn(session,full_request())]
    turn=asyncio.run(run())[-1].turn
    assert turn.structured_result_status=='validated_pending_annotation' and turn.structured_model_calls==1
    assert len(provider.options)==1 and turn.structured_result['type']=='activity_plan'
    assert any(s['stage']=='LOCAL_PENDING_REVALIDATION' and s['provider_calls_added']==0 for s in turn.structured_attempt_trace)
    corrected=ActivityPlanContent.model_validate(turn.structured_result['data']);assert not semantic_guard(corrected)
    storage=LocalStorage(tmp_path/'objects');service=ActivityPlanDocumentService(storage)
    doc=service.create('preview-fixture','fixture-user',corrected,DocumentPresentationOptions())
    content=storage.get(doc.storage_key);assert len(content)>1000
    with ZipFile(BytesIO(content)) as z:
        xml=ET.fromstring(z.read('word/document.xml'))
        text=''.join(xml.itertext())
        assert corrected.document_title in text and '待确认提案' in text
        assert all(item.name in text for item in corrected.activity_items)
        assert corrected.invitation_copy in text


def scope(service,actor,task_id):
    service.authorization={**service.authorization,'allowed_tenant_ids':[actor.tenant_id],
        'task_scoped_tenant_ids':[actor.tenant_id],'tenant_task_restrictions':[
            {'tenant_id':actor.tenant_id,'user_id':actor.user_id,'task_id':task_id,'max_images':3,'max_reserved_cny':'0.60'}]}


def test_owner_scope_allows_only_selected_article_and_never_calls_provider(setup):
    service,actors,old,budget=setup;actor=actors[0]
    selected=completed_task(service,actor,'wechat');other=completed_task(service,actor,'wechat')
    scope(service,actor,selected['id'])
    plan0=service.plan(actor,selected['id']);assert plan0['count']==3 and plan0['can_generate']
    blocked=service.plan(actor,other['id']);assert not blocked['can_generate'] and blocked['blocker_code']=='ILLUSTRATION_TASK_NOT_AUTHORIZED'
    with pytest.raises(IllustrationError):service.confirm(actor,blocked['id'],blocked['digest'],True)
    assert service.capability(actor)['reserved_requests']==0 and old==[]
    assert service.confirm(actor,plan0['id'],plan0['digest'],True)['status']=='queued'
    assert service.capability(actor)['reserved_requests']==3 and old==[]
    with service.db() as c:
        with pytest.raises(IllustrationError):service.check_task_allowance(actor,selected['id'],1,service.limits()[0],c)


def test_owner_scope_does_not_bypass_global_qwen_budget(setup):
    service,actors,old,budget=setup;actor=actors[0];task=completed_task(service,actor,'wechat');scope(service,actor,task['id'])
    service.authorization['max_requests']=1
    plan0=service.plan(actor,task['id']);assert not plan0['can_generate']
    with pytest.raises(IllustrationError):service.confirm(actor,plan0['id'],plan0['digest'],True)
    assert service.capability(actor)['reserved_requests']==0 and old==[]
