"""FIXTURE_ONLY; no external Provider, no real customer data."""
import copy
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from app.activity_plan_runtime import PLAN_SCHEMA, parse_plan, semantic_guard, render_markdown
from app.preview_budget import PreviewBudget, bounded_text_output, observe_text_terminal, preview_task_failure, PreviewBudgetExceeded
from app.preview_long_output import activity_schema_request, apply_policy, incomplete_message
from app.preview_text_guard import PreviewTaskTextGate
from app.test_text_gate import TestTextGate, TestTextGateDenied

PRICES={'text_input_cny_per_million':'2','text_output_cny_per_million':'8'}


def body(agent='preview-wechat-agent', requested=4096):
    b={'model':'deepseek-flash','stream':True,'reasoning':{'effort':'high'},
       'instructions':'FIXTURE_ONLY preserve facts and full requested content',
       'input':[{'role':'user','content':'FIXTURE_ONLY 生成完整文章'}],
       'tools':[{'type':'function','name':'mcp__platform__enterprise_config_get','parameters':{'type':'object','properties':{},'additionalProperties':False}}]}
    if requested is not None:b['max_output_tokens']=requested
    if agent=='campaign-agent':b['text']={'format':{'type':'json_schema','name':'ActivityPlanContent','strict':True,'schema':copy.deepcopy(PLAN_SCHEMA)}}
    return b


@pytest.mark.parametrize('agent',['campaign-agent','preview-wechat-agent'])
@pytest.mark.parametrize('sdk_max',[None,4096,8192])
def test_actual_dispatch_target_overrides_low_sdk_default_without_changing_facts(agent,sdk_max):
    b=body(agent,sdk_max);original=copy.deepcopy(b)
    out,cost=bounded_text_output(b,PRICES,agent)
    assert out['max_output_tokens']==16384 and out['reasoning']['effort']=='low'
    assert out['input']==original['input'] and out['instructions']==original['instructions'] and out['tools']==original['tools']
    assert b==original and cost<Decimal('5')
    assert TestTextGate.request_bound(out)['output_tokens_upper']==16384
    assert out['model']=='deepseek-flash'


@pytest.mark.parametrize('words',[600,1000,2000])
def test_wechat_lengths_preserved_and_full_html_fixture(words):
    from app.preview_wechat_article import render
    b=body();b['input'][0]['content']=f'FIXTURE_ONLY 需要{words}字完整文章'
    out,_=bounded_text_output(b,PRICES,'preview-wechat-agent')
    assert out['input']==b['input'] and out['max_output_tokens']==16384
    text='# FIXTURE_ONLY 标题\n\n导语。\n\n## 章节\n\n'+'学'*words+'\n\n## 结尾\n\n完整结尾。'
    html=render(text)
    assert html.count('学')==words and '完整结尾。' in html and '导语。' in html


def test_preparation_receives_space_but_forced_tool_keeps_contract():
    b=body('campaign-agent');b.pop('text')
    out,_=bounded_text_output(b,PRICES,'campaign-agent')
    assert out['max_output_tokens']==16384 and out['reasoning']['effort']=='low'
    assert apply_policy(b,'campaign-agent')[1]['phase']=='activity_preparation'
    b=body();b['tool_choice']={'type':'function','name':'mcp__platform__enterprise_config_get'}
    out,meta=apply_policy(b,'preview-wechat-agent')
    assert out==b and meta['phase']=='unchanged'


def test_exact_task_terminal_classifies_generic_sdk_failure(tmp_path):
    meter=PreviewBudget(tmp_path)
    meter.observe({'event':'text_http_terminal','task_id':'fixture-other','status':'incomplete'})
    assert not meter.task_output_incomplete('fixture-task')
    meter.observe({'event':'text_http_terminal','task_id':'fixture-task','status':'incomplete'})
    assert meter.task_output_incomplete('fixture-task')
    meter.observe({'event':'text_http_terminal','task_id':'fixture-task','status':'completed'})
    assert not meter.task_output_incomplete('fixture-task')


@pytest.mark.parametrize('agent',['copywriting-agent','preview-wechat-agent','campaign-agent'])
def test_existing_controlled_contract_not_increased(agent):
    b=body(agent);out,cost=bounded_text_output(b,PRICES,agent,controlled=True)
    assert out['max_output_tokens']==4096 and out['reasoning']['effort']=='high' and cost<=Decimal('.60')
    assert apply_policy(b,agent,controlled=True)[1]['phase']=='unchanged'


def test_formal_gate_does_not_inherit_preview_output_approval():
    b=body();b['model']='deepseek-v4-pro';b['max_output_tokens']=16384
    with pytest.raises(TestTextGateDenied):TestTextGate.bounded_payload(json.dumps(b))


