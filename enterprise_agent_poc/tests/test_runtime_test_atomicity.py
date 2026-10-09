"""Repository transaction contracts; native PG fault rehearsal is separate."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from uuid import uuid4

import pytest
from app.agent_productization import AgentCatalogError
from app.runtime.codex_provider import CodexRuntimeProvider
from test_agent_execution import execution
from test_agent_productization import catalog, version
from test_agent_runtime_test_lifecycle import setup_test


def prepare(c):
    t,v,tester,jobs=setup_test(c)
    c.tasks._agents._runtime=CodexRuntimeProvider(None)
    return t,v,tester,jobs


def invoke(c,t,v,tester,key):
    return asyncio.run(tester.run(t,v,c.actor,version(c,t)['configuration_fingerprint'],idempotency_key=key))


def rows(c,t):
    with c.store.connection() as conn:
        return {name:[dict(r) for r in conn.execute(query,(t,))] for name,query in {
            'instances':'SELECT * FROM tenant_agent_instances WHERE agent_id=?',
            'tasks':'SELECT * FROM tasks WHERE agent_id=?',
            'contexts':'SELECT * FROM agent_execution_contexts WHERE agent_id=?',
            'tests':'SELECT x.* FROM agent_template_tests x JOIN agent_template_versions v ON v.id=x.agent_template_version_id WHERE v.agent_template_id=? AND x.test_type=\'runtime\'',
        }.items()}


@pytest.mark.parametrize('sql_prefix',[
    'INSERT INTO tenant_agent_instances','INSERT INTO agent_execution_contexts',
    'INSERT INTO tasks','INSERT INTO task_events','INSERT INTO task_agent_contexts',
    'INSERT INTO agent_template_tests'])
def test_any_initialization_exception_rolls_back_every_record(execution,monkeypatch,sql_prefix):
    c=execution;t,v,tester,jobs=prepare(c);key=str(uuid4())
    real=c.store.connection
    class Failing:
        def __init__(self,conn):self.conn=conn
        def execute(self,sql,params=()):
            if sql.startswith(sql_prefix):raise RuntimeError('injected initialization failure')
            return self.conn.execute(sql,params)
    @contextmanager
    def connection():
        with real() as conn:yield Failing(conn)
    with monkeypatch.context() as patch:
        patch.setattr(c.store,'connection',connection)
        with pytest.raises(RuntimeError,match='injected'):invoke(c,t,v,tester,key)
    assert all(not r for r in rows(c,t).values()) and not jobs
    first=invoke(c,t,v,tester,key)
    assert all(len(r)==1 for r in rows(c,t).values())
    assert invoke(c,t,v,tester,key)==first and jobs==[first['task_id']]


def test_enqueue_observes_committed_initialization(execution):
    c=execution;t,v,tester,_=prepare(c)
    def enqueue(task):
        state=rows(c,t)
        assert all(len(r)==1 for r in state.values())
        assert state['tasks'][0]['id']==state['tests'][0]['task_id']==task
        assert state['instances'][0]['status']=='configured'
    tester.enqueue=enqueue
    invoke(c,t,v,tester,str(uuid4()))


def test_concurrent_same_key_has_one_reservation(execution):
    c=execution;t,v,tester,jobs=prepare(c);key=str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:invoke(c,t,v,tester,key),range(2)))
    assert results[0]==results[1]
    assert all(len(r)==1 for r in rows(c,t).values())
    assert jobs==[results[0]['task_id']]


def test_enqueue_failure_replay_does_not_execute_again(execution):
    c=execution;t,v,tester,_=prepare(c);key=str(uuid4());calls=[]
    def fail(task):calls.append(task);raise RuntimeError('queue unavailable')
    tester.enqueue=fail
    with pytest.raises(AgentCatalogError,match='enqueue failed'):invoke(c,t,v,tester,key)
    result=invoke(c,t,v,tester,key)
    assert result['status']=='failed' and calls==[result['task_id']]
    assert all(len(r)==1 for r in rows(c,t).values())


@pytest.mark.parametrize('key',['bad',str(uuid4()).upper(),''])
def test_invalid_key_rejected_without_initialization(execution,key):
    c=execution;t,v,tester,jobs=prepare(c)
    with pytest.raises(AgentCatalogError,match='Idempotency-Key'):invoke(c,t,v,tester,key)
    assert all(not r for r in rows(c,t).values()) and not jobs


def test_existing_other_tenant_instances_unchanged(execution):
    c=execution;t,v,tester,_=prepare(c)
    with c.store.connection() as conn:before=[dict(r) for r in conn.execute('SELECT * FROM tenant_agent_instances ORDER BY tenant_id,agent_id')]
    invoke(c,t,v,tester,str(uuid4()))
    with c.store.connection() as conn:after=[dict(r) for r in conn.execute('SELECT * FROM tenant_agent_instances WHERE agent_id<>? ORDER BY tenant_id,agent_id',(t,))]
    assert after==before


def test_fingerprint_reuse_does_not_silently_create_new_test(execution):
    c=execution;t,v,tester,_=prepare(c);key=str(uuid4())
    invoke(c,t,v,tester,key)
    c.control.edit_version(t,v,{'persona':'Changed revision'},c.actor)
    with pytest.raises(AgentCatalogError,match='idempotency identity'):invoke(c,t,v,tester,key)
    assert all(len(r)==1 for r in rows(c,t).values())


def test_borrowed_connection_is_runtime_test_only(execution):
    c=execution
    with c.store.connection() as conn:
        with pytest.raises(PermissionError,match='Atomic Runtime Test'):
            c.product.create_task('tenant-a',c.actor,'copywriting-agent','x',None,_test_connection=conn)
