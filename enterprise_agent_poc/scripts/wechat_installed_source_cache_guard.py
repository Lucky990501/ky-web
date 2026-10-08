"""Read-only exact installed Source + narrowly verified bytecode cache checks.

Run under the pinned native Python 3.11, NOT the Windows test interpreter, for
real cache qualification. No cache file is imported, executed, deleted or
added to the old sealed manifest. Only the seven 06-observed cache paths are
eligible; any other extra file still rejects. Tests use synthetic compilation.
"""
import hashlib
import importlib.util
import marshal
from pathlib import Path
import struct
import sys
import types

from scripts.wechat_runtime_test_lifecycle_guard import Blocked, need

CACHE_MODULES = frozenset(('__init__', 'agent_productization', 'security', 'store',
    'tenant_secret_reference', 'wechat_action_contract', 'wechat_skill'))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def verified_cache(path, source):
    """Use this interpreter's own format/compiler; never execute marshal code."""
    raw, source_raw = path.read_bytes(), source.read_bytes()
    need(len(raw) >= 16 and raw[:4] == importlib.util.MAGIC_NUMBER, 'PYC_MAGIC_OR_HEADER_REJECTED')
    flags = struct.unpack('<I', raw[4:8])[0]
    need(flags in (0, 1, 3), 'PYC_FLAGS_REJECTED')
    if flags:
        need(raw[8:16] == importlib.util.source_hash(source_raw), 'PYC_SOURCE_HASH_REJECTED')
    else:
        mtime, size = struct.unpack('<II', raw[8:16])
        need(mtime == int(source.stat().st_mtime) & 0xffffffff
            and size == len(source_raw) & 0xffffffff, 'PYC_SOURCE_TIMESTAMP_REJECTED')
    try:
        code = marshal.loads(raw[16:])
        expected = compile(source_raw, str(source), 'exec', dont_inherit=True, optimize=0)
        need(type(code) is types.CodeType and code.co_filename == str(source)
            and marshal.dumps(code) == marshal.dumps(expected), 'PYC_COMPILED_CODE_REJECTED')
    except (EOFError, ValueError, TypeError, SyntaxError):
        raise Blocked('PYC_COMPILED_CODE_REJECTED') from None
    return {'cache_sha256': sha(raw), 'source_sha256': sha(source_raw), 'flags': flags}


def installed_source(release, sealed_files, *, required_python=(3, 11)):
    """Keep every old sealed file hash and reject unrelated generated extras."""
    need(sys.version_info[:2] == required_python and sys.flags.optimize == 0,
        'PINNED_PYTHON_REQUIRED_FOR_CACHE_VALIDATION')
    release = Path(release).absolute()
    need(release.is_dir() and not release.is_symlink(), 'INSTALLED_RELEASE_PATH_REJECTED')
    need(type(sealed_files) is dict and bool(sealed_files), 'SEALED_SOURCE_FILES_REQUIRED')
    actual = {}
    for path in release.rglob('*'):
        need(not path.is_symlink(), 'INSTALLED_SOURCE_SYMLINK_REJECTED')
        if path.is_file():
            actual[path.relative_to(release).as_posix()] = path
    for relative, expected in sealed_files.items():
        need(relative in actual and sha(actual[relative].read_bytes()) == expected,
            'PERSISTENT_INSTALLED_SOURCE_HASH')
    tag = sys.implementation.cache_tag
    allowed = {f'enterprise_agent_poc/app/__pycache__/{name}.{tag}.pyc': name
        for name in CACHE_MODULES}
    extras = set(actual) - set(sealed_files)
    need(extras <= set(allowed), 'UNAPPROVED_INSTALLED_FILE_ADDITION')
    validated = {}
    for relative in sorted(extras):
        source_name = 'enterprise_agent_poc/app/' + allowed[relative] + '.py'
        need(source_name in sealed_files, 'PYC_UNSEALED_SOURCE_REJECTED')
        validated[relative] = verified_cache(actual[relative], actual[source_name])
    return {'approved_source_files': len(sealed_files), 'verified_cache_files': len(validated),
        'sealed_manifest_rewritten': False, 'all_other_extras_rejected': True}
