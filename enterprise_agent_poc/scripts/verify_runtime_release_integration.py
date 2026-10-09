"""OFFLINE component regression, explicitly NOT full Native qualification.

Load the separate Application draft and the Tooling draft. Reuse the existing
signed API/SQLite fixture; no real administrator, provider or remote service.
Root trust is emulated only in this test process. It is never an installer.
"""
import argparse
import ast
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--application-project', type=Path, required=True)
args = parser.parse_args()
tooling = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(args.application_project), str(tooling/'tests'), str(tooling)]
sys.dont_write_bytecode = True
import scripts
scripts.__path__ = [str(tooling/'scripts'), *scripts.__path__]
import test_exact_test_admin_lifecycle as base
from app import test_runtime_tooling as pair
from app.product_service import TaskExecutionNotAuthorized
from scripts import wechat_runtime_native_successor as native
from scripts import exact_test_admin_lifecycle as admin


class ExecutionTests(base.Fixture):
    def test_boundary_queued_active_lease(self):
        self.lease(); queued = self.enqueue()
        task = self.product.task_for_worker(queued['task_id'])
        native.validate_execution_permission(self.snapshot(), self.permission_scope, task)
        self.assertEqual(self.runtime.turns, 0)

    def test_boundary_revoked_lease_preserves_reservation(self):
        self.lease(); queued = self.enqueue(); self.revoke()
        before = self.snapshot(); task = self.product.task_for_worker(queued['task_id'])
        with self.assertRaisesRegex(base.g.Blocked, 'CURRENT_RUNTIME_PERMISSION_REQUIRED'):
            native.validate_execution_permission(before, self.permission_scope, task)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.runtime.turns, 0)
        self.assertEqual(before['platform_admins'], [])
        self.assertEqual(task['status'], 'queued')
        self.assertEqual(native.validate_ledger(before, self.permission_scope)[1],
                         {queued['runtime_test_id']: queued['task_id']})

    def test_boundary_wrong_task_owner(self):
        self.lease(); queued = self.enqueue()
        task = dict(self.product.task_for_worker(queued['task_id']), user_id='foreign')
        with self.assertRaisesRegex(base.g.Blocked, 'TASK_IDENTITY'):
            native.validate_execution_permission(self.snapshot(), self.permission_scope, task)

    def test_boundary_wrong_task_tenant(self):
        self.lease(); queued = self.enqueue()
        task = dict(self.product.task_for_worker(queued['task_id']), tenant_id='foreign')
        with self.assertRaisesRegex(base.g.Blocked, 'TASK_IDENTITY'):
            native.validate_execution_permission(self.snapshot(), self.permission_scope, task)

    def test_boundary_expired_current_lease(self):
        self.lease(); queued = self.enqueue(); task = self.product.task_for_worker(queued['task_id'])
        expired_now = datetime.now(timezone.utc) + timedelta(hours=2)
        with patch.object(native, 'datetime') as clock:
            clock.now.return_value = expired_now
            clock.fromisoformat.side_effect = datetime.fromisoformat
            with self.assertRaisesRegex(base.g.Blocked, 'EXPIRED_ADMIN_NEEDS_RECOVERY'):
                native.validate_execution_permission(self.snapshot(), self.permission_scope, task)

    def test_boundary_task_service_checks_before_any_test_write(self):
        self.lease(); queued = self.enqueue(); self.revoke(); before = self.snapshot()
        def deny(task):
            raise TaskExecutionNotAuthorized('CURRENT_EXECUTION_AUTHORIZATION_REQUIRED')
        self.tasks.execution_permission_guard = deny
        with self.assertRaises(TaskExecutionNotAuthorized):
            asyncio.run(self.tasks.execute(self.product.task_for_worker(queued['task_id'])))
        self.assertEqual(before, self.snapshot()); self.assertEqual(self.runtime.turns, 0)