def test_exact_schema_only_allows_legitimate_tooolless_final_phase():
    b=body('campaign-agent');b['tools']=[]
    assert activity_schema_request(b,'campaign-agent')
    assert not activity_schema_request(b,'preview-wechat-agent')
    b['text']['format']['schema']['properties']['arbitrary']={'type':'string'}
    assert not activity_schema_request(b,'campaign-agent')


def test_full_activity_json_preserves_all_fields_and_guard():
    data=json.loads((Path(__file__).parent/'fixtures/activity_plan_d1.json').read_text(encoding='utf-8'))
    data['activity_items']=[{'phase':'14:00-14:30','name':'签到交流','description':'家长与学生签到，工作人员介绍本次活动流程。','image_requirement':'写实的家长与学生签到现场。'}]
    data['pending_items']=[]
    data['invitation_copy']='欢迎家长和学生参加学习计划交流。'
    plan=parse_plan(json.dumps(data,ensure_ascii=False))
    assert plan is not None and semantic_guard(plan,())==[]
    markdown=render_markdown(plan)
    assert all(item.description in markdown and item.image_requirement in markdown for item in plan.activity_items)
    assert parse_plan(json.dumps(data)[:-10]) is None


@pytest.mark.parametrize('status',['incomplete','completed','failed'])
def test_real_shaped_terminal_diagnostics_and_save_barrier(tmp_path,status):
    meter=PreviewBudget(tmp_path)
    response={'status':status,'usage':{'input_tokens':200,'output_tokens':8192,'total_tokens':8392,'output_tokens_details':{'reasoning_tokens':7000}},
              'output':[{'type':'reasoning','content':[{'type':'reasoning_text','text':'SECRET_THOUGHT_NOT_TO_LOG'}]},
                        {'type':'message','status':'incomplete' if status=='incomplete' else 'completed','content':[{'type':'output_text','text':'PRIVATE_ARTICLE'}]}]}
    if status=='incomplete':response['incomplete_details']={'reason':'max_output_tokens'}
    observe_text_terminal(meter,'fixture-task',b'data: '+json.dumps({'type':'response.'+status,'response':response}).encode())
    gate=object.__new__(PreviewTaskTextGate);gate.guard=SimpleNamespace(budget=meter,check=lambda _:None);gate.task=lambda _:{}
    if status=='completed':gate.validate_completion('fixture-task')
    else:
        with pytest.raises(RuntimeError):gate.validate_completion('fixture-task')
    with sqlite3.connect(meter.path) as c:raw=c.execute('select payload from observations').fetchone()[0]
    record=json.loads(raw)
    assert record['reasoning_tokens']==7000 and record['non_reasoning_output_tokens']==1192 and record['visible_output_chars']==15
    assert 'SECRET_THOUGHT' not in raw and 'PRIVATE_ARTICLE' not in raw
    assert meter.summary()['text']['requests']==0


def test_unknown_terminal_cannot_save_and_messages_are_agent_specific(tmp_path):
    meter=PreviewBudget(tmp_path);meter.observe({'event':'text_http_attempt','task_id':'fixture-task'})
    gate=object.__new__(PreviewTaskTextGate);gate.guard=SimpleNamespace(budget=meter,check=lambda _:None);gate.task=lambda _:{}
    with pytest.raises(RuntimeError):gate.validate_completion('fixture-task')
    assert incomplete_message('campaign-agent').startswith('活动方案生成未完成')
    assert incomplete_message('preview-wechat-agent').startswith('文章生成未完成')
    assert preview_task_failure({}, {'error':'Incomplete response returned, reason: max_output_tokens'},None)=='MODEL_OUTPUT_INCOMPLETE'


def test_cost_bound_and_persistent_ledger_not_reset(tmp_path):
    meter=PreviewBudget(tmp_path);meter.reserve('text',Decimal('1'),agent_id='campaign-agent')
    before=meter.path.read_bytes()
    b=body('campaign-agent');b['input'][0]['content']='x'*50000
    out,cost=bounded_text_output(b,PRICES,'campaign-agent')
    assert out['max_output_tokens']==16384 and cost<5
    with pytest.raises(PreviewBudgetExceeded):bounded_text_output(b,{'text_input_cny_per_million':'200','text_output_cny_per_million':'800'},'campaign-agent')
    PreviewBudget(tmp_path)
    assert meter.path.read_bytes()==before
    assert meter.summary()['text']['requests']==1


def test_existing_successful_empty_lookup_loop_control_retained():
    from app.preview_request_context import compact_knowledge_history
    b=body('campaign-agent');b['input'] += sum(([{'type':'function_call','name':'mcp__platform__knowledge_search','call_id':str(i),'arguments':'{}'},
            {'type':'function_call_output','call_id':str(i),'output':json.dumps({'result':[]})}] for i in range(3)),[])
    _,diagnostic=compact_knowledge_history(b)
    assert diagnostic['redundant_search_tail']>=2
