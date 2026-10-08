"""Formal d8a API/executor/queue/persistence code, isolated DB + Runtime double.

No hand-inserted positive tests, no manually written PASS, no actual Provider,
native service, live schema claim or formal-admin grant/revoke qualification.
"""
import ast
import asyncio
import copy
import json
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import py_compile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_skill_dispatch as f  # Settings + unrelated DOCX stub BEFORE imports.
from app.agent_runtime_test import AgentRuntimeTest
from app.runtime.codex_provider import CodexRuntimeProvider
from app.agent_catalog_api import catalog_router
from app.agent_productization import AgentCatalogError
from app.auth import AuthenticationError, SessionIssuer, UserPrincipal
from fastapi import Cookie, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from scripts import wechat_runtime_test_lifecycle_guard as g
from scripts import wechat_installed_source_cache_guard as cache_guard


class RuntimeDouble(f.RuntimeProvider):
    def __init__(self):
        self.profiles = {}; self.turns = 0; self.fail = False

    async def create_session(self, profile, developer_instructions, *, task_id=None):
        thread = str(uuid4()); self.profiles[thread] = profile
        return f.RuntimeSession(thread, profile.id)

    async def resume_session(self, profile, thread_id, **kwargs):
        self.profiles[thread_id] = profile
        return f.RuntimeSession(thread_id, profile.id)

    def startup_events(self, profile):
        return tuple({'event': 'skill_discovered', 'skill': slug} for slug in profile.skill_manifest)

    async def run_turn(self, session, message):
        self.turns += 1
        if self.fail:
            raise RuntimeError('synthetic runtime failure')
        return f.RuntimeTurn(session.thread_id, '隔离 Runtime double 文案，不是真实模型质量证据。')

    async def close(self):
        pass


