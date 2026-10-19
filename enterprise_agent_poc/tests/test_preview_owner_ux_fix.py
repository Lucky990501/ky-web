"""FIXTURE_ONLY regressions: no real model, image HTTP or paid request."""
from decimal import Decimal
import io,json
import pytest
from app.image_request_contract import ImageRequestError
from app.preview_budget import PreviewBudgetExceeded
from app.preview_image_errors import image_admission_error
from app.preview_demo_credit import grant_once
from app.preview_wechat_article import render,body_only_markdown,normalize_tree,BODY_POLICY
from app.preview_illustration_html import ArticleTree,full_document
from test_preview_image_execution import image_fixture,payload
from test_preview_illustrations import setup,completed_task,GRANT


@pytest.mark.parametrize('error,status,code',[
 (ValueError('insufficient_credit'),402,'PREVIEW_INSUFFICIENT_CREDIT'),
 (ImageRequestError('image_size_conflict','raw'),422,'image_size_conflict'),
 (ImageRequestError('image_aspect_ratio_conflict','raw'),422,'image_aspect_ratio_conflict'),
 (ImageRequestError('image_count_limit','raw'),422,'image_count_limit'),
 (ImageRequestError('IMAGE_REFERENCE_INVALID','raw'),422,'IMAGE_REFERENCE_INVALID'),
 (ImageRequestError('IMAGE_EDIT_BASE_MISSING','raw'),404,'IMAGE_EDIT_BASE_MISSING'),
 (PreviewBudgetExceeded('raw','PREVIEW_EMBEDDING_BUDGET_EXHAUSTED'),429,'IMAGE_CONTEXT_BUDGET_EXHAUSTED'),
 (PermissionError('credential=/opt/private'),403,'IMAGE_AUTHORIZATION_REQUIRED'),
 (ValueError('credential=/opt/private'),422,'IMAGE_REQUEST_INVALID'),
])
def test_safe_image_error_classification(error,status,code):
    actual,result=image_admission_error(error)
    assert actual==status and result['error_code']==code
    assert 'raw' not in result['user_message'] and '/opt/' not in result['user_message']
    assert '多参考图及高清能力尚未核验' not in result['user_message']


def test_insufficient_credit_preflight_and_submit_create_no_task_or_paid_reservation(image_fixture):
    executor,actors,calls=image_fixture;actor=actors[0]
    with executor.main.store.connection() as conn:conn.execute('update credit_accounts set balance=0 where tenant_id=?',(actor.tenant_id,))
    for operation in (lambda:executor.credit_preflight(actor),lambda:executor.submit(actor,payload(message='生成一张9:16海报',size='2160x3840',quality='high'))):
        with pytest.raises(ValueError) as error:operation()
        assert image_admission_error(error.value)[0]==402
    with executor.main.store.connection() as conn:assert conn.execute('select count(*) from tasks').fetchone()[0]==0
    assert calls==[] and executor.budget.summary()['image']['requests']==0


def test_2160_high_plan_confirm_submit_records_exact_parameters_without_dispatch(image_fixture):
    from app.preview_image_parameters import preview_plan,quality_value
    executor,actors,calls=image_fixture;actor=actors[0];executor.credit_preflight(actor)
    plan=preview_plan('生成一张9:16海报',size='2160x3840');assert plan[0]['size']=='2160x3840'
    assert quality_value('high')=='high'
    task=executor.submit(actor,payload(message='生成一张9:16海报',size='2160x3840',quality='high'))
    assert task['status']=='queued'
    saved=executor.submission(task['id']);assert saved['size']=='2160x3840' and saved['quality']=='high'
    assert calls==[] and executor.budget.summary()['image']['requests']==0


def test_owner_demo_credit_is_once_bounded_audited_and_tenant_scoped(image_fixture,monkeypatch):
    executor,actors,_=image_fixture;actor=actors[0];other=actors[1];monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    with executor.main.store.connection() as conn:
        conn.execute('update credit_accounts set balance=0 where tenant_id=?',(actor.tenant_id,))
        previous=conn.execute('select * from credit_transactions').fetchall()
    with pytest.raises(PermissionError):grant_once(executor.main.product_store,actor.tenant_id,other.user_id)
    result=grant_once(executor.main.product_store,actor.tenant_id,actor.user_id);assert result['added']==1000
    again=grant_once(executor.main.product_store,actor.tenant_id,actor.user_id);assert again['added']==0 and again['already_applied']
    with executor.main.store.connection() as conn:
        assert conn.execute('select balance from credit_accounts where tenant_id=?',(other.tenant_id,)).fetchone()[0]==200
        assert len(conn.execute('select * from credit_transactions').fetchall())==len(previous)+1
    task=executor.main.product_store.create_task(actor.tenant_id,actor.user_id,'campaign-agent','FIXTURE_ONLY admission',None)
    assert task['status']=='queued'  # Intentionally not scheduled; Provider calls=0.
    monkeypatch.delenv('CUSTOMER_PREVIEW_MODE')
    with pytest.raises(PermissionError):grant_once(executor.main.product_store,actor.tenant_id,actor.user_id)


