"""Offline real Task/Agent/MCP/Registry authorization; process/runtime are mocks.

pytest's conftest isolates Settings BEFORE imports: no live credentials.
Uses the already installed local MCP SDK, no HTTP listener or Provider calls.
"""
import ast
import asyncio
import base64
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import types
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from PIL import Image
from app.document_generator import ActivityPlanContent
from mcp.server.fastmcp import FastMCP,Context
from mcp.types import ToolAnnotations,CallToolResult,TextContent
from app.store import POCStore
from app.product_store import ProductStore
from app.agent_productization import AgentProductization
from app.agent_execution import ExecutionResolver,profile
from app.skill_registry import SkillRegistry
from app.security import RuntimePrincipal,RuntimeTokenIssuer
from app.service import AgentService
from app.product_service import TaskService
from app.runtime.base import RuntimeProvider,RuntimeStartError
from app.domain import RuntimeProfile,RuntimeSession,RuntimeTurn,RuntimeStreamEvent,SandboxPolicy
from app.platform_mcp.service import PlatformMCPService
from app import wechat_skill
from app.skill_dispatch import (ActionRegistration,RuntimeEntry,SkillActionDispatcher,
    SkillDispatchError,add_dispatch_binding,safe_arguments)
from app.skill_python_runtime import SkillRuntimeError,file_tree_digest
from app.wechat_action_contract import WechatActionContract
from app.tenant_secret_reference import TenantSecretReferences
from app.wechat_prepare_action import WechatPrepareAdapter

# Compile the actual provider class without importing unrelated DOCX runtime
# dependencies absent in this already installed MCP-only local interpreter.
# Its session/header methods and trace normalization are not mocked/reimplemented.
def provider_class():
    import hashlib,re
    from app.tool_dependencies import attempt_observation,now
    path=wechat_skill.SOURCE.parents[2]/'app/runtime/codex_provider.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    nodes=[node for node in tree.body if isinstance(node,ast.ImportFrom) and node.module=='__future__'
           or isinstance(node,ast.ClassDef) and node.name=='CodexRuntimeProvider']
    namespace=dict(RuntimeProvider=RuntimeProvider,RuntimeSession=RuntimeSession,RuntimeProfile=RuntimeProfile,
        RuntimeTurn=RuntimeTurn,RuntimeStreamEvent=RuntimeStreamEvent,SandboxPolicy=SandboxPolicy,
        RuntimeStartError=RuntimeStartError,asyncio=asyncio,uuid4=uuid.uuid4,json=json,hashlib=hashlib,
        re=re,time=time,SimpleNamespace=SimpleNamespace,attempt_observation=attempt_observation,now=now)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),namespace)
    return namespace['CodexRuntimeProvider']


CodexRuntimeProvider=provider_class()


class ModelMock(RuntimeProvider):
    def __init__(self,test):self.test=test;self.scope=None;self.profile=None
    async def create_session(self,profile,developer_instructions,*,task_id=None):
        self.profile=profile
        self.test.assertIsNotNone(task_id)
        self.scope=self.test.issuer.issue_task_scope(profile.tenant_id,task_id)
        return RuntimeSession(str(uuid.uuid4()),profile.id)
    async def resume_session(self,profile,thread_id,developer_instructions=None,recovery_context=None,*,task_id=None):
        await self.create_session(profile,developer_instructions,task_id=task_id)
        return RuntimeSession(thread_id,profile.id)
    async def run_turn(self,session,message):
        principal=RuntimePrincipal(self.profile.tenant_id,self.profile.agent_id,self.profile.id,
            self.profile.tool_scopes,int(time.time())+300,self.profile.execution_context_id,self.profile.instance_id)
        self.test.bearer=self.test.issuer.issue(principal);self.test.scope=self.scope
        result=await self.test.tool_call()
        self.test.model_received=result.structuredContent
        return RuntimeTurn(session.thread_id,result.structuredContent['summary'],mcp_calls=(dict(
            server='platform',tool='skill_action_execute',status='completed',duration_ms=1,
            input_summary='Pinned Skill PREPARE',output_summary=json.dumps(result.structuredContent),
            result_is_error=result.isError),),status='completed')
    async def close(self):pass


class DispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=tempfile.TemporaryDirectory(prefix='skill-dispatch-seed-')
        cls.seed_root=Path(cls.fixture.name)
        cls.seed=POCStore(cls.seed_root/'fixture.db');cls.seed.seed_demo_data()
        product=ProductStore(cls.seed);product.initialize()
        product.create_user('tenant-a','dispatch@example.invalid','unused','Synthetic dispatch fixture','member')
        cls.actor=product.user_by_email('dispatch@example.invalid')['id']
        cls.registry=SkillRegistry(cls.seed,cls.seed_root/'registry',wechat_skill.SOURCE.parents[2]/'skill_packages')
        cls.registry.initialize();catalog=AgentProductization(cls.seed);catalog.initialize()
        with cls.seed.connection() as conn:
            conn.execute("UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,advance_origin='controlled_advance',advanced_at=CURRENT_TIMESTAMP,advanced_by_release_id='dispatch-fixture',advanced_by_source_commit=? WHERE scope='agent_data_contract'",('f'*40,))
        agent=catalog.create_template(dict(slug=wechat_skill.AGENT_SLUG,name='公众号运营助手'),cls.actor)
        agent=catalog.create_version(agent['id'],dict(persona='Synthetic test'),cls.actor)
        cls.agent,cls.agent_revision=agent['id'],agent['versions'][0]['id']
        imported=wechat_skill.import_revision(cls.registry,cls.actor);cls.registry.publish(imported['id'],cls.actor)
        cls.skill=cls.registry.version(imported['id'])
        wechat_skill.apply_binding_plan(catalog,cls.skill,cls.agent_revision,cls.actor)
        add_dispatch_binding(catalog,cls.agent,cls.agent_revision,cls.actor)
        settings=SimpleNamespace(environment='test',model_provider_id='deepseek',model_id='deepseek-v4-pro',reasoning_effort='high')
        resolver=ExecutionResolver(cls.seed,cls.registry,catalog,settings,'tenant-a');resolver.initialize_local()
        catalog.execution_resolver=resolver;catalog.validate(cls.agent,cls.agent_revision,cls.actor)
        with cls.seed.connection() as conn:
            conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id,overrides_json) VALUES (?,?,'configured',?,?,'{}')",('tenant-a',cls.agent,str(uuid.uuid4()),cls.agent_revision))

    @classmethod
    def tearDownClass(cls):cls.fixture.cleanup()

    def setUp(self):
        # Enforce this fixture's no-DOCX boundary only during its own test.
        # A module-level sys.modules replacement poisons collection of the
        # existing Word/release regressions in a combined Application suite.
        no_document = patch.object(ActivityPlanContent, 'model_validate',
            side_effect=AssertionError('DOCX action outside dispatch scope'))
        no_document.start();self.addCleanup(no_document.stop)
        self.temp=tempfile.TemporaryDirectory(prefix='skill-dispatch-test-');self.root=Path(self.temp.name)
        shutil.copyfile(self.seed.database_path,self.root/'fixture.db')
        self.store=POCStore(self.root/'fixture.db');self.product=ProductStore(self.store)
        self.catalog=AgentProductization(self.store)
        self.registry=SkillRegistry(self.store,self.__class__.registry.data_root,self.__class__.registry.bundled_root)
        self.settings=SimpleNamespace(environment='test',model_provider_id='deepseek',model_id='deepseek-v4-pro',
            reasoning_effort='high',data_dir=self.root/'data',skill_dispatch_config=None)
        self.resolver=ExecutionResolver(self.store,self.registry,self.catalog,self.settings,'tenant-a')
        self.product.execution_resolver=self.resolver;self.catalog.execution_resolver=self.resolver
        self.issuer=RuntimeTokenIssuer('synthetic-dispatch-signing-material-not-a-real-secret')
        self.runtime_calls=[];self.process_calls=[]
        self.skill_root=self.registry.published_root/'wechat-html-draft/1.0.0'
        def resolve(revision,action):
            self.runtime_calls.append((revision['id'],action))
            return RuntimeEntry(self.root/'mock-runtime/venv/bin/python',self.skill_root,self.root/'mock-runtime','1'*64,self.registry.data_root)
        self.runtime=SimpleNamespace(resolve=resolve)
        self.secret_backend={}
        self.permission=WechatActionContract(self.store,self.issuer,'test',TenantSecretReferences('test',self.secret_backend))
        registrations=[ActionRegistration(wechat_skill.SLUG,wechat_skill.VERSION,self.skill['checksum'],action,
            scope,'scripts/wechat_draft.py',('--check',),WechatPrepareAdapter(),self.runtime,self.permission.resolve,enabled)
            for action,scope,enabled in (('PREPARE','wechat:prepare',True),('CREATE_DRAFT','wechat:draft:create',False))]
        self.dispatch=SkillActionDispatcher(self.store,self.issuer,self.registry,self.settings.data_dir,
            registrations,runner=self.child,source_identity=dict(source_commit='c'*40,source_tree='d'*40))
        self.mcp_service=PlatformMCPService.__new__(PlatformMCPService)
        self.mcp_service._store=self.store;self.mcp_service._tokens=self.issuer
        self.mcp_service._settings=self.settings;self.mcp_service._skill_dispatcher=self.dispatch
        self.mcp=self.create_mcp()
        raw=io.BytesIO();Image.new('RGB',(8,8),'green').save(raw,format='PNG')
        self.article=dict(title='SENSITIVE-TITLE',digest='Sensitive digest',
            html='<section id="article"><p>PRIVATE-ARTICLE-BODY</p></section>',
            cover_asset='cover.png',assets={'cover.png':base64.b64encode(raw.getvalue()).decode()})
        self.make_task()

    def tearDown(self):self.temp.cleanup()

    def create_mcp(self):
        # Compile actual transport definitions without importing its global
        # Settings/store/service singleton. Real FastMCP registration/validation.
        path=wechat_skill.SOURCE.parents[2]/'app/platform_mcp/server.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        names={'_bearer_from_context','_execution_scope_from_context','_wechat_task_scope','create_mcp'}
        nodes=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in names]
        import hashlib,os
        namespace=dict(FastMCP=FastMCP,Context=Context,ToolAnnotations=ToolAnnotations,
            CallToolResult=CallToolResult,TextContent=TextContent,service=self.mcp_service,
            Any=object,asyncio=asyncio,json=json,hashlib=hashlib,os=os)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),namespace)
        return namespace['create_mcp']()

    def make_task(self,text='帮我排版公众号文章'):
        self.task=self.product.create_task('tenant-a',self.actor,self.agent,text,None,
            _test_revision=self.agent_revision,_test_id=str(uuid.uuid4()))
        self.product.set_task(self.task['id'],'tenant-a','running','generating','Synthetic test')
        with self.store.connection() as conn:self.context=self.resolver.task_context(conn,self.task)
        p=profile(self.context)
        self.bearer=self.issuer.issue(RuntimePrincipal('tenant-a',self.agent,p.id,p.tool_scopes,
            int(time.time())+300,self.context['id'],p.instance_id))
        self.scope=self.issuer.issue_task_scope('tenant-a',self.task['id'])
        run,conversation=str(uuid.uuid4()),str(uuid.uuid4())
        self.store.create_run_trace(run,conversation,'tenant-a',self.agent,'synthetic-thread',{})
        self.product.attach_task_run(self.task['id'],'tenant-a',run,conversation)

    def child(self,args,**kwargs):
        self.process_calls.append((args,kwargs));workspace=kwargs['cwd']
        (workspace/'run').mkdir()
        (workspace/'run/prepared.html').write_text('<section id="article">Prepared</section>',encoding='utf-8')
        (workspace/'run/preflight.json').write_text(json.dumps(dict(title_chars=15,digest_chars=16,image_count=0,
            remote_images_not_checked_offline=0,visual_verified=False,title='SENSITIVE-TITLE')),encoding='utf-8')
        return SimpleNamespace(returncode=0,stdout=b'PRIVATE-STDOUT',stderr=b'SECRET-STACK')

    async def tool_call(self,**changes):
        arguments=dict(skill_key='wechat-html-draft',revision=self.skill['id'],action='PREPARE',article=self.article)
        arguments.update(changes)
        ctx=SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers={
            'authorization':'Bearer '+self.bearer,'x-runtime-execution-scope':self.scope})))
        return await self.mcp._tool_manager.call_tool('skill_action_execute',arguments,context=ctx)

    def result(self,**changes):return asyncio.run(self.tool_call(**changes)).structuredContent

    def test_01_binding_correct_revision(self):
        result=self.result();self.assertEqual(result['status'],'completed')
        self.assertTrue(all(item[0]==self.skill['id'] for item in self.runtime_calls))

    def test_02_exact_revision_no_latest(self):
        result=self.result(revision='latest');self.assertEqual(result['error_code'],'SKILL_REVISION_NOT_FOUND')

    def test_03_wrong_revision_rejected(self):
        result=self.result(revision=str(uuid.uuid4()));self.assertEqual(result['error_code'],'SKILL_REVISION_NOT_FOUND')
        self.assertFalse(self.process_calls)

    def test_04_missing_runtime(self):
        self.runtime.resolve=lambda *args:(_ for _ in ()).throw(SkillRuntimeError())
        self.assertEqual(self.result()['error_code'],'SKILL_RUNTIME_NOT_READY')

    def test_05_runtime_lock_mismatch(self):
        with patch.object(self.runtime,'resolve',side_effect=SkillRuntimeError()):
            self.assertEqual(self.result()['error_code'],'SKILL_RUNTIME_NOT_READY')

    def test_06_prepare_permission(self):self.assertEqual(self.result()['status'],'completed')

    def test_07_create_without_credential_before_execution(self):
        self.make_task('创建公众号草稿')
        self.assertEqual(self.result(action='CREATE_DRAFT')['error_code'],'SKILL_ACTION_NOT_ALLOWED')
        self.assertFalse(self.runtime_calls);self.assertFalse(self.process_calls)

    def test_08_prepare_never_resolves_secret(self):
        with patch.object(self.permission.secrets,'resolve_wechat',side_effect=AssertionError('Secret lookup forbidden')):
            self.assertEqual(self.result()['status'],'completed')

    def test_09_workspace_scoped_to_task(self):
        self.result();workspace=self.process_calls[0][1]['cwd']
        self.assertIn(self.task['id'],workspace.parts);self.assertTrue((workspace/'article.json').is_file())
        self.assertTrue((workspace/'receipt.json').is_file())

    def test_10_skill_install_unchanged(self):
        before=file_tree_digest(self.skill_root);self.result();self.assertEqual(before,file_tree_digest(self.skill_root))

    def test_11_safe_argv(self):
        self.result();args,options=self.process_calls[0]
        self.assertFalse(options['shell']);self.assertIn('--check',args);self.assertNotIn('--run',args)
        self.assertEqual(args[0],str(self.root/'mock-runtime/venv/bin/python'))

    def test_12_no_path_option_injection(self):
        for key in ('executable','script','python_path','options','workspace'):
            result=self.result(article=dict(self.article,**{key:'/forbidden'}))
            self.assertEqual(result['error_code'],'SKILL_INPUT_INVALID')
        self.assertFalse(self.process_calls)

    def test_13_normalized_success(self):
        result=self.result();self.assertEqual(result['status'],'completed')
        raw=json.dumps(result)
        for value in ('PRIVATE-STDOUT','SECRET-STACK','SENSITIVE-TITLE',str(self.root)):self.assertNotIn(value,raw)
        self.assertEqual(len(result['artifact_refs']),2);self.assertTrue(result['verification']['offline'])

    def test_14_failed_exit_safe(self):
        self.dispatch.runner=lambda *args,**kw:SimpleNamespace(returncode=7,stdout=b'SECRET',stderr=b'Traceback SECRET')
        result=self.result();self.assertEqual(result['error_code'],'SKILL_EXECUTION_FAILED');self.assertNotIn('Traceback',json.dumps(result))

    def test_15_malformed_output(self):
        def wrong(args,**kwargs):
            result=self.child(args,**kwargs);(kwargs['cwd']/'run/preflight.json').write_text('{}');return result
        self.dispatch.runner=wrong;self.assertEqual(self.result()['error_code'],'SKILL_RESULT_INVALID')

    def test_16_receipt_audit_no_content(self):
        self.result()
        with self.store.connection() as conn:row=conn.execute("SELECT payload FROM execution_events WHERE event_type='skill.execution' ORDER BY id DESC").fetchone()
        data=json.loads(row['payload']);self.assertEqual(data['status'],'completed');self.assertEqual(data['revision'],self.skill['id'])
        for value in ('PRIVATE-ARTICLE-BODY','SENSITIVE-TITLE','PRIVATE-STDOUT','SECRET-STACK'):self.assertNotIn(value,row['payload'])

    def test_17_ordinary_chat_does_not_execute(self):
        self.make_task('你好，今天怎么样')
        self.assertEqual(self.result()['error_code'],'SKILL_ACTION_NOT_ALLOWED');self.assertFalse(self.process_calls)

    def test_18_real_task_agent_mcp_chain_model_mock(self):
        task=self.product.create_task('tenant-a',self.actor,self.agent,'排版公众号文章',None,
            _test_revision=self.agent_revision,_test_id=str(uuid.uuid4()))
        model=ModelMock(self);agents=AgentService(self.store,model,self.settings)
        asyncio.run(TaskService(self.product,agents).execute(task))
        completed=self.product.task_for_worker(task['id']);self.assertEqual(completed['status'],'completed')
        self.assertEqual(self.model_received['status'],'completed');self.assertTrue(self.runtime_calls)
        trace=self.store.run_trace(completed['run_id'],'tenant-a')
        self.assertTrue(trace['payload']['assistant_message_saved']);self.assertEqual(trace['payload']['mcp_calls'][0]['tool'],'skill_action_execute')

    def test_19_cross_tenant_task_scope(self):
        self.scope=self.issuer.issue_task_scope('tenant-b',self.task['id'])
        self.assertEqual(self.result()['error_code'],'SKILL_ACTION_NOT_ALLOWED');self.assertFalse(self.process_calls)

    def test_20_no_secret_proxy_env_in_child(self):
        with patch.dict(__import__('os').environ,{'WECHAT_APP_SECRET':'SYNTHETIC','HTTP_PROXY':'SYNTHETIC','PYTHONPATH':'SYNTHETIC'}):self.result()
        env=self.process_calls[0][1]['env']
        for key in ('WECHAT_APP_SECRET','HTTP_PROXY','PYTHONPATH'):self.assertNotIn(key,env)

    def test_21_cancellation_prevents_result_delivery(self):
        def cancelled(args,**kwargs):
            result=self.child(args,**kwargs);self.product.cancel_task(self.task['id'],'tenant-a',self.actor);return result
        self.dispatch.runner=cancelled
        self.assertEqual(self.result()['error_code'],'SKILL_ACTION_NOT_ALLOWED')

    def test_22_trace_argument_redaction(self):
        self.assertEqual(safe_arguments(dict(skill_key='wechat-html-draft',revision=self.skill['id'],action='PREPARE',article=self.article)),
            dict(skill_key_identity=__import__('hashlib').sha256(b'wechat-html-draft').hexdigest(),revision=self.skill['id'],action='PREPARE'))

    def test_23_actual_codex_runtime_installs_signed_header(self):
        p=profile(self.context);captured={}
        async def thread_start(**kwargs):captured.update(kwargs);return SimpleNamespace(id='mock-thread')
        async def get(profile):return SimpleNamespace(thread_start=thread_start)
        manager=SimpleNamespace(get=get,_token_issuer=self.issuer,_start_event=lambda *a,**k:None,
                                _paths=lambda profile:(self.root/'home',self.root/'workspace'))
        runtime=CodexRuntimeProvider(manager)
        asyncio.run(runtime.create_session(p,'Synthetic test',task_id=self.task['id']))
        header=captured['config']['mcp_servers']['platform']['http_headers']['X-Runtime-Execution-Scope']
        self.assertEqual(self.issuer.verify_task_scope(header,'tenant-a'),self.task['id'])

    def test_24_disabled_instance(self):
        with self.store.connection() as conn:conn.execute("UPDATE tenant_agent_instances SET status='disabled' WHERE agent_id=?",(self.agent,))
        self.assertEqual(self.result()['error_code'],'SKILL_ACTION_NOT_ALLOWED')

    def test_25_non_wechat_registration_reuses_bridge(self):
        # Reuse an actual second published Registry revision and exact binding.
        from app.bundled_skills import deterministic_zip
        content=deterministic_zip('mock-offline-skill','1.0.0',{'SKILL.md':(b'---\nname: mock-offline-skill\ndescription: Synthetic offline test\n---\n# Synthetic\n','100644'),
            'scripts/offline.py':(b'# Synthetic process boundary\n','100644')})
        item=self.registry.import_archive('mock-offline-skill','1.0.0','Mock','Synthetic',content,self.actor)
        self.registry.publish(item['id'],self.actor);item=self.registry.version(item['id'])
        self.catalog.bind_skills(self.agent,self.agent_revision,[dict(skill_id=self.skill['skill_id'],skill_version_id=self.skill['id']),dict(skill_id=item['skill_id'],skill_version_id=item['id'])],self.actor)
        self.catalog.validate(self.agent,self.agent_revision,self.actor);self.make_task()
        other_root=self.registry.published_root/'mock-offline-skill/1.0.0'
        runtime=SimpleNamespace(resolve=lambda revision,action:RuntimeEntry(self.root/'mock-runtime/python',other_root,self.root/'mock-runtime','2'*64))
        registration=ActionRegistration('mock-offline-skill','1.0.0',item['checksum'],'PREPARE','wechat:prepare',
            'scripts/offline.py',('--check',),WechatPrepareAdapter(),runtime,lambda *args:None)
        self.dispatch.registrations[(registration.skill_key,registration.version,registration.action)]=registration
        result=self.result(skill_key='mock-offline-skill',revision=item['id'])
        self.assertEqual(result['status'],'completed');self.assertTrue(self.process_calls[0][0][-5].endswith('offline.py'))

    def test_26_actual_provider_trace_omits_article(self):
        provider=CodexRuntimeProvider(SimpleNamespace())
        arguments=dict(skill_key='wechat-html-draft',revision=self.skill['id'],action='PREPARE',article=self.article)
        item=SimpleNamespace(tool='skill_action_execute',server='platform',status='completed',id='call',
            arguments=json.dumps(arguments),result=dict(status='completed',artifact_refs=[]),error=None)
        result=SimpleNamespace(items=[item],usage=None,final_response='Prepared',status='completed',error=None,duration_ms=1)
        turn=provider._runtime_turn_from_result(RuntimeSession('thread','profile'),profile(self.context),result)
        raw=json.dumps(turn.mcp_calls)
        for text in ('SENSITIVE-TITLE','PRIVATE-ARTICLE-BODY',self.article['assets']['cover.png']):self.assertNotIn(text,raw)

    def test_27_production_factory_is_disabled(self):
        from app.skill_dispatch_config import from_settings
        settings=SimpleNamespace(environment='production',skill_dispatch_config=Path('/must-not-read'))
        with self.assertRaises(SkillDispatchError):from_settings(self.store,self.issuer,settings)

    def test_28_unsafe_result_html_is_rejected(self):
        def wrong(args,**kwargs):
            result=self.child(args,**kwargs);(kwargs['cwd']/'run/prepared.html').write_text('<script>alert(1)</script>');return result
        self.dispatch.runner=wrong
        self.assertEqual(self.result()['error_code'],'SKILL_RESULT_INVALID')

    def test_29_writable_native_source_is_not_ready(self):
        from app.skill_dispatch_config import PythonRevisionBinding
        project=self.root/'sealed';(project/'integrations').mkdir(parents=True)
        (project/'skills/mock').mkdir(parents=True)
        (project/'integrations/wechat-html-draft.revision.v1.json').write_text(json.dumps(dict(source_root='skills/mock')))
        runtime=SimpleNamespace(project=project,resolve=lambda *args:Path('/synthetic/python'),
                                contract=lambda:({},None,None))
        with self.assertRaises(SkillDispatchError):PythonRevisionBinding(runtime).resolve(self.skill,'PREPARE')

    def test_30_generic_result_rejects_extra_stdout(self):
        adapter=self.dispatch.registrations[('wechat-html-draft','1.0.0','PREPARE')].adapter
        original=adapter.normalize
        with patch.object(adapter,'normalize',side_effect=lambda *args:dict(original(*args),stdout='PRIVATE-STDOUT')):
            self.assertEqual(self.result()['error_code'],'SKILL_RESULT_INVALID')

    def test_31_malformed_signed_task_header(self):
        self.scope='not-a-signed-task'
        self.assertEqual(self.result()['error_code'],'SKILL_ACTION_NOT_ALLOWED');self.assertFalse(self.process_calls)


if __name__=='__main__':unittest.main()
