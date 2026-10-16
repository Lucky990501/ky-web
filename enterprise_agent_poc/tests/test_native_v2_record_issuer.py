"""Directed complete CLI checks. All decisions/reports are FIXTURE_NOT_AUTHORITY."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import sys
import time

import pytest

ENTRY=Path(__file__).resolve().parents[1]/'scripts/native_v2_record_issuer.py'
spec=importlib.util.spec_from_file_location('native_v2_issuer_unit',ENTRY)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=value if isinstance(value,bytes) else mod.enc(value)
    path.write_bytes(raw);path.chmod(0o600)
    return hashlib.sha256(raw).hexdigest()

@pytest.fixture
def packet(tmp_path):
    base=tmp_path/'native-v2-fixture-entry';base.mkdir(mode=0o700)
    rid='fixture-b38-direct-successor'
    for path in ('shared/release-state/native-v2-manual-issuance','shared/release-qualifications'):
        (base/path).mkdir(parents=True,mode=0o700)
    save(base/'release.lock',b'')
    candidate={'release_id':rid,'source_commit':'1'*40,'source_tree':'2'*40,
               'parent_source_commit':mod.B38,'archive_sha256':'3'*64,
               'raw_manifest_sha256':'4'*64,'manifest_sha256':'5'*64}
    reports={}
    for check in mod.CHECKS:
        raw=base/'shared/release-evidence'/rid/(check+'.raw.json')
        raw_hash=save(raw,{'fixture':True,'source_commit':candidate['source_commit'],
                       'source_tree':candidate['source_tree'],'assertions':1,'failures':0,'errors':0})
        path=raw.with_name(check+'.json');reports[check]=str(path)
        save(path,{'kind':mod.FIXTURE,'fixture':True,'check':check,
              'source_commit':candidate['source_commit'],'source_tree':candidate['source_tree'],
              'assertions':1,'failures':0,'errors':0,'raw_skipped':0,
              'unresolved_release_critical_skips':0,'resolved_skip_receipts':[],
              'observed_at':int(time.time()),'run_id':'fixture-'+check,
              'raw_format':'json','raw_path':str(raw),'raw_sha256':raw_hash})
    request=base/'request.json'
    value={'kind':'B38_NATIVE_V2_MANUAL_ISSUANCE_V1','fixture':True,'business_base_source':mod.B38,'candidate':candidate,
           'predecessor':dict(mod.PRIOR),'report_paths':reports,'binding_sha256':'6'*64,
           'schema_fingerprint':'7'*64,'issuer_sha256':hashlib.sha256(ENTRY.read_bytes()).hexdigest(),
           'principal':'Lucky','owner_seconds':86400,'root_seconds':3600,'maintenance_seconds':14400,
           'recovery_seconds':3600,'remaining_work_seconds':120,
           'review_text':'Synthetic isolated input only. No real decision, provider or release authority.',
           'fees':{'automatic_paid_retries':0,'real_provider_requests':0}}
    save(request,value)
    return base,request,value

def run(packet,action,ack=True,live=False):
    base,path,value=packet
    pin=hashlib.sha256(path.read_bytes()).hexdigest()
    args=[sys.executable,'-I','-B','-S',str(ENTRY),action,'--request',str(path),'--request-sha256',pin]
    if not live:args+=['--isolation-root',str(base)]
    pid,fd=pty.fork()
    if pid==0:os.execv(sys.executable,args)
    output=b'';sent=False;deadline=time.monotonic()+12
    try:
        while time.monotonic()<deadline:
            ready,_,_=select.select([fd],[],[],0.1)
            if ready:
                try:data=os.read(fd,65536)
                except OSError:break
                if not data:break
                output+=data
                match=re.search(rb'TYPE EXACTLY: ([^\r\n]+)',output)
                if match and not sent:
                    challenge=match.group(1)
                    assert challenge.startswith(b'FIXTURE-'), 'Never automate actual human confirmation'
                    os.write(fd,(challenge if ack else b'DECLINED')+b'\n');sent=True
        else:
            os.kill(pid,signal.SIGKILL);pytest.fail('fixture CLI timeout')
    finally:
        os.close(fd)
    _,status=os.waitpid(pid,0)
    return os.waitstatus_to_exitcode(status),output.decode(errors='replace')

def audit(packet):return packet[0]/'shared/release-state/native-v2-manual-issuance'/packet[2]['candidate']['release_id']
def qualify(packet):return packet[0]/'shared/release-qualifications'
def capture(packet):
    assert run(packet,'owner')[0]==0
    assert run(packet,'root')[0]==0

def test_fixture_complete_final_cli_and_independent_decisions(packet):
    assert run(packet,'review')[0]==0
    capture(packet)
    owner=json.loads((audit(packet)/'owner.json').read_bytes())
    root=json.loads((audit(packet)/'root.json').read_bytes())
    assert owner['role']!=root['role'] and owner['original_sha256']!=root['original_sha256']
    assert root['owner_sha256']==mod.sha((audit(packet)/'owner.json').read_bytes())
    assert root['expires_at']-root['issued_at']==3600
    code,text=run(packet,'seal');assert code==0,text
    files=list(qualify(packet).glob('*.json'));assert len(files)==2
    for path in files:
        value=json.loads(path.read_bytes())
        assert value['scope']==mod.FIXTURE and path.stat().st_mode&0o777==0o600
    evidence=json.loads(next(qualify(packet).glob('*.evidence.v1.json')).read_bytes())
    assert set(evidence['checks'])==set(mod.CHECKS)
    assert set(evidence['checks'].values())=={mod.FIXTURE}
    assert json.loads((audit(packet)/'seal.complete.json').read_bytes())['status']==mod.FIXTURE

def test_live_entry_rejects_fixture_host_before_any_decision(packet):
    assert run(packet,'owner',live=True)[0]==2
    assert not audit(packet).exists()

def test_no_person_confirmation_creates_no_archive(packet):
    code,text=run(packet,'owner',ack=False)
    assert code==2 and 'HUMAN_NOT_CONFIRMED' in text
    assert not audit(packet).exists()

@pytest.mark.parametrize('change,reason',[
    ('old_parent','B38_SUCCESSOR_IDENTITY'),('wrong_predecessor','D906_AND_TEN_REPORTS'),
    ('missing_report','D906_AND_TEN_REPORTS'),('missing_fixture_marker','REQUEST_KIND_OR_FIXTURE'),
    ('wrong_code_hash','ISSUER_OR_BINDING_IDENTITY'),('bad_window','EXISTING_MANUAL_WINDOW_RULES')])
def test_final_entry_rejects_identity_and_scope(packet,change,reason):
    base,path,p=packet
    if change=='old_parent':p['candidate']['parent_source_commit']='not-a-git-parent'
    if change=='wrong_predecessor':p['predecessor']['source_commit']='562'+'0'*37
    if change=='missing_report':p['report_paths'].pop('stage2')
    if change=='missing_fixture_marker':p['fixture']=False
    if change=='wrong_code_hash':p['issuer_sha256']='0'*64
    if change=='bad_window':p['maintenance_seconds']=999999
    save(path,p)
    code,text=run(packet,'review');assert code==2 and reason in text
    assert not audit(packet).exists()

def test_duplicate_json_and_0400_refused(packet):
    base,path,p=packet
    save(path,mod.enc(p).rstrip()[:-1]+b',"fixture":true}')
    assert 'DUPLICATE_JSON_KEY' in run(packet,'review')[1]
    save(path,p);path.chmod(0o400)
    assert 'PROTECTED_FILE' in run(packet,'review')[1]

@pytest.mark.parametrize('case',['missing','failure','hash','expired','raw_failure','fake_kind','wrong_source'])
def test_bad_real_evidence_stops_before_release_or_claim(packet,case):
    capture(packet)
    path=Path(packet[2]['report_paths']['full_pytest']);r=json.loads(path.read_bytes())
    if case=='missing':path.unlink()
    elif case=='raw_failure':
        raw=Path(r['raw_path']);v=json.loads(raw.read_bytes());v['failures']=1
        r['raw_sha256']=save(raw,v);save(path,r)
    else:
        if case=='failure':r['failures']=1
        if case=='hash':r['raw_sha256']='0'*64
        if case=='expired':r['observed_at']=1
        if case=='fake_kind':r['kind']='SOURCE_BOUND_NATIVE_V2_REPORT'
        if case=='wrong_source':r['source_commit']='0'*40
        save(path,r)
    assert run(packet,'seal')[0]==2
    assert not (audit(packet)/'release.json').exists()
    assert not (audit(packet)/'seal.claim.json').exists()
    assert list(qualify(packet).iterdir())==[]

@pytest.mark.parametrize('case',['owner_expired','root_expired','recovery_short','failed_claim','lock_busy'])
def test_time_replay_and_lock_before_human_release(packet,case):
    capture(packet)
    if case in ('owner_expired','root_expired'):
        p=audit(packet)/('owner.json' if case=='owner_expired' else 'root.json')
        d=json.loads(p.read_bytes());d['expires_at']=1;save(p,d)
    if case=='recovery_short':
        p=audit(packet)/'root.json';d=json.loads(p.read_bytes());d['maintenance_expires_at']=int(time.time())+3700;save(p,d)
    if case=='failed_claim':save(audit(packet)/'seal.claim.json',{'old_failed':True})
    lock=None
    if case=='lock_busy':
        lock=(packet[0]/'release.lock').open('rb');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    try:assert run(packet,'seal')[0]==2
    finally:
        if lock:lock.close()
    assert not (audit(packet)/'release.json').exists()
    assert list(qualify(packet).iterdir())==[]

def test_withdrawal_preserves_original_and_blocks_replay(packet):
    capture(packet);assert run(packet,'seal')[0]==0
    before=next(qualify(packet).glob('*.approval.v1.json')).read_bytes()
    assert run(packet,'seal')[0]==2
    assert run(packet,'revoke')[0]==0
    assert list(qualify(packet).glob('*.approval.v1.json'))==[]
    assert (audit(packet)/'approval.revoked-preserved.json').read_bytes()==before
    assert run(packet,'root')[0]==2 and run(packet,'seal')[0]==2

def test_native_frozen_consumer_rejects_fixture_record_schema(packet,monkeypatch):
    capture(packet);assert run(packet,'seal')[0]==0
    from scripts import schema015_successor_binding as frozen
    c=packet[2]['candidate'];root=packet[0]/'releases'/c['release_id']/'enterprise_agent_poc';root.mkdir(parents=True)
    manifest={'release_id':c['release_id'],'archive_sha256':c['archive_sha256']}
    save(root.parent/(c['release_id']+'.manifest.json'),manifest)
    monkeypatch.setattr(frozen,'declaration',lambda *a:{'successor':c})
    monkeypatch.setattr(frozen,'protected_json',lambda p:(json.loads(p.read_bytes()),mod.sha(p.read_bytes())))
    with pytest.raises(frozen.BindingBlocked,match='qualification_approval_identity'):
        frozen.qualification(packet[0],root,manifest,packet[2]['schema_fingerprint'])
