"""Offline component evidence ONLY. No install, real host or service operations."""
import copy
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import test_delivery_adoption as a
from scripts import test_delivery_persistent as c
from test_persistent_callbacks import fixture, bind


def reuse(tmp_path, monkeypatch):
    r, p, g, d = fixture()
    p.update(runtime_contract=c.REUSE, runtime_reuse_evidence=str(tmp_path/'reuse.json'),
             runtime_roots=[str(c.RUNTIME)], tooling={**p['tooling'],'git_parents':[c.EXECUTOR790['source']]})
    r['candidate'] = dict(source=c.SOURCE33,tree=c.TREE33,git_parents=[c.SOURCE801])
    record = dict(contract=c.REUSE,status='OBSERVED_EXISTING_RUNTIME_NOT_FULL_QUALIFICATION',
        environment='test',hostname=p['hostname'],runtime_root=str(c.RUNTIME),
        runtime_roots=p['runtime_roots'],executor_baseline=c.EXECUTOR790,tooling=p['tooling'],
        predecessor=dict(source=c.SOURCE801,tree=c.TREE801),target=r['candidate'],
        operator=p['operator'],units=c.UNITS,issued_at=100,expires_at=200,
        maintenance_exclusion='NO_CONCURRENT_RUNTIME_OR_UNIT_MAINTENANCE',runtime_mutation_authority=False,
        mountinfo_sha256='b'*64,
        runtime_inventory_sha256=c.p.digest({'isolated_inventory':True}),
        dependency_files={'/synthetic/lib.so':'a'*64},executables={})
    def save():
        Path(p['runtime_reuse_evidence']).write_text(json.dumps(record))
        p['runtime_profile_sha256']=c.p.digest(record)
        g.update(tooling=p['tooling'],runtime_profile_sha256=p['runtime_profile_sha256'])
        bind(r,p,g,d)
    monkeypatch.setattr(c.p,'trusted',lambda path,**kw:Path(path))
    monkeypatch.setattr(c.time,'time',lambda:150)
    monkeypatch.setattr(c.socket,'gethostname',lambda:'instance-mif0a40l')  # component only, no Native claim
    sha=c.p.sha
    monkeypatch.setattr(c.p,'sha',lambda path:'b'*64 if str(path)=='/proc/self/mountinfo' else sha(path))
    save()
    return r,p,g,d,record,save


def test_reuse_accepts_explicit_bound_evidence(tmp_path,monkeypatch):
    r,p,g,d,v,save=reuse(tmp_path,monkeypatch)
    c.validate(r,p,g,d,150)
    assert c.reuse_evidence(p)==v


@pytest.mark.parametrize('fault',['host','environment','service','source','tree','parent','runtime',
    'tooling','baseline','operator','scope','mutation','expired','old_status','missing','actual_host'])
def test_reuse_bad_evidence_rejected(tmp_path,monkeypatch,fault):
    r,p,g,d,v,save=reuse(tmp_path,monkeypatch)
    if fault=='host':v['hostname']='production'
    if fault=='actual_host':monkeypatch.setattr(c.socket,'gethostname',lambda:'other-host')
    if fault=='environment':v['environment']='production'
    if fault=='service':v['units']={**c.UNITS,'other':'nginx.service'}
    if fault=='source':v['target']={**v['target'],'source':'1'*40}
    if fault=='tree':v['target']={**v['target'],'tree':'1'*40}
    if fault=='parent':p['tooling']['git_parents']=['1'*40]
    if fault=='runtime':v['runtime_root']='/other-runtime'
    if fault=='tooling':v['tooling']={'source':'1'*40}
    if fault=='baseline':v['executor_baseline']={**c.EXECUTOR790,'tree':'1'*40}
    if fault=='operator':v['operator']={'name':'other'}
    if fault=='scope':v['maintenance_exclusion']='ALLOW_OTHER_ROOT_WRITERS'
    if fault=='mutation':v['runtime_mutation_authority']=True
    if fault=='expired':v['expires_at']=150
    if fault=='old_status':v['status']='PASS'
    if fault=='missing':v.pop('runtime_inventory_sha256'); v.pop('contract')
    save()
    with pytest.raises((c.p.Block,KeyError)):c.reuse_evidence(p)


