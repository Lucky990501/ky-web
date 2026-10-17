"""One exact-pair isolated native systemd failure probe; never Production switch.

Uses existing systemd API/MCP/Worker commands, pg_ctl, Redis and Native records.
Only this Operation's temporary units/private pointer can be changed. Original
release consumers are not patched or fed fabricated qualification records.
"""
import argparse,json,os,re,signal,socket,subprocess,sys,time,urllib.request
from pathlib import Path
from urllib.parse import urlencode
sys.path.insert(0,str(Path(__file__).resolve().parent))
from native_v2_execution import Authority,Blocked,need,read,put,sha,artifact,pg,restore_host,Snapshot,aggregates,command,TEST_HOST,D906
import native_v2_execution as execution

PY=Path('/opt/enterprise-agent-workbench-test/shared/runtime/python311/bin/python')
REDIS=Path('/opt/enterprise-agent-workbench-test/shared/runtime/redis-7.0.15/bin/redis-server')
ROLES=('api','mcp','worker')
def source(auth,generation):
    rid=auth.p['predecessor']['release_id'] if generation in ('prior','recovered') else auth.c['release_id']
    base=auth.base if auth.fixture else auth.private
    return base/'releases'/rid/'enterprise_agent_poc'
def unit_prefix(auth):return 'eaw-native-v2-'+auth.x['operation_id'].replace('-','')[:12]
def safe_env(auth):
    env={k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG') if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1')
    return env
def runtime_env(auth):
    r=auth.private;env=safe_env(auth)
    env.update(APP_ENV='test',EMBEDDING_PROVIDER='local-hash',EMBEDDING_DIMENSION='1536',
      ENTERPRISE_POC_DATABASE_URL='postgresql:///native_restored?'+urlencode({'host':restore_host(auth),'port':25432,'user':'native_restore'}),
      ENTERPRISE_POC_DATA_DIR=str(auth.base/'initial-runtime-data') if auth.fixture else '/opt/enterprise-agent-workbench/shared/runtime-data',
      ENTERPRISE_POC_OBJECT_STORAGE_DIR=str(auth.base/'initial-objects') if auth.fixture else '/opt/enterprise-agent-workbench/shared/runtime-objects',
      ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA='false',ENTERPRISE_POC_TASK_QUEUE='redis',REDIS_URL='redis://127.0.0.1:26479/0',
      ENTERPRISE_POC_TASK_QUEUE_NAMESPACE=unit_prefix(auth),ENTERPRISE_POC_TOKEN_SECRET='ISOLATED-NATIVE-PROBE-NOT-A-PRODUCTION-CREDENTIAL',
      ENTERPRISE_POC_MCP_HOST='127.0.0.1',ENTERPRISE_POC_MCP_PORT='26481',ENTERPRISE_POC_MCP_URL='http://127.0.0.1:26481/mcp',
      ENTERPRISE_POC_MODEL_BASE_URL='http://127.0.0.1:1/disabled')
    return env
def source_verify(auth,generation):
    app=source(auth,generation);rid=app.parent.name
    artifact(auth,app.parent/(rid+'.tar.gz'),app.parent/(rid+'.manifest.json'),app.parent,prior=generation in ('prior','recovered'))
    return app
def sample(auth):
    root=pg(auth,'restore')
    args=[root/'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-h',restore_host(auth),'-p','25432','-U','native_restore','-d','native_restored']
    with (auth.private/'sample.log').open('ab') as log:
        s=Snapshot(args,log)
        try:
            s.one("BEGIN READ ONLY; SET LOCAL TIME ZONE 'UTC'; SELECT 'READY';")
            need(s.one("SELECT count(*) FROM tasks WHERE status IN ('queued','running');")=='0','NONQUIESCENT_TASK_BACKLOG')
            return aggregates(s)
        finally:s.close()
def supervise(auth):
    auth.isolated();root=pg(auth,'restore');r=auth.private
    completed=json.loads(read(r/'restore/restore.complete.json'))
    need(completed['data_preserved'] is True and completed['private_pg_stopped'] is True
         and completed['operation_id']==auth.x['operation_id'] and not (r/'restore/restore.failed.json').exists(),'COMPLETED_RESTORE_REQUIRED')
    started=False;redis=None
    try:
        command([root/'pg_ctl','-D',r/'restore/pgdata','-l',r/'restore/runtime-pg.log','-o',"-h 127.0.0.1 -k '' -p 25432 -c shared_buffers=16MB -c max_connections=30",'-w','start'],45);started=True
        need('v=7.0.15' in command([REDIS,'--version']).decode(),'PINNED_REDIS_VERSION')
        log=(r/'runtime-redis.log').open('ab')
        redis=subprocess.Popen([str(REDIS),'--bind','127.0.0.1','--port','26479','--save','','--appendonly','no','--dir',str(r)],stdout=log,stderr=log,env=safe_env(auth))
        before=sample(auth)
        expected=json.loads(read(r/'incoming/snapshot.readonly.summary.json'))['tables']
        need(before==expected,'RESTORE_CHANGED_BEFORE_NATIVE_START')
        put(r/'native-ready.json',{'pid':os.getpid(),'network_namespace':os.readlink('/proc/self/ns/net'),'before':before})
        end=time.monotonic()+240
        while not (r/'native-done.json').exists():
            auth.check();need(time.monotonic()<end,'NATIVE_SUPERVISOR_DEADLINE');time.sleep(.2)
    finally:
        if redis and redis.poll() is None:redis.terminate();redis.wait(timeout=10)
        if started:command([root/'pg_ctl','-D',r/'restore/pgdata','-m','fast','-w','stop'],45)
