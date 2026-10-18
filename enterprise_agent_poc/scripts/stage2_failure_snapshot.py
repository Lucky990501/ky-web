"""Bounded, read-only diagnostics for the existing disposable Stage2 fixture."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import time
from uuid import uuid4

STATUSES = {'queued','pending','running','passed','failed','invalidated','completed','cancelled',
            'in_progress','incomplete','unknown','success'}
STAGES = {'loading_context','starting_runtime','runtime','runtime_start','runtime_terminal',
          'required_tools','final_response','failed','queued','worker_started','worker_interrupted',
          'enqueue_failed','persisting_result','result_persistence_failed','trace_lookup','cancelled',
          'stage2_startup','runtime_validation','pytest_failed','context_loading','completed','persisting'}
CODES = {'runtime_error','runtime_start_error','runtime_terminal_error','required_tool_dependency_error',
         'empty_final_response','result_persistence_error','worker_interrupted','enqueue_failed',
         'permission_denied','test_text_gate_identity','test_text_gate_task_required',
         'stage2_runtime_test_only','stage2_budget_stopped','stage2_budget_stopped_or_missing',
         'stage2_inflight_or_unknown','stage2_real_owner_decision_required','stage2_exact_fee_profile',
         'stage2_tool_outcome_not_proven_success','test_text_gate_task_context',
         'test_text_gate_task_missing','stage2_exact_probe_identity_and_input'}
BOOLS = ('worker_started','worker_interrupted','runtime_completed','required_tool_calls_completed',
         'final_response_received','final_response_persisted','artifact_completed','skill_discovery_passed')
EXCEPTIONS = {'RuntimeError','RuntimeStartError','TestTextGateDenied','AgentRunError','PermissionError',
              'TypeError','ValueError','TimeoutError','IsolationError','CalledProcessError','OtherRuntimeError'}

def safe_worker_events(text):
    events=[]
    for line in text.splitlines()[-200:]:
        match=re.search(r'STAGE2_FAILURE type=([A-Za-z]+) stage=([a-z_]+) correlation=([a-f0-9-]+)',line)
        if match and match[1] in EXCEPTIONS and match[2] in STAGES:
            event={'exception_type':match[1],'stage':match[2],'correlation':identity(match[3])}
            origin=re.search(r' origin=([A-Za-z_][A-Za-z0-9_]*\.py:[A-Za-z_][A-Za-z0-9_]*:[0-9]{1,6})(?:\s|$)',line)
            if origin:event['code_origin']=origin[1]
            events.append(event)
        elif re.match(r'^([A-Za-z]+):',line):
            name=line.split(':',1)[0]
            if name in EXCEPTIONS:events.append({'exception_type':name,'stage':'stage2_startup','correlation':None})
    return events[-12:]

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def label(value, allowed):
    return value if isinstance(value,str) and value in allowed else None

def identity(value):
    return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',value) else None

def error_category(value):
    # Emit only a fixed known category, never an exception message or its suffix.
    if not isinstance(value,str):return None
    return next((code for code in sorted(CODES) if re.search(r'(?<![a-z_])'+re.escape(code)+r'(?![a-z_])',value)),None)

def decoded(raw):
    if isinstance(raw,dict):return raw
    try:return json.loads(raw or '{}')
    except (TypeError,ValueError):return {}

def safe_payload(raw):
    p=decoded(raw)
    if not isinstance(p,dict):return {}
    result={key:p[key] for key in BOOLS if type(p.get(key)) is bool}
    result.update({key:label(p.get(key),allowed) for key,allowed in
                   [('status',STATUSES),('runtime_status',STATUSES),('failure_stage',STAGES),('error_code',CODES)]})
    result['safe_error_category']=error_category(p.get('error'))
    if type(p.get('worker_pid')) is int and p['worker_pid']>0:result['worker_pid']=p['worker_pid']
    return result

def secure_directory(path):
    path=Path(path)
    if not path.is_absolute() or path.resolve(strict=True)!=path:raise ValueError('snapshot_noncanonical_directory')
    s=path.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o700:
        raise ValueError('snapshot_directory_must_be_owned_0700')
    for parent in path.parents:
        s=parent.lstat()
        if not stat.S_ISDIR(s.st_mode) or (s.st_mode&0o022 and not s.st_mode&stat.S_ISVTX):
            raise ValueError('snapshot_untrusted_parent')
    return path

def durable_json(directory,name,value):
    directory=secure_directory(directory)
    if not re.fullmatch(r'[A-Za-z0-9._-]+\.json',name):raise ValueError('snapshot_filename')
    raw=(json.dumps(value,ensure_ascii=True,sort_keys=True,indent=2)+'\n').encode()
    if len(raw)>262144:raise ValueError('snapshot_size_bound')
    target=directory/name
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    with os.fdopen(os.open(target,os.O_RDONLY|os.O_NOFOLLOW),'rb') as f:
        s=os.fstat(f.fileno())
        if s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o600 or s.st_nlink!=1 or f.read()!=raw:
            raise ValueError('snapshot_readback')
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    return {'path':str(target),'sha256':sha(raw),'bytes':len(raw),'mode':'0600'}

def capture(config_path,destination,*,test_id=None,processes=(),phase='runtime_validation',exception=None,ledger=None):
    from scripts.stage2_isolation import bootstrap,assert_worker_isolated
    config,settings=bootstrap(config_path)
    root=Path(config['root']);destination=secure_directory(destination)
    if destination.is_relative_to(root):raise ValueError('snapshot_must_survive_private_root_cleanup')
    assert_worker_isolated(settings,root,config['snapshot'])
    from app.store import POCStore
    project=Path(__file__).resolve().parents[1]
    source=subprocess.check_output(['git','-C',str(project),'show','-s','--format=%H%n%T','HEAD'],text=True).splitlines()
    out={'kind':'STAGE2_PRIVATE_FAILURE_SNAPSHOT_V1','observed_at':int(time.time()),
         'source_commit':source[0],'source_tree':source[1],
         'source_dirty':bool(subprocess.check_output(['git','-C',str(project),'status','--porcelain'],text=True).strip()),
         'snapshot_helper_sha256':sha(Path(__file__).read_bytes()),'config_sha256':sha(Path(config_path).read_bytes()),
         'private_runtime_id':root.name,'phase':label(phase,STAGES),'tests':[],'worker':[],
         'exception_type':label(type(exception).__name__,EXCEPTIONS) if exception else None,
         'exception_category':error_category(str(exception)) if exception else None,
         'admission':{'status':'NOT_CONFIGURED_NO_AUTHORITY_READ','actual_http_calls':0}}
    # This store is the marked synthetic PG already checked above. No app
    # import/initialization, DML, free-form payload or customer DB is permitted.
    with POCStore(settings.database_url).connection() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        sql="SELECT id,status,task_id,agent_template_version_id,configuration_fingerprint,result_json,created_at FROM agent_template_tests WHERE test_type='runtime'"
        rows=conn.execute(sql+(' AND id=?' if test_id else " AND status IN ('failed','invalidated','queued','running')")+' ORDER BY created_at DESC LIMIT 4',((test_id,) if test_id else ())).fetchall()
        for row in rows:
            r=dict(row);raw=r.pop('result_json');item={k:identity(r[k]) for k in ('id','task_id','agent_template_version_id','configuration_fingerprint')}
            item.update(status=label(r['status'],STATUSES),created_at=str(r['created_at']),result=safe_payload(raw),result_json_sha256=sha(str(raw).encode()))
            task=conn.execute('SELECT id,status,stage,error_code,run_id,created_at,started_at,completed_at FROM tasks WHERE id=?',(r['task_id'],)).fetchone() if r['task_id'] else None
            if task:
                t=dict(task);item['task']={k:identity(t[k]) for k in ('id','run_id')}
                item['task'].update(status=label(t['status'],STATUSES),stage=label(t['stage'],STAGES),error_code=label(t['error_code'],CODES),**{k:str(t[k]) if t[k] else None for k in ('created_at','started_at','completed_at')})
                trace=conn.execute('SELECT run_id,status,payload FROM run_traces WHERE run_id=?',(t['run_id'],)).fetchone() if t['run_id'] else None
                if trace:item['trace']={'run_id':identity(trace['run_id']),'status':label(trace['status'],STATUSES),'safe':safe_payload(trace['payload']),'payload_sha256':sha(str(trace['payload']).encode())}
            out['tests'].append(item)
    worker_log=root/'worker.log'
    out['worker_failure_events']=safe_worker_events(worker_log.read_text(errors='replace')[-65536:]) if worker_log.is_file() else []
    for name,process in processes:
        p=Path('/proc')/str(process.pid);record={'role':name if name in {'worker','api','mcp','redis'} else 'other','pid':process.pid,'exit_code':process.poll()}
        if p.exists():
            try:record.update(ipc=os.readlink(p/'ns/ipc'),mount=os.readlink(p/'ns/mnt'),shm_device=(p/'root/dev/shm').stat().st_dev)
            except OSError:record['proc_metadata_unavailable']=True
        out['worker'].append(record)
    if ledger and Path(ledger).is_file():
        with sqlite3.connect('file:'+str(ledger)+'?mode=ro',uri=True) as c:
            attempts=c.execute('SELECT state,input_actual,output_actual FROM attempts').fetchall()
            stopped=c.execute('SELECT stopped FROM control').fetchone()
            out['admission']={'status':'READ_ONLY_LEDGER_SNAPSHOT','actual_http_calls':len(attempts),
                'input_tokens':sum(r[1] or 0 for r in attempts),'output_tokens':sum(r[2] or 0 for r in attempts),
                'attempt_states':[label(r[0],{'INFLIGHT','COMPLETE'}) for r in attempts],
                'stopped':stopped[0] if stopped and stopped[0] in {None,'TRANSPORT_FAILED_OR_CANCELLED'} else 'OTHER_STOP',
                'outbound_denials':c.execute('SELECT count(*) FROM denials').fetchone()[0],
                'denial_categories':[label(r[0],CODES) for r in c.execute('SELECT reason FROM denials ORDER BY id DESC LIMIT 4')],
                'ledger_sha256':sha(Path(ledger).read_bytes())}
    receipt=durable_json(destination,'failure-'+uuid4().hex+'.json',out)
    return receipt

def try_capture(*args,**kwargs):
    """Never mask the original failure; persist a bounded capture-error receipt."""
    try:return capture(*args,**kwargs)
    except Exception as exc:
        try:return durable_json(args[1],'capture-error-'+uuid4().hex+'.json',{
            'kind':'STAGE2_FAILURE_CAPTURE_ERROR','exception_type':type(exc).__name__,
            'exception_category':error_category(str(exc)),'original_failure_preserved':True})
        except Exception:return None