@pytest.mark.parametrize('fault',['candidate','predecessor','tree','parent','recover_target'])
def test_exact_application_pair(tmp_path,monkeypatch,fault):
    r,p,g,d,v,save=reuse(tmp_path,monkeypatch)
    if fault=='candidate':r['candidate']['source']='1'*40
    if fault=='predecessor':r['observed_predecessor']['source']='1'*40
    if fault=='tree':r['candidate']['tree']='1'*40
    if fault=='parent':r['candidate']['git_parents']=[]
    if fault=='recover_target':r['mode']='recover'
    save()
    with pytest.raises(c.p.Block):c.validate(r,p,g,d,150)


@pytest.mark.parametrize('fault',['unknown','mixed','downgrade','null'])
def test_contract_cannot_fall_through(tmp_path,monkeypatch,fault):
    r,p,g,d,v,save=reuse(tmp_path,monkeypatch)
    if fault=='unknown':p['runtime_contract']='V99'
    if fault=='mixed':p['runtime_closure_receipt']='old.json'
    if fault=='downgrade':p.pop('runtime_contract')
    if fault=='null':p['runtime_contract']=None
    with pytest.raises(c.p.Block):c.verify_runtime_installation(p)


def test_inventory_is_actual_and_rechecked(tmp_path,monkeypatch):
    r,p,g,d,v,save=reuse(tmp_path,monkeypatch); calls=[]
    monkeypatch.setattr(a,'runtime_inventory',lambda roots,**kw:calls.append(kw) or {'isolated_inventory':True})
    monkeypatch.setattr(c,'observe_reused_runtime',lambda *args:calls.append('observed'))
    assert c.verify_reused_runtime(p)==v
    assert calls==[dict(protected=True,timezone_hardlinks=str(c.TIMEZONE)),'observed']
    monkeypatch.setattr(a,'runtime_inventory',lambda *args,**kw:{'drift':True})
    with pytest.raises(c.p.Block,match='EXISTING_RUNTIME_DRIFT'):c.verify_reused_runtime(p)


def test_observation_hash_and_mount_drift_are_rejected(tmp_path,monkeypatch):
    r,p,g,d,v,save=reuse(tmp_path,monkeypatch)
    v['expires_at']=199
    Path(p['runtime_reuse_evidence']).write_text(json.dumps(v))
    with pytest.raises(c.p.Block,match='RUNTIME_REUSE_EVIDENCE'):c.reuse_evidence(p)
    save();v['mountinfo_sha256']='c'*64;save()
    with pytest.raises(c.p.Block,match='RUNTIME_MOUNT_DRIFT'):c.reuse_evidence(p)


@pytest.mark.parametrize('fault',[False,True])
def test_install_consumer_verifies_original_executor_identity(tmp_path,monkeypatch,fault):
    r,p,g,d,v,save=reuse(tmp_path,monkeypatch);calls=[]
    expected={**c.EXECUTOR790,'git_parents':['3f683774e412ed405fb321c4b97a0d273d39f2b0']}
    monkeypatch.setattr(c.p,'identity',lambda *args:{**expected,'tree':'0'*40} if fault else expected)
    monkeypatch.setattr(c,'verify_reused_runtime',lambda policy:calls.append(policy))
    if fault:
        with pytest.raises(c.p.Block,match='RUNTIME_REUSE_EXECUTOR_BASELINE'):c.verify_runtime_installation(p)
        assert not calls
    else:
        c.verify_runtime_installation(p);assert calls==[p]


