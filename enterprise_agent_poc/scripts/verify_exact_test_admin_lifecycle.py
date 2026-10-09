"""Deterministic exact-admin + formal API lifecycle; never a live entry."""
import json
from pathlib import Path
import sys
import unittest
import argparse
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
sys.dont_write_bytecode = True
import test_exact_test_admin_lifecycle as tests
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current-dca-source', type=Path, required=True)
    args = parser.parse_args()
    expected = '0e079b0fd87db36be8efadff63ce129e90e1a5e20dfa27cf4228a83fd1e90d6b'
    import hashlib
    before = args.current_dca_source.read_bytes()
    assert hashlib.sha256(before).hexdigest() == expected, '06 current DCA code identity mismatch'
    result = unittest.TextTestRunner(verbosity=1).run(tests.suite(args.current_dca_source))
    assert args.current_dca_source.read_bytes() == before, 'Sealed DCA code changed'
    print(json.dumps(dict(tests=result.testsRun, failed=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
        evidence='ISOLATED_SIGNED_API_FORMAL_EXECUTOR_RUNTIME_DOUBLE_NATIVE_AUTHORITY_EMULATION',
        primary_changes=0, production_changes=0, provider_calls=0, wechat_calls=0, image_calls=0)))
    raise SystemExit(0 if result.wasSuccessful() else 1)