class LifecycleTests(unittest.TestCase):
    setUpClass = classmethod(f.DispatchTests.setUpClass.__func__)
    tearDownClass = classmethod(f.DispatchTests.tearDownClass.__func__)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wechat-runtime-lifecycle-fixture-')
        self.root = Path(self.temp.name)
        (self.root/'test-only-fixture.marker').write_text('wechat-runtime-test-offline-v1')
        shutil.copyfile(self.seed.database_path, self.root / 'fixture.db')
        self.store = f.POCStore(self.root / 'fixture.db'); self.product = f.ProductStore(self.store)
        self.catalog = f.AgentProductization(self.store)
        self.registry = f.SkillRegistry(self.store, self.__class__.registry.data_root,
            self.__class__.registry.bundled_root)
        self.settings = SimpleNamespace(environment='test', model_provider_id='deepseek',
            model_id='deepseek-v4-pro', reasoning_effort='high', data_dir=self.root/'data')
        self.resolver = f.ExecutionResolver(self.store, self.registry, self.catalog, self.settings, 'tenant-a')
        self.catalog.execution_resolver = self.product.execution_resolver = self.resolver
        self.runtime = RuntimeDouble()
        self.tasks = f.TaskService(self.product, f.AgentService(self.store, self.runtime, self.settings))
        self.tester = AgentRuntimeTest(self.resolver, self.product, self.tasks)
        self.jobs = []; self.tester.enqueue = self.jobs.append
        self.tester.isolation_guard = self.admission
        self.catalog.runtime_tester = self.tasks.runtime_test_lifecycle = self.tester
        # Existing formal registry Grant is used ONLY to set up the local API
        # fixture. It has no audit/Revoke API and is NOT a qualified live lease.
        self.registry.grant_platform_admin(self.actor)
        user = next(r for r in self.rows('users') if r['id'] == self.actor)
        revision = next(r for r in self.rows('agent_template_versions') if r['id'] == self.agent_revision)
        self.scope = dict(contract=g.VERSION, environment='test', source=g.SOURCE, tree=g.TREE,
            tenant_id='tenant-a', agent_id=self.agent, agent_slug='wechat-official-account-writing',
            revision_id=self.agent_revision, fingerprint=revision['configuration_fingerprint'],
            actor_id=self.actor, actor_sha256=g.digest(user), maximum_runtime_tests=8,
            publication_allowed=False, skill_id=self.skill['skill_id'], skill_revision_id=self.skill['id'],
            skill_sha256=self.skill['checksum'], model_config_id='codex-deepseek-v4-pro-high')
        self.sessions = SessionIssuer('synthetic-offline-session-key-only')
        self.token = self.sessions.issue(UserPrincipal(self.actor, 'tenant-a', 'member',
            self.sessions.credential_version(user['password_hash'])))
        main = Path(f.wechat_skill.__file__).parent / 'main.py'
        nodes = [n for n in ast.parse(main.read_bytes()).body if isinstance(n, ast.FunctionDef)
            and n.name in {'current_user', 'require_platform_admin'}]
        namespace = dict(Cookie=Cookie, HTTPException=HTTPException, UserPrincipal=UserPrincipal,
            AuthenticationError=AuthenticationError, sessions=self.sessions,
            product_store=self.product, skill_registry=self.registry)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(main), 'exec'), namespace)
        app = FastAPI(); app.include_router(catalog_router(self.catalog, namespace['require_platform_admin']))
        @app.exception_handler(AgentCatalogError)
        async def error(request, exception):
            return JSONResponse({'detail': str(exception)}, status_code=exception.status_code)
        self.client = TestClient(app); self.client.cookies.set('workbench_session', self.token)
        self.endpoint = f'/api/v1/platform/agents/{self.agent}/versions/{self.agent_revision}/test'
        # Windows asyncio uses a private socketpair implemented via loopback
        # connect. Permit ONLY that internal construction, not general loopback
        # targets/HTTP calls; everything else is rejected before connecting.
        internal = threading.local()
        original_pair, original_connect = socket.socketpair, socket.socket.connect
        def pair(*args, **kwargs):
            internal.socketpair = True
            try: return original_pair(*args, **kwargs)
            finally: internal.socketpair = False
        def connect(sock, address):
            if getattr(internal, 'socketpair', False):
                return original_connect(sock, address)
            raise AssertionError('Network forbidden')
        self.internal_socketpair = patch.object(socket, 'socketpair', pair)
        self.denied_network = patch.object(socket.socket, 'connect', connect)
        self.internal_socketpair.start(); self.denied_network.start()

    def tearDown(self):
        self.denied_network.stop(); self.internal_socketpair.stop(); self.client.close(); self.temp.cleanup()

    def rows(self, table):
        with self.store.connection() as conn:
            return [dict(r) for r in conn.execute('SELECT * FROM ' + table)]

    def snapshot(self):
        tables = ('users', 'platform_admins', 'agent_templates', 'agent_template_versions',
            'tenant_agent_instances', 'agent_template_tests', 'tasks', 'task_agent_contexts',
            'agent_execution_contexts', 'run_traces', 'conversations', 'conversation_owners',
            'conversation_agent_contexts', 'task_results', 'messages', 'agent_template_version_skills',
            'skill_versions', 'skill_packages', 'credit_transactions')
        return {t: self.rows(t) for t in tables}

    def admission(self):
        return g.execution_admission(self.snapshot(), self.scope, environment='test',
            source=g.SOURCE, tree=g.TREE, authenticated_actor_id=self.actor)

    def enqueue(self):
        # Actual formal executor verifies CodexRuntimeProvider type before queue
        # creation. No method on it executes; Worker uses RuntimeDouble later.
        original = self.tasks._agents._runtime
        self.tasks._agents._runtime = CodexRuntimeProvider(None)
        try:
            response = self.client.post(self.endpoint,
                json={'configuration_fingerprint': self.scope['fingerprint']})
        finally:
            self.tasks._agents._runtime = original
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def execute(self, failed=False):
        result = self.enqueue(); self.runtime.fail = failed
        asyncio.run(self.tasks.execute(self.product.task_for_worker(result['task_id'])))
        return result

    def check(self, data=None, scope=None, **identity):
        return g.validate_records(data or self.snapshot(), scope or self.scope,
            **dict(environment='test', source=g.SOURCE, tree=g.TREE, **identity))

    def publish_check(self):
        with self.store.connection() as conn:
            return g.publication_eligibility(self.snapshot(), self.scope,
                resolver=self.resolver, connection=conn)

    def publish_enable(self):
        self.assertTrue(self.publish_check())
        path = f'/api/v1/platform/agents/{self.agent}'
        response = self.client.post(path+f'/versions/{self.agent_revision}/publish', json={'mode': 'production'})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.put(path+'/instances/tenant-a', json={
            'agent_template_version_id': self.agent_revision, 'overrides': {}})
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(path+'/instances/tenant-a/enable')
        self.assertEqual(response.status_code, 200, response.text)

    def test_01_first_formal_test_created_without_previous_runtime_pass(self):
        self.assertFalse(any(r['test_type'] == 'runtime' for r in self.rows('agent_template_tests')))
        result = self.enqueue()
        self.assertEqual(self.jobs, [result['task_id']]); self.assertEqual(self.runtime.turns, 0)
        self.assertEqual(self.check(), {result['runtime_test_id']: False})

    def test_02_real_formal_executor_result_persistence(self):
        result = self.execute()
        self.assertEqual(self.check(), {result['runtime_test_id']: True})
        self.assertEqual(self.runtime.turns, 1)

    def test_03_formal_failed_runtime_can_persist(self):
        result = self.execute(failed=True)
        self.assertEqual(self.check(), {result['runtime_test_id']: False})
        row = next(r for r in self.rows('agent_template_tests') if r['id'] == result['runtime_test_id'])
        self.assertEqual(row['status'], 'failed')

    def test_04_failed_test_cannot_publish_even_with_publication_authority(self):
        self.execute(failed=True); self.scope['publication_allowed'] = True
        with self.assertRaisesRegex(g.Blocked, 'REAL_PASS_REQUIRED'): self.publish_check()
        with self.assertRaises(AgentCatalogError): self.catalog.publish(self.agent, self.agent_revision, self.actor, 'production')

    def test_05_formal_pass_requires_independent_publish_authority(self):
        self.execute()
        with self.assertRaisesRegex(g.Blocked, 'PUBLISH_AUTHORIZATION_REQUIRED'): self.publish_check()
        self.scope['publication_allowed'] = True
        self.publish_enable()
        self.assertTrue(next(r for r in self.product.agents('tenant-a') if r['id'] == self.agent))

    def test_06_no_test_no_publish(self):
        self.scope['publication_allowed'] = True
        with self.assertRaisesRegex(g.Blocked, 'REAL_PASS_REQUIRED'): self.publish_check()
        with self.assertRaises(AgentCatalogError): self.catalog.publish(self.agent, self.agent_revision, self.actor, 'production')

    def test_07_forged_pass_of_queued_test_rejected(self):
        self.enqueue(); data = self.snapshot()
        next(r for r in data['agent_template_tests'] if r['test_type'] == 'runtime')['status'] = 'passed'
        with self.assertRaisesRegex(g.Blocked, 'TERMINAL_TASK_REQUIRED'): self.check(data)

    def test_08_wrong_tenant_rejected(self):
        self.execute(); data = self.snapshot(); data['tasks'][0]['tenant_id'] = 'tenant-b'
        with self.assertRaisesRegex(g.Blocked, 'TASK_OWNERSHIP'): self.check(data)

    def test_09_wrong_agent_rejected(self):
        self.execute(); data = self.snapshot(); data['tasks'][0]['agent_id'] = 'image-agent'
        with self.assertRaisesRegex(g.Blocked, 'TASK_OWNERSHIP'): self.check(data)

    def test_10_wrong_revision_rejected(self):
        self.execute(); data = self.snapshot()
        next(r for r in data['agent_template_tests'] if r['test_type'] == 'runtime')['agent_template_version_id'] = 'fake'
        with self.assertRaisesRegex(g.Blocked, 'REVISION_SCOPE'): self.check(data)

    def test_11_cross_user_task_rejected(self):
        self.execute(); data = self.snapshot(); data['tasks'][0]['user_id'] = 'another-user'
        with self.assertRaisesRegex(g.Blocked, 'TASK_OWNERSHIP'): self.check(data)

    def test_12_fake_run_identity_rejected(self):
        self.execute(); data = self.snapshot(); data['tasks'][0]['run_id'] = 'fake-run'
        with self.assertRaisesRegex(g.Blocked, 'RELATION:run_traces'): self.check(data)

    def test_13_cross_tenant_run_rejected(self):
        self.execute(); data = self.snapshot(); data['run_traces'][0]['tenant_id'] = 'tenant-b'
        with self.assertRaisesRegex(g.Blocked, 'RUN_OWNERSHIP'): self.check(data)

    def test_14_forged_result_body_rejected(self):
        self.execute(); data = self.snapshot()
        row = next(r for r in data['agent_template_tests'] if r['test_type'] == 'runtime')
        result = json.loads(row['result_json']); result['run_id'] = 'fake'; row['result_json'] = json.dumps(result)
        with self.assertRaisesRegex(g.Blocked, 'RESULT_EVIDENCE_MISMATCH'): self.check(data)

    def test_15_deleted_final_response_rejected(self):
        self.execute(); data = self.snapshot(); data['task_results'] = []
        with self.assertRaisesRegex(g.Blocked, 'RELATION:task_results'): self.check(data)

    def test_16_record_remains_valid_on_repeated_read_after_persistence(self):
        self.execute(); before = self.store.database_path.read_bytes()
        self.assertEqual(self.check(), self.check()); self.assertEqual(before, self.store.database_path.read_bytes())

    def test_17_execution_admission_does_not_certify_quality_or_provider(self):
        result = self.admission()
        self.assertTrue(result['execution_eligible'])
        self.assertFalse(result['publish_eligible']); self.assertFalse(result['provider_authorized'])

    def test_18_production_and_source_drift_rejected(self):
        for key, value in (('environment', 'production'), ('source', 'f'*40), ('tree', 'e'*40)):
            with self.subTest(key=key), self.assertRaises(g.Blocked):
                args = dict(environment='test', source=g.SOURCE, tree=g.TREE); args[key] = value
                g.validate_records(self.snapshot(), self.scope, **args)

    def test_19_forged_request_fields_and_unsigned_session_rejected(self):
        response = self.client.post(self.endpoint, json={'configuration_fingerprint': self.scope['fingerprint'], 'status': 'passed'})
        self.assertEqual(response.status_code, 422)
        self.client.cookies.set('workbench_session', 'unsigned')
        self.assertEqual(self.client.post(self.endpoint, json={'configuration_fingerprint': self.scope['fingerprint']}).status_code, 401)
        self.assertFalse(self.jobs)

    def test_20_legal_and_illegal_transitions(self):
        result = self.enqueue(); before = next(r for r in self.rows('agent_template_tests') if r['id'] == result['runtime_test_id'])
        task = self.product.task_for_worker(result['task_id']); self.tester.started(task)
        running = next(r for r in self.rows('agent_template_tests') if r['id'] == result['runtime_test_id'])
        g.check_transition(before, running); self.assertFalse(self.check()[result['runtime_test_id']])
        asyncio.run(self.tasks.execute(task))
        terminal = next(r for r in self.rows('agent_template_tests') if r['id'] == result['runtime_test_id'])
        g.check_transition(running, terminal)
        with self.assertRaisesRegex(g.Blocked, 'ILLEGAL_TRANSITION'): g.check_transition(terminal, before)
        changed = copy.deepcopy(terminal); changed['task_id'] = 'fake'
        with self.assertRaisesRegex(g.Blocked, 'IDENTITY_MUTATION'): g.check_transition(terminal, changed)

    def test_21_readiness_never_replaces_current_native_guard(self):
        with self.assertRaisesRegex(g.Blocked, 'APPROVED_DYNAMIC_GUARD_BASELINE_NOT_ATTESTED'):
            g.live_preflight(None, expected_adapter_sha256=None)

    def test_22_formal_admin_gap_fails_closed_not_fake_lease(self):
        with self.assertRaisesRegex(g.Blocked, 'FORMAL_ADMIN_GRANT_REVOKE_ADAPTER_UNAVAILABLE'):
            g.require_formal_admin_adapter(None)
        with self.assertRaisesRegex(g.Blocked, 'NOT_QUALIFIED_FOR_D8A'):
            g.require_formal_admin_adapter(SimpleNamespace(grant=True, revoke=True))

    def test_23_incomplete_trace_evidence_cannot_be_claimed_passed(self):
        self.execute(); data = self.snapshot(); payload = json.loads(data['run_traces'][0]['payload'])
        payload['runtime_completed'] = False; data['run_traces'][0]['payload'] = json.dumps(payload)
        with self.assertRaisesRegex(g.Blocked, 'RESULT_EVIDENCE_MISMATCH'): self.check(data)

    def test_24_wrong_context_rejected(self):
        self.execute(); data = self.snapshot(); data['agent_execution_contexts'][0]['agent_template_version_id'] = 'fake'
        with self.assertRaisesRegex(g.Blocked, 'CONTEXT_IDENTITY'): self.check(data)

    def test_25_runtime_double_pass_does_not_enable_old_create_draft(self):
        self.execute()
        from app.wechat_action_contract import WechatActionContract
        from app.tenant_secret_reference import TenantSecretReferences
        permission = WechatActionContract(self.store, f.RuntimeTokenIssuer('synthetic-test-key'),
            'test', TenantSecretReferences('test', {}))
        self.assertFalse(permission.network_allowed)

    def test_26_admin_role_and_secret_backend_application_bytes_unchanged(self):
        self.execute()
        user = next(r for r in self.rows('users') if r['id'] == self.actor)
        self.assertEqual(user['role'], 'member'); self.assertEqual(g.digest(user), self.scope['actor_sha256'])

    def test_27_publish_enable_then_formal_ordinary_member_chat(self):
        self.execute(); self.scope['publication_allowed'] = True; self.publish_enable()
        self.product.create_user('tenant-a', 'ordinary-runtime@example.invalid', 'synthetic-unused', 'Fixture member', 'member')
        user = self.product.user_by_email('ordinary-runtime@example.invalid')
        self.assertFalse(self.registry.is_platform_admin(user['id']))
        task = self.product.create_task('tenant-a', user['id'], self.agent, '普通文案请求', None)
        asyncio.run(self.tasks.execute(task))
        self.assertEqual(self.product.task_for_worker(task['id'])['status'], 'completed')
        self.assertTrue(any(self.check().values()))

    def test_28_missing_test_association_rejected(self):
        self.execute(); data = self.snapshot()
        data['agent_template_tests'] = [r for r in data['agent_template_tests'] if r['test_type'] != 'runtime']
        with self.assertRaisesRegex(g.Blocked, 'ORPHAN_TEST_CONTEXT'): self.check(data)

    def test_29_wrong_skill_revision_rejected(self):
        self.execute(); data = self.snapshot()
        binding = next(r for r in data['agent_template_version_skills'] if r['agent_template_version_id'] == self.agent_revision)
        binding['skill_version_id'] = 'fake'
        with self.assertRaisesRegex(g.Blocked, 'EXACT_SKILL_BINDING'): self.check(data)

    def test_30_unknown_platform_admin_rejected_before_admission(self):
        data = self.snapshot(); data['platform_admins'] = [{'user_id': 'unknown', 'granted_at': 'synthetic'}]
        with self.assertRaisesRegex(g.Blocked, 'PLATFORM_ADMIN_REQUIRED'):
            g.execution_admission(data, self.scope, environment='test', source=g.SOURCE,
                tree=g.TREE, authenticated_actor_id=self.actor)

    def test_31_scope_limit_does_not_create_second_test(self):
        self.scope['maximum_runtime_tests'] = 1; self.enqueue()
        with self.assertRaisesRegex(g.Blocked, 'TEST_SCOPE_LIMIT'): self.admission()
        self.assertEqual(len(self.jobs), 1)

    def test_32_wrong_authenticated_actor_rejected(self):
        with self.assertRaisesRegex(g.Blocked, 'AUTHENTICATED_ACTOR_REQUIRED'):
            g.execution_admission(self.snapshot(), self.scope, environment='test', source=g.SOURCE,
                tree=g.TREE, authenticated_actor_id='unknown')

    def test_33_completed_task_before_finished_is_not_publish_quality(self):
        result = self.enqueue()
        with patch.object(self.tester, 'finished', lambda _: None):
            asyncio.run(self.tasks.execute(self.product.task_for_worker(result['task_id'])))
        self.assertEqual(self.product.task_for_worker(result['task_id'])['status'], 'completed')
        self.assertFalse(self.check()[result['runtime_test_id']])
        self.scope['publication_allowed'] = True
        with self.assertRaisesRegex(g.Blocked, 'REAL_PASS_REQUIRED'): self.publish_check()

    def test_34_terminal_result_mutation_rejected(self):
        result = self.execute()
        before = next(r for r in self.rows('agent_template_tests') if r['id'] == result['runtime_test_id'])
        changed = copy.deepcopy(before); changed['result_json'] = '{}'
        with self.assertRaisesRegex(g.Blocked, 'TERMINAL_EVIDENCE_MUTATION'):
            g.check_transition(before, changed)

    def test_35_fail_and_pass_both_retained_for_read_only_revalidation(self):
        failed, passed = self.execute(failed=True), self.execute()
        expected = {failed['runtime_test_id']: False, passed['runtime_test_id']: True}
        self.assertEqual(self.check(), expected); self.assertEqual(self.check(), expected)

    def test_36_invalidated_flag_without_fingerprint_change_rejected(self):
        self.execute(); data = self.snapshot()
        next(r for r in data['agent_template_tests'] if r['test_type'] == 'runtime')['status'] = 'invalidated'
        with self.assertRaisesRegex(g.Blocked, 'RESULT_EVIDENCE_MISMATCH'): self.check(data)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wechat-pyc-fixture-')
        self.root = Path(self.temp.name).absolute()
        self.source = self.root / 'enterprise_agent_poc/app/store.py'
        self.source.parent.mkdir(parents=True)
        self.source.write_text('def identity():\n    return "synthetic fixture"\n', encoding='utf-8')
        self.pins = {self.source.relative_to(self.root).as_posix(): cache_guard.sha(self.source.read_bytes())}
        self.version = tuple(sys.version_info[:2])

    def tearDown(self): self.temp.cleanup()

    def compile(self, mode=py_compile.PycInvalidationMode.TIMESTAMP):
        return Path(py_compile.compile(str(self.source), doraise=True, optimize=0, invalidation_mode=mode))

    def check(self): return cache_guard.installed_source(self.root, self.pins, required_python=self.version)

    def test_01_clean_installed_source(self): self.assertEqual(self.check()['verified_cache_files'], 0)

    def test_02_exact_timestamp_cache_verified(self):
        self.compile(); self.assertEqual(self.check()['verified_cache_files'], 1)

    def test_03_checked_hash_cache_verified(self):
        self.compile(py_compile.PycInvalidationMode.CHECKED_HASH)
        self.assertEqual(self.check()['verified_cache_files'], 1)

    def test_04_other_pyc_rejected_not_blanket_ignored(self):
        cache = self.compile(); (cache.parent/'unknown.pyc').write_bytes(cache.read_bytes())
        with self.assertRaisesRegex(g.Blocked, 'UNAPPROVED_INSTALLED_FILE_ADDITION'): self.check()

    def test_05_sealed_source_mutation_rejected(self):
        self.compile(); self.source.write_text('unauthorized=1\n')
        with self.assertRaisesRegex(g.Blocked, 'PERSISTENT_INSTALLED_SOURCE_HASH'): self.check()

    def test_06_missing_source_rejected(self):
        self.compile(); self.source.unlink()
        with self.assertRaisesRegex(g.Blocked, 'PERSISTENT_INSTALLED_SOURCE_HASH'): self.check()

    def test_07_fake_bytecode_with_valid_header_rejected(self):
        import marshal
        cache = self.compile(); raw = cache.read_bytes()
        bad = compile('unauthorized=1\n', str(self.source), 'exec')
        cache.write_bytes(raw[:16] + marshal.dumps(bad))
        with self.assertRaisesRegex(g.Blocked, 'PYC_COMPILED_CODE_REJECTED'): self.check()

    def test_08_wrong_magic_rejected(self):
        cache = self.compile(); raw = cache.read_bytes(); cache.write_bytes(b'FAKE'+raw[4:])
        with self.assertRaisesRegex(g.Blocked, 'PYC_MAGIC_OR_HEADER_REJECTED'): self.check()

    def test_09_plain_generated_file_rejected(self):
        (self.root/'new-config.json').write_text('{}')
        with self.assertRaisesRegex(g.Blocked, 'UNAPPROVED_INSTALLED_FILE_ADDITION'): self.check()

    def test_10_other_python_version_rejected(self):
        with self.assertRaisesRegex(g.Blocked, 'PINNED_PYTHON_REQUIRED'):
            cache_guard.installed_source(self.root, self.pins, required_python=(0, 0))

    def test_11_cache_optimization_not_silently_relaxed(self):
        Path(py_compile.compile(str(self.source), doraise=True, optimize=1))
        with self.assertRaisesRegex(g.Blocked, 'UNAPPROVED_INSTALLED_FILE_ADDITION'): self.check()



