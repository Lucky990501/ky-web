"""Isolated contracts; Runtime doubles here are not the real Worker E2E."""
import asyncio
from dataclasses import replace
import json
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.agent_catalog_api import catalog_router
from app.agent_productization import AgentCatalogError
from app.agent_runtime_test import AgentRuntimeTest
from app.runtime.codex_provider import CodexRuntimeProvider
from test_agent_execution import execution, context
from test_agent_productization import catalog,new_draft,published_skill,version
from app.auth import UserPrincipal


def setup_test(c):
    t,v=new_draft(c)
    c.control.bind_skills(t,v,[published_skill(c)],c.actor)
    c.control.validate(t,v,c.actor)
    tester=AgentRuntimeTest(c.resolver,c.product,c.tasks)
    tester.isolation_guard=lambda:None
    jobs=[];tester.enqueue=jobs.append
    c.control.runtime_tester=tester;c.tasks.runtime_test_lifecycle=tester
    return t,v,tester,jobs


def enqueue(c,t,v,tester):
    previous=c.tasks._agents._runtime
    c.tasks._agents._runtime=CodexRuntimeProvider(None)
    try:return asyncio.run(tester.run(t,v,c.actor,version(c,t)['configuration_fingerprint']))
    finally:c.tasks._agents._runtime=previous


def record(c,id):
    with c.store.connection() as conn:return dict(conn.execute('SELECT * FROM agent_template_tests WHERE id=?',(id,)).fetchone())


def test_async_enqueue_then_worker_pass_no_api_execution(execution):
    c=execution;t,v,tester,jobs=setup_test(c);result=enqueue(c,t,v,tester)
    assert jobs==[result['task_id']] and c.runtime.turns==0
    assert record(c,result['runtime_test_id'])['status']=='queued'
    task=c.product.task_for_worker(result['task_id'])
    tester.started(task);assert record(c,result['runtime_test_id'])['status']=='running'
    asyncio.run(c.tasks.execute(task))
    assert record(c,result['runtime_test_id'])['status']=='passed'
    turns=c.runtime.turns
    asyncio.run(c.tasks.execute(task))
    assert c.runtime.turns==turns
    c.control.publish(t,v,c.actor,'production')


@pytest.mark.parametrize('when',['queued','running','passed'])
def test_fingerprint_change_late_worker_never_revalidates(execution,when):
    c=execution;t,v,tester,jobs=setup_test(c);result=enqueue(c,t,v,tester)
    task=c.product.task_for_worker(result['task_id'])
    if when=='running':tester.started(task)
    if when=='passed':asyncio.run(c.tasks.execute(task))
    c.control.edit_version(t,v,{'persona':'Changed draft'},c.actor)
    assert record(c,result['runtime_test_id'])['status']=='invalidated'
    asyncio.run(c.tasks.execute(task));tester.finished(task)
    assert record(c,result['runtime_test_id'])['status']=='invalidated'
    assert context(c,task)['configuration_fingerprint'] != version(c,t)['configuration_fingerprint']
    c.control.validate(t,v,c.actor)
    with pytest.raises(AgentCatalogError,match='Runtime Test Pending'):c.control.publish(t,v,c.actor,'production')


def test_worker_failed_does_not_unlock_publish(execution,monkeypatch):
    c=execution;t,v,tester,jobs=setup_test(c);result=enqueue(c,t,v,tester)
    async def fail(*args,**kwargs):raise RuntimeError('unit failed runtime')
    monkeypatch.setattr(c.runtime,'run_turn',fail)
    asyncio.run(c.tasks.execute(c.product.task_for_worker(result['task_id'])))
    assert record(c,result['runtime_test_id'])['status']=='failed'
    with pytest.raises(AgentCatalogError):c.control.publish(t,v,c.actor,'production')


@pytest.mark.parametrize('missing',['runtime_completed','required_tool_calls_completed','final_response_received','final_response_persisted'])
def test_missing_evidence_cannot_pass(execution,missing):
    c=execution;t,v,tester,jobs=setup_test(c);result=enqueue(c,t,v,tester)
    c.tasks.runtime_test_lifecycle=None
    task=c.product.task_for_worker(result['task_id']);asyncio.run(c.tasks.execute(task))
    saved=c.product.task_for_worker(task['id'])
    with c.store.connection() as conn:
        row=conn.execute('SELECT payload FROM run_traces WHERE run_id=?',(saved['run_id'],)).fetchone()
        payload=json.loads(row['payload']);payload[missing]=False
        conn.execute('UPDATE run_traces SET payload=? WHERE run_id=?',(json.dumps(payload),saved['run_id']))
    tester.finished(task)
    assert record(c,result['runtime_test_id'])['status']=='failed'