@pytest.mark.parametrize('explicit',[False,True])
def test_original_full_runtime_path_not_weakened(tmp_path,monkeypatch,explicit):
    p={'runtime_roots':['original'],'runtime_closure_receipt':str(tmp_path/'closure')}
    if explicit:p['runtime_contract']=c.FULL_RUNTIME
    inventory={'original':True}
    receipt=dict(status='PASS',runtime_inventory_sha256=c.p.digest(inventory),
                 checks={k:'PASS' for k in ('dynamic_loader','python_imports','pgvector_load','redis_start','relocation')})
    Path(p['runtime_closure_receipt']).write_text(json.dumps(receipt))
    p['runtime_profile_sha256']=c.p.digest(receipt)
    calls=[]
    monkeypatch.setattr(c.p,'trusted',lambda x,**kw:Path(x))
    monkeypatch.setattr(a,'runtime_inventory',lambda roots,**kw:calls.append(kw) or inventory)
    c.verify_runtime_installation(p)
    assert calls==[{'protected':True}]  # no hardlink exception in the old path
    receipt['checks'].pop('relocation')
    Path(p['runtime_closure_receipt']).write_text(json.dumps(receipt));p['runtime_profile_sha256']=c.p.digest(receipt)
    with pytest.raises(c.p.Block,match='PERSISTENT_RUNTIME_CLOSURE'):c.verify_runtime_installation(p)


@pytest.mark.parametrize('role',['postgresql','redis'])
def test_pg_redis_are_readonly_observation_only(monkeypatch,role):
    unit='enterprise-agent-standard-test-'+role+'.service';calls=[]
    monkeypatch.setattr(c.subprocess,'run',lambda args,**kw:calls.append(args) or SimpleNamespace(returncode=0,stdout=b'User=eaw-test'))
    assert c.systemctl('show',unit)=={'User':'eaw-test'}
    for action in ('stop','start'):
        with pytest.raises(c.p.Block):c.systemctl(action,unit)
    assert len(calls)==1


def hardlinks(tmp_path,monkeypatch):
    root=tmp_path/'runtime';tz=root/'share/timezone';tz.mkdir(parents=True)
    first=tz/'a';first.write_bytes(b'TZif deterministic data');os.link(first,tz/'b')
    monkeypatch.setattr(a,'TIMEZONE_DATA_ROOT',tz)
    # Metadata doubles ONLY for temp paths: inode/link counts, directory entries
    # and hashes are real. This is not Root-path or Native installation evidence.
    real_stat,real_lstat=os.stat,os.lstat
    def metadata(fn,path,*args,**kw):
        s=fn(path,*args,**kw)
        fields={name:getattr(s,name) for name in dir(s) if name.startswith('st_')}
        fields.update(st_uid=0,st_gid=0)
        if stat.S_ISREG(s.st_mode):fields['st_mode']=stat.S_IFREG|0o644
        return SimpleNamespace(**fields)
    monkeypatch.setattr(a.os,'stat',lambda path,*args,**kw:metadata(real_stat,path,*args,**kw))
    monkeypatch.setattr(a.os,'lstat',lambda path,*args,**kw:metadata(real_lstat,path,*args,**kw))
    monkeypatch.setattr(a.os,'listxattr',lambda *args,**kw:[],raising=False)
    monkeypatch.setattr(a,'sys',SimpleNamespace(platform='linux'))
    def trust(path):
        path=Path(path)
        if path.is_file():a.need(path.stat().st_nlink==1,'SINGLE_LINK_REQUIRED')
        return path
    monkeypatch.setattr(a,'trust',trust)
    return root,tz,first


def test_closed_hardlink_group_and_original_rule(tmp_path,monkeypatch):
    root,tz,first=hardlinks(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='SINGLE_LINK_REQUIRED'):a.runtime_inventory([str(root)],protected=True)
    result=a.runtime_inventory([str(root)],protected=True,timezone_hardlinks=str(tz))
    assert result['dependency_closure_proven'] is False
    assert result['contract']=='TEST_EXISTING_RUNTIME_INVENTORY_V1'
    assert result['entries'][str(first)]['links']==2
    assert result['entries'][str(first)]['sha256']==a.sha(tz/'b')
    with pytest.raises(ValueError,match='SINGLE_LINK_REQUIRED'):a.trust(first)


