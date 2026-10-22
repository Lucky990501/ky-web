"""Brand confirmation, Preview scope and preserved requirements: FIXTURE ONLY."""
from contextvars import ContextVar
from types import SimpleNamespace
import pytest
from app import preview_brand_topics as brand
from test_preview_image_execution import image_fixture

def test_owner_supplied_entities_and_address_boundaries():
    text=brand.policy()
    for term in ['融侨外滩壹号写字楼B座','易明教育赋能中心（定制化伴学）','长明会','心力、认知、表达、关系','提分成为成长的自然结果','不得自动用作赋能中心校区','不得擅自扩写成保证提分','新的创作会话','等待用户确认']:
        assert term in text

@pytest.mark.parametrize('message',['写一段招生文案','写易明教育的公众号','写长明会文章','介绍总部','做个活动方案'])
def test_first_request_always_asks_even_when_a_name_is_present(message):
    selected,question=brand.decision([],message)
    assert not selected and question.startswith(brand.PREFIX)
    assert all(name in question for name in ['易明长青总部','易明教育赋能中心','长明会'])

@pytest.mark.parametrize('reply,expected',[('2',(2,)),('长明会，面向成长型个体',(3,)),('总部',(1,)),('1、2',(1,2)),('综合品牌',(1,2,3))])
def test_confirmation_and_followup_do_not_ask_again(reply,expected):
    history=[{'role':'user','content':'请保留我最初的内容需求'},{'role':'assistant','content':brand.QUESTION}]
    assert brand.decision(history,reply)==(expected,None)
    history.extend([{'role':'user','content':reply},{'role':'assistant','content':'FIXTURE draft'}])
    assert brand.decision(history,'第二段改短一点')==(expected,None)

def test_confirmation_not_inferred_from_old_draft_or_other_messages():
    assert brand.decision([{'role':'assistant','content':'易明教育与长明会都很好'}],'开始写吧')[1]
    history=[{'role':'user','content':'写长明会介绍'},{'role':'assistant','content':brand.QUESTION}]
    assert brand.decision(history,'确认')==((3,),None)
    history.extend([{'role':'user','content':'3'},{'role':'assistant','content':'FIXTURE'}])
    assert brand.decision(history,'这次写易明教育')[1]
    assert brand.decision([{'role':'assistant','content':brand.QUESTION}],'不是1也不要2')[1]

def test_owned_context_provider_gate_and_other_tenant_unchanged(image_fixture,monkeypatch):
    executor,actors,calls=image_fixture;actor=actors[0];main=executor.main
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true');monkeypatch.setattr(brand,'TENANT',actor.tenant_id)
    cid='fixture-brand-conversation';main.store.save_conversation(cid,actor.tenant_id,'copywriting-agent','fixture','fixture-thread','fixture')
    with main.store.connection() as c:c.execute('insert into conversation_owners(conversation_id,user_id,title) values(?,?,?)',(cid,actor.user_id,'Fixture'))
    task={'id':'fixture','agent_id':'copywriting-agent','tenant_id':actor.tenant_id,'user_id':actor.user_id,'conversation_id':cid,'input_text':'2'}
    body={'instructions':'existing','input':[],'tools':[{'name':'fixture'}]}
    with pytest.raises(PermissionError):brand.provider_request(body,task,main)
    main.product_store.add_message(cid,'user','写一篇完整文章')
    main.product_store.add_message(cid,'assistant',brand.QUESTION)
    result=brand.provider_request(body,task,main)
    assert '易明教育赋能中心（定制化伴学）' in result['instructions'] and result['tools']==body['tools']
    assert brand.provider_request(result,task,main)==result
    with pytest.raises(PermissionError):brand.for_task(main,{**task,'user_id':actors[2].user_id})
    other={**task,'tenant_id':actors[1].tenant_id}
    assert brand.provider_request(body,other,main) is body
    assert calls==[]

def test_preview_only_install_does_not_mutate_shared_catalog(monkeypatch):
    from app.agent_catalog import CATALOG
    before=dict(CATALOG)
    monkeypatch.delenv('CUSTOMER_PREVIEW_MODE',raising=False)
    with pytest.raises(RuntimeError):brand.install()
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true');brand.install()
    assert CATALOG==before
