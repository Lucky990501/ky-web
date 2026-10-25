"""Offline doubles only: no persistent Test installation or systemd mutation."""
import copy
import importlib.util
from pathlib import Path
import sys
import uuid
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import test_delivery_persistent as c
from scripts import test_delivery_verified_entry as b


def fixture(mode='activate'):
    policy = dict(contract='STANDARD_TEST_DELIVERY_POLICY_V1', context=c.CONTEXT,
        environment='test', hostname='instance-mif0a40l', units=c.UNITS, service_user='eaw-test',
        operations_root=str(c.BASE/'operations'), repository=str(c.BASE/'repository'),
        operator={'name':'test-operator','uid':1000,'gid':1000},
        tooling={'source':'d'*40,'tree':'e'*40}, runtime_profile_sha256='f'*64,
        tooling_files_sha256='a'*64, accepted_issuer_record_sha256='b'*64,
        actions=['prepare','activate','recover','status'])
    req = dict(contract='STANDARD_TEST_DELIVERY_V1', context=c.CONTEXT, environment='test',
        action='test_delivery', mode=mode, operation_id=str(uuid.uuid4()),
        observation={'hostname':policy['hostname']}, calls={'text':0,'image':0,'wechat':0},
        primary_switch_authority=True, production_authority=False,
        candidate={'source':'c'*40,'tree':'a'*40,'git_parents':[c.SOURCE801]},
        observed_predecessor={'source':c.SOURCE801}, artifact={})
    if mode == 'recover':
        req['candidate'].update(source=c.SOURCE801, tree=c.TREE801)
        req['artifact']=dict(archive_sha256=c.ARCHIVE801,manifest_sha256=c.MANIFEST801)
    decision = dict(context=c.CONTEXT, kind='ACTUAL_OWNER_AUTHORIZED_06_ROOT_DECISION_RECORD',
        technical_action='execute_new_test_persistent_v1', actual_root_uid=0,
        operator=policy['operator'], host=policy['hostname'], owner_references=['SYNTHETIC_ONLY'],
        primary_switch_authority=True, production_authority=False, preview_authority=False,
        provider_budget=0, revoked=False, uses=1, issued_at=100, expires_at=200)
    grant = dict(action='test_delivery', mode=mode, context=c.CONTEXT, hostname=policy['hostname'],
        operation_id=req['operation_id'], actor=policy['operator'], tooling=policy['tooling'],
        runtime_profile_sha256=policy['runtime_profile_sha256'],issuer_record_sha256='b'*64,
        actions=policy['actions'],production_authority=False,preview_authority=False,
        not_before=100,expires_at=200,revoked=False,uses=1)
    bind(req,policy,grant,decision)
    return req,policy,grant,decision


def bind(r,p,g,d):
    g.update(request_sha256=c.p.digest(r),policy_sha256=c.p.digest(p))
    d.update(request_sha256=c.p.digest(r),policy_scope_sha256=c.p.digest({k:v for k,v in p.items()
                    if k!='accepted_issuer_record_sha256'}))


@pytest.mark.parametrize('mode',['activate','recover'])
def test_valid_contract(mode): c.validate(*fixture(mode),150)


@pytest.mark.parametrize('fault',['host','identity','source','tree','parent','hash','expired','revoked','mode','production','context'])
def test_bad_scope(fault):
    r,p,g,d=fixture('recover' if fault in ('source','tree','hash') else 'activate')
    if fault=='host': p['hostname']='production'
    if fault=='identity': g['actor']={'uid':0}
    if fault=='source': r['candidate']['source']='1'*40
    if fault=='tree': r['candidate']['tree']='1'*40
    if fault=='parent': r['candidate']['git_parents']=[]
    if fault=='hash': r['artifact']['archive_sha256']='1'*64
    if fault=='expired': g['expires_at']=150
    if fault=='revoked': d['revoked']=True
    if fault=='mode': g['mode']='other'
    if fault=='production': d['production_authority']=True
    if fault=='context': d['context']='ISOLATED_CANDIDATE_CONTEXT_V1'
    bind(r,p,g,d)
    with pytest.raises(c.p.Block): c.validate(r,p,g,d,150)


