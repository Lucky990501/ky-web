"""Exact finite-text branches; real SDK/FastMCP, explicitly simulated upstream."""
import asyncio,copy,hashlib,json,os,sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.test_text_gate import TestTextGate,TestTextGateDenied,input_shape
from app.stage2_two_probe_gate import Stage2TextGate,ProbeBudget
from test_stage2_multiturn_sdk import _sdk_case
from test_stage2_two_probe_gate import task,gate_for


def envelope(item=None):
    return {'model':'deepseek-v4-pro','reasoning':{'effort':'high'},'max_output_tokens':8192,
            'input':[item or {'role':'user','content':'SYNTHETIC_SECRET_MUST_NOT_BE_RECORDED'}]}


CASES=[
 ('payload_invalid_type',lambda p:[]),
 ('top_level_unknown',lambda p:{**p,'previous_response_id':'SYNTHETIC_SECRET'}),
 ('instructions_invalid_type',lambda p:{**p,'instructions':{}}),
 ('input_invalid_container',lambda p:{**p,'input':{}}),
 ('input_empty',lambda p:{**p,'input':[]}),
 ('item_invalid_container',lambda p:{**p,'input':[0]}),
 ('item_unknown_type',lambda p:envelope({'type':'SYNTHETIC_SECRET_UNKNOWN_TYPE'})),
 ('message_unknown_field',lambda p:envelope({'role':'user','content':'x','unknown_secret_key':'secret'})),
 ('message_invalid_role',lambda p:envelope({'role':'SYNTHETIC_SECRET_ROLE','content':'x'})),
 ('function_call_unknown_field',lambda p:envelope({'type':'function_call','unknown_secret_key':True})),
 ('function_call_invalid_structure',lambda p:envelope({'type':'function_call','call_id':0})),
 ('function_arguments_invalid_json',lambda p:envelope({'type':'function_call','call_id':'a','name':'b','arguments':'bad json'})),
 ('function_arguments_invalid_container',lambda p:envelope({'type':'function_call','call_id':'a','name':'b','arguments':'[]'})),
 ('function_output_unknown_field',lambda p:envelope({'type':'function_call_output','extra':'x'})),
 ('function_output_invalid_call_id',lambda p:envelope({'type':'function_call_output','call_id':0,'output':'x'})),
 ('custom_call_unknown_field',lambda p:envelope({'type':'custom_tool_call','extra':'x'})),
 ('custom_call_invalid_structure',lambda p:envelope({'type':'custom_tool_call','call_id':'x','name':'other','input':'x'})),
 ('reasoning_unknown_field',lambda p:envelope({'type':'reasoning','summary':[],'extra':'x'})),
 ('reasoning_opaque_history',lambda p:envelope({'type':'reasoning','summary':[],'encrypted_content':'SYNTHETIC_SECRET_CIPHER'})),
 ('reasoning_summary_invalid_container',lambda p:envelope({'type':'reasoning','summary':None})),
 ('tools_invalid_container',lambda p:{**p,'tools':{}}),
 ('tool_invalid_container',lambda p:{**p,'tools':[0]}),
 ('tool_definition_invalid_type',lambda p:{**p,'tools':[{'type':'unknown'}]}),
 ('custom_tool_invalid_identity',lambda p:{**p,'tools':[{'type':'custom','name':'unknown'}]}),
]
for prefix,item,field,kind in [
 ('message_content',{'role':'user'},'content','input_text'),
 ('function_output',{'type':'function_call_output','call_id':'x'},'output','output_text'),
 ('reasoning_content',{'type':'reasoning','summary':[]},'content','reasoning_text'),
 ('reasoning_summary',{'type':'reasoning'},'summary','summary_text')]:
    bad=[('invalid_container',{}),('part_invalid_container',[0]),
         ('part_invalid_type',[{'type':'input_image','text':'secret'}]),
         ('part_invalid_text',[{'type':kind,'text':{}}]),
         ('part_unknown_field',[{'type':kind,'text':'secret','unknown_secret_key':'secret'}]),
         ('part_annotations',[{'type':kind,'text':'secret','annotations':[{'secret':'body'}]}])]
    # Summary-container validation is its own explicit branch above.
    for suffix,value in bad:
        if prefix=='reasoning_summary' and suffix=='invalid_container':continue
        CASES.append((prefix+'_'+suffix,lambda p,i=item,f=field,v=value:envelope({**i,f:copy.deepcopy(v)})))


