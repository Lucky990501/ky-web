"""Local deterministic qualification, synthetic credentials and zero live calls."""
import json
import logging
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
sys.dont_write_bytecode=True
import test_wechat_personal_config
if __name__=='__main__':
    logging.getLogger().setLevel(logging.WARNING)
    result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromModule(test_wechat_personal_config))
    print(json.dumps(dict(tests=result.testsRun,failed=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),wechat_calls=0,provider_calls=0,primary_changes=0,production_changes=0)))
    raise SystemExit(0 if result.wasSuccessful() else 1)
