"""Stage 2 isolated contract tests. Runtime double evidence is NOT real E2E."""
import asyncio
from dataclasses import replace
import json
import sqlite3
import time
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_catalog import CATALOG
from app.agent_execution import ExecutionResolver,profile
from app.agent_productization import AgentCatalogError,canonical
from app.auth import UserPrincipal
from app.domain import RuntimeProfile,RuntimeSession,RuntimeTurn
from app.platform_mcp.service import PlatformMCPService
from app.product_service import TaskService
from app.product_store import ResultPersistenceError
from app.security import RuntimePrincipal,RuntimeTokenIssuer,TokenError
from app.service import AgentService
from app.settings import Settings
from test_agent_productization import catalog,new_draft,published_skill,version
from test_skill_registry import skill_zip


class ContextRuntime:
    def __init__(self):
        self.profiles={}; self.instructions=[]; self.turns=0
    async def create_session(self,p,instructions):
        thread=str(uuid4()); self.profiles[thread]=p; self.instructions.append(instructions)
        return RuntimeSession(thread,p.id)
    async def resume_session(self,p,thread_id,**kwargs):
        assert self.profiles[thread_id].id == p.id
        return RuntimeSession(thread_id,p.id)
    def startup_events(self,p):
        return tuple({'event':'skill_discovered','skill':s} for s in p.skill_manifest)
    async def run_turn(self,session,message):
        self.turns+=1
        return RuntimeTurn(session.thread_id,'隔离模拟最终文案')
    async def close(self): pass


def attach(c,tmp_path):
    settings=replace(Settings.from_env(),data_dir=tmp_path,database_url=c.store.database_url,database_path=c.store.database_path,object_storage_dir=tmp_path/'objects')
    r=ExecutionResolver(c.store,c.registry,c.control,settings,'tenant-a')
    c.control.execution_resolver=r; c.product.execution_resolver=r
    r.initialize_local()
    c.resolver=r; c.runtime=ContextRuntime()
    c.tasks=TaskService(c.product,AgentService(c.store,c.runtime,settings,c.registry.manifest_for_agent))
    return c


@pytest.fixture
def execution(catalog,tmp_path):
    return attach(catalog,tmp_path)


def make_ready(c,**fields):
    t,v=new_draft(c,credit_cost=3,**fields)
    c.control.bind_skills(t,v,[published_skill(c)],c.actor)
    c.control.validate(t,v,c.actor)
    with c.store.connection() as conn:
        conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id,overrides_json) VALUES ('tenant-a',?,'configured',?,?,'{}')",(t,str(uuid4()),v))
    evidence(c,t,v)
    c.control.publish(t,v,c.actor,'production')
    c.control.configure_instance(t,'tenant-a',v,{})
    c.control.set_instance_status(t,'tenant-a','enabled')
    return t,v


def evidence(c,t,v):
    test_id=str(uuid4())
    task=c.product.create_task('tenant-a',c.actor,t,'test',None,_test_revision=v,_test_id=test_id)
    asyncio.run(c.tasks.execute(task))
    saved=c.product.task_for_worker(task['id'])
    assert saved['status']=='completed'
    with c.store.connection() as conn:
        conn.execute("UPDATE agent_template_tests SET status='passed',result_json=? WHERE id=?",(canonical({'execution_chain':'codex-runtime-persisted','skill_discovery_passed':True,'fixture':'unit-runtime-double-only'}),test_id))
    return task


def context(c,task):
    with c.store.connection() as conn:return c.resolver.task_context(conn,task)


def token(c,ctx,**changes):
    policy=json.loads(ctx['tool_policy_snapshot'])
    principal=RuntimePrincipal(ctx['tenant_id'],ctx['agent_id'],ctx['runtime_profile_id'],tuple(policy['scopes']),int(time.time())+60,ctx['id'],ctx['instance_id'])
    issuer=RuntimeTokenIssuer(c.resolver.settings.token_secret)
    return issuer.issue(replace(principal,**changes))


