"""Targeted offline Agent/MCP dispatch verification, no Settings/secret loading."""
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
sys.dont_write_bytecode=True
import test_skill_dispatch


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromModule(test_skill_dispatch))
    print(json.dumps(dict(tests=result.testsRun,failed=len(result.failures),errors=len(result.errors),
        skipped=len(result.skipped),scope='OFFLINE_AGENT_MCP_WITH_RUNTIME_PROCESS_MOCK',
        provider_calls=0,image_calls=0,wechat_calls=0,primary_changes=0)))
    raise SystemExit(0 if result.wasSuccessful() else 1)