@pytest.mark.parametrize('role',['api','mcp','worker'])
def test_fixed_dropin(role):
    text=c.dropin(role,c.SOURCE801)
    assert '--role '+role in text and '--project '+str(c.ROOT/'releases'/c.SOURCE801/'enterprise_agent_poc') in text
    assert 'BindReadOnlyPaths=' in text and ' -I -B ' in text


@pytest.mark.parametrize('role,source',[('postgresql',c.SOURCE801),('api','x;touch /tmp/pwn'),('api','../escape')])
def test_injection_rejected(role,source):
    with pytest.raises(c.p.Block): c.dropin(role,source)


def test_systemctl_argv_never_shell(monkeypatch):
    calls=[]
    def run(args,**kw):
        assert not kw.get('shell'); calls.append(args)
        return type('Result',(),dict(returncode=0,stdout=b''))
    monkeypatch.setattr(c.subprocess,'run',run)
    c.systemctl('start',c.UNITS['api'])
    assert calls==[['/usr/bin/systemctl','start',c.UNITS['api']]]
    with pytest.raises(c.p.Block):c.systemctl('start','nginx.service')


@pytest.mark.parametrize('failure',['authority','intent','effect','result',None])
def test_audit_before_effect_and_failure_retained(failure):
    r,p,g,d=fixture(); events=[]
    def auth(req):
        if failure=='authority':raise c.p.Block('REVOKED')
    def journal(stage,value):
        assert set(value)=={'operation_id','actor','authorization_sha256','source','target','executor','at','action','result'}
        events.append(stage)
        if failure and stage.startswith(failure+':'):raise OSError('audit failure')
    cb=c.Callbacks(r,p,auth,journal)
    def effect():
        events.append('EFFECT')
        if failure=='effect':raise RuntimeError('synthetic')
    if failure:
        with pytest.raises(Exception):cb.effect('startapi',effect)
    else:cb.effect('startapi',effect)
    if failure in ('authority','intent'):assert 'EFFECT' not in events
    if not failure:assert events==['intent:startapi','EFFECT','result:startapi']


def test_new_bootstrap_controller_is_separate():
    p={'context':c.CONTEXT,'controller_python':'/usr/bin/python3.12','controller_python_sha256':'a'*64}
    assert b.controller_binary(p,'execute-persistent')==('/usr/bin/python3.12','a'*64)
    p['context']='ISOLATED_CANDIDATE_CONTEXT_V1'
    with pytest.raises(ValueError):b.controller_binary(p,'execute-persistent')


def test_unit_drift_rejected(monkeypatch):
    r,p,g,d=fixture();p['service_files']={'/unit': 'a'*64}
    r['current_cwds']={x:'expected' for x in c.UNITS}
    cb=c.Callbacks(r,p,lambda req:None,lambda *a:None,
        command=lambda *a:dict(User='eaw-test',ActiveState='active',MainPID='123',FragmentPath='/unit',DropInPaths=''))
    monkeypatch.setattr(c.os,'readlink',lambda path:'wrong')
    with pytest.raises(c.p.Block,match='CURRENT_SOURCE_DRIFT'):cb.verify_live(r['current_cwds'])


@pytest.mark.parametrize('recover',[False,True])
def test_switch_real_dropin_files_fixed_commands(tmp_path,monkeypatch,recover):
    r,p,g,d=fixture('recover' if recover else 'activate'); calls=[]; events=[]
    for unit in c.UNITS.values():(tmp_path/(unit+'.d')).mkdir()
    monkeypatch.setattr(c.p,'trusted',lambda path,**kw:tmp_path/Path(path).name)
    # Directory fsync is Linux-specific; component run also supports Windows.
    if sys.platform!='linux':
        real_open=c.os.open
        monkeypatch.setattr(c.os,'O_DIRECTORY',0,raising=False)
        def opened(path,flags,*args):
            if Path(path).is_dir():return real_open(tmp_path/'directory-fsync-probe',c.os.O_CREAT|c.os.O_RDWR,0o600)
            return real_open(path,flags,*args)
        monkeypatch.setattr(c.os,'open',opened)
    cb=c.Callbacks(r,p,lambda req:None,lambda stage,value:events.append(stage),
                   command=lambda *args:calls.append(args))
    cb.switch()
    assert calls==[('stop',c.UNITS[x]) for x in ('worker','api','mcp')]+[('daemon-reload',)]+[('start',c.UNITS[x]) for x in ('mcp','api','worker')]
    for role,unit in c.UNITS.items():
        assert (tmp_path/(unit+'.d')/'90-standard-test-release.conf').read_text()==c.dropin(role,r['candidate']['source'])
    assert all(events[i].startswith('intent:') and events[i+1].startswith('result:') for i in range(0,len(events),2))


