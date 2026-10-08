"""Real synthetic Task/MCP/Registry gates; no Runtime Test, HTTP or Provider.

Only native authority loading, HTTP transport and Skill runtime/child I/O are
fixture boundaries. No qualification is installed on PRIMARY by these tests.
"""
import asyncio
import ast
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import uuid

import test_skill_dispatch as fixture
import test_controlled_skill_action as controlled_fixture
from app import controlled_skill_action as control
from app import skill_only_test_qualification as qualification
from app.auth import SessionIssuer, UserPrincipal
from app.agent_execution import profile
from app.service import AgentService
from app.product_service import TaskService


class QualificationTests(unittest.TestCase):
    create_mcp = fixture.DispatchTests.create_mcp
    child = fixture.DispatchTests.child
    tool_call = fixture.DispatchTests.tool_call
    make_task = lambda self: None  # Do not generate even a queued Runtime Test.

    @classmethod
    def setUpClass(cls):
        fixture.DispatchTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixture.DispatchTests.tearDownClass.__func__(cls)

    def setUp(self):
        fixture.DispatchTests.setUp(self)
        self.environment = patch.dict(os.environ, APP_ENV='test')
        self.environment.start()
        self.settings.model_provider_id = 'disabled-test-provider'
        self.settings.model_id = 'disabled-test-model'
        self.product.create_user('tenant-a', 'integrated-productization-admin-v1@example.invalid',
            'synthetic-unused-password-hash', '[INTERNAL TEST] qualification', 'member')
        self.admin = self.product.user_by_email('integrated-productization-admin-v1@example.invalid')
        # No platform_admin grant: native scoped execution is not an admin role.
        self.sessions = SessionIssuer('synthetic-local-session-signing-material')
        self.session = self.sessions.issue(UserPrincipal(self.admin['id'], 'tenant-a', 'member',
            self.sessions.credential_version(self.admin['password_hash'])))
        self.tasks = TaskService(self.product, AgentService(self.store, controlled_fixture.NoProvider(), self.settings))
        self.tester = controlled_fixture.tester_class()(self.resolver, self.product, self.tasks)
        self.tasks.runtime_test_lifecycle = self.tester
        self.approval = dict(contract=control.CONTRACT, environment='test', authority_id='synthetic-test-authority',
            fixture_identity='a'*64, source_commit='b'*40, source_tree='c'*40,
            tenant_id='tenant-a', user_id=self.admin['id'], user_email=self.admin['email'],
            actions=['wechat-html-draft:PREPARE', 'wechat-html-draft:CREATE_DRAFT'],
            mcp_url=control.MCP_URL, dispatch_source='b'*40, dispatch_tree='c'*40,
            dispatch_contract_sha256=control.DISPATCH_CONTRACT_SHA, provider_calls=0, image_calls=0, wechat_calls=0)
        with self.store.connection() as conn:
            version = self.catalog._version(conn, self.agent, self.agent_revision)
            self.approval['qualification'] = dict(contract=qualification.CONTRACT,
                eligibility_mode=qualification.MODE, environment='test', entry=control.CONTRACT,
                tenant_id='tenant-a', agent_id=self.agent, agent_revision_id=self.agent_revision,
                skill_key='wechat-html-draft', revision=self.skill['id'], action='PREPARE',
                configuration_fingerprint=version['configuration_fingerprint'],
                artifact_sha256=qualification.ARTIFACT, runtime_lock_sha256=qualification.LOCK,
                source_commit='b'*40, source_tree='c'*40, authority_id=self.approval['authority_id'],
                provider_calls=0, image_calls=0, wechat_calls=0)
        self.native = patch.object(control, 'load_approval', lambda: (self.approval, 'e'*64))
        self.native.start()
        self.entry = control.ControlledSkillActionEntry(self.tester, self.sessions, self.issuer)
        self.tester.controlled_skill_actions = self.entry
        self.tester.isolation_guard = lambda: dict(status='PASS', environment='test', contract=control.CONTRACT,
            fixture_identity='a'*64, api_source='b'*40, api_tree='c'*40, worker_source='b'*40,
            worker_tree='c'*40, mcp_source='b'*40, mcp_tree='c'*40, observed_at=__import__('time').time())
        self.dispatch.source_identity = dict(source_commit='b'*40, source_tree='c'*40)
        async def call(client, bearer, task_scope, arguments):
            self.bearer, self.scope = bearer, task_scope
            return await self.tool_call(**arguments)
        self.transport = patch.object(control.PlatformSkillActionClient, 'call', call)
        self.transport.start()
        self.request = dict(tenant_id='tenant-a', agent_id=self.agent, skill_key='wechat-html-draft',
            revision=self.skill['id'], action='PREPARE', input=self.article)

    def tearDown(self):
        self.transport.stop()
        self.native.stop()
        self.environment.stop()
        fixture.DispatchTests.tearDown(self)

    def execute(self, **changes):
        return asyncio.run(self.tester.run_controlled_skill_action(self.session, {**self.request, **changes}))

    def context(self, result):
        task = self.product.task_for_worker(result['task_id'])
        with self.store.connection() as conn:
            return self.resolver.task_context(conn, task)

    def test_01_unqualified_runtime_prepare_completed(self):
        result = self.execute()
        self.assertEqual(result['status'], 'completed')
        trace = self.store.run_trace(result['run_id'], 'tenant-a')
        self.assertEqual(trace['status'], 'completed')
        audit = trace['payload']['controlled_action_audit']
        self.assertEqual(audit['eligibility_mode'], qualification.MODE)
        self.assertEqual(audit['revision'], self.skill['id'])
        self.assertEqual(audit['provider_calls'], 0)
        self.assertIsNone(trace['payload']['model_provider'])
        self.assertIsNone(trace['payload']['codex_thread_id'])
        self.assertTrue(trace['payload']['assistant_message_saved'])
        with self.store.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM agent_template_tests WHERE test_type='runtime'").fetchone()['n'], 0)
            self.assertFalse(self.resolver._runtime_passed(conn, self.catalog._version(conn, self.agent, self.agent_revision)))
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM platform_admins').fetchone()['n'], 0)

    def test_02_missing_qualification_not_runtime_pass(self):
        del self.approval['qualification']
        self.approval.update(dispatch_source=control.DISPATCH_SOURCE, dispatch_tree=control.DISPATCH_TREE)
        with self.assertRaises(PermissionError):
            self.execute()
        self.assertFalse(self.process_calls)

    def test_03_production_hard_reject(self):
        self.settings.environment = 'production'
        with self.assertRaisesRegex(control.ControlledActionError, control.NOT_ALLOWED):
            self.execute()
        self.assertFalse(self.process_calls)

    def test_04_ordinary_chat_still_rejected(self):
        with self.assertRaises((LookupError, PermissionError)):
            self.product.create_task('tenant-a', self.admin['id'], self.agent, 'Hello', None)
        self.assertFalse(self.process_calls)

    def test_05_qualified_profile_cannot_enter_codex(self):
        result = self.execute()
        with self.assertRaisesRegex(PermissionError, control.ZERO_PROVIDER):
            profile(self.context(result))

    def test_06_agent_model_run_without_ticket_rejected(self):
        result = self.execute()
        with self.assertRaisesRegex(PermissionError, control.ZERO_PROVIDER):
            asyncio.run(self.tasks._agents.run('tenant-a', self.agent, 'Hello', execution_context=self.context(result)))

    def test_07_wrong_agent_rejected(self):
        with self.assertRaises(PermissionError):
            self.execute(agent_id=str(uuid.uuid4()))

    def test_08_wrong_skill_rejected(self):
        with self.assertRaises(PermissionError):
            self.execute(skill_key='other-skill')

    def test_09_wrong_revision_rejected(self):
        with self.assertRaises(PermissionError):
            self.execute(revision=str(uuid.uuid4()))

    def test_10_draft_missing_credential_negative_only(self):
        result = self.execute(action='CREATE_DRAFT')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'SKILL_ACTION_NOT_ALLOWED')
        self.assertFalse(self.process_calls)
        with self.store.connection() as conn:
            events = conn.execute("SELECT payload FROM execution_events WHERE event_type='skill.execution'").fetchall()
        self.assertEqual(json.loads(events[-1]['payload'])['failure_phase'], 'action_permission')

    def test_11_request_boolean_not_authority(self):
        with self.assertRaises(PermissionError):
            self.execute(skill_only_test_qualified=True)

    def test_12_source_mismatch_rejected(self):
        self.approval['qualification']['source_commit'] = 'f'*40
        with self.assertRaises(PermissionError):
            self.execute()

    def test_13_zero_provider_flag_required(self):
        self.approval['qualification']['provider_calls'] = 1
        with self.assertRaises(PermissionError):
            self.execute()

    def test_14_lifecycle_durable_receipt(self):
        result = self.execute()
        with self.store.connection() as conn:
            row = conn.execute("SELECT payload FROM execution_events WHERE event_type='skill.controlled_action'").fetchone()
        receipt = json.loads(row['payload'])
        for key in ('environment','agent_id','skill_key','revision','action','controlled_source','authority_id','task_id','run_id'):
            self.assertIn(key, receipt)
        self.assertEqual(receipt['eligibility_mode'], qualification.MODE)
        self.assertEqual(receipt['run_id'], result['run_id'])

    def test_15_other_mcp_scope_rejected(self):
        result = self.execute()
        with self.store.connection() as conn:
            with self.assertRaises(PermissionError):
                qualification.authorize_scope(conn, self.context(result), 'knowledge:search')

    def test_16_wrong_fingerprint_rejected(self):
        self.approval['qualification']['configuration_fingerprint'] = 'f'*64
        with self.assertRaises(PermissionError):
            self.execute()

    def test_17_wrong_entry_rejected(self):
        self.approval['qualification']['entry'] = 'ordinary_chat'
        with self.assertRaises(PermissionError):
            self.execute()

    def test_18_wrong_lock_rejected(self):
        self.approval['qualification']['runtime_lock_sha256'] = 'f'*64
        with self.assertRaises(PermissionError):
            self.execute()

    def test_19_old_mcp_source_rejected_for_new_qualification(self):
        self.approval.update(dispatch_source=control.DISPATCH_SOURCE, dispatch_tree=control.DISPATCH_TREE)
        with self.assertRaises(PermissionError):
            self.execute()

    def test_20_disabled_member_rejected(self):
        self.product.update_member_status('tenant-a', self.admin['id'], 'disabled')
        with self.assertRaises(PermissionError):
            self.execute()

    def test_21_forged_internal_proof_denied(self):
        with self.assertRaises(PermissionError):
            self.product.create_task('tenant-a', self.admin['id'], self.agent, 'Hello'+control.TASK_MARKER, None,
                _controlled_action=True, _controlled_qualification=True)

    def test_22_runtime_test_flag_stays_false(self):
        policy = json.loads(self.context(self.execute())['tool_policy_snapshot'])
        self.assertIs(policy['runtime_test'], False)
        self.assertIs(policy['model_execution_disabled'], True)
        self.assertEqual(policy['eligibility_mode'], qualification.MODE)

    def test_23_non_test_permission_consumer_rejected(self):
        context = self.context(self.execute())
        with self.store.connection() as conn, self.assertRaises(PermissionError):
            qualification.check_context(conn, context, environment='production')

    def test_24_changed_authority_revokes_context(self):
        context = self.context(self.execute())
        self.approval['qualification']['revision'] = str(uuid.uuid4())
        with self.store.connection() as conn, self.assertRaises(PermissionError):
            qualification.check_context(conn, context, environment='test')

    def test_25_runtime_pass_predicate_unchanged(self):
        root = Path(__file__).resolve().parents[2]
        base = subprocess.check_output(['git','show','0bfded6ee6837e8c0908721d8ffab1035506e133:enterprise_agent_poc/app/agent_execution.py'],cwd=root).decode()
        current = (root/'enterprise_agent_poc/app/agent_execution.py').read_text(encoding='utf-8')
        def predicate(raw):
            node = next(n for n in ast.walk(ast.parse(raw)) if isinstance(n,ast.FunctionDef) and n.name=='_runtime_passed')
            return ast.dump(node,include_attributes=False)
        self.assertEqual(predicate(base),predicate(current))

    def test_26_runtime_control_and_skill_files_unchanged(self):
        root = Path(__file__).resolve().parents[2]
        for name in ('app/agent_runtime_test.py','app/runtime/codex_provider.py',
                     'app/controlled_skill_action.py','app/skill_only_test_qualification.py',
                     'app/skill_python_runtime.py',
                     'integrations/wechat-python311-linux.v1.lock.json'):
            # Current successor explicitly extends Secret + connected gates;
            # qualification/runtime/control and the immutable Skill remain frozen.
            base = subprocess.check_output(['git','show','dca318de578a9b9601ad036e1b176bc5ab702029:enterprise_agent_poc/'+name],cwd=root)
            self.assertEqual(base,(root/'enterprise_agent_poc'/name).read_bytes(),name)
        # Within wechat_skill.py the earlier successor was authorized to change
        # ONLY Revision collection/verification. This Secret successor must not
        # alter its business/permission/binding logic either.
        # Preserve a full-module AST invariant outside those two functions,
        # rather than dropping the Skill protection or reapproving all changes.
        name = 'app/wechat_skill.py'
        base = subprocess.check_output(['git','show','4ca21336bdd17a507d819457c2fc41c5e410846b:enterprise_agent_poc/'+name],cwd=root)
        current = (root/'enterprise_agent_poc'/name).read_bytes()
        def business(raw):
            tree = ast.parse(raw)
            tree.body = [node for node in tree.body if not (
                isinstance(node,ast.FunctionDef) and node.name in {'source_files','verify_revision_contract'})]
            return ast.dump(tree,include_attributes=False)
        self.assertEqual(business(base),business(current),'Only canonical identity functions may change')

    def test_27_qualified_authority_cannot_use_normal_context(self):
        result = self.execute()
        context = self.context(result)
        policy = json.loads(context['tool_policy_snapshot'])
        del policy['eligibility_mode']
        context['tool_policy_snapshot'] = json.dumps(policy)
        # A mismatched context must fail, even before actual dispatch.
        from app.controlled_skill_action import _Ticket
        trace = self.store.run_trace(result['run_id'], 'tenant-a')
        ticket = _Ticket(self.entry, result['task_id'], json.dumps(self.request),
            json.dumps(trace['payload']['controlled_action_audit']), self.session)
        with self.assertRaises(PermissionError):
            self.entry.check_ticket(ticket, result['task_id'], context)
