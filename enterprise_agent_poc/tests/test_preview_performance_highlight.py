"""FIXTURE_ONLY measured local stage delays, never Provider or customer data."""
import asyncio
import copy
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import subprocess
import threading
import time
import types
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import httpx
import pytest

from app.preview_wechat_article import normalize_tree,render,BODY_POLICY
from app.preview_illustration_html import ArticleTree
from app.preview_illustration_provider import IllustrationProvider,IllustrationConfig,IllustrationError,validated_image
from test_preview_illustrations import setup,completed_task,picture
from test_preview_image_execution import image_fixture
from test_preview_business_delivery import seven


def test_highlight_only_body_emphasis_and_idempotent_heading_numbers():
    root=ET.fromstring('<article><h1><strong>标题不黄</strong></h1><h2><b>章节不黄</b></h2><p>普通正文<strong>核心句子</strong><span><b>核心提醒</b></span></p><figure><figcaption><strong>图注不黄</strong></figcaption></figure></article>')
    once=ET.tostring(normalize_tree(root));twice=ET.tostring(normalize_tree(root));assert once==twice
    for node in root.findall('.//p//strong')+root.findall('.//p//b'):
        assert node.get('style')=='color:#FFCA00;font-weight:700'
    for node in root.findall('.//h1/strong')+root.findall('.//h2/b')+root.findall('.//figcaption/strong'):
        assert '#FFCA00' not in node.get('style')
    assert '#222222' in root.find('.//p').get('style')
    assert '1至2处' in BODY_POLICY and '**重点内容**' in BODY_POLICY


def test_nested_inline_style_cannot_override_body_highlight():
    root=ET.fromstring('<article><p>普通<strong><span style="color:red;font-weight:400;font-style:italic">核心</span></strong></p></article>')
    once=ET.tostring(normalize_tree(root));assert ET.tostring(normalize_tree(root))==once
    style=root.find('.//strong/span').get('style');assert 'color:#FFCA00' in style and 'font-weight:700' in style and 'font-style:italic' in style and 'red' not in style


def test_markdown_and_safe_export_preserve_facts_images_and_highlight():
    html=render('# 正式标题\n\n普通正文，**核心事实不变**。\n\n## 章节\n\n保持自然沟通。')
    assert '**' not in html and 'color:#FFCA00;font-weight:700' in html
    tree=ArticleTree(html+'<script>alert(1)</script><p onclick="bad()">普通事实</p>')
    out=tree.html([{'url':'/preview/api/v1/preview/illustrations/assets/'+'a'*32,'filename':'fixture.png','position':0,'label':'课堂'}],portable=True)
    assert 'images/fixture.png' in out and '核心事实不变' in out and '普通事实' in out
    assert 'onclick' not in out and '<script' not in out and 'alert(1)' not in out
    assert 'color:#FFCA00;font-weight:700' in out


def test_real_asset_export_contract_fixture_highlight_cover_and_no_rebilling(setup,monkeypatch):
    service,actors,_,budget=setup;actor=actors[0]
    _,p=seven(service,actor)
    from test_preview_illustrations import enable_fixture_provider
    calls=enable_fixture_provider(service,monkeypatch)
    with service.db() as c:
        payload=json.loads(c.execute('select payload from illustration_plans where id=?',(p['id'],)).fetchone()[0])
        payload['html']=payload['html'].replace('教育文章原文。','教育文章<strong>核心事实</strong>。',1)
        c.execute('update illustration_plans set payload=? where id=?',(json.dumps(payload),p['id']))
    asyncio.run(service.execute(actor,p['id']))
    before=service.capability()['reserved_requests'];html=service.export_article(actor,p['id']);pack=service.export_article(actor,p['id'],True)
    with ZipFile(BytesIO(pack)) as z:
        assert z.testzip() is None and len([n for n in z.namelist() if n.startswith('images/')])==7
        assert b'color:#FFCA00;font-weight:700' in z.read('article.html')
        assert json.loads(z.read('publication.json'))['cover'] in z.namelist()
    assert b'color:#FFCA00;font-weight:700' in html
    assert len(calls)==7 and service.capability()['reserved_requests']==before and budget.summary()['text']['requests']==0


