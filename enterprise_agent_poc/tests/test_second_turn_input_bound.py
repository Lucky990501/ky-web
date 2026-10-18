"""Finite text only; SDK null metadata never authorizes opaque context."""
import copy,json
import pytest
from app.test_text_gate import TestTextGate,TestTextGateDenied


def payload(item):
    return {'model':'deepseek-v4-pro','reasoning':{'effort':'high'},
            'max_output_tokens':8192,'input':[item]}


@pytest.mark.parametrize('field,part',[('content','reasoning_text'),('summary','summary_text')])
def test_plaintext_reasoning_all_text_is_measured_and_preserved(field,part):
    item={'type':'reasoning','content':None,'summary':[],'encrypted_content':None}
    item[field]=[{'type':part,'text':'真实中文 SYNTHETIC'}]
    p=payload(item);before=copy.deepcopy(p)
    first=TestTextGate.request_bound(p)
    p['input'][0][field][0]['text']+='测'*1000
    second=TestTextGate.request_bound(p)
    assert second['input_tokens_upper']>=first['input_tokens_upper']+6000
    normalized=TestTextGate.bounded_payload(json.dumps(before).encode())
    assert normalized==before


@pytest.mark.parametrize('opaque',['','ciphertext',False,0,[],{}])
def test_non_null_opaque_history_is_never_admitted(opaque):
    with pytest.raises(TestTextGateDenied,match='input_bound_not_proven'):
        TestTextGate.request_bound(payload({'type':'reasoning','summary':[],'encrypted_content':opaque}))


@pytest.mark.parametrize('change',[
    {'previous_response_id':'hidden'},
    {'conversation':'hidden'},
    {'unrecognized_field':'unknown'},
])
def test_unknown_or_remote_top_level_context_rejected(change):
    with pytest.raises(TestTextGateDenied,match='input_bound_not_proven'):
        TestTextGate.request_bound({**payload({'type':'reasoning','summary':[]}),**change})


@pytest.mark.parametrize('item',[
    {'type':'reasoning','summary':[],'unknown':'hidden'},
    {'type':'reasoning','content':[{'type':'input_image','image_url':'remote'}],'summary':[]},
    {'type':'reasoning','summary':[{'type':'summary_text','text':'visible','annotations':[{'type':'opaque'}]}]},
    {'type':'reasoning','summary':[{'type':'summary_text','text':{},'extra':'unknown'}]},
    {'type':'reasoning','summary':None},
    {'type':'reasoning','content':[{'type':'reasoning_text','text':{},'extra':'unknown'}],'summary':[]},
])
def test_unknown_multimodal_and_malformed_reasoning_rejected(item):
    with pytest.raises(TestTextGateDenied,match='input_bound_not_proven'):
        TestTextGate.request_bound(payload(item))