def role(auth,generation,name):
    auth.isolated();app=source_verify(auth,generation)
    need(Path.cwd()==app,'NATIVE_WORKING_DIRECTORY')
    # Native authority is checked under -I -B -S before exec. Actual application
    # dependencies then load in the pinned interpreter, with no inherited keys.
    code="import os,sys,runpy;sys.path.insert(0,os.getcwd());role=sys.argv[1]\n"
    code+="if role=='api':\n import uvicorn;uvicorn.run('app.main:app',host='127.0.0.1',port=26480,log_level='warning')\n"
    code+="elif role=='mcp':\n from app.platform_mcp.server import store,create_mcp\n store.initialize();create_mcp().run(transport='streamable-http')\n"
    code+="else:runpy.run_module('app.worker',run_name='__main__')\n"
    os.execve(str(PY),[str(PY),'-I','-B','-c',code,name],runtime_env(auth))
def http(url,body=None,headers=None):
    data=json.dumps(body).encode() if body is not None else None
    request=urllib.request.Request(url,data=data,headers=headers or {})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=3) as response:
        raw=response.read(1024*1024)
        if not raw:return {},dict(response.headers)
        if response.headers.get('Content-Type','').startswith('text/event-stream'):
            raw=next(x[5:].lstrip() for x in raw.splitlines() if x.startswith(b'data:'))
        return json.loads(raw),dict(response.headers)
def verify(auth,generation,units,label):
    auth.isolated();auth.check();app=source_verify(auth,generation);measured={}
    need(re.fullmatch('[a-z][a-z0-9-]{0,63}',label) and set(units)==set(ROLES)
         and units=={role:unit_prefix(auth)+'-'+generation+'-'+role for role in ROLES},'EXACT_PRIVATE_VERIFY_SCOPE')
    deadline=time.monotonic()+55
    while True:
        try:
            health,_=http('http://127.0.0.1:26480/api/health');break
        except Exception:
            need(time.monotonic()<deadline,'NATIVE_API_READINESS');time.sleep(.2)
    for name,unit in units.items():
        info=dict(x.split('=',1) for x in command(['systemctl','show',unit,'-p','MainPID','-p','ActiveState','-p','SubState']).decode().splitlines())
        pid=int(info['MainPID']);need(pid>0 and info['ActiveState']=='active' and info['SubState']=='running','NATIVE_SERVICE_NOT_RUNNING:'+name)
        need(Path('/proc/'+str(pid)+'/cwd').resolve()==app and Path('/proc/'+str(pid)).stat().st_uid==1000,'MEASURED_NATIVE_SOURCE_CWD')
        need(unit_prefix(auth) in Path('/proc/'+str(pid)+'/cgroup').read_text(),'OWN_NATIVE_UNIT_ONLY')
        measured[name]={'unit':unit,'pid':pid,'cwd':str(app),'exe':os.readlink('/proc/'+str(pid)+'/exe')}
    headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
    while True:
        try:
            answer,h=http('http://127.0.0.1:26481/mcp',{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'native-isolated-probe','version':'1'}}},headers)
            break
        except (OSError,TimeoutError):
            need(time.monotonic()<deadline,'NATIVE_MCP_READINESS');time.sleep(.2)
    session=next((v for k,v in h.items() if k.lower()=='mcp-session-id'),None)
    if session:headers['Mcp-Session-Id']=session
    http('http://127.0.0.1:26481/mcp',{'jsonrpc':'2.0','method':'notifications/initialized'},headers)
    tools,_=http('http://127.0.0.1:26481/mcp',{'jsonrpc':'2.0','id':2,'method':'tools/list'},headers)
    need(len(tools['result']['tools'])>=3,'REAL_MCP_PROTOCOL')
    before=json.loads(read(auth.private/'native-ready.json'))['before'];after=sample(auth)
    put(auth.private/(label+'-data.json'),{'before':before,'after':after,'match':before==after})
    need(before==after,'NATIVE_STARTUP_BUSINESS_DATA_DRIFT')
    result={'fixture':auth.fixture,'generation':generation,'source_commit':D906 if generation in ('prior','recovered') else auth.c['source_commit'],
      'source_tree':auth.p['predecessor']['source_tree'] if generation in ('prior','recovered') else auth.c['source_tree'],
      'services':measured,'health':health,'mcp_tools':len(tools['result']['tools']),'business_data_preserved':True,'provider_calls':0}
    put(auth.private/(label+'.json'),result)
