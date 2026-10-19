"""FIXTURE_ONLY wire/continuation checks; no real Provider or customer content."""
import copy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from app.preview_budget import (PreviewBudget,PreviewInputRejected,bounded_text_output,
    plaintext_history,preview_text_wire,preview_input_message,observe_text_terminal,preview_task_failure)
from app.test_text_gate import TestTextGate,TestTextGateDenied,MAX_REQUEST_BYTES

PRICES={'text_input_cny_per_million':9,'text_output_cny_per_million':27}


def sdk_body():
    return {'model':'deepseek-v4-pro','reasoning':{'effort':'high'},'stream':True,
        'instructions':'FIXTURE_ONLY preserve business instructions',
        'input':[{'role':'developer','content':[{'type':'input_text','text':'FIXTURE_ONLY Skill'}]},
                 {'role':'user','content':[{'type':'input_text','text':'FIXTURE_ONLY business request'}]}],
        'tools':[{'type':'namespace','name':'mcp__platform','description':'FIXTURE_ONLY business tools',
                  'tools':[{'type':'function','name':'enterprise_config_get','parameters':{'type':'object'}}]}]}


@pytest.mark.parametrize('agent',['copywriting-agent','campaign-agent','preview-wechat-agent'])
def test_sdk_shaped_tool_continuation_above_old64k_preserves_full_output(agent):
    body=sdk_body();body['input'] += [
        {'type':'function_call','namespace':'mcp__platform','name':'enterprise_config_get','call_id':'fixture-call','arguments':'{}'},
        {'type':'function_call_output','call_id':'fixture-call','output':'FIXTURE_ONLY '+('x'*70000)}]
    safe,_=plaintext_history(body);mapped,namespaces=TestTextGate.provider_tools(safe)
    before=copy.deepcopy(mapped)
    wire=preview_text_wire(mapped);assert 65536<len(wire)<MAX_REQUEST_BYTES
    accepted,cost=bounded_text_output(mapped,PRICES,agent)
    assert accepted['max_output_tokens']==8192 and Decimal('.60')<cost<5
    assert accepted['input']==before['input'] and accepted['tools']==before['tools']
    assert accepted['model']=='deepseek-v4-pro' and accepted['reasoning']=={'effort':'high'}
    assert namespaces['mcp__platform__enterprise_config_get']==('mcp__platform','enterprise_config_get')


def test_copywriting_two_turn_inputs_and_final_terminal_are_complete(tmp_path):
    first=sdk_body();first['input'][-1]['content'][0]['text']='FIXTURE_ONLY 写招生口播，请问主题与人群'
    first,_=TestTextGate.provider_tools(first)
    first,_=bounded_text_output(first,PRICES,'copywriting-agent')
    assert first['max_output_tokens']==8192
    second=copy.deepcopy(first)
    second['input'] += [{'role':'assistant','content':[{'type':'output_text','text':'FIXTURE_ONLY 请补充招生主题和目标人群。'}]},
                        {'role':'user','content':[{'type':'input_text','text':'FIXTURE_ONLY 主题亲子阅读，面向小学生家长。'}]}]
    second,_=bounded_text_output(second,PRICES,'copywriting-agent')
    assert second['max_output_tokens']==8192 and len(second['input'])==4
    meter=PreviewBudget(tmp_path)
    event={'type':'response.completed','response':{'status':'completed','usage':{'input_tokens':100,'output_tokens':6000,'total_tokens':6100}}}
    observe_text_terminal(meter,'fixture-task',b'data: '+json.dumps(event).encode())
    import sqlite3
    with sqlite3.connect(meter.path) as c:observed=json.loads(c.execute('select payload from observations').fetchone()[0])
    assert observed['status']=='completed' and observed['usage']['output_tokens']==6000
    assert meter.summary()['text']['requests']==0
    assert preview_task_failure({}, {'error':'Incomplete response returned, reason: max_output_tokens'},None)=='MODEL_OUTPUT_INCOMPLETE'


def test_campaign_json_and_wechat_html_chains_remain_intact():
    from app.document_generator import ActivityPlanContent
    from app.activity_plan_runtime import result_envelope,render_markdown
    from app.preview_wechat_article import render
    data=json.loads((Path(__file__).parent/'fixtures/activity_plan_d1.json').read_text(encoding='utf-8'))
    plan=ActivityPlanContent.model_validate(data)
    assert result_envelope(plan)['data']==plan.model_dump(mode='json')
    assert plan.document_title in render_markdown(plan)
    html=render('# FIXTURE_ONLY 教育文章\n\n导语。\n\n## 倾听\n\n'+('温和倾听孩子。'*100))
    assert '<h1' in html and '<h2' in html and html.count('温和倾听孩子。')==100


def test_real_wire_limit_rejects_with_safe_branch():
    body={'input':[{'role':'user','content':'PRIVATE_NOT_TO_LOG'+('x'*MAX_REQUEST_BYTES)}]}
    with pytest.raises(PreviewInputRejected) as caught:preview_text_wire(body)
    exc=caught.value
    assert exc.branch_id=='preview_wire_too_large' and exc.validation_stage=='preview_text_preflight'
    assert exc.safe_structure['wire_limit_bytes']==262144
    assert 'PRIVATE_NOT_TO_LOG' not in json.dumps(exc.safe_structure)
    assert '大小上限' in preview_input_message(exc.branch_id)


def test_ascii_security_bound_remains_when_utf8_wire_fits():
    body=sdk_body();body['instructions']='资料'*24000
    body,_=TestTextGate.provider_tools(body)
    assert len(preview_text_wire(body))<262144
    with pytest.raises(TestTextGateDenied):bounded_text_output(body,PRICES,'campaign-agent')


def test_unknown_sdk_message_fields_are_still_rejected():
    body=sdk_body();body['input'][0]['unknown_sdk_field']='PRIVATE_NOT_TO_LOG'
    body,_=TestTextGate.provider_tools(body)
    with pytest.raises(TestTextGateDenied) as caught:bounded_text_output(body,PRICES,'preview-wechat-agent')
    assert caught.value.branch_id=='message_unknown_field'
    assert 'PRIVATE_NOT_TO_LOG' not in json.dumps(caught.value.safe_structure)
    assert '尚未兼容' in preview_input_message(caught.value.branch_id)


@pytest.mark.parametrize('item',[{'type':'item_reference','id':'opaque'}, {'type':'message','role':'user','content':'x','encrypted_content':'PRIVATE'}])
def test_opaque_or_unbound_history_remains_rejected(item):
    with pytest.raises(ValueError):plaintext_history({'input':[item]})


def test_controlled_copywriting_does_not_inherit_new_output_cap():
    body=sdk_body();body,_=TestTextGate.provider_tools(body)
    accepted,cost=bounded_text_output(body,PRICES,'copywriting-agent',controlled=True)
    assert accepted['max_output_tokens']==4096 and cost<=Decimal('.60')
