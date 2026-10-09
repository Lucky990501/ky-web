"""Exact permission service + real signed API/Executor, isolated SQLite only."""
import copy
import ast
import collections
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_wechat_runtime_test_lifecycle_guard as base
from scripts import exact_test_admin_lifecycle as a
from scripts import wechat_runtime_test_lifecycle_guard as g
from scripts import wechat_runtime_native_successor as n
from app.test_exact_admin_gate import install_exact_admin_gate, CAPABILITY_PATH


class Fixture(base.LifecycleTests):
    """Inherited fixture setup, but NEVER its uncontrolled registry Grant."""
    legacy_grant_fixture = False
    def setUp(self):
        super().setUp()
        now = datetime.now(timezone.utc)
        actor = next(r for r in self.rows('users') if r['id'] == self.actor)
        self.permission_scope = dict(contract=a.CONTRACT, environment='test', base_source=a.BASE_SOURCE,
            base_tree=a.BASE_TREE, application_source='a'*40, application_tree='b'*40,
            tooling_source='a'*40, tooling_tree='b'*40, tenant_id='tenant-a', principal_id=self.actor,
            principal_sha256=a.principal_hash(actor), original_role='member', agent_id=self.agent,
            agent_slug='wechat-official-account-writing', revision_id=self.agent_revision,
            fingerprint=self.scope['fingerprint'], run_id=str(uuid4()), operations=sorted(a.OPS),
            issued_at=(now-timedelta(seconds=5)).isoformat(), expires_at=(now+timedelta(minutes=30)).isoformat(),
            production_authority=False, publication_authorized=True)
        self.admin = a.ExactTestAdmin(self.store, environment='test',
            authority_loader=lambda: copy.deepcopy(self.permission_scope))
        def isolated_database(conn):
            self.assertFalse(self.store.is_postgres)
            self.assertTrue(self.store.database_path.is_relative_to(self.root))
            self.assertEqual((self.root/'test-only-fixture.marker').read_text(), 'wechat-runtime-test-offline-v1')
        self.admin._database = isolated_database  # Explicit fake DB authority; NOT Linux/PG evidence.
        self.required_flag = patch.dict(os.environ, {a.REGISTRATION: 'true'})
        self.required_flag.start()
        self.assertTrue(install_exact_admin_gate(self.app, environment='test', store=self.product,
            sessions=self.sessions, service=self.admin))
        self.client.headers['x-exact-test-admin-run-id'] = self.permission_scope['run_id']
        self.probe = a.FixedLoopbackCapabilityProbe(self.token)
        self.probe._request = lambda scope: self.client.get(CAPABILITY_PATH).json()
        self.admin.gate_probe = self.probe
        self.call = {k: self.permission_scope[k] for k in ('principal_id', 'tenant_id', 'run_id')}
    def tearDown(self):
        self.required_flag.stop(); super().tearDown()
    def lease(self):
        self.admin.prepare(**self.call); return self.admin.grant(**self.call)
    def revoke(self): return self.admin.revoke(**self.call)
    def audits(self):
        return [r for r in self.rows('execution_events') if r['event_type'] == a.EVENT]
    def snapshot(self):
        data = super().snapshot()
        for table in ('execution_events', 'enterprise_configs', 'agent_template_version_tools', 'tool_capabilities',
            'task_events', 'credit_accounts', 'skills'):
            data[table] = self.rows(table)
        return data


