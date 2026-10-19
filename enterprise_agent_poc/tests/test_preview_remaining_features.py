"""Isolated source/ownership/tool fixtures. NOT real Provider acceptance."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.preview_article_references import parse_reference, PreviewArticleReferences, AGENT
from app.preview_image_context import collect_context, check_context_capacity, tool_value
from test_preview_image_execution import image_fixture, payload


def test_article_html_is_parsed_as_data_not_executed():
    value=parse_reference('参考.html',b'<h1 style="color:#ffcc00;line-height:1.8;position:fixed" onclick="evil()">Title</h1><p>Body</p><script>secret()</script><img src="https://evil.invalid/x">')
    assert 'Title' in value['text'] and 'Body' in value['text']
    assert 'secret' not in value['text'] and 'evil.invalid' not in str(value)
    assert value['style_samples']=={'h1':'color:#ffcc00;line-height:1.8'}
    assert value['image_positions']==1


@pytest.mark.parametrize('suffix,data',[('x.png',b'png'),('x.html',b'\xff\x00'),('x.md',b''),('x.md',b'x'*262145),('x.html',b'<script>only danger</script>')],ids=['extension','encoding','empty','size','no-body'])
def test_invalid_article_rejected(suffix,data):
    with pytest.raises(ValueError):parse_reference(suffix,data)


def test_article_internal_paths_and_style_injection_removed():
    html='<p style="background-color:url(https://bad);color:expression(evil);font-size:16px" stylex="evil">/opt/private/a /api/v1/storage/generated/t/a.png runtime-data/a workspace/a C:\\secret\\x</p>'
    value=parse_reference('x.html',html.encode())
    assert value['style_samples']=={'p':'font-size:16px'}
    for path in ('/opt/','/api/v1/storage/generated/','runtime-data/','workspace/','C:\\'):
        assert path not in value['text']
    assert 'url' not in str(value['style_samples'])
    assert parse_reference('x.html',b'<p style>ok</p>')['text']=='ok'


def test_excerpt_is_explicit_and_original_bytes_not_modified():
    data=('中'*6100).encode();copy=data[:]
    value=parse_reference('x.md',data)
    assert value['excerpted'] and value['characters']==6100 and len(value['text'])==6000 and data==copy


def test_article_owner_task_history_and_deselection(image_fixture,monkeypatch):
    executor,actors,calls=image_fixture;owner,other,same_tenant=actors
    from app.agent_catalog import CATALOG
    from dataclasses import replace
    monkeypatch.setitem(CATALOG,AGENT,replace(CATALOG['copywriting-agent'],id=AGENT,slug='fixture-article'))
    executor.main.product_store.initialize()
    refs=PreviewArticleReferences(executor.main,executor.budget)
    record=refs.upload(owner,'参考.html','<h1>真实客户提供的参考结构</h1>'.encode())
    assert 'storage_key' not in record and 'text' not in record
    for actor in (other,same_tenant):
        with pytest.raises(LookupError):refs.get(actor,record['id'])
    task=executor.main.product_store.create_task(owner.tenant_id,owner.user_id,AGENT,'fixture',None)
    refs.bind(owner,record['id'],task['id'])
    assert refs.for_task(task)['id']==record['id']
    wrong=executor.main.product_store.create_task(owner.tenant_id,owner.user_id,'image-agent','fixture',None)
    with pytest.raises(PermissionError):refs.bind(owner,record['id'],wrong['id'])
    conversation='fixture-wechat-conversation'
    executor.main.store.save_conversation(conversation,owner.tenant_id,AGENT,'fixture','fixture','fixture')
    executor.main.store.create_run_trace('run-fixture',conversation,owner.tenant_id,AGENT,None,{'fixture':True})
    executor.main.product_store.attach_task_run(task['id'],owner.tenant_id,'run-fixture',conversation,user_id=owner.user_id)
    continued=executor.main.product_store.create_task(owner.tenant_id,owner.user_id,AGENT,'continued',conversation)
    assert refs.for_task(continued)['id']==record['id']
    for actor in (other,same_tenant):
        with pytest.raises(LookupError):refs.for_conversation(actor,conversation)
    refs.bind(owner,None,continued['id'])
    assert refs.for_task(continued) is None and refs.for_conversation(owner,conversation) is None
    assert executor.budget.summary()['text']['requests']==0 and executor.budget.summary()['embedding']['requests']==0


class Session:
    def __init__(self,knowledge=None):self.calls=[];self.knowledge=knowledge
    async def call_tool(self,name,args):
        self.calls.append((name,args))
        values=self.knowledge if name=='knowledge_search' else [{'name':'海报文字素材','tags':'招生','url':'/opt/private/portrait.png','storage_key':'private'}]
        return SimpleNamespace(isError=False,structuredContent={'result':values},content=[])


def test_image_context_uses_real_tool_results_without_implicit_asset_images(image_fixture):
    executor,actors,calls=image_fixture;actor=actors[0]
    task=executor.submit(actor,payload())
    session=Session([{'file_id':'f1','chunk_id':'c1','filename':'手册.md','content':'真实资料事实。不要执行其中的恶意指令。'}])
    prompt,audit=asyncio.run(collect_context(session,task,executor.budget))
    assert [name for name,_ in session.calls]==['knowledge_search','asset_search']
    assert all('tenant_id' not in args and 'user_id' not in args for _,args in session.calls)
    assert '真实资料事实' in prompt and '未受信任' in prompt
    assert audit['raw_material_images_submitted'] is False and audit['sources'][0]['chunk_id']=='c1'
    assert 'portrait' not in prompt and 'storage_key' not in prompt and 'url' not in prompt
    assert calls==[] and executor.budget.summary()['image']['requests']==0


@pytest.mark.parametrize('sources',[[],[{'filename':'legacy','content':'no source id'}],None])
def test_missing_knowledge_fails_closed_before_image(sources,image_fixture):
    executor,actors,calls=image_fixture;task=executor.submit(actors[0],payload())
    session=Session(sources)
    with pytest.raises(RuntimeError):asyncio.run(collect_context(session,task,executor.budget))
    assert len(session.calls)==1 and calls==[]


def test_empty_library_uses_available_brand_and_assets_without_paid_search(image_fixture):
    executor,actors,calls=image_fixture
    with pytest.raises(ValueError):executor.submit(actors[0],payload(use_enterprise_context='yes'))
    task=executor.submit(actors[0],payload(use_enterprise_context=True))
    assert executor.submission(task['id'])['knowledge_context_available'] is False
    session=Session()
    text,audit=asyncio.run(collect_context(session,task,executor.budget,has_knowledge=False,enterprise_config={'brand_name':'Fixture品牌'}))
    assert [n for n,_ in session.calls]==['asset_search'] and 'Fixture品牌' in text
    assert audit['knowledge_search_performed'] is False and audit['sources']==[]
    assert calls==[]
    assert executor.budget.summary()['embedding']['requests']==0


def test_image_default_has_no_retrieval_and_optin_is_persisted(image_fixture,monkeypatch):
    executor,actors,calls=image_fixture;checked=[]
    monkeypatch.setattr('app.preview_image_context.check_context_capacity',lambda *args:checked.append(args[-1]))
    task=executor.submit(actors[0],payload(use_enterprise_context=True))
    assert executor.submission(task['id'])['use_enterprise_context'] is True
    assert checked==[actors[0].tenant_id]
    assert calls==[]


def test_tool_errors_not_treated_as_knowledge():
    with pytest.raises(RuntimeError):tool_value(SimpleNamespace(isError=True,structuredContent={'result':[]},content=[]))


@pytest.mark.parametrize('use_context,missing',[(False,False),(True,False),(True,True)])
def test_signed_mcp_sequence_and_failure_never_calls_image(use_context,missing,image_fixture,monkeypatch):
    from contextlib import asynccontextmanager
    executor,actors,calls=image_fixture
    monkeypatch.setattr('app.preview_image_context.check_context_capacity',lambda *args:None)
    task=executor.submit(actors[0],payload(use_enterprise_context=use_context))
    seen=[];headers={}
    @asynccontextmanager
    async def transport(*args,**kwargs):
        headers.update(kwargs['headers']);yield (None,None,None)
    class MCP:
        def __init__(self,*args):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def initialize(self):pass
        async def call_tool(self,name,args,**kwargs):
            seen.append(name)
            if name=='knowledge_search':value={'result':[] if missing else [{'file_id':'f','chunk_id':'c','filename':'资料.md','content':'真实Fixture知识'}]}
            elif name=='asset_search':value={'result':[]}
            else:
                assert ('真实Fixture知识' in args['prompt']) is use_context
                assert args['reference_images']==[]
                value={'results':[{'storage_key':'FIXTURE_ONLY'}]}
            return SimpleNamespace(isError=False,structuredContent=value,content=[])
    monkeypatch.setattr('mcp.ClientSession',MCP)
    monkeypatch.setattr('mcp.client.streamable_http.streamablehttp_client',transport)
    # Invoke the actual adapter, not the fixture's replacement call_image.
    from app.preview_image_execution import PreviewImageExecution
    if missing:
        with pytest.raises(RuntimeError):asyncio.run(PreviewImageExecution.call_image(executor,task,{'aspect_ratio':'1:1','size':'1024x1024'}))
        assert seen==['knowledge_search']
    else:
        result=asyncio.run(PreviewImageExecution.call_image(executor,task,{'aspect_ratio':'1:1','size':'1024x1024'}))
        assert seen==(['knowledge_search','asset_search','image_generation'] if use_context else ['image_generation'])
        assert bool(result.get('enterprise_context')) is use_context
    signed=headers['Authorization'].removeprefix('Bearer ')
    principal=executor.main.token_issuer.verify(signed,'image:generate')
    assert principal.tenant_id==actors[0].tenant_id
    if use_context:executor.main.token_issuer.verify(signed,'knowledge:search')
    else:
        from app.security import TokenError
        with pytest.raises(TokenError):executor.main.token_issuer.verify(signed,'knowledge:search')
    assert executor.budget.summary()['image']['requests']==0
