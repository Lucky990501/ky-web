"""Isolated executor wiring tests. Fixtures are NOT technical authorization."""
import copy
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tarfile
from types import SimpleNamespace
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import test_delivery_context as c
from scripts import test_delivery_isolated as w
from scripts import test_delivery_native as n
from scripts import test_delivery_verified_entry as bootstrap

p = c.p


@pytest.fixture
def request_data():
    return dict(context=c.CONTEXT, target='ISOLATED_CANDIDATE',
        contract='STANDARD_TEST_DELIVERY_V1', action='test_delivery', environment='test',
        steps=list(c.STEPS), operation_id=str(uuid.uuid4()), calls=dict(text=0, image=0, wechat=0),
        candidate=dict(source='a'*40, tree='b'*40, git_parents=['c'*40]),
        artifact=dict(archive_sha256='a'*64, manifest_sha256='b'*64, selected_file_count=1),
        primary_switch_authority=False, production_authority=False, primary_backup='NOT_PERMITTED',
        recovery_kind='SAME_CANDIDATE_PRIVATE_DATA_ONLY', checks=list(c.CHECKS),
        observation=dict(hostname='synthetic-host'),
        observed_predecessor=dict(source='c'*40, tree='d'*40, git_parents=[]),
        limits=dict(aggregate_bytes=p.LIMIT, scratch_bytes=p.SCRATCH, input_bytes=p.INPUT_LIMIT,
            receipt_bytes=p.RECEIPT_LIMIT, host_reserve_bytes=p.HOST_RESERVE, min_free_fraction=.2))


def synthetic_authority(r):
    actor=dict(name='synthetic-operator', uid=12345, gid=12345)
    policy=dict(context=c.CONTEXT, target='ISOLATED_CANDIDATE',
        contract='STANDARD_TEST_DELIVERY_POLICY_V1', environment='test', hostname='synthetic-host',
        operator=actor, tooling=dict(source='e'*40, tree='f'*40), runtime_profile_sha256='a'*64,
        tooling_files_sha256='b'*64, actions=['prepare','activate','recover','status'],
        accepted_issuer_record_sha256='c'*64)
    grant=dict(context=c.CONTEXT, target='ISOLATED_CANDIDATE', action='test_delivery',
        operation_id=r['operation_id'], request_sha256=p.digest(r), policy_sha256=p.digest(policy),
        hostname=policy['hostname'], actor=actor, tooling=policy['tooling'],
        runtime_profile_sha256=policy['runtime_profile_sha256'], issuer_record_sha256='c'*64,
        actions=policy['actions'], production_authority=False, preview_authority=False,
        not_before=100, expires_at=1000, revoked=False, uses=1)
    decision=dict(context=c.CONTEXT, target='ISOLATED_CANDIDATE',
        kind='ACTUAL_OWNER_AUTHORIZED_06_ROOT_DECISION_RECORD', technical_action=c.ACTION,
        actual_root_uid=0, request_sha256=p.digest(r), host=policy['hostname'], operator=actor,
        owner_references=['SYNTHETIC_NOT_AUTHORITY'], production_authority=False,
        preview_authority=False, primary_switch_authority=False, provider_budget=0,
        revoked=False, uses=1, issued_at=100, expires_at=1000)
    decision['policy_scope_sha256']=c.policy_scope_sha256(policy)
    return policy,grant,decision


@pytest.mark.parametrize('field,value', [('target','PRIMARY'),('context','unknown'),
    ('primary_switch_authority',True),('production_authority',True),('primary_backup','PRIMARY'),
    ('calls',dict(text=1,image=0,wechat=0)),('steps',['activate']),
    ('recovery_kind','RESTORE_894'),('operation_id','not-an-operation'),('checks',[])])
def test_scope_expansion_rejects_before_any_callback(request_data,field,value):
    request_data[field]=value; calls=[]
    with pytest.raises((ValueError,KeyError)):
        c.dispatch_context(request_data,lambda *a:calls.append(a),lambda *a:calls.append(a),
                           lambda *a:calls.append(a),lambda:calls.append('lock'))
    assert calls==[]


def test_request_receipt_budget_is_enforced_before_claim(request_data):
    request_data['unexpected_large_field']='x'*c.REQUEST_LIMIT
    with pytest.raises(ValueError,match='ISOLATED_REQUEST_SIZE'):c.validate_request(request_data)


def test_controller_and_application_interpreters_are_separate():
    policy=dict(controller_python='/usr/bin/python3.10',controller_python_sha256='a'*64,
        python='/opt/protected/runtime/bin/python',python_sha256='b'*64)
    assert bootstrap.controller_binary(policy,'execute-isolated')==('/usr/bin/python3.10','a'*64)
    assert bootstrap.controller_binary(policy,'execute')==(policy['python'],'b'*64)
    policy['controller_python']=policy['python']
    with pytest.raises(ValueError,match='SYSTEM_CONTROLLER_PYTHON_REQUIRED'):
        bootstrap.controller_binary(policy,'execute-isolated')