class AdminTests(Fixture):
    # Avoid unittest inheriting the unrelated 36 Parent tests a second time.
    def test_admin_01_grant_use_publish_chat_revoke(self):
        self.assertFalse(self.registry.is_platform_admin(self.actor)); original = self.rows('users')
        self.lease(); g.require_formal_admin_adapter(self.admin)
        result = self.execute(); self.assertTrue(self.check()[result['runtime_test_id']])
        self.scope['publication_allowed'] = True; self.publish_enable()
        task = self.product.create_task('tenant-a', self.actor, self.agent, '普通成员角色文案', None)
        base.asyncio.run(self.tasks.execute(task))
        self.assertEqual(self.product.task_for_worker(task['id'])['status'], 'completed')
        self.assertEqual(self.revoke()['active_test_platform_admin'], 0)
        self.assertEqual(self.rows('users'), original)
        self.assertFalse(self.registry.is_platform_admin(self.actor))
        self.assertEqual(self.client.get('/api/v1/platform/agents/'+self.agent).status_code, 403)
        self.assertTrue(any(self.check().values()))

    def test_admin_02_failed_test_retained_revoke_and_no_publish(self):
        self.lease(); result = self.execute(failed=True)
        self.assertFalse(self.check()[result['runtime_test_id']])
        url = '/api/v1/platform/agents/'+self.agent+'/versions/'+self.agent_revision+'/publish'
        self.assertEqual(self.client.post(url, json={'mode': 'production'}).status_code, 409)
        self.revoke(); self.assertEqual(self.rows('platform_admins'), [])
        self.assertFalse(self.check()[result['runtime_test_id']])

    def test_admin_03_grant_without_proven_revoke_rejected(self):
        with self.assertRaisesRegex(g.Blocked, 'REVOKE_NOT_PREPARED'): self.admin.grant(**self.call)
        self.assertEqual(self.rows('platform_admins'), [])

    def test_admin_04_wrong_principal_tenant_run_rejected(self):
        for key in self.call:
            bad = dict(self.call); bad[key] = str(uuid4())
            with self.subTest(key=key), self.assertRaisesRegex(g.Blocked, 'CALLER_SCOPE_MISMATCH'):
                self.admin.prepare(**bad)
        self.assertEqual(self.rows('platform_admins'), [])

    def test_admin_05_repeated_revoke_safe_and_no_regrant(self):
        self.lease(); first = self.revoke(); count = len(self.audits())
        self.assertEqual(self.revoke(), first); self.assertEqual(len(self.audits()), count)
        with self.assertRaisesRegex(g.Blocked, 'RUN_CONSUMED'): self.admin.grant(**self.call)

    def test_admin_06_transaction_failure_rolls_back_membership_and_audit(self):
        self.admin.prepare(**self.call); before = self.audits()
        original = self.admin._audit
        def fail(conn, scope, action, history, **kw):
            if action == 'granted': raise RuntimeError('synthetic crash inside transaction')
            return original(conn, scope, action, history, **kw)
        with patch.object(self.admin, '_audit', fail), self.assertRaises(RuntimeError): self.admin.grant(**self.call)
        self.assertEqual(self.rows('platform_admins'), []); self.assertEqual(self.audits(), before)
        self.assertEqual(self.revoke()['active_test_platform_admin'], 0)

    def test_admin_07_exception_in_operation_finishes_ticket_and_revoke(self):
        self.lease()
        self.client._transport.raise_server_exceptions = False
        with patch.object(self.catalog, 'detail', side_effect=RuntimeError('synthetic operation interruption')):
            response = self.client.get('/api/v1/platform/agents/'+self.agent)
        self.assertEqual(response.status_code, 500)
        self.revoke(); self.assertEqual(self.rows('platform_admins'), [])

    def test_admin_08_inflight_revoke_rejects_without_quiescence_proof(self):
        self.lease()
        who = base.UserPrincipal(self.actor, 'tenant-a', 'member')
        ticket = self.admin.begin_operation(who, 'GET', '/api/v1/platform/agents/'+self.agent, None, self.call['run_id'])
        with self.assertRaisesRegex(g.Blocked, 'RECOVERY_PROOF_REQUIRED'): self.revoke()
        with self.assertRaisesRegex(g.Blocked, 'RECOVERY_PROOF_REQUIRED'):
            self.admin.revoke(**self.call, dead_operation_proof=lambda *_: {ticket})
        self.admin.finish_operation(ticket, status_code=500); self.revoke()

    def test_admin_09_protected_dead_process_recovery(self):
        self.lease(); who = base.UserPrincipal(self.actor, 'tenant-a', 'member')
        self.admin.begin_operation(who, 'GET', '/api/v1/platform/agents/'+self.agent, None, self.call['run_id'])
        with self.store.connection() as conn: history = self.admin._history(conn, self.permission_scope)
        proof = dict(contract=a.CONTRACT, run_id=self.call['run_id'], application_source='a'*40,
            application_tree='b'*40, last_audit_sha256=a.event_hash(history[-1][0]),
            tickets=sorted(a.outstanding(history)), quiesced_process_ids=[os.getpid()],
            observed_at=datetime.now(timezone.utc).isoformat(), independent_approval='EXACT_API_PROCESS_QUIESCENCE_ATTESTED_BY_06')
        with patch('app.test_tenant_seeding.native_json', lambda path: (proof, 'synthetic-root-fixture')):
            self.assertEqual(self.admin.revoke(**self.call, dead_operation_proof=a.load_dead_operation_proof)['active_test_platform_admin'], 0)
        self.assertFalse(self.registry.is_platform_admin(self.actor))

    def test_admin_10_expired_lease_blocks_operation_but_revoke_still_works(self):
        self.lease()
        future = datetime.now(timezone.utc)+timedelta(hours=2)
        class FutureClock(datetime):
            @classmethod
            def now(cls, tz=None): return future
        with patch.object(a, 'datetime', FutureClock):
            self.assertEqual(self.client.get('/api/v1/platform/agents/'+self.agent).status_code, 403)
            self.assertEqual(self.revoke()['active_test_platform_admin'], 0)

    def test_admin_11_other_agent_revision_and_skill_management_denied(self):
        self.lease()
        for path in ('/api/v1/platform/skills', '/api/v1/platform/agents/other',
            '/api/v1/platform/agents/'+self.agent+'/versions/'+str(uuid4())+'/publish'):
            with self.subTest(path=path): self.assertEqual(self.client.get(path).status_code, 403)
        self.revoke()

    def test_admin_12_run_header_not_optional(self):
        self.lease(); del self.client.headers['x-exact-test-admin-run-id']
        self.assertEqual(self.client.get('/api/v1/platform/agents/'+self.agent).status_code, 403)
        self.revoke()

    def test_admin_13_no_independent_publication_authority(self):
        self.permission_scope['publication_authorized'] = False
        self.lease(); self.execute()
        self.assertEqual(self.client.post('/api/v1/platform/agents/'+self.agent+'/versions/'+self.agent_revision+'/publish',
            json={'mode': 'production'}).status_code, 403)
        self.revoke()

    def test_admin_14_foreign_membership_never_deleted(self):
        self.lease()
        self.product.create_user('tenant-a', 'foreign-admin-negative@example.invalid', 'synthetic-unused', 'Negative fixture', 'member')
        foreign = self.product.user_by_email('foreign-admin-negative@example.invalid')['id']
        # Negative isolated tamper, not the positive permission path.
        with self.store.connection() as conn:
            conn.execute('INSERT INTO platform_admins(user_id) VALUES (?)', (foreign,))
        with self.assertRaisesRegex(g.Blocked, 'FOREIGN_ADMIN_REMAINS'): self.revoke()
        self.assertFalse(self.registry.is_platform_admin(self.actor))
        self.assertTrue(self.registry.is_platform_admin(foreign))

    def test_admin_15_owned_row_tampering_rejected(self):
        self.lease()
        with self.store.connection() as conn: conn.execute('UPDATE platform_admins SET granted_at=? WHERE user_id=?', ('2000-01-01T00:00:00+00:00', self.actor))
        with self.assertRaisesRegex(g.Blocked, 'OWNED_MEMBERSHIP_DRIFT'): self.revoke()

    def test_admin_16_production_no_gate_or_permission_expansion(self):
        with patch.object(self.admin, 'authority_loader', side_effect=AssertionError('No Test file in Production')):
            self.admin.environment = 'production'
            with self.assertRaisesRegex(g.Blocked, 'PRODUCTION_REJECTED'): self.admin.prepare(**self.call)
        self.assertFalse(install_exact_admin_gate(base.FastAPI(), environment='production', store=None, sessions=None))
        self.assertEqual(self.rows('platform_admins'), [])

    def test_admin_17_gate_probe_required_and_no_boolean_bypass(self):
        self.admin.gate_probe = lambda _: {'gate_required': True}
        with self.assertRaisesRegex(g.Blocked, 'REQUEST_GATE_NOT_PROVEN'): self.admin.prepare(**self.call)
        self.assertEqual(self.audits(), [])

    def test_admin_18_actor_role_and_audit_tamper_rejected(self):
        self.lease()
        with self.store.connection() as conn:
            row = conn.execute('SELECT * FROM execution_events WHERE event_type=? ORDER BY id LIMIT 1', (a.EVENT,)).fetchone()
            body = json.loads(row['payload']); body['principal_id'] = str(uuid4())
            conn.execute('UPDATE execution_events SET payload=? WHERE id=?', (json.dumps(body), row['id']))
        with self.assertRaisesRegex(g.Blocked, 'AUDIT_CHAIN_REJECTED'): self.revoke()