@pytest.mark.parametrize('fault',['external','other_directory','wrong_scope','changed_hash','new_alias','bad_owner','bad_mode','acl'])
def test_hardlink_negatives(tmp_path,monkeypatch,fault):
    root,tz,first=hardlinks(tmp_path,monkeypatch)
    if fault=='external':os.link(first,tmp_path/'outside')
    if fault=='other_directory':os.link(first,root/'code.py')
    if fault=='wrong_scope':tz=root
    if fault in ('bad_owner','bad_mode'):
        for name in ('stat','lstat'):
            original=getattr(a.os,name)
            def bad(path,*args,_original=original,**kw):
                v=_original(path,*args,**kw)
                if Path(path)==first:
                    values=vars(v).copy();values['st_uid' if fault=='bad_owner' else 'st_mode']=1000 if fault=='bad_owner' else stat.S_IFREG|0o666
                    return SimpleNamespace(**values)
                return v
            monkeypatch.setattr(a.os,name,bad)
    if fault=='acl':
        monkeypatch.setattr(a.os,'listxattr',lambda path,*args,**kw:['system.posix_acl_access'] if Path(path)==first else [])
        monkeypatch.setattr(a.os,'getxattr',lambda *args,**kw:b'synthetic-acl')
    if fault in ('changed_hash','new_alias'):
        real=a.sha;calls=[]
        def sha(path):
            value=real(path);calls.append(path)
            if len(calls)==1:
                if fault=='changed_hash':first.write_bytes(b'changed')
                else:os.link(first,tz/'new')
            return value
        monkeypatch.setattr(a,'sha',sha)
    with pytest.raises(ValueError):a.runtime_inventory([str(root)],protected=True,timezone_hardlinks=str(tz))


def process_fixture(tmp_path,monkeypatch):
    import pwd
    runtime=tmp_path/'runtime';runtime.mkdir()
    exe=runtime/'python';exe.write_bytes(b'isolated executable')
    library=tmp_path/'library.so';library.write_bytes(b'isolated dependency')
    unitfile=tmp_path/'unit';unitfile.write_text('synthetic unit')
    monkeypatch.setattr(c,'RUNTIME',runtime)
    monkeypatch.setattr(c,'BASE',tmp_path)
    units={**c.UNITS,**{r:'enterprise-agent-standard-test-'+r+'.service' for r in ('postgresql','redis')}}
    rows={unit:dict(User='eaw-test',ActiveState='active',MainPID=str(100+i),
                   FragmentPath=str(unitfile),DropInPaths='') for i,unit in enumerate(units.values())}
    record=dict(executables={r:str(exe) for r in units},
                dependency_files={str(exe):c.p.sha(exe),str(library):c.p.sha(library)},launcher_files={})
    for directory in ('repository','tooling'):
        launcher=tmp_path/directory/'enterprise_agent_poc/scripts/test_delivery_fixture.py'
        launcher.parent.mkdir(parents=True);launcher.write_text('synthetic launcher')
        record['launcher_files'][str(launcher)]=c.p.sha(launcher)
        record['dependency_files'][str(launcher)]=c.p.sha(launcher)
    import hashlib
    record['persistent_processes']={r:dict(pid=rows[units[r]]['MainPID'],start_time='123',
        argv_sha256=hashlib.sha256(b'synthetic command').hexdigest()) for r in ('postgresql','redis')}
    policy={'service_files':{str(unitfile):c.p.sha(unitfile)}}
    monkeypatch.setattr(a,'trust',lambda path:Path(path))
    monkeypatch.setattr(c.p,'trusted',lambda path,**kw:Path(path))
    monkeypatch.setattr(pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=1234))
    real_stat,real_read,real_bytes=c.os.stat,Path.read_text,Path.read_bytes
    def procstat(path,*args,**kw):
        if str(path).startswith('/proc/'):
            if str(path).count('/')==2:return SimpleNamespace(st_uid=1234)
            if Path(path).name=='exe':return real_stat(exe)
        return real_stat(path,*args,**kw)
    monkeypatch.setattr(c.os,'stat',procstat)
    monkeypatch.setattr(c.os,'readlink',lambda path:str(exe))
    def text(path,*args,**kw):
        if str(path).startswith('/proc/'):
            if path.name=='stat':return '1 (fixture) '+' '.join(['S']+['0']*18+['123'])
            if path.name=='maps':
                s=library.stat()
                return f'1-2 r-xp 0 {os.major(s.st_dev):02x}:{os.minor(s.st_dev):02x} {s.st_ino} '+str(library)+'\n'
        return real_read(path,*args,**kw)
    monkeypatch.setattr(Path,'read_text',text)
    monkeypatch.setattr(Path,'read_bytes',lambda path:b'synthetic command'
                        if str(path).startswith('/proc/') else real_bytes(path))
    def command(action,unit):
        assert action=='show'  # no service mutation in observer
        return dict(rows[unit])
    return policy,record,rows,command,exe,library,unitfile


