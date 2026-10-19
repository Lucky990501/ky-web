"""Ten-item Preview UX contract. FIXTURE_ONLY, no actual Provider spend."""
import asyncio
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from test_preview_image_execution import image_fixture
from test_preview_illustrations import setup,completed_task,enable_fixture_provider
from test_preview_wechat_image_policy import article,authorize


def test_cover_is_independent_body_unchanged_zip_and_history_no_replay(setup,monkeypatch):
    service,actors,old,budget=setup;authorize(service,actors);actor=actors[0]
    task=completed_task(service,actor,'wechat',article(2));plan=service.plan(actor,task['id'])
    assert plan['count']==4 and [i['position'] for i in plan['items']]==[0,2,4,-1]
    calls=enable_fixture_provider(service,monkeypatch)
    service.confirm(actor,plan['id'],plan['digest'],True);asyncio.run(service.execute(actor,plan['id']))
    done=service.current(actor,task['id']);cover=done['publication']['cover']
    assert cover['role']=='cover' and cover['url'] not in done['html']
    assert len(done['assets'])==4 and done['html'].count('<img ')==3
    assert done['publication']['title']=='Fixture文章' and done['publication']['digest']
    with ZipFile(io.BytesIO(service.export_article(actor,plan['id'],True))) as z:
        publication=json.loads(z.read('publication.json'))
        assert publication['cover']=='images/'+cover['filename']
        assert publication['cover'] in z.namelist()
        assert publication['cover'] not in z.read('article.html').decode()
        assert 'Fixture正文0' in z.read('article.html').decode()
    for peer in actors[1:]:
        with pytest.raises(LookupError):service.asset(peer,cover['id'])
    before=service.capability()['reserved_requests']
    service.export_article(actor,plan['id']);service.get(actor,plan['id']);service.plan(actor,task['id'])
    assert len(calls)==4 and service.capability()['reserved_requests']==before
    assert old==[] and budget.summary()['text']['requests']==0


def test_legacy_three_image_plan_does_not_gain_a_paid_cover_on_read(setup,monkeypatch):
    service,actors,_,_=setup;actor=actors[0]
    task=completed_task(service,actor,'wechat');plan=service.plan(actor,task['id'])
    with service.db() as c:
        payload=json.loads(c.execute('select payload from illustration_plans where id=?',(plan['id'],)).fetchone()[0])
        payload['items']=[i for i in payload['items'] if i.get('role')!='cover']
        c.execute('update illustration_plans set payload=? where id=?',(json.dumps(payload),plan['id']))
    assert service.plan(actor,task['id'])['count']==3
    calls=enable_fixture_provider(service,monkeypatch)
    service.confirm(actor,plan['id'],plan['digest'],True);asyncio.run(service.execute(actor,plan['id']))
    done=service.current(actor,task['id'])
    assert done['count']==3 and done['publication']['cover'] is None and len(calls)==3


def test_failed_first_image_starts_no_more_calls_and_leaves_text_completed(setup,monkeypatch):
    service,actors,_,_=setup;actor=actors[0]
    task=completed_task(service,actor,'wechat');plan=service.plan(actor,task['id'])
    calls=enable_fixture_provider(service,monkeypatch,fail_index=0)
    service.confirm(actor,plan['id'],plan['digest'],True);asyncio.run(service.execute(actor,plan['id']))
    assert len(calls)==1 and service.get(actor,plan['id'])['status']=='failed'
    assert service.main.product_store.task(task['id'],actor.tenant_id,actor.user_id)['status']=='completed'


def test_preview_only_frontend_controls_and_rendered_copy_contract():
    root=Path(__file__).resolve().parents[1]
    js=(root/'app/static/customer-preview.js').read_text(encoding='utf-8')
    assert 'window.confirm' not in js and '对标文章（可选）' not in js
    assert '独立图片体验：' not in js and 'preview-image-quality' not in js and 'preview-use-image-context' not in js
    assert "const useContext=true" in js and "quality='high'" in js
    assert "['2048x2048','1:1']" in js and "['2160x3840','9:16（竖版）']" in js
    article_js=(root/'app/static/wechat-article-ui.js').read_text(encoding='utf-8')
    assert "doc.execCommand('copy')" in article_js and 'range.selectNodeContents(doc.body)' in article_js
    assert "summary.textContent='发布建议'" in article_js
    assert "setAttribute('sandbox','allow-same-origin')" in article_js
    assert "setAttribute('sandbox','allow-scripts" not in article_js
    assert "if(mode==='rich'&&window.WORKBENCH_PREVIEW_BASE)" in article_js