def bind(c,t,v,tools):
    c.control.bind_tools(t,v,[{'tool_capability_id':tool,'invocation_requirement':'optional'} for tool in tools],c.actor)


def test_productized_outside_catalog_runs_and_persists(execution):
    c=execution;t,v=make_ready(c)
    task=c.product.create_task('tenant-a',c.actor,t,'create',None)
    ctx=context(c,task)
    assert t not in CATALOG and ctx['profile_hash_version']=='v2'
    asyncio.run(c.tasks.execute(task));saved=c.product.task_for_worker(task['id'])
    assert saved['status']=='completed'
    detail=c.product.conversation_detail('tenant-a',c.actor,saved['conversation_id'])
    assert detail['messages'][-1]['content']=='隔离模拟最终文案'
    assert c.store.run_trace(saved['run_id'],'tenant-a')['payload']['final_response_persisted']


def test_productized_history_uses_context_display_after_disable(execution):
    c=execution;t,v=make_ready(c,name='固定名称')
    task=c.product.create_task('tenant-a',c.actor,t,'first',None)
    asyncio.run(c.tasks.execute(task))
    saved=c.product.task_for_worker(task['id'])
    c.control.create_version(t,{'from_version_id':v},c.actor)
    c.control.set_instance_status(t,'tenant-a','disabled')
    detail=c.product.conversation_detail('tenant-a',c.actor,saved['conversation_id'])
    history=next(row for row in c.product.conversations('tenant-a',c.actor) if row['id']==saved['conversation_id'])
    for item in (detail,history):
        assert item['agent']['name']=='固定名称'
        assert item['project']['type']=='固定名称项目'
    assert c.product._history_agent(saved['conversation_id'],'tenant-b',t)['name']=='历史智能体'


@pytest.mark.parametrize('agent_id',list(CATALOG))
def test_legacy_resolver_profile_identity_unchanged(execution,agent_id):
    c=execution
    with c.store.connection() as conn:assert c.resolver.resolve(conn,'tenant-a',c.actor,agent_id) is None
    actual=c.tasks._agents.profile_for('tenant-a',agent_id)
    s=c.resolver.settings
    expected=RuntimeProfile.build(tenant_id='tenant-a',agent_id=agent_id,model_provider_id=s.model_provider_id,model_id=s.model_id,reasoning_effort=s.reasoning_effort,skill_manifest=c.registry.manifest_for_agent(agent_id))
    assert actual.id==expected.id and actual.profile_hash_version=='v1'


def test_disabled_run_card_and_resume_denied(execution):
    c=execution;t,v=make_ready(c)
    task=c.product.create_task('tenant-a',c.actor,t,'first',None);asyncio.run(c.tasks.execute(task));saved=c.product.task_for_worker(task['id'])
    c.control.set_instance_status(t,'tenant-a','disabled')
    assert t not in {a['id'] for a in c.product.agents('tenant-a')}
    for conversation in (None,saved['conversation_id']):
        with pytest.raises(LookupError):c.product.create_task('tenant-a',c.actor,t,'denied',conversation)
    assert c.product.conversation_detail('tenant-a',c.actor,saved['conversation_id'])


def test_unpublished_and_runtime_pending_rejected(execution):
    c=execution;t,v=new_draft(c)
    with c.store.connection() as conn:
        conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id) VALUES ('tenant-a',?,'enabled',?,?)",(t,str(uuid4()),v))
    with pytest.raises(AgentCatalogError):c.product.create_task('tenant-a',c.actor,t,'no',None)
    c.control.validate(t,v,c.actor);c.control.publish(t,v,c.actor,'local_test')
    with pytest.raises(AgentCatalogError):c.control.set_instance_status(t,'tenant-a','enabled')


