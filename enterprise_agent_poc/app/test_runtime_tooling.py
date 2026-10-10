"""Root-approved, separate Application/Tooling pair for TEST only.

This bootstrap runs before importing external Tooling. No request or environment
variable selects code. The immutable pair is issued by release control, not by
the application. It does not grant Admin, Publish or provider permission.
"""
from __future__ import annotations

import hashlib
import importlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path('/etc/enterprise-agent-test-exact-admin-v1')
PAIR = ROOT / 'runtime-pair.v1.json'
CONTRACT = 'INTEGRATED_RUNTIME_RELEASE_PAIR_V1'
APPLICATION = Path(__file__).resolve().parents[1]
TEST_ROOT = Path('/opt/enterprise-agent-workbench-test')
ISOLATED_ROOT = Path('/opt/enterprise-agent-native-isolated')
ISOLATED_AUTHORITY = Path('/etc/enterprise-agent-native-isolated-v1')
ISOLATED_CONTRACT = 'INTEGRATED_RUNTIME_RELEASE_PAIR_V2'


def authority_root():
    # Installed Application location, never an HTTP field, DSN or environment
    # override, chooses the trust root. PRIMARY cannot load an isolated seal.
    if APPLICATION.parent.parent == ISOLATED_ROOT / 'releases':
        return ISOLATED_AUTHORITY
    return ROOT


def require(value, code):
    if not value:
        raise RuntimeError(code)


def checkout_identity(path):
    # The registered checkout is intentionally owned by root, not the service
    # UID. Trust only this exact path for this command; never set global '*'.
    result = subprocess.run(['git', '--no-optional-locks', '-c', 'safe.directory='+str(path),
        '-C', str(path), 'rev-parse', 'HEAD', 'HEAD^{tree}'], check=True,
        capture_output=True, timeout=20, text=True)
    source, tree = result.stdout.strip().splitlines()
    return {'source_commit': source, 'source_tree': tree}


def verify_checkout(path, identity):
    require(set(identity) == {'path', 'source', 'tree', 'files'}, 'RUNTIME_PAIR_CHECKOUT_SHAPE')
    require(all(re.fullmatch('[0-9a-f]{40}', identity[k]) for k in ('source', 'tree')),
            'RUNTIME_PAIR_EXACT_GIT_REQUIRED')
    for item in (path, *path.parents):
        info = item.lstat()
        require(info.st_uid == info.st_gid == 0 and not stat.S_ISLNK(info.st_mode)
                and not info.st_mode & 0o022, 'RUNTIME_PAIR_PATH_TRUST')
    actual = {}
    for item in path.rglob('*'):
        relative = item.relative_to(path)
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == info.st_gid == 0
                and not info.st_mode & 0o022, 'RUNTIME_PAIR_INSTALLED_PATH_TRUST')
        if '.git' in relative.parts:
            # Git metadata is excluded from the byte map, not from ownership
            # checks. The runtime UID must not be able to forge HEAD/config.
            continue
        if item.is_file():
            require(not info.st_mode & 0o222, 'RUNTIME_PAIR_READONLY_SOURCE_REQUIRED')
            actual[relative.as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
    require(checkout_identity(path) == {
        'source_commit': identity['source'], 'source_tree': identity['tree']}, 'RUNTIME_PAIR_GIT_MISMATCH')
    require(actual == identity['files'], 'RUNTIME_PAIR_INSTALLED_FILE_MAP')


def load_pair():
    from app.test_tenant_seeding import native_json
    require(os.environ.get('APP_ENV') == 'test' and sys.dont_write_bytecode
            and os.environ.get('PYTHONDONTWRITEBYTECODE') == '1', 'RUNTIME_PAIR_TEST_ENVIRONMENT')
    root = authority_root()
    isolated = root == ISOLATED_AUTHORITY
    pair, sha = native_json(root / 'runtime-pair.v1.json')
    require(set(pair) == {'contract', 'environment', 'application', 'tooling', 'authority',
            'production_authority'} and pair['contract'] == (ISOLATED_CONTRACT if isolated else CONTRACT) and pair['environment'] == 'test'
            and pair['production_authority'] is False and pair['authority'] ==
            ('NATIVE_PARENT_CHAIN_ISOLATION_REFACTOR_APPROVED' if isolated else
             'INTEGRATED_RUNTIME_RELEASE_RECOVERY_FIX_APPROVED'), 'RUNTIME_PAIR_AUTHORITY')
    release_root = ISOLATED_ROOT if isolated else TEST_ROOT
    for role, parent in (('application', release_root / 'releases'),
                         ('tooling', release_root / 'shared/source-qualifications')):
        require(set(pair[role]) == {'path', 'source', 'tree', 'files_sha256'}, 'RUNTIME_PAIR_IDENTITY_SHAPE')
        path = Path(pair[role]['path'])
        require(path.parent == parent and path.name not in ('', '.', '..'), 'RUNTIME_PAIR_FIXED_ROOT')
        files, files_sha = native_json(root / (role + '-files.v1.json'))
        require(files_sha == pair[role]['files_sha256'], 'RUNTIME_PAIR_FILE_MANIFEST_PIN')
        verify_checkout(path, {k: pair[role][k] for k in ('path', 'source', 'tree')} | {'files': files})
    require(Path(pair['application']['path']) / 'enterprise_agent_poc' == APPLICATION,
            'RUNTIME_PAIR_LOADED_APPLICATION_MISMATCH')
    return pair, sha


def tooling_module(name):
    require(name in {'exact_test_admin_lifecycle', 'wechat_runtime_native_successor', 'native_parent_contract'}, 'RUNTIME_PAIR_MODULE')
    pair, _ = load_pair()
    project = Path(pair['tooling']['path']) / 'enterprise_agent_poc'
    # scripts is a namespace shared with the app's receipt codec. Route only the
    # approved Tooling package first; reject already imported foreign modules.
    import scripts
    target = str(project / 'scripts')
    if target not in scripts.__path__:
        scripts.__path__ = [target, *scripts.__path__]
    module = importlib.import_module('scripts.' + name)
    require(Path(module.__file__).resolve() == project / 'scripts' / (name + '.py'),
            'RUNTIME_PAIR_IMPORTED_MODULE_MISMATCH')
    return module
