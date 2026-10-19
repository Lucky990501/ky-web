"""FIXTURE_ONLY: no Key, real Provider or supplier bill is used by this suite."""
import asyncio
import base64
from dataclasses import replace
import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import httpx
from PIL import Image
import pytest

from app.preview_illustration_provider import IllustrationConfig,IllustrationProvider,IllustrationError,download_vetted_image,validated_image,MODEL
from app.preview_illustration_service import PreviewIllustrationService,PREFIX
from app.preview_illustration_html import ArticleTree
from app.preview_illustration_routes import illustration_request
from test_preview_image_execution import image_fixture

GRANT={'approved':True,'pricing_verified':True,'authorization_id':'FIXTURE_ONLY_NOT_AUTHORIZATION',
       'model':MODEL,'request_max_cny':'0.1','max_total_cny':'1','max_requests':10,
       'max_images_per_task':5,'automatic_paid_retries':0}


def picture(color='blue',size=(640,400)):
    output=io.BytesIO();Image.new('RGB',size,color).save(output,format='PNG');return output.getvalue()


@pytest.fixture
def setup(image_fixture,monkeypatch):
    executor,actors,old_calls=image_fixture
    from app.agent_catalog import CATALOG
    monkeypatch.setitem(CATALOG,'preview-wechat-agent',replace(CATALOG['copywriting-agent'],id='preview-wechat-agent',slug='wechat-official-account-writing'))
    executor.main.product_store.initialize()
    config=IllustrationConfig(key='FIXTURE_ONLY_NOT_A_KEY',enabled=True)
    service=PreviewIllustrationService(executor.main,executor.budget.path.parent,config,GRANT)
    return service,actors,old_calls,executor.budget


def completed_task(service,actor,kind='activity',content=None):
    agent='campaign-agent' if kind=='activity' else 'preview-wechat-agent'
    if kind=='wechat':
        with service.main.store.connection() as conn:
            conn.execute("insert or ignore into tenant_agent_instances(tenant_id,agent_id,status) values(?,?,'enabled')",(actor.tenant_id,agent))
    task=service.main.product_store.create_task(actor.tenant_id,actor.user_id,agent,'FIXTURE_ONLY 完整方案',None)
    if kind=='activity':
        data=json.loads((Path(__file__).parent/'fixtures/activity_plan_d1.json').read_text(encoding='utf-8'))
        data['activity_items']=data['activity_items'][:2]
        if content:data=content
        text='FIXTURE_ONLY 已保存的完整活动方案';structured={'type':'activity_plan','version':'1','data':data}
    else:
        text=content or ('<article><h1>测试教育文章</h1><p style="line-height:1.8;color:#333333">'+('完整教育原文。'*60)+
            '</p><h2 style="background-color:#fff3c4">倾听孩子</h2><p>保留原正文事实。</p><h2>一起成长</h2><blockquote>保持温和沟通</blockquote></article>');structured=None
    metadata={'structured_result':structured,'assistant_message_id':f"task:{task['id']}:assistant"}
    with service.main.store.connection() as conn:
        conn.execute("update tasks set status='completed',stage='completed' where id=?",(task['id'],))
        conn.execute('insert into task_results(task_id,final_response,result_json) values(?,?,?)',(task['id'],text,json.dumps(metadata,ensure_ascii=False)))
    return task


def enable_fixture_provider(service,monkeypatch,fail_index=None):
    calls=[]
    async def generate(prompt,record_http):
        index=len(calls);calls.append(prompt)
        record_http(503 if index==fail_index else 200,'FIXTURE-'+str(index))
        if index==fail_index:raise IllustrationError('ILLUSTRATION_PROVIDER_REJECTED','FIXTURE rejection')
        content=picture(['blue','orange','green'][index%3]);return content,validated_image(content)
    monkeypatch.setattr(service.provider,'generate',generate);return calls