def test_enable_failed_prerequisite_persists_blocked_until_explicit_enable(execution):
    c=execution;t,v=make_ready(c)
    original=c.resolver.settings
    c.resolver.settings=replace(original,model_id='not-approved')
    with pytest.raises(AgentCatalogError,match='Instance blocked'):
        c.control.set_instance_status(t,'tenant-a','enabled')
    with c.store.connection() as conn:
        assert conn.execute("SELECT status FROM tenant_agent_instances WHERE tenant_id='tenant-a' AND agent_id=?",(t,)).fetchone()['status']=='blocked'
    assert t not in {item['id'] for item in c.product.agents('tenant-a')}
    with pytest.raises(LookupError):c.product.create_task('tenant-a',c.actor,t,'denied',None)
    c.resolver.settings=original
    assert c.control.set_instance_status(t,'tenant-a','enabled')['status']=='enabled'


def test_required_bound_tool_not_called_cannot_complete_or_charge(execution):
    c=execution;t,v=new_draft(c,credit_cost=3)
    c.control.bind_skills(t,v,[published_skill(c)],c.actor)
    c.control.bind_tools(t,v,[{'tool_capability_id':'config_get','invocation_requirement':'required'}],c.actor)
    with c.store.connection() as conn:
        conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id) VALUES ('tenant-a',?,'configured',?,?)",(t,str(uuid4()),v))
    task=c.product.create_task('tenant-a',c.actor,t,'test required tool',None,_test_revision=v,_test_id=str(uuid4()))
    asyncio.run(c.tasks.execute(task))
    saved=c.product.task_for_worker(task['id'])
    assert saved['status']=='failed'
    trace=c.store.run_trace(saved['run_id'],'tenant-a')
    assert trace['payload']['required_tool_calls_completed'] is False
    assert not trace['payload']['final_response_persisted']
    with c.store.connection() as conn:
        assert conn.execute('SELECT COUNT(*) n FROM credit_transactions WHERE task_id=?',(task['id'],)).fetchone()['n']==0


def test_fingerprint_edit_invalidates_runtime_test(execution):
    c=execution;t,v=new_draft(c)
    c.control.bind_skills(t,v,[published_skill(c)],c.actor)
    with c.store.connection() as conn:conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id) VALUES ('tenant-a',?,'configured',?,?)",(t,str(uuid4()),v))
    evidence(c,t,v)
    c.control.edit_version(t,v,{'persona':'Changed'},c.actor)
    assert all(x['status']=='invalidated' for x in version(c,t)['tests'])
    assert not version(c,t)['production_ready']


def test_no_binding_has_no_default_scopes(execution):
    c=execution;t,v=make_ready(c)
    task=c.product.create_task('tenant-a',c.actor,t,'x',None);ctx=context(c,task)
    assert profile(ctx).tool_scopes==()
    mcp=PlatformMCPService(c.store,RuntimeTokenIssuer(c.resolver.settings.token_secret),c.resolver.settings)
    with pytest.raises(TokenError):mcp.enterprise_config_get(token(c,ctx))
    with pytest.raises(TokenError):mcp._principal(token(c,ctx),'image:generate')


@pytest.mark.parametrize('claim',['tenant_id','agent_id','instance_id','execution_context_id','runtime_profile_id','scopes'])
def test_v2_tool_db_identity_and_scope_cannot_be_forged(execution,claim):
    c=execution;t,v=new_draft(c)
    bind(c,t,v,['config_get']);c.control.bind_skills(t,v,[published_skill(c)],c.actor)
    with c.store.connection() as conn:conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id) VALUES ('tenant-a',?,'configured',?,?)",(t,str(uuid4()),v))
    evidence(c,t,v);c.control.validate(t,v,c.actor);c.control.publish(t,v,c.actor,'production');c.control.set_instance_status(t,'tenant-a','enabled')
    task=c.product.create_task('tenant-a',c.actor,t,'x',None);ctx=context(c,task)
    change=('enterprise_config:read','image:generate') if claim=='scopes' else 'tenant-b' if claim=='tenant_id' else 'wrong'
    mcp=PlatformMCPService(c.store,RuntimeTokenIssuer(c.resolver.settings.token_secret),c.resolver.settings)
    assert mcp.enterprise_config_get(token(c,ctx))['brand_name']
    with pytest.raises(TokenError):mcp.enterprise_config_get(token(c,ctx,**{claim:change}))
    c.control.set_instance_status(t,'tenant-a','disabled')
    with pytest.raises(TokenError):mcp.enterprise_config_get(token(c,ctx))