@pytest.mark.parametrize('action',['stopapi','bindapi','reload','startapi'])
def test_switch_failure_never_continues_or_blindly_recovers(action):
    r,p,g,d=fixture();cb=c.Callbacks(r,p,None,None);seen=[]
    def effect(name,fn):
        seen.append(name)
        if name==action:raise OSError('injected')
        # Deliberately no effect; tests exact fixed sequence stop point only.
    cb.effect=effect
    with pytest.raises(OSError):cb.switch()
    assert seen[-1]==action and all('recover' not in x for x in seen)


def test_install_scope_rejects_production_before_read():
    with pytest.raises(c.p.Block,match='INSTALL_CONTEXT'):
        c.verify_installation(dict(context=c.CONTEXT,environment='production',hostname='instance-mif0a40l',units=c.UNITS))


def test_missing_installation_pin_fails_closed(monkeypatch,tmp_path):
    r,p,g,d=fixture();p['tooling_files']={}; p['tooling']['git_parents']=[]
    monkeypatch.setattr(c.p,'trusted',lambda path,**kw:tmp_path)
    monkeypatch.setattr(c.p,'identity',lambda *args:p['tooling'])
    with pytest.raises(c.p.Block,match='INSTALL_COMPLETE_PIN_SET'):c.verify_installation(p)


def interrupted(tmp_path, monkeypatch, tail='intent:stopapi', failure=False, mode='activate'):
    """Real durable journal/files; trust substitute applies ONLY to temp fixtures."""
    import json
    monkeypatch.setattr(c.p, 'trusted', lambda path, **kw: Path(path))
    r, policy, grant, decision = fixture('recover')
    identity801 = dict(source=c.SOURCE801, tree=c.TREE801, git_parents=['8'*40])
    target = dict(source='c'*40, tree='a'*40, git_parents=[c.SOURCE801])
    old_artifact = dict(r['artifact'])
    candidate_artifact = dict(archive_sha256='1'*64, manifest_sha256='2'*64)
    original, _, _, _ = fixture()
    original.update(candidate=target, observed_predecessor=identity801,
                    artifact=candidate_artifact, current_artifact=old_artifact,
                    current_cwds={r:str(c.ROOT/'candidate/enterprise_agent_poc') for r in c.UNITS})
    if mode == 'recover':
        original.update(mode=mode, candidate=identity801, observed_predecessor=target,
                        artifact=old_artifact, current_artifact=candidate_artifact,
                        failed_operation_id=str(uuid.uuid4()), recovery_evidence={'request.json':'4'*64})
    r.update(candidate=identity801, observed_predecessor=target, current_artifact=candidate_artifact,
             failed_operation_id=original['operation_id'],
             current_cwds={role:str(c.ROOT/'releases'/target['source']/'enterprise_agent_poc') for role in c.UNITS})
    policy['operations_root'] = str(tmp_path)
    append = c.p.delivery_journal(tmp_path,original)
    append('claim',{'request_sha256':c.p.digest(original)})
    directory = tmp_path/original['operation_id']
    c.p.exclusive(directory/'request.json',original)
    cb = c.Callbacks(original,policy,lambda req:None,append)
    if tail != 'claim':
        for action in c.EFFECTS:
            cb.audit('intent',action)
            if tail == 'intent:'+action: break
            cb.audit('result',action,'PASS')
            if tail == 'result:'+action: break
    if failure:
        c.p.exclusive(directory/'failure.json',dict(status='UNKNOWN_REQUIRES_AUTHORIZED_RECOVERY',
            reason='FixtureFailure',operation_id=original['operation_id'],request_sha256=c.p.digest(original)))
    r['recovery_evidence'] = {f.name:c.p.sha(f) for f in directory.iterdir()}
    bind(r,policy,grant,decision)
    return r,policy,directory,original