@pytest.mark.parametrize('branch,mutate',CASES,ids=[x[0] for x in CASES])
def test_every_finite_text_refusal_has_unique_safe_branch(branch,mutate):
    with pytest.raises(TestTextGateDenied) as caught:TestTextGate.request_bound(mutate(envelope()))
    exc=caught.value
    assert str(exc)=='test_text_gate_input_bound_not_proven'
    assert exc.branch_id==branch and exc.validation_stage=='request_bound'
    raw=json.dumps(exc.safe_structure)
    assert 'SYNTHETIC_SECRET' not in raw and 'unknown_secret_key' not in raw


def test_first_bounded_payload_and_transform_phases_are_not_missed():
    p=envelope();p['instructions']={};p['tools']=[{'type':'namespace','name':'fixture','tools':[]}]
    with pytest.raises(TestTextGateDenied) as caught:TestTextGate.bounded_payload(json.dumps(p).encode())
    assert (caught.value.branch_id,caught.value.validation_stage)==('namespace_instructions_invalid_type','pre_transform')
    with pytest.raises(TestTextGateDenied) as caught:TestTextGate.bounded_payload(json.dumps(envelope({'type':'reasoning','encrypted_content':'secret'})).encode())
    assert (caught.value.branch_id,caught.value.validation_stage)==('reasoning_opaque_history','post_transform')
    assert len(json.dumps(input_shape({'input':[{'type':'unknown','content':[{'type':'unknown','text':'s'}]*1000}]*1000})))<262144


@pytest.mark.parametrize('phase',['post_transform','request_bound'])
@pytest.mark.parametrize('sink_failure',[False,True])
def test_forward_durable_denial_and_sink_failure_never_send(tmp_path,monkeypatch,phase,sink_failure):
    identity={'kind':'FIXTURE_NOT_AUTHORITY','ledger':str(tmp_path/'budget.sqlite')}
    budget=ProbeBudget.initialize(tmp_path/'budget.sqlite',identity,lambda:identity)
    t=task();g=gate_for(budget,t);g.settings.data_dir=tmp_path/'private'
    evidence=tmp_path/'evidence';evidence.mkdir(mode=0o700);monkeypatch.setenv('STAGE2_FAILURE_EVIDENCE_DIR',str(evidence))
    principal=SimpleNamespace(runtime_profile_id='ctx',tenant_id=t['tenant_id'],agent_id=t['agent_id'])
    g.issuer=SimpleNamespace(verify=lambda *_:principal);g.contexts={'ctx':(t['tenant_id'],t['id'],t['agent_id'],t['user_id'],t['run_id'])}
    raw=json.dumps(envelope({'type':'reasoning','encrypted_content':'SYNTHETIC_SECRET'})).encode()
    if phase=='request_bound':
        # Inject AFTER the actual bounded_payload's first proof; second proof must diagnose too.
        original=g.bounded_payload
        def changed(raw,**kw):
            value,ns=original(json.dumps(envelope()).encode(),**kw)
            value['previous_response_id']='SYNTHETIC_SECRET';return value,ns
        g.bounded_payload=changed
    if sink_failure:
        import scripts.stage2_failure_snapshot as sink
        monkeypatch.setattr(sink,'durable_json',lambda *_:(_ for _ in ()).throw(OSError('synthetic')))
    with pytest.raises(TestTextGateDenied,match='input_bound_not_proven'):asyncio.run(g.forward('fixture',raw))
    files=list(evidence.glob('input-denial-*.json'))
    with sqlite3.connect(budget.path) as c:assert c.execute('SELECT count(*) FROM attempts').fetchone()[0]==0
    if sink_failure:assert files==[];return
    assert len(files)==1
    body=files[0].read_bytes();receipt=json.loads(body)
    assert receipt['validation_stage']==phase
    assert receipt['branch_id']==('top_level_unknown' if phase=='request_bound' else 'reasoning_opaque_history')
    assert receipt['request_sha256']==hashlib.sha256(raw).hexdigest()
    assert not receipt['admission_reserved'] and not receipt['real_http_started_for_rejected_request']
    assert 'SYNTHETIC_SECRET' not in body.decode() and receipt['runner_sha256']
    assert files[0].stat().st_mode&0o777==0o600 and receipt['gate_sha256']