def test_v2_token_cannot_downgrade_to_v1_and_skip_context_authorization(execution):
    c=execution;t,v=make_ready(c)
    task=c.product.create_task('tenant-a',c.actor,t,'x',None)
    ctx=context(c,task)
    issuer=RuntimeTokenIssuer(c.resolver.settings.token_secret)
    original=token(c,ctx,scopes=('enterprise_config:read',))
    downgraded=original.replace('poc2.','poc1.',1)
    with pytest.raises(TokenError):issuer.verify(downgraded,'enterprise_config:read')
    mcp=PlatformMCPService(c.store,issuer,c.resolver.settings)
    with pytest.raises(TokenError):mcp.enterprise_config_get(downgraded)


def test_cross_tenant_instance_context_conversation_task_denied(execution):
    c=execution;t,v=make_ready(c)
    user_b=c.product.user_by_email('enterprise@example.invalid')['id']
    with pytest.raises(LookupError):c.product.create_task('tenant-b',user_b,t,'no',None)
    task=c.product.create_task('tenant-a',c.actor,t,'first',None);asyncio.run(c.tasks.execute(task));saved=c.product.task_for_worker(task['id'])
    assert not c.product.task(task['id'],'tenant-b',user_b)
    assert not c.product.conversation_detail('tenant-b',user_b,saved['conversation_id'])
    with c.store.connection() as conn:
        with pytest.raises(LookupError):c.resolver.task_context(conn,{**task,'tenant_id':'tenant-b','user_id':user_b})
    c.control.configure_instance(t,'tenant-b',v,{});c.control.set_instance_status(t,'tenant-b','enabled')
    with pytest.raises(LookupError):c.product.create_task('tenant-b',user_b,t,'no',saved['conversation_id'])


def test_task_and_old_conversation_keep_revision_persona_cost_profile(execution):
    c=execution;t,v=make_ready(c)
    first=c.product.create_task('tenant-a',c.actor,t,'first',None);old=context(c,first)
    second_v=c.control.create_version(t,{'from_version_id':v,'credit_cost':9,'persona':'New persona'},c.actor)['versions'][0]['id']
    asyncio.run(c.tasks.execute(first));saved=c.product.task_for_worker(first['id'])
    # Revision upgrade is an explicit configure + enable, never conversation migration.
    with c.store.connection() as conn:conn.execute("UPDATE tenant_agent_instances SET status='configured',agent_template_version_id=? WHERE tenant_id='tenant-a' AND agent_id=?",(second_v,t))
    evidence(c,t,second_v);c.control.validate(t,second_v,c.actor);c.control.publish(t,second_v,c.actor,'production');c.control.set_instance_status(t,'tenant-a','enabled')
    resumed=c.product.create_task('tenant-a',c.actor,t,'second',saved['conversation_id'])
    assert context(c,resumed)==old
    new=c.product.create_task('tenant-a',c.actor,t,'new',None);new_ctx=context(c,new)
    assert new_ctx['credit_cost']==9 and old['credit_cost']==3
    assert new_ctx['runtime_profile_id']!=old['runtime_profile_id']
    with pytest.raises(ValueError):asyncio.run(c.tasks._agents.run('tenant-a',t,'wrong',saved['conversation_id'],execution_context=new_ctx))
    asyncio.run(c.tasks.execute(resumed))
    with c.store.connection() as conn:
        assert conn.execute('SELECT amount FROM credit_transactions WHERE task_id=?',(resumed['id'],)).fetchone()['amount']==-3


