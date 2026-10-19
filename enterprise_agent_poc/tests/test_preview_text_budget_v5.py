"""FIXTURE_ONLY: Owner-authorized cumulative limits; no Provider HTTP."""
import copy
from decimal import Decimal
import json
import sqlite3

import pytest

from app.preview_budget import (PreviewBudget,PreviewBudgetExceeded,bounded_text_output,
    text_request_ceiling,ORDINARY_TEXT_AGENTS)
from app.preview_text_guard import PreviewTextGuard
from app.test_text_gate import TestTextGate

PRICES={'text_input_cny_per_million':9,'text_output_cny_per_million':27}


def known_context():
    body={'model':'deepseek-v4-pro','max_output_tokens':8192,
        'instructions':'FIXTURE_ONLY complete Skill and structured output contract',
        'tools':[{'type':'function','name':'mcp__platform__'+name,'parameters':{'type':'object'}}
                 for name in ['enterprise_config_get','knowledge_search','asset_search']],
        'input':[{'role':'user','content':'FIXTURE_ONLY long business request'}]}
    for index in range(4):
        body['input'] += [
            {'type':'function_call','call_id':str(index),'name':'mcp__platform__knowledge_search','arguments':'{"query":"FIXTURE_ONLY"}'},
            {'type':'function_call_output','call_id':str(index),'output':json.dumps({'result':[{'chunk_id':str(index),'content':'FIXTURE_ONLY fact and citation'}]})}]
    padding=66998-TestTextGate.request_bound(body)['input_tokens_upper']
    assert padding>0
    body['instructions']+='x'*padding
    assert TestTextGate.request_bound(body)['input_tokens_upper']==66998
    return body


@pytest.mark.parametrize('agent',sorted(ORDINARY_TEXT_AGENTS))
def test_three_agents_accept_known_context_and_persistent_reserve(tmp_path,agent):
    original=known_context();before=copy.deepcopy(original)
    accepted,cost=bounded_text_output(original,PRICES,agent)
    expected_output=4096 if agent=='copywriting-agent' else 8192
    assert accepted['max_output_tokens']==expected_output
    assert cost==Decimal(66998*9+expected_output*27)/1_000_000
    assert Decimal('.60')<cost<=Decimal('5')
    assert original==before and all(accepted[k]==original[k] for k in ['model','instructions','input','tools'])
    meter=PreviewBudget(tmp_path);guard=PreviewTextGuard(meter,tmp_path/'ordinary-no-grant.json')
    task={'id':'fixture-task','tenant_id':'preview-fixture','user_id':'fixture-user','agent_id':agent}
    guard.bind(task);guard.reserve(task,cost)
    assert PreviewBudget(tmp_path).summary()['text']['reserved_ceiling_cny']==str(cost)


@pytest.mark.parametrize('agent',sorted(ORDINARY_TEXT_AGENTS))
def test_five_yuan_exact_allowed_but_excess_refused_before_reservation(tmp_path,agent):
    meter=PreviewBudget(tmp_path);meter.reserve('text',Decimal('5'),agent_id=agent)
    with pytest.raises(PreviewBudgetExceeded):meter.reserve('text',Decimal('5.000001'),agent_id=agent)
    assert meter.summary()['text']['requests']==1
    assert meter.summary()['text']['reserved_ceiling_cny']=='5'


@pytest.mark.parametrize('agent',[None,'unknown','image-agent'])
def test_non_target_agents_do_not_inherit_higher_ceiling(tmp_path,agent):
    assert text_request_ceiling(agent)==Decimal('.60')
    with pytest.raises(PreviewBudgetExceeded):PreviewBudget(tmp_path).reserve('text',Decimal('.61'),agent_id=agent)


def test_controlled_task_retains_point60_and_four_request_scope(tmp_path):
    meter=PreviewBudget(tmp_path);path=tmp_path/'grant.json';guard=PreviewTextGuard(meter,path)
    scope={'authorization_id':'FIXTURE_CONTROLLED','tenant_id':'preview-fixture','user_id':'fixture-user','agent_id':'preview-wechat-agent'}
    path.write_text(json.dumps({'controlled_text_acceptance':{**scope,'max_requests':4}}));path.chmod(0o600)
    task={'id':'fixture-task',**{k:scope[k] for k in ['tenant_id','user_id','agent_id']}}
    guard.declare(scope);guard.bind(task)
    with pytest.raises(PreviewBudgetExceeded):guard.reserve(task,Decimal('.600001'))
    assert meter.summary()['text']['requests']==0
    for _ in range(4):guard.reserve(task,Decimal('.60'))
    with pytest.raises(PreviewBudgetExceeded):PreviewTextGuard(PreviewBudget(tmp_path),path).reserve(task,Decimal('.01'))
    path.write_text('{}')
    with pytest.raises(PreviewBudgetExceeded):guard.reserve(task,Decimal('.01'))
    assert meter.summary()['text']['requests']==4


def test_controlled_output_still_refuses_known_context():
    with pytest.raises(PreviewBudgetExceeded) as error:
        bounded_text_output(known_context(),PRICES,'campaign-agent',controlled=True)
    assert error.value.cost_diagnostics['request_ceiling_cny']=='0.60'


def test_cost_above_five_remains_closed_with_actual_rate():
    body={'model':'deepseek-v4-pro','max_output_tokens':8192,
          'input':[{'role':'user','content':'FIXTURE_ONLY'} for _ in range(2200)]}
    with pytest.raises(PreviewBudgetExceeded) as error:bounded_text_output(body,PRICES,'campaign-agent')
    d=error.value.cost_diagnostics
    assert d['request_ceiling_cny']=='5.00' and Decimal(d['minimum_total_cny'])>5


def test_complete_output_is_not_silently_cut_to_fit_five():
    body={'model':'deepseek-v4-pro','max_output_tokens':8192,'instructions':'',
          'input':[{'role':'user','content':'FIXTURE_ONLY'} for _ in range(1700)]}
    body['instructions']='x'*(540000-TestTextGate.request_bound(body)['input_tokens_upper'])
    assert TestTextGate.request_bound(body)['input_tokens_upper']==540000
    with pytest.raises(PreviewBudgetExceeded) as error:bounded_text_output(body,PRICES,'campaign-agent')
    d=error.value.cost_diagnostics
    assert Decimal(d['minimum_total_cny'])<5<Decimal(d['complete_total_cny'])
    assert d['desired_output_tokens']==8192 and body['max_output_tokens']==8192


def test_text_amount_and_old_ledger_survive_restart(tmp_path):
    meter=PreviewBudget(tmp_path)
    with sqlite3.connect(meter.path) as c:
        c.executemany('insert into requests values(?,?,?)',[('text','5',1)]*119+[('text','4.9',2)])
        old=c.execute('select * from requests order by rowid').fetchall()
    resumed=PreviewBudget(tmp_path);resumed.reserve('text',Decimal('.1'),agent_id='campaign-agent')
    with pytest.raises(PreviewBudgetExceeded):resumed.reserve('text',Decimal('.01'),agent_id='campaign-agent')
    with sqlite3.connect(meter.path) as c:assert c.execute('select * from requests order by rowid limit 120').fetchall()==old
    assert resumed.summary()['text']['reserved_ceiling_cny']=='600.0'


def test_image_embedding_and_global_policy_are_unchanged_except_approved_totals():
    assert PreviewBudget.LIMITS=={'text':(1000,Decimal('600')),'image':(70,Decimal('70')),'embedding':(271,Decimal('15'))}
    assert PreviewBudget.TOTAL_LIMIT_CNY==685
