"""FIXTURE_ONLY. No SDK/Provider requests, reservations or real customer text."""
import copy
from decimal import Decimal
import json

import pytest

from app.preview_budget import PREVIEW_ERRORS, PreviewBudgetExceeded, bounded_text_output
from app.test_text_gate import TestTextGate

PRICES={'text_input_cny_per_million':9,'text_output_cny_per_million':27}


def request():
    return {'model':'deepseek-v4-pro','instructions':'FIXTURE_ONLY activity JSON instructions',
            'input':[{'role':'user','content':'FIXTURE_ONLY short activity request'}],
            'tools':[{'type':'function','name':'mcp__platform__'+name,'parameters':{'type':'object'}}
                     for name in ['enterprise_config_get','knowledge_search','asset_search']]}


def test_short_activity_keeps_complete_json_output_space():
    original=request();before=copy.deepcopy(original)
    accepted,cost=bounded_text_output(original,PRICES,'campaign-agent')
    assert accepted['max_output_tokens']==8192 and cost<=Decimal('.60')
    assert original==before and accepted['tools']==original['tools']


def test_accumulated_tool_results_accept_ordinary_but_not_controlled():
    original=request()
    original['instructions']='FIXTURE_ONLY system/Skill context '+('规则'*2100)
    # Four five-chunk searches: keep every fact and citation in the fixture.
    for number in range(4):
        original['input'] += [
            {'type':'function_call','call_id':str(number),'name':'mcp__platform__knowledge_search','arguments':'{"query":"FIXTURE_ONLY"}'},
            {'type':'function_call_output','call_id':str(number),
             'output':json.dumps({'result':[{'chunk_id':f'{number}-{i}','content':'资料'*170} for i in range(5)]},ensure_ascii=False)},
        ]
    before=copy.deepcopy(original)
    accepted,cost=bounded_text_output(original,PRICES,'campaign-agent')
    assert accepted['max_output_tokens']==8192 and Decimal('.60')<cost<=Decimal('5')
    assert accepted['input']==original['input'] and original==before
    with pytest.raises(PreviewBudgetExceeded) as caught:
        bounded_text_output(original,PRICES,'campaign-agent',controlled=True)
    exc=caught.value;d=exc.cost_diagnostics
    assert exc.code=='PREVIEW_CONTEXT_COST_LIMIT' and original==before
    assert d['desired_output_tokens']==8192 and d['minimum_output_tokens']==2048
    assert d['request_ceiling_cny']=='0.60'
    assert Decimal(d['minimum_total_cny'])>Decimal('.60')
    bound=TestTextGate.request_bound({**original,'max_output_tokens':8192})['input_tokens_upper']
    assert d['input_tokens_upper']==bound
    assert Decimal(d['input_reserved_cny'])==Decimal(bound)*9/1_000_000
    assert Decimal(d['minimum_output_reserved_cny'])==Decimal('0.055296')
    assert Decimal(d['complete_output_reserved_cny'])==Decimal('0.221184')
    assert 'FIXTURE_ONLY' not in json.dumps(d) and '资料' not in json.dumps(d)
    assert '输入文字过长' not in str(exc) and '反复重试' in str(exc)


@pytest.mark.parametrize('agent,cap',[('copywriting-agent',4096),('preview-wechat-agent',8192)])
def test_other_agents_and_prices_unchanged(agent,cap):
    original=request();before=copy.deepcopy(original)
    accepted,cost=bounded_text_output(original,PRICES,agent)
    assert accepted['max_output_tokens']==cap and cost<=Decimal('.60')
    assert original==before


def test_error_clarifies_current_request_not_whole_task():
    text=PREVIEW_ERRORS['PREVIEW_CONTEXT_COST_LIMIT']
    assert '本次请求尚未调用模型' in text
    assert '新建会话' not in text
