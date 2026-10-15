"""Root-owned stdlib-only first byte verifier. No installer or authority issuer.
Operator must verify THIS entry's Git bytes/SHA with existing system tools
before first invocation. It never imports the candidate before pin closure.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time

CONTROL = Path('/etc/enterprise-agent-test-business-894-successor-v2')
CODE_FILES = ('entry.py', 'existing_recovery_bound.py', 'runtime_probe.py',
              'registry_extension.py', 'verified_entry.py')


def protected(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022:
            raise RuntimeError('FIRST_ENTRY_ROOT_PATH_TRUST')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o222:
        raise RuntimeError('FIRST_ENTRY_IMMUTABLE_SINGLE_FILE')


def verify(command):
    if not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
        raise RuntimeError('SYSTEM_PYTHON_ISOLATED_STDLIB_ONLY_REQUIRED')
    name = ('partial-prepare-continuation.approved.v1.json' if command == 'continue-prepare' or
            (command == 'query' and (CONTROL/'partial-prepare-continuation.approved.v1.json').exists()) else
            'runtime-binding.v1.json' if command == 'runtime-check' else 'scope.approved.v1.json')
    protected(CONTROL / name)
    value = json.loads((CONTROL / name).read_bytes())
    if command not in ('runtime-check', 'query'):
        if not (value['issued_at'] <= time.time() < value['expires_at']) or value['revoked'] or (CONTROL/'REVOKED').exists():
            raise RuntimeError('FIRST_ENTRY_ACTUAL_AUTHORITY_REQUIRED')
    binary = Path(sys.executable).resolve()
    if str(binary) != value['python_real_path'] or hashlib.sha256(binary.read_bytes()).hexdigest() != value['python_sha256']:
        raise RuntimeError('FIRST_ENTRY_SYSTEM_INTERPRETER_PIN')
    for path in (binary, *binary.parents):
        s = path.lstat()
        if s.st_uid != 0 or s.st_gid != 0 or s.st_mode & 0o022 or stat.S_ISLNK(s.st_mode):
            raise RuntimeError('FIRST_ENTRY_SYSTEM_INTERPRETER_ROOT_TRUST')
    manifest = CONTROL / 'system-python-trust.v2.json'
    protected(manifest)
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != value['system_python_manifest_sha256']:
        raise RuntimeError('SYSTEM_STDLIB_MANIFEST_PIN')
    facts = json.loads(manifest.read_bytes())
    for name, pin in facts['stdlib_source_and_extension_pins'].items():
        path = Path(name).resolve(strict=True)
        for ancestor in (path, *path.parents):
            s = ancestor.lstat()
            if s.st_uid != 0 or s.st_gid != 0 or s.st_mode & 0o022 or stat.S_ISLNK(s.st_mode):
                raise RuntimeError('SYSTEM_STDLIB_ANCESTOR_TRUST')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != pin:
            raise RuntimeError('SYSTEM_STDLIB_BYTES_CHANGED')
    if set(value['code_sha256']) != set(CODE_FILES):
        raise RuntimeError('FIRST_ENTRY_COMPLETE_CODE_PIN_SET')
    for name in CODE_FILES:
        protected(CONTROL/name)
        if hashlib.sha256((CONTROL/name).read_bytes()).hexdigest() != value['code_sha256'][name]:
            raise RuntimeError('FIRST_ENTRY_CODE_PIN_BEFORE_COMPILE')
    if any(CONTROL.rglob('*.pyc')):
        raise RuntimeError('UNLISTED_CANDIDATE_CACHED_BYTES')
    return value


def main():
    verify(sys.argv[1])
    namespace = {'__name__': 'verified_candidate_not_imported', '__file__': str(CONTROL/'entry.py')}
    exec(compile((CONTROL/'entry.py').read_bytes(), str(CONTROL/'entry.py'), 'exec'), namespace)
    return namespace['main']()


if __name__ == '__main__':
    try:
        print(json.dumps(main(), sort_keys=True, default=str))
    except Exception as error:
        print(json.dumps({'status': 'VERIFIED_ENTRY_BLOCKED', 'category': type(error).__name__}))
        raise SystemExit(2)