def test_live_dependency_observer_and_interrupted_services(tmp_path,monkeypatch):
    p,v,rows,command,*_=process_fixture(tmp_path,monkeypatch)
    c.observe_reused_runtime(p,v,command)
    rows[c.UNITS['api']].update(ActiveState='inactive',MainPID='0')
    c.observe_reused_runtime(p,v,command)  # only application roles can be inactive


@pytest.mark.parametrize('fault',['dependency','unlisted_mapping','executable','user','unit','pg_stopped','redis_stopped','pid','uid','pg_restart','redis_restart','launcher','mapping_inode','executable_inode'])
def test_actual_observer_rejects_drift(tmp_path,monkeypatch,fault):
    p,v,rows,command,exe,lib,unit=process_fixture(tmp_path,monkeypatch)
    if fault=='dependency':lib.write_bytes(b'drift')
    if fault=='unlisted_mapping':v['dependency_files'].pop(str(lib))
    if fault=='executable':v['executables']['api']=str(exe.parent/'different')
    if fault=='user':rows[c.UNITS['api']]['User']='root'
    if fault=='unit':unit.write_text('drift')
    if fault=='launcher':v['launcher_files'].clear()
    if fault in ('pg_restart','redis_restart'):
        v['persistent_processes']['postgresql' if fault=='pg_restart' else 'redis']['start_time']='different'
    if fault in ('pg_stopped','redis_stopped'):
        role='postgresql' if fault=='pg_stopped' else 'redis'
        rows['enterprise-agent-standard-test-'+role+'.service']['ActiveState']='inactive'
    if fault=='pid':
        old=command;calls=[]
        def command(action,unit):
            value=old(action,unit);calls.append(unit)
            if calls.count(unit)>1:value['MainPID']='999'
            return value
    if fault=='uid':monkeypatch.setattr(__import__('pwd'),'getpwnam',lambda name:SimpleNamespace(pw_uid=9999))
    if fault=='mapping_inode':
        old_read=Path.read_text
        def changed(path,*args,**kw):
            value=old_read(path,*args,**kw)
            if str(path).startswith('/proc/') and path.name=='maps':
                fields=value.split(None,5);fields[4]='999';return ' '.join(fields)
            return value
        monkeypatch.setattr(Path,'read_text',changed)
    if fault=='executable_inode':
        old_stat=c.os.stat
        monkeypatch.setattr(c.os,'stat',lambda path,*args,**kw:SimpleNamespace(st_dev=0,st_ino=0)
            if str(path).startswith('/proc/') and Path(path).name=='exe' else old_stat(path,*args,**kw))
    with pytest.raises(c.p.Block):c.observe_reused_runtime(p,v,command)


