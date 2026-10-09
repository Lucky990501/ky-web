"""Actual HTTP/systemd/PG16.6 A-G faults in the existing isolated Native realm.

Run INSIDE its loopback-only namespace as root after fresh formal issuance.
No test rows, quality results, audit entries, schema hooks, or runtime doubles
are injected. Database locks only choose the process-interruption boundary.
Each case requires a fresh environment-owned contract, never resealed history.
"""
import argparse
import asyncio
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from redis import Redis

ROOT=Path('/opt/enterprise-agent-native-isolated')
AUTH=Path('/etc/enterprise-agent-native-isolated-v1')
PY=sys.executable
UNITS=['enterprise-agent-native-isolated-'+r+'.service' for r in ('api','mcp','worker')]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--scenario',choices=list('ABCDEFG')+['R'],required=True)
    case=parser.parse_args().scenario
    assert os.geteuid()==0 and sys.dont_write_bytecode
    env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        'HOME':str(ROOT/'private'),**dict(line.split('=',1) for line in (AUTH/'service.env').read_text().splitlines())}
    assert env['APP_ENV']=='test' and env['ENTERPRISE_POC_DATABASE_URL']=='postgresql://native_isolated@127.0.0.1:56432/native_isolated'
    pair=json.loads((AUTH/'runtime-pair.v1.json').read_text())
    scope=json.loads((AUTH/'scope.v1.json').read_text())
    scripts=Path(pair['tooling']['path'])/'enterprise_agent_poc/scripts'
    operator=[PY,'-B',str(scripts/'runtime_recovery_operator.py')]
    cli=[PY,'-B',str(scripts/'run_exact_test_admin_lifecycle.py')]
    evidence={'scenario':case,'source_pair':{k:{f:pair[k][f] for f in ('source','tree')} for k in ('application','tooling')},
              'run_id':scope['run_id'],'provider_calls':0,'wechat_calls':0,'image_calls':0}
    def command(args,expected=0):
        result=subprocess.run(list(map(str,args)),env=env,text=True,capture_output=True,timeout=90)
        assert result.returncode==expected,(result.returncode,result.stdout[-1600:],result.stderr[-1600:])
        return result.stdout.strip()
    def native(action='status',expected=0):return json.loads(command(cli+[action,'--run-id',scope['run_id']],expected))
    def recover():return json.loads(command(operator+['recover']))
    def wait(check,code):
        until=time.monotonic()+25
        while not check():
            assert time.monotonic()<until,code
            time.sleep(.03)
    conn=psycopg.connect(env['ENTERPRISE_POC_DATABASE_URL'],row_factory=dict_row,autocommit=True)
    assert conn.execute("SELECT current_setting('server_version_num') AS v").fetchone()['v']=='160006'
    redis=Redis.from_url(env['REDIS_URL'],decode_responses=True)
    def rows():
        return dict(instances=conn.execute('SELECT tenant_id,agent_id,status,instance_id,agent_template_version_id FROM tenant_agent_instances ORDER BY instance_id').fetchall(),
                    tests=conn.execute("SELECT id,task_id,status,result_json FROM agent_template_tests WHERE test_type='runtime' ORDER BY id").fetchall(),
                    tasks=conn.execute('SELECT id,tenant_id,user_id,agent_id,status,run_id,conversation_id FROM tasks ORDER BY id').fetchall())
    def blocked(prefix):
        return bool(conn.execute("SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE %s",(prefix+'%',)).fetchone())
    def lock(table):
        c=psycopg.connect(env['ENTERPRISE_POC_DATABASE_URL']);c.execute('LOCK TABLE '+table+' IN SHARE MODE');return c
    def api(opener,path,body,headers=None):
        req=urllib.request.Request('http://127.0.0.1:28100'+path,data=json.dumps(body).encode(),
            headers={'Content-Type':'application/json',**(headers or {})})
        try:
            with opener.open(req,timeout=45) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as exc:return exc.code,json.loads(exc.read(4096))
    def kill_api():
        command(['systemctl','kill','--kill-who=main','--signal=SIGKILL',UNITS[0]])
        wait(lambda:command(['systemctl','show',UNITS[0],'-p','MainPID','--value'])=='0','API_NOT_DEAD')
    jar=http.cookiejar.CookieJar()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(jar))
    assert json.loads(command(operator+['startup']))['status']=='PASS'
    assert api(opener,'/api/v1/auth/login',json.loads((ROOT/'private/bootstrap-login.json').read_text()))[0]==200
    token=next(c.value for c in jar if c.name=='workbench_session')
    tokenfile=Path('/run/enterprise-agent-native-isolated-v1/session.token');tokenfile.parent.mkdir(mode=0o700,exist_ok=True)
    if tokenfile.exists():tokenfile.rename(tokenfile.with_name('prior-'+str(time.time_ns())+'.token'))
    with tokenfile.open('x') as stream:stream.write(token)
    tokenfile.chmod(0o600)
    assert native('prepare')['status']=='REVOKE_PROVEN_BEFORE_GRANT'
    assert native('grant')['status']=='GRANTED'
    if case!='E':command(['systemctl','stop',UNITS[2]])
    assert rows()=={'instances':[],'tests':[],'tasks':[]}
    path=f"/api/v1/platform/agents/{scope['agent_id']}/versions/{scope['revision_id']}/test"
    headers={'X-Exact-Test-Admin-Run-Id':scope['run_id'],'Idempotency-Key':str(uuid4())}
    body={'configuration_fingerprint':scope['fingerprint']}
    locks=[];outcome=[];thread=None
    def request():
        try:outcome.append(api(opener,path,body,headers))
        except Exception as exc:outcome.append(type(exc).__name__)
    try:
        if case in 'ABD':
            table={'A':'tenant_agent_instances','B':'agent_execution_contexts','D':'agent_template_tests'}[case]
            hold=lock(table);locks.append(hold)
            thread=threading.Thread(target=request);thread.start()
            wait(lambda:blocked('INSERT INTO '+table),'WRITE_NOT_BLOCKED_'+case)
            assert rows()=={'instances':[],'tests':[],'tasks':[]}
            evidence['uncommitted_initialization_invisible']=True
            kill_api();hold.rollback();thread.join(5)
            assert rows()=={'instances':[],'tests':[],'tasks':[]}
        elif case=='C':
            # Pause Redis writes, not PG: reservation commits but enqueue cannot
            # complete and the Manager cannot start. No application hook.
            redis.client_pause(20000,mode='WRITE')
            thread=threading.Thread(target=request);thread.start()
            wait(lambda:len(rows()['tests'])==1,'RESERVATION_NOT_COMMITTED')
            kill_api();redis.client_unpause();thread.join(5)
        elif case in 'FG':
            hold=lock('agent_template_tests');locks.append(hold)
            thread=threading.Thread(target=request);thread.start()
            wait(lambda:blocked('INSERT INTO agent_template_tests'),'TEST_NOT_BLOCKED')
            audit_hold=lock('execution_events');locks.append(audit_hold)
            hold.rollback()
            wait(lambda:len(rows()['tests'])==1,'RESERVATION_NOT_COMMITTED')
            wait(lambda:blocked('LOCK TABLE execution_events'),'ADMISSION_FINISH_NOT_BLOCKED')
            kill_api();audit_hold.rollback();thread.join(5)
        elif case=='E':
            hold=lock('run_traces');locks.append(hold)
            thread=threading.Thread(target=request);thread.start()
            wait(lambda:blocked('INSERT INTO run_traces'),'WORKER_RUN_NOT_STARTED')
            assert rows()['tests'][0]['status']=='running'
            kill_api();hold.rollback();thread.join(5)
            wait(lambda:rows()['tests'][0]['status']=='failed','EARLY_RUNTIME_FAILURE_NOT_TERMINAL')
            evidence['actual_worker_runtime_failed_without_provider']=True
        else:
            first=api(opener,path,body,headers);second=api(opener,path,body,headers)
            assert first[0]==second[0]==202 and first[1]==second[1]
            assert len(rows()['tests'])==len(rows()['tasks'])==len(rows()['instances'])==1
            queue=env['ENTERPRISE_POC_TASK_QUEUE_NAMESPACE']+':tasks:pending'
            assert redis.lrange(queue,0,-1)==[first[1]['task_id']]
            evidence['idempotent_http_replay']=first[1]
            # Confirm no second business row; Redis namespace is inspected by
            # the post-revoke consumer check below as well.
        if thread:assert not thread.is_alive(),'REQUEST_STILL_ALIVE'
        evidence['before_recovery']=rows()
        if case=='G':
            hold=lock('platform_admins');locks.append(hold)
            process=subprocess.Popen(operator+['recover'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                wait(lambda:blocked('LOCK TABLE platform_admins'),'RECOVERY_NOT_AT_TRANSACTION')
                process.kill();process.communicate(timeout=5)
                hold.rollback()
                assert conn.execute('SELECT count(*) AS n FROM platform_admins').fetchone()['n']==1
                evidence['interrupted_recover_kept_original_membership']=True
            finally:
                if process.poll() is None:process.kill();process.communicate(timeout=5)
                hold.rollback()
        evidence['recovery']=recover()
        assert evidence['recovery']['guard']['active_test_platform_admin']==0
        assert rows()==evidence['before_recovery']
        evidence['guard']=native()
        assert evidence['guard']['status']=='PASS'
        count=conn.execute("SELECT count(*) AS n FROM execution_events WHERE event_type='exact_test_admin.lifecycle'").fetchone()['n']
        assert recover()['status']=='PASS'
        assert conn.execute("SELECT count(*) AS n FROM execution_events WHERE event_type='exact_test_admin.lifecycle'").fetchone()['n']==count
        assert rows()==evidence['before_recovery']
        for r in rows()['tasks']:
            assert (r['tenant_id'],r['user_id'],r['agent_id'])==(scope['tenant_id'],scope['principal_id'],scope['agent_id'])
        if rows()['tests'] and case!='E':
            if case in 'FGR':
                wait(lambda:rows()['tasks'][0]['id'] in redis.lrange(
                    env['ENTERPRISE_POC_TASK_QUEUE_NAMESPACE']+':tasks:processing',0,-1),'REVOKED_WORKER_NOT_OBSERVED')
                evidence['actual_redis_processing_held']=True
            assert rows()['tasks'][0]['status']=='queued'
            evidence['revoked_worker_did_not_execute']=True
        assert api(opener,path,body,headers)[0]==403
        assert api(opener,path.removesuffix('/test')+'/publish',{'mode':'production'},headers)[0]==403
        assert conn.execute("SELECT count(*) AS n FROM agent_template_versions WHERE status='published'").fetchone()['n']==0
        command(['systemctl','stop',*UNITS]);conn.close()
        command(['systemctl','restart','enterprise-agent-native-isolated-pg.service'])
        assert json.loads(command(operator+['startup']))['status']=='PASS'
        conn=psycopg.connect(env['ENTERPRISE_POC_DATABASE_URL'],row_factory=dict_row,autocommit=True)
        assert rows()==evidence['before_recovery']
        assert native()['active_test_platform_admin']==0
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        async def mcp_health():
            async with streamablehttp_client('http://127.0.0.1:28101/mcp') as (read,write,_):
                async with ClientSession(read,write) as session:
                    initialized=await session.initialize();listed=await session.list_tools()
                    return {'protocol':initialized.protocolVersion,'tools':len(listed.tools),'tool_calls':0}
        evidence['mcp_health']=asyncio.run(mcp_health())
        evidence['pg_restart_guard_health']='PASS'
        evidence['result']='PASS'
    finally:
        for hold in locks:hold.close()
        if thread:thread.join(5)
        if 'result' not in evidence:
            # Same existing protected operator only; no SQL cleanup or bypass.
            try:evidence['failure_cleanup']=recover()
            except Exception:evidence['failure_cleanup']='BLOCKED_REQUIRES_REVIEW'
        command(['systemctl','stop',*UNITS])
        evidence['final_admin_count']=conn.execute('SELECT count(*) AS n FROM platform_admins').fetchone()['n']
        conn.close();command(['systemctl','stop','enterprise-agent-native-isolated-pg.service','enterprise-agent-native-isolated-redis.service'])
        evidence['final_service_pids']={u:command(['systemctl','show',u,'-p','MainPID','--value']) for u in UNITS+['enterprise-agent-native-isolated-pg.service','enterprise-agent-native-isolated-redis.service']}
        with (ROOT/'logs'/('atomicity-'+case+'.json')).open('x') as stream:json.dump(evidence,stream,indent=2,sort_keys=True)
        print(json.dumps(evidence),flush=True)
    assert evidence['final_admin_count']==0 and set(evidence['final_service_pids'].values())=={'0'}


if __name__=='__main__':main()