def baseline_proof(snapshot, native_root, source):
    """Read/hash-only current baseline, NEVER import a live native module."""
    fixture = json.loads((Path(__file__).parent/'fixtures/wechat_runtime_guard_snapshot_v1.json').read_bytes())
    class BaselineTests(unittest.TestCase):
        def test_snapshot_byte_identity(self):
            self.assertEqual(cache_guard.sha(snapshot.read_bytes()), fixture['snapshot_sha256'])
        def test_current_installed_overlay_metadata_identities(self):
            for name, expected in fixture['guard_files'].items():
                self.assertEqual(cache_guard.sha((native_root/name).read_bytes()), expected, name)
            approval = json.loads((native_root/'approval.v1.json').read_bytes())
            self.assertEqual(approval['code_sha256']['primary_guard.py'], fixture['guard_files']['primary_guard.py'])
        def test_actual_imported_dca_source_identity(self):
            self.assertEqual(cache_guard.sha(source.read_bytes()), fixture['dca_import_sha256'])
        def test_actual_schema_status_and_cache_scope(self):
            self.assertEqual(set(fixture['runtime_test_columns']), g.FIELDS)
            self.assertEqual(set(fixture['runtime_test_statuses']), set(g.EDGES))
            self.assertEqual(set(fixture['extra_cache_modules']), cache_guard.CACHE_MODULES)
            self.assertEqual(fixture['actual_run_table'], 'run_traces')
    return unittest.defaultTestLoader.loadTestsFromTestCase(BaselineTests)


