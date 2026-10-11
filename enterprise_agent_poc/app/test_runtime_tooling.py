"""RAG successor's independently approved Test App/Tooling bootstrap.

No issuer, Source switch, provider grant or fallback to the 28e/2c91 authority.
Gitdir and common-dir trust are checked BEFORE invoking Git.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

CONTRACT = 'RAG_EXACT_TEST_RUNTIME_PAIR_V1'
BASE_SOURCE = '8ff8ddc732d31bb648fa82227097e1b120c9ec64'
BASE_TREE = '63d705caa0952679d95502424e49a327cd967c49'
PREDECESSOR = {'source': 'd8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7',
               'tree': '6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5'}
SCHEMA = {'versions': [f'{i:03}' for i in range(1, 16)], 'migration': 'NONE',
          'fingerprint': '7b1ee4f00c1ba59c0ecd69ae3fdb194708ceca8b157e2879d16b77ae9b6f13b7',
          'pg_version': '16.6', 'vector_version': '0.8.6', 'embedding_dimension': 128,
          'data_contract': 'member_account_status_v1'}
APPLICATION = Path(__file__).resolve().parents[1]
TEST_ROOT = Path('/opt/enterprise-agent-workbench-test')
ROOT = Path('/etc/enterprise-agent-test-rag-successor-v1')
ISOLATED_ROOT = Path('/opt/enterprise-agent-rag-native-isolated')
ISOLATED_AUTHORITY = Path('/etc/enterprise-agent-rag-native-isolated-v1')


def require(value, code):
    if not value:
        raise RuntimeError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, default=str).encode()).hexdigest()


def authority_root():
    location = APPLICATION.parent.parent
    if location == TEST_ROOT / 'releases':
        return ROOT
    if location == ISOLATED_ROOT / 'releases':
        return ISOLATED_AUTHORITY
    raise RuntimeError('RAG_PAIR_UNREGISTERED_INSTALLATION')


def native_installation():
    # A flag can request stricter validation, never choose code/authority.
    mode = os.environ.get('ENTERPRISE_POC_TEST_RAG_NATIVE_REQUIRED', 'false')
    require(mode in ('true', 'false'), 'RAG_NATIVE_FLAG')
    installed = APPLICATION.parent.parent in (TEST_ROOT / 'releases', ISOLATED_ROOT / 'releases')
    if installed or mode == 'true':
        require(os.environ.get('APP_ENV') == 'test', 'RAG_NATIVE_TEST_ONLY')
        authority_root()
        return True
    return False


def trusted(path, *, readonly=False):
    require(path.is_absolute() and path == Path(os.path.normpath(path)), 'RAG_PATH_SHAPE')
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == info.st_gid == 0
                and not info.st_mode & 0o022, 'RAG_PATH_TRUST')
    if readonly:
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o222, 'RAG_READONLY_FILE')


def native_json(path):
    trusted(path, readonly=True)
    info = path.lstat()
    require(info.st_size <= 4_000_000, 'RAG_DOCUMENT_SIZE')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'RAG_DUPLICATE_KEY')
            result[key] = value
        return result
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        actual = os.fstat(stream.fileno())
        require((info.st_dev, info.st_ino) == (actual.st_dev, actual.st_ino), 'RAG_FILE_REPLACED')
        raw = stream.read(4_000_001)
    require(len(raw) <= 4_000_000, 'RAG_DOCUMENT_SIZE')
    return json.loads(raw, object_pairs_hook=unique), hashlib.sha256(raw).hexdigest()


def read_trusted(path):
    trusted(path, readonly=True)
    return path.read_bytes()


def git_metadata(path):
    marker = path / '.git'
    trusted(marker)
    if marker.is_file():
        raw = read_trusted(marker).decode().strip()
        require(raw.startswith('gitdir: ') and '\n' not in raw, 'RAG_GITDIR_MARKER')
        directory = Path(raw[8:])
        if not directory.is_absolute():
            directory = marker.parent / directory
        directory = Path(os.path.abspath(directory))
    else:
        require(marker.is_dir(), 'RAG_GITDIR_MARKER')
        directory = marker
    trusted(directory)
    common = directory
    if (directory / 'commondir').exists():
        trusted(directory / 'commondir', readonly=True)
        raw = (directory / 'commondir').read_text().strip()
        require(bool(raw) and '\n' not in raw, 'RAG_COMMONDIR_MARKER')
        common = Path(os.path.abspath(directory / raw))
        trusted(common)
    for root in {directory, common}:
        require(not (root / 'objects/info/alternates').exists(), 'RAG_EXTERNAL_GIT_OBJECTS')
        for item in root.rglob('*'):
            trusted(item)
    return directory, common


def git_read(path, arguments):
    trusted(path)
    before = git_metadata(path)
    binary = Path('/usr/bin/git')
    trusted(binary)
    command = [str(binary), '--no-optional-locks', '-c', 'core.fsmonitor=false',
        '-c', 'core.hooksPath=/dev/null', '-c', 'safe.directory=' + str(path), '-C', str(path)]
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/nonexistent',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0'}
    result = subprocess.run(command + arguments, env=env, capture_output=True, timeout=20)
    require(result.returncode == 0, 'RAG_GIT_READ_FAILED')
    require(git_metadata(path) == before, 'RAG_GIT_METADATA_REPLACED')
    return result.stdout


def checkout_identity(path):
    fields = git_read(path, ['show', '-s', '--format=%H %T %P', 'HEAD']).decode().strip().split()
    require(len(fields) >= 2 and all(re.fullmatch('[a-f0-9]{40}', x) for x in fields), 'RAG_GIT_IDENTITY')
    return {'source_commit': fields[0], 'source_tree': fields[1], 'parents': fields[2:]}


def verify_checkout(path, identity):
    require(set(identity) == {'path', 'source', 'tree', 'files'}, 'RAG_CHECKOUT_SHAPE')
    require(str(path) == identity['path'] and all(isinstance(identity[k], str)
        and re.fullmatch('[a-f0-9]{40}', identity[k]) for k in ('source', 'tree')), 'RAG_CHECKOUT_IDENTITY')
    require(isinstance(identity['files'], dict) and bool(identity['files']) and all(
        isinstance(k, str) and not Path(k).is_absolute() and '..' not in Path(k).parts
        and k == Path(k).as_posix() and '\\' not in k and '.git' not in Path(k).parts and isinstance(v, str)
        and re.fullmatch('[a-f0-9]{64}', v) for k, v in identity['files'].items()), 'RAG_FILE_MAP_SHAPE')
    trusted(path)
    actual = {}
    blobs = {}
    for item in path.rglob('*'):
        trusted(item)
        relative = item.relative_to(path)
        if '.git' in relative.parts:
            continue
        if item.is_file():
            trusted(item, readonly=True)
            raw = item.read_bytes()
            actual[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
            blobs[relative.as_posix()] = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    observed = checkout_identity(path)
    require((observed['source_commit'], observed['source_tree']) ==
            (identity['source'], identity['tree']), 'RAG_CHECKOUT_MISMATCH')
    require(actual == identity['files'], 'RAG_INSTALLED_FILE_MAP')
    committed = {}
    for entry in git_read(path, ['ls-tree', '-r', '-z', '--full-tree', 'HEAD']).split(b'\0'):
        if not entry:
            continue
        head, name = entry.split(b'\t', 1)
        mode, kind, blob = head.decode().split()
        require(mode in ('100644', '100755') and kind == 'blob', 'RAG_GIT_FILE_TYPE')
        committed[name.decode('utf-8')] = blob
    require(blobs == committed, 'RAG_GIT_BLOB_MAP_MISMATCH')
    return observed


def validate_pair(pair, approval, pair_sha):
    require(isinstance(pair, dict) and isinstance(approval, dict), 'RAG_PAIR_INPUT')
    require(set(pair) == {'contract', 'environment', 'application', 'tooling', 'predecessor',
        'base', 'schema', 'production_authority', 'budget'}, 'RAG_PAIR_SHAPE')
    require(pair['contract'] == CONTRACT and pair['environment'] == 'test'
        and pair['production_authority'] is False and pair['budget'] == {'provider': 0, 'wechat': 0, 'image': 0}
        and all(type(x) is int for x in pair['budget'].values()), 'RAG_PAIR_DOMAIN')
    require(pair['base'] == {'source': BASE_SOURCE, 'tree': BASE_TREE}
        and pair['predecessor'] == PREDECESSOR and pair['schema'] == SCHEMA, 'RAG_EXACT_CONTRACT')
    for name in ('application', 'tooling'):
        value = pair[name]
        require(set(value) == {'path', 'source', 'tree', 'files_sha256'} and all(
            isinstance(value[k], str) and re.fullmatch('[a-f0-9]{40}', value[k]) for k in ('source', 'tree'))
            and re.fullmatch('[a-f0-9]{64}', value['files_sha256']), 'RAG_PAIR_SIDE')
    require(set(approval) == {'contract', 'pair_sha256', 'authorization', 'production_authority',
        'application_source', 'application_tree', 'tooling_source', 'tooling_tree'}, 'RAG_APPROVAL_SHAPE')
    require(approval['contract'] == CONTRACT and approval['pair_sha256'] == pair_sha
        and approval['authorization'] == 'RAG_NATIVE_TEST_PAIR_INDEPENDENTLY_APPROVED'
        and approval['production_authority'] is False and all(
            approval[name+'_'+key] == pair[name][key]
            for name in ('application', 'tooling') for key in ('source', 'tree')), 'RAG_INDEPENDENT_APPROVAL')


def load_pair():
    require(os.environ.get('APP_ENV') == 'test' and sys.dont_write_bytecode
        and os.environ.get('PYTHONDONTWRITEBYTECODE') == '1', 'RAG_PAIR_TEST_ENVIRONMENT')
    root = authority_root()
    pair, pair_sha = native_json(root / 'runtime-pair.v1.json')
    approval, _ = native_json(root / 'runtime-pair.approval.v1.json')
    validate_pair(pair, approval, pair_sha)
    home = ISOLATED_ROOT if root == ISOLATED_AUTHORITY else TEST_ROOT
    for name, parent in (('application', home / 'releases'), ('tooling', home / 'shared/source-qualifications')):
        path = Path(pair[name]['path'])
        require(path.parent == parent, 'RAG_PAIR_FIXED_ROOT')
        files, file_sha = native_json(root / (name + '-files.v1.json'))
        require(file_sha == pair[name]['files_sha256'], 'RAG_FILE_MANIFEST_PIN')
        observed = verify_checkout(path, {k: pair[name][k] for k in ('path', 'source', 'tree')} | {'files': files})
        if name == 'application':
            require(observed['parents'] == [BASE_SOURCE], 'RAG_APPLICATION_EXACT_PARENT')
    require(Path(pair['application']['path']) / 'enterprise_agent_poc' == APPLICATION, 'RAG_LOADED_APPLICATION')
    return pair, pair_sha


def tooling_module(name):
    require(name == 'test_rag_native_successor', 'RAG_TOOLING_MODULE')
    pair, _ = load_pair()
    project = Path(pair['tooling']['path']) / 'enterprise_agent_poc'
    expected = project / 'scripts' / (name + '.py')
    cached = sys.modules.get('scripts.' + name)
    require(cached is None or Path(cached.__file__).resolve() == expected, 'RAG_FOREIGN_IMPORTED_TOOLING')
    import scripts
    require(all(Path(p).resolve() in (APPLICATION / 'scripts', project / 'scripts')
                for p in scripts.__path__), 'RAG_FOREIGN_SCRIPTS_NAMESPACE')
    scripts.__path__ = [str(project / 'scripts'), str(APPLICATION / 'scripts')]
    module = importlib.import_module('scripts.' + name)
    require(Path(module.__file__).resolve() == expected, 'RAG_TOOLING_IMPORT')
    return module


def startup(role):
    if not native_installation():
        return False
    tooling_module('test_rag_native_successor').guard(role)
    return True
