"""Offline source/package/behavior verification. Does not connect to services."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.dont_write_bytecode=True
from app import wechat_skill as adapter


def module_from_bytes(name,raw,path):
    module=types.ModuleType(name);module.__file__=str(path);sys.modules[name]=module
    exec(compile(raw,str(path),'exec'),module.__dict__)
    return module


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original-zip',type=Path)
    parser.add_argument('--build-package',type=Path)
    parser.add_argument('--runtime-tests',action='store_true',help='Run only new revision Python runtime contracts')
    args=parser.parse_args()
    if args.build_package:
        raw=adapter.native_package();args.build_package.parent.mkdir(parents=True,exist_ok=True)
        if args.build_package.exists() and args.build_package.read_bytes()!=raw:
            raise ValueError('ARTIFACT_ALREADY_EXISTS_WITH_DIFFERENT_IDENTITY')
        if not args.build_package.exists():args.build_package.write_bytes(raw)
        print(json.dumps(adapter.revision_contract(),ensure_ascii=False));return
    sys.path.insert(0,str(ROOT/'tests'))
    import test_wechat_skill_integration as integration
    # The original suite is unchanged; replace only its temporary-directory
    # allocation to preserve the current Windows sandbox's inherited ACLs.
    if args.runtime_tests:
        import test_skill_python_runtime
        suite=unittest.defaultTestLoader.loadTestsFromModule(test_skill_python_runtime)
    elif args.original_zip:
        raw=args.original_zip.read_bytes()
        if adapter.digest(raw)!=adapter.ORIGINAL_SHA:raise ValueError('ORIGINAL_ZIP_SHA_MISMATCH')
        with zipfile.ZipFile(args.original_zip) as archive:
            module_from_bytes('wechat_draft',archive.read('wechat-html-draft/scripts/wechat_draft.py'),'original/wechat_draft.py')
            suite_module=module_from_bytes('original_tests',archive.read('wechat-html-draft/scripts/test_workflow.py'),'original/test_workflow.py')
        suite=unittest.defaultTestLoader.loadTestsFromModule(suite_module)
    else:
        sys.path.insert(0,str(adapter.SOURCE/'scripts'))
        import test_workflow
        import test_wechat_action_contract
        suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromModule(test_workflow),
                                 unittest.defaultTestLoader.loadTestsFromModule(integration),
                                 unittest.defaultTestLoader.loadTestsFromModule(test_wechat_action_contract)])
    from unittest.mock import patch
    import tempfile
    with patch.object(tempfile,'TemporaryDirectory',integration.Scratch):
        result=unittest.TextTestRunner(verbosity=1).run(suite)
    print(json.dumps(dict(tests=result.testsRun,failed=len(result.failures),errors=len(result.errors),
                         skipped=len(result.skipped),scope='OFFLINE_LOCAL_ONLY',wechat_calls=0)))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__=='__main__':main()
