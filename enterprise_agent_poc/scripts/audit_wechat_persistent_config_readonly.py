"""PRIMARY metadata only: no passwords/keys/Secret backend/API/restart/mutation.

Operator runs with the existing Test Python. PG attempt uses NO credential file
or password and a fixed target; if local trust is unavailable, STOP. stdout has
only schema, keys, flags, public audit identity/version/status and fingerprints.
Never dump request bodies, AppID/name, password hashes, ciphertext or tokens.
"""
import hashlib
import argparse
import json
import os
from pathlib import Path
import subprocess

P=Path('/etc/enterprise-agent-test-successor-wechat-d8a-seeding-approval-v1')
R=Path('/opt/enterprise-agent-workbench-test')
TENANT='internal-test-staging-v1'  # Exact approved existing fixture, not an input.
SOURCE='d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7'
TREE='6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5'

def audit():
    from psycopg import connect
    from psycopg.rows import dict_row
    out={'scope':'READ_ONLY_NO_CREDENTIAL_ACCESS','primary_restarts':0,'primary_mutations':0,'production':'UNCHANGED'}
    path=(R/'current').resolve()
    identity=subprocess.run(['git','-c','safe.directory='+str(path),'-C',str(path),'rev-parse','HEAD','HEAD^{tree}'],
        capture_output=True,text=True,timeout=10,check=True).stdout.splitlines()
    if identity!=[SOURCE,TREE]:raise RuntimeError('READONLY_SOURCE_IDENTITY_BLOCKED')
    out['application_source'],out['application_tree']=identity
    out['services']={}
    for role in ('api','mcp','worker','postgresql','redis'):
        raw=subprocess.run(['systemctl','show','enterprise-agent-test-'+role+'.service','-p','ActiveState','-p','MainPID','-p','WorkingDirectory'],capture_output=True,text=True,timeout=5,check=True).stdout
        out['services'][role]=dict(line.split('=',1) for line in raw.splitlines())
    pins=json.loads((P/'predecessor-live-protected.v1.json').read_bytes())['row_hashes']
    out['protected_tables']={name:len(rows) for name,rows in pins.items()}
    out['old_common_sha256']=hashlib.sha256((P/'common.py').read_bytes()).hexdigest()
    # Never fall back to .pgpass, process env credentials or an original_env().
    for name in list(os.environ):
        if name.startswith('PG'):os.environ.pop(name)
    os.environ['PGPASSFILE']='/nonexistent-readonly-audit-pgpass'
    with connect(host='127.0.0.1',port=55432,dbname='enterprise_agent_test',user='enterprise_agent_test',
        sslmode='disable',connect_timeout=5,row_factory=dict_row,options='-c default_transaction_read_only=on -c statement_timeout=5000') as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        target=dict(conn.execute('SELECT host(inet_server_addr()) AS address,inet_server_port() AS port,current_database() AS database,current_user AS db_role').fetchone())
        if target!=dict(address='127.0.0.1',port=55432,database='enterprise_agent_test',db_role='enterprise_agent_test'):raise RuntimeError('READONLY_TARGET_BLOCKED')
        out['schema']=[dict(r) for r in conn.execute("SELECT column_name,ordinal_position,udt_name,datetime_precision,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name='enterprise_configs' ORDER BY ordinal_position")]
        out['triggers']=[dict(r) for r in conn.execute("SELECT trigger_name,event_manipulation,action_timing FROM information_schema.triggers WHERE event_object_schema='public' AND event_object_table='enterprise_configs'")]
        # Match the native to_jsonb snapshot representation without revealing it.
        rows=[dict(r)['row'] for r in conn.execute('SELECT to_jsonb(t) AS row FROM enterprise_configs t')]
        def old_hash(row):return hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':'),ensure_ascii=False,default=str).encode()).hexdigest()
        out['old_enterprise_config_pin_match']=sorted(old_hash(r) for r in rows)==pins.get('enterprise_configs')
        selected=[r for r in rows if r['tenant_id']==TENANT]
        if len(selected)!=1:raise RuntimeError('READONLY_TENANT_BLOCKED')
        row=selected[0];payload=json.loads(row['payload']);account=payload.get('wechat_account') or {};ref=account.get('wechat_app_secret_ref') or {}
        out['config']={'account_present':bool(account),'account_keys':sorted(account),'secret_reference_keys':sorted(ref),
            'secret_reference_version':ref.get('version'),'reference_tenant_match':ref.get('tenant_id')==TENANT,
            'reference_environment':ref.get('environment'),'reference_provider':ref.get('provider'),'updated_at':row['updated_at']}
        audits=[]
        for r in conn.execute("SELECT id,event_type,payload,created_at FROM execution_events WHERE event_type IN ('tenant_secret.provision','tenant_secret.rotate','tenant_secret.revoke','wechat.account.verification') ORDER BY id"):
            e=json.loads(r['payload'])
            if e.get('tenant_id')!=TENANT:continue
            audits.append({'id':r['id'],'event_type':r['event_type'],'created_at':str(r['created_at']),
                'fields':sorted(e),'actor_id':e.get('actor_id'),'environment':e.get('environment'),
                'version':e.get('version'),'status':e.get('status'),'error_code':e.get('error_code'),
                'config_hash_bound':any(k in e for k in ('before_config_sha256','after_config_sha256','config_row_sha256'))})
        out['audits']=audits
        out['actor_identities']=[dict(r) for r in conn.execute("SELECT id,tenant_id,role,account_status FROM users WHERE tenant_id=%s AND role='enterprise_admin'",(TENANT,))]
        out['persistent_guard_observation_installed']=(Path('/etc/enterprise-agent-test-wechat-persistent-config-v1/approval.v1.json')).exists()
    out['old_d8a_native_restart_qualified']='NO' if not out['old_enterprise_config_pin_match'] else 'REQUIRES_FULL_NATIVE_READONLY_CHECK'
    out['wechat_calls']=out['provider_calls']=out['image_calls']=0
    return out

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-06-readonly',action='store_true',help='06 must first approve exact audit code/scope; never a self-grant')
    if not parser.parse_args().approved_06_readonly:
        print(json.dumps({'status':'PENDING_06_READONLY_SNAPSHOT','executed':False}));raise SystemExit(2)
    try:print(json.dumps(audit(),sort_keys=True,ensure_ascii=False))
    except Exception:print(json.dumps({'status':'READONLY_AUDIT_BLOCKED_NO_CREDENTIAL_FALLBACK','mutations':0,'restarts':0}));raise SystemExit(2)
