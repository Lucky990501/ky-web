"""FIXTURE: normal six-step and complex ten-step plans auto-illustrate once."""
import asyncio,copy,json
import pytest
from app.domain import RuntimeProfile
from app.activity_plan_runtime import PLAN_SCHEMA
from app.preview_activity_pending import planning_schema
from app.preview_long_output import apply_policy
from test_activity_plan_runtime import ScriptedProvider,plan,full_request
from test_preview_image_execution import image_fixture
from test_preview_illustrations import setup,enable_fixture_provider
from test_preview_qwen_experience_limits import experience,activity


@pytest.mark.parametrize('count',[6,10,11])
def test_preview_schema_and_server_limit_preserve_original_contract(monkeypatch,count):
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    original=copy.deepcopy(PLAN_SCHEMA)
    source=plan();base=source['activity_items'][0]
    source['activity_items']=[{**base,'name':f'FIXTURE环节{i+1}'} for i in range(count)]
    class Provider(ScriptedProvider):
        async def _stream_provider_turn(self,*args,output_schema=None,**kwargs):
            self.schema=output_schema
            async for event in super()._stream_provider_turn(*args,output_schema=output_schema,**kwargs):yield event
    provider=Provider([json.dumps(source,ensure_ascii=False)])
    profile=RuntimeProfile.build(tenant_id='preview-fixture-a',agent_id='campaign-agent',model_provider_id='deepseek',model_id='deepseek-flash',reasoning_effort='low',skill_manifest={})
    async def run():
        session=await provider.create_session(profile,'')
        return [e async for e in provider.stream_turn(session,full_request())][-1].turn
    turn=asyncio.run(run())
    assert provider.schema==planning_schema() and provider.schema['properties']['activity_items']['maxItems']==10
    assert PLAN_SCHEMA==original and len(provider.options)==1
    assert '约6个' in provider.options[0][0] and '最多10个' in provider.options[0][0]
    if count<=10:
        assert turn.status=='completed' and len(turn.structured_result['data']['activity_items'])==count
    else:
        assert turn.status=='failed' and turn.structured_result is None and not turn.text
        assert turn.structured_result_status=='activity_item_limit_failed'


def test_limited_schema_retains_long_output_policy():
    body={'model':'deepseek-flash','max_output_tokens':4096,'text':{'format':{'type':'json_schema','schema':planning_schema()}},'reasoning':{'effort':'high'}}
    out,_=apply_policy(body,'campaign-agent')
    assert out['max_output_tokens']==16384 and out['reasoning']['effort']=='low'
    assert apply_policy(body,'campaign-agent',controlled=True)[0]==body


@pytest.mark.parametrize('count',[6,10])
def test_all_steps_generate_automatically_once_and_export_word_fixture(setup,monkeypatch,count):
    service,actors,old,budget=setup;experience(service,actors);actor=actors[0]
    service.authorization.update(automatic_after_text=True,request_max_cny='0.20')
    task=activity(service,actor,count);calls=enable_fixture_provider(service,monkeypatch)
    async def run():
        accepted=service.auto_after_text(actor,task['id']);assert accepted['status']=='queued'
        service.auto_after_text(actor,task['id'])
        await asyncio.gather(*list(service.jobs))
    asyncio.run(run());done=service.current(actor,task['id'])
    assert done['status']=='completed' and len(calls)==len(done['assets'])==count and done['document']['download_url']
    assert [x['position'] for x in done['assets']]==list(range(count))
    before=service.capability(actor)['reserved_requests'];service.current(actor,task['id'])
    assert service.capability(actor)['reserved_requests']==before
    assert not old and budget.summary()['image']['requests']==0


def test_existing_sixteen_step_plan_is_preserved_with_accurate_reason(setup):
    service,actors,old,budget=setup;experience(service,actors);actor=actors[0]
    service.authorization.update(automatic_after_text=True,request_max_cny='0.20')
    task=activity(service,actor,16)
    before=service.main.product_store.task(task['id'],actor.tenant_id,actor.user_id)['structured_result']
    result=service.auto_after_text(actor,task['id'])
    assert result['status']=='skipped' and result['error_code']=='ILLUSTRATION_TASK_LIMIT'
    assert '16' in result['user_message'] and '10' in result['user_message']
    assert service.main.product_store.task(task['id'],actor.tenant_id,actor.user_id)['structured_result']==before
    assert service.capability(actor)['reserved_requests']==0 and not old