def test_post_start_health_rechecks_entire_runtime(tmp_path,monkeypatch):
    r,p,g,d,v,save=reuse(tmp_path,monkeypatch);events=[]
    cb=c.Callbacks(r,p,lambda req:None,lambda *args:None)
    cb.verify_live=lambda *args,**kw:events.append('live')
    cb.health_transport=lambda:events.append('health')
    monkeypatch.setattr(c,'verify_reused_runtime',lambda policy:events.append('inventory-and-dependencies'))
    cb.health()
    assert events==['live','health','inventory-and-dependencies']


@pytest.mark.parametrize('fault',[None,'post_effect_dependency','expired_after_effect','installation'])
def test_actual_execute_consumer_holds_lock_and_retains_unknown(tmp_path,monkeypatch,fault):
    from contextlib import contextmanager
    r,p,g,d,v,save=reuse(tmp_path,monkeypatch)
    p['revocation_marker']=str(tmp_path/'revoked')
    g['issuer_record_path']=str(tmp_path/'decision.json')
    bind(r,p,g,d)
    Path(g['issuer_record_path']).write_text(json.dumps(d))
    p['accepted_issuer_record_sha256']=c.p.sha(g['issuer_record_path'])
    g['issuer_record_sha256']=p['accepted_issuer_record_sha256']
    bind(r,p,g,d)
    paths=[]
    for name,value in (('request',r),('policy',p),('grant',g)):
        path=tmp_path/(name+'.json');path.write_text(json.dumps(value));paths.append(path)
    state={'locked':False,'effect':False};events=[];writes={}
    @contextmanager
    def lock():
        state['locked']=True
        def verify():assert state['locked']
        try:yield verify
        finally:state['locked']=False
    monkeypatch.setattr(c.runpy,'run_path',lambda *args,**kw:{'release_lock':lock})
    monkeypatch.setattr(c.socket,'gethostname',lambda:p['hostname'])  # synthetic component host only
    monkeypatch.setattr(c,'sys',SimpleNamespace(platform='linux',flags=SimpleNamespace(isolated=True,no_site=True),dont_write_bytecode=True))
    monkeypatch.setattr(c.os,'geteuid',lambda:0,raising=False)  # component flag, never actual Root
    def installation(policy):
        assert state['locked'];events.append('installation')
        if fault=='installation':raise c.p.Block('INSTALLATION_REJECTED')
    monkeypatch.setattr(c,'verify_installation',installation)
    def runtime(policy,**kw):
        assert state['locked'];events.append('fresh-runtime')
        c.reuse_evidence(policy)
        if fault=='post_effect_dependency' and state['effect']:raise c.p.Block('RUNTIME_DEPENDENCY_DRIFT')
    monkeypatch.setattr(c,'verify_reused_runtime',runtime)
    def journal(*args):
        assert state['locked']
        return lambda phase,value:events.append(phase)
    monkeypatch.setattr(c.p,'delivery_journal',journal)
    monkeypatch.setattr(c.p,'exclusive',lambda path,value:writes.update({Path(path).name:value}))
    def run(cb):
        def effect():
            assert state['locked'];state['effect']=True
            if fault=='expired_after_effect':monkeypatch.setattr(c.time,'time',lambda:200)
        cb.effect('stage',effect)
        return {'component_only':True}
    monkeypatch.setattr(c.Callbacks,'run',run)
    if fault:
        with pytest.raises(c.p.Block):c.execute(*paths)
        assert 'completion.json' not in writes
        if fault!='installation':assert writes['failure.json']['status']=='UNKNOWN_REQUIRES_AUTHORIZED_RECOVERY'
        else:assert not state['effect'] and not writes
    else:
        assert c.execute(*paths)=={'component_only':True}
        assert 'completion.json' in writes and events.index('installation') < events.index('claim')
    assert state['locked'] is False