def test_env_is_independent_blank_disabled_and_parameter_contract(tmp_path):
    path=tmp_path/'.env.illustration';path.write_text('N1N_ILLUSTRATION_API_KEY=\nN1N_ILLUSTRATION_ENABLED=false\n',encoding='utf-8');path.chmod(0o600)
    config=IllustrationConfig.load(path);assert not config.key and not config.enabled and config.model==MODEL
    path.write_text('N1N_ILLUSTRATION_API_KEY=FIXTURE_ONLY_PRIVATE\n',encoding='utf-8')
    config=IllustrationConfig.load(path);assert 'FIXTURE_ONLY_PRIVATE' not in repr(config)
    path.write_text('N1N_ILLUSTRATION_WATERMARK=false\n',encoding='utf-8')
    with pytest.raises(IllustrationError):IllustrationConfig.load(path)


def test_disabled_or_missing_budget_never_reserves_or_calls(setup,monkeypatch):
    service,actors,old,budget=setup;actor=actors[0];calls=enable_fixture_provider(service,monkeypatch)
    task=completed_task(service,actor);plan=service.plan(actor,task['id'])
    for config,grant,code in [(IllustrationConfig(),GRANT,'ILLUSTRATION_NOT_CONFIGURED'),(service.config,{},'ILLUSTRATION_BUDGET_PENDING')]:
        service.config=config;service.authorization=grant
        with pytest.raises(IllustrationError) as error:service.confirm(actor,plan['id'],plan['digest'],True)
        assert error.value.code==code
    assert service.capability()['reserved_requests']==0 and calls==old==[] and budget.summary()['image']['requests']==0


@pytest.mark.parametrize('base',['https://api.n1n.ai','https://llm-api.net'])
def test_explicit_approved_base_keeps_exact_model_body_and_no_fallback(tmp_path,monkeypatch,base):
    path=tmp_path/'.env.illustration'
    path.write_text('N1N_ILLUSTRATION_BASE_URL='+base+'\nN1N_ILLUSTRATION_API_KEY=FIXTURE_ONLY\nN1N_ILLUSTRATION_ENABLED=true\n',encoding='utf-8');path.chmod(0o600)
    config=IllustrationConfig.load(path);assert config.base_url==base and config.model==MODEL
    client_type=httpx.AsyncClient;requests=[]
    def respond(request):
        requests.append(request)
        return httpx.Response(503,json={'error':'FIXTURE_ONLY rejection'})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client_type(**{**kwargs,'transport':httpx.MockTransport(respond)}))
    with pytest.raises(IllustrationError) as error:
        asyncio.run(IllustrationProvider(config).generate('FIXTURE_ONLY',lambda *args:None))
    assert error.value.code=='ILLUSTRATION_PROVIDER_REJECTED' and len(requests)==1
    assert str(requests[0].url)==base+'/v1/images/generations?sort=success_rate'
    assert json.loads(requests[0].content)=={'prompt':'FIXTURE_ONLY','model':MODEL,'provider':{'sort':'success_rate'},'size':'1024x1024','watermark':True,'prompt_extend':True,'prompt_extend_mode':'direct'}


@pytest.mark.parametrize('base',['http://llm-api.net','https://llm-api.net/v1/images/generations','https://llm-api.net/','https://llm-api.net.evil.invalid','https://user@llm-api.net','https://127.0.0.1','https://unapproved.invalid'])
def test_unapproved_or_non_base_endpoint_is_rejected_before_transport(tmp_path,base):
    path=tmp_path/'.env.illustration';path.write_text('N1N_ILLUSTRATION_BASE_URL='+base+'\n',encoding='utf-8');path.chmod(0o600)
    with pytest.raises(IllustrationError) as error:IllustrationConfig.load(path)
    assert error.value.code=='ILLUSTRATION_CONFIG_INVALID'
    with pytest.raises(IllustrationError) as error:
        asyncio.run(IllustrationProvider(IllustrationConfig(key='FIXTURE_ONLY',enabled=True,base_url=base)).generate('FIXTURE_ONLY',lambda *args:None))
    assert error.value.code=='ILLUSTRATION_CONFIG_INVALID'


