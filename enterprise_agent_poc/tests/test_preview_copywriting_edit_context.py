"""Zero-paid context/wiring fixtures, not real model quality acceptance."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from test_preview_image_execution import image_fixture

from app.preview_copywriting_prompt import editing_context,install,prompt,provider_request


def data(context):return json.loads(context.split('\n',1)[1])


@pytest.mark.parametrize('edit_request',[
    '写一个短视频脚本','改成短视频脚本','写成口播逐字稿','压缩到30秒','改得更有共鸣',
    '开头更吸引人','结尾自然一些','内容太长了','换一种表达方式','保留内容但调整语气'])
def test_edit_priority_latest_full_draft_and_user_fact_boundary(monkeypatch,edit_request):
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    draft='完整开头'+('旧稿正文。'*3000)+'完整结尾'
    messages=[{'role':'user','content':'易明教育高考冲刺文案，仅已确认：整理错题与复习计划。'},
              {'role':'assistant','content':draft}]
    context=editing_context(messages,edit_request);value=data(context)
    assert value['operation']=='EDIT_PREVIOUS_DRAFT'
    assert value['previous_draft']==draft and value['latest_user_request']==edit_request
    assert len(value['previous_draft'])>12000
    assert value['user_provided_materials_and_requests']==[messages[0]['content']]
    assert '模型旧稿，不是已核验企业事实' in context


def test_video_default_then_thirty_seconds_uses_second_draft(monkeypatch):
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    messages=[{'role':'user','content':'生成易明教育高考冲刺文案；仅提供复习计划指导，不承诺提分。'},
              {'role':'assistant','content':'FIXTURE_FIRST_DRAFT'}]
    second=data(editing_context(messages,'写一个短视频脚本'))
    assert second['target_seconds']=='45-60'
    messages += [{'role':'user','content':'写一个短视频脚本'},
                 {'role':'assistant','content':'FIXTURE_SECOND_VIDEO_DRAFT'}]
    third=data(editing_context(messages,'再缩短到30秒，开头更有冲突'))
    assert third['target_seconds']==30 and third['previous_draft']=='FIXTURE_SECOND_VIDEO_DRAFT'
    assert third['operation']=='EDIT_PREVIOUS_DRAFT'
    assert '不承诺提分' in third['original_user_goal']


def test_explicit_new_topic_does_not_force_old_draft(monkeypatch):
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    value=data(editing_context([{'role':'assistant','content':'高考旧稿'}],'换个主题，写家庭阅读口播'))
    assert value['operation']=='NEW_TOPIC' and value['previous_draft'] is None


def test_preview_only_and_prompt_not_duplicated(monkeypatch):
    monkeypatch.delenv('CUSTOMER_PREVIEW_MODE',raising=False)
    with pytest.raises(RuntimeError):editing_context([],'改短')
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    body={'instructions':prompt(),'input':[]}
    result,_=provider_request(body,{'agent_id':'copywriting-agent'})
    assert result['instructions'].count(prompt())==1
    for rule in ['多轮修改优先规则','EDIT_PREVIOUS_DRAFT','150～220','不回退到第一版','不是简单缩短或同义词替换']:
        assert rule in prompt()


def test_actual_service_three_turn_fixture_and_tenant_boundary(monkeypatch,tmp_path):
    from app.agent_catalog import CATALOG
    from app.domain import RuntimeSession,RuntimeTurn
    from app.service import AgentService
    from app.store import POCStore
    from app.product_store import ProductStore
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    monkeypatch.setitem(CATALOG,'copywriting-agent',CATALOG['copywriting-agent']);install()
    store=POCStore(tmp_path/'fixture.sqlite');store.initialize();products=ProductStore(store);products.initialize()
    with store.connection() as c:
        c.execute("INSERT INTO tenants VALUES ('preview-edit-fixture','Fixture','Fixture')")
        c.execute("INSERT INTO tenants VALUES ('preview-other-fixture','Other','Other')")
    contexts=[];turns=[]
    class Runtime:
        async def create_session(self,profile,developer_instructions,**kwargs):return RuntimeSession('fixture-thread',profile.id)
        async def resume_session(self,profile,thread,developer_instructions=None,recovery_context=None,**kwargs):
            contexts.append(data(recovery_context));return RuntimeSession(thread,profile.id)
        async def run_turn(self,session,message):
            turns.append(message)
            return RuntimeTurn(thread_id=session.thread_id,text='FIXTURE_DRAFT_'+str(len(turns)))
    settings=SimpleNamespace(model_provider_id='fixture',model_id='fixture',reasoning_effort='low',test_text_gate_enabled=False)
    service=AgentService(store,Runtime(),settings)
    first=asyncio.run(service.run('preview-edit-fixture','copywriting-agent','生成易明教育高考冲刺文案，真实素材：复习计划指导。'))
    # The real TaskService owns persistence; this isolated service Fixture supplies it.
    products.add_message(first.conversation_id,'user',turns[0]);products.add_message(first.conversation_id,'assistant',first.text)
    second=asyncio.run(service.run('preview-edit-fixture','copywriting-agent','写一个短视频脚本',first.conversation_id))
    products.add_message(first.conversation_id,'user',turns[1]);products.add_message(first.conversation_id,'assistant',second.text)
    third=asyncio.run(service.run('preview-edit-fixture','copywriting-agent','再缩短到30秒，开头更有冲突',first.conversation_id))
    assert first.conversation_id==second.conversation_id==third.conversation_id
    assert [v['previous_draft'] for v in contexts]==['FIXTURE_DRAFT_1','FIXTURE_DRAFT_2']
    assert [v['target_seconds'] for v in contexts]==['45-60',30]
    assert all(v['operation']=='EDIT_PREVIOUS_DRAFT' for v in contexts)
    assert len(turns)==3  # No automatic replay.
    with pytest.raises(LookupError):asyncio.run(service.run('preview-other-fixture','copywriting-agent','改短',first.conversation_id))
    assert len(turns)==3
    # Other agents and non-Preview retain the exact historical recovery path.
    old=service._recovery_context(first.conversation_id,'preview-edit-fixture','x',agent_id='campaign-agent')
    assert not old.startswith('Preview文案多轮编辑上下文')
    monkeypatch.delenv('CUSTOMER_PREVIEW_MODE')
    assert service._recovery_context(first.conversation_id,'preview-edit-fixture','x',agent_id='copywriting-agent')==old


def test_full_owned_task_result_restores_draft_after_message_limit(image_fixture,monkeypatch):
    from uuid import uuid4
    from app.preview_copywriting_prompt import conversation_editing_context
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    executor,actors,_=image_fixture;store=executor.main.store;products=executor.main.product_store;actor=actors[0]
    conversation=str(uuid4());store.save_conversation(conversation,actor.tenant_id,'copywriting-agent','FIXTURE','FIXTURE','FIXTURE')
    with store.connection() as c:c.execute('INSERT INTO conversation_owners(conversation_id,user_id) VALUES (?,?)',(conversation,actor.user_id))
    task=products.create_task(actor.tenant_id,actor.user_id,'copywriting-agent','FIXTURE',conversation)
    full='开头'+('完整可见逐字稿。'*2000)+'结尾'
    with store.connection() as c:
        c.execute("UPDATE tasks SET status='completed' WHERE id=?",(task['id'],))
        c.execute("INSERT INTO task_results(task_id,final_response,result_json) VALUES (?,?,'{}')",(task['id'],full))
    messages=[{'role':'user','content':'用户已确认素材'},{'role':'assistant','content':full[:12000]}]
    result=data(conversation_editing_context(store,conversation,actor.tenant_id,messages,'写一个短视频脚本'))
    assert len(full)>12000 and result['previous_draft']==full
    foreign=data(conversation_editing_context(store,conversation,actors[1].tenant_id,messages,'改短'))
    assert foreign['previous_draft']==full[:12000]  # Foreign query never expands content.
    with store.connection() as c:c.execute('UPDATE conversation_owners SET user_id=? WHERE conversation_id=?',(actors[2].user_id,conversation))
    mismatch=data(conversation_editing_context(store,conversation,actor.tenant_id,messages,'改短'))
    assert mismatch['previous_draft']==full[:12000]


def test_preview_codex_plaintext_resume_retains_full_edit_context(monkeypatch):
    from app.runtime.codex_provider import CodexRuntimeProvider
    from app.domain import RuntimeSession
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    runtime=CodexRuntimeProvider(SimpleNamespace());seen=[]
    async def create(profile,instructions,**kwargs):
        seen.append(instructions);return RuntimeSession('FIXTURE_NEW_THREAD',profile.id)
    monkeypatch.setattr(runtime,'create_session',create)
    context=editing_context([{'role':'user','content':'真实素材'},{'role':'assistant','content':'最近完整稿'}],'再缩短到30秒，开头更有冲突')
    session=asyncio.run(runtime.resume_session(SimpleNamespace(id='FIXTURE_PROFILE'),'OLD_THREAD',
        developer_instructions=prompt(),recovery_context=context,task_id='FIXTURE_TASK'))
    assert session.thread_id=='FIXTURE_NEW_THREAD' and context in seen[0]
    assert seen[0].count(prompt())==1 and 'latest_user_request' in seen[0]