def common_args(a):
    args=['--authority',a.authority,'--request-sha256',a.request_sha256]
    if a.fixture_root:args+=['--fixture-root',a.fixture_root]
    return args
def launch(auth,a,name,action,extra=(),namespace=None,cwd=None,wait=False):
    name=unit_prefix(auth)+'-'+name
    props=['Slice=enterprise-agent-test.slice','CPUQuota=50%','MemoryMax=256M','TasksMax=64','RuntimeMaxSec=240','NoNewPrivileges=yes',
      'PrivateTmp=yes','ProtectHome=yes','ProtectSystem=strict','ReadWritePaths='+str(auth.private),'ReadWritePaths=/tmp',
      'BindPaths='+str(auth.private)+':'+str(auth.private),
      'BindReadOnlyPaths='+str(Path(__file__).resolve().parents[2])+':'+str(Path(__file__).resolve().parents[2]),
      'InaccessiblePaths=-/opt/enterprise-agent-workbench-test/data -/opt/enterprise-agent-workbench-test/shared/credentials -/opt/enterprise-agent-workbench-test/shared/config -/opt/enterprise-agent-workbench-test/releases']
    if auth.fixture:props+=['BindPaths='+str(auth.base)+':'+str(auth.base),
        'BindPaths='+str(auth.private/'mirror/shared/runtime-data')+':'+str(auth.base/'initial-runtime-data')]
    else:
        need((auth.private/'mirror/shared/runtime-data/skill-registry').is_dir(),'ACTUAL_REGISTRY_MIRROR_REQUIRED')
        props+=['BindReadOnlyPaths='+str(auth.directory)+':'+str(auth.directory),
                'BindPaths='+str(auth.private/'mirror')+':/opt/enterprise-agent-workbench',
                'ReadWritePaths=/opt/enterprise-agent-workbench']
    props+=['NetworkNamespacePath=/proc/'+str(namespace)+'/ns/net'] if namespace else ['PrivateNetwork=yes']
    if cwd:props+=['WorkingDirectory='+str(cwd)]
    cmd=['sudo','-n','systemd-run','--unit='+name,'--uid=lucky','--gid=lucky','--collect']
    if wait:cmd+=['--wait','--pipe']
    else:props+=['StandardOutput=append:'+str(auth.private/(name+'.log')),'StandardError=inherit']
    for p in props:cmd+=['-p',p]
    cmd+=['/usr/bin/env','-i',*[k+'='+v for k,v in safe_env(auth).items()],str(PY),'-I','-B','-S']
    cmd+=[str(Path(__file__).resolve()),action,*common_args(a),*extra]
    result=subprocess.run(cmd,capture_output=True,timeout=75 if wait else 20)
    put(auth.private/(name+'.launch.json'),{'exit_code':result.returncode,'stdout':result.stdout.decode(errors='replace'),'stderr':result.stderr.decode(errors='replace')})
    if not wait:need(result.returncode==0,'NATIVE_UNIT_LAUNCH:'+name)
    return name,result.returncode
def stop_group(auth,units):
    for unit in units.values():
        p=subprocess.run(['systemctl','show',unit,'-p','MainPID'],capture_output=True,text=True)
        if p.returncode:continue
        pid=int(p.stdout.strip().split('=',1)[1] or '0')
        if pid<=0:continue
        try:
            need(unit_prefix(auth) in Path('/proc/'+str(pid)+'/cgroup').read_text() and Path('/proc/'+str(pid)).stat().st_uid==1000,'STOP_ONLY_OWN_PRIVATE_UNIT')
            os.kill(pid,signal.SIGTERM)
        except (ProcessLookupError,FileNotFoundError):pass
    time.sleep(1)
