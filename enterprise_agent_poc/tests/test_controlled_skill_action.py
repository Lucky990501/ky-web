"""Offline synthetic Tasks + real services/MCP; no secrets, HTTP or Provider.

Historical Agent eligibility is created by an explicitly mocked Codex turn in
the fixture. Controlled actions themselves have an explosive RuntimeProvider.
No fixture is installed on PRIMARY and no real PREPARE process is executed.
"""
import ast
import asyncio
import json
import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

# Import first: substitutes Settings BEFORE any app/.env imports.
import test_skill_dispatch as fixture
from app.auth import SessionIssuer, UserPrincipal
from app.agent_execution import completion_evidence
from app.agent_productization import AgentCatalogError, canonical
from app.controlled_skill_action import (
    ControlledSkillActionEntry, ControlledActionError, PlatformSkillActionClient,
    validate_approval, load_approval, NOT_ALLOWED, AUTH_BLOCKED, ZERO_PROVIDER,
    CONTRACT, TASK_MARKER, DISPATCH_SOURCE, DISPATCH_TREE, DISPATCH_CONTRACT_SHA, MCP_URL,
)
from app.product_service import TaskService
from app.service import AgentService
from app.domain import RuntimeTurn


def tester_class():
    # Actual orchestration class, without unrelated provider/DOCX import graph.
    path = fixture.wechat_skill.SOURCE.parents[2] / 'app/agent_runtime_test.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AgentRuntimeTest']
    ns = dict(json=json, os=os, uuid4=uuid.uuid4, canonical=canonical,
              AgentCatalogError=AgentCatalogError, completion_evidence=completion_evidence,
              CodexRuntimeProvider=fixture.CodexRuntimeProvider)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    return ns['AgentRuntimeTest']


class NoProvider:
    def __getattribute__(self, name):
        raise AssertionError('ZERO_PROVIDER_CONTRACT_VIOLATION: ' + name)


class HistoricalMock(fixture.ModelMock):
    async def run_turn(self, session, message):
        turn = await super().run_turn(session, message)
        return RuntimeTurn(session.thread_id, turn.text, mcp_calls=turn.mcp_calls,
            lifecycle_events=(dict(event='skill_discovered', skill='wechat-html-draft'),), status='completed')