@pytest.mark.parametrize('failure',[True,False])
@pytest.mark.parametrize('tail',['intent:stage','result:stage','intent:stopworker','result:stopapi',
    'result:stopmcp','intent:bindapi','result:bindworker','intent:reload','result:startapi','intent:health'])
def test_p1_trusted_lifecycle_prefixes(tmp_path,monkeypatch,failure,tail):
    r,policy,directory,original = interrupted(tmp_path,monkeypatch,tail,failure)
    before = {f.name:f.read_bytes() for f in directory.iterdir()}
    result = c.recovery_evidence(r,policy)
    assert result['kind'] == ('TRUSTED_FAILURE_RECORD' if failure else 'TRUSTED_UNFINISHED_INTENT_WITHOUT_FAILURE')
    assert result['original_status']=='UNKNOWN' and result['last_stage']==tail
    assert before == {f.name:f.read_bytes() for f in directory.iterdir()}


@pytest.mark.parametrize('fault',['missing_intent','tampered','gap','request','actor','target','source',
    'archive','manifest','completion','health_success','extra_terminal','chain','original_scope'])
def test_p1_corrupt_or_terminal_evidence_rejected(tmp_path,monkeypatch,fault):
    import json
    tail = 'claim' if fault=='missing_intent' else 'result:health' if fault=='health_success' else 'intent:stopapi'
    r,policy,directory,original = interrupted(tmp_path,monkeypatch,tail)
    if fault=='tampered': (directory/'intent-stopapi.json').write_text('{}')
    elif fault=='gap': (directory/'result-stage.json').unlink()
    elif fault in ('completion','extra_terminal'):
        c.p.exclusive(directory/('completion.json' if fault=='completion' else 'success.json'),{'status':'PASS'})
    elif fault in ('archive','manifest'): r['current_artifact'][fault+'_sha256']='9'*64
    elif fault=='original_scope':
        value=dict(original,environment='production'); (directory/'request.json').write_text(json.dumps(value))
    elif fault in ('request','actor','target','source','chain'):
        path=directory/'intent-stopapi.json'; value=c.p.read(path)
        if fault=='request':value['request_sha256']='9'*64
        elif fault=='chain':value['prior_sha256']='9'*64
        elif fault=='actor':value['payload']['actor']['uid']=0
        else:value['payload'][fault]={'source':'9'*40}
        path.write_text(json.dumps(value))
    if fault!='tampered': r['recovery_evidence']={f.name:c.p.sha(f) for f in directory.iterdir()}
    with pytest.raises(c.p.Block):c.recovery_evidence(r,policy)


def test_p1_no_intent_with_trusted_failure_preserved(tmp_path,monkeypatch):
    r,policy,_,_=interrupted(tmp_path,monkeypatch,'claim',True)
    assert c.recovery_evidence(r,policy)['kind']=='TRUSTED_FAILURE_RECORD'


def test_p1_duplicate_recovery_requires_latest_operation(tmp_path,monkeypatch):
    r,policy,_,_=interrupted(tmp_path,monkeypatch)
    later=tmp_path/str(uuid.uuid4()); later.mkdir()
    c.p.exclusive(later/'request.json',dict(r,operation_id=later.name))
    with pytest.raises(c.p.Block,match='RECOVERY_ALREADY_REFERENCED'):c.recovery_evidence(r,policy)


def test_p1_interrupted_recovery_can_be_independently_referenced(tmp_path,monkeypatch):
    r,policy,directory,original=interrupted(tmp_path,monkeypatch,mode='recover')
    result=c.recovery_evidence(r,policy)
    assert result['original_mode']=='recover' and result['original_status']=='UNKNOWN'
    assert not (directory/'failure.json').exists()


