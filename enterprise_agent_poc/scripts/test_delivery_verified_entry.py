"""Installed Test-only bootstrap. Verify trusted bytes before importing Tooling.

No installer, grant issuer, sudo invocation or default policy is provided.
The existing authorized Root adoption channel must install this bootstrap and
the exact policy; local copies have no protected execution authority.
"""
import hashlib
import json
import os
from pathlib import Path
import runpy
import stat
import sys


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def trust(path):
    p = Path(path)
    require(p.is_absolute() and p.resolve(strict=True) == p, 'PATH_IDENTITY')
    for part in (p, *p.parents):
        s = part.lstat()
        require(s.st_uid == 0 and not s.st_mode & 0o022 and not stat.S_ISLNK(s.st_mode), 'ROOT_OWNERSHIP')
        require(not any(x.startswith('system.posix_acl') for x in os.listxattr(part)), 'ACL_REVIEW')
    return p


def main():
    require(sys.platform == 'linux' and os.geteuid() == 0, 'LINUX_ROOT_REQUIRED')
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'ISOLATED_PYTHON_REQUIRED')
    require(sys.argv.count('--policy') == 1, 'EXACT_POLICY_REQUIRED')
    policy_path = trust(sys.argv[sys.argv.index('--policy') + 1])
    require(policy_path.stat().st_size <= 1024**2, 'POLICY_SIZE')
    def unique(pairs):
        obj = {}
        for k, v in pairs:
            require(k not in obj, 'DUPLICATE_POLICY_FIELD'); obj[k] = v
        return obj
    p = json.loads(policy_path.read_bytes(), object_pairs_hook=unique)
    require(p['environment'] == 'test', 'TEST_ONLY')
    root = trust(p['tooling_root'])
    require(root.is_dir(), 'TOOLING_ROOT')
    actual = {}
    for path in root.rglob('*'):
        require(not path.is_symlink(), 'TOOLING_LINK')
        require('__pycache__' not in path.parts and path.suffix != '.pyc', 'UNPINNED_BYTECODE')
        if path.is_file():
            trust(path)
            actual[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    require(actual == p['tooling_files'], 'COMPLETE_TOOLING_PIN_SET')
    require(str(Path(__file__).resolve()) in actual, 'BOOTSTRAP_NOT_ADOPTED')
    require(hashlib.sha256(trust(p['python']).read_bytes()).hexdigest() == p['python_sha256'], 'PYTHON_PIN')
    require(Path(sys.executable).resolve() == Path(p['python']).resolve(), 'PYTHON_IDENTITY')
    require(sys.argv[1] in ('execute', 'execute-isolated'), 'EXECUTE_ONLY')
    entry = root / ('scripts/test_delivery_context.py' if sys.argv[1] == 'execute-isolated'
                    else 'scripts/test_delivery_pipeline.py')
    require(str(entry) in actual, 'ENTRY_PIN')
    # Every local import is now within the complete, Root-owned pinned tree.
    runpy.run_path(str(entry), run_name='__main__', init_globals={'_VERIFIED_TEST_ENTRY': True})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'BLOCKED', 'error': type(exc).__name__}))
        raise SystemExit(2)