def old_guard_rejection(source):
    """Exact 06-attested imported DCA AST, not a full native restart proof."""
    import collections
    original = source.read_bytes()
    graph = next(n for n in ast.parse(original).body if isinstance(n, ast.FunctionDef) and n.name == 'graph')
    class HistoricalTests(LifecycleTests):
        def test_old_validation_only_graph_rejects_formal_runtime_row(self):
            result = self.enqueue(); data = self.snapshot()
            namespace = dict(need=g.need, digest=g.digest, collections=collections, Path=Path,
                TENANT='tenant-a', EMAIL='dispatch@example.invalid', ARTIFACT=self.skill['checksum'],
                APP=Path(f.wechat_skill.__file__).parents[1])
            exec(compile(ast.Module(body=[graph], type_ignores=[]), str(source), 'exec'), namespace)
            with self.assertRaisesRegex(g.Blocked, 'NO_RUNTIME_QUALITY_EVIDENCE'):
                namespace['graph'](data, {'protected_baseline_row_hashes': {}, 'mutable_tables': []})
            self.assertEqual(self.check(), {result['runtime_test_id']: False})
            self.assertEqual(original, source.read_bytes())
    return unittest.TestSuite([HistoricalTests('test_old_validation_only_graph_rejects_formal_runtime_row')])