def test_plan_maps_all_requirements_and_reuses_identity(setup):
    service,actors,old,budget=setup;actor=actors[0];task=completed_task(service,actor)
    first=service.plan(actor,task['id']);second=service.plan(actor,task['id'])
    assert first['id']==second['id'] and first['count']==2
    assert [x['position'] for x in first['items']]==[0,1] and first['maximum_cost_cny']=='0.2'
    with service.db() as conn:payload=json.loads(conn.execute('select payload from illustration_plans').fetchone()[0])
    assert payload['items'][0]['prompt']!=payload['items'][1]['prompt']
    assert '感恩留言墙' in payload['items'][0]['prompt'] and '师生默契问答' in payload['items'][1]['prompt']
    assert old==[] and budget.summary()['image']['requests']==0


@pytest.mark.parametrize('other',[1,2])
def test_cross_user_and_tenant_cannot_plan_read_confirm_or_asset(setup,monkeypatch,other):
    service,actors,old,budget=setup;actor=actors[0];task=completed_task(service,actor);plan=service.plan(actor,task['id'])
    calls=enable_fixture_provider(service,monkeypatch);service.confirm(actor,plan['id'],plan['digest'],True);asyncio.run(service.execute(actor,plan['id']))
    asset=service.get(actor,plan['id'])['assets'][0]
    for fn in [lambda:service.plan(actors[other],task['id']),lambda:service.get(actors[other],plan['id']),
               lambda:service.confirm(actors[other],plan['id'],plan['digest'],True),lambda:service.asset(actors[other],asset['id'])]:
        with pytest.raises(LookupError):fn()
    assert len(calls)==2


def test_confirmation_digest_disabled_agent_and_whole_plan_capacity(setup):
    service,actors,old,budget=setup;actor=actors[0];task=completed_task(service,actor);plan=service.plan(actor,task['id'])
    for digest,confirmed in [('wrong',True),(plan['digest'],False)]:
        with pytest.raises(PermissionError):service.confirm(actor,plan['id'],digest,confirmed)
    service.authorization={**GRANT,'max_images_per_task':1}
    with pytest.raises(IllustrationError) as error:service.confirm(actor,plan['id'],plan['digest'],True)
    assert error.value.code=='ILLUSTRATION_BUDGET_EXHAUSTED' and service.capability()['reserved_requests']==0
    with service.main.store.connection() as conn:conn.execute("update tenant_agent_instances set status='disabled' where tenant_id=? and agent_id='campaign-agent'",(actor.tenant_id,))
    with pytest.raises(PermissionError):service.confirm(actor,plan['id'],plan['digest'],True)


def test_source_version_change_rejected_without_reservation(setup):
    service,actors,old,budget=setup;actor=actors[0];task=completed_task(service,actor,'wechat');plan=service.plan(actor,task['id'])
    with service.main.store.connection() as conn:conn.execute('update task_results set final_response=? where task_id=?',('<h1>另一个版本</h1>',task['id']))
    with pytest.raises(IllustrationError) as error:service.confirm(actor,plan['id'],plan['digest'],True)
    assert error.value.code=='ILLUSTRATION_SOURCE_CHANGED' and service.capability()['reserved_requests']==0


def test_acceptance_budget_scope_denies_unapproved_visitor_before_reservation(setup):
    service,actors,old,budget=setup;actor=actors[0];task=completed_task(service,actor);plan=service.plan(actor,task['id'])
    service.authorization={**GRANT,'allowed_tenant_ids':['preview-other-test-only']}
    assert not service.capability(actor)['enabled']
    with pytest.raises(PermissionError):service.confirm(actor,plan['id'],plan['digest'],True)
    assert service.capability()['reserved_requests']==0