def rehearse(auth,a):
    need(os.uname().nodename==TEST_HOST and os.getuid()==1000,'TEST_CONTROLLER_ONLY')
    need(auth.x['recovery_helper_sha256']==sha(Path(__file__).read_bytes()),'PINNED_RECOVERY_PROBE')
    auth.check(180);need(not (auth.private/'native.claim.json').exists(),'NATIVE_CLAIM_ALREADY_CONSUMED')
    for g in ('prior','candidate'):source_verify(auth,g)
    put(auth.private/'native.claim.json',{'operation_id':auth.x['operation_id'],'request_sha256':auth.pin})
    groups=[];result=None
    try:
        launch(auth,a,'supervisor','supervise')
        end=time.monotonic()+45
        while not (auth.private/'native-ready.json').exists():need(time.monotonic()<end,'SUPERVISOR_READY_TIMEOUT');time.sleep(.2)
        ready=json.loads(read(auth.private/'native-ready.json'));ns=ready['pid']
        need(Path('/proc/'+str(ns)).stat().st_uid==1000 and os.readlink('/proc/'+str(ns)+'/ns/net')==ready['network_namespace'],'OWN_PRIVATE_NETNS')
        def start(g):
            units={role:launch(auth,a,g+'-'+role,'role',['--generation',g,'--role',role],ns,source(auth,g))[0] for role in ROLES}
            groups.append(units);return units
        def check(g,units,label):
            _,code=launch(auth,a,label,'verify',['--generation',g,'--units-json',json.dumps(units),'--label',label],ns,wait=True)
            need(code==0,'NATIVE_VERIFY:'+label)
        pointer=auth.private/'runtime-current';pointer.symlink_to(source(auth,'prior'))
        prior=start('prior');check('prior',prior,'prior-before')
        _,code=launch(auth,a,'precommit-fault','role',['--generation','candidate','--role','api'],ns,source(auth,'candidate'),wait=True)
        need(code!=0 and pointer.resolve()==source(auth,'prior'),'ACTUAL_PRECOMMIT_BIND_FAILURE')
        check('prior',prior,'prior-preserved');stop_group(auth,prior)
        candidate=start('candidate');check('candidate',candidate,'candidate-running')
        staged=auth.private/'pointer-staged';staged.symlink_to(source(auth,'candidate'));os.replace(staged,pointer)
        pid=json.loads(read(auth.private/'candidate-running.json'))['services']['api']['pid']
        need(unit_prefix(auth) in Path('/proc/'+str(pid)+'/cgroup').read_text(),'KILL_ONLY_OWN_CANDIDATE_API')
        os.kill(pid,signal.SIGKILL);stop_group(auth,candidate)
        staged=auth.private/'pointer-recovery';staged.symlink_to(source(auth,'prior'));os.replace(staged,pointer)
        recovered=start('recovered');check('recovered',recovered,'d906-recovered')
        need(pointer.resolve()==source(auth,'prior'),'EXACT_D906_PRIVATE_RECOVERY')
        result={'kind':'ISOLATED_NATIVE_SYSTEMD_EXACT_PAIR_RECOVERY_NOT_PRODUCTION_RELEASE','fixture':auth.fixture,
          'source_commit':auth.c['source_commit'],'source_tree':auth.c['source_tree'],'operation_id':auth.x['operation_id'],
          'predecessor_source':D906,'pre_commit_failure_recovery':True,'post_commit_runtime_recovery':True,
          'business_data_preserved':True,'native_systemd_services':True,'real_failure_injections':2,'assertions':execution.ASSERTIONS,'failures':0,'errors':0,'skipped':0,
          'production_release_scripts_executed':False,'production_changes':0,'provider_calls':0}
        put(auth.private/'native.complete.json',result)
    except BaseException:
        put(auth.private/'native.failed.json',{'status':'STOP_RETAIN_CLAIM_AND_ALL_FAILURE_EVIDENCE'});raise
    finally:
        for group in reversed(groups):stop_group(auth,group)
        if not (auth.private/'native-done.json').exists():put(auth.private/'native-done.json',{'stop_own_supervisor':True})
    return result
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['rehearse','supervise','role','verify']);p.add_argument('--authority',required=True);p.add_argument('--request-sha256',required=True);p.add_argument('--fixture-root');p.add_argument('--generation',choices=['prior','candidate','recovered']);p.add_argument('--role',choices=ROLES);p.add_argument('--units-json');p.add_argument('--label')
    a=p.parse_args();auth=Authority(a.authority,a.request_sha256,a.fixture_root)
    need(auth.x['recovery_helper_sha256']==sha(Path(__file__).read_bytes()),'EXACT_RECOVERY_HELPER')
    if a.action=='supervise':supervise(auth)
    elif a.action=='role':role(auth,a.generation,a.role)
    elif a.action=='verify':verify(auth,a.generation,json.loads(a.units_json),a.label)
    else:print(json.dumps(rehearse(auth,a)),flush=True)
if __name__=='__main__':
    try:main()
    except BaseException as exc:
        print(json.dumps({'status':'STOP_RETAIN_NO_RETRY','error_type':type(exc).__name__,'failure':str(exc) if isinstance(exc,Blocked) else 'SYSTEM_OR_INPUT_FAILURE'}),flush=True);raise SystemExit(1)
