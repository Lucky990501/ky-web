"""FIXTURE_ONLY prompt/wiring checks, not real model quality evidence."""
import asyncio
from dataclasses import asdict
import hashlib
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.agent_catalog import CATALOG
from app.preview_copywriting_prompt import AGENT,PROMPT_PATH,install,prompt,provider_request


@pytest.fixture
def preview(monkeypatch):
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    monkeypatch.setitem(CATALOG,AGENT,CATALOG[AGENT])


def test_only_preview_catalog_instructions_change_profile_and_skills_preserved(preview):
    before={key:asdict(value) for key,value in CATALOG.items()}
    install();after={key:asdict(value) for key,value in CATALOG.items()}
    for key in before:
        assert after[key]==({**before[key],'instructions':prompt()} if key==AGENT else before[key])
    install();assert CATALOG[AGENT].instructions==prompt()


def test_not_loaded_into_production(monkeypatch):
    monkeypatch.delenv('CUSTOMER_PREVIEW_MODE',raising=False)
    before=dict(CATALOG)
    with pytest.raises(RuntimeError):install()
    assert CATALOG==before


@pytest.mark.parametrize('rule',[
 '先共情，再表达价值','讲师引导型课程口播','人物故事型成长证言',
 '人物证言必须有实际素材依据','如果没有足够故事材料，不能虚构一段第一人称亲身经历',
 '不得把未填写的占位符当作真实素材','不要求每次填写所有字段',
 '不主动输出：镜头说明、背景音乐、拍摄建议、营销分析、写作方法、说明性前言',
 '直接输出完整成稿','不机械调用企业配置、知识库和素材搜索',
])
def test_owner_rules_loaded_verbatim_as_semantic_requirements(rule):
    assert rule in prompt()


def test_old_skill_formats_cannot_override_new_default(preview):
    install();text=CATALOG[AGENT].instructions
    assert '强制标题、标签、传统营销结构和机械工具调用流程不适用于本任务' in text
    assert '必须先调用 enterprise_config_get，再调用 knowledge_search 和 asset_search' not in text
    assert '教育类文案默认采用强观点' not in text
    assert '不得泄露密钥、跨Tenant/用户资料' in text


def test_provider_request_contains_authoritative_prompt_exactly_once(preview):
    body={'instructions':'SDK instruction fixture','tools':[{'name':'tenant_tool'}],'input':[]}
    result,digest=provider_request(body,{'agent_id':AGENT})
    assert digest==hashlib.sha256(prompt().encode()).hexdigest()
    assert result['instructions'].endswith(prompt()) and result['tools']==body['tools']
    again,same=provider_request(result,{'agent_id':AGENT})
    assert again==result and same==digest and again['instructions'].count(prompt())==1
    assert body['instructions']=='SDK instruction fixture'


@pytest.mark.parametrize('agent',['image-agent','campaign-agent','preview-wechat-agent'])
def test_other_agent_provider_requests_unchanged(agent):
    body={'instructions':'Original','input':[{'role':'user','content':'Fixture'}]}
    result,digest=provider_request(body,{'agent_id':agent})
    assert result is body and digest is None


def test_service_new_and_resumed_conversation_receives_prompt_without_profile_change(preview,tmp_path):
    from app.service import AgentService
    from app.store import POCStore
    from app.product_store import ProductStore
    from app.domain import RuntimeSession,RuntimeTurn
    store=POCStore(tmp_path/'fixture.sqlite');store.initialize()
    ProductStore(store).initialize()
    with store.connection() as conn:conn.execute('insert into tenants values(?,?,?)',('preview-fixture','Fixture','not-real-key'))
    seen=[]
    class Runtime:
        async def create_session(self,profile,developer_instructions,**kwargs):
            seen.append(('new',developer_instructions));return RuntimeSession('fixture-thread',profile.id)
        async def resume_session(self,profile,thread,developer_instructions=None,**kwargs):
            seen.append(('resume',developer_instructions));return RuntimeSession(thread,profile.id)
        async def run_turn(self,session,message):
            return RuntimeTurn(thread_id=session.thread_id,text='FIXTURE_ONLY 非真实模型结果')
    settings=SimpleNamespace(model_provider_id='fixture',model_id='fixture',reasoning_effort='low',test_text_gate_enabled=False)
    service=AgentService(store,Runtime(),settings)
    before=service.profile_for('preview-fixture',AGENT)
    install();assert service.profile_for('preview-fixture',AGENT)==before
    first=asyncio.run(service.run('preview-fixture',AGENT,'Fixture课程素材'))
    second=asyncio.run(service.run('preview-fixture',AGENT,'Fixture温和改写',first.conversation_id))
    assert first.conversation_id==second.conversation_id
    assert seen==[('new',prompt()),('resume',prompt())]


def test_placeholder_and_launcher_are_preview_only():
    root=PROMPT_PATH.parent.parent
    script=(root/'app/static/customer-preview.js').read_text(encoding='utf-8')
    assert "main?.dataset.agentId!=='copywriting-agent'" in script
    assert '描述视频主题、出镜人物、目标观众和真实故事，帮你生成可直接录制的口播逐字稿。' in script
    assert 'preview_copywriting_prompt' not in (root/'app/main.py').read_text(encoding='utf-8')