def test_preview_body_only_and_style_preserve_real_prose_and_ignore_ambiguous_markers():
    content='# 正式标题\n\n真实导语。\n\n## 倾听\n\n保留全部正文。\n\n## 行动\n\n结尾。'
    packaged='## 标题候选\n\n1.候选甲\n2.候选乙\n3.候选丙\n\n## 正文\n\n'+content
    assert body_only_markdown(packaged)==content
    ambiguous='# 写作思路与教育\n\n这是文章的真实主题与正文。'
    assert body_only_markdown(ambiguous)==ambiguous
    html=render(packaged);tree=ArticleTree(html)
    assert '候选甲' not in html and all(t in html for t in ['真实导语','保留全部正文','结尾'])
    headings=list(tree.root.iter('h2'));assert [next(h.iter('span')).text for h in headings]==['01','02']
    assert all('font-size:30px' in h.get('style') and 'border:0' in h.get('style') for h in headings)
    assert 'line-height:1.95' in html and 'margin:0 0 24px' in html
    before=''.join(tree.root.itertext());normalize_tree(tree.root);assert ''.join(tree.root.itertext())==before
    assert 'font-size:28px!important' in full_document(html,'标题')


def test_preview_renderer_keeps_image_permissions_and_sanitizes_scripts():
    html=render('# 标题\n\n## 章节\n\n![授权图](/api/v1/storage/generated/preview-fixture-a/test.png)\n\n<script>PAYLOAD</script>')
    assert '/api/v1/storage/generated/preview-fixture-a/test.png' in html
    tree=ArticleTree('<h1>标题</h1><p>正文</p><script>PAYLOAD</script><iframe>SECRET</iframe><img src="https://evil.invalid/a.png">')
    assert 'PAYLOAD' not in tree.html() and 'SECRET' not in tree.html() and 'evil.invalid' not in tree.html()


def test_capability_separates_tenant_global_and_per_task_limits(setup):
    service,actors,old,budget=setup;actor=actors[0]
    service.authorization={**GRANT,'allowed_tenant_ids':[actor.tenant_id]}
    assert service.capability(actors[1])['eligibility_code']=='ILLUSTRATION_TENANT_NOT_AUTHORIZED'
    assert service.capability(actor)['enabled']
    task=completed_task(service,actor);plan=service.plan(actor,task['id'])
    service.authorization['max_images_per_task']=1
    blocked=service.get(actor,plan['id']);assert not blocked['can_generate'] and blocked['blocker_code']=='ILLUSTRATION_TASK_LIMIT'
    service.authorization['max_images_per_task']=5
    service.authorization['max_requests']=1
    blocked=service.get(actor,plan['id']);assert not blocked['can_generate'] and blocked['blocker_code']=='ILLUSTRATION_BUDGET_EXHAUSTED'
    with service.db() as conn:conn.execute('insert into illustration_requests(id,plan_id,item_index,ceiling,status,created) values(?,?,?,?,?,?)',('fixture-used','old-fixture-plan',0,'0.1','failed',0))
    assert service.capability(actor)['eligibility_code']=='ILLUSTRATION_BUDGET_EXHAUSTED'
    assert old==[] and budget.summary()['image']['requests']==0


def test_wechat_preview_install_does_not_change_other_agents_or_shared_defaults(monkeypatch):
    from app import wechat_article_format
    from app.agent_catalog import CATALOG
    from scripts.customer_preview import add_preview_agent
    from app.preview_wechat_article import install
    before=dict(CATALOG);original=wechat_article_format.render_wechat_article
    monkeypatch.setattr(wechat_article_format,'render_wechat_article',original)
    for key,value in before.items():monkeypatch.setitem(CATALOG,key,value)
    monkeypatch.setitem(CATALOG,'preview-wechat-agent',before.get('preview-wechat-agent'))
    monkeypatch.setenv('CUSTOMER_PREVIEW_MODE','true')
    add_preview_agent();assert BODY_POLICY in CATALOG['preview-wechat-agent'].instructions
    for key in ('campaign-agent','image-agent'):assert CATALOG[key]==before[key]
    assert '三个标题候选' in original('# 标题\n\n三个标题候选是这个测试的正文。')
    monkeypatch.delenv('CUSTOMER_PREVIEW_MODE')
    with pytest.raises(RuntimeError):install()
