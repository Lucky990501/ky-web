"""B38 direct-successor Native V2 manual issuance, never a release operator.

Independently compare this entry's SHA256 before execution with Python -I -B -S.
Only the existing authenticated human controlling TTY can confirm Owner, Root,
and independent release decisions. This module neither runs qualification nor
creates evidence of successful execution. Its fixture lane cannot issue permits.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import time
import xml.etree.ElementTree as ET

BASE=Path('/opt/enterprise-agent-workbench')
HOST='iv-yekqs6ez9cnu262hhsg0'
B38='b38e37a1a9c6a8dc2f8b98b501f1e21a92a251f3'
D906='d9063ee43bc65e821457221ad824f5dde84fe408'
PRIOR={'release_id':'20261014-d9063ee-release-tty-pg1614-v1','source_commit':D906,
       'source_tree':'3a612584d278925afbcd1e2004fd47016fc9b6ce',
       'archive_sha256':'9d8f56ba6748ed44de6aa994f5972c86021ed02d1afd88de2fccfb9c2a54ee86',
       'raw_manifest_sha256':'30fb8c512253ce62cfce7c5bee7ef072c0d16dab5b436c47e94c12a7f7ab36fc',
       'manifest_sha256':'f54752cfd149526f98adad416a6626fdb02e347c356c52dc5ccc6b6ed50dd166'}
CHECKS=('full_pytest','stage2','source_installation','api_native','mcp_native',
        'worker_native','exact_predecessor_on_schema015','pre_commit_failure_recovery',
        'post_commit_runtime_recovery','business_data_preserved')
FIXTURE='FIXTURE_NOT_AUTHORITY'
HASH=re.compile(r'[0-9a-f]{64}\Z')
ID=re.compile(r'[A-Za-z0-9._:-]{1,128}\Z')

class Blocked(ValueError): pass
def need(ok,code):
    if not ok: raise Blocked(code)
def sha(raw): return hashlib.sha256(raw).hexdigest()
def enc(value): return (json.dumps(value,sort_keys=True,ensure_ascii=False,indent=2)+'\n').encode()
def digest(value): return sha(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
def pairs(rows):
    out={}
    for key,value in rows:
        need(key not in out,'DUPLICATE_JSON_KEY');out[key]=value
    return out
def parse(raw): return json.loads(raw,object_pairs_hook=pairs)

class Issuer:
    def __init__(self,path,pin,isolation=None):
        need(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,'SYSTEM_PYTHON_I_B_S_REQUIRED')
        self.fixture=isolation is not None
        if self.fixture:
            self.base=Path(isolation).absolute()
            need(os.uname().nodename!=HOST and self.base.name.startswith('native-v2-fixture-')
                 and self.base.is_relative_to(Path(os.environ.get('TMPDIR','/tmp'))), 'ISOLATION_CONTEXT_REQUIRED')
        else:
            need(os.uname().nodename==HOST and os.getuid()==os.getgid()==0,'EXACT_PRODUCTION_ROOT_REQUIRED')
            self.base=BASE
        self.uid=os.getuid()
        self.request_raw=self.read(Path(path),262144)
        need(HASH.fullmatch(pin) and sha(self.request_raw)==pin,'REQUEST_SHA256')
        self.pin=pin;self.p=parse(self.request_raw);p=self.p
        need(p['kind']=='B38_NATIVE_V2_MANUAL_ISSUANCE_V1' and p['fixture'] is self.fixture,'REQUEST_KIND_OR_FIXTURE')
        c=p['candidate'];self.rid=c['release_id']
        need(ID.fullmatch(self.rid) and p['business_base_source']==B38
             and re.fullmatch('[0-9a-f]{40}',c['parent_source_commit'])
             and c['parent_source_commit']!=c['source_commit']
             and re.fullmatch('[0-9a-f]{40}',c['source_commit'])
             and re.fullmatch('[0-9a-f]{40}',c['source_tree'])
             and c['source_commit'] not in {B38,D906},'B38_SUCCESSOR_IDENTITY')
        need(p['predecessor']==PRIOR and set(p['report_paths'])==set(CHECKS),'D906_AND_TEN_REPORTS')
        need(all(HASH.fullmatch(c[k]) for k in ('archive_sha256','raw_manifest_sha256','manifest_sha256')),'ACTUAL_CANDIDATE_HASHES')
        need(HASH.fullmatch(p['binding_sha256']) and HASH.fullmatch(p['schema_fingerprint'])
             and p['issuer_sha256']==sha(Path(__file__).read_bytes()),'ISSUER_OR_BINDING_IDENTITY')
        need(p['principal']=='Lucky' and p['owner_seconds']==86400 and p['root_seconds']==3600
             and p['maintenance_seconds']==14400 and p['recovery_seconds']==3600,'EXISTING_MANUAL_WINDOW_RULES')
        need(isinstance(p['review_text'],str) and p['review_text']
             and p['fees']['automatic_paid_retries']==0,'ACTUAL_REVIEW_AND_FEE_SCOPE_REQUIRED')
        need(type(p['remaining_work_seconds']) is int and 0<=p['remaining_work_seconds']<=10800,'FINITE_REMAINING_WORK')
        self.app_root=self.base/'releases'/self.rid/'enterprise_agent_poc'
        self.audit=self.base/'shared/release-state/native-v2-manual-issuance'/self.rid
        self.qual=self.base/'shared/release-qualifications'
        for item in p['report_paths'].values():
            q=Path(item)
            need(q.is_absolute() and q.is_relative_to(self.base/'shared/release-evidence'/self.rid)
                 and q.suffix=='.json' and '..' not in q.parts,'EXACT_REPORT_PATH')
        if not self.fixture:
            # Capture can run from an independently hash-verified preparation
            # copy before installation. Only seal imports the installed Source.
            need((self.base/'release-current').resolve()==self.base/'releases'/PRIOR['release_id']/'enterprise_agent_poc','CURRENT_D906_BEFORE_ISSUANCE')
            prior_raw=self.read(self.base/'releases'/PRIOR['release_id']/(PRIOR['release_id']+'.manifest.json'),protected=False)
            need(sha(prior_raw)==PRIOR['raw_manifest_sha256'],'CURRENT_D906_MANIFEST')

    def ancestors(self,path):
        need(path.is_absolute() and path.resolve(strict=True)==path,'CANONICAL_TRUSTED_PATH')
        for node in (path,*path.parents):
            if self.fixture and not node.is_relative_to(self.base):continue
            s=node.lstat()
            need(stat.S_ISDIR(s.st_mode) and s.st_uid==self.uid and not s.st_mode&0o022,'TRUSTED_ANCESTOR')

    def read(self,path,limit=262144,protected=True):
        self.ancestors(path.parent)
        with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as f:
            s=os.fstat(f.fileno())
            need(stat.S_ISREG(s.st_mode) and s.st_uid==self.uid and s.st_nlink==1
                 and not s.st_mode&0o022 and (not protected or stat.S_IMODE(s.st_mode)==0o600)
                 and s.st_size<=limit,'PROTECTED_FILE')
            raw=f.read(limit+1);end=os.fstat(f.fileno())
        after=path.lstat()
        need(len(raw)<=limit and (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==
             (end.st_dev,end.st_ino,end.st_size,end.st_mtime_ns)==
             (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'READ_RACE')
        return raw

    def put(self,path,value):
        raw=value if isinstance(value,bytes) else enc(value)
        self.ancestors(path.parent)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)
        need(self.read(path)==raw,'DURABLE_READBACK')

    def artifact(self):
        c=self.p['candidate'];raw=self.read(self.app_root.parent/(self.rid+'.manifest.json'),protected=False)
        m=parse(raw)
        need(sha(raw)==c['raw_manifest_sha256'] and digest(m)==c['manifest_sha256']
             and m['release_id']==self.rid and m['source_commit']==c['source_commit'],'ACTUAL_MANIFEST_IDENTITY')
        raw=self.read(self.app_root.parent/(self.rid+'.tar.gz'),64*1024*1024,False)
        need(sha(raw)==c['archive_sha256']==m['archive_sha256'],'ACTUAL_ARCHIVE_IDENTITY')
        with tarfile.open(fileobj=io.BytesIO(raw)) as bundle:
            members=bundle.getmembers();need(len(members)<=1000,'ARCHIVE_BOUND')
            seen=set()
            for item in members:
                q=PurePosixPath(item.name)
                need(not q.is_absolute() and '..' not in q.parts and '\\' not in item.name
                     and q.parts and q.parts[0]=='enterprise_agent_poc' and item.name not in seen
                     and (item.isdir() or item.isfile()),'ARCHIVE_PATH')
                seen.add(item.name)
                if item.isfile():
                    need(item.size<=8*1024*1024,'ARCHIVE_FILE_BOUND')
                    installed=self.app_root.parent/Path(*q.parts)
                    need(self.read(installed,8*1024*1024,False)==bundle.extractfile(item).read(),'INSTALLED_ARCHIVE_BYTES')
        need(m['runtime_only_release']['schema_version']==2
             and m['runtime_only_release']['successor']['source_tree']==c['source_tree']
             and m['runtime_only_release']['successor']['parent_source_commit']==c['parent_source_commit']
             and digest(m['runtime_only_release'])==self.p['binding_sha256'],'NATIVE_V2_DECLARATION')
        need(Path(__file__).resolve()==self.app_root/'scripts/native_v2_record_issuer.py','CANDIDATE_OWN_ENTRY_REQUIRED')
        # Every imported repository byte was compared with the approved immutable archive.
        sys.path.insert(0,str(self.app_root))
        from scripts import schema015_successor_binding as binding
        binding.declaration(self.app_root,m)
        need(self.p['predecessor']==binding.PREDECESSOR,'EXACT_D906_ARTIFACTS')
        self.manifest=m
        need((self.base/'release-current').resolve()==self.base/'releases'/binding.PREDECESSOR['release_id']/'enterprise_agent_poc','CURRENT_D906_BEFORE_ISSUANCE')
        return binding

    def valid(self,role):
        need(not os.path.lexists(self.audit/'revoked.json'),'AUTHORITY_REVOKED')
        raw=self.read(self.audit/(role+'.json'));v=parse(raw);now=int(time.time())
        need(v['kind']==(FIXTURE if self.fixture else 'ACTUAL_NATIVE_V2_HUMAN_DECISION')
             and v['role']==role and v['request_sha256']==self.pin and v['principal']=='Lucky'
             and v['issued_at']<=now<v['expires_at'],'DECISION_SOURCE_TIME_OR_SCOPE')
        need(v['original_sha256']==sha(v['original'].encode()) and v['tty'].startswith('/dev/'),'DECISION_ORIGINAL_SOURCE')
        return v,raw

    def confirm(self,role,detail,expires):
        with open('/dev/tty','r',encoding='utf-8') as tty_in, open('/dev/tty','w',encoding='utf-8') as tty:
            need(os.isatty(tty.fileno()) and os.isatty(tty_in.fileno()),'CONTROLLING_TTY_REQUIRED')
            prefix='FIXTURE-' if self.fixture else ''
            challenge=prefix+role.upper()+' '+self.pin+' '+sha(enc(detail))
            tty.write('Lucky personally verifies independent '+role+' authority and unrevoked scope.\n'
                      'This entry only captures/seals; it never switches services or calls Providers.\n'
                      +self.request_raw.decode()+'\n'+enc(detail).decode()+'\nTYPE EXACTLY: '+challenge+'\n> ')
            tty.flush();need(tty_in.readline().rstrip('\r\n')==challenge,'HUMAN_NOT_CONFIRMED')
            now=int(time.time());need(now<expires,'DECISION_EXPIRED_WHILE_WAITING')
            original=enc({'role':role,'confirmation':challenge,'review':detail,'request':self.p}).decode()
            return {'kind':FIXTURE if self.fixture else 'ACTUAL_NATIVE_V2_HUMAN_DECISION',
                    'role':role,'principal':'Lucky','request_sha256':self.pin,'issued_at':now,
                    'expires_at':expires,'original':original,'original_sha256':sha(original.encode()),
                    'tty':os.ttyname(tty.fileno()),'pid':os.getpid(),'uid':self.uid,
                    'host':os.uname().nodename,'source_reference':role+'-tty-pid'+str(os.getpid())+'-epoch'+str(now)}

    def reports(self):
        hashes={}
        for name,path in self.p['report_paths'].items():
            raw=self.read(Path(path));r=parse(raw)
            need(r['kind']==(FIXTURE if self.fixture else 'SOURCE_BOUND_NATIVE_V2_REPORT') and r['fixture'] is self.fixture
                 and r['check']==name and r['source_commit']==self.p['candidate']['source_commit']
                 and r['source_tree']==self.p['candidate']['source_tree'],'REAL_SOURCE_BOUND_REPORT:'+name)
            need(type(r['assertions']) is int and r['assertions']>0 and r['failures']==r['errors']==0
                 and r['unresolved_release_critical_skips']==0 and ID.fullmatch(r['run_id']), 'ACTUAL_REPORT_FAILURE:'+name)
            need(0<=int(time.time())-r['observed_at']<=86400,'REPORT_FRESHNESS:'+name)
            source=Path(r['raw_path'])
            need(source.is_relative_to(Path(path).parent),'RAW_REPORT_PATH')
            original=self.read(source,8*1024*1024)
            need(sha(original)==r['raw_sha256'],'RAW_REPORT_HASH:'+name)
            if r['raw_format']=='junit':
                tree=ET.fromstring(original);suites=[tree] if tree.tag=='testsuite' else tree.findall('testsuite')
                need(suites and sum(int(x.get('tests','0')) for x in suites)>0
                     and all(int(x.get('failures','0'))==int(x.get('errors','0'))==0 for x in suites), 'RAW_JUNIT_FAILURE:'+name)
                skipped=sum(int(x.get('skipped','0')) for x in suites)
                need(skipped==r['raw_skipped'],'RAW_SKIP_COUNT:'+name)
                passed=sum(int(x.get('tests','0'))-int(x.get('failures','0'))-int(x.get('errors','0'))-int(x.get('skipped','0')) for x in suites)
                need(passed>0 and passed==r['assertions'],'RAW_PASSED_COUNT:'+name)
            else:
                data=parse(original)
                need(data.get('fixture') is self.fixture and data.get('failures')==data.get('errors')==0
                     and data.get('source_commit')==r['source_commit']
                     and data.get('source_tree')==r['source_tree']
                     and type(data.get('assertions')) is int and data['assertions']==r['assertions']>0
                     and data.get('skipped',0)==r['raw_skipped'],'RAW_NATIVE_MEASUREMENT:'+name)
            need(r['raw_skipped']==0 or bool(r['resolved_skip_receipts']),'RAW_SKIPS_REQUIRE_CLOSURE:'+name)
            for receipt in r['resolved_skip_receipts']:
                closure=self.read(Path(receipt['path']))
                resolution=parse(closure)
                need(sha(closure)==receipt['sha256'] and resolution['status']=='RESOLVED'
                     and resolution['fixture'] is self.fixture
                     and resolution['source_commit']==r['source_commit']
                     and resolution['check']==name,'SKIP_CLOSURE:'+name)
            hashes[name]=sha(raw)
        return hashes

    def owner(self):
        need(not os.path.lexists(self.audit),'OLD_OR_ALREADY_ISSUED_ARCHIVE')
        parent=self.audit.parent;self.ancestors(parent)
        decision=self.confirm('owner',{'authority':'PROJECT_OWNER_POLICY_AUTHORITY','scope':'exact Native V2 qualification and bounded operations in request'},int(time.time())+86400)
        # Actual interval is anchored at the actual acknowledgement, never launch time.
        decision['expires_at']=decision['issued_at']+86400
        self.audit.mkdir(mode=0o700);self.put(self.audit/'request.json',self.request_raw)
        self.put(self.audit/'owner.json',decision)
        return {'status':FIXTURE if self.fixture else 'OWNER_ARCHIVED_NOT_RELEASE_AUTHORITY'}

    def root(self):
        owner,owner_raw=self.valid('owner')
        need(not os.path.lexists(self.audit/'root.json'),'ROOT_ALREADY_CAPTURED')
        r=self.confirm('root',{'authority':'ROOT_TECHNICAL_AUTHORITY','owner_sha256':sha(owner_raw),'scope':'exact candidate installation, qualification and two native-v2 records only; no service switch'},owner['expires_at'])
        self.valid('owner')
        r.update(expires_at=min(r['issued_at']+3600,owner['expires_at']),maintenance_expires_at=min(r['issued_at']+14400,owner['expires_at']),owner_sha256=sha(owner_raw))
        self.put(self.audit/'root.json',r)
        return {'status':FIXTURE if self.fixture else 'ROOT_ARCHIVED_NOT_RELEASE_AUTHORITY'}

    def seal(self):
        lock=self.base/'release.lock' if self.fixture else Path('/run/lock/enterprise-agent-workbench-release.lock')
        fd=os.open(lock,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            return self._seal_locked()
        finally:os.close(fd)

    def _seal_locked(self):
        need(not any(os.path.lexists(self.audit/name) for name in
             ('seal.claim.json','seal.failed.json','seal.complete.json','release.json')),'SEAL_ALREADY_ATTEMPTED_NO_REPLAY')
        owner,owner_raw=self.valid('owner');root,root_raw=self.valid('root')
        if not self.fixture:self.binding=self.artifact()
        self.ancestors(self.qual)
        need(not any(os.path.lexists(self.qual/(self.rid+s)) for s in ('.approval.v1.json','.evidence.v1.json')),'EXISTING_RECORD_NO_OVERWRITE')
        need(root['owner_sha256']==sha(owner_raw),'INDEPENDENT_OWNER_ROOT_BINDING')
        need(int(time.time())+self.p['remaining_work_seconds']+3600<root['maintenance_expires_at'],'RECOVERY_TIME_RESERVED')
        reports=self.reports()
        evidence={'schema_version':1,'scope':FIXTURE if self.fixture else 'ISOLATED_NATIVE_SCHEMA015_EXACT_PAIR',
                  'candidate':self.p['candidate'],'predecessor':self.p['predecessor'],
                  'binding_sha256':self.p['binding_sha256'],'schema_fingerprint':self.p['schema_fingerprint'],
                  'schema':'015','pending':0,'migration_action':'NONE','data_contract':'member_account_status_v1',
                  'checks':dict.fromkeys(CHECKS,FIXTURE if self.fixture else 'PASS')}
        release=self.confirm('release',{'authority':'PRODUCTION_RELEASE_AUTHORITY','scope':'exact candidate switch and exact d906 runtime recovery; no DB rewind',
                     'owner_sha256':sha(owner_raw),'root_sha256':sha(root_raw),'actual_report_sha256':reports,
                     'evidence_sha256':sha(enc(evidence))},root['maintenance_expires_at'])
        self.put(self.audit/'release.json',release)
        self.valid('owner');self.valid('root');need(self.reports()==reports,'REPORT_DRIFT_DURING_HUMAN_REVIEW')
        approval={'schema_version':1,'scope':FIXTURE if self.fixture else 'SCHEMA015_EXACT_PAIR_RELEASE_RECOVERY',
                  'issuer_uid':0,'audit_id':release['source_reference'],'issued_at':release['issued_at'],
                  'expires_at':root['maintenance_expires_at'],'binding_sha256':self.p['binding_sha256'],
                  'candidate':self.p['candidate'],'predecessor':self.p['predecessor'],'evidence_sha256':sha(enc(evidence))}
        self.valid('owner');self.valid('root');self.ancestors(self.qual)
        need(not any(os.path.lexists(self.qual/(self.rid+s)) for s in ('.approval.v1.json','.evidence.v1.json')),'EXISTING_RECORD_NO_OVERWRITE')
        self.put(self.audit/'seal.claim.json',{'request_sha256':self.pin,'report_sha256':reports})
        try:
            self.put(self.qual/(self.rid+'.evidence.v1.json'),evidence)
            self.put(self.qual/(self.rid+'.approval.v1.json'),approval)
            if not self.fixture:self.binding.qualification(self.base,self.app_root,self.manifest,self.p['schema_fingerprint'])
            self.put(self.audit/'seal.complete.json',{'status':FIXTURE if self.fixture else 'FROZEN_CONSUMER_VERIFIED',
                     'approval_sha256':sha(enc(approval)),'evidence_sha256':sha(enc(evidence))})
        except Exception:
            target=self.qual/(self.rid+'.approval.v1.json')
            if os.path.lexists(target) and self.read(target)==enc(approval):
                self.withdraw(target,'approval.failed-preserved.json')
            self.put(self.audit/'seal.failed.json',{'status':'STOP_NO_RETRY_RETAIN_RECORDS'})
            raise
        return {'status':FIXTURE if self.fixture else 'NATIVE_V2_RECORDS_SEALED_NOT_DEPLOYED'}

    def revoke(self):
        self.valid('owner')
        decision=self.confirm('revoke',{'scope':'invalidate only this request; preserve originals; no service action'},int(time.time())+3600)
        lock=self.base/'release.lock' if self.fixture else Path('/run/lock/enterprise-agent-workbench-release.lock')
        fd=os.open(lock,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.valid('owner')
            target=self.qual/(self.rid+'.approval.v1.json')
            if os.path.lexists(target):
                value=parse(self.read(target))
                need(value['candidate']==self.p['candidate'] and value['binding_sha256']==self.p['binding_sha256'], 'REVOKE_ONLY_OWN_EXACT_RECORD')
                self.withdraw(target,'approval.revoked-preserved.json')
            self.put(self.audit/'revoked.json',decision)
        finally:os.close(fd)
        return {'status':FIXTURE if self.fixture else 'EXACT_AUTHORITY_WITHDRAWN_OR_CAPTURE_REVOKED'}

    def withdraw(self,path,name):
        """V2 ignores sidecars: remove the consumable name, retain original bytes."""
        saved=self.audit/name
        need(not os.path.lexists(saved),'WITHDRAWAL_ARCHIVE_ALREADY_EXISTS')
        raw=self.read(path)
        os.rename(path,saved)
        for directory in (path.parent,saved.parent):
            fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(fd)
            finally:os.close(fd)
        need(self.read(saved)==raw and not os.path.lexists(path),'WITHDRAWAL_DURABILITY')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('action',choices=('review','owner','root','seal','revoke'))
    ap.add_argument('--request',required=True,type=Path);ap.add_argument('--request-sha256',required=True)
    ap.add_argument('--isolation-root',type=Path)
    args=ap.parse_args()
    try:
        app=Issuer(args.request,args.request_sha256,args.isolation_root)
        result={'status':'REVIEW_NOT_AUTHORITY','request_sha256':app.pin,'request':app.p} if args.action=='review' else getattr(app,args.action)()
        print(json.dumps(result,ensure_ascii=False));return 0
    except (Exception,) as exc:
        print(json.dumps({'status':'BLOCKED','check':str(exc) if isinstance(exc,Blocked) else 'INPUT_IO_OR_CONSUMER_REJECTED','retry':False,'error_type':type(exc).__name__}));return 2

if __name__=='__main__':raise SystemExit(main())
