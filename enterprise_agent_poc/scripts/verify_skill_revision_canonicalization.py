"""Targeted canonical identity tests only: no model, service or Skill execution."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
sys.dont_write_bytecode = True
import test_skill_revision_canonicalization

if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=1).run(
        unittest.defaultTestLoader.loadTestsFromModule(test_skill_revision_canonicalization))
    print(json.dumps(dict(tests=result.testsRun, failed=len(result.failures), errors=len(result.errors),
        skipped=len(result.skipped), scope='OFFLINE_PINNED_4CA_REAL_THIRTEEN_FILES_ORDER_PARITY',
        provider_calls=0, image_calls=0, wechat_calls=0, primary_changes=0)))
    raise SystemExit(0 if result.wasSuccessful() else 1)