@pytest.mark.parametrize('reason',['expired','missing','wrong_target','lock','audit'])
def test_p1_run_never_effects_without_authority_or_audit(tmp_path,monkeypatch,reason):
    r,policy,_,_=interrupted(tmp_path,monkeypatch)
    calls=[]
    def check(req):raise c.p.Block('SYNTHETIC_'+reason.upper())
    cb=c.Callbacks(r,policy,check,lambda *args:None,lambda *args:calls.append(args))
    with pytest.raises(c.p.Block):cb.run()
    assert not calls


def setup_recovery_run(tmp_path,monkeypatch,classification):
    """Exercise real run/evidence/journal/staging; systemd and external pins fake."""
    import io, tarfile
    tail = ('intent:stage' if classification=='ALREADY_HEALTHY_801_NO_SERVICE_EFFECT' else
            'intent:health' if classification=='TARGET_RUNNING_UNKNOWN_NOT_SUCCESS' else 'intent:stopapi')
    r,policy,directory,original=interrupted(tmp_path,monkeypatch,tail)
    root=tmp_path/'controller'; root.mkdir(); (root/'releases').mkdir()
    # Keep operations enumeration separate from synthetic installation inputs.
    operations=tmp_path/'operations'; operations.mkdir(); directory.rename(operations/directory.name)
    policy['operations_root']=str(operations)
    monkeypatch.setattr(c,'BASE',root)
    archive=root/'801.tar'
    with tarfile.open(archive,'w') as tf:
        item=tarfile.TarInfo('enterprise_agent_poc/app.txt'); item.size=3
        tf.addfile(item,io.BytesIO(b'801'))
    r['artifact']['archive_sha256']=c.p.sha(archive)
    # The precise constants are replaced ONLY inside this synthetic test.
    monkeypatch.setattr(c,'ARCHIVE801',c.p.sha(archive))
    original['current_artifact']=dict(r['artifact'])
    # Build a fresh correctly chained historical fixture using its helper, then
    # relocate it. Avoid rewriting genuine records (all records here synthetic).
    original_dir=operations/original['operation_id']
    import json
    (original_dir/'request.json').write_text(json.dumps(original))
    prior=None
    stages=['claim']
    for action in c.EFFECTS:
        stages.extend(['intent:'+action,'result:'+action])
    for stage in stages[:stages.index(tail)+1]:
        file=original_dir/(stage.replace(':','-')+'.json'); row=c.p.read(file)
        row['request_sha256']=c.p.digest(original); row['prior_sha256']=prior
        if stage=='claim':row['payload']['request_sha256']=c.p.digest(original)
        file.write_text(json.dumps(row)); prior=c.p.digest(row)
    r['recovery_evidence']={f.name:c.p.sha(f) for f in original_dir.iterdir()}
    manifest=root/'manifest.json'; manifest.write_text('{}')
    # Both original and current request must bind that new fixture manifest.
    # Use original manifest constant in policy hash check to keep the old request
    # immutable: the SHA adapter below models the frozen official manifest.
    official_manifest=original['current_artifact']['manifest_sha256']
    monkeypatch.setattr(c,'MANIFEST801',official_manifest)
    real_sha=c.p.sha
    monkeypatch.setattr(c.p,'sha',lambda f: official_manifest if Path(f)==manifest else real_sha(f))
    policy.update(repository=str(root),service_files={},archive=str(archive),manifest=str(manifest),
                  recovery_archive=str(archive),recovery_manifest=str(manifest))
    proof=root/'data.json'
    old_cwd=str(c.ROOT/'candidate/enterprise_agent_poc')
    new_cwd=str(c.ROOT/'releases'/r['observed_predecessor']['source']/'enterprise_agent_poc')
    services={role:dict(state='active',pid=100+i,cwd=old_cwd) for i,role in enumerate(c.UNITS)}
    managed={role:'ORIGINAL_801_UNIT' for role in c.UNITS}
    if classification=='PARTIALLY_OR_FULLY_STOPPED_UNKNOWN':
        services['worker']=dict(state='inactive',pid=0,cwd=None)
    elif classification=='TARGET_RUNNING_UNKNOWN_NOT_SUCCESS':
        for role in c.UNITS:services[role]['cwd']=new_cwd;managed[role]=r['observed_predecessor']['source']
    r['recovery_observation']={'classification':classification,'services':services,'managed_sources':managed}
    c.p.exclusive(proof,dict(request_sha256=c.p.digest(r),target=r['candidate'],status='PASS',
                            backup_restore_verified=True,current_data_compatible=True))
    policy.update(data_compatibility_receipt=str(proof),data_compatibility_sha256=c.p.sha(proof))
    monkeypatch.setattr(c.p,'identity',lambda *a:r['candidate'])
    monkeypatch.setattr(c.p,'artifact',lambda *a:r['artifact'])
    monkeypatch.setattr(c.p,'schema_identity',lambda *a:'same-schema')
    # Prestage only a synthetic, hash checked release; run retains real staging verification.
    destination=root/'releases'/c.SOURCE801
    (destination/'enterprise_agent_poc').mkdir(parents=True)
    (destination/'enterprise_agent_poc/app.txt').write_bytes(b'801')
    c.p.exclusive(destination/'installed.json',dict(identity=r['candidate'],artifact=r['artifact']))
    append=c.p.delivery_journal(operations,r); append('claim',{'request_sha256':c.p.digest(r)})
    c.p.exclusive(operations/r['operation_id']/'request.json',r)
    events=[]
    cb=c.Callbacks(r,policy,lambda req:events.append('authority'),append,
                   command=lambda *a:pytest.fail('REAL_SYSTEMCTL_FORBIDDEN'))
    cb.verify_live=lambda *a,**kw:events.append('live')
    cb.verify_current_bytes=lambda:events.append('bytes')
    cb.recovery_observation=lambda:dict(r['recovery_observation'])
    cb.switch=lambda:events.append('switch')
    cb.health=lambda:events.append('health')
    return cb,events,operations/r['operation_id']