class PairTests(unittest.TestCase):
    def setUp(self):
        self.value = {'contract': pair.CONTRACT, 'environment': 'test',
            'authority': 'INTEGRATED_RUNTIME_RELEASE_RECOVERY_FIX_APPROVED', 'production_authority': False,
            'application': dict(path=str(pair.TEST_ROOT/'releases/app-exact'), source='a'*40,
                                tree='b'*40, files_sha256='c'*64),
            'tooling': dict(path=str(pair.TEST_ROOT/'shared/source-qualifications/tool-exact'),
                            source='d'*40, tree='e'*40, files_sha256='f'*64)}
        self.env = patch.dict(os.environ, APP_ENV='test', PYTHONDONTWRITEBYTECODE='1')
        self.env.start()
        self.app = patch.object(pair, 'APPLICATION', pair.TEST_ROOT/'releases/app-exact/enterprise_agent_poc')
        self.app.start()
    def tearDown(self):
        self.app.stop(); self.env.stop()
    def load(self, value=None, bad_manifest=False):
        value = self.value if value is None else value
        def read(path):
            if path == pair.PAIR: return value, 'pair-pin'
            return {}, ('0'*64 if bad_manifest else value[path.name.split('-')[0]]['files_sha256'])
        with patch('app.test_tenant_seeding.native_json', read), patch.object(pair, 'verify_checkout') as check:
            result = pair.load_pair()
            self.assertEqual(check.call_count, 2)
            return result
    def test_pair_separate_identity_allowed(self):
        self.assertNotEqual(self.load()[0]['application']['source'], self.value['tooling']['source'])
    def test_pair_production_rejected(self):
        with patch.dict(os.environ, APP_ENV='production'), self.assertRaisesRegex(RuntimeError, 'TEST_ENVIRONMENT'):
            self.load()
    def test_pair_wrong_contract(self):
        bad = copy.deepcopy(self.value); bad['contract'] = 'unapproved'
        with self.assertRaisesRegex(RuntimeError, 'AUTHORITY'): self.load(bad)
    def test_pair_arbitrary_tool_path(self):
        bad = copy.deepcopy(self.value); bad['tooling']['path'] = '/tmp/evil'
        with self.assertRaisesRegex(RuntimeError, 'FIXED_ROOT'): self.load(bad)
    def test_pair_manifest_pin_mismatch(self):
        with self.assertRaisesRegex(RuntimeError, 'MANIFEST_PIN'): self.load(bad_manifest=True)
    def test_pair_wrong_loaded_application(self):
        with (patch.object(pair, 'APPLICATION', Path('/other')),
                self.assertRaisesRegex(RuntimeError, 'LOADED_APPLICATION_MISMATCH')):
            self.load()
    def test_pair_unknown_field_rejected(self):
        bad = copy.deepcopy(self.value); bad['tooling']['skip_hash'] = True
        with self.assertRaisesRegex(RuntimeError, 'IDENTITY_SHAPE'): self.load(bad)
    def binding(self, scope=None, sha='approved', project=None):
        approved = {role+'_'+key: self.value[role][key]
                    for role in ('application', 'tooling') for key in ('source', 'tree')}
        with patch.object(admin, 'PROJECT', project or
                          Path(self.value['tooling']['path'])/'enterprise_agent_poc'):
            admin.validate_release_pair(approved if scope is None else scope, self.value, sha, 'approved')
        return approved
    def test_pair_correct_separate_source_scope(self): self.binding()
    def test_pair_wrong_application_source(self):
        bad = self.binding(); bad['application_source'] = '9'*40
        with self.assertRaisesRegex(base.g.Blocked, 'PAIR_BINDING'): self.binding(bad)
    def test_pair_wrong_tooling_source(self):
        bad = self.binding(); bad['tooling_source'] = '9'*40
        with self.assertRaisesRegex(base.g.Blocked, 'PAIR_BINDING'): self.binding(bad)
    def test_pair_wrong_tree(self):
        bad = self.binding(); bad['application_tree'] = '9'*40
        with self.assertRaisesRegex(base.g.Blocked, 'PAIR_BINDING'): self.binding(bad)
    def test_pair_wrong_approval_pin(self):
        with self.assertRaisesRegex(base.g.Blocked, 'PAIR_PIN'): self.binding(sha='wrong')
    def test_pair_wrong_loaded_tooling(self):
        with self.assertRaisesRegex(base.g.Blocked, 'LOADED_TOOLING_MISMATCH'):
            self.binding(project=Path('/foreign/enterprise_agent_poc'))


class WorkerTests(unittest.TestCase):
    def worker(self, held):
        # Execute the actual worker loop, with an explicit queue double. This
        # checks ACK semantics only; it is NOT systemd/Redis qualification.
        source = args.application_project/'app/worker.py'
        node = next(n for n in ast.parse(source.read_bytes()).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'run')
        queue = SimpleNamespace(ping=lambda: True, recover_processing=lambda: None,
            reserve_knowledge=lambda: None, acknowledge=lambda task: acknowledgments.append(task))
        count = 0; acknowledgments = []; closed = []
        def reserve():
            nonlocal count
            count += 1
            if count == 1: return 'reserved-task'
            raise RuntimeError('END_COMPONENT_TEST')
        queue.reserve = reserve
        async def execute(task):
            if held: raise TaskExecutionNotAuthorized('denied')
        async def close(): closed.append(True)
        namespace = dict(settings=SimpleNamespace(task_queue='redis'),
            store=SimpleNamespace(initialize=lambda: None),
            RedisTaskQueue=SimpleNamespace(from_settings=lambda s: queue),
            product_store=SimpleNamespace(task_for_worker=lambda task: dict(id=task, status='queued')),
            task_service=SimpleNamespace(execute=execute), runtime=SimpleNamespace(close=close),
            asyncio=asyncio, TaskExecutionNotAuthorized=TaskExecutionNotAuthorized,
            logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a: None))
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        with self.assertRaisesRegex(RuntimeError, 'END_COMPONENT_TEST'): asyncio.run(namespace['run']())
        self.assertEqual(closed, [True])
        return acknowledgments
    def test_worker_held_reservation_not_acknowledged(self): self.assertEqual(self.worker(True), [])
    def test_worker_normal_ack_unchanged(self): self.assertEqual(self.worker(False), ['reserved-task'])


suite = unittest.TestSuite(ExecutionTests(n) for n in sorted(dir(ExecutionTests)) if n.startswith('test_boundary_'))
suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(PairTests))
suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(WorkerTests))
with patch.object(pair, 'tooling_module', lambda name: importlib.import_module('scripts.'+name)):
    result = unittest.TextTestRunner(verbosity=2).run(suite)
print(json.dumps(dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
    skipped=len(result.skipped), evidence='OFFLINE_COMPONENT_ONLY_SQLITE_QUEUE_DOUBLE_ROOT_AUTHORITY_EMULATION',
    native_full_entry='NOT_VERIFIED', primary_changes=0, production_changes=0, provider_calls=0,
    wechat_calls=0, image_calls=0)))
raise SystemExit(0 if result.wasSuccessful() else 1)
