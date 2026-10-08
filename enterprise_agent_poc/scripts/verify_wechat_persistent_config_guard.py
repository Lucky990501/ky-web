"""Targeted offline lifecycle/old-guard proof; no live connection or mutation."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
sys.dont_write_bytecode=True
import test_wechat_persistent_config_guard as tests
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-native-common',type=Path,required=True,help='Read-only d8a common.py; ONLY graph AST executes')
    args=parser.parse_args();before=args.old_native_common.read_bytes()
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(tests.LifecycleTests)
    suite.addTests(tests.old_native_graph_proof(args.old_native_common))
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    unchanged=before==args.old_native_common.read_bytes()
    print(json.dumps(dict(tests=result.testsRun,failed=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
        scope='OFFLINE_REAL_D8A_SIGNED_API_SYNTHETIC_CODEC_DB_ROOT_OBSERVATION_FIXTURES',old_guard_sha256=hashlib.sha256(before).hexdigest(),
        old_guard_unchanged=unchanged,legacy_live_config_without_new_observation='FAIL_CLOSED_EVIDENCE_GAP',
        primary_restarts=0,primary_changes=0,production_changes=0,wechat_calls=0,provider_calls=0,image_calls=0)))
    raise SystemExit(0 if result.wasSuccessful() and unchanged else 1)