@pytest.mark.parametrize('reasoning',['null_encrypted','content','summary','opaque'])
def test_actual_sdk_serialization_with_fastmcp_denial_receipt(tmp_path,monkeypatch,reasoning):
    case=tmp_path/'private';case.mkdir()
    evidence=tmp_path/'evidence';evidence.mkdir(mode=0o700)
    monkeypatch.setenv('STAGE2_FAILURE_EVIDENCE_DIR',str(evidence))
    if reasoning=='opaque':
        with pytest.raises(Exception):_sdk_case(case,monkeypatch,'complete','dict',reasoning=reasoning,mcp_first=True)
        snapshots=[json.loads(p.read_bytes()) for p in evidence.glob('input-denial-*.json')]
        assert len(snapshots)==1 and snapshots[0]['branch_id']=='reasoning_opaque_history'
        assert snapshots[0]['validation_stage']=='post_transform'
        assert snapshots[0]['prior_attempts']=={'requests':1,'http_complete':1}
        assert snapshots[0]['mcp_typed_completed_no_error']==1
        assert not snapshots[0]['admission_reserved'] and not snapshots[0]['real_http_started_for_rejected_request']
    else:
        _sdk_case(case,monkeypatch,'complete','dict',reasoning=reasoning,mcp_first=True)
        audit=json.loads((case/'MULTITURN_AUDIT.json').read_bytes())
        assert audit['runtime_tasks_completed']==2 and audit['http_sends']==6 and audit['real_provider_calls']==0
        assert not list(evidence.glob('input-denial-*.json'))


@pytest.mark.parametrize('fault,branch',[
 ('unknown_item','item_unknown_type'),('unknown_field','function_output_unknown_field'),
 ('output_part','function_output_part_invalid_type'),('instructions','namespace_instructions_invalid_type')])
def test_fault_injection_into_actual_sdk_second_request(tmp_path,monkeypatch,fault,branch):
    """Actual SDK wire, then explicit structural mutation; not historical recovery."""
    case=tmp_path/'private';case.mkdir();evidence=tmp_path/'evidence';evidence.mkdir(mode=0o700)
    monkeypatch.setenv('STAGE2_FAILURE_EVIDENCE_DIR',str(evidence))
    original=Stage2TextGate.bounded_payload
    def injected(raw,**kwargs):
        payload=json.loads(raw)
        outputs=[i for i in payload.get('input',[]) if i.get('type')=='function_call_output']
        if outputs:
            if fault=='unknown_item':payload['input'].append({'type':'SYNTHETIC_SECRET_UNKNOWN'})
            if fault=='unknown_field':outputs[0]['unknown_secret_key']='SYNTHETIC_SECRET'
            if fault=='output_part':outputs[0]['output']=[{'type':'input_image','text':'SYNTHETIC_SECRET'}]
            if fault=='instructions':payload['instructions']={}
            raw=json.dumps(payload).encode()
        return original(raw,**kwargs)
    monkeypatch.setattr(Stage2TextGate,'bounded_payload',staticmethod(injected))
    with pytest.raises(Exception):_sdk_case(case,monkeypatch,'complete','dict',mcp_first=True)
    receipts=[json.loads(p.read_bytes()) for p in evidence.glob('input-denial-*.json')]
    assert len(receipts)==1 and receipts[0]['branch_id']==branch
    assert receipts[0]['validation_stage']==('pre_transform' if fault=='instructions' else 'post_transform')
    assert receipts[0]['prior_attempts']['requests']==1
    assert not receipts[0]['admission_reserved'] and not receipts[0]['real_http_started_for_rejected_request']
    assert 'SYNTHETIC_SECRET' not in json.dumps(receipts)


@pytest.mark.parametrize('outcome',['mcp_error','mcp_timeout','identity_mismatch'])
def test_real_sdk_mcp_error_timeout_identity_stay_closed(tmp_path,monkeypatch,outcome):
    case=tmp_path/'private';case.mkdir();evidence=tmp_path/'evidence';evidence.mkdir(mode=0o700)
    monkeypatch.setenv('STAGE2_FAILURE_EVIDENCE_DIR',str(evidence))
    if outcome=='identity_mismatch':
        observe=Stage2TextGate.observe_mcp_tool
        def wrong_identity(self,task_id,item):
            return observe(self,task_id,item.model_copy(update={'tool':'knowledge_search'}))
        monkeypatch.setattr(Stage2TextGate,'observe_mcp_tool',wrong_identity)
    _sdk_case(case,monkeypatch,outcome,'dict')
    receipts=[json.loads(p.read_bytes()) for p in evidence.glob('input-denial-*.json')]
    assert len(receipts)==1 and receipts[0]['branch_id']=='stage2_tool_outcome_not_proven_success'
    assert not receipts[0]['admission_reserved'] and not receipts[0]['real_http_started_for_rejected_request']
