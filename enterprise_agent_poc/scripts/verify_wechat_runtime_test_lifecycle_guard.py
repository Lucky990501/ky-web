"""Offline formal Runtime Test code; no PRIMARY or real Provider/WeChat calls."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
sys.dont_write_bytecode = True
import test_wechat_runtime_test_lifecycle_guard as tests

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--historical-dca-guard', type=Path, required=True,
        help='06-attested imported DCA source; only graph AST runs, not full native verification')
    parser.add_argument('--snapshot', type=Path, required=True, help='Exact 06 read-only snapshot Markdown')
    parser.add_argument('--current-native-root', type=Path, required=True, help='Read-only current code/policy/approval/witness copies')
    args = parser.parse_args(); before = args.historical_dca_guard.read_bytes()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(tests.LifecycleTests)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(tests.CacheTests))
    suite.addTests(tests.old_guard_rejection(args.historical_dca_guard))
    suite.addTests(tests.baseline_proof(args.snapshot, args.current_native_root, args.historical_dca_guard))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    unchanged = before == args.historical_dca_guard.read_bytes()
    print(json.dumps(dict(tests=result.testsRun, failed=len(result.failures), errors=len(result.errors),
        skipped=len(result.skipped), historical_guard_sha256=hashlib.sha256(before).hexdigest(),
        historical_guard_unchanged=unchanged, evidence='FORMAL_D8A_API_EXECUTOR_WITH_RUNTIME_DOUBLE_ONLY',
        live_quality='NOT_PROVEN', current_native='06_SNAPSHOT_HASH_AND_SCHEMA_CHECKED_NOT_INTEGRATED',
        formal_admin_grant_revoke='UNAVAILABLE_IN_D8A', primary_changes=0, production_changes=0,
        provider_calls=0, wechat_calls=0, image_calls=0)))
    raise SystemExit(0 if result.wasSuccessful() and unchanged else 1)