def test_provider_429_is_single_attempt_and_numeric_diagnostics_only(monkeypatch,caplog):
    real_client=httpx.AsyncClient;calls=[];statuses=[]
    async def handler(request):
        calls.append(request);assert json.loads(request.content)['watermark'] is False
        return httpx.Response(429,headers={'x-request-id':'FIXTURE-429'},json={'error':'FIXTURE_ONLY'})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real_client(**{**kw,'transport':httpx.MockTransport(handler)}))
    caplog.set_level(logging.INFO,logger='uvicorn.error.preview_illustration_timing')
    p=IllustrationProvider(IllustrationConfig(key='FIXTURE_ONLY_SECRET',enabled=True))
    with pytest.raises(IllustrationError) as error:asyncio.run(p.generate('FIXTURE_PRIVATE_PROMPT',lambda *args:statuses.append(args)))
    assert error.value.code=='ILLUSTRATION_PROVIDER_REJECTED' and statuses==[(429,'FIXTURE-429')] and len(calls)==1
    assert 'FIXTURE_PRIVATE_PROMPT' not in caplog.text and 'FIXTURE_ONLY_SECRET' not in caplog.text
    assert 'provider_headers' in caplog.text and 'provider_total' in caplog.text


def test_seven_image_bounded_threaded_processing_benchmark(setup,monkeypatch,tmp_path):
    import app.preview_illustration_service as current
    service,actors,_,_=setup;actor=actors[0]
    old=types.ModuleType('fixture_before_illustration_service')
    before_file=os.environ.get('PREVIEW_PERF_BASELINE_SERVICE')
    source=Path(before_file).read_text(encoding='utf-8') if before_file else subprocess.check_output(['git','show','2f77e48d5cc0c8c226d2d95e1e00958a2af0da39:enterprise_agent_poc/app/preview_illustration_service.py'],text=True,encoding='utf-8')
    exec(compile(source,'FIXTURE_BASELINE_SERVICE','exec'),old.__dict__)
    original_validate=validated_image;threads=[]
    def slow_validate(*args,**kw):
        threads.append(threading.get_ident());time.sleep(.025);return original_validate(*args,**kw)
    monkeypatch.setattr(current,'validated_image',slow_validate);old.validated_image=slow_validate
    storage=service.storage
    class SlowStorage:
        def put(self,*args):time.sleep(.008);return storage.put(*args)
        def get(self,*args):time.sleep(.008);return storage.get(*args)
    timings=[]
    async def run(klass,global_limit,tenant_limit,label):
        monkeypatch.setattr(current,'MAX_CONCURRENT_ILLUSTRATIONS_PER_TENANT',tenant_limit)
        s=klass(service.main,service.root,service.config,copy.deepcopy(service.authorization));s.generation_slots=asyncio.Semaphore(global_limit);s.storage=SlowStorage()
        _,plan=seven(s,actor);active=peak=0;calls=[]
        async def provider(prompt,record_http,**kwargs):
            nonlocal active,peak
            calls.append(prompt);active+=1;peak=max(peak,active)
            try:
                await asyncio.sleep(.040);record_http(200,'FIXTURE-PERF-'+str(len(calls)));raw=picture();return raw,original_validate(raw)
            finally:active-=1
        s.provider.generate=provider
        start=time.perf_counter();await s.execute(actor,plan['id']);ms=(time.perf_counter()-start)*1000
        result=s.get(actor,plan['id']);assert result['status']=='completed' and len(result['assets'])==7 and len(calls)==7
        assert peak<=tenant_limit
        timings.append({'mode':'FIXTURE_ONLY_INJECTED_STAGE_DELAYS','variant':label,'global':global_limit,'tenant':tenant_limit,'duration_ms':round(ms,2),'peak_provider':peak})
        return ms
    async def scenario():
        before=await run(old.PreviewIllustrationService,4,2,'baseline_sync_4_2')
        after=await run(current.PreviewIllustrationService,4,2,'threaded_4_2_deployed_policy')
        await run(current.PreviewIllustrationService,6,3,'threaded_6_3_fixture_only_not_authorized_for_deployment')
        assert after<before
    asyncio.run(scenario())
    (tmp_path/'image-performance.json').write_text(json.dumps(timings))
    print('IMAGE_FIXTURE_BENCHMARK='+json.dumps(timings))
    assert len(set(threads))>1


@pytest.mark.parametrize('iteration',range(3))
def test_single_429_preserves_six_successes_and_original_reservations(setup,monkeypatch,iteration):
    service,actors,_,_=setup;actor=actors[0];_,p=seven(service,actor);calls=[]
    async def provider(prompt,record_http,**kwargs):
        i=len(calls);calls.append(i);record_http(429 if i==2 else 200,'FIXTURE-'+str(i))
        if i==2:raise IllustrationError('ILLUSTRATION_PROVIDER_REJECTED','FIXTURE_ONLY 429')
        raw=picture();return raw,validated_image(raw)
    service.provider.generate=provider;asyncio.run(service.execute(actor,p['id']))
    result=service.get(actor,p['id']);assert result['status']=='failed' and len(result['assets'])==6 and len(calls)==7,(result.get('error_code'),json.loads(service.row(actor,p['id'])['payload']).get('item_errors'),len(calls))
    assert service.capability()['reserved_requests']==7 and result['recoverable_missing']==1