def test_task_context_atomic_rollback_no_task_or_snapshot(execution,monkeypatch):
    c=execution;t,v=make_ready(c)
    with c.store.connection() as conn:
        counts=[conn.execute(f'SELECT COUNT(*) AS n FROM {name}').fetchone()['n'] for name in ('tasks','agent_execution_contexts')]
    original=c.resolver.resolve
    def fail(*args,**kwargs):original(*args,**kwargs);raise RuntimeError('synthetic failure after Context insert')
    monkeypatch.setattr(c.resolver,'resolve',fail)
    with pytest.raises(RuntimeError):c.product.create_task('tenant-a',c.actor,t,'rollback',None)
    with c.store.connection() as conn:assert counts==[conn.execute(f'SELECT COUNT(*) AS n FROM {name}').fetchone()['n'] for name in ('tasks','agent_execution_contexts')]


def test_context_immutable_and_unique_mapping(execution):
    c=execution;t,v=make_ready(c);task=c.product.create_task('tenant-a',c.actor,t,'x',None);ctx=context(c,task)
    for sql,params in [('UPDATE agent_execution_contexts SET credit_cost=100 WHERE id=?',(ctx['id'],)),('DELETE FROM agent_execution_contexts WHERE id=?',(ctx['id'],)),('UPDATE task_agent_contexts SET context_id=? WHERE task_id=?',(ctx['id'],task['id']))]:
        with pytest.raises(sqlite3.IntegrityError),c.store.connection() as conn:conn.execute(sql,params)
    with pytest.raises(sqlite3.IntegrityError),c.store.connection() as conn:conn.execute('INSERT INTO task_agent_contexts VALUES (?,?)',(task['id'],ctx['id']))


def test_deprecated_skill_historical_context_only(execution):
    c=execution
    item=c.registry.import_archive('test-social','1.0.0','Test','',skill_zip('test-social'),c.actor)
    c.registry.publish(item['id'],c.actor)
    t,v=new_draft(c)
    c.control.bind_skills(t,v,[{'skill_id':item['skill_id'],'skill_version_id':item['id']}],c.actor)
    with c.store.connection() as conn:conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id) VALUES ('tenant-a',?,'configured',?,?)",(t,str(uuid4()),v))
    evidence(c,t,v);c.control.validate(t,v,c.actor);c.control.publish(t,v,c.actor,'production');c.control.set_instance_status(t,'tenant-a','enabled')
    task=c.product.create_task('tenant-a',c.actor,t,'old',None);asyncio.run(c.tasks.execute(task));saved=c.product.task_for_worker(task['id'])
    c.registry.deprecate(item['id'])
    resumed=c.product.create_task('tenant-a',c.actor,t,'history',saved['conversation_id'])
    assert context(c,resumed)['skill_manifest_snapshot']==context(c,task)['skill_manifest_snapshot']
    with pytest.raises(AgentCatalogError):c.control.create_version(t,{'from_version_id':v},c.actor)
    with pytest.raises(AgentCatalogError):c.product.create_task('tenant-a',c.actor,t,'new',None)


@pytest.mark.parametrize('forged',['tenant_id','instance_id','context_id','agent_template_version_id','tool_scopes','credit_cost','persona'])
def test_http_rejects_client_execution_overrides(execution,monkeypatch,forged):
    import app.main as main
    c=execution;t,v=make_ready(c)
    monkeypatch.setattr(main,'product_store',c.product)
    http=FastAPI();http.router.routes.extend(r for r in main.app.routes if getattr(r,'path','')=='/api/v1/agents/{agent_id}/runs')
    with TestClient(http) as client:
        client.cookies.set('workbench_session',main.sessions.issue(UserPrincipal(c.actor,'tenant-a','enterprise_admin')))
        assert client.post(f'/api/v1/agents/{t}/runs',json={'message':'x',forged:'forged'}).status_code==422


