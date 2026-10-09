"""Deterministic parent-boundary tests. NOT native/systemd qualification."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import native_parent_contract as p
from scripts.wechat_runtime_test_lifecycle_guard import Blocked, digest


class ParentTests(unittest.TestCase):
    def setUp(self):
        self.pair = {'contract': 'INTEGRATED_RUNTIME_RELEASE_PAIR_V2', 'environment': 'test',
            'production_authority': False, 'application': {'path': str(p.HOME/'releases/app')},
            'tooling': {'path': str(p.HOME/'shared/source-qualifications/tool')}}
        self.context = dict(contract=p.VERSION, realm=p.REALM, environment='test', production_authority=False,
            security=dict(pair_sha256='a'*64, issuer_sha256='b'*64, schema_sha256='c'*64,
            database=dict(host='127.0.0.1', port=56432, name='native_isolated', role='native_isolated',
                          system_identifier='1234567890123456789', server_version=160006),
            predecessor=dict(kind='ISOLATED_EMPTY_DATABASE', system_identifier='1234567890123456789',
                             bootstrap_receipt_sha256='d'*64)),
            state=dict(tenant_id='native-isolated-test', principal_id='actor', agent_id='agent', revision_id='revision',
                       row_pins={}, credit_account={}))
        self.data = dict(tasks=[], run_traces=[], task_events=[], conversations=[], messages=[],
                         enterprise_configs=[{'tenant_id': 'native-isolated-test', 'payload': '{}'}],
                         users=[{'id': 'actor', 'tenant_id': 'native-isolated-test'}])
        self.context['state']['row_pins'] = {t: [digest(r) for r in rows] for t, rows in self.data.items()}

    def validate(self): p.validate_context(self.context, self.pair, 'a'*64)
    def state(self, tasks=()): return p.validate_state(self.data, self.context, set(tasks))
    def test_valid_independent_context(self): self.validate()
    def test_production_context(self):
        self.context['environment'] = 'production'
        with self.assertRaises(Blocked): self.validate()
    def test_production_authority(self):
        self.context['production_authority'] = True
        with self.assertRaises(Blocked): self.validate()
    def test_primary_path(self):
        self.pair['application']['path'] = '/opt/enterprise-agent-workbench-test/releases/app'
        with self.assertRaises(Blocked): self.validate()
    def test_primary_pair(self):
        self.pair['contract'] = 'INTEGRATED_RUNTIME_RELEASE_PAIR_V1'
        with self.assertRaises(Blocked): self.validate()
    def test_wrong_pair_pin(self):
        self.context['security']['pair_sha256'] = 'e'*64
        with self.assertRaises(Blocked): self.validate()
    def test_primary_database(self):
        self.context['security']['database']['port'] = 55432
        with self.assertRaises(Blocked): self.validate()
    def test_wildcard_database(self):
        self.context['security']['database']['host'] = '0.0.0.0'
        with self.assertRaises(Blocked): self.validate()
    def test_cluster_predecessor_mismatch(self):
        self.context['security']['predecessor']['system_identifier'] = '2234567890123456789'
        with self.assertRaises(Blocked): self.validate()
    def test_real_tenant_name(self):
        self.context['state']['tenant_id'] = 'internal-test-staging-v1'
        with self.assertRaises(Blocked): self.validate()
    def test_unknown_contract_field(self):
        self.context['ignore_guard'] = True
        with self.assertRaises(Blocked): self.validate()
    def test_no_state_changed(self): self.assertEqual(self.state()['status'], 'PASS')
    def test_config_not_wholly_mutable(self):
        self.data['enterprise_configs'][0]['payload'] = '{"injected":true}'
        with self.assertRaisesRegex(Blocked, 'BASELINE_MUTATION'): self.state()
    def test_identity_row_cannot_disappear(self):
        self.data['users'] = []
        with self.assertRaisesRegex(Blocked, 'BASELINE_MUTATION'): self.state()
    def test_added_table(self):
        self.data['unexpected'] = []
        with self.assertRaisesRegex(Blocked, 'TABLE_SET'): self.state()
    def task(self):
        row = dict(id='task', tenant_id='native-isolated-test', user_id='actor', agent_id='agent',
                   conversation_id='conversation', run_id='run', status='queued')
        self.data['tasks'].append(row)
        return row
    def test_unadmitted_task(self):
        self.task()
        with self.assertRaisesRegex(Blocked, 'UNAPPROVED_ROW'): self.state()
    def test_exact_admitted_task(self):
        self.task(); self.assertEqual(self.state(['task'])['status'], 'PASS')
    def test_cross_tenant(self):
        self.task()['tenant_id'] = 'foreign'
        with self.assertRaisesRegex(Blocked, 'TASK_OWNER'): self.state(['task'])
    def test_cross_user(self):
        self.task()['user_id'] = 'foreign'
        with self.assertRaisesRegex(Blocked, 'TASK_OWNER'): self.state(['task'])
    def test_foreign_event(self):
        self.task(); self.data['task_events'].append({'task_id': 'foreign'})
        with self.assertRaisesRegex(Blocked, 'UNAPPROVED_ROW'): self.state(['task'])
    def test_deleted_admitted_task(self):
        with self.assertRaisesRegex(Blocked, 'ADMISSION_TASK_SET'): self.state(['nonexistent'])


if __name__ == '__main__':
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(ParentTests))
    print(json.dumps(dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                         skipped=len(result.skipped), evidence='COMPONENT_ONLY_NOT_NATIVE')))
    raise SystemExit(not result.wasSuccessful())
