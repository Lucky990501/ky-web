"""Targeted offline SQLite/native-authority fixtures; never run live guard code."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
sys.dont_write_bytecode=True
import test_tenant_agent_seeding as tests
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-guard-source',type=Path,required=True,help='Read-only existing guard source; ONLY graph AST compiled with synthetic dependencies')
    args=parser.parse_args()
    before=args.native_guard_source.read_bytes()
    suite=unittest.defaultTestLoader.loadTestsFromModule(tests)
    suite.addTest(unittest.FunctionTestCase(lambda:tests.verify_existing_native_guard(unittest.TestCase(),args.native_guard_source),
        description='Existing native graph rejects synthetic Agent Instances; source unchanged'))
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    unchanged=before==args.native_guard_source.read_bytes()
    print(json.dumps(dict(tests=result.testsRun,failed=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
        scope='OFFLINE_ISOLATED_SQLITE_FAKE_NATIVE_AUTHORITY_ACTUAL_API_LIFESPAN',native_guard_sha256=hashlib.sha256(before).hexdigest(),
        native_guard_unchanged=unchanged,primary_changes=0,production_changes=0,wechat_calls=0,provider_calls=0,image_calls=0)))
    raise SystemExit(0 if result.wasSuccessful() and unchanged else 1)
