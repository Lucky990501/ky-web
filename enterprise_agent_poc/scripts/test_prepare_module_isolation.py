"""TEST ONLY: verify dca/d8a guard modules load under isolated identities.

This checker grants no install, Provision, switch, or Production authority.
The real Prepare remains a separate fixed subprocess; this module only proves
that both frozen guard files resolve independently in either load order.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import stat
import sys

NATIVE = Path('/etc/enterprise-agent-test-successor-wechat-d8a-common-cache-v1')
DCA_COMMON = Path('/etc/enterprise-agent-test-successor-wechat-dca-v1/common.py')
D8A_COMMON = NATIVE / 'common.py'
MODULES = {
    'dca': (DCA_COMMON, 'dca318de578a9b9601ad036e1b176bc5ab702029', 'fc28a7666b5a1224fa39991aee574527f75834aa'),
    'd8a': (D8A_COMMON, 'd8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7', '6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5'),
}


class ModuleIsolationBlocked(PermissionError):
    """Fail-closed public category; never include module internals."""


def require(value: bool, code: str) -> None:
    if not value:
        raise ModuleIsolationBlocked(code)


def trusted(path: Path) -> None:
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
                and not info.st_mode & 0o022, 'UNTRUSTED_MODULE_PATH')
    require(path.is_file() and not path.stat().st_mode & 0o222, 'WRITABLE_MODULE_REJECTED')


def fixed_order(order: tuple[str, str]) -> None:
    require(order in (('dca', 'd8a'), ('d8a', 'dca')), 'UNAUTHORIZED_MODULE_ORDER')


def validate_module(kind: str, module, expected_path: Path | None = None,
                    expected_source: str | None = None, expected_tree: str | None = None) -> dict:
    require(kind in MODULES, 'UNAUTHORIZED_MODULE_KIND')
    path, source, tree = MODULES[kind]
    if expected_path is not None:
        require(expected_path == path, 'UNAUTHORIZED_MODULE_PATH')
    if expected_source is not None:
        require(expected_source == source, 'WRONG_SOURCE_REJECTED')
    if expected_tree is not None:
        require(expected_tree == tree, 'WRONG_TREE_REJECTED')
    require(Path(module.__file__).resolve() == path.resolve(), 'MODULE_FILE_IDENTITY_REJECTED')
    require(module.SOURCE == source and module.TREE == tree, 'MODULE_SOURCE_TREE_REJECTED')
    if kind == 'd8a':
        require(callable(getattr(module, 'transport_identity', None)), 'D8A_TRANSPORT_GUARD_MISSING')
    return {'kind': kind, 'path': str(path), 'source': source, 'tree': tree}


def load_fixed(kind: str, sequence: int):
    require(kind in MODULES, 'UNAUTHORIZED_MODULE_KIND')
    path = MODULES[kind][0]
    trusted(path)
    name = f'test_prepare_module_isolation_{sequence}_{kind}'
    require(name != 'common' and name not in sys.modules, 'MODULE_NAME_COLLISION')
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, 'MODULE_SPEC_REJECTED')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_order(order: tuple[str, str]) -> dict:
    fixed_order(order)
    common_before = sys.modules.get('common')
    resolved = []
    for sequence, kind in enumerate(order):
        resolved.append(validate_module(kind, load_fixed(kind, sequence)))
    require(sys.modules.get('common') is common_before, 'GLOBAL_COMMON_CACHE_MUTATED')
    require(resolved[0]['path'] != resolved[1]['path'], 'MODULE_PATHS_NOT_ISOLATED')
    return {'order': list(order), 'resolved': resolved, 'common_cache_unchanged': True}


def verify() -> dict:
    require(os.environ.get('APP_ENV') == 'test', 'PRODUCTION_MODULE_ISOLATION_REJECTED')
    require(os.geteuid() == 0, 'ROOT_MODULE_VERIFICATION_REQUIRED')
    require(Path(__file__).parent == NATIVE, 'ROOT_MUST_NOT_EXECUTE_MUTABLE_TOOLING')
    trusted(Path(__file__))
    first = check_order(('dca', 'd8a'))
    second = check_order(('d8a', 'dca'))
    return {'status': 'PREPARE_MODULE_ISOLATION_PASS', 'orders': [first, second],
            'formal_prepare_execution': 'SEPARATE_FIXED_SUBPROCESS',
            'global_common_cache_mutation': False, 'production_deploy_authority': False}


if __name__ == '__main__':
    import json
    try:
        print(json.dumps(verify(), sort_keys=True))
    except ModuleIsolationBlocked as error:
        print(json.dumps({'status': 'BLOCKED', 'category': str(error)}, sort_keys=True))
        raise SystemExit(2)
