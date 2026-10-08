"""Fail-closed tests for the Test-only Prepare module isolation checker."""
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import test_prepare_module_isolation as m


class ModuleIsolationTests(unittest.TestCase):
    def setUp(self):
        self.dca = SimpleNamespace(__file__=str(m.DCA_COMMON), SOURCE=m.MODULES['dca'][1], TREE=m.MODULES['dca'][2])
        self.d8a = SimpleNamespace(__file__=str(m.D8A_COMMON), SOURCE=m.MODULES['d8a'][1], TREE=m.MODULES['d8a'][2],
                                   transport_identity=lambda: None)

    def test_dca_identity(self):
        self.assertEqual(m.validate_module('dca', self.dca)['source'], m.MODULES['dca'][1])

    def test_d8a_identity_and_transport_guard(self):
        self.assertEqual(m.validate_module('d8a', self.d8a)['tree'], m.MODULES['d8a'][2])

    def test_both_orders_are_authorized(self):
        m.fixed_order(('dca', 'd8a'));m.fixed_order(('d8a', 'dca'))

    def test_duplicate_order_rejected(self):
        with self.assertRaisesRegex(m.ModuleIsolationBlocked, 'UNAUTHORIZED_MODULE_ORDER'):
            m.fixed_order(('dca', 'dca'))

    def test_wrong_source_rejected(self):
        with self.assertRaisesRegex(m.ModuleIsolationBlocked, 'WRONG_SOURCE'):
            m.validate_module('d8a', self.d8a, expected_source='0'*40)

    def test_wrong_tree_rejected(self):
        with self.assertRaisesRegex(m.ModuleIsolationBlocked, 'WRONG_TREE'):
            m.validate_module('d8a', self.d8a, expected_tree='0'*40)

    def test_wrong_module_path_rejected(self):
        wrong = SimpleNamespace(**vars(self.d8a));wrong.__file__='/tmp/untrusted/common.py'
        with self.assertRaisesRegex(m.ModuleIsolationBlocked, 'MODULE_FILE_IDENTITY'):
            m.validate_module('d8a', wrong)

    def test_d8a_transport_guard_required(self):
        wrong = SimpleNamespace(__file__=str(m.D8A_COMMON), SOURCE=m.MODULES['d8a'][1], TREE=m.MODULES['d8a'][2])
        with self.assertRaisesRegex(m.ModuleIsolationBlocked, 'TRANSPORT_GUARD'):
            m.validate_module('d8a', wrong)

    def test_production_rejected(self):
        with patch.dict(os.environ, {'APP_ENV':'production'}), \
             self.assertRaisesRegex(m.ModuleIsolationBlocked, 'PRODUCTION'):
            m.verify()

    def test_non_root_rejected(self):
        with patch.dict(os.environ, {'APP_ENV':'test'}), patch.object(os, 'geteuid', return_value=1000, create=True), \
             self.assertRaisesRegex(m.ModuleIsolationBlocked, 'ROOT_MODULE'):
            m.verify()

    def test_common_cache_identity_is_not_target_name(self):
        for sequence, kind in enumerate(('dca','d8a')):
            self.assertNotEqual(f'test_prepare_module_isolation_{sequence}_{kind}', 'common')


if __name__ == '__main__':
    unittest.main()