class ControlledTests(unittest.TestCase):
    create_mcp = fixture.DispatchTests.create_mcp
    make_task = fixture.DispatchTests.make_task
    child = fixture.DispatchTests.child
    tool_call = fixture.DispatchTests.tool_call

    @classmethod
    def setUpClass(cls):
        fixture.DispatchTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        fixture.DispatchTests.tearDownClass.__func__(cls)

    def setUp(self):
        fixture.DispatchTests.setUp(self)
        # Formally persist synthetic historical runtime evidence, then use
        # ordinary publication/instance gates. Never weaken them for this entry.
        self.historical = self.product.create_task('tenant-a', self.actor, self.agent,
            '请排版公众号文章', None, _test_revision=self.agent_revision, _test_id=str(uuid.uuid4()))
        historical_agents = AgentService(self.store, HistoricalMock(self), self.settings)
        historical_tasks = TaskService(self.product, historical_agents)
        cls = tester_class()
        historical_tester = cls(self.resolver, self.product, historical_tasks)
        historical_tasks.runtime_test_lifecycle = historical_tester
        asyncio.run(historical_tasks.execute(self.historical))
        self.catalog.publish(self.agent, self.agent_revision, self.actor, 'local_test')
        self.catalog.configure_instance(self.agent, 'tenant-a', self.agent_revision, {})
        self.catalog.set_instance_status(self.agent, 'tenant-a', 'enabled')
        # Test-only synthetic admin via existing user/grant logic, no SQL role
        # elevation. Password is deliberately not a real credential.
        self.product.create_user('tenant-a', 'integrated-productization-admin-v1@example.invalid',
                                 'synthetic-unused-password-hash', '[INTERNAL TEST] fixture', 'member')
        self.admin = self.product.user_by_email('integrated-productization-admin-v1@example.invalid')
        self.registry.grant_platform_admin(self.admin['id'])
        self.sessions = SessionIssuer('synthetic-local-session-signing-material')
        self.session = self.sessions.issue(UserPrincipal(self.admin['id'], 'tenant-a', 'member',
            self.sessions.credential_version(self.admin['password_hash'])))
        self.tasks = TaskService(self.product, AgentService(self.store, NoProvider(), self.settings))
        self.tester = cls(self.resolver, self.product, self.tasks)
        self.guard_calls = 0
        def guard():
            self.guard_calls += 1
            return dict(status='PASS', environment='test', contract=CONTRACT, fixture_identity='a'*64,
                api_source='b'*40, api_tree='c'*40, worker_source='b'*40, worker_tree='c'*40,
                mcp_source=DISPATCH_SOURCE, mcp_tree=DISPATCH_TREE, observed_at=time.time())
        self.tester.isolation_guard = guard
        self.entry = ControlledSkillActionEntry(self.tester, self.sessions, self.issuer)
        self.tester.controlled_skill_actions = self.entry
        self.tasks.runtime_test_lifecycle = self.tester
        self.approval = dict(contract=CONTRACT, environment='test', authority_id='synthetic-test-authority',
            fixture_identity='a'*64, source_commit='b'*40, source_tree='c'*40,
            tenant_id='tenant-a', user_id=self.admin['id'], user_email=self.admin['email'],
            actions=['wechat-html-draft:PREPARE', 'wechat-html-draft:CREATE_DRAFT'],
            mcp_url=MCP_URL, dispatch_source=DISPATCH_SOURCE,
            dispatch_tree=DISPATCH_TREE, dispatch_contract_sha256=DISPATCH_CONTRACT_SHA,
            provider_calls=0, image_calls=0, wechat_calls=0)
        self.entry._approval_loader = lambda: (self.approval, 'e'*64)
        # Real FastMCP tool registration + existing service/dispatcher, replacing
        # only the transport boundary; runtime/process were mocked by fixture.
        self.dispatch.source_identity = dict(source_commit=DISPATCH_SOURCE, source_tree=DISPATCH_TREE)
        async def call(client, bearer, task_scope, arguments):
            self.bearer, self.scope = bearer, task_scope
            return await self.tool_call(**arguments)
        self.transport = patch.object(PlatformSkillActionClient, 'call', call)
        self.transport.start()
        self.process_calls.clear(); self.runtime_calls.clear()
        self.request = dict(tenant_id='tenant-a', agent_id=self.agent, skill_key='wechat-html-draft',
                            revision=self.skill['id'], action='PREPARE', input=self.article)

    def tearDown(self):
        self.transport.stop()
        fixture.DispatchTests.tearDown(self)

    def execute(self, **changes):
        return asyncio.run(self.tester.run_controlled_skill_action(self.session, {**self.request, **changes}))

    def trace(self, result):
        return self.store.run_trace(result['run_id'], 'tenant-a')

    def count_tasks(self):
        with self.store.connection() as conn:
            return conn.execute('SELECT COUNT(*) AS n FROM tasks').fetchone()['n']

    def test_01_test_binding_revision_prepare(self):
        result = self.execute()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['result']['action'], 'PREPARE')
        self.assertTrue(self.runtime_calls)
        self.assertGreaterEqual(self.guard_calls, 2)

    def test_02_production_deny_before_mutation(self):
        before = self.count_tasks(); self.settings.environment = 'production'
        with self.assertRaisesRegex(ControlledActionError, NOT_ALLOWED): self.execute()
        self.assertEqual(before, self.count_tasks()); self.assertFalse(self.process_calls)

    def test_03_missing_binding_fails_both(self):
        # Configuration drift after context creation, before MCP execution.
        original = self.entry.dispatch
        async def changed(*args):
            with self.store.connection() as conn:
                conn.execute('DELETE FROM agent_template_version_skills WHERE agent_template_version_id=?', (self.agent_revision,))
            return await original(*args)
        with patch.object(self.entry, 'dispatch', changed): result = self.execute()
        self.assertEqual(result['status'], 'failed'); self.assertFalse(self.process_calls)
        self.assertEqual(self.trace(result)['status'], 'failed')

    def test_04_missing_revision(self):
        result = self.execute(revision=str(uuid.uuid4()))
        self.assertEqual(result['error_code'], 'SKILL_REVISION_NOT_FOUND')
        self.assertEqual(result['status'], 'failed'); self.assertFalse(self.process_calls)

    def test_05_package_identity_mismatch(self):
        original = self.entry.dispatch
        async def changed(*args):
            with self.store.connection() as conn:
                conn.execute('UPDATE skill_packages SET sha256=? WHERE skill_version_id=?', ('f'*64, self.skill['id']))
            return await original(*args)
        with patch.object(self.entry, 'dispatch', changed): result = self.execute()
        self.assertEqual(result['error_code'], 'SKILL_REVISION_NOT_FOUND'); self.assertFalse(self.process_calls)

    def test_06_runtime_not_ready(self):
        with patch.object(self.runtime, 'resolve', side_effect=fixture.SkillRuntimeError()): result = self.execute()
        self.assertEqual(result['error_code'], 'SKILL_RUNTIME_NOT_READY')
        self.assertFalse(self.process_calls)

    def test_07_prepare_no_secret_resolution(self):
        with patch.object(self.permission.secrets, 'resolve_wechat', side_effect=AssertionError('No secret allowed')):
            self.assertEqual(self.execute()['status'], 'completed')

    def test_08_create_draft_original_credential_gate(self):
        result = self.execute(action='CREATE_DRAFT')
        self.assertEqual(result['status'], 'failed'); self.assertEqual(result['error_code'], 'SKILL_ACTION_NOT_ALLOWED')
        self.assertFalse(self.process_calls); self.assertFalse(self.runtime_calls)
        with self.store.connection() as conn:
            receipts = [json.loads(row['payload']) for row in conn.execute("SELECT payload FROM execution_events WHERE event_type='wechat.action.permission'")]
        self.assertTrue(any(row.get('action') == 'CREATE_DRAFT' and row.get('execution_status') == 'BLOCKED_NOT_EXECUTED' for row in receipts))

    def test_09_no_runtime_provider_access(self):
        result = self.execute(); payload = self.trace(result)['payload']
        self.assertEqual(result['provider_calls'], 0)
        self.assertEqual(payload['provider_calls'], 0); self.assertIsNone(payload['model'])
        self.assertIsNone(payload['codex_thread_id'])
        self.assertIsNone(self.trace(result)['codex_thread_id'])

    def test_10_real_task_run_context_association(self):
        result = self.execute()
        with self.store.connection() as conn:
            row = conn.execute('SELECT t.run_id,c.agent_id FROM tasks t JOIN task_agent_contexts m ON m.task_id=t.id JOIN agent_execution_contexts c ON c.id=m.context_id WHERE t.id=?', (result['task_id'],)).fetchone()
        self.assertEqual(row['run_id'], result['run_id']); self.assertEqual(row['agent_id'], self.agent)

    def test_11_success_persisted_without_quality_promotion(self):
        result = self.execute(); trace = self.trace(result)
        self.assertEqual(trace['status'], 'completed'); self.assertTrue(trace['payload']['assistant_message_saved'])
        with self.store.connection() as conn:
            self.assertIsNone(conn.execute('SELECT id FROM agent_template_tests WHERE task_id=?', (result['task_id'],)).fetchone())

    def test_12_process_failure_failed_safe_diagnostic(self):
        self.dispatch.runner = lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=b'PRIVATE', stderr=b'SECRET')
        result = self.execute(); trace = self.trace(result)
        self.assertEqual(result['status'], 'failed'); self.assertEqual(trace['status'], 'failed')
        self.assertEqual(trace['payload']['error'], 'SKILL_EXECUTION_FAILED')
        self.assertNotIn('SECRET', json.dumps(trace))

    def test_13_normalized_result_formal_task_result(self):
        result = self.execute()
        with self.store.connection() as conn:
            saved = conn.execute('SELECT final_response FROM task_results WHERE task_id=?', (result['task_id'],)).fetchone()
        self.assertEqual(json.loads(saved['final_response']), result['result'])
        self.assertEqual(set(result['result']), {'status','action','artifact_refs','summary','verification','receipt_ref'})

    def test_14_audit_and_receipt(self):
        result = self.execute()
        with self.store.connection() as conn:
            events = [json.loads(r['payload']) for r in conn.execute("SELECT payload FROM execution_events WHERE event_type='skill.controlled_action'")]
        self.assertEqual(events[-1]['task_id'], result['task_id'])
        self.assertTrue(events[-1]['CONTROLLED_TEST_ACTION']); self.assertEqual(events[-1]['provider_calls'], 0)
        self.assertEqual(events[-1]['caller_id'], self.admin['id']); self.assertEqual(events[-1]['runtime_identity'], '1'*64)
        self.assertNotIn('PRIVATE-ARTICLE-BODY', json.dumps(events)); self.assertNotIn(self.session, json.dumps(events))

    def test_15_input_path_selectors_rejected_before_task(self):
        before = self.count_tasks()
        for name in ('python', 'script', 'skill_path', 'argv', 'env'):
            with self.assertRaisesRegex(ControlledActionError, 'SKILL_INPUT_INVALID'):
                self.execute(input={**self.article, name: 'C:/evil'})
        self.assertEqual(before, self.count_tasks()); self.assertFalse(self.process_calls)

    def test_16_ordinary_chat_still_uses_existing_model_path(self):
        mock = HistoricalMock(self)
        tasks = TaskService(self.product, AgentService(self.store, mock, self.settings))
        task = self.product.create_task('tenant-a', self.actor, self.agent, '请排版公众号文章', None)
        asyncio.run(tasks.execute(task))
        self.assertEqual(self.product.task_for_worker(task['id'])['status'], 'completed')
        self.assertFalse(self.store.run_trace(self.product.task_for_worker(task['id'])['run_id'], 'tenant-a')['payload'].get('CONTROLLED_TEST_ACTION', False))

    def test_17_member_denied_before_task(self):
        user = self.product.user_by_email('dispatch@example.invalid')
        self.session = self.sessions.issue(UserPrincipal(self.actor, 'tenant-a', 'member', self.sessions.credential_version(user['password_hash'])))
        before = self.count_tasks()
        with self.assertRaisesRegex(ControlledActionError, AUTH_BLOCKED): self.execute()
        self.assertEqual(before, self.count_tasks())

    def test_18_second_unknown_admin_fail_closed(self):
        self.registry.grant_platform_admin(self.actor)
        with self.assertRaisesRegex(ControlledActionError, AUTH_BLOCKED): self.execute()

    def test_19_missing_authority_guard(self):
        self.tester.isolation_guard = None
        with self.assertRaisesRegex(ControlledActionError, AUTH_BLOCKED): self.execute()

    def test_20_exact_revision_no_latest_or_paths(self):
        for revision in ('latest', 'draft', '/tmp/skill', 'not-a-uuid'):
            with self.assertRaisesRegex(ControlledActionError, 'SKILL_INPUT_INVALID'): self.execute(revision=revision)

    def test_21_foreign_tenant_denied(self):
        with self.assertRaisesRegex(ControlledActionError, AUTH_BLOCKED): self.execute(tenant_id='tenant-b')

    def test_22_approval_cannot_enable_model_or_external_endpoint(self):
        for change in (dict(provider_calls=1), dict(mcp_url='http://example.invalid/mcp'), dict(dispatch_source='f'*40)):
            with self.assertRaisesRegex(ControlledActionError, AUTH_BLOCKED): validate_approval({**self.approval, **change})

    def test_23_wrong_client_never_model(self):
        self.entry._client = NoProvider()
        with self.assertRaisesRegex(ControlledActionError, ZERO_PROVIDER): self.execute()

    def test_24_ticket_lost_worker_rejects_no_model(self):
        task = self.product.create_task('tenant-a', self.admin['id'], self.agent,
                                       '请排版公众号文章' + TASK_MARKER, None, _controlled_action=True)
        self.assertEqual(task['status'], 'running'); self.assertEqual(task['stage'], 'controlled_action_reserved')
        recovered = next(row for row in self.product.recoverable_tasks() if row['id'] == task['id'])
        self.assertEqual(recovered['status'], 'queued')
        asyncio.run(self.tasks.execute(recovered))
        saved = self.product.task_for_worker(task['id'])
        self.assertEqual(saved['status'], 'failed'); self.assertEqual(saved['error_code'], NOT_ALLOWED)
        self.assertFalse(self.process_calls)

    def test_25_missing_native_approval_is_not_implicitly_authorized(self):
        # Windows can never be mistaken for live native approval installation.
        with self.assertRaisesRegex(ControlledActionError, AUTH_BLOCKED): load_approval()

    def test_26_no_fake_runtime_quality_for_disabled_agent(self):
        self.catalog.set_instance_status(self.agent, 'tenant-a', 'disabled')
        before = self.count_tasks()
        with self.assertRaises((LookupError, AgentCatalogError)): self.execute()
        self.assertEqual(before, self.count_tasks()); self.assertFalse(self.process_calls)

    def test_27_transport_error_failed_without_leak(self):
        async def broken(*args): raise RuntimeError('TOKEN_SECRET /private/absolute/path')
        with patch.object(PlatformSkillActionClient, 'call', broken): result = self.execute()
        self.assertEqual(result['status'], 'failed'); self.assertNotIn('TOKEN_SECRET', json.dumps(self.trace(result)))

    def test_28_revoked_session_denied_before_task(self):
        self.session = self.sessions.issue(UserPrincipal(self.admin['id'], 'tenant-a', 'member', 'obsolete'))
        before = self.count_tasks()
        with self.assertRaisesRegex(ControlledActionError, AUTH_BLOCKED): self.execute()
        self.assertEqual(before, self.count_tasks())

    def test_29_fake_mcp_result_cannot_replace_receipt(self):
        async def forged(*args):
            return SimpleNamespace(isError=False, structuredContent=dict(status='completed', action='PREPARE',
                artifact_refs=[], summary='PRIVATE-STDOUT', verification={}, receipt_ref='skill-receipt:'+str(uuid.uuid4())))
        with patch.object(PlatformSkillActionClient, 'call', forged): result = self.execute()
        self.assertEqual(result['status'], 'failed'); self.assertNotIn('PRIVATE-STDOUT', json.dumps(self.trace(result)))

    def test_30_completed_duplicate_never_calls_dispatch_again(self):
        result = self.execute(); count = len(self.process_calls)
        asyncio.run(self.tasks.execute(self.product.task_for_worker(result['task_id'])))
        self.assertEqual(count, len(self.process_calls)); self.assertEqual(self.trace(result)['status'], 'completed')

    def test_31_other_required_tools_not_fabricated(self):
        # Required policy snapshot is immutable. Exercise the bridge gate by
        # adding a requirement to the derived profile, not DB/authorization.
        from dataclasses import replace
        from app.agent_execution import profile
        def additional(context): return replace(profile(context), required_tools=('knowledge_search',))
        with patch('app.agent_execution.profile', additional): result = self.execute()
        self.assertEqual(result['status'], 'failed'); self.assertEqual(result['error_code'], NOT_ALLOWED)
        self.assertFalse(self.process_calls)

    def test_32_binding_permission_revoked_before_dispatch(self):
        original = self.entry.dispatch
        async def revoked(*args):
            self.catalog.set_instance_status(self.agent, 'tenant-a', 'disabled')
            return await original(*args)
        with patch.object(self.entry, 'dispatch', revoked): result = self.execute()
        self.assertEqual(result['status'], 'failed'); self.assertFalse(self.process_calls)

    def test_33_stale_mixed_or_legacy_authority_fail_closed(self):
        valid = self.tester.isolation_guard()
        for value in (None, {**valid, 'observed_at': time.time()-60}, {**valid, 'worker_source': DISPATCH_SOURCE},
                      {**valid, 'fixture_identity': 'f'*64}, {**valid, 'mcp_source': 'f'*40}):
            self.tester.isolation_guard = lambda: value
            before = self.count_tasks()
            with self.assertRaisesRegex(ControlledActionError, AUTH_BLOCKED): self.execute()
            self.assertEqual(before, self.count_tasks())


if __name__ == '__main__': unittest.main()
