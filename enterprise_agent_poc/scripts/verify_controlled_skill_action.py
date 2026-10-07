"""Targeted offline suite only; neither imports Settings nor executes a Skill."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
sys.dont_write_bytecode = True
import test_controlled_skill_action
import test_skill_dispatch


if __name__ == '__main__':
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(module)
        for module in (test_controlled_skill_action, test_skill_dispatch))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    print(json.dumps(dict(tests=result.testsRun, failed=len(result.failures), errors=len(result.errors),
        skipped=len(result.skipped), scope='OFFLINE_REAL_SERVICES_MCP_SYNTHETIC_RUNTIME_PROCESS',
        provider_calls=0, image_calls=0, wechat_calls=0, primary_changes=0)))
    raise SystemExit(0 if result.wasSuccessful() else 1)