def test_branded_word_embeds_original_logo_and_distinct_illustrations(setup,monkeypatch):
    from PIL import ImageDraw
    from app.document_generator import ActivityPlanDocumentService
    service,actors,old,budget=setup;actor=actors[0]
    output=io.BytesIO();logo=Image.new('RGBA',(128,64),(0,0,0,0));ImageDraw.Draw(logo).rectangle((20,20,108,44),fill=(20,120,50,255));logo.save(output,format='PNG')
    key=f'brand-assets/{actor.tenant_id}/logos/{uuid4().hex}.png';service.storage.put(key,output.getvalue(),'image/png')
    with service.main.store.connection() as conn:conn.execute('update enterprise_configs set payload=? where tenant_id=?',(json.dumps({'brand_name':'Fixture测试品牌','brand_logo':key}),actor.tenant_id))
    task=completed_task(service,actor);plan=service.plan(actor,task['id']);calls=enable_fixture_provider(service,monkeypatch)
    service.confirm(actor,plan['id'],plan['digest'],True);asyncio.run(service.execute(actor,plan['id']));done=service.get(actor,plan['id'])
    assert done['status']=='completed'
    document=ActivityPlanDocumentService(service.storage).get(actor.tenant_id,actor.user_id,done['document']['document_id'])
    with ZipFile(io.BytesIO(document.content)) as archive:
        images=[archive.read(name) for name in archive.namelist() if name.startswith('word/media/')]
        assert picture('blue') in images and picture('orange') in images and len(images)>=3
        assert 'Fixture测试品牌' in archive.read('word/header1.xml').decode()
    assert len(calls)==2


def test_activity_word_real_embedded_bytes_and_reexport_no_duplicate_fee(setup,monkeypatch,tmp_path):
    service,actors,old,budget=setup;actor=actors[0];task=completed_task(service,actor);plan=service.plan(actor,task['id'])
    calls=enable_fixture_provider(service,monkeypatch);service.confirm(actor,plan['id'],plan['digest'],True)
    asyncio.run(service.execute(actor,plan['id']));done=service.get(actor,plan['id'])
    assert done['status']=='completed' and len(done['assets'])==2 and len(calls)==2
    document=done['document'];from app.document_generator import ActivityPlanDocumentService
    stored=ActivityPlanDocumentService(service.storage).get(actor.tenant_id,actor.user_id,document['document_id'])
    with ZipFile(io.BytesIO(stored.content)) as archive:
        media=[archive.read(name) for name in archive.namelist() if name.startswith('word/media/')]
        assert len(media)==2 and picture('blue') in media and picture('orange') in media
        xml=archive.read('word/document.xml').decode();assert '感恩留言墙' in xml and '师生默契问答' in xml and '亲爱的社区教师' in xml
        assert xml.count('<wp:inline')==2 and 'r:link=' not in xml
    with pytest.raises(LookupError):ActivityPlanDocumentService(service.storage).get(actors[1].tenant_id,actors[1].user_id,document['document_id'])
    service.confirm(actor,plan['id'],plan['digest'],True);asyncio.run(service.execute(actor,plan['id']));service.finish_export(actor,plan['id'])
    restored=PreviewIllustrationService(service.main,service.root,service.config,GRANT).get(actor,plan['id'])
    assert restored['document']['document_id']==document['document_id'] and len(calls)==2
    assert old==[] and budget.summary()['image']['requests']==0


def test_partial_failure_keeps_success_does_not_replay_or_claim_complete(setup,monkeypatch):
    service,actors,old,budget=setup;actor=actors[0];task=completed_task(service,actor);plan=service.plan(actor,task['id'])
    calls=enable_fixture_provider(service,monkeypatch,fail_index=1);service.confirm(actor,plan['id'],plan['digest'],True);asyncio.run(service.execute(actor,plan['id']))
    done=service.get(actor,plan['id']);assert done['status']=='failed' and len(done['assets'])==1 and not done.get('document')
    service.confirm(actor,plan['id'],plan['digest'],True);asyncio.run(service.execute(actor,plan['id']))
    assert len(calls)==2 and service.capability()['reserved_requests']==2


