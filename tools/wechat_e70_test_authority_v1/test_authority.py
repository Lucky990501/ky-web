"""Isolated component tests. These tests never grant real Root/Test authority."""
import copy
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('e70_authority', HERE / 'authority.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
WORKSPACE = HERE.parents[3]


def fixture(name, default):
    path = Path(os.environ.get(name, str(WORKSPACE / default))).resolve()
    if not path.is_file():
        raise RuntimeError('REQUIRED_ISOLATED_TEST_FIXTURE_MISSING:' + name)
    return path


class Policy(unittest.TestCase):
    def setUp(self):
        self.op = str(uuid.uuid4())
        self.c = a.contract_value('TEST-AUDIT-NOT-REAL-APPROVAL', self.op,
                                  (a.now() + dt.timedelta(hours=1)).isoformat(),
                                  'a' * 40, 'b' * 40, 'c' * 64,
                                  {'history': 'test'}, {'current': str(a.BASELINE), 'services': {}})

    def test_exact_e70_and562(self):
        a.validate_contract(self.c)
        self.assertEqual(self.c['application']['source'], a.SOURCE)
        self.assertEqual(self.c['exact_predecessor'], a.PREDECESSOR)

    def rejected(self, edit):
        c = copy.deepcopy(self.c)
        edit(c)
        with self.assertRaises(a.Block):
            a.validate_contract(c)

    def test_wrong_application(self): self.rejected(lambda c: c['application'].update(source='0' * 40))
    def test_wrong_tree(self): self.rejected(lambda c: c['application'].update(tree='0' * 40))
    def test_wrong_parent(self): self.rejected(lambda c: c['application'].update(parent='0' * 40))
    def test_wrong_predecessor(self): self.rejected(lambda c: c['exact_predecessor'].update(source_commit='0' * 40))
    def test_production_authority(self): self.rejected(lambda c: c.update(production_deploy_authority=True))
    def test_wrong_schema(self): self.rejected(lambda c: c.update(schema='001-016'))
    def test_migration(self): self.rejected(lambda c: c.update(migration='APPLY'))
    def test_db_writes(self): self.rejected(lambda c: c['permissions'].update(database_mutation=True))
    def test_restart(self): self.rejected(lambda c: c['permissions'].update(existing_service_restart=True))
    def test_switch(self): self.rejected(lambda c: c['permissions'].update(release_pointer_switch=True))
    def test_provider(self): self.rejected(lambda c: c['permissions'].update(provider_calls=1))
    def test_wechat(self): self.rejected(lambda c: c['permissions'].update(wechat_calls=1))
    def test_image(self): self.rejected(lambda c: c['permissions'].update(image_calls=1))
    def test_native_recovery(self): self.rejected(lambda c: c.update(native_recovery_authority=True))
    def test_unbounded_memory(self): self.rejected(lambda c: c['limits'].update(MemoryMax='infinity'))
    def test_install_outside_scope(self): self.rejected(lambda c: c.update(installed_candidate='/opt/production/fake'))
    def test_extra_field(self): self.rejected(lambda c: c.update(skip_gate=True))
    def test_bool_as_zero(self): self.rejected(lambda c: c['permissions'].update(provider_calls=False))
    def test_float_as_zero(self): self.rejected(lambda c: c['permissions'].update(provider_calls=0.0))
    def test_expired(self): self.rejected(lambda c: c['audit'].update(expires_at=(a.now() - dt.timedelta(seconds=1)).isoformat()))
    def test_over_24h(self): self.rejected(lambda c: c['audit'].update(expires_at=(a.now() + dt.timedelta(hours=25)).isoformat()))
    def test_no_audit(self): self.rejected(lambda c: c['audit'].update(id=''))
    def test_app_as_tooling(self): self.rejected(lambda c: c['tooling'].update(source=a.SOURCE))
    def test_operation_path_escape(self):
        with self.assertRaises(a.Block): a.scope_id('../fake')

    def test_duplicate_json(self):
        with self.assertRaises(a.Block): a.decode(b'{"scope":1,"scope":2}')

    def test_nonfinite_json(self):
        with self.assertRaises(a.Block): a.decode(b'{"x":NaN}')

    def test_duration(self):
        self.assertEqual(a.duration_us('30min'), 1800 * 1000000)
        self.assertEqual(a.duration_us('2s'), 2000000)
        self.assertEqual(a.duration_us('1min 500ms'), 60500000)

    def test_infinite_duration(self):
        with self.assertRaises(a.Block): a.duration_us('infinity')

    def test_cli_has_no_bypass(self):
        p = subprocess.run([sys.executable, '-B', str(HERE / 'authority.py'), 'issue', '--force'],
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)

    def test_plan_is_readonly(self):
        p = subprocess.run([sys.executable, '-B', str(HERE / 'authority.py'), 'plan'],
                           capture_output=True, text=True, check=True)
        v = json.loads(p.stdout)
        self.assertFalse(v['authority_issued'])
        self.assertEqual(v['permissions']['provider_calls'], 0)

    def test_unprotected_process_cannot_issue(self):
        p = subprocess.run([sys.executable, '-B', str(HERE / 'authority.py'), 'issue',
                            '--request', str(HERE / 'no-file'), '--audit-id', 'TEST',
                            '--operation-id', self.op, '--expires-at', self.c['audit']['expires_at'],
                            '--tooling-source', 'a' * 40, '--tooling-tree', 'b' * 40,
                            '--expected-code-sha256', 'c' * 64], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(json.loads(p.stdout)['result'], 'E70_TEST_AUTHORITY_BLOCKED')


class Artifact(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pins = a.load_pins()
        cls.archive = fixture('E70_TEST_ARCHIVE', '.wechat-e70-validation-authority-package-v1/offline-lf/20261011-e70b61a-wechat-html-ui-offline-v1.tar.gz')
        cls.manifest = fixture('E70_TEST_MANIFEST', '.wechat-e70-validation-authority-package-v1/offline-lf/20261011-e70b61a-wechat-html-ui-offline-v1.manifest.json')
        cls.prior_archive = fixture('E70_TEST_PRIOR_ARCHIVE', '.dependency-retry-patch-release-v1/20261007-562f201-reference-dependency-retry-v1.tar.gz')
        cls.prior_manifest = fixture('E70_TEST_PRIOR_MANIFEST', '.dependency-retry-patch-release-v1/20261007-562f201-reference-dependency-retry-v1.manifest.json')
        cls.request = fixture('E70_TEST_REQUEST', 'WECHAT_E70_TEST_VALIDATION_AUTHORITY_REQUEST_V1.json')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.m = json.loads(self.manifest.read_bytes())
        self.addCleanup(self.temp.cleanup)

    def check_manifest(self, value, archive=None, linux=False):
        p = self.root / 'manifest.json'
        p.write_text(json.dumps(value), encoding='utf-8')
        return a.package(archive or self.archive, p, self.pins, linux=linux)

    def test_real_offline_source_byte_pins_not_native(self):
        result = a.package(self.archive, self.manifest, self.pins, linux=False)
        self.assertEqual(result['selected_files'], 144)

    def test_windows_receipt_is_not_linux(self):
        with self.assertRaisesRegex(a.Block, 'LINUX_BUILD_RECEIPT_REQUIRED'):
            a.package(self.archive, self.manifest, self.pins)

    def test_synthetic_linux_metadata_contract_only(self):
        # Explicit synthetic metadata test, NOT a Linux build or Native proof.
        self.m['build_platform'] = 'Linux-x86_64'
        self.assertEqual(self.check_manifest(self.m, linux=True)['source'], a.SOURCE)

    def test_original_predecessor_identity(self):
        v = a.package(self.prior_archive, self.prior_manifest, self.pins, role='predecessor', linux=False)
        self.assertEqual(v['archive_sha256'], a.PREDECESSOR['archive_sha256'])

    def test_predecessor_raw_manifest_reformat_rejected(self):
        value = json.loads(self.prior_manifest.read_bytes())
        p = self.root / 'prior.json'
        p.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaisesRegex(a.Block, 'ORIGINAL_562_ARTIFACT_IDENTITY'):
            a.package(self.prior_archive, p, self.pins, role='predecessor', linux=False)

    def bad(self, change):
        change(self.m)
        with self.assertRaises(a.Block): self.check_manifest(self.m)

    def test_wrong_source(self): self.bad(lambda m: m.update(source_commit='0' * 40))
    def test_wrong_archive(self): self.bad(lambda m: m.update(archive_sha256='0' * 64))
    def test_wrong_tree(self): self.bad(lambda m: m['runtime_only_release'].update(source_tree='0' * 40))
    def test_wrong_predecessor(self): self.bad(lambda m: m['runtime_only_release']['exact_predecessor'].update(release_id='bad'))
    def test_wrong_contract_version(self): self.bad(lambda m: m['runtime_only_release'].update(schema_version=1))
    def test_unknown_contract(self): self.bad(lambda m: m['runtime_only_release'].update(contract_id='unknown'))
    def test_runtime_bool_zero_rejected(self): self.bad(lambda m: m['runtime_only_release'].update(pending=False))
    def test_extra_manifest_field(self): self.bad(lambda m: m.update(skip=True))
    def test_missing_file(self): self.bad(lambda m: m['selected_files'].pop())
    def test_extra_file(self): self.bad(lambda m: m['selected_files'].append('enterprise_agent_poc/fake'))
    def test_duplicate_file(self): self.bad(lambda m: m['selected_files'].append(m['selected_files'][0]))

    def test_exact_request(self):
        self.assertFalse(a.validate_request(self.request.read_bytes())['protected_approval'])

    def test_rewritten_request(self):
        with self.assertRaisesRegex(a.Block, 'EXACT_UPLOADED_REQUEST_HASH_REQUIRED'):
            a.validate_request(self.request.read_bytes() + b' ')

    def test_pins_tamper(self):
        p = self.root / 'e70-package-pins.v1.json'
        p.write_text('{}', encoding='utf-8')
        with patch.object(a, '__file__', str(self.root / 'authority.py')):
            with self.assertRaisesRegex(a.Block, 'E70_FILE_PIN_IDENTITY'): a.load_pins()

    def custom_tar(self, entries):
        p = self.root / 'bad.tar.gz'
        with tarfile.open(p, 'w:gz', format=tarfile.PAX_FORMAT, pax_headers={'comment': a.SOURCE}) as t:
            for name, kind in entries:
                info = tarfile.TarInfo(name)
                info.mode = 0o4644 if kind == 'suid' else 0o644
                if kind == 'link':
                    info.type = tarfile.SYMTYPE
                    info.linkname = '/tmp/escape'
                    t.addfile(info)
                else:
                    info.size = 1
                    t.addfile(info, io.BytesIO(b'x'))
        self.m['archive_sha256'] = a.sha(p.read_bytes())
        return p

    def test_traversal(self):
        p = self.custom_tar([('enterprise_agent_poc/../escape', 'file')])
        with self.assertRaisesRegex(a.Block, 'UNSAFE_TAR_MEMBER'): self.check_manifest(self.m, p)

    def test_symlink(self):
        p = self.custom_tar([('enterprise_agent_poc/link', 'link')])
        with self.assertRaisesRegex(a.Block, 'UNSAFE_TAR_MEMBER'): self.check_manifest(self.m, p)

    def test_changed_business_bytes(self):
        name = self.m['selected_files'][0]
        p = self.custom_tar([(name, 'file')])
        with self.assertRaisesRegex(a.Block, 'ALL_PACKAGE_BYTE_MODE_PINS_REQUIRED'): self.check_manifest(self.m, p)

    def test_wrong_tar_source(self):
        p = self.root / 'wrong.tar.gz'
        with tarfile.open(p, 'w:gz', format=tarfile.PAX_FORMAT, pax_headers={'comment': '0' * 40}) as t:
            info = tarfile.TarInfo('enterprise_agent_poc/file')
            info.size = 1
            t.addfile(info, io.BytesIO(b'x'))
        self.m['archive_sha256'] = a.sha(p.read_bytes())
        with self.assertRaisesRegex(a.Block, 'TAR_SOURCE_MISMATCH'): self.check_manifest(self.m, p)

    def test_duplicate_tar_member(self):
        name = self.m['selected_files'][0]
        p = self.custom_tar([(name, 'file'), (name, 'file')])
        with self.assertRaisesRegex(a.Block, 'DUPLICATE_OR_EXCESS_TAR_MEMBER'): self.check_manifest(self.m, p)

    def test_special_mode(self):
        p = self.custom_tar([(self.m['selected_files'][0], 'suid')])
        with self.assertRaisesRegex(a.Block, 'UNSAFE_TAR_MEMBER'): self.check_manifest(self.m, p)

    def test_expansion_limit(self):
        p = self.root / 'expansion.tar.gz'
        p.write_bytes(a.gzip.compress(b'0' * (16 * 1024 * 1024 + 1)))
        self.m['archive_sha256'] = a.sha(p.read_bytes())
        with self.assertRaisesRegex(a.Block, 'TAR_EXPANSION_LIMIT'): self.check_manifest(self.m, p)

    def test_inputs_outside_isolation(self):
        with self.assertRaisesRegex(a.Block, 'ONLY_ISOLATED_ARTIFACT_PATH_ALLOWED'):
            a.isolated_input_path(self.manifest)


class SimulatedSeal(unittest.TestCase):
    """Only synthetic temp files; OS permission calls are mocked, no Root approval."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / 'SIMULATED_NOT_AN_APPROVAL'
        self.addCleanup(self.temp.cleanup)
        self.c = a.contract_value('TEST-SIMULATION-NOT-REAL-AUDIT', str(uuid.uuid4()),
                                 (a.now() + dt.timedelta(hours=1)).isoformat(),
                                 'a' * 40, 'b' * 40, 'c' * 64, {}, {})
        for name in ('fchmod', 'fchown', 'chown'):
            p = patch.object(a.os, name, create=True)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(a.os, 'O_DIRECTORY', 0, create=True)
        p.start()
        self.addCleanup(p.stop)
        # Windows cannot open/fsync a directory; this tests publishing order,
        # not real POSIX directory persistence or ownership.
        self.real_open = os.open
        def local_open(path, flags, *args):
            if Path(path) == self.root and flags == os.O_RDONLY:
                return self.real_open(self.root / 'contract.e70.v1.json', os.O_RDWR)
            return self.real_open(path, flags, *args)
        p = patch.object(a.os, 'open', side_effect=local_open)
        p.start()
        self.addCleanup(p.stop)

    def test_approval_is_last_and_hash_bound(self):
        result = a.seal_scope(self.root, self.c)
        approval = json.loads((self.root / 'approval.e70.v1.json').read_bytes())
        self.assertEqual(approval['contract_sha256'], result['contract_sha256'])
        self.assertEqual(approval['evidence_sha256'], result['evidence_sha256'])

    def test_duplicate_scope_no_overwrite(self):
        a.seal_scope(self.root, self.c)
        before = (self.root / 'approval.e70.v1.json').read_bytes()
        with self.assertRaisesRegex(a.Block, 'SCOPE_ALREADY_EXISTS_NO_OVERWRITE'):
            a.seal_scope(self.root, self.c)
        self.assertEqual(before, (self.root / 'approval.e70.v1.json').read_bytes())

    def test_partial_write_has_no_approval(self):
        original = a.write_exclusive
        def failure(path, value):
            if path.name == 'issuance-evidence.e70.v1.json': raise OSError('synthetic failure')
            return original(path, value)
        with patch.object(a, 'write_exclusive', side_effect=failure):
            with self.assertRaises(OSError): a.seal_scope(self.root, self.c)
        self.assertTrue((self.root / 'contract.e70.v1.json').is_file())
        self.assertFalse((self.root / 'approval.e70.v1.json').exists())


class SimulatedApprovalConsumption(unittest.TestCase):
    """Fake ownership only in ordinary temp files; never a protected approval."""
    def setUp(self):
        SimulatedSeal.setUp(self)
        self.authority = Path(self.temp.name).resolve() / 'SIMULATED_AUTHORITY'
        self.authority.mkdir()
        (self.authority / 'scopes').mkdir()
        self.root = self.authority / 'scopes' / self.c['audit']['operation_id']
        code = (HERE / 'authority.py').read_bytes()
        (self.authority / 'authority.py').write_bytes(code)
        (self.authority / 'e70-package-pins.v1.json').write_bytes((HERE / 'e70-package-pins.v1.json').read_bytes())
        self.c['tooling']['code_sha256'] = a.sha(code)
        self.c['tooling']['entry'] = str(self.authority / 'authority.py')
        self.original_read = a.read_regular
        replacements = {
            'AUTHORITY': self.authority,
            'trusted_directory': lambda p: Path(p).resolve(),
            'read_regular': lambda p, **kw: self.original_read(p, protected=False),
            'history_snapshot': lambda: self.c['historical_authority'],
            'baseline_snapshot': lambda: self.c['test_baseline'],
        }
        for key, value in replacements.items():
            p = patch.object(a, key, value)
            p.start()
            self.addCleanup(p.stop)
        a.seal_scope(self.root, self.c)

    def load(self):
        return a.load_scope(self.c['audit']['operation_id'])

    def tamper(self, name, change):
        p = self.root / name
        v = json.loads(p.read_bytes())
        change(v)
        p.write_text(json.dumps(v), encoding='utf-8')

    def test_exact_sealed_chain_component_only(self):
        self.assertEqual(self.load()['application']['tree'], a.TREE)

    def test_wrong_contract_sha(self):
        self.tamper('approval.e70.v1.json', lambda v: v.update(contract_sha256='0' * 64))
        with self.assertRaisesRegex(a.Block, 'ROOT_APPROVAL_BINDING_MISMATCH'): self.load()

    def test_wrong_approval_tooling(self):
        self.tamper('approval.e70.v1.json', lambda v: v['tooling'].update(source='0' * 40))
        with self.assertRaisesRegex(a.Block, 'ROOT_APPROVAL_BINDING_MISMATCH'): self.load()

    def test_wrong_contract_source(self):
        self.tamper('contract.e70.v1.json', lambda v: v['application'].update(source='0' * 40))
        with self.assertRaisesRegex(a.Block, 'EXACT_TEST_SCOPE_REQUIRED'): self.load()

    def test_wrong_evidence(self):
        self.tamper('issuance-evidence.e70.v1.json', lambda v: v.update(state='NATIVE_PASS'))
        with self.assertRaisesRegex(a.Block, 'ROOT_APPROVAL_BINDING_MISMATCH'): self.load()

    def test_missing_approval(self):
        (self.root / 'approval.e70.v1.json').unlink()
        with self.assertRaises(FileNotFoundError): self.load()

    def test_wrong_trusted_code(self):
        (self.authority / 'authority.py').write_bytes(b'not trusted code')
        with self.assertRaisesRegex(a.Block, 'TRUSTED_CONSUMER_CODE_MISMATCH'): self.load()

    def test_wrong_pins(self):
        (self.authority / 'e70-package-pins.v1.json').write_bytes(b'{}')
        with self.assertRaisesRegex(a.Block, 'E70_FILE_PIN_IDENTITY'): self.load()

    def test_historical_mutation(self):
        with patch.object(a, 'history_snapshot', return_value={'changed': True}):
            with self.assertRaisesRegex(a.Block, 'HISTORICAL_AUTHORITY_CHANGED'): self.load()

    def test_baseline_mutation(self):
        with patch.object(a, 'baseline_snapshot', return_value={'current': 'mixed'}):
            with self.assertRaisesRegex(a.Block, 'D8A_BASELINE_CHANGED'): self.load()

    def test_expired_consumption(self):
        with patch.object(a, 'now', return_value=a.now() + dt.timedelta(hours=2)):
            with self.assertRaisesRegex(a.Block, 'EXPIRY_WITHIN_24H_REQUIRED'): self.load()


class SimulatedExecutionGuard(unittest.TestCase):
    """Kernel metadata seams are simulated; no actual Linux/Root qualification."""
    def setUp(self):
        self.patchers = [
            patch.object(a.sys, 'platform', 'linux'),
            patch.object(a.platform, 'machine', return_value='x86_64'),
            patch.object(a.platform, 'node', return_value=a.HOST),
            patch.object(a.sys, 'flags', types.SimpleNamespace(isolated=1)),
            patch.object(a.sys, 'dont_write_bytecode', True),
            patch.object(a.sys, 'executable', '/usr/bin/python3.10'),
            patch.object(a.os, 'getuid', return_value=0, create=True),
            patch.object(a, '__file__', str(a.AUTHORITY / 'authority.py')),
            patch.object(a.Path, 'resolve', lambda p, **kw: p),
            patch.object(a.Path, 'stat', return_value=types.SimpleNamespace(st_uid=0, st_mode=0o100755)),
            patch.object(a, 'trusted_directory', side_effect=lambda p: p),
            patch.object(a, 'read_regular', return_value=b'simulated trusted code'),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def test_guard_success_is_simulated_only(self): a.execution_guard(signing=True)

    def test_no_root_signing(self):
        with patch.object(a.os, 'getuid', return_value=1000):
            with self.assertRaisesRegex(a.Block, 'ROOT_SIGNER_REQUIRED'): a.execution_guard(signing=True)

    def test_wrong_host(self):
        with patch.object(a.platform, 'node', return_value='production-host'):
            with self.assertRaisesRegex(a.Block, 'EXACT_TEST_HOST_REQUIRED'): a.execution_guard(signing=True)

    def test_no_python_isolation(self):
        with patch.object(a.sys, 'flags', types.SimpleNamespace(isolated=0)):
            with self.assertRaisesRegex(a.Block, 'PYTHON_ISOLATED_NO_PYC_REQUIRED'): a.execution_guard(signing=True)

    def test_user_managed_python(self):
        with patch.object(a.sys, 'executable', '/opt/test/shared/runtime/python311/bin/python'):
            with self.assertRaisesRegex(a.Block, 'SYSTEM_PYTHON_REQUIRED_FOR_AUTHORITY'): a.execution_guard(signing=True)

    def test_wrong_python_owner(self):
        with patch.object(a.Path, 'stat', return_value=types.SimpleNamespace(st_uid=1000, st_mode=0o100755)):
            with self.assertRaisesRegex(a.Block, 'ROOT_SYSTEM_PYTHON_REQUIRED'): a.execution_guard(signing=True)

    def test_group_writable_python(self):
        with patch.object(a.Path, 'stat', return_value=types.SimpleNamespace(st_uid=0, st_mode=0o100775)):
            with self.assertRaisesRegex(a.Block, 'ROOT_SYSTEM_PYTHON_REQUIRED'): a.execution_guard(signing=True)

    def test_untrusted_script_location(self):
        with patch.object(a, '__file__', '/tmp/untrusted-authority.py'):
            with self.assertRaisesRegex(a.Block, 'FIXED_TRUSTED_ENTRY_REQUIRED'): a.execution_guard(signing=True)


class SimulatedResourceGuard(unittest.TestCase):
    def setUp(self):
        self.values = {'User': 'lucky', 'MemoryMax': str(4 * 1024**3),
                       'MemoryHigh': str(3 * 1024**3), 'MemorySwapMax': str(1024**3),
                       'TasksMax': '128', 'RuntimeMaxUSec': '30min', 'CPUQuotaPerSecUSec': '1s'}
        for p in (
            patch.object(a.Path, 'read_text', return_value='0::/enterprise-agent-test.slice/ea-test-e70-verify.service'),
            patch.object(a.subprocess, 'check_output', side_effect=lambda *args, **kw: '\n'.join(k + '=' + v for k, v in self.values.items())),
            patch.object(a.os, 'statvfs', return_value=types.SimpleNamespace(f_bavail=30, f_blocks=100), create=True),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_bounded_component(self): a.resource_guard()
    def test_wrong_slice(self):
        with patch.object(a.Path, 'read_text', return_value='0::/system.slice/api.service'):
            with self.assertRaisesRegex(a.Block, 'E70_BOUNDED_TEST_SLICE_REQUIRED'): a.resource_guard()
    def test_root_job_rejected(self):
        self.values['User'] = 'root'
        with self.assertRaisesRegex(a.Block, 'UNPRIVILEGED_VALIDATION_USER_REQUIRED'): a.resource_guard()
    def test_memory_unlimited(self):
        self.values['MemoryMax'] = 'infinity'
        with self.assertRaisesRegex(a.Block, 'BOUNDED_MemoryMax'): a.resource_guard()
    def test_memory_over_cap(self):
        self.values['MemoryMax'] = str(8 * 1024**3)
        with self.assertRaisesRegex(a.Block, 'BOUNDED_MemoryMax'): a.resource_guard()
    def test_runtime_over_cap(self):
        self.values['RuntimeMaxUSec'] = '31min'
        with self.assertRaisesRegex(a.Block, 'BOUNDED_EXECUTION_TIME_REQUIRED'): a.resource_guard()
    def test_cpu_over_cap(self):
        self.values['CPUQuotaPerSecUSec'] = '3s'
        with self.assertRaisesRegex(a.Block, 'BOUNDED_CPU_QUOTA_REQUIRED'): a.resource_guard()
    def test_disk_floor(self):
        with patch.object(a.os, 'statvfs', return_value=types.SimpleNamespace(f_bavail=19, f_blocks=100)):
            with self.assertRaisesRegex(a.Block, 'ROOT_FREE_SPACE_FLOOR'): a.resource_guard()


if __name__ == '__main__':
    unittest.main()
