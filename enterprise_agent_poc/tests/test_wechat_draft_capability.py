"""Pure V2 authority contract negatives. These are not installed approvals."""
import copy
import pytest
from app.wechat_draft_capability import CONTRACT, ENDPOINTS, PINS, validate, load
from app.wechat_draft_operations import DraftOperationError


def fixture():
    binding=dict(environment='test',tenant_id='synthetic-tenant',user_id='synthetic-user',agent_id='synthetic-agent',
        agent_revision_id='synthetic-agent-revision',skill_revision_id='synthetic-skill-revision',account_identity='a'*64,secret_version=1)
    value=dict(binding,contract=CONTRACT,authority_id='synthetic-only',source_commit='b'*40,source_tree='c'*40,
        code_pins={name:'d'*64 for name in PINS},not_before=100,expires_at=200,revoked=False,
        network=dict(host='api.weixin.qq.com',port=443,endpoints=ENDPOINTS),max_requests=16)
    return binding,value,dict(source_commit='b'*40,source_tree='c'*40)


def test_exact_contract_only():
    binding,value,source=fixture();validate(value,binding,source,150)


@pytest.mark.parametrize('field,value',[
    ('contract','V1'),('environment','production'),('source_commit','a'*40),('source_tree','a'*40),
    ('tenant_id','other'),('user_id','other'),('agent_id','other'),('agent_revision_id','other'),('skill_revision_id','other'),
    ('account_identity','e'*64),('secret_version',2),('not_before',151),('expires_at',150),('expires_at',999999),
    ('revoked',True),('revoked',0),('code_pins',{}),('max_requests',0),('max_requests',33),('max_requests',True),
    ('network',dict(host='evil.invalid',port=443,endpoints=ENDPOINTS))])
def test_fail_closed(field,value):
    binding,approval,source=fixture();approval[field]=value
    with pytest.raises(DraftOperationError):validate(approval,binding,source,150)


def test_missing_extra_fields_and_source_mismatch():
    binding,value,source=fixture()
    for item in ({**value,'enabled':True},{k:v for k,v in value.items() if k!='code_pins'}):
        with pytest.raises(DraftOperationError):validate(item,binding,source,150)
    with pytest.raises(DraftOperationError):validate(value,binding,{},150)


def test_no_installed_authority_is_default_deny(monkeypatch,tmp_path):
    from app import wechat_draft_capability
    monkeypatch.setattr(wechat_draft_capability,'PATH',tmp_path/'absent')
    with pytest.raises(DraftOperationError):load(fixture()[0])