@pytest.mark.parametrize('state',['PARTIALLY_OR_FULLY_STOPPED_UNKNOWN','TARGET_RUNNING_UNKNOWN_NOT_SUCCESS',
    'ALREADY_HEALTHY_801_NO_SERVICE_EFFECT'])
def test_p1_run_routes_after_all_preflight_checks(tmp_path,monkeypatch,state):
    cb,events,operation=setup_recovery_run(tmp_path,monkeypatch,state)
    result=cb.run()
    assert (operation/'recovery-observation.json').exists()
    assert result['recovery']['original_status']=='UNKNOWN'
    if state=='ALREADY_HEALTHY_801_NO_SERVICE_EFFECT':
        assert result['status']==state and 'switch' not in events and not (operation/'intent-stage.json').exists()
    else:
        assert events.index('bytes') < events.index('switch') < events.index('health')
        assert (operation/'result-health.json').exists()


def test_p1_state_drift_and_audit_write_failure_prevent_switch(tmp_path,monkeypatch):
    cb,events,operation=setup_recovery_run(tmp_path,monkeypatch,'PARTIALLY_OR_FULLY_STOPPED_UNKNOWN')
    cb.recovery_observation=lambda:{'classification':'DIFFERENT'}
    with pytest.raises(c.p.Block,match='RECOVERY_OBSERVATION_DRIFT'):cb.run()
    assert 'switch' not in events
    cb.recovery_observation=lambda:dict(cb.request['recovery_observation'])
    monkeypatch.setattr(c.p,'exclusive',lambda *a:(_ for _ in ()).throw(OSError('audit unavailable')))
    with pytest.raises(OSError):cb.run()
    assert 'switch' not in events


def test_p1_second_controller_interruption_retains_unknown(tmp_path,monkeypatch):
    cb,events,operation=setup_recovery_run(tmp_path,monkeypatch,'PARTIALLY_OR_FULLY_STOPPED_UNKNOWN')
    def interrupted_switch():
        cb.effect('stopworker',lambda:(_ for _ in ()).throw(KeyboardInterrupt()))
    cb.switch=interrupted_switch
    with pytest.raises(KeyboardInterrupt):cb.run()
    assert (operation/'intent-stopworker.json').exists()
    assert not (operation/'result-stopworker.json').exists()
    assert not (operation/'failure.json').exists() and not (operation/'completion.json').exists()
    with pytest.raises(FileExistsError):c.p.delivery_journal(operation.parent,cb.request)('claim',{})
    next_request=copy.deepcopy(cb.request)
    next_request.update(operation_id=str(uuid.uuid4()),failed_operation_id=operation.name,
                        recovery_evidence={f.name:c.p.sha(f) for f in operation.iterdir()})
    evidence=c.recovery_evidence(next_request,cb.policy)
    assert evidence['original_mode']=='recover' and evidence['original_status']=='UNKNOWN'
    assert evidence['last_stage']=='intent:stopworker'