def test_wechat_safe_positions_assets_zip_and_no_raw_model_urls(setup,monkeypatch):
    service,actors,old,budget=setup;actor=actors[0];task=completed_task(service,actor,'wechat');plan=service.plan(actor,task['id'])
    calls=enable_fixture_provider(service,monkeypatch);service.confirm(actor,plan['id'],plan['digest'],True);asyncio.run(service.execute(actor,plan['id']))
    done=service.get(actor,plan['id']);assert done['status']=='completed' and len(done['assets'])==3
    assert done['html'].count('<img ')==3 and '#fff3c4' in done['html'] and '保留原正文事实' in done['html']
    assert all(asset['url'].startswith(PREFIX+'/assets/') and 'storage_key' not in asset for asset in done['assets'])
    archive_bytes=service.export_article(actor,plan['id'],True)
    with ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert len([n for n in archive.namelist() if n.startswith('images/')])==3
        html=archive.read('article.html').decode();assert '/api/' not in html and 'images/' in html
        assert '后台需人工上传' in archive.read('使用说明.txt').decode()
    assert len(calls)==3 and service.capability()['provider_attempts']==3
    assert service.export_article(actor,plan['id'],False).startswith(b'<!doctype html>')


@pytest.mark.parametrize('html',[
    '<h1>中文</h1><script>alert(1)</script><p onclick="bad()">段落<strong>重点</strong></p><img src="http://127.0.0.1/private">',
    '<section><style>body{color:red}</style><iframe src="/api/private">SECRET</iframe><p style="color:#ffcc00;background-image:url(https://evil);position:fixed">温暖</p></section>',
    '<p>/api/v1/storage/generated/private.jpg /opt/secret/runtime-data/a workspace/private</p>',
])
def test_untrusted_article_cleaning(html):
    safe=ArticleTree(html).html()
    for bad in ['<script','<iframe','onclick','background-image','position:','<img','/api/v1/storage/','/opt/','workspace/']:assert bad not in safe


def test_whole_document_escaped_preview_wrapper_reuses_safety():
    from html import escape
    content='<article><h1>中文标题</h1><p>正文</p><script>bad()</script></article>'
    wrapped='<section id="article"><p>'+escape(content)+'</p></section>'
    tree=ArticleTree(wrapped)
    assert tree.positions()[0].tag=='h1' and 'bad()' not in tree.html()


def test_restart_keeps_reservations_and_never_replays_unknown_work(setup):
    service,actors,old,budget=setup;actor=actors[0];task=completed_task(service,actor);plan=service.plan(actor,task['id'])
    service.confirm(actor,plan['id'],plan['digest'],True)
    restarted=PreviewIllustrationService(service.main,service.root,service.config,GRANT)
    restored=restarted.get(actor,plan['id'])
    assert restored['status']=='failed' and restored['error_code']=='ILLUSTRATION_INTERRUPTED_NOT_RETRIED'
    assert restarted.capability()['reserved_requests']==2 and restarted.capability()['provider_attempts']==0


@pytest.mark.parametrize('url',['http://example.com/image.png','https://127.0.0.1/a','https://unknown.invalid/a','https://cdn.example.com:444/a','https://u:p@cdn.example.com/a','https://cdn.example.com/a#x'])
def test_external_url_is_fail_closed_before_download(url):
    with pytest.raises(IllustrationError):download_vetted_image(url,('cdn.example.com',))


def test_resource_host_private_dns_never_connects(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    monkeypatch.setattr('socket.create_connection',lambda *a,**k:pytest.fail('Must not connect'))
    with pytest.raises(IllustrationError):download_vetted_image('https://approved.example/a',('approved.example',))


@pytest.mark.parametrize('body',[b'not an image',b'',picture(size=(10,10)),b'a'*(2*1024*1024+1)],ids=['fake','empty','too-small','oversized'])
def test_actual_image_format_and_size_validation(body):
    with pytest.raises(IllustrationError):validated_image(body)


def test_qwen_exact_wire_b64_contract_no_retry_and_safe_errors(monkeypatch):
    observed=[];config=IllustrationConfig(key='FIXTURE_ONLY_NOT_A_KEY',enabled=True)
    def response(request):
        observed.append(request);body=json.loads(request.content)
        assert str(request.url)=='https://api.n1n.ai/v1/images/generations?sort=success_rate'
        assert body=={'prompt':'FIXTURE_ONLY prompt','model':MODEL,'provider':{'sort':'success_rate'},'size':'1024x1024','watermark':True,'prompt_extend':True,'prompt_extend_mode':'direct'}
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(picture()).decode()}]},headers={'x-request-id':'fixture-id'})
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kwargs:httpx.MockTransport(response))
    events=[];content,metadata=asyncio.run(IllustrationProvider(config).generate('FIXTURE_ONLY prompt',lambda *x:events.append(x)))
    assert metadata['response_format']=='b64_json' and metadata['width']==640 and len(observed)==1 and events==[(200,'fixture-id')]
    def fail(request):observed.append(request);return httpx.Response(429,json={'error':'PRIVATE_DO_NOT_REPORT'})
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kwargs:httpx.MockTransport(fail))
    with pytest.raises(IllustrationError) as error:asyncio.run(IllustrationProvider(config).generate('FIXTURE_ONLY prompt',lambda *x:None))
    assert len(observed)==2 and 'PRIVATE' not in str(error.value)