# unittest must not discover inherited Parent tests in this module; the verifier
# selects ONLY test_admin_* names. Generic inherited 302 regressions run separately.
class NativeTests(Fixture):
    def setUp(self):
        super().setUp()
        # Formal, isolated terminal task without executing any image provider.
        historical = self.product.create_task('tenant-a', self.actor, 'image-agent', 'Synthetic historical failure', None)
        self.product.set_task(historical['id'], 'tenant-a', 'failed', 'failed', 'Synthetic closed task')
        self.historical = historical['id']
        self.before = self.snapshot()
        self.anchors = {t: copy.deepcopy(next(r for r in self.before[t] if
            r.get('id') == (self.agent if t == 'agent_templates' else self.agent_revision)
            or t == 'tenant_agent_instances' and r['agent_id'] == self.agent))
            for t in ('agent_templates', 'agent_template_versions', 'tenant_agent_instances')}
        self.pins = {t: [g.digest(r) for r in rows] for t, rows in self.before.items()}
        self.parent_calls = 0

    def predecessor(self, view):
        self.parent_calls += 1
        self.assertEqual(view['enterprise_configs'], self.before['enterprise_configs'])
        old = next(r for r in view['tasks'] if r['id'] == self.historical)
        self.assertEqual(old, next(r for r in self.before['tasks'] if r['id'] == self.historical))
        protected = set(self.pins) - {'tasks', 'run_traces', 'conversations', 'conversation_owners', 'task_results',
            'messages', 'task_events', 'credit_transactions', 'credit_accounts', 'execution_events'}
        for table in protected:
            g.need(sorted(g.digest(r) for r in view[table]) == sorted(self.pins[table]), 'PARENT_PROTECTED_DRIFT:'+table)
        node = next(x for x in ast.parse(self.dca_source.read_bytes()).body if isinstance(x, ast.FunctionDef) and x.name == 'graph')
        namespace = dict(need=g.need, digest=g.digest, collections=collections, Path=Path,
            TENANT='tenant-a', EMAIL='dispatch@example.invalid', ARTIFACT=self.skill['checksum'],
            APP=Path(base.f.wechat_skill.__file__).parents[1])
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(self.dca_source), 'exec'), namespace)
        return namespace['graph'](view, {'protected_baseline_row_hashes': {t: self.pins[t] for t in protected},
            'mutable_tables': ['tasks', 'run_traces', 'conversations', 'conversation_owners', 'task_results', 'messages', 'task_events', 'credit_transactions']})

    def native(self, data=None, **mode):
        return n.validate_snapshot(data or self.snapshot(), scope=self.permission_scope, runtime_scope=self.scope,
            anchors=self.anchors, parent_pins=self.pins, predecessor_validate=self.predecessor,
            ordinary_principals=[self.actor], **mode)

    def test_native_01_complete_lifecycle_retained_after_revoke_and_repeat(self):
        self.assertEqual(self.native()['status'], 'PASS'); self.lease(); self.native()
        result = self.execute(); self.native(); self.assertTrue(self.check()[result['runtime_test_id']])
        self.scope['publication_allowed'] = True; self.publish_enable(); self.native()
        task = self.product.create_task('tenant-a', self.actor, self.agent, 'Ordinary member chat', None)
        base.asyncio.run(self.tasks.execute(task)); self.native()
        self.revoke(); self.assertEqual(self.native()['active_test_platform_admin'], 0)
        before = self.store.database_path.read_bytes()
        self.assertEqual(self.native(), self.native()); self.assertEqual(before, self.store.database_path.read_bytes())
        self.assertGreater(self.parent_calls, 5)

    def test_native_02_failure_can_be_retained_and_revalidated(self):
        self.lease(); self.execute(failed=True); self.revoke()
        result = self.native(); self.assertEqual(result['runtime_tests'], 1); self.assertEqual(result['passed_runtime_tests'], 0)

    def test_native_03_request_without_formal_admin_admission_rejected(self):
        # Negative copy of an executor-created row, no manual positive PASS.
        self.lease(); self.execute(); data = self.snapshot()
        data['execution_events'] = [r for r in data['execution_events'] if r['event_type'] != a.EVENT]
        with self.assertRaisesRegex(g.Blocked, 'UNAPPROVED_ADMIN'): self.native(data)

    def test_native_04_current_config_mutation_never_projected(self):
        self.lease(); self.execute(); self.revoke(); data = self.snapshot()
        data['enterprise_configs'][0]['payload'] = '{}'
        with self.assertRaises(AssertionError): self.native(data)

    def test_native_05_terminal_task_witness_mutation_never_projected(self):
        self.lease(); self.execute(); self.revoke(); data = self.snapshot()
        next(r for r in data['tasks'] if r['id'] == self.historical)['input_text'] = 'tampered'
        with self.assertRaises(AssertionError): self.native(data)

    def test_native_06_unrelated_agent_mutation_rejected(self):
        self.lease(); self.execute(); self.revoke(); data = self.snapshot()
        next(r for r in data['agent_templates'] if r['id'] != self.agent)['name'] = 'tampered'
        with self.assertRaisesRegex(g.Blocked, 'PARENT_PROTECTED_DRIFT'): self.native(data)

    def test_native_07_direct_enable_without_formal_op_rejected(self):
        self.lease(); self.execute(); self.revoke(); data = self.snapshot()
        next(r for r in data['tenant_agent_instances'] if r['agent_id'] == self.agent)['status'] = 'enabled'
        self.scope['publication_allowed'] = True
        with self.assertRaisesRegex(g.Blocked, 'FORMAL_OPERATION_EVIDENCE_REQUIRED'): self.native(data)

    def test_native_08_first_task_before_api_response_worker_race(self):
        self.lease(); who = base.UserPrincipal(self.actor, 'tenant-a', 'member')
        ticket = self.admin.begin_operation(who, 'POST', self.endpoint,
            {'configuration_fingerprint': self.scope['fingerprint']}, self.call['run_id'])
        old_gate = self.tester.isolation_guard
        self.tester.isolation_guard = lambda: self.native(for_execution=True)
        original = self.tasks._agents._runtime; self.tasks._agents._runtime = base.CodexRuntimeProvider(None)
        try:
            queued = base.asyncio.run(self.tester.run(self.agent, self.agent_revision, self.actor, self.scope['fingerprint']))
        finally: self.tasks._agents._runtime = original; self.tester.isolation_guard = old_gate
        result = self.native(for_execution=True)
        self.assertEqual(result['runtime_tests'], 1); self.assertEqual(result['passed_runtime_tests'], 0)
        with self.assertRaisesRegex(g.Blocked, 'IN_FLIGHT'): self.native()
        self.admin.finish_operation(ticket, status_code=202, evidence={'runtime_test_id': queued['runtime_test_id'], 'task_id': queued['task_id']})
        self.native(); self.revoke(); self.native()

    def test_native_09_published_evidence_does_not_tolerate_persona_edit(self):
        self.lease(); self.execute(); self.scope['publication_allowed'] = True; self.publish_enable(); self.revoke()
        data = self.snapshot(); next(r for r in data['agent_template_versions'] if r['id'] == self.agent_revision)['persona'] = 'unauthorized'
        with self.assertRaisesRegex(g.Blocked, 'NON_LIFECYCLE_MUTATION'): self.native(data)

    def test_native_10_new_anchor_cannot_replace_old_seal(self):
        self.lease(); self.execute(); self.revoke()
        self.pins['agent_template_versions'] = ['0'*64]
        with self.assertRaisesRegex(g.Blocked, 'EXISTING_BASELINE_ANCHOR'): self.native()


def suite(dca_source=None):
    result = unittest.TestSuite(AdminTests(name) for name in sorted(dir(AdminTests)) if name.startswith('test_admin_'))
    if dca_source is not None:
        NativeTests.dca_source = dca_source
        result.addTests(NativeTests(name) for name in sorted(dir(NativeTests)) if name.startswith('test_native_'))
    return result
