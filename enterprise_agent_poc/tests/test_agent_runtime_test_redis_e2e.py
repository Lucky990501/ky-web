"""Opt-in real private-socket PG/Redis/API/Worker/Runtime integration.

The child process passes the same initialization gate as every service. It
never reads a credential: only the isolated Worker holds the model test key.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def probe(config_path):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from scripts.stage2_isolation import bootstrap
    config,settings=bootstrap(config_path)
    from app.store import POCStore
    from app.task_queue import RedisTaskQueue
    import httpx
    store=POCStore(settings.database_url)
    queue=RedisTaskQueue.from_settings(settings)
    evidence_path=Path(config['root'])/'evidence/redis-e2e.json'
    with httpx.Client(base_url=f"http://127.0.0.1:{config['api_port']}",timeout=10) as api:
        assert api.post('/api/v1/auth/login',json={'account':'runtime@stage2.test','password':'Stage2Local!2026'}).status_code==200
        templates=api.get('/api/v1/platform/agents').json()
        template=next(t for t in templates if t['slug']=='social-content-agent')
        tid=template['id']
        if evidence_path.exists():
            evidence=json.loads(evidence_path.read_text())
        else:
            detail=api.get(f'/api/v1/platform/agents/{tid}').json()
            source=next(v for v in detail['versions'] if v['status']=='published')
            assert api.post(f'/api/v1/platform/agents/{tid}/instances/tenant-a/disable').status_code==200
            result=api.post(f'/api/v1/platform/agents/{tid}/versions',json={'from_version_id':source['id']})
            assert result.status_code==201
            draft=max(result.json()['versions'],key=lambda v:v['revision'])
            endpoint=f"/api/v1/platform/agents/{tid}/versions/{draft['id']}"
            assert api.post(endpoint+'/validation').status_code==200
            started=time.monotonic()
            response=api.post(endpoint+'/test',json={'configuration_fingerprint':draft['configuration_fingerprint']})
            assert response.status_code==202
            evidence=response.json()
            assert evidence['status']=='queued' and time.monotonic()-started<10
            observed=['queued']
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                with store.connection() as conn:
                    test=dict(conn.execute('SELECT * FROM agent_template_tests WHERE id=?',(evidence['runtime_test_id'],)).fetchone())
                observed.append(test['status'])
                if test['status'] in {'passed','failed','invalidated'}:break
                time.sleep(.25)
            assert test['status']=='passed',test['status']
            assert 'running' in observed
            evidence.update(http_status=202,observed_states=list(dict.fromkeys(observed)))
        with store.connection() as conn:
            test=dict(conn.execute('SELECT * FROM agent_template_tests WHERE id=?',(evidence['runtime_test_id'],)).fetchone())
            task=dict(conn.execute('SELECT * FROM tasks WHERE id=?',(test['task_id'],)).fetchone())
            trace=dict(conn.execute('SELECT * FROM run_traces WHERE run_id=?',(task['run_id'],)).fetchone())
            result=json.loads(test['result_json']);payload=json.loads(trace['payload'])
            assert test['status']=='passed' and task['status']==trace['status']=='completed'
            assert result['worker_pid']!=int(os.environ['STAGE25_API_PID'])
            assert all(payload.get(k) is True for k in ('runtime_completed','required_tool_calls_completed','final_response_received','final_response_persisted'))
            before=[dict(r) for r in conn.execute('SELECT id,task_id,amount FROM credit_transactions WHERE task_id=?',(task['id'],))]
            assert len(before)==1 and before[0]['amount']==-3
            assert len(conn.execute("SELECT id FROM agent_templates WHERE definition_source='legacy' AND id IN ('image-agent','copywriting-agent','campaign-agent')").fetchall())==3
            assert len(conn.execute('SELECT * FROM schema_migrations').fetchall())==10
            public_runs=[dict(r) for r in conn.execute("SELECT t.id,t.status,t.conversation_id,m.context_id,c.agent_template_version_id,c.configuration_fingerprint,c.credit_cost FROM tasks t JOIN task_agent_contexts m ON m.task_id=t.id JOIN agent_execution_contexts c ON c.id=m.context_id WHERE t.agent_id=? AND t.id NOT IN (SELECT task_id FROM agent_template_tests WHERE task_id IS NOT NULL)",(tid,))]
            assert len(public_runs)==2 and all(t['status']=='completed' for t in public_runs)
            assert len({t['conversation_id'] for t in public_runs})==len({t['context_id'] for t in public_runs})==1
            assert all(t['credit_cost']==3 for t in public_runs)
            assert conn.execute("SELECT count(*) AS n FROM tenant_agent_instances WHERE tenant_id='tenant-b' AND agent_id=?",(tid,)).fetchone()['n']==0
            evidence['public_conversation_id']=public_runs[0]['conversation_id']
            evidence['resume_context_id']=public_runs[0]['context_id']
            evidence['resume_revision_id']=public_runs[0]['agent_template_version_id']
            evidence['skill_counts']={table:conn.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'] for table in ('skills','skill_versions','skill_packages','agent_skill_bindings')}
        queue.enqueue(task['id']);queue.enqueue(task['id'])
        deadline=time.monotonic()+15
        while time.monotonic()<deadline and (queue._client.llen(queue.pending) or queue._client.llen(queue.processing)):time.sleep(.1)
        assert queue._client.llen(queue.pending)==queue._client.llen(queue.processing)==0
        with store.connection() as conn:
            assert before==[dict(r) for r in conn.execute('SELECT id,task_id,amount FROM credit_transactions WHERE task_id=?',(task['id'],))]
            assert conn.execute('SELECT count(*) AS n FROM agent_template_tests WHERE task_id=?',(task['id'],)).fetchone()['n']==1
            assert conn.execute('SELECT status FROM agent_template_tests WHERE id=?',(test['id'],)).fetchone()['status']=='passed'
        evidence.update(worker_pid=result['worker_pid'],api_pid=int(os.environ['STAGE25_API_PID']),duplicate_delivery='passed',runtime_test_status='passed',task_status='completed',trace_status='completed')
        evidence_path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
        print(json.dumps(evidence,ensure_ascii=False))


def test_real_private_redis_worker_runtime_e2e():
    config=os.environ.get('STAGE25_REDIS_E2E_CONFIG')
    if not config:pytest.skip('Explicit isolated service manifest required; unit doubles are not E2E')
    env={k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG','PYTHONDONTWRITEBYTECODE','STAGE25_API_PID') if k in os.environ}
    result=subprocess.run([sys.executable,__file__,'--probe',config],env=env,capture_output=True,text=True,timeout=150)
    assert result.returncode==0,result.stdout+result.stderr


if __name__=='__main__':
    assert sys.argv[1]=='--probe'
    probe(sys.argv[2])
