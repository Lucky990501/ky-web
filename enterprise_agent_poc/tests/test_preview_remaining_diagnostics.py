"""FIXTURE_ONLY new diagnostics/Flash compatibility; no Provider."""
import asyncio
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.preview_provider_diagnostics import provider_error_diagnostic,request_diagnostic
from app.preview_budget import plaintext_history,bounded_text_output
from app.test_text_gate import TestTextGate


@pytest.mark.parametrize('value',[None,[],{'error':{'code':['secret'],'type':{},'param':['input']}},{'error':{'code':'sk-PRIVATE','type':'private_name','param':'PRIVATE_TEXT','message':'do not retain customer material'}}])
def test_provider_error_never_retains_unknown_data(value):
    result=provider_error_diagnostic(400,value)
    assert result=={'http_status':400,'code':'UNKNOWN','type':'UNKNOWN','param':'UNKNOWN','category':'provider_request_rejected'}
    assert 'PRIVATE' not in json.dumps(result)


def test_allowlisted_error_fields_and_category_only():
    result=provider_error_diagnostic(400,{'error':{'code':'invalid_parameter','type':'invalid_request_error','param':'tool_choice','message':'Unsupported tool_choice; PRIVATE customer content'}})
    assert result['code']=='invalid_parameter' and result['param']=='tool_choice'
    assert result['category']=='tool_choice_parameter' and 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('kind',['minimal','sse','function','schema','sdk_namespace'])
def test_flash_response_matrix_on_formal_request_checker(kind):
    body={'model':'deepseek-flash','max_output_tokens':8192,'input':[{'role':'user','content':'FIXTURE_ONLY'}]}
    if kind=='sse':body['stream']=True
    if kind=='function':
        body['tools']=[{'type':'function','name':'enterprise_config_get','parameters':{'type':'object','properties':{}}}]
        body['input'] += [{'type':'function_call','name':'enterprise_config_get','call_id':'fixture-call','arguments':'{}'},
            {'type':'function_call_output','call_id':'fixture-call','output':'{"brand_name":"FIXTURE_ONLY"}'}]
    if kind=='schema':body['text']={'format':{'type':'json_schema','name':'fixture_result','schema':{'type':'object','properties':{'complete':{'type':'boolean'}},'required':['complete'],'additionalProperties':False}}}
    if kind=='sdk_namespace':body['tools']=[{'type':'namespace','name':'mcp__platform','tools':[{'type':'function','name':'knowledge_search','parameters':{'type':'object'}}]}]
    normalized,_=plaintext_history(body);normalized,_=TestTextGate.provider_tools(normalized)
    accepted,cost=bounded_text_output(normalized,{'text_input_cny_per_million':2,'text_output_cny_per_million':8},'campaign-agent')
    assert TestTextGate.request_bound(accepted)['input_tokens_upper']>0 and Decimal(0)<cost<=5
    diagnostic=request_diagnostic(accepted)
    assert diagnostic['model']=='deepseek-flash' and 'FIXTURE_ONLY' not in json.dumps(diagnostic)


@pytest.mark.parametrize('mode,stage,reason',[
    ('invalid_knowledge','knowledge','preview_image_knowledge_no_evidence'),
    ('missing_identity','knowledge','preview_image_knowledge_source_unverified'),
    ('invalid_assets','asset_search','preview_context_result_invalid'),
    ('asset_error','asset_search','preview_context_tool_failed')])
def test_image_stage_failure_retains_no_raw_payload(mode,stage,reason):
    from app.preview_image_context import collect_context
    class Session:
        async def call_tool(self,name,args):
            if name=='knowledge_search':
                value={'result':[]} if mode not in {'invalid_knowledge','missing_identity'} else ({'unexpected':1} if mode=='invalid_knowledge' else {'result':[{'content':'PRIVATE'}]})
                return SimpleNamespace(isError=False,structuredContent=value,content=[])
            return SimpleNamespace(isError=mode=='asset_error',structuredContent={'unexpected':'PRIVATE'},content=[])
    budget=Mock();task={'id':'fixture-task','tenant_id':'preview-fixture','user_id':'fixture-user','input_text':'PRIVATE'}
    with pytest.raises(RuntimeError,match=reason) as error:asyncio.run(collect_context(Session(),task,budget))
    assert error.value.preview_image_stage==stage
    events=[c.args[0] for c in budget.observe.call_args_list]
    assert any(e['stage']==stage and e['status']=='failed' for e in events)
    assert 'PRIVATE' not in json.dumps(events)


def test_image_success_marks_context_and_keeps_fact_sources():
    from app.preview_image_context import collect_context
    class Session:
        async def call_tool(self,name,args):
            result=[{'file_id':'f','chunk_id':'c','filename':'FIXTURE.md','content':'PRIVATE'}] if name=='knowledge_search' else []
            return SimpleNamespace(isError=False,structuredContent={'result':result},content=[])
    budget=Mock();task={'id':'fixture-task','tenant_id':'preview-fixture','user_id':'fixture-user','input_text':'PRIVATE'}
    text,audit=asyncio.run(collect_context(Session(),task,budget))
    stages=[c.args[0] for c in budget.observe.call_args_list if c.args[0]['event']=='preview_image_stage']
    assert [e['stage'] for e in stages]==['knowledge','knowledge','asset_search','asset_search','context_build','context_build']
    assert 'PRIVATE' not in json.dumps(stages) and audit['sources'][0]['file_id']=='f'


def test_observed_successful_empty_knowledge_loop_stops_after_two_without_fake_facts():
    from app.preview_request_context import compact_knowledge_history
    body={'input':[{'role':'user','content':'FIXTURE_ONLY complete activity facts'}]}
    for number in range(45):
        body['input'] += [{'type':'function_call','name':'mcp__platform__knowledge_search','call_id':str(number),'arguments':'{}'},
            {'type':'function_call_output','call_id':str(number),'output':'Output:\n{"result":[]}'}]
        result,counts=compact_knowledge_history(body)
        assert result==body and counts['redundant_search_tail']==number+1
        if number==1:assert counts['redundant_search_tail']>=2
    assert counts['repeated_source_bodies']==0
