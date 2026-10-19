"""FIXTURE_ONLY. No vendor credentials, external requests or real billed calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from app.preview_budget import PreviewBudget,PreviewBudgetExceeded,bounded_text_output,observe_text_terminal,preview_task_failure
from app.preview_text_guard import PreviewTextGuard,PreviewTaskTextGate
from app.security import RuntimeTokenIssuer,RuntimePrincipal,TokenError

SCOPE={'authorization_id':'FIXTURE_ONLY_FOUR','tenant_id':'preview-fixture-a','user_id':'fixture-user-a','agent_id':'preview-wechat-agent'}
TASK={'id':'fixture-task-a',**{k:SCOPE[k] for k in ('tenant_id','user_id','agent_id')},'status':'running'}
PRICES={'text_input_cny_per_million':'2','text_output_cny_per_million':'20'}

def write_grant(path,**override):
    path.write_text(json.dumps({'controlled_text_acceptance':{**SCOPE,'max_requests':4,**override}}),encoding='utf-8');path.chmod(0o600)

@pytest.fixture
def setup(tmp_path):
    budget=PreviewBudget(tmp_path);path=tmp_path/'grant.json';write_grant(path)
    guard=PreviewTextGuard(budget,path);guard.declare(SCOPE);guard.bind(TASK)
    return guard,budget,path

@pytest.mark.parametrize('failure',['missing','malformed','no_control','wrong_identity','wrong_auth','revoked','closed','world_readable','wrong_mode'])
def test_declared_task_never_falls_back_and_ordinary_visitor_works(setup,failure):
    guard,budget,path=setup
    if failure=='missing':path.unlink()
    elif failure=='malformed':path.write_text('{',encoding='utf-8')
    elif failure=='no_control':path.write_text('{}',encoding='utf-8')
    elif failure=='wrong_identity':write_grant(path,user_id='fixture-user-other')
    elif failure=='wrong_auth':write_grant(path,authorization_id='DIFFERENT')
    elif failure=='revoked':write_grant(path,revoked=True)
    elif failure=='closed':write_grant(path,max_requests=0,status='STOPPED_APPROVAL_EXCEEDED')
    elif failure in {'world_readable','wrong_mode'}:
        if os.name=='nt':pytest.skip('POSIX permission verification runs as real Preview uid in systemd')
        path.chmod(0o644 if failure=='world_readable' else 0)
    with pytest.raises(PreviewBudgetExceeded):guard.reserve(TASK,Decimal('.1'))
    other={**TASK,'id':'ordinary-task','user_id':'ordinary-user'};guard.bind(other);guard.reserve(other,Decimal('.1'))
    assert budget.summary()['text']['requests']==1

def test_unreadable_open_and_wrong_owner_never_dispatch(setup,monkeypatch):
    guard,budget,path=setup
    original=os.open
    def unreadable(p,*args,**kwargs):
        if Path(p)==path:raise PermissionError('FIXTURE_ONLY permission refused')
        return original(p,*args,**kwargs)
    monkeypatch.setattr(os,'open',unreadable)
    with pytest.raises(PreviewBudgetExceeded):guard.reserve(TASK,Decimal('.1'))
    assert budget.summary()['text']['requests']==0

def test_actual_foreign_owner_fixture_is_unreadable(setup):
    filename=os.environ.get('PREVIEW_GUARD_FOREIGN_OWNER_FIXTURE')
    if not filename:pytest.skip('Root-owned0600 fixture is supplied only by Linux systemd verifier')
    guard,budget,_=setup;guard.path=Path(filename)
    assert guard.path.stat().st_uid!=os.geteuid()
    with pytest.raises(PreviewBudgetExceeded):guard.reserve(TASK,Decimal('.1'))
    assert budget.summary()['text']['requests']==0

def test_four_allowed_fifth_denied_restart_and_concurrent(setup):
    guard,budget,path=setup
    def reserve(_):
        resumed=PreviewTextGuard(PreviewBudget(budget.path.parent),path)
        try:resumed.reserve(TASK,Decimal('.1'));return True
        except PreviewBudgetExceeded:return False
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(reserve,range(10)))
    assert sum(results)==4 and budget.summary()['text']['requests']==4
    with pytest.raises(PreviewBudgetExceeded):guard.reserve(TASK,Decimal('.1'))

def test_new_task_or_changed_identity_cannot_use_bound_grant(setup):
    guard,budget,path=setup
    with pytest.raises(PreviewBudgetExceeded):guard.bind({**TASK,'id':'second-task'})
    with pytest.raises(PreviewBudgetExceeded):guard.bind({**TASK,'tenant_id':'other-tenant'})
    with pytest.raises(PreviewBudgetExceeded):guard.check({**TASK,'user_id':'other-user'})
    assert budget.summary()['text']['requests']==0

def test_missing_config_at_task_bind_keeps_durable_declaration(setup):
    guard,budget,path=setup;path.unlink()
    with pytest.raises(PreviewBudgetExceeded):PreviewTextGuard(budget,path).bind(TASK)
    with sqlite3.connect(budget.path) as c:assert c.execute("select count(*) from observations where payload like '%preview_text_task_binding%'").fetchone()[0]==1

def test_explicit_new_declaration_cannot_reactivate_old_task(setup):
    guard,budget,path=setup;new_scope={**SCOPE,'authorization_id':'FIXTURE_EXPLICIT_SUCCESSOR'}
    guard.declare(new_scope);write_grant(path,authorization_id=new_scope['authorization_id'])
    with pytest.raises(PreviewBudgetExceeded):guard.bind(TASK)
    successor={**TASK,'id':'explicit-new-task'};guard.bind(successor);guard.reserve(successor,Decimal('.1'))
    write_grant(path)
    with pytest.raises(PreviewBudgetExceeded):guard.reserve(TASK,Decimal('.1'))
    assert budget.summary()['text']['requests']==1

def test_manager_task_path_credential_cache_restart_and_mcp_scopes(setup,tmp_path):
    guard,budget,path=setup
    class Store:
        def connection(self):
            c=sqlite3.connect(tmp_path/'tasks.sqlite');c.row_factory=sqlite3.Row;return c
    store=Store()
    with store.connection() as c:
        c.execute('create table tasks(id,tenant_id,user_id,agent_id,status)')
        c.execute('insert into tasks values(?,?,?,?,?)',tuple(TASK[k] for k in ('id','tenant_id','user_id','agent_id','status')))
    gate=PreviewTaskTextGate(store,guard,'http://127.0.0.1:18920/_preview_model')
    profile=SimpleNamespace(tenant_id=TASK['tenant_id'],agent_id=TASK['agent_id'])
    url,token=asyncio.run(gate.bind(profile,TASK['id']));assert url.endswith('/tasks/'+TASK['id'])
    assert asyncio.run(gate.bind(profile,TASK['id']))==(url,token)
    assert gate.resolve(TASK['id'],token)['user_id']==TASK['user_id']
    with pytest.raises(PreviewBudgetExceeded):gate.resolve('wrong-task',token)
    with pytest.raises(PreviewBudgetExceeded):gate.resolve(TASK['id'],'wrong-token')
    restarted=PreviewTaskTextGate(store,guard,gate.base_url)
    with pytest.raises(PreviewBudgetExceeded):restarted.resolve(TASK['id'],token)
    issuer=RuntimeTokenIssuer('FIXTURE_ONLY_SIGNING_SECRET')
    original=issuer.issue(RuntimePrincipal(TASK['tenant_id'],TASK['agent_id'],'fixture-profile',('enterprise_config:read','knowledge:search','assets:search'),int(__import__('time').time())+100))
    reduced=gate.protect_mcp_token(issuer,TASK['id'],original)
    assert issuer.verify(reduced,'enterprise_config:read').scopes==('enterprise_config:read',)
    with pytest.raises(TokenError):issuer.verify(reduced,'knowledge:search')
    write_grant(path,revoked=True)
    with pytest.raises(PreviewBudgetExceeded):gate.resolve(TASK['id'],token)

def test_bounded_compact_wechat_output_and_safe_incomplete_evidence(setup):
    guard,budget,path=setup
    body={'model':'fixture','input':[{'role':'user','content':'写约600字温和倾听孩子文章'}],'instructions':'Original Skill 1.0.1','tools':[]}
    article,cost=bounded_text_output(body,PRICES,'preview-wechat-agent',controlled=True)
    assert 4096<=article['max_output_tokens']<=8192 and cost<=Decimal('.60')
    assert 'Original Skill 1.0.1' in article['instructions'] and 'Markdown' in article['instructions']
    normal,_=bounded_text_output(body,PRICES,'copywriting-agent')
    assert normal['max_output_tokens']==4096 and normal['instructions']==body['instructions']
    short,_=bounded_text_output({**body,'max_output_tokens':1024},PRICES,'copywriting-agent')
    assert short['max_output_tokens']==1024
    with pytest.raises(PreviewBudgetExceeded):bounded_text_output({**body,'instructions':'x'*40000},{**PRICES,'text_input_cny_per_million':'50'},'preview-wechat-agent')
    observe_text_terminal(budget,TASK['id'],b'data: '+json.dumps({'type':'response.incomplete','response':{'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'},'output':[{'text':'DO_NOT_STORE'}],'usage':{'input_tokens':100,'output_tokens':4096}}}).encode())
    with sqlite3.connect(budget.path) as c:p=json.loads(c.execute('select payload from observations order by rowid desc limit 1').fetchone()[0])
    assert p['incomplete_reason']=='max_output_tokens' and 'DO_NOT_STORE' not in str(p)
    assert preview_task_failure({}, {'error':'Incomplete response returned, reason: max_output_tokens'},None)=='MODEL_OUTPUT_INCOMPLETE'

def test_compact_article_still_renders_warm_safe_complete_html():
    from app.wechat_article_format import render_wechat_article
    markdown='# 温和倾听孩子\n\n'+('陪伴孩子时，先听见心声，再一起寻找办法。'*30)+'\n\n## 给表达留时间\n\n**允许慢慢说**\n\n> 倾听也是支持\n\n## 一起寻找办法\n\n保持温和沟通。'
    html=render_wechat_article(markdown)
    assert html.count('<h1')==1 and html.count('<h2')==2 and '#FFCA00'.lower() in html.lower()
    assert '陪伴孩子时' in html and '保持温和沟通。' in html and '<blockquote' in html and html.endswith('</section>')
