"""Private recovery fixtures only; no server/grant/Runtime mutations."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import types

import pytest
from test_successor import module, authority_fixture

HERE = Path(__file__).parent


@pytest.fixture
def c(): return module('partial_prepare_v5', HERE/'entry.py')


@pytest.fixture
def grant_fixture(c, authority_fixture, monkeypatch):
    old, _, control = authority_fixture
    c.OP = control/'operation'; c.OP.mkdir()
    c.RELEASE = control/'existing-release'; c.RELEASE.mkdir()
    c.CONTINUATION_GRANT = control/'partial-prepare-continuation.approved.v1.json'
    c.CONTINUATION_CLAIM = control/'partial-prepare-continuation-claimed.v1.json'
    attempt=c.OP/'ATTEMPTED.json'; attempt.write_text('original consumed attempt fixture')
    c.ORIGINAL_PARTIAL_RECORDS={'operation/ATTEMPTED.json':c.sha(attempt)}
    doc=dict(old,kind='EXACT_894_PARTIAL_PREPARE_METADATA_CONTINUATION_V1',
        status='APPROVED_SINGLE_USE', action='complete-prepare-metadata-only',
        application_source=c.SOURCE,application_tree=c.TREE,
        operation_path=str(c.OP),release_path=str(c.RELEASE),
        attempts=1,manual_retry=False,original_records=c.ORIGINAL_PARTIAL_RECORDS,
        issued_at=1,expires_at=1801,tooling_source='b'*40,tooling_tree='c'*40,
        write_paths=[str(c.CONTINUATION_CLAIM),str(c.OP/'prepare.json'),str(control/'runtime-binding.v1.json')],
        preflight_sha256='d'*64,system_python_manifest_sha256='e'*64,
        application_python_real_path='/fixture/unprivileged',application_python_sha256='f'*64)
    def write(): c.CONTINUATION_GRANT.write_text(json.dumps(doc))
    write()
    monkeypatch.setattr(c.time,'time',lambda:2)
    return doc,write


def test_exact_new_continuation_grant(c,grant_fixture):
    assert c.continuation_authority('d'*64,clock=lambda:2)['action']=='complete-prepare-metadata-only'


@pytest.mark.parametrize('field,bad',[
    ('application_source','0'*40),('application_tree','0'*40),('operation_path','/other'),
    ('runtime_predecessor_source','0'*40),('host','other-host'),('action','prepare'),
    ('expires_at',1),('issued_at',None),('expires_at',1802),('revoked',True),
    ('manual_retry',True),('attempts',2),('production_authority',True),('provider_budget',1),
    ('write_paths',['/arbitrary']),('original_records',{}),('tooling_source','fixture'),
    ('technical_decision_reference',''),('code_sha256',{}),('preflight_sha256','wrong')])
def test_inexact_expired_or_wrong_grant_refused(c,grant_fixture,field,bad):
    d,write=grant_fixture;d[field]=bad;write()
    with pytest.raises(c.Blocked): c.continuation_authority('d'*64,clock=lambda:2)


def test_missing_grant_never_inherits_old_consumed_scope(c,grant_fixture):
    c.CONTINUATION_GRANT.unlink()  # private fixture only
    with pytest.raises(FileNotFoundError): c.continuation_authority(clock=lambda:2)


def test_new_grant_revocation_file_refused(c,grant_fixture):
    (c.CONTROL/'CONTINUATION_REVOKED').touch()
    with pytest.raises(c.Blocked,match='REVOKED'): c.continuation_authority(clock=lambda:2)


@pytest.fixture
def completion(c,grant_fixture,monkeypatch):
    proof={'preflight_sha256':'d'*64,'witness':{'files':{'fixture':'exact'},'metadata_pins':{'current':'exact'}}}
    monkeypatch.setattr(c,'continuation_preflight',lambda **kw: proof)
    monkeypatch.setattr(c,'continuation_authority',lambda *a,**kw:grant_fixture[0])
    monkeypatch.setattr(c.os,'chown',lambda *a:None) # no Root ownership claimed by fixtures
    monkeypatch.setattr(c,'sealed_runtime',lambda:(c.read(c.CONTROL/'runtime-binding.v1.json'),c.read(c.OP/'prepare.json')))
    return proof,grant_fixture[0]


def test_legal_completion_writes_only_three_exclusive_records_preserves_original(c,completion):
    before=c.sha(c.OP/'ATTEMPTED.json'); contents=list(c.RELEASE.iterdir())
    assert c.continue_prepare()['status']=='PREPARED_NOT_TEST_PASS'
    assert c.sha(c.OP/'ATTEMPTED.json')==before and list(c.RELEASE.iterdir())==contents
    assert {p.name for p in c.OP.iterdir()}=={'ATTEMPTED.json','prepare.json'}
    assert c.CONTINUATION_CLAIM.exists() and (c.CONTROL/'runtime-binding.v1.json').exists()
    assert c.read(c.OP/'prepare.json')['completion_kind']=='ORIGINAL_PARTIAL_PREPARE_METADATA_ONLY'


def test_repeat_completion_refused_no_record_overwrite(c,completion):
    c.continue_prepare(); before=c.sha(c.OP/'prepare.json')
    with pytest.raises(c.Blocked,match='ALREADY_ATTEMPTED'):c.continue_prepare()
    assert c.sha(c.OP/'prepare.json')==before


def test_bad_staging_preflight_creates_no_claim(c,completion,monkeypatch):
    def bad(**kw):raise c.Blocked('PARTIAL_FILE_BYTES:fixture')
    monkeypatch.setattr(c,'continuation_preflight',bad)
    with pytest.raises(c.Blocked,match='PARTIAL_FILE_BYTES'):c.continue_prepare()
    assert not c.CONTINUATION_CLAIM.exists() and not (c.OP/'prepare.json').exists()


@pytest.mark.parametrize('failure_at',['prepare.json','runtime-binding.v1.json'])
def test_interrupted_partial_completion_is_not_success_and_not_retry(c,completion,monkeypatch,failure_at):
    original=c.record
    def failing(path,*a,**kw):
        if path.name==failure_at:raise OSError('private fixture failure')
        return original(path,*a,**kw)
    monkeypatch.setattr(c,'record',failing)
    with pytest.raises(OSError):c.continue_prepare()
    assert c.CONTINUATION_CLAIM.exists()
    with pytest.raises(c.Blocked,match='ALREADY_ATTEMPTED'):c.continue_prepare()


def test_expiry_after_preflight_before_claim_has_zero_writes(c,completion,monkeypatch):
    calls=[]
    def grant(*a):
        calls.append(1)
        if len(calls)==2:raise c.Blocked('CONTINUATION_AUTHORITY_EXPIRED')
        return completion[1]
    monkeypatch.setattr(c,'continuation_authority',grant)
    with pytest.raises(c.Blocked,match='EXPIRED'):c.continue_prepare()
    assert not c.CONTINUATION_CLAIM.exists() and not (c.OP/'prepare.json').exists()


@pytest.fixture
def private_tree(c,tmp_path,monkeypatch):
    root=tmp_path/'existing';root.mkdir();p=root/'original.txt';p.write_bytes(b'original');p.chmod(0o444);root.chmod(0o555)
    real=Path.lstat
    def ls(path):
        info=real(path)
        if path==Path('/tmp'):
            return types.SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0,st_gid=0)
        return info
    monkeypatch.setattr(Path,'lstat',ls)
    yield root,{'original.txt':{'sha256':c.sha(p),'mode':0o444}}
    root.chmod(0o755)


def test_actual_private_tree_complete_byte_map(c,private_tree):
    root,files=private_tree
    assert set(c.exact_partial_tree(root,files,set()))=={'.','original.txt'}


@pytest.mark.parametrize('bad',['bytes','missing','extra','mode','symlink','hardlink','empty-dir'])
def test_incomplete_or_corrupt_staging_never_accepted(c,private_tree,bad):
    root,files=private_tree;p=root/'original.txt';root.chmod(0o755)
    if bad=='bytes':p.chmod(0o644);p.write_bytes(b'changed');p.chmod(0o444)
    elif bad=='missing':p.unlink()
    elif bad=='extra':(root/'not-in-git').write_text('extra')
    elif bad=='mode':p.chmod(0o644)
    elif bad=='symlink':p.rename(root/'preserved');p.symlink_to(root/'preserved')
    elif bad=='hardlink':os.link(p,root/'second')
    elif bad=='empty-dir':(root/'unexpected').mkdir()
    root.chmod(0o555)
    with pytest.raises(c.Blocked):c.exact_partial_tree(root,files,set())


def test_entry_stays_under_original_lock_and_cannot_replay_install(c):
    tree=ast.parse((HERE/'entry.py').read_bytes());f={n.name:ast.unparse(n) for n in tree.body if isinstance(n,ast.FunctionDef)}
    assert 'with locked()' in f['main'] and "'continue-prepare': continue_prepare" in f['main']
    for name in ('continuation_preflight','partial_staging_bytes','continue_prepare'):
        assert not any(x in f[name] for x in ('formal(', 'extract_function(', '.mkdir(', '.rename(', '.unlink(', 'os.replace(', 'units('))
    assert 'NO_RETRY_OR_OVERWRITE' in f['prepare']
    assert 'PARTIAL_ONLY_TWO_RECEIPTS_MISSING_NO_RETRY' in f['continuation_preflight']
    assert 'CONTINUATION_READONLY_RUNTIME_ONLY' in f['runtime_call']


def test_historical_original_evidence_pins_remain_exact(c):
    assert c.ORIGINAL_PARTIAL_RECORDS['operation/ATTEMPTED.json']=='46847ceaec6e165e074872384336d9257f035d0eaa99a205f770851a56e8b120'
    assert c.ORIGINAL_PARTIAL_RECORDS['single-prepare-reauthorization-claimed.v1.json']=='f6caf929ad514dac296d8964fff7a6e13b229d900d7b106cc6b0cff224b3fde1'
    assert len(c.PARTIAL_GIT_PINS)==22 and len(c.PARTIAL_GIT_DIRS)==10