@pytest.mark.parametrize('denied',['disabled','tenant','slug','local-queue','target'])
def test_production_policy_fail_closed(execution,denied):
    c=execution;t,v,tester,jobs=setup_test(c)
    settings=replace(c.resolver.settings,environment='production',task_queue='redis',
        agent_runtime_test_production_enabled=True,agent_runtime_test_allowed_tenant_ids=('tenant-a',),
        agent_runtime_test_allowed_template_slugs=('new-agent',))
    changes={'disabled':{'agent_runtime_test_production_enabled':False},'tenant':{'agent_runtime_test_allowed_tenant_ids':('other',)},
        'slug':{'agent_runtime_test_allowed_template_slugs':('other',)},'local-queue':{'task_queue':'local'},'target':{}}
    c.resolver.settings=replace(settings,**changes[denied])
    if denied=='target':c.resolver.test_tenant_id='tenant-b'
    with pytest.raises(AgentCatalogError):enqueue(c,t,v,tester)
    assert not jobs


def test_explicit_production_policy_allows_only_target_admin(execution):
    c=execution;t,v,tester,jobs=setup_test(c)
    c.resolver.settings=replace(c.resolver.settings,environment='production',task_queue='redis',
        agent_runtime_test_production_enabled=True,agent_runtime_test_allowed_tenant_ids=('tenant-a',),
        agent_runtime_test_allowed_template_slugs=('new-agent',))
    enqueue(c,t,v,tester);assert len(jobs)==1 and c.runtime.turns==0


def test_api_202_and_forged_fields_denied(execution):
    c=execution;t,v,tester,jobs=setup_test(c)
    c.tasks._agents._runtime=CodexRuntimeProvider(None)
    app=FastAPI()
    def admin(cookie):return UserPrincipal(c.actor,'tenant-a','enterprise_admin')
    app.include_router(catalog_router(c.control,admin))
    @app.exception_handler(AgentCatalogError)
    async def err(request,e):return JSONResponse({'detail':str(e)},status_code=e.status_code)
    endpoint=f'/api/v1/platform/agents/{t}/versions/{v}/test'
    with TestClient(app) as client:
        payload={'configuration_fingerprint':version(c,t)['configuration_fingerprint']}
        assert client.post(endpoint,json={**payload,'tenant_id':'tenant-b'}).status_code==422
        result=client.post(endpoint,json=payload)
        assert result.status_code==202 and result.json()['status']=='queued' and jobs


def test_slug_uuid_unknown_reserved_and_card(execution):
    from test_agent_execution import make_ready
    c=execution;t,v=make_ready(c)
    assert c.product.resolve_agent_reference('new-agent')==t
    assert c.product.resolve_agent_reference(t)==t
    for legacy in ('image-agent','copywriting-agent','campaign-agent'):
        assert c.product.resolve_agent_reference(legacy)==legacy
        with pytest.raises(AgentCatalogError):c.control.create_template({'slug':legacy,'name':'Reserved'},c.actor)
    with pytest.raises(LookupError):c.product.resolve_agent_reference('unknown-agent')
    card=next(a for a in c.product.agents('tenant-a') if a['id']==t)
    assert card['slug']=='new-agent' and card['conversation_path']=='/agents/new-agent'
    for reserved in ('image','copywriting','campaign','image-generation',t):
        with pytest.raises(AgentCatalogError):c.control.create_template({'slug':reserved,'name':'Reserved'},c.actor)
    with pytest.raises(AgentCatalogError):c.control.edit_version(t,v,{'slug':'changed-slug'},c.actor)
    for unsafe in ([],{},'../escape','UpperCase','a'*101):
        with pytest.raises(AgentCatalogError):c.control.create_template({'slug':unsafe,'name':'Unsafe'},c.actor)


def test_non_platform_admin_cannot_enqueue(execution):
    c=execution;t,v,tester,jobs=setup_test(c)
    with c.store.connection() as conn:conn.execute('DELETE FROM platform_admins WHERE user_id=?',(c.actor,))
    with pytest.raises(AgentCatalogError,match='platform_admin'):enqueue(c,t,v,tester)
    assert not jobs


def test_interrupted_worker_delivery_fails_without_repeating_runtime(execution):
    c=execution;t,v,tester,jobs=setup_test(c);result=enqueue(c,t,v,tester)
    task=c.product.task_for_worker(result['task_id']);tester.started(task)
    with c.store.connection() as conn:
        conn.execute('UPDATE agent_template_tests SET result_json=? WHERE id=?',
            (json.dumps({'worker_started':True,'worker_pid':-1}),result['runtime_test_id']))
    asyncio.run(c.tasks.execute(task))
    assert record(c,result['runtime_test_id'])['status']=='failed'
    assert c.product.task_for_worker(task['id'])['error_code']=='worker_interrupted'
    assert c.runtime.turns==0
    with pytest.raises(AgentCatalogError):c.control.publish(t,v,c.actor,'production')
