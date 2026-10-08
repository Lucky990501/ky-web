"""Offline pinned V2 Provision/Canonicalization/API/cleanup compatibility proof."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
sys.dont_write_bytecode=True
import test_tenant_agent_seeding_v2 as tests  # Stubs Settings before formal tooling imports.

def load_fixture(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tooling-root',type=Path,required=True,help='Read-only clean accepted 85a1451 checkout; NOT a live command')
    parser.add_argument('--native-guard-source',type=Path,required=True,help='Read-only original canonical-v2 common.py; only graph AST executes')
    args=parser.parse_args();project=args.tooling_root/'enterprise_agent_poc'
    identity=subprocess.run(['git','-C',str(args.tooling_root),'rev-parse','HEAD','HEAD^{tree}'],capture_output=True,text=True,timeout=10,check=True).stdout.splitlines()
    assert identity==[tests.TOOL_SOURCE,tests.TOOL_TREE],'Wrong formal tooling identity'
    assert not subprocess.run(['git','-C',str(args.tooling_root),'status','--porcelain','--untracked-files=no'],capture_output=True,text=True,timeout=10,check=True).stdout.strip(),'Dirty formal tooling'
    pins={project/'scripts/receipt_row_canonicalization.py':tests.CANON_SHA,
        project/'scripts/provision_test_tenant.py':tests.PROVISION_SHA,args.native_guard_source:tests.GUARD_SHA}
    for path,pin in pins.items():assert tests.sha(path.read_bytes())==pin,'Formal source pin drift'
    before={path:path.read_bytes() for path in pins}
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(tests.CanonicalIdentityTests)
    frozen=subprocess.run(['git','-C',str(ROOT.parent),'show',
        '9e6daef89a0aa6bd10bb8788fbc73207d11dccd5:enterprise_agent_poc/app/test_tenant_seeding.py'],
        capture_output=True,timeout=10,check=True).stdout
    suite.addTests(tests.integration_suite(project/'scripts/provision_test_tenant.py',args.native_guard_source,frozen))
    # Original 58 accepted tooling fixtures, including V2 exact cleanup/replay.
    minimal=load_fixture('test_wechat_minimal_provision',project/'tests/test_wechat_minimal_provision.py')
    canonical=load_fixture('test_receipt_row_canonicalization',project/'tests/test_receipt_row_canonicalization.py')
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(minimal))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(canonical))
    with tests.fixture_posix_credentials(lambda p:p.name=='user.env' and p.parent.name.startswith('wechat-minimal-unit-')):
        result=unittest.TextTestRunner(verbosity=1).run(suite)
    unchanged=all(path.read_bytes()==raw for path,raw in before.items())
    print(json.dumps(dict(tests=result.testsRun,failed=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
        tooling_source=tests.TOOL_SOURCE,tooling_tree=tests.TOOL_TREE,formal_sources_unchanged=unchanged,
        canonical_sha256=tests.CANON_SHA,native_guard_sha256=tests.GUARD_SHA,
        scope='OFFLINE_FORMAL_V2_PROVISION_REAL_RECEIPT_CANONICAL_ROWS_SQLITE_API_LIFESPAN_CLEANUP_FAKE_NATIVE_TRUST',
        posix_credential_stat='NATIVE' if tests.os.name=='posix' else 'EXACT_EPHEMERAL_WINDOWS_FIXTURE_EMULATION',
        primary_changes=0,production_changes=0,wechat_calls=0,provider_calls=0,image_calls=0)))
    raise SystemExit(0 if result.wasSuccessful() and unchanged else 1)