def test_p1_pre_stop_drift_never_stops_services(tmp_path,monkeypatch):
    cb,events,operation=setup_recovery_run(tmp_path,monkeypatch,'PARTIALLY_OR_FULLY_STOPPED_UNKNOWN')
    calls=[]
    def observe():
        calls.append(1)
        value=copy.deepcopy(cb.request['recovery_observation'])
        if len(calls)>1:value['services']['api']['pid']+=1
        return value
    cb.recovery_observation=observe
    with pytest.raises(c.p.Block,match='RECOVERY_PRESTOP_DRIFT'):cb.run()
    assert 'switch' not in events and not (operation/'intent-stopworker.json').exists()


def test_p1_completed_801_noop_is_not_recoverable_again(tmp_path,monkeypatch):
    cb,events,operation=setup_recovery_run(tmp_path,monkeypatch,'ALREADY_HEALTHY_801_NO_SERVICE_EFFECT')
    result=cb.run();c.p.exclusive(operation/'completion.json',result)
    new=copy.deepcopy(cb.request);new['operation_id']=str(uuid.uuid4())
    new['failed_operation_id']=operation.name
    new['recovery_evidence']={f.name:c.p.sha(f) for f in operation.iterdir()}
    with pytest.raises(c.p.Block,match='RECOVERY_TERMINAL_PRESENT'):c.recovery_evidence(new,cb.policy)


@pytest.mark.parametrize('tail,state,configuration,accepted',[
    ('intent:stage','old','old',True),
    ('intent:stage','stopped','old',False),
    ('intent:stopworker','old','old',True),
    ('intent:stopworker','worker_stopped','old',True),
    ('result:stopworker','old','old',False),
    ('result:stopmcp','stopped','old',True),
    ('intent:bindapi','stopped','api_new',True),
    ('intent:bindapi','stopped','new',False),
    ('result:bindworker','stopped','new',True),
    ('result:bindworker','stopped','old',False),
    ('intent:reload','stopped','new',True),
    ('intent:startmcp','mcp_new','new',True),
    ('intent:startmcp','old','new',False),
    ('intent:health','new','new',True),
    ('intent:health','old','old',False),
])
def test_p1_journal_runtime_state_matrix(tmp_path,monkeypatch,tail,state,configuration,accepted):
    r,policy,directory,original=interrupted(tmp_path,monkeypatch,tail)
    old=str(c.ROOT/'candidate/enterprise_agent_poc')
    new=str(c.ROOT/'releases'/original['candidate']['source']/'enterprise_agent_poc')
    observed={'services':{},'managed_sources':{}}
    for role in c.UNITS:
        active=state in ('old','new') or (state=='worker_stopped' and role!='worker') or (state=='mcp_new' and role=='mcp')
        cwd=(new if state in ('new','mcp_new') else old) if active else None
        observed['services'][role]=dict(state='active' if active else 'inactive',cwd=cwd)
        observed['managed_sources'][role]=(original['candidate']['source'] if configuration=='new' or
            (configuration=='api_new' and role=='api') else 'ORIGINAL_801_UNIT')
    if accepted:c.recovery_lifecycle(original,r['recovery_evidence'],directory,observed)
    else:
        with pytest.raises(c.p.Block):c.recovery_lifecycle(original,r['recovery_evidence'],directory,observed)