@pytest.mark.parametrize('fault',[None,'root_uid','root_gid','wrong_uid','wrong_name'])
def test_isolated_runtime_version_probes_drop_identity(monkeypatch,fault):
    account=SimpleNamespace(pw_name='synthetic-operator',pw_uid=12345,pw_gid=12345)
    requested=dict(name=account.pw_name,uid=account.pw_uid,gid=account.pw_gid)
    if fault=='root_uid':account.pw_uid=0;requested['uid']=0
    if fault=='root_gid':account.pw_gid=0;requested['gid']=0
    if fault=='wrong_uid':requested['uid']=54321
    if fault=='wrong_name':requested['name']='other'
    monkeypatch.setitem(sys.modules,'pwd',SimpleNamespace(getpwnam=lambda _:account))
    request=dict(context=c.CONTEXT,operator=requested)
    if fault:
        with pytest.raises(ValueError,match='NONROOT_RUNTIME_PROBE_REQUIRED'):
            c.adoption.runtime_probe_identity(request)
    else:
        assert c.adoption.runtime_probe_identity(request)==dict(user=12345,group=12345,extra_groups=[])
    assert c.adoption.runtime_probe_identity({})=={} # historical branch unchanged


@pytest.mark.skipif(sys.platform!='linux',reason='existing Test OS controller Python')
def test_real_system_controller_imports_without_application_runtime():
    controller=Path('/usr/bin/python3.10')
    assert controller.is_file()
    result=subprocess.run([str(controller),'-I','-S','-B',str(Path(c.__file__)),'--help'],
        capture_output=True,text=True,env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent'})
    assert result.returncode==0 and 'execute-isolated' in result.stdout


@pytest.mark.parametrize('target,field,value', [('decision','kind','HISTORICAL'),
    ('decision','technical_action','install_894'),('decision','actual_root_uid',1000),
    ('decision','request_sha256','0'*64),('decision','policy_scope_sha256','0'*64),('decision','owner_references',[]),
    ('decision','provider_budget',1),('decision','primary_switch_authority',True),
    ('decision','revoked',True),('decision','expires_at',149),('decision','uses',2),
    ('grant','expires_at',149),('grant','revoked',True),('grant','uses',2),
    ('grant','operation_id',str(uuid.uuid4())),('grant','issuer_record_sha256','0'*64),
    ('grant','target','PRIMARY'),('policy','target','PRIMARY')])
def test_actual_authority_fields_fail_closed(request_data,target,field,value):
    policy,grant,decision=synthetic_authority(request_data)
    {'policy':policy,'grant':grant,'decision':decision}[target][field]=value
    with pytest.raises((ValueError,KeyError)):
        c.technical_scope(request_data,policy,grant,decision,150)


def test_synthetic_contract_positive_is_not_a_signed_record(request_data):
    c.technical_scope(request_data,*synthetic_authority(request_data),150)


def test_isolated_adoption_needs_no_original_primary_pair():
    assert c.adoption.artifact_roles(dict(context=c.CONTEXT,artifacts={'candidate':{}}))==('candidate',)
    assert c.adoption.artifact_roles(dict(artifacts={'candidate':{},'predecessor':{}}))==('candidate','predecessor')
    for request in (dict(context='PRIMARY',artifacts={'candidate':{}}),
                    dict(context=c.CONTEXT,artifacts={'candidate':{},'predecessor':{}}),
                    dict(artifacts={'candidate':{}})):
        with pytest.raises(ValueError):c.adoption.artifact_roles(request)


@pytest.mark.parametrize('field,value',[('status','FAIL'),('context','PRIMARY'),('stage','activate'),
    ('operation_id',str(uuid.uuid4())),('request_sha256','0'*64),('source','c'*40),('artifact',{})])
def test_step_receipt_cannot_be_relabelled(request_data,field,value):
    record=dict(status='PASS',context=c.CONTEXT,stage='preflight',operation_id=request_data['operation_id'],
        request_sha256=p.digest(request_data),source=request_data['candidate']['source'],artifact=request_data['artifact'])
    record[field]=value
    with pytest.raises(ValueError,match='ISOLATED_STEP_RECEIPT_BINDING'):
        c.bound_receipt(record,request_data,'preflight')


def test_replay_under_different_operation_store_changes_signed_policy_scope(request_data):
    policy,grant,decision=synthetic_authority(request_data)
    policy['operations_root']='/different-store'
    grant['policy_sha256']=p.digest(policy)
    with pytest.raises(ValueError,match='ACTUAL_TECHNICAL_DECISION'):
        c.technical_scope(request_data,policy,grant,decision,150)


def test_bridge_real_pipe_and_eof_cleanup_receipt(request_data,tmp_path,monkeypatch):
    # Actual child process and pipes. systemd/cgroup status alone is a fixture;
    # this does NOT establish adopted Unit qualification.
    request_file=tmp_path/'synthetic-request.json';request_file.write_text(json.dumps(request_data))
    helper=tmp_path/'synthetic-child.py'
    helper.write_text('''import json,sys
r=json.load(open(sys.argv[1])); last=None
for line in sys.stdin:
 x=json.loads(line); last=x
 print(json.dumps(dict(status="PASS",context="ISOLATED_CANDIDATE_CONTEXT_V1",stage=x["stage"],operation_id=r["operation_id"],request_sha256=x["request_sha256"],source=r["candidate"]["source"],artifact=r["artifact"])),flush=True)
print(json.dumps(dict(status="CLOSED",context="ISOLATED_CANDIDATE_CONTEXT_V1",operation_id=r["operation_id"],request_sha256=last["request_sha256"],private_services_stopped=True)),flush=True)
''')
    run=subprocess.run
    def systemctl(argv,**kwargs):
        if argv[:2]==['/usr/bin/systemctl','stop']:
            return subprocess.CompletedProcess(argv,0)
        if argv[:2]==['/usr/bin/systemctl','show']:
            return subprocess.CompletedProcess(argv,1,stdout='ActiveState=inactive\nMainPID=0\nLoadState=not-found\n')
        return run(argv,**kwargs)
    monkeypatch.setattr(subprocess,'run',systemctl)
    # Windows lacks statvfs; Linux may have unrelated host disk pressure. This
    # single transport test uses a resource fixture; real admission is separate.
    monkeypatch.setattr(os,'statvfs',lambda _:SimpleNamespace(f_bavail=900,f_blocks=1000,f_frsize=1024**3),raising=False)
    validations=[]
    bridge=c.SandboxBridge([sys.executable,'-B',str(helper),str(request_file)],
                           'test-delivery-'+request_data['operation_id']+'.service',lambda:validations.append(True))
    receipts={}
    for name in c.STEPS:
        receipts[name]=bridge.step(name,request_data,receipts)
    bridge.close(expect_success=True)
    assert bridge.proc.returncode==0 and validations and bridge.closed
    with pytest.raises(ValueError,match='ISOLATED_BRIDGE_CLOSED'):
        bridge.step('preflight',request_data,{})


@pytest.mark.parametrize('fault',['eof','oversize','malformed','foreign_source'])
def test_real_pipe_denials_never_become_success(request_data,tmp_path,monkeypatch,fault):
    rfile=tmp_path/'fixture.json';rfile.write_text(json.dumps(request_data))
    helper=tmp_path/'fixture-child.py'
    helper.write_text('''import json,sys
r=json.load(open(sys.argv[1]));x=json.loads(sys.stdin.readline());mode=sys.argv[2]
if mode=="eof":sys.exit(0)
if mode=="oversize":print("x"*262145,flush=True)
elif mode=="malformed":print("{",flush=True)
else:print(json.dumps(dict(status="PASS",context="ISOLATED_CANDIDATE_CONTEXT_V1",stage=x["stage"],operation_id=r["operation_id"],request_sha256=x["request_sha256"],source="wrong",artifact=r["artifact"])),flush=True)
''')
    run=subprocess.run
    def unit(argv,**kw):
        if argv[:2]==['/usr/bin/systemctl','stop']:return subprocess.CompletedProcess(argv,0)
        if argv[:2]==['/usr/bin/systemctl','show']:
            return subprocess.CompletedProcess(argv,0,stdout='ActiveState=inactive\nMainPID=0\nLoadState=not-found\n')
        return run(argv,**kw)
    monkeypatch.setattr(subprocess,'run',unit)
    monkeypatch.setattr(os,'statvfs',lambda _:SimpleNamespace(f_bavail=900,f_blocks=1000,f_frsize=1024**3),raising=False)
    bridge=c.SandboxBridge([sys.executable,'-B',str(helper),str(rfile),fault],
        'test-delivery-'+request_data['operation_id']+'.service',lambda:None)
    try:
        with pytest.raises(ValueError):bridge.step('preflight',request_data,{})
    finally:bridge.close()
    assert bridge.proc.poll() is not None and bridge.closed


@pytest.mark.parametrize('fault',['missing_cleanup','exit_nonzero'])
def test_exit_zero_and_pass_receipts_do_not_replace_cleanup_proof(request_data,tmp_path,monkeypatch,fault):
    rfile=tmp_path/'fixture.json';rfile.write_text(json.dumps(request_data))
    helper=tmp_path/'fixture-child.py'
    helper.write_text('''import json,sys
r=json.load(open(sys.argv[1]));last=None
for line in sys.stdin:
 x=json.loads(line);last=x
 print(json.dumps(dict(status="PASS",context="ISOLATED_CANDIDATE_CONTEXT_V1",stage=x["stage"],operation_id=r["operation_id"],request_sha256=x["request_sha256"],source=r["candidate"]["source"],artifact=r["artifact"])),flush=True)
if sys.argv[2]=="exit_nonzero":
 print(json.dumps(dict(status="CLOSED",context="ISOLATED_CANDIDATE_CONTEXT_V1",operation_id=r["operation_id"],request_sha256=last["request_sha256"],private_services_stopped=True)),flush=True)
 sys.exit(2)
''')
    run=subprocess.run
    def unit(argv,**kw):
        if argv[:2]==['/usr/bin/systemctl','stop']:return subprocess.CompletedProcess(argv,0)
        if argv[:2]==['/usr/bin/systemctl','show']:
            return subprocess.CompletedProcess(argv,0,stdout='ActiveState=inactive\nMainPID=0\nLoadState=not-found\n')
        return run(argv,**kw)
    monkeypatch.setattr(subprocess,'run',unit)
    monkeypatch.setattr(os,'statvfs',lambda _:SimpleNamespace(f_bavail=900,f_blocks=1000,f_frsize=1024**3),raising=False)
    bridge=c.SandboxBridge([sys.executable,'-B',str(helper),str(rfile),fault],
        'test-delivery-'+request_data['operation_id']+'.service',lambda:None)
    prior={}
    for name in c.STEPS:prior[name]=bridge.step(name,request_data,prior)
    with pytest.raises(ValueError,match='CLEANUP_RECEIPT_MISSING|ISOLATED_UNIT_EXIT_FAILED'):
        bridge.close(expect_success=True)


def test_entry_replay_never_appends_failure_to_previous_operation(request_data,tmp_path,monkeypatch):
    """Real exclusive files/journal, explicit authority and Unit test doubles."""
    policy,grant,decision=synthetic_authority(request_data)
    operations=tmp_path/'operations';operations.mkdir()
    policy.update(operations_root=str(operations),revocation_marker=str(tmp_path/'REVOKED'))
    decision['policy_scope_sha256']=c.policy_scope_sha256(policy)
    issued=int(time.time())-1;expires=issued+2000
    decision.update(issued_at=issued,expires_at=expires)
    record=tmp_path/'SYNTHETIC_NOT_AUTHORITY.json';record.write_text(json.dumps(decision))
    policy['accepted_issuer_record_sha256']=p.sha(record)
    grant.update(issuer_record_path=str(record),issuer_record_sha256=p.sha(record),
        policy_sha256=p.digest(policy),not_before=issued,expires_at=expires)
    paths=[]
    for name,value in [('request',request_data),('policy',policy),('grant',grant)]:
        path=tmp_path/(name+'.json');path.write_text(json.dumps(value));paths.append(path)
    @contextmanager
    def locked(): yield lambda:None
    monkeypatch.setattr(c,'controller_preflight',lambda:None)
    monkeypatch.setattr(c,'release_lock',locked)
    monkeypatch.setattr(p,'trusted',lambda path,**kw:Path(path))
    monkeypatch.setattr(c,'protected_preflight',lambda *a:{'synthetic':True})
    monkeypatch.setattr(p,'observe',lambda *a:{'synthetic':True})
    monkeypatch.setattr(p,'sandbox_command',lambda *a,**kw:['NOT_EXECUTED'])
    calls=[]
    class UnitDouble:
        failure_receipt=None
        def __init__(self,*a):pass
        def step(self,step,r,prior):
            calls.append(step)
            return dict(status='PASS',context=c.CONTEXT,stage=step,
                operation_id=r['operation_id'],request_sha256=p.digest(r),
                source=r['candidate']['source'],artifact=r['artifact'])
        def close(self,**kw):pass
    monkeypatch.setattr(c,'SandboxBridge',UnitDouble)
    result=c.execute(*paths,tmp_path/'unused-archive',tmp_path/'unused-manifest')
    assert result['test_native']=='PENDING_EXTERNAL_ACCEPTANCE'
    assert result['artifact_references']==dict(archive=str(tmp_path/'unused-archive'),manifest=str(tmp_path/'unused-manifest'))
    op=operations/request_data['operation_id']
    before={f.name:f.read_bytes() for f in op.iterdir()}
    assert len(calls)==7 and 'failure.json' not in before
    with pytest.raises(FileExistsError):
        c.execute(*paths,tmp_path/'unused-archive',tmp_path/'unused-manifest')
    assert len(calls)==7
    assert {f.name:f.read_bytes() for f in op.iterdir()}==before


@pytest.mark.parametrize('fault',[None,'missing_loader','wrong_inventory','different_operator',
    'unadopted_material','primary_context','unqualified_namespace','wrong_driver',
    'different_profile','different_root','different_python'])
def test_runtime_inventory_does_not_replace_actual_closure_and_profile(fault):
    profile=dict(status='PASS',sandbox_profile='PRIVATE_STAGE2_TMPFS_V2',driver_sha256='a'*64,
        checks={k:'PASS' for k in ('namespace','no_host_socket_access','write_containment',
        'disk_exhaustion','cgroup_termination','zero_egress','proc_secret_isolation')})
    inventory={'synthetic':True}
    policy=dict(operator={'uid':12345},tooling={'source':'b'*40},driver_sha256='a'*64,
        runtime_profile_sha256=p.digest(profile),runtime_root='/opt/independent/runtime',python_sha256='c'*64)
    closure=dict(status='PASS',runtime_inventory_sha256=p.digest(inventory),operator=policy['operator'],
        checks={k:'PASS' for k in ('dynamic_loader','python_imports','pgvector_load','redis_start','relocation')},
        sandbox_profile_sha256=p.digest(profile),runtime_root=policy['runtime_root'],python_sha256='c'*64)
    material=dict(status='MATERIALS_VERIFIED',context=c.CONTEXT,tooling=policy['tooling'])
    if fault=='missing_loader': closure['checks'].pop('dynamic_loader')
    if fault=='wrong_inventory': closure['runtime_inventory_sha256']='0'*64
    if fault=='different_operator': closure['operator']={'uid':1000}
    if fault=='unadopted_material': material['status']='UNSIGNED'
    if fault=='primary_context': material['context']='PRIMARY'
    if fault=='unqualified_namespace': profile['checks']['namespace']='NOT_PROVEN'
    if fault=='wrong_driver': profile['driver_sha256']='0'*64
    if fault=='different_profile': closure['sandbox_profile_sha256']='0'*64
    if fault=='different_root': closure['runtime_root']='/opt/active/runtime'
    if fault=='different_python': closure['python_sha256']='0'*64
    if fault:
        with pytest.raises(ValueError): c.runtime_receipts(policy,inventory,closure,material,profile)
    else:
        c.runtime_receipts(policy,inventory,closure,material,profile)


@pytest.mark.parametrize('fault',[None,'host_ram','slice_ram','unbounded_slice','expanded_slice','inactive'])
def test_resources_preserve_existing_slice_and_safety_reserve(fault):
    state=dict(ActiveState='active',MemoryCurrent=str(400*1024**2),
        MemoryHigh=str(3*1024**3),MemoryMax=str(4*1024**3))
    available=4*1024**3
    if fault=='host_ram': available=100*1024**2
    if fault=='slice_ram': state['MemoryCurrent']=str(2*1024**3)
    if fault=='unbounded_slice': state['MemoryMax']='infinity'
    if fault=='expanded_slice': state['MemoryHigh']=str(5*1024**3)
    if fault=='inactive': state['ActiveState']='inactive'
    if fault:
        with pytest.raises(ValueError):c.resource_admission(state,available)
    else:c.resource_admission(state,available)


class JobDouble:
    """Only orchestration double, no PG/Provider/Native PASS."""
    def __init__(self,request,root):
        self.request=request; self.parent=root; self.s=SimpleNamespace(root=None,database='synthetic_only')
        self.project=None; self.evidence=[]; self.process_identities=[]; self.events=[]
        self.peak=10; self.free_min=.9; self.runtime_versions={'fixture':True}
        self.health_value={'knowledge':'degraded'}; self.stage='PRECHECK'
        self.s.stop=lambda:self.events.append('cleanup')
    def space(self): self.events.append('space')
    def prepare_root(self):
        self.events.append('prepare'); self.s.root=self.parent/'marked-fixture'; self.s.root.mkdir()
    def start_databases(self): self.events.append('pg_redis_start')
    def command(self,args): self.events.append(args)
    def role_args(self,role): return ['fixture',role]
    def start_role(self,role): self.events.append('start:'+role)
    def stop_role(self,role,kill=False): self.events.append(('kill:' if kill else 'stop:')+role)
    def health(self): self.events.append('health')
    def database_fingerprint(self,database): return {'schema_migrations':{'rows':1,'sha256':'a'*64}}
    def phase(self,name): self.events.append('phase:'+name)
    def backup_restore(self,project,*,reader_identity):
        self.events.append('same_source_backup_restore')
        return dict(reader_kind='SAME_CANDIDATE',reader_source=reader_identity['source'],restored_database_distinct=True)


@pytest.fixture
def callbacks(request_data,tmp_path,monkeypatch):
    archive=tmp_path/'fixture.tar.gz'; archive.write_bytes(b'archive')
    manifest=tmp_path/'manifest.json'; manifest.write_bytes(b'manifest')
    request_data['artifact'].update(archive_sha256=n.sha(archive),manifest_sha256=n.sha(manifest))
    job=JobDouble(request_data,tmp_path)
    def extract(a,m,i,b,d):
        assert i==request_data['candidate'] and b==request_data['artifact']
        project=d/'enterprise_agent_poc'; project.mkdir(parents=True); return project
    monkeypatch.setattr(n,'safe_extract',extract)
    monkeypatch.setattr(w,'installed_readback',lambda *a:dict(status='PASS',fixture=True))
    return w.IsolatedCallbacks(request_data,archive,manifest,tmp_path,job=job)


def test_complete_fixed_dispatch_journal_and_callback_wiring(callbacks,tmp_path,monkeypatch):
    r=callbacks.request; lock_held=True; validations=[]
    monkeypatch.setattr(p,'trusted',lambda path,**kw:Path(path)) # only a component journal fixture
    journal=p.delivery_journal(tmp_path,r)
    def verify():
        assert lock_held
    def invoke(step,request,prior):
        value=callbacks.step(dict(stage=step,request_sha256=p.digest(request),prior_sha256=p.digest(prior)))
        c.bound_receipt(value,request,step); return value
    result=c.dispatch_context(r,invoke,journal,lambda r:validations.append(r['operation_id']),verify)
    assert list(result['receipts'])==c.STEPS
    assert result['receipts']['backup']['measurement']['backup_kind']=='NOT_APPLICABLE_NEW_EMPTY_INSTANCE'
    assert result['receipts']['activate']['measurement']['activation_kind']=='ISOLATED_START_ONLY'
    assert result['receipts']['qualify']['measurement']['recovery']['reader_source']==r['candidate']['source']
    assert len(list((tmp_path/r['operation_id']).glob('*.json')))==15
    events=callbacks.job.events
    assert events.index('prepare') < events.index('pg_redis_start') < events.index('health')
    assert 'same_source_backup_restore' in events and not any('894' in str(x) for x in events)
    with pytest.raises(FileExistsError):
        c.dispatch_context(r,invoke,p.delivery_journal(tmp_path,r),lambda r:None,verify)
    callbacks.close(); assert events[-1]=='cleanup'


@pytest.mark.parametrize('failed_step',c.STEPS)
def test_failure_receipt_precedes_own_instance_cleanup(callbacks,failed_step,monkeypatch):
    def fail(): raise RuntimeError('INJECTED_'+failed_step.upper())
    monkeypatch.setattr(callbacks,failed_step,fail)
    # Generate each message only after the preceding response was consumed.
    class Incoming:
        def __iter__(self):
            for name in c.STEPS:
                yield json.dumps(dict(stage=name,request_sha256=p.digest(callbacks.request),
                                      prior_sha256=p.digest(callbacks.receipts)))+'\n'
    out=io.StringIO()
    with pytest.raises(RuntimeError,match='INJECTED_'):
        w.serve(callbacks,Incoming(),out)
    receipt=json.loads(out.getvalue().splitlines()[-1])
    assert receipt['status']=='FAILED' and receipt['failure_code']=='INJECTED_'+failed_step.upper()
    if callbacks.job.s.root:
        assert callbacks.job.events[-1]=='cleanup'
    assert callbacks.index==c.STEPS.index(failed_step)


@pytest.mark.parametrize('fault',['stage','request_sha256','prior_sha256','extra'])
def test_protocol_reentry_and_foreign_messages_reject(callbacks,fault):
    command=dict(stage='preflight',request_sha256=p.digest(callbacks.request),prior_sha256=p.digest({}))
    command[fault]='wrong'
    with pytest.raises(ValueError,match='FIXED_STAGE_ORDER_OR_BINDING'):
        callbacks.step(command)
    assert callbacks.index==0


def test_eof_stops_instance_and_never_says_success(callbacks):
    incoming=io.StringIO(json.dumps(dict(stage='preflight',request_sha256=p.digest(callbacks.request),
        prior_sha256=p.digest({})))+'\n')
    out=io.StringIO()
    with pytest.raises(ValueError,match='INCOMPLETE_STAGE_STREAM'):
        w.serve(callbacks,incoming,out)
    assert json.loads(out.getvalue().splitlines()[-1])['status']=='FAILED'
    assert 'CLOSED' not in out.getvalue()


def test_actual_cli_is_helpful_but_ordinary_execution_cannot_read_records(tmp_path):
    script=Path(c.__file__)
    result=subprocess.run([sys.executable,'-I','-B',str(script),'--help'],capture_output=True,text=True)
    assert result.returncode==0 and 'plan-isolated' in result.stdout and 'execute-isolated' in result.stdout
    result=subprocess.run([sys.executable,'-I','-B',str(script),'execute-isolated',
        *[part for key in ('request','policy','grant','archive','manifest')
          for part in ('--'+key,str(tmp_path/(key+'.DOES_NOT_EXIST')))]],capture_output=True,text=True)
    assert result.returncode!=0 and 'ADOPTED_ROOT_ENTRY_REQUIRED' in result.stderr
    assert list(tmp_path.iterdir())==[]


def test_sandbox_reuses_isolation_profile_without_predecessor_artifact(request_data):
    policy=dict(runtime_root='/opt/enterprise-agent-test-delivery/runtime',
        tooling_root='/opt/enterprise-agent-test-delivery/tooling',
        python='/opt/enterprise-agent-test-delivery/runtime/bin/python',
        qualification_driver='/opt/enterprise-agent-test-delivery/tooling/scripts/test_delivery_isolated.py',
        operator_uid=12345,operator_gid=12345,slice='enterprise-agent-test.slice',driver_protocol=c.CONTEXT)
    args=p.sandbox_command(request_data,policy,Path('/opt/operation'),Path('/opt/archive'),Path('/opt/manifest'),isolated_candidate=True)
    command=' '.join(args)
    assert 'PrivateNetwork=yes' in args and 'MemoryMax=1536M' in args
    assert 'enterprise-agent-test.slice' in command and 'BindReadOnlyPaths=' in command
    assert 'predecessor' not in command and '/current' not in command and 'release_switch' not in command
    with pytest.raises(ValueError,match='NO_PRIMARY_RESTORE_INPUT'):
        p.sandbox_command(request_data,policy,Path('/opt/operation'),Path('/a'),Path('/m'),Path('/old'),Path('/oldm'),isolated_candidate=True)


@pytest.mark.skipif(sys.platform!='linux',reason='real POSIX flock and fork')
def test_real_global_lock_exclusion_and_journal_same_critical_section(tmp_path,request_data,monkeypatch):
    if os.getuid()==0:
        pytest.fail('component tests must run as non-Root')
    lockdir=tmp_path/'locks'; lockdir.mkdir(); lockdir.chmod(0o1777)
    lock=lockdir/'shared.lock'; lock.write_bytes(b''); lock.chmod(0o644)
    original=Path.lstat
    # Map UID metadata to Root only for this private lock fixture. Real kernel
    # inode, open, flock, fork and contention below are NOT mocked.
    def metadata(path):
        value=original(path)
        if path in (lock,lockdir):
            fields=list(value); fields[4]=fields[5]=0; return os.stat_result(fields)
        return value
    monkeypatch.setattr(c,'GLOBAL_LOCK',lock); monkeypatch.setattr(Path,'lstat',metadata)
    fstat=os.fstat
    def opened_metadata(fd):
        value=fstat(fd)
        if value.st_ino==original(lock).st_ino:
            fields=list(value);fields[4]=fields[5]=0;return os.stat_result(fields)
        return value
    monkeypatch.setattr(os,'fstat',opened_metadata)
    monkeypatch.setattr(p,'trusted',lambda path,**kw:Path(path))
    with c.release_lock() as verify:
        child=os.fork()
        if child==0:
            try:
                with c.release_lock(): os._exit(5)
            except p.Block as exc:
                os._exit(0 if str(exc)=='TEST_GLOBAL_RELEASE_LOCK_BUSY' else 6)
        _,status=os.waitpid(child,0); assert os.waitstatus_to_exitcode(status)==0
        def step(name,r,prior):
            verify()
            return dict(status='PASS',operation_id=r['operation_id'],request_sha256=p.digest(r))
        c.dispatch_context(request_data,step,p.delivery_journal(tmp_path,request_data),lambda r:None,verify)
        old=lockdir/'old'; lock.rename(old); lock.write_bytes(b'')
        with pytest.raises(ValueError,match='GLOBAL_LOCK_INODE_CHANGED'): verify()


@pytest.mark.parametrize('folder,source', [('b1-linux','801fd27a92f32ecc87e9aec1a8c253a8942d1e92'),
    ('894-linux-reconstruction','894bdd2a031725a0d0df3b95d6dfc26d08f0e4ab')])
def test_two_actual_sources_use_identical_executor_and_existing_artifacts(folder,source,tmp_path):
    repo=Path(os.environ.get('CONTEXT_REAL_GIT_REPO',Path(__file__).resolve().parents[2]))
    evidence=Path(os.environ.get('CONTEXT_REAL_ARTIFACTS',repo.parent/'.standard-test-delivery-evidence-v1'))
    artifact=evidence/folder
    if not artifact.is_dir():
        pytest.skip('actual preserved artifacts supplied only for dedicated source-independence run')
    files=[Path(module.__file__) for module in (c,w,n,p,c.adoption)]
    before={file.name:p.sha(file) for file in files}
    old=p.identity(repo,'894bdd2a031725a0d0df3b95d6dfc26d08f0e4ab')
    observation=dict(environment='test',hostname='OFFLINE_NOT_AUTHORITY',primary_source=old['source'],
        primary_tree=old['tree'],primary_path='/OBSERVED_NOT_CHANGED',observed_at=1)
    ident=p.identity(repo,source)
    request=c.isolated_plan(repo,source,ident['tree'],artifact/'candidate.tar.gz',
        artifact/'candidate.manifest.json',observation,'a'*64)
    c.validate_request(request)
    project=n.safe_extract(artifact/'candidate.tar.gz',artifact/'candidate.manifest.json',
        ident,request['artifact'],tmp_path/'new-candidate')
    assert w.installed_readback(artifact/'candidate.tar.gz',artifact/'candidate.manifest.json',
                               project,request['artifact'])['status']=='PASS'
    assert project.is_dir() and not request['predecessor_original_artifact_available']
    assert request['observed_predecessor']==old and request['target']=='ISOLATED_CANDIDATE'
    assert not request['primary_switch_authority'] and before=={file.name:p.sha(file) for file in files}
    p.exclusive(tmp_path/'source-reuse.json',dict(candidate=ident,artifact=request['artifact'],
        executor_files=before,installed_readback='PASS',target='ISOLATED_CANDIDATE',
        primary_rollback_proven=False,protected_execution=False,
        artifact_notice='EXISTING_B1' if folder=='b1-linux' else 'RECONSTRUCTED_894_FOR_SOURCE_REUSE_ONLY'))


@pytest.mark.parametrize('fault',[None,'file_bytes','extra_file'])
def test_actual_private_installed_readback_rejects_tampering(tmp_path,fault):
    archive=tmp_path/'fixture.tar.gz'
    with tarfile.open(archive,'w:gz',format=tarfile.PAX_FORMAT,pax_headers={'comment':'a'*40}) as tf:
        member=tarfile.TarInfo('enterprise_agent_poc/app/main.py');member.size=4
        tf.addfile(member,io.BytesIO(b'code'))
    manifest=tmp_path/'fixture.manifest.json'
    manifest.write_text(json.dumps(dict(release_id='fixture',source_commit='a'*40,
        archive_sha256=n.sha(archive),selected_files=['enterprise_agent_poc/app/main.py'],
        selected_file_count=1,build_platform='Linux-x86_64')))
    bound=dict(archive_sha256=n.sha(archive),manifest_sha256=n.sha(manifest))
    project=n.safe_extract(archive,manifest,{'source':'a'*40},bound,tmp_path/'install')
    if fault=='file_bytes':
        file=project/'app/main.py';file.chmod(0o600);file.write_bytes(b'evil')
    if fault=='extra_file':(project/'unlisted').write_bytes(b'extra')
    if fault:
        with pytest.raises(ValueError,match='INSTALLED_FILE_'):w.installed_readback(archive,manifest,project,bound)
    else:assert w.installed_readback(archive,manifest,project,bound)['status']=='PASS'


@pytest.mark.skipif(not os.environ.get('CONTEXT_LIFECYCLE_PG'),reason='optional real private PG/Redis component fixture')
def test_real_private_pg_redis_split_lifecycle_without_candidate(request_data):
    """Real backend control test, NOT B1/Native deployment or Root adoption."""
    assert sys.platform=='linux' and os.getuid()!=0
    assert os.environ.get('CONTEXT_LIFECYCLE_ONLY')=='SYNTHETIC_COMPONENT_NOT_NATIVE'
    assert {name for _,name in __import__('socket').if_nameindex()}=={'lo'}
    parent=Path('/private/tmp')
    pg=Path(os.environ['CONTEXT_LIFECYCLE_PG']);redis=Path(os.environ['CONTEXT_LIFECYCLE_REDIS'])
    assert all(path.is_relative_to(Path('/opt/enterprise-agent-workbench-test/shared/runtime'))
               and path.resolve()==path for path in (pg,redis))
    job=n.Job(request_data,parent,dev_runtime={'pg':str(pg),'redis':str(redis)})
    job.input_size=0;root=None;redis_process=None
    try:
        job.prepare_root();root=job.s.root
        assert not job.s.pg_started and job.s.manifest is None
        job.start_databases()
        assert job.s.pg_started and job.s.database.startswith('stage25_')
        redis_process=next(proc for role,proc in job.s.children if role=='redis')
        import psycopg
        with psycopg.connect(host=str(root/'pg/socket'),port=54330,user='stage1_fixture',
                             dbname=job.s.database) as db:
            version=db.execute('SHOW server_version').fetchone()[0].split()[0]
            assert version=='16.6'
            db.execute('CREATE EXTENSION vector')
            vector=db.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
            assert vector=='0.8.6'
            db.execute('CREATE TABLE component_lifecycle_fixture (id integer PRIMARY KEY)')
            db.execute('INSERT INTO component_lifecycle_fixture VALUES (1),(2)')
            assert db.execute('SELECT count(*) FROM component_lifecycle_fixture').fetchone()==(2,)
        pong=subprocess.check_output([str(job.s.redis_cli),'-s',str(root/'redis.sock'),'PING'],text=True).strip()
        assert pong=='PONG'
        measurement=dict(level='PRIVATE_BACKEND_COMPONENT_NOT_NATIVE',postgresql=version,pgvector=vector,
            redis_ping=pong,synthetic_rows=2,private_database=job.s.database,
            socket_sha256=__import__('hashlib').sha256(str(root/'pg/socket').encode()).hexdigest(),
            mount_namespace=os.readlink('/proc/self/ns/mnt'),ipc_namespace=os.readlink('/proc/self/ns/ipc'),
            cgroup=Path('/proc/self/cgroup').read_text().strip(),provider_calls=0,candidate_started=False)
    finally:
        job.s.stop()
    assert root is not None and not root.exists() and not job.s.pg_started
    assert redis_process is not None and redis_process.poll() is not None
    measurement.update(private_root_removed=True,postgres_stopped=True,redis_stopped=True)
    p.exclusive(Path(os.environ['CONTEXT_LIFECYCLE_REPORT']),measurement)


@pytest.mark.skipif(os.environ.get('CONTEXT_REAL_LOCK')!='READONLY_CONTENTION_ONLY',
                    reason='explicit actual-host lock probe only')
def test_actual_original_lock_with_new_context_code():
    """No file creation/writes or approval consumption, briefly locks same inode."""
    assert sys.platform=='linux' and os.getuid()!=0
    path=c.GLOBAL_LOCK;before=path.stat()
    try:
        with c.release_lock() as verify:
            child=os.fork()
            if child==0:
                try:
                    with c.release_lock():os._exit(5)
                except p.Block as exc:
                    os._exit(0 if str(exc)=='TEST_GLOBAL_RELEASE_LOCK_BUSY' else 6)
            _,status=os.waitpid(child,0);assert os.waitstatus_to_exitcode(status)==0
            verify()
    except p.Block as exc:
        if str(exc)=='TEST_GLOBAL_RELEASE_LOCK_BUSY':pytest.skip('real release lock busy; no retry')
        raise
    after=path.stat()
    assert (before.st_dev,before.st_ino,before.st_mtime_ns,before.st_size)==(
        after.st_dev,after.st_ino,after.st_mtime_ns,after.st_size)
