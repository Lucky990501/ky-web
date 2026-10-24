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
