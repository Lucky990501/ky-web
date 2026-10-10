"""Durable Action route inside TaskService / SkillActionDispatcher, no model.

Only server-verified bundles reach this route. The old model/MCP CREATE_DRAFT
registration remains disabled. Every child network permit rechecks authority.
"""
import asyncio
import json
import time
from types import SimpleNamespace

from app.auth import UserPrincipal
from app.security import RuntimePrincipal
from app.wechat_prepare_reader import WechatPrepareReader, identity
from app.wechat_draft_operations import DraftOperations, DraftOperationError, TASK_MARKER
from app.wechat_action_contract import account_config


class WechatDraftExecution:
    def __init__(self, product, tokens, settings):
        self.product,self.store,self.tokens,self.settings=product,product._store,tokens,settings
        self.operations=DraftOperations(self.store)
        self.reader=WechatPrepareReader(self.store,settings.data_dir)
        from app.wechat_draft_capability import load
        self._capability=load
        from app.tenant_secret_backend import backend_from_settings
        from app.tenant_secret_reference import TenantSecretReferences
        self.secrets=TenantSecretReferences(settings.environment,backend=backend_from_settings(settings))

    def _check(self, binding, conn=None):
        if self.settings.environment!='test' or binding['environment']!=self.settings.environment:
            raise DraftOperationError('WECHAT_ENVIRONMENT_BLOCKED')
        if conn is None:
            with self.store.connection() as transaction:
                return self._check(binding,transaction)
        context=conn.execute('SELECT * FROM agent_execution_contexts WHERE id=?',(binding['context_id'],)).fetchone()
        if not context:raise DraftOperationError()
        context=dict(context);policy=json.loads(context['tool_policy_snapshot'])
        if policy.get('runtime_test') is not False or policy.get('eligibility_mode')=='SKILL_ONLY_TEST_QUALIFIED':
            raise DraftOperationError('WECHAT_PUBLISHED_AGENT_REQUIRED')
        resolver=self.product.execution_resolver
        if resolver is None:raise DraftOperationError()
        resolver.check_context(conn,context)
        version=conn.execute('SELECT * FROM agent_template_versions WHERE id=? AND agent_template_id=? AND status=\'published\'',
                             (binding['agent_revision_id'],binding['agent_id'])).fetchone()
        current=conn.execute('''SELECT i.instance_id FROM tenant_agent_instances i JOIN agent_templates a ON a.id=i.agent_id
            JOIN users u ON u.tenant_id=i.tenant_id WHERE i.tenant_id=? AND i.agent_id=? AND i.status='enabled'
            AND i.agent_template_version_id=? AND a.current_published_version_id=i.agent_template_version_id
            AND u.id=? AND u.account_status='enabled' FOR SHARE OF i,a,u''',
            (binding['tenant_id'],binding['agent_id'],binding['agent_revision_id'],binding['user_id'])).fetchone()
        if not version or not current or not resolver._runtime_passed(conn,dict(version)):
            raise DraftOperationError('WECHAT_PUBLISHED_AGENT_REQUIRED')
        if not {'skills:execute','wechat:draft:create'} <= set(policy['scopes']):raise DraftOperationError()
        account=account_config(self.store.enterprise_config(binding['tenant_id']).get('wechat_account'),binding['tenant_id'])
        target=identity(dict(tenant_id=binding['tenant_id'],environment=binding['environment'],appid=account['wechat_app_id']))
        reference=account.get('wechat_app_secret_ref') or {}
        if target!=binding['account_identity'] or reference.get('provider')!='wechat':raise DraftOperationError()
        if reference.get('version')!=binding['secret_version']:raise DraftOperationError('WECHAT_SECRET_VERSION_CHANGED')
        self.secrets.require_connected(binding['tenant_id'],account)
        capability,pin=self._capability(binding)
        if pin!=binding['capability_identity'] or not self.settings.wechat_network_allowed:
            raise DraftOperationError('WECHAT_CAPABILITY_REQUIRED')
        return context,account,capability

    def prepare_binding(self, principal, agent, message, expected_version):
        bundle=self.reader.bundle(principal,agent,message)
        binding=bundle['binding']
        if binding['article_version']!=expected_version:raise DraftOperationError('WECHAT_ARTICLE_VERSION_CHANGED')
        account=account_config(self.store.enterprise_config(principal.tenant_id).get('wechat_account'),principal.tenant_id)
        binding['secret_version']=(account.get('wechat_app_secret_ref') or {}).get('version')
        _,binding['capability_identity']=self._capability(binding)
        self._check(binding)
        return bundle

    def create(self, principal, agent, message, expected_version):
        # An old operation remains readable after revocation/rotation; never mint
        # a new operation just because its former execution grant is gone.
        prior=self.operations.owned(principal,agent,message)
        for row in prior:
            if row['article_version']==expected_version:
                return self.operations.public(row)
        bundle=self.prepare_binding(principal,agent,message,expected_version)
        binding=bundle['binding']
        row,_=self.operations.create(binding,lambda conn:self._check(binding,conn))
        # Commit already succeeded. A failed enqueue is recoverable by retrying
        # confirmation (same operation) or normal queue recovery; never rollback.
        return self.operations.public(row)

    def reauthorize(self, row):
        binding=row['binding_json']
        principal=UserPrincipal(row['user_id'],row['tenant_id'],'member')
        fresh=self.reader.bundle(principal,row['agent_id'],row['source_message_id'])
        for key in fresh['binding']:
            if fresh['binding'][key]!=binding[key]:raise DraftOperationError('WECHAT_ARTICLE_VERSION_CHANGED')
        context,account,capability=self._check(binding)
        return fresh,context,account,capability

    async def execute(self, task):
        # Blocking PG/subprocess work never blocks the API event loop.
        await asyncio.to_thread(self._execute,task)

    def _execute(self, task):
        row=self.operations.by_task(task['id'])
        if not row:raise DraftOperationError('WECHAT_OPERATION_MISSING')
        claimed=self.operations.claim(row['id'])
        if not claimed:
            current=self.operations.by_task(row['action_task_id'])
            if current['state'] not in {'UNKNOWN','CONFIRMED','FAILED','REVOKED'}:
                from app.product_service import TaskExecutionNotAuthorized
                raise TaskExecutionNotAuthorized('WECHAT_WORKER_LEASE_HELD')
            self._sync_terminal(current)
            return
        lease=claimed['lease_id']
        try:
            bundle,context,account,capability=self.reauthorize(claimed)
            policy=json.loads(context['tool_policy_snapshot'])
            bearer=self.tokens.issue(RuntimePrincipal(row['tenant_id'],row['agent_id'],context['runtime_profile_id'],
                tuple(policy['scopes']),int(time.time())+600,context['id'],context['instance_id']))
            scope=self.tokens.issue_task_scope(row['tenant_id'],row['action_task_id'])
            with self.store.connection() as conn:
                conn.execute("UPDATE tasks SET status='running',stage='wechat_draft' WHERE id=? AND status='queued'",(row['action_task_id'],))
                conn.execute("UPDATE run_traces SET status='running' WHERE run_id=?",(row['action_run_id'],))
            from app.skill_dispatch_config import from_settings
            dispatcher=from_settings(self.store,self.tokens,self.settings)
            dispatcher.execute_draft_operation(bearer,scope,claimed,bundle,self,read_only=bool(claimed['draft_media_id']))
        except Exception:
            self.operations.unknown(row['id'],lease)
        finally:
            current=self.operations.by_task(row['action_task_id'])
            self._sync_terminal(current)

    def _sync_terminal(self,current):
        if current['state'] not in {'UNKNOWN','CONFIRMED','FAILED','REVOKED'}:return
        status='completed' if current['state']=='CONFIRMED' else 'failed'
        with self.store.connection() as conn:
            conn.execute('UPDATE tasks SET status=?,stage=?,error_code=?,user_message=? WHERE id=?',
                (status,current['state'],'wechat_result_unknown' if current['state']=='UNKNOWN' else None,
                 '请查询草稿操作回执',current['action_task_id']))
            conn.execute('UPDATE run_traces SET status=? WHERE run_id=?',(status,current['action_run_id']))
