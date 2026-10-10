"""Targeted synthetic qualification/controlled/dispatch regression. No live I/O."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
sys.dont_write_bytecode = True
import test_skill_only_test_qualification
import test_controlled_skill_action
import test_skill_dispatch

if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromModule(module) for module in (
            test_skill_only_test_qualification, test_controlled_skill_action, test_skill_dispatch)))
    print(json.dumps(dict(tests=result.testsRun, failed=len(result.failures), errors=len(result.errors),
        skipped=len(result.skipped), scope='OFFLINE_REAL_TASK_MCP_GATES_NATIVE_HTTP_RUNTIME_PROCESS_FIXTURES',
        provider_calls=0, image_calls=0, wechat_calls=0, primary_changes=0)))
    raise SystemExit(0 if result.wasSuccessful() else 1)
