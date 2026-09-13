"""Asynchronous, server-policy-controlled tests on the normal task queue."""
import json
import os
from uuid import uuid4

from app.agent_execution import completion_evidence
from app.agent_productization import AgentCatalogError, canonical
from app.runtime.codex_provider import CodexRuntimeProvider


class AgentRuntimeTest:
    def __init__(self, resolver, product, tasks):
        self.resolver, self.product, self.tasks = resolver, product, tasks
        self.isolation_guard = None
        self.enqueue = None

    async def run(self, template_id, version_id, actor, fingerprint=None):
        r=self.resolver
        if r.settings.environment != 'production':
            if not self.isolation_guard:
                raise AgentCatalogError('Runtime Test isolation hard gate not configured',409)
            self.isolation_guard()
        if not r.registry.is_platform_admin(actor):
            raise AgentCatalogError('Authenticated platform_admin required',403)
        if not isinstance(self.tasks._agents._runtime,CodexRuntimeProvider):
            raise AgentCatalogError('Runtime Test requires real CodexRuntimeProvider',409)
        if not self.enqueue:
            raise AgentCatalogError('Runtime Test queue unavailable',409)
        with r.store.connection() as conn:
            if not r.store.is_postgres:conn.execute('BEGIN IMMEDIATE')
            r.catalog._productized(r.catalog._template(conn,template_id,lock=True))
            if not r.test_allowed(conn,r.test_tenant_id,template_id):
                raise AgentCatalogError('Server Runtime Test policy denied',409)
            version=r.catalog._version(conn,template_id,version_id,draft=True)
            if not fingerprint or fingerprint != version['configuration_fingerprint']:
                raise AgentCatalogError('Current configuration fingerprint required',409)
            errors=r.catalog._validation_errors(conn,version)
            if errors:raise AgentCatalogError('; '.join(errors),409)
            r._ready(conn,r.test_tenant_id,version)
            # Never choose the first user. The authenticated admin must belong
            # to the explicitly designated test Tenant; otherwise fail closed.
            if not conn.execute('SELECT 1 FROM users WHERE id=? AND tenant_id=?',(actor,r.test_tenant_id)).fetchone():
                raise AgentCatalogError('Admin must belong to designated Pilot Tenant',409)
            instance=conn.execute('SELECT * FROM tenant_agent_instances WHERE tenant_id=? AND agent_id=?',(r.test_tenant_id,template_id)).fetchone()
            if instance and instance['status']=='enabled':
                raise AgentCatalogError('Disable Pilot instance before testing another Draft',409)
            conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id,overrides_json) VALUES (?,?,'configured',?,?, '{}') ON CONFLICT(tenant_id,agent_id) DO UPDATE SET status='configured',agent_template_version_id=excluded.agent_template_version_id",
                         (r.test_tenant_id,template_id,str(uuid4()),version_id))
        test_id=str(uuid4())
        task=self.product.create_task(r.test_tenant_id,actor,template_id,
            '读取绑定 Skill 的 SKILL.md，按 Persona 用一句中文介绍能力。调用全部 required 工具；若绑定 optional enterprise_config_get，请读取企业配置。只返回最终结果。',
            None,_test_revision=version_id,_test_id=test_id,_test_fingerprint=fingerprint)
        try:self.enqueue(task['id'])
        except Exception:
            self.product.set_task(task['id'],r.test_tenant_id,'failed','enqueue_failed','任务入队失败',error_code='enqueue_failed')
            with r.store.connection() as conn:
                conn.execute("UPDATE agent_template_tests SET status='failed' WHERE id=? AND status='queued'",(test_id,))
            raise AgentCatalogError('Runtime Test enqueue failed',503) from None
        return {'runtime_test_id':test_id,'task_id':task['id'],'status':'queued'}

    def started(self, task):
        interrupted=False
        with self.resolver.store.connection() as conn:
            if not self.resolver.store.is_postgres:conn.execute('BEGIN IMMEDIATE')
            test=conn.execute("SELECT * FROM agent_template_tests WHERE task_id=? AND test_type='runtime'"+(' FOR UPDATE' if self.resolver.store.is_postgres else ''),(task['id'],)).fetchone()
            if test and test['status']=='running' and json.loads(test['result_json']).get('worker_pid') != os.getpid():
                previous_pid=json.loads(test['result_json']).get('worker_pid')
                if isinstance(previous_pid,int) and previous_pid>0:
                    try:os.kill(previous_pid,0)
                    except ProcessLookupError:pass
                    else:return False  # A live consumer owns this test.
                # A recovered delivery must not repeat an interrupted model turn.
                # Retry requires a new, explicitly authorized Runtime Test.
                conn.execute("UPDATE agent_template_tests SET status='failed',result_json=? WHERE id=? AND status='running'",
                    (canonical({'worker_interrupted':True,'worker_pid':os.getpid()}),test['id']))
                interrupted=True
            changed=conn.execute("UPDATE agent_template_tests SET status='running',result_json=? WHERE task_id=? AND test_type='runtime' AND status='queued'",
                (canonical({'worker_started':True,'worker_pid':os.getpid()}),task['id']))
        if interrupted:
            self.product.set_task(task['id'],task['tenant_id'],'failed','worker_interrupted','测试消费者中断，请重新执行 Runtime Test',error_code='worker_interrupted')
            return False
        if changed.rowcount:
            self.product.set_task(task['id'],task['tenant_id'],'running','worker_started','独立任务消费者开始执行')
        return True

    def finished(self, task):
        r=self.resolver
        with r.store.connection() as conn:
            test=conn.execute("SELECT * FROM agent_template_tests WHERE task_id=? AND test_type='runtime'",(task['id'],)).fetchone()
            if not test or test['status'] in {'passed','failed','invalidated'}:return
            saved=self.product.task_for_worker(task['id'])
            ctx=r.task_context(conn,task)
            if (test['agent_template_version_id'] != ctx['agent_template_version_id'] or
                test['configuration_fingerprint'] != ctx['configuration_fingerprint']):
                raise AgentCatalogError('Runtime Test task/context association mismatch',409)
            version=r.catalog._version(conn,ctx['agent_id'],ctx['agent_template_version_id'])
            test=conn.execute("SELECT * FROM agent_template_tests WHERE id=?"+(' FOR UPDATE' if r.store.is_postgres else ''),(test['id'],)).fetchone()
            if test['status'] in {'passed','failed','invalidated'}:return
            invalidated=version['configuration_fingerprint'] != test['configuration_fingerprint']
            trace=r.store.run_trace(saved['run_id'],task['tenant_id']) if saved.get('run_id') else None
            payload=trace['payload'] if trace else {}
            manifest=json.loads(ctx['skill_manifest_snapshot'])
            discovered={e.get('skill') for e in payload.get('lifecycle_events',[]) if e.get('event')=='skill_discovered'}
            skill_passed=set(manifest)<=discovered
            passed=(saved['status']=='completed' and trace and trace['status']=='completed'
                    and payload.get('execution_context_id')==ctx['id'] and completion_evidence(payload) and skill_passed
                    and (ctx['output_policy']!='image_required' or payload.get('artifact_completed') is True))
            status='invalidated' if invalidated else ('passed' if passed else 'failed')
            result={'status':status,'task_id':task['id'],'version_id':ctx['agent_template_version_id'],
                'configuration_fingerprint':ctx['configuration_fingerprint'],'context_id':ctx['id'],
                'instance_id':ctx['instance_id'],'run_id':saved.get('run_id'),'conversation_id':saved.get('conversation_id'),
                'runtime_profile_id':ctx['runtime_profile_id'],'skill_discovery_passed':skill_passed,
                'execution_chain':'codex-runtime-persisted' if passed else 'codex-runtime-failed',
                'worker_started':True,'worker_pid':os.getpid(),
                **{k:payload.get(k) for k in ('runtime_completed','required_tool_calls_completed','final_response_received','final_response_persisted','artifact_completed')}}
            conn.execute("UPDATE agent_template_tests SET status=?,result_json=? WHERE id=? AND task_id=? AND agent_template_version_id=? AND configuration_fingerprint=? AND status IN ('queued','running')",
                (status,canonical(result),test['id'],task['id'],ctx['agent_template_version_id'],ctx['configuration_fingerprint']))