def test_url_response_reuses_only_verified_host_download(monkeypatch):
    config=IllustrationConfig(key='FIXTURE_ONLY',enabled=True,asset_hosts=('approved.example',))
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kwargs:httpx.MockTransport(lambda request:httpx.Response(200,json={'data':[{'url':'https://approved.example/fixture.png'}]})))
    seen=[]
    def download(url,hosts):seen.append((url,hosts));return picture()
    monkeypatch.setattr('app.preview_illustration_provider.download_vetted_image',download)
    content,metadata=asyncio.run(IllustrationProvider(config).generate('FIXTURE_ONLY',lambda *x:None))
    assert metadata['response_format']=='url' and seen==[('https://approved.example/fixture.png',('approved.example',))]


def test_transport_failure_is_classified_without_secret_or_retry(monkeypatch):
    calls=[]
    def failure(request):calls.append(request);raise httpx.ConnectError('PRIVATE sk-do-not-print')
    monkeypatch.setattr(httpx,'AsyncHTTPTransport',lambda **kwargs:httpx.MockTransport(failure))
    with pytest.raises(IllustrationError) as error:
        asyncio.run(IllustrationProvider(IllustrationConfig(key='FIXTURE_ONLY',enabled=True)).generate('FIXTURE_ONLY',lambda *x:pytest.fail('No HTTP response')))
    assert len(calls)==1 and error.value.code=='ILLUSTRATION_PROVIDER_UNKNOWN'
    assert error.value.safe_details=={'failure_phase':'connect','error_type':'ConnectError'}
    assert 'PRIVATE' not in str(error.value) and 'sk-' not in json.dumps(error.value.safe_details)


def test_http_owned_routes_and_reject_client_html_tenant_secret(setup,monkeypatch):
    service,actors,old,budget=setup;task=completed_task(service,actors[0]);enable_fixture_provider(service,monkeypatch)
    async def run():
        async def app(scope,receive,send):await illustration_request(service,actors[0],scope,receive,send)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://fixture.invalid') as client:
            for body in [{'task_id':task['id'],'tenant_id':'foreign'},{'html':'<h1>fake</h1>'},{'api_key':'PRIVATE'}]:
                assert (await client.post(PREFIX+'/plans',headers={'Origin':'https://fixture.invalid'},json=body)).status_code==422
            assert (await client.post(PREFIX+'/plans',headers={'Origin':'https://evil.invalid'},json={'task_id':task['id']})).status_code==403
            response=await client.post(PREFIX+'/plans',headers={'Origin':'https://fixture.invalid'},json={'task_id':task['id']});assert response.status_code==200
            plan=response.json();accepted=await client.post(PREFIX+'/plans/'+plan['id']+'/generate',headers={'Origin':'https://fixture.invalid'},json={'confirmed':True,'digest':plan['digest']});assert accepted.status_code==202
            await asyncio.gather(*service.jobs)
            done=(await client.get(PREFIX+'/plans/'+plan['id'])).json();assert done['status']=='completed'
            image=await client.get(done['assets'][0]['url']);assert image.status_code==200 and image.headers['content-type']=='image/png'
    asyncio.run(run())