def observation_fixture(tmp_path,monkeypatch):
    import pwd
    from types import SimpleNamespace
    r,policy,_,_=interrupted(tmp_path,monkeypatch)
    # Only read fake proc files and temporary files. No /proc mutation, network
    # traffic, or actual systemctl is allowed in these tests.
    base=tmp_path/'materials'; base.mkdir(); (base/'releases').mkdir()
    monkeypatch.setattr(c,'BASE',base)
    unit=base/'unit';unit.write_text('synthetic')
    policy['service_files']={str(unit):c.p.sha(unit)}
    old=str(c.ROOT/'candidate/enterprise_agent_poc')
    r['current_cwds']={role:old for role in c.UNITS}
    rows={unitname:dict(User='eaw-test',ActiveState='active',MainPID=str(200+i),
          FragmentPath=str(unit),DropInPaths='') for i,unitname in enumerate(c.UNITS.values())}
    cwd={row['MainPID']:old for row in rows.values()}
    argv={}
    for role,unitname in c.UNITS.items():
        command=c.dropin(role,r['observed_predecessor']['source']).split('ExecStart=')[-1].strip().split()
        command[command.index('--project')+1]=old
        argv[rows[unitname]['MainPID']]=b'\0'.join(x.encode() for x in command)+b'\0'
    monkeypatch.setattr(pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=1234))
    real_stat=c.os.stat;real_read=Path.read_bytes;real_sha=c.p.sha
    monkeypatch.setattr(c.os,'stat',lambda path,*a,**kw:SimpleNamespace(st_uid=1234)
        if re_proc(path) and str(path).count('/')==2 else real_stat(path,*a,**kw))
    monkeypatch.setattr(c.os,'readlink',lambda path:cwd[str(path).split('/')[2]])
    monkeypatch.setattr(Path,'read_bytes',lambda path:argv[str(path).split('/')[2]]
        if re_proc(path) else real_read(path))
    monkeypatch.setattr(c.p,'sha',lambda path:'e'*64 if re_proc(path) or
        Path(path)==base/'runtime/python/bin/python' else real_sha(path))
    cb=c.Callbacks(r,policy,lambda req:None,lambda *a:None,
                   lambda action,unit:dict(rows[unit]) if action=='show' else pytest.fail('MUTATION_FORBIDDEN'))
    cb.health_transport=lambda:None
    return cb,rows,cwd,argv,base


def re_proc(path):return str(path).startswith('/proc/')


def test_p1_actual_observer_healthy_801_and_partial_stop(tmp_path,monkeypatch):
    cb,rows,cwd,argv,_=observation_fixture(tmp_path,monkeypatch)
    assert cb.recovery_observation()['classification']=='ALREADY_HEALTHY_801_NO_SERVICE_EFFECT'
    rows[c.UNITS['worker']].update(ActiveState='inactive',MainPID='0')
    assert cb.recovery_observation()['classification']=='PARTIALLY_OR_FULLY_STOPPED_UNKNOWN'


@pytest.mark.parametrize('fault',['transition','inactive_pid','cwd','argv','user','unit','health','partial_release'])
def test_p1_actual_observer_rejects_drift(tmp_path,monkeypatch,fault):
    cb,rows,cwd,argv,base=observation_fixture(tmp_path,monkeypatch)
    row=rows[c.UNITS['api']];pid=row['MainPID']
    if fault=='transition':row['ActiveState']='activating'
    elif fault=='inactive_pid':row['ActiveState']='inactive'
    elif fault=='cwd':cwd[pid]='/another/release'
    elif fault=='argv':argv[pid]=b'/bin/sh\0'
    elif fault=='user':row['User']='root'
    elif fault=='unit':row['DropInPaths']='/unexpected/dropin'
    elif fault=='health':cb.health_transport=lambda:(_ for _ in ()).throw(c.p.Block('HEALTH_CONTRACT'))
    else:
        (base/'releases'/c.SOURCE801).mkdir()
        cb.policy['recovery_archive']=str(base/'missing.tar')
    with pytest.raises((c.p.Block,FileNotFoundError)):cb.recovery_observation()


@pytest.mark.parametrize('failure',['expired','missing','target','manifest'])
def test_p1_exact_recovery_authorization_rejected(failure):
    r,p,g,d=fixture('recover')
    if failure=='expired':g['expires_at']=140
    elif failure=='missing':d['owner_references']=[]
    elif failure=='target':r['candidate']['source']='7'*40
    else:r['artifact']['manifest_sha256']='7'*64
    bind(r,p,g,d)
    with pytest.raises(c.p.Block):c.validate(r,p,g,d,150)
