"""Offline fake protected-backend API/MCP gate verification; no live calls."""
import json
import logging
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
sys.dont_write_bytecode=True
import test_wechat_secret_provisioning
if __name__=='__main__':
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromModule(test_wechat_secret_provisioning))
    print(json.dumps(dict(tests=result.testsRun,failed=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
        scope='OFFLINE_SYNTHETIC_ENCRYPTED_BACKEND_REAL_API_DB_MCP_GATES',provider_calls=0,wechat_calls=0,image_calls=0,primary_changes=0)))
    raise SystemExit(0 if result.wasSuccessful() else 1)