def test_dynamic_agents_and_unknown_metadata_http(execution,monkeypatch):
    import app.main as main
    c=execution;t,v=make_ready(c);monkeypatch.setattr(main,'product_store',c.product)
    http=FastAPI();http.router.routes.extend(r for r in main.app.routes if getattr(r,'path','') in ('/api/v1/agents','/api/v1/agents/{agent_id}'))
    with TestClient(http) as client:
        client.cookies.set('workbench_session',main.sessions.issue(UserPrincipal(c.actor,'tenant-a','enterprise_admin')))
        assert t in {a['id'] for a in client.get('/api/v1/agents').json()}
        assert client.get(f'/api/v1/agents/{t}').status_code==200
        assert client.get('/api/v1/agents/unknown').status_code==404


def test_persistence_recovery_idempotent_fixed_credit(execution,monkeypatch):
    c=execution;t,v=make_ready(c);task=c.product.create_task('tenant-a',c.actor,t,'x',None)
    original=c.product.complete_task_success
    monkeypatch.setattr(c.product,'complete_task_success',lambda *a,**k:(_ for _ in ()).throw(ResultPersistenceError('synthetic')))
    asyncio.run(c.tasks.execute(task));assert c.product.task_for_worker(task['id'])['status']=='failed'
    turns=c.runtime.turns
    monkeypatch.setattr(c.product,'complete_task_success',original)
    asyncio.run(c.tasks.execute(task));asyncio.run(c.tasks.execute(task))
    assert c.runtime.turns==turns and c.product.task_for_worker(task['id'])['status']=='completed'
    with c.store.connection() as conn:assert conn.execute('SELECT COUNT(*) AS n FROM credit_transactions WHERE task_id=?',(task['id'],)).fetchone()['n']==1


def test_insufficient_credit_rolls_back_context(execution):
    c=execution;t,v=make_ready(c)
    with c.store.connection() as conn:
        conn.execute("UPDATE credit_accounts SET balance=0 WHERE tenant_id='tenant-a'")
        before=conn.execute('SELECT COUNT(*) AS n FROM agent_execution_contexts').fetchone()['n']
    with pytest.raises(ValueError,match='insufficient_credit'):c.product.create_task('tenant-a',c.actor,t,'x',None)
    with c.store.connection() as conn:assert conn.execute('SELECT COUNT(*) AS n FROM agent_execution_contexts').fetchone()['n']==before


def test_skill_snapshot_prevents_destructive_delete_after_draft_binding_removed(execution):
    c=execution;t,v=new_draft(c)
    item=c.registry.import_archive('only-context','1.0.0','Context','',skill_zip('only-context'),c.actor);c.registry.publish(item['id'],c.actor)
    c.control.bind_skills(t,v,[{'skill_id':item['skill_id'],'skill_version_id':item['id']}],c.actor)
    with c.store.connection() as conn:conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id) VALUES ('tenant-a',?,'configured',?,?)",(t,str(uuid4()),v))
    evidence(c,t,v);c.control.bind_skills(t,v,[],c.actor)
    for sql in ('DELETE FROM skill_versions WHERE id=?','DELETE FROM skill_packages WHERE skill_version_id=?'):
        with pytest.raises(sqlite3.IntegrityError,match='historical context'),c.store.connection() as conn:conn.execute(sql,(item['id'],))


def test_runtime_test_without_hard_isolation_guard_blocks_before_writes(execution):
    from app.agent_runtime_test import AgentRuntimeTest
    c=execution;t,v=new_draft(c)
    with pytest.raises(AgentCatalogError,match='isolation hard gate'):
        asyncio.run(AgentRuntimeTest(c.resolver,c.product,c.tasks).run(t,v,c.actor))


def test_context_contains_no_credentials_and_persona_is_snapshot(execution):
    c=execution;t,v=make_ready(c);task=c.product.create_task('tenant-a',c.actor,t,'x',None);ctx=context(c,task)
    assert c.resolver.settings.token_secret not in canonical(ctx)
    assert 'unit-test-placeholder' not in canonical(ctx)
    assert 'Synthetic test persona' in ctx['persona_snapshot']
    assert 'API Key' not in ctx and 'token_secret' not in ctx
