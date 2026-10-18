"""A content block alone cannot authorize another paid HTTP request."""
import asyncio,json
from uuid import uuid4
import pytest
from openai_codex.generated.v2_all import McpToolCallThreadItem
from app.stage2_two_probe_gate import Stage2TextGate,ProbeBudget
from app.test_text_gate import TestTextGateDenied

def fixture(tmp_path):
    identity={'kind':'EXPLICIT_SYNTHETIC_NOT_AUTHORITY'}
    g=object.__new__(Stage2TextGate)
    g.budget=ProbeBudget.initialize(tmp_path/'synthetic.sqlite',identity,lambda:identity)
    task,call=str(uuid4()),str(uuid4())
    g.contexts={'synthetic':('stage25-test-tenant',task,str(uuid4()),str(uuid4()),str(uuid4()))}
    content=[{'type':'text','text':'EXPLICIT_SYNTHETIC_NO_CUSTOMER_DATA'}]
    item=McpToolCallThreadItem.model_validate(dict(type='mcpToolCall',id=call,server='platform',
        tool='enterprise_config_get',arguments={},status='completed',result={'content':content}))
    payload={'input':[{'type':'function_call','call_id':call,'name':'mcp__platform__enterprise_config_get'},
        {'type':'function_call_output','call_id':call,'output':'Wall time: 0\nOutput:\n'+json.dumps(content)}]}
    return g,task,item,payload

def test_exact_sdk_task_call_tool_status_and_content(tmp_path):
    g,t,item,p=fixture(tmp_path);g.observe_mcp_tool(t,item);g.tool_outcomes(p,task_id=t)
    with g.budget.connect() as c:
        row=dict(c.execute('SELECT * FROM tool_observations').fetchone())
    assert row['task_id']==t and row['call_id']==item.id and row['status']=='completed'
    assert row['tool']=='enterprise_config_get' and row['source']=='SDK_TYPED_ITEM_COMPLETED'
    assert 'EXPLICIT_SYNTHETIC_NO_CUSTOMER_DATA' not in json.dumps(row)

@pytest.mark.parametrize('change',['no_observation','other_task','other_call','other_tool','changed_content','failed','unknown_status','transport_error','missing_result','duplicate_call','is_error'])
def test_unknown_mismatch_and_error_stop_before_next_send(tmp_path,change):
    g,t,item,p=fixture(tmp_path)
    if change=='failed':item.status=type(item.status).failed
    if change=='unknown_status':item.status=type(item.status).in_progress
    if change=='transport_error':
        from openai_codex.generated.v2_all import McpToolCallError
        item.error=McpToolCallError(message='EXPLICIT_SYNTHETIC')
    if change=='missing_result':item.result=None
    if change!='no_observation':g.observe_mcp_tool(t,item)
    if change=='other_task':t=str(uuid4())
    if change=='other_call':p['input'][1]['call_id']=str(uuid4())
    if change=='other_tool':p['input'][0]['name']='mcp__platform__knowledge_search'
    if change=='changed_content':p['input'][1]['output']+='MUTATED'
    if change=='duplicate_call':p['input'].insert(0,p['input'][0].copy())
    if change=='is_error':p['input'][1]['output']='\nOutput:\n'+json.dumps({'result':{},'isError':True})
    with pytest.raises(TestTextGateDenied):g.tool_outcomes(p,task_id=t)
    with g.budget.connect() as c:
        assert c.execute('SELECT stopped FROM control').fetchone()[0]
        assert c.execute('SELECT count(*) FROM attempts').fetchone()[0]==0

def test_conflicting_completed_observation_cannot_be_replaced(tmp_path):
    g,t,item,p=fixture(tmp_path);g.observe_mcp_tool(t,item)
    item.tool='knowledge_search'
    with pytest.raises(TestTextGateDenied):g.observe_mcp_tool(t,item)
