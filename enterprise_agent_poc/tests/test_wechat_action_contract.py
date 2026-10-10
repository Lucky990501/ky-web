"""Real Runtime token/DB authorization, with synthetic environment-only secrets."""
import json
import os
import shutil
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch
import uuid

from app.agent_execution import ExecutionResolver
from app.agent_productization import AgentProductization
from app.security import RuntimePrincipal,RuntimeTokenIssuer,TokenError
from app.store import POCStore
from app.product_store import ProductStore
from app.skill_registry import SkillRegistry
from app import wechat_skill
from app.tenant_secret_reference import TenantSecretReferences,SecretReferenceError,environment_key
from app.wechat_action_contract import WechatActionContract,WechatActionError,trigger,explicit_draft_intent
from test_wechat_skill_integration import Scratch


def reference(tenant='tenant-a',environment='test',name='account-a'):
    return dict(provider='runtime_environment',tenant_id=tenant,environment=environment,name=name)


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed=cls('test_01_prepare_without_account_or_secret')
        seed._build_fixture()
        cls.seed=seed

    @classmethod
    def tearDownClass(cls):cls.seed.temp.cleanup()

    def _build_fixture(self):
        self.temp=Scratch();self.root=self.temp.root
        self.store=POCStore(self.root/'contract.db');self.store.seed_demo_data()
        self.product=ProductStore(self.store);self.product.initialize()
        self.product.create_user('tenant-a','action-fixture@example.invalid','unused','Synthetic action fixture','member')
        self.actor=self.product.user_by_email('action-fixture@example.invalid')['id']
        self.registry=SkillRegistry(self.store,self.root/'registry',wechat_skill.SOURCE.parents[2]/'skill_packages')
        self.registry.initialize()
        self.catalog=AgentProductization(self.store);self.catalog.initialize()
        with self.store.connection() as conn:
            conn.execute("UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,advance_origin='controlled_advance',advanced_at=CURRENT_TIMESTAMP,advanced_by_release_id='wechat-contract-fixture',advanced_by_source_commit=? WHERE scope='agent_data_contract'",('f'*40,))
        existing=self.catalog.create_template(dict(slug=wechat_skill.AGENT_SLUG,name='公众号运营助手'),self.actor)
        existing=self.catalog.create_version(existing['id'],dict(persona='Synthetic test'),self.actor)
        self.agent,self.revision=existing['id'],existing['versions'][0]['id']
        skill=wechat_skill.import_revision(self.registry,self.actor);self.registry.publish(skill['id'],self.actor)
        wechat_skill.apply_binding_plan(self.catalog,self.registry.version(skill['id']),self.revision,self.actor)
        self.settings=SimpleNamespace(environment='test',model_provider_id='deepseek',model_id='deepseek-v4-pro',reasoning_effort='high')
        self.resolver=ExecutionResolver(self.store,self.registry,self.catalog,self.settings,'tenant-a')
        self.resolver.initialize_local();self.product.execution_resolver=self.resolver
        self.catalog.execution_resolver=self.resolver
        self.catalog.validate(self.agent,self.revision,self.actor)
        # Configured test instance, exactly like existing execution-contract fixtures.
        with self.store.connection() as conn:
            conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id,overrides_json) VALUES (?,?,'configured',?,?,'{}')",('tenant-a',self.agent,str(uuid.uuid4()),self.revision))
        self.issuer=RuntimeTokenIssuer('synthetic-test-signing-secret-only-v1')
        self.material={}
        self.refs=TenantSecretReferences('test',self.material)
        self.contract=WechatActionContract(self.store,self.issuer,'test',self.refs)

    def setUp(self):
        # Immutable package is already formally imported/published by _build_fixture.
        # Each test gets a fresh DB; no package/permission method is monkeypatched.
        self.temp=Scratch();self.root=self.temp.root
        shutil.copyfile(self.seed.store.database_path,self.root/'contract.db')
        self.store=POCStore(self.root/'contract.db');self.product=ProductStore(self.store)
        self.actor,self.agent,self.revision=self.seed.actor,self.seed.agent,self.seed.revision
        self.registry=SkillRegistry(self.store,self.seed.registry.data_root,self.seed.registry.bundled_root)
        self.catalog=AgentProductization(self.store);self.settings=self.seed.settings
        self.resolver=ExecutionResolver(self.store,self.registry,self.catalog,self.settings,'tenant-a')
        self.product.execution_resolver=self.resolver;self.catalog.execution_resolver=self.resolver
        self.issuer=RuntimeTokenIssuer('synthetic-test-signing-secret-only-v1')
        self.material={};self.refs=TenantSecretReferences('test',self.material)
        self.contract=WechatActionContract(self.store,self.issuer,'test',self.refs)

    def tearDown(self):self.temp.cleanup()

    def task(self,text):
        task=self.product.create_task('tenant-a',self.actor,self.agent,text,None,
            _test_revision=self.revision,_test_id=str(uuid.uuid4()))
        self.product.set_task(task['id'],'tenant-a','running','generating','Synthetic test')
        with self.store.connection() as conn:context=self.resolver.task_context(conn,task)
        policy=json.loads(context['tool_policy_snapshot'])
        principal=RuntimePrincipal('tenant-a',self.agent,context['runtime_profile_id'],tuple(policy['scopes']),
            int(time.time())+60,context['id'],context['instance_id'])
        return self.issuer.issue(principal),self.issuer.issue_task_scope('tenant-a',task['id']),task

    def account(self,ref=None):
        value=dict(account_display_name='Synthetic Test Account',wechat_app_id='wxaaaaaaaaaaaaaaaa',
            wechat_app_secret_ref=ref or reference())
        self.product.update_enterprise_config('tenant-a',{'wechat_account':value})
        return value

    def test_01_prepare_without_account_or_secret(self):
        bearer,scope,_=self.task('帮我排版公众号文章')
        receipt=self.contract.resolve(bearer,scope,'PREPARE')
        self.assertEqual(receipt['network_targets'],[]);self.assertIsNone(receipt['account_config_identity'])

    def test_02_create_without_secret_blocks(self):
        self.account();bearer,scope,_=self.task('创建公众号草稿')
        with self.assertRaises(SecretReferenceError):self.contract.resolve(bearer,scope,'CREATE_DRAFT')

    def test_03_synthetic_test_secret_permission_pass_no_execution(self):
        account=self.account();key=environment_key(account['wechat_app_secret_ref'],'tenant-a','test','wechat_app_secret')
        self.material[key]='SYNTHETIC-SECRET-A-NOT-A-REAL-CREDENTIAL'
        bearer,scope,_=self.task('上传到公众号草稿箱')
        # Legacy process-env material alone no longer certifies connected/network.
        # Positive connected protected-backend coverage is in secret provisioning.
        with self.assertRaises(SecretReferenceError): self.contract.resolve(bearer,scope,'CREATE_DRAFT')

    def test_04_ordinary_chat_not_triggered(self):
        bearer,scope,_=self.task('你好，今天怎么样')
        with self.assertRaisesRegex(WechatActionError,'NOT_TRIGGERED'):self.contract.resolve(bearer,scope,'PREPARE')

    def test_05_writing_is_prepare(self):self.assertEqual(trigger('写一篇公众号文章'),'PREPARE')

    def test_06_upload_draftbox_intent(self):
        for value in ('上传到草稿箱','创建公众号草稿','放到公众号草稿箱',
                      '请上传到公众号草稿箱','帮我把这篇文章上传到公众号草稿箱'):
            self.assertEqual(trigger(value),'CREATE_DRAFT')

    def test_07_trigger_not_upload_authorization(self):
        bearer,scope,_=self.task('写公众号文章，给我排版预览')
        with self.assertRaisesRegex(WechatActionError,'UPLOAD_NOT_AUTHORIZED'):self.contract.resolve(bearer,scope,'CREATE_DRAFT')

    def test_08_tenant_a_cannot_resolve_b(self):
        source={environment_key(reference('tenant-b'),'tenant-b','test','wechat_app_secret'):'SYNTHETIC-B'}
        with self.assertRaises(SecretReferenceError):TenantSecretReferences('test',source).resolve_wechat('tenant-a',dict(wechat_app_id='wxaaaaaaaaaaaaaaaa',wechat_app_secret_ref=reference('tenant-b')))
        with self.assertRaises(ValueError):self.account(reference('tenant-b'))

    def test_09_test_cannot_resolve_production(self):
        class Backend(dict):
            reads=0
            def get(self,*args):self.reads+=1;return super().get(*args)
        backend=Backend()
        ref=reference(environment='production')
        backend[environment_key(ref,'tenant-a','production','wechat_app_secret')]='SYNTHETIC-PRODUCTION-MARKER'
        self.account(ref)
        self.contract.secrets=TenantSecretReferences('test',backend)
        bearer,scope,_=self.task('创建公众号草稿')
        with self.assertRaises(SecretReferenceError):self.contract.resolve(bearer,scope,'CREATE_DRAFT')
        self.assertEqual(backend.reads,0)

    def test_10_audit_has_no_secret(self):
        account=self.account();self.material[environment_key(account['wechat_app_secret_ref'],'tenant-a','test','wechat_app_secret')]='SYNTHETIC-NO-LOG-SECRET'
        bearer,scope,task=self.task('创建公众号草稿')
        with self.assertRaises(SecretReferenceError): self.contract.resolve(bearer,scope,'CREATE_DRAFT')
        with self.store.connection() as conn:row=conn.execute("SELECT payload FROM execution_events WHERE event_type='wechat.action.permission' ORDER BY id DESC").fetchone()
        self.assertNotIn('SYNTHETIC-NO-LOG-SECRET',row['payload']);self.assertNotIn('WECHAT_APP_SECRET',row['payload'])
        self.assertEqual(json.loads(row['payload'])['task_id'],task['id'])

    def test_11_temporary_child_injection_scrubbed(self):
        account=self.account();self.material[environment_key(account['wechat_app_secret_ref'],'tenant-a','test','wechat_app_secret')]='SYNTHETIC-LEASE'
        bearer,scope,_=self.task('创建公众号草稿')
        with self.assertRaises(SecretReferenceError): self.contract.prepare_secret_injection(bearer,scope)
        lease=self.refs.resolve_wechat('tenant-a',account)  # legacy lease redaction contract, not draft authority
        self.assertNotIn('SYNTHETIC-LEASE',repr(lease))
        with lease.child_environment({'PATH':'test','OPENAI_API_KEY':'DO-NOT-INHERIT'}) as env:
            self.assertEqual(env['WECHAT_APP_SECRET'],'SYNTHETIC-LEASE');self.assertNotIn('OPENAI_API_KEY',env)
            self.assertEqual(lease.redact('failure SYNTHETIC-LEASE'),'failure [REDACTED]')
        self.assertEqual(env,{});self.assertEqual(lease._values,{})
        self.assertNotEqual(os.environ.get('WECHAT_APP_SECRET'),'SYNTHETIC-LEASE')

    def test_12_plaintext_config_rejected_before_db(self):
        for value in ({'wechat_app_secret':'DO-NOT-SAVE'}, {'wechat_account':dict(account_display_name='Test',wechat_app_id='wxaaaaaaaaaaaaaaaa',AppSecret='DO-NOT-SAVE')}, {'wechat_app_secret_ref':'DO-NOT-SAVE'}):
            with self.assertRaises(ValueError):self.product.update_enterprise_config('tenant-a',value)
        self.assertNotIn('DO-NOT-SAVE',json.dumps(self.store.enterprise_config('tenant-a')))

    def test_13_signed_scope_required_and_cross_tenant_reject(self):
        bearer,scope,task=self.task('公众号排版')
        for wrong in (task['id'],self.issuer.issue_task_scope('tenant-b',task['id'])):
            with self.assertRaises(TokenError):self.contract.resolve(bearer,wrong,'PREPARE')

    def test_14_legacy_token_cannot_grant_action(self):
        token=self.issuer.issue(RuntimePrincipal('tenant-a',self.agent,'legacy',('wechat:prepare',),int(time.time())+60))
        with self.assertRaises(TokenError):self.contract.resolve(token,self.issuer.issue_task_scope('tenant-a','task'),'PREPARE')

    def test_15_disabled_instance_denies(self):
        bearer,scope,_=self.task('公众号排版')
        with self.store.connection() as conn:conn.execute("UPDATE tenant_agent_instances SET status='disabled' WHERE agent_id=?",(self.agent,))
        with self.assertRaises(TokenError):self.contract.resolve(bearer,scope,'PREPARE')

    def test_16_unbound_capability_denies(self):
        self.catalog.bind_tools(self.agent,self.revision,[],self.actor)
        bearer,scope,_=self.task('公众号排版')
        with self.assertRaises(TokenError):self.contract.resolve(bearer,scope,'PREPARE')

    def test_17_informational_or_negative_intent_not_grant(self):
        for text in ('如何上传到草稿箱','不要上传到公众号','介绍“创建公众号草稿”这个功能','写公众号文章，不需要创建草稿',
                     '不上传到公众号','未授权上传到公众号','假设我让你上传到草稿箱','如果条件满足就创建公众号草稿',
                     '昨天上传到公众号','帮我看看昨天上传到公众号的情况','文章正文：上传到公众号',
                     '> 上传到草稿箱','`创建公众号草稿`','```text\n上传到公众号\n```','上传到草稿箱了吗'):
            self.assertFalse(explicit_draft_intent(text))

    def test_18_bindings_only_existing_agent_optional_actions(self):
        detail=self.catalog.detail(self.agent)
        self.assertEqual(detail['slug'],wechat_skill.AGENT_SLUG)
        tools=detail['versions'][0]['tools']
        self.assertEqual({x['tool_capability_id'] for x in tools},{'wechat_prepare_authorize','wechat_create_draft_authorize'})
        self.assertTrue(all(x['invocation_requirement']=='optional' for x in tools))

    def test_19_deny_unsupported_actions(self):
        for action in ('PUBLISH','MASS_SEND','DELETE','UPDATE_EXISTING_DRAFT'):
            with self.assertRaises(WechatActionError):self.contract.resolve('invalid','invalid',action)

    def test_20_missing_account_blocks(self):
        bearer,scope,_=self.task('创建公众号草稿')
        with self.assertRaises(WechatActionError):self.contract.resolve(bearer,scope,'CREATE_DRAFT')

    def test_21_access_token_reference_injection(self):
        account=dict(account_display_name='Synthetic Test',wechat_app_id='wxaaaaaaaaaaaaaaaa',wechat_access_token_ref=reference(name='token-a'))
        self.product.update_enterprise_config('tenant-a',{'wechat_account':account})
        self.material[environment_key(account['wechat_access_token_ref'],'tenant-a','test','wechat_access_token')]='SYNTHETIC-TOKEN'
        bearer,scope,_=self.task('放到公众号草稿箱')
        with self.assertRaises(SecretReferenceError): self.contract.prepare_secret_injection(bearer,scope)
        lease=self.refs.resolve_wechat('tenant-a',account)
        with lease.child_environment() as env:
            self.assertEqual(env['WECHAT_ACCESS_TOKEN'],'SYNTHETIC-TOKEN');self.assertNotIn('WECHAT_APP_SECRET',env)

    def test_22_resolver_environment_must_match_contract(self):
        with self.assertRaises(SecretReferenceError):
            WechatActionContract(self.store,self.issuer,'test',TenantSecretReferences('production',{}))

    def test_23_credential_denial_is_audited_without_values(self):
        self.account();bearer,scope,_=self.task('创建公众号草稿')
        with self.assertRaises(SecretReferenceError):self.contract.resolve(bearer,scope,'CREATE_DRAFT')
        with self.store.connection() as conn:row=conn.execute("SELECT payload FROM execution_events WHERE event_type='wechat.action.permission' ORDER BY id DESC").fetchone()
        audit=json.loads(row['payload'])
        self.assertEqual(audit['execution_status'],'BLOCKED_NOT_EXECUTED');self.assertEqual(audit['network_targets'],[])

    def test_24_ambient_global_credentials_are_ignored(self):
        self.account();bearer,scope,_=self.task('创建公众号草稿')
        with patch.dict(os.environ,{'WECHAT_APP_SECRET':'AMBIENT-MUST-NOT-USE','WECHAT_ACCESS_TOKEN':'AMBIENT-TOKEN'}):
            with self.assertRaises(SecretReferenceError):self.contract.resolve(bearer,scope,'CREATE_DRAFT')

    def test_25_child_exception_is_redacted_and_scope_cleared(self):
        account=self.account();self.material[environment_key(account['wechat_app_secret_ref'],'tenant-a','test','wechat_app_secret')]='SYNTHETIC-CHILD-SECRET'
        bearer,scope,_=self.task('创建公众号草稿')
        with self.assertRaises(SecretReferenceError):self.contract.prepare_secret_injection(bearer,scope)
        lease=self.refs.resolve_wechat('tenant-a',account)
        with self.assertRaises(SecretReferenceError) as caught:
            with lease.child_environment() as env:raise ValueError('failed token=SYNTHETIC-CHILD-SECRET')
        self.assertNotIn('SYNTHETIC-CHILD-SECRET',str(caught.exception));self.assertEqual(env,{})
        with self.assertRaises(SecretReferenceError):lease.redact('any output')

    def test_26_mcp_requires_transport_signed_task_header(self):
        import ast,hashlib
        from pathlib import Path
        # Execute the actual pure header helpers without importing/configuring a
        # live FastMCP server or reading any runtime settings/credentials.
        path=Path(__file__).resolve().parents[1]/'app/platform_mcp/server.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in
               {'_execution_scope_from_context','_wechat_task_scope'}]
        namespace={'Any':object,'hashlib':hashlib}
        exec(compile(ast.Module(body=funcs,type_ignores=[]),str(path),'exec'),namespace)
        helper=namespace['_wechat_task_scope']
        for headers in ({},{'mcp-session-id':'session'}):
            ctx=SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers=headers)))
            with self.assertRaises(PermissionError):helper(ctx,'synthetic-bearer')
        ctx=SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={'x-runtime-execution-scope':'signed-task'})))
        self.assertEqual(helper(ctx,'synthetic-bearer'),'signed-task')


if __name__=='__main__':unittest.main()
