"""Exact e70/562 TEST authority adapter. Stdlib only; no service/DB/network writer.

Only `issue` writes protected files, and only under its own append-only scope.
It must be invoked by the separately authorized Root administrator. This file
is not a Root approval and has no Production or Native activation authority.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import subprocess
import sys
import tarfile
import uuid

SOURCE = 'e70b61aa3228001d0fdf522f8673dfe1d1042e62'
TREE = '4cfa565813a96713402883540bfaf37c75e23c47'
PARENT = '562f201e362f0b10ae4fe3a8dae9a8c4d9ef91d3'
PREDECESSOR = {
    'release_id': '20261007-562f201-reference-dependency-retry-v1',
    'source_commit': PARENT, 'source_tree': 'e5205965e57fce6ce2b65dd747a110d00f7f1b1a',
    'archive_sha256': '17f31cb139ef9a619e455e5999b407fc7cfd98b45c3fd3757912b16a2da39941',
    'raw_manifest_sha256': 'eb6d382b3bc0b720d54349293d4f2afe73f372c8fd3838beb3eb86d557a9811c',
    'manifest_sha256': 'f99d02e36e33e2d0df20127019d019c52f93b5fdb26ebd0105c306c2ae93968a',
}
AUTHORITY_ID = 'WECHAT_E70_TEST_AUTHORITY_V1'
CONTRACT_ID = 'WECHAT_562_EXACT_UI_RUNTIME_ONLY_V1'
REQUEST_SHA = 'aa72f7fc6ad11b607d909e24508263131e1825a3b24afc4deaf05587310b3ade'
PINS_SHA = '53a4d4123bf602afe1620cd8001132637d10d68f1a1e8c01f3273dbe763691a7'
R = Path('/opt/enterprise-agent-workbench-test')
AUTHORITY = R / 'shared/test-release-authority-e70-v1'
VALIDATION = R / 'release-evidence/wechat-e70-validation-v1'
BASELINE = R / 'releases/20261008-d8a7814-wechat-personal-v1'
HOST = 'iv-yew0uhfksgbw80bm48mv'
PERMISSIONS = {
    'linux_candidate_build': True, 'isolated_package_installation_verification': True,
    'production_access': False, 'database_mutation': False, 'native_service_activation': False,
    'existing_service_restart': False, 'release_pointer_switch': False,
    'database_recovery_rehearsal': False, 'provider_calls': 0, 'wechat_calls': 0, 'image_calls': 0,
}
LIMITS = {'slice': 'enterprise-agent-test.slice', 'CPUQuota': '200%', 'MemoryHigh': '3G',
          'MemoryMax': '4G', 'MemorySwapMax': '1G', 'TasksMax': 512,
          'storage_limit_gib': 10, 'root_min_free_fraction': 0.2, 'runtime_max_seconds': 1800}
DEPENDENCIES = {
    'pyproject_sha256': '63b0fd2d61909df49abc23f49f6b3f3944f2c3d08e552ab7ba143a4bf33ee27d',
    'python': '3.11.16', 'mcp': '1.30.0', 'node': '24.21.0', 'python_package_count': 69,
    'snapshot_sha256': '0ee0a997e95f650a5be699075ec214dd7d9c2d94a94d8c7a7becfd0397fd1008',
    'status': 'REQUIRES_FRESH_INSTALLED_ATTESTATION_BEFORE_RUNTIME_QUALIFICATION',
    'automatic_dependency_mutation': False,
}
BASE_FIELDS = {'release_id', 'source_commit', 'archive_sha256', 'selected_files',
               'selected_file_count', 'build_platform'}


class Block(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise Block(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())


def unique(pairs):
    out = {}
    for key, value in pairs:
        need(key not in out, 'DUPLICATE_JSON_FIELD')
        out[key] = value
    return out


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(Block('NONFINITE_JSON')))


def now():
    return dt.datetime.now(dt.timezone.utc)


def timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        raise Block('INVALID_UTC_TIME')
    need(parsed.tzinfo is not None and parsed.utcoffset() == dt.timedelta(0), 'UTC_TIME_REQUIRED')
    return parsed


def canonical_path(path):
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=True) == path and '..' not in path.parts,
         'CANONICAL_NONSYMLINK_PATH_REQUIRED')
    return path


def read_regular(path, *, protected=False, max_bytes=16 * 1024 * 1024):
    path = canonical_path(path)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        st = os.fstat(stream.fileno())
        need(stat.S_ISREG(st.st_mode) and st.st_size <= max_bytes, 'REGULAR_BOUNDED_FILE_REQUIRED')
        if protected:
            need(st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o640, 'ROOT_0640_FILE_REQUIRED')
        data = stream.read(max_bytes + 1)
        need(len(data) <= max_bytes, 'FILE_TOO_LARGE')
        return data


def trusted_directory(path):
    path = canonical_path(path)
    for parent in (path, *path.parents):
        st = parent.lstat()
        need(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022,
             'ROOT_NONWRITABLE_PARENT_REQUIRED')
    return path


def execution_guard(*, signing=False):
    need(sys.platform == 'linux' and platform.machine() == 'x86_64', 'LINUX_X64_REQUIRED')
    need(sys.flags.isolated == 1 and sys.dont_write_bytecode, 'PYTHON_ISOLATED_NO_PYC_REQUIRED')
    need(platform.node() == HOST, 'EXACT_TEST_HOST_REQUIRED')
    if signing:
        need(os.getuid() == 0, 'ROOT_SIGNER_REQUIRED')
    executable = Path(sys.executable).resolve(strict=True)
    need(executable.parent == Path('/usr/bin') and executable.name.startswith('python3'),
         'SYSTEM_PYTHON_REQUIRED_FOR_AUTHORITY')
    trusted_directory(executable.parent)
    st = executable.stat()
    need(st.st_uid == 0 and not st.st_mode & 0o022, 'ROOT_SYSTEM_PYTHON_REQUIRED')
    need(Path(__file__).resolve() == AUTHORITY / 'authority.py', 'FIXED_TRUSTED_ENTRY_REQUIRED')
    trusted_directory(AUTHORITY)
    read_regular(AUTHORITY / 'authority.py', protected=True)


def load_pins():
    raw = read_regular(Path(__file__).resolve().with_name('e70-package-pins.v1.json'))
    need(sha(raw) == PINS_SHA, 'E70_FILE_PIN_IDENTITY')
    pins = decode(raw)
    need((pins['source'], pins['tree'], pins['parent']) == (SOURCE, TREE, PARENT)
         and pins['pin_version'] == 1 and len(pins['candidate_files']) == 144
         and len(pins['predecessor_files']) == 141, 'EXACT_PACKAGE_PINS_REQUIRED')
    return pins


def validate_request(raw):
    need(sha(raw) == REQUEST_SHA, 'EXACT_UPLOADED_REQUEST_HASH_REQUIRED')
    value = decode(raw)
    need(value['document_type'] == 'AUTHORITY_REQUEST_NOT_AN_APPROVAL'
         and value['protected_approval'] is False and value['production_deploy_authority'] is False,
         'REQUEST_IS_NOT_APPROVAL')
    binding = value['source_binding']
    need((binding['application_source'], binding['application_tree'], binding['exact_parent'],
          binding['contract']) == (SOURCE, TREE, PARENT, CONTRACT_ID), 'EXACT_E70_REQUEST_BINDING')
    need(value['external_call_budget'] == {'provider': 0, 'wechat': 0, 'image': 0}, 'ZERO_CALLS_REQUIRED')
    need(value['schema_contract']['migration_action'] == 'NONE'
         and value['schema_contract']['current'] == value['schema_contract']['target'] == '001-015',
         'SCHEMA015_NO_MIGRATION_REQUIRED')
    return value


def history_snapshot():
    result = {}
    for name in ('test-release-authority', 'test-release-authority-v2',
                 'test-release-authority-v3', 'test-release-authority-v4'):
        root = R / 'shared' / name
        if not root.exists():
            result[name] = None
            continue
        trusted_directory(root)
        result[name] = {p.name: sha(read_regular(p)) for p in sorted(root.iterdir())
                        if p.is_file() and p.suffix in ('.py', '.json')}
    for name in ('test-release-contract.v1.json', 'test-environment-reference.v1.json'):
        result['config/' + name] = sha(read_regular(R / 'shared/config' / name, protected=True))
    return result


def baseline_snapshot():
    need((R / 'current').resolve(strict=True) == BASELINE, 'D8A_BASELINE_POINTER_CHANGED')
    result = {'current': str(BASELINE), 'services': {}}
    for role in ('api', 'mcp', 'worker'):
        unit = 'enterprise-agent-test-' + role + '.service'
        output = subprocess.check_output(['/usr/bin/systemctl', 'show', unit, '-p', 'ActiveState',
                                          '-p', 'MainPID'], text=True, timeout=10,
                                         env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        props = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        pid = int(props.get('MainPID', '0'))
        need(props.get('ActiveState') == 'active' and pid > 0, 'EXISTING_SERVICE_STATE_CHANGED')
        cwd = os.readlink('/proc/' + str(pid) + '/cwd')
        need(cwd == str(BASELINE / 'enterprise_agent_poc'), 'EXISTING_SERVICE_CWD_CHANGED')
        result['services'][role] = {'pid': pid, 'cwd': cwd}
    return result


def package(archive, manifest_path, pins, *, role='candidate', linux=True):
    archive = canonical_path(archive)
    raw = read_regular(manifest_path)
    manifest = decode(raw)
    need(isinstance(manifest, dict) and set(manifest) == BASE_FIELDS | {'runtime_only_release'},
         'STRICT_MANIFEST_FIELDS')
    expected_files = pins[role + '_files']
    files = manifest['selected_files']
    need(isinstance(files, list) and files == sorted(expected_files)
         and type(manifest['selected_file_count']) is int
         and manifest['selected_file_count'] == len(files), 'EXACT_MANIFEST_FILESET')
    archive_bytes = read_regular(archive, max_bytes=64 * 1024 * 1024)
    archive_sha = sha(archive_bytes)
    need(manifest['archive_sha256'] == archive_sha, 'ARCHIVE_SHA_MISMATCH')
    expected_source = SOURCE if role == 'candidate' else PARENT
    need(manifest['source_commit'] == expected_source, 'PACKAGE_SOURCE_MISMATCH')
    if role == 'candidate':
        need(digest(manifest['runtime_only_release']) == digest(pins['manifest_runtime_only_release']), 'EXACT_E70_MANIFEST_V2_REQUIRED')
        need(bool(re.fullmatch(r'2026[0-9]{4}-e70b61a-[a-z0-9-]+', manifest['release_id'])), 'E70_RELEASE_ID_REQUIRED')
        if linux:
            need(manifest['build_platform'] == 'Linux-x86_64', 'LINUX_BUILD_RECEIPT_REQUIRED')
    else:
        need(manifest['release_id'] == PREDECESSOR['release_id']
             and archive_sha == PREDECESSOR['archive_sha256']
             and sha(raw) == PREDECESSOR['raw_manifest_sha256']
             and digest(manifest) == PREDECESSOR['manifest_sha256'], 'ORIGINAL_562_ARTIFACT_IDENTITY')
    # Bound expansion before tarfile parses any caller-controlled PAX header.
    with gzip.GzipFile(fileobj=io.BytesIO(archive_bytes), mode='rb') as compressed:
        tar_bytes = compressed.read(16 * 1024 * 1024 + 1)
    need(len(tar_bytes) <= 16 * 1024 * 1024, 'TAR_EXPANSION_LIMIT')
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode='r:') as tar:
        need(tar.pax_headers.get('comment') == expected_source, 'TAR_SOURCE_MISMATCH')
        names = set()
        observed = {}
        for index, member in enumerate(tar):
            need(index < 512 and member.name not in names, 'DUPLICATE_OR_EXCESS_TAR_MEMBER')
            names.add(member.name)
            p = PurePosixPath(member.name)
            need(p.parts and p.parts[0] == 'enterprise_agent_poc' and not p.is_absolute()
                 and '..' not in p.parts and '\\' not in member.name
                 and (member.isfile() or member.isdir()) and not member.mode & ~0o777,
                 'UNSAFE_TAR_MEMBER')
            if member.isdir():
                need(member.size == 0, 'DIRECTORY_PAYLOAD_FORBIDDEN')
            else:
                need(member.size <= 32 * 1024 * 1024 and member.name in expected_files, 'UNLISTED_OR_OVERSIZED_MEMBER')
                data = tar.extractfile(member).read()
                observed[member.name] = {'sha256': sha(data), 'execute_bits': member.mode & 0o111}
        need(observed == expected_files, 'ALL_PACKAGE_BYTE_MODE_PINS_REQUIRED')
    return {'archive_sha256': archive_sha, 'raw_manifest_sha256': sha(raw),
            'canonical_manifest_identity': digest(manifest), 'selected_files': len(files),
            'release_id': manifest['release_id'], 'source': expected_source}


def predecessor_package(pins):
    root = R / 'candidates' / PREDECESSOR['release_id']
    return package(root / (PREDECESSOR['release_id'] + '.tar.gz'),
                   root / (PREDECESSOR['release_id'] + '.manifest.json'), pins,
                   role='predecessor', linux=False)


def scope_id(value):
    try:
        need(str(uuid.UUID(value)) == value, 'CANONICAL_OPERATION_UUID_REQUIRED')
    except (ValueError, TypeError):
        raise Block('CANONICAL_OPERATION_UUID_REQUIRED')
    return value


def contract_value(audit_id, operation_id, expiry, tooling_source, tooling_tree, code_sha,
                   history, baseline):
    need(bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}', audit_id)), 'REAL_AUDIT_REFERENCE_REQUIRED')
    scope_id(operation_id)
    need(now() < timestamp(expiry) <= now() + dt.timedelta(hours=24), 'EXPIRY_WITHIN_24H_REQUIRED')
    need(all(re.fullmatch('[0-9a-f]{40}', v) for v in (tooling_source, tooling_tree))
         and tooling_source not in (SOURCE, PARENT), 'INDEPENDENT_TOOLING_SOURCE_TREE_REQUIRED')
    need(bool(re.fullmatch('[0-9a-f]{64}', code_sha)), 'APPROVED_TOOLING_CODE_HASH_REQUIRED')
    return {
        'contract_version': 1, 'authority_id': AUTHORITY_ID, 'scope': 'TEST_ONLY_RELEASE_AUTHORITY',
        'production_deploy_authority': False, 'request_sha256': REQUEST_SHA,
        'application': {'source': SOURCE, 'tree': TREE, 'parent': PARENT},
        'tooling': {'source': tooling_source, 'tree': tooling_tree, 'code_sha256': code_sha,
                    'pins_sha256': PINS_SHA, 'entry': str(AUTHORITY / 'authority.py')},
        'exact_predecessor': PREDECESSOR, 'schema': '001-015', 'migration': 'NONE',
        'data_contract': 'member_account_status_v1', 'permissions': PERMISSIONS,
        'limits': LIMITS, 'dependencies': DEPENDENCIES, 'validation_root': str(VALIDATION),
        'installed_candidate': str(VALIDATION / 'installed/candidate'),
        'installed_predecessor': str(VALIDATION / 'installed/predecessor'),
        'historical_authority': history, 'test_baseline': baseline,
        'audit': {'id': audit_id, 'operation_id': operation_id, 'issued_at': now().isoformat(),
                  'expires_at': expiry, 'issuer_uid': 0},
        'native_recovery_authority': False,
    }


def validate_contract(c):
    need(isinstance(c, dict) and c.get('authority_id') == AUTHORITY_ID, 'E70_AUTHORITY_REQUIRED')
    expected = contract_value(c['audit']['id'], c['audit']['operation_id'], c['audit']['expires_at'],
                              c['tooling']['source'], c['tooling']['tree'], c['tooling']['code_sha256'],
                              c['historical_authority'], c['test_baseline'])
    expected['audit']['issued_at'] = c['audit']['issued_at']
    need(digest(c) == digest(expected) and timestamp(c['audit']['issued_at']) <= now(), 'EXACT_TEST_SCOPE_REQUIRED')


def write_exclusive(path, value):
    data = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o640)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o640)
        os.fchown(stream.fileno(), 0, 1000)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return sha(data)


def seal_scope(root, c):
    """Approval is published last. Partial failures are retained and fail closed."""
    need(not root.exists(), 'SCOPE_ALREADY_EXISTS_NO_OVERWRITE')
    root.mkdir(mode=0o750)
    os.chown(root, 0, 1000)
    os.chmod(root, 0o750)
    contract_sha = write_exclusive(root / 'contract.e70.v1.json', c)
    receipt = {'authority_id': AUTHORITY_ID, 'audit': c['audit'], 'contract_sha256': contract_sha,
               'tooling': c['tooling'], 'state': 'ROOT_SEALED_TEST_SCOPE_NOT_NATIVE_PASS'}
    receipt_sha = write_exclusive(root / 'issuance-evidence.e70.v1.json', receipt)
    anchor = {'approval_version': 1, 'authority_id': AUTHORITY_ID, 'contract_sha256': contract_sha,
              'evidence_sha256': receipt_sha, 'tooling': c['tooling'], 'audit': c['audit']}
    # This must be the last newly published scope file, not an early commit.
    approval_sha = write_exclusive(root / 'approval.e70.v1.json', anchor)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return {'scope_root': str(root), 'contract_sha256': contract_sha,
            'approval_sha256': approval_sha, 'evidence_sha256': receipt_sha}


def issue(args):
    execution_guard(signing=True)
    need(sha(read_regular(AUTHORITY / 'authority.py', protected=True)) == args.expected_code_sha256,
         'ADMIN_APPROVED_CODE_HASH_MISMATCH')
    need(sha(read_regular(AUTHORITY / 'e70-package-pins.v1.json', protected=True)) == PINS_SHA,
         'E70_FILE_PIN_IDENTITY')
    pins = load_pins()
    validate_request(read_regular(args.request, max_bytes=65536))
    prior = predecessor_package(pins)
    before = baseline_snapshot()
    history = history_snapshot()
    c = contract_value(args.audit_id, args.operation_id, args.expires_at,
                       args.tooling_source, args.tooling_tree, args.expected_code_sha256,
                       history, before)
    need(baseline_snapshot() == before and history_snapshot() == history, 'PRE_ISSUE_STATE_CHANGED')
    import fcntl
    lock_path = AUTHORITY / '.issue.lock'
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        need(os.fstat(fd).st_uid == 0 and stat.S_IMODE(os.fstat(fd).st_mode) == 0o600, 'ROOT_SIGNING_LOCK_REQUIRED')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Block('AUTHORITY_SIGNING_LOCKED')
        scopes = AUTHORITY / 'scopes'
        if not scopes.exists():
            scopes.mkdir(mode=0o750)
            os.chown(scopes, 0, 1000)
            os.chmod(scopes, 0o750)
        trusted_directory(scopes)
        sealed = seal_scope(scopes / scope_id(args.operation_id), c)
        need(baseline_snapshot() == before and history_snapshot() == history, 'POST_ISSUE_STATE_CHANGED')
    finally:
        os.close(fd)
    return {'result': 'E70_TEST_AUTHORITY_ISSUED_NOT_NATIVE_PASS', **sealed,
            'audit_id': args.audit_id, 'predecessor_package': prior, 'native_authority': False,
            'production_authority': False, 'provider_calls': 0, 'wechat_calls': 0, 'image_calls': 0}


def load_scope(operation_id):
    root = trusted_directory(AUTHORITY / 'scopes' / scope_id(operation_id))
    raw = read_regular(root / 'contract.e70.v1.json', protected=True)
    c = decode(raw)
    validate_contract(c)
    anchor = decode(read_regular(root / 'approval.e70.v1.json', protected=True))
    evidence_raw = read_regular(root / 'issuance-evidence.e70.v1.json', protected=True)
    expected = {'approval_version': 1, 'authority_id': AUTHORITY_ID, 'contract_sha256': sha(raw),
                'evidence_sha256': sha(evidence_raw), 'tooling': c['tooling'], 'audit': c['audit']}
    need(anchor == expected and c['audit']['operation_id'] == operation_id, 'ROOT_APPROVAL_BINDING_MISMATCH')
    evidence = decode(evidence_raw)
    need(evidence == {'authority_id': AUTHORITY_ID, 'audit': c['audit'], 'contract_sha256': sha(raw),
                      'tooling': c['tooling'], 'state': 'ROOT_SEALED_TEST_SCOPE_NOT_NATIVE_PASS'},
         'ISSUANCE_EVIDENCE_MISMATCH')
    need(sha(read_regular(AUTHORITY / 'authority.py', protected=True)) == c['tooling']['code_sha256'],
         'TRUSTED_CONSUMER_CODE_MISMATCH')
    need(sha(read_regular(AUTHORITY / 'e70-package-pins.v1.json', protected=True)) == PINS_SHA,
         'E70_FILE_PIN_IDENTITY')
    need(history_snapshot() == c['historical_authority'], 'HISTORICAL_AUTHORITY_CHANGED')
    need(baseline_snapshot() == c['test_baseline'], 'D8A_BASELINE_CHANGED')
    return c


def isolated_input_path(value):
    path = canonical_path(value)
    need(path.is_relative_to(VALIDATION / 'artifacts'), 'ONLY_ISOLATED_ARTIFACT_PATH_ALLOWED')
    return path


def installed_files(root, expected, required_root):
    root = canonical_path(root)
    need(root == required_root and root.is_dir(), 'EXACT_ISOLATED_INSTALL_PATH_REQUIRED')
    trusted_directory(root)
    actual = {}
    for p in root.rglob('*'):
        need(not p.is_symlink(), 'INSTALLED_SYMLINK_FORBIDDEN')
        if p.is_file():
            st = p.stat()
            need(st.st_uid == 0 and not st.st_mode & 0o022, 'INSTALLED_ROOT_NONWRITABLE_FILE_REQUIRED')
            actual[p.relative_to(root).as_posix()] = {
                'sha256': sha(read_regular(p)), 'execute_bits': st.st_mode & 0o111}
        else:
            need(p.is_dir(), 'SPECIAL_INSTALLED_FILE_FORBIDDEN')
            trusted_directory(p)
    need(actual == expected, 'ALL_INSTALLED_FILE_PINS_REQUIRED')


def seal_package(args):
    """After authorized Linux build, Root seals exact output without editing scope."""
    execution_guard(signing=True)
    c = load_scope(args.operation_id)
    pins = load_pins()
    result = package(isolated_input_path(args.archive), isolated_input_path(args.manifest), pins)
    need(result['archive_sha256'] == args.expected_archive_sha256
         and result['raw_manifest_sha256'] == args.expected_raw_manifest_sha256
         and result['canonical_manifest_identity'] == args.expected_canonical_manifest_identity,
         'ADMIN_APPROVED_LINUX_RECEIPT_MISMATCH')
    receipt = {'receipt_version': 1, 'authority_id': AUTHORITY_ID, 'operation_id': args.operation_id,
               'application_source': SOURCE, 'application_tree': TREE,
               'archive_path': str(canonical_path(args.archive)),
               'manifest_path': str(canonical_path(args.manifest)),
               'package': result, 'audit_id': c['audit']['id']}
    root = AUTHORITY / 'scopes' / scope_id(args.operation_id)
    receipt_sha = write_exclusive(root / 'candidate-receipt.e70.v1.json', receipt)
    return {'result': 'E70_LINUX_PACKAGE_RECEIPT_SEALED_NOT_NATIVE_PASS',
            'receipt_sha256': receipt_sha, 'package': result}


def resource_guard():
    cgroup = Path('/proc/self/cgroup').read_text()
    matches = re.findall(r'/enterprise-agent-test\.slice/(ea-test-e70-[A-Za-z0-9._-]+\.service)', cgroup)
    need(len(matches) == 1, 'E70_BOUNDED_TEST_SLICE_REQUIRED')
    properties = ('User', 'MemoryMax', 'MemoryHigh', 'MemorySwapMax', 'TasksMax',
                  'RuntimeMaxUSec', 'CPUQuotaPerSecUSec')
    out = subprocess.check_output(['/usr/bin/systemctl', 'show', matches[0],
                                   *['--property=' + p for p in properties]], text=True, timeout=10,
                                  env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
    values = dict(line.split('=', 1) for line in out.splitlines() if '=' in line)
    need(values.get('User') == 'lucky', 'UNPRIVILEGED_VALIDATION_USER_REQUIRED')
    for key, cap in (('MemoryMax', 4 * 1024**3), ('MemoryHigh', 3 * 1024**3),
                     ('MemorySwapMax', 1024**3), ('TasksMax', 512)):
        need(values.get(key, '').isdigit() and 0 < int(values[key]) <= cap, 'BOUNDED_' + key)
    need(0 < duration_us(values.get('RuntimeMaxUSec', '')) <= 1800 * 1000000,
         'BOUNDED_EXECUTION_TIME_REQUIRED')
    need(0 < duration_us(values.get('CPUQuotaPerSecUSec', '')) <= 2 * 1000000,
         'BOUNDED_CPU_QUOTA_REQUIRED')
    st = os.statvfs('/')
    need(st.f_bavail / st.f_blocks >= 0.2, 'ROOT_FREE_SPACE_FLOOR')


def duration_us(value):
    units = {'us': 1, 'ms': 1000, 's': 1000000, 'min': 60000000, 'h': 3600000000}
    tokens = re.findall(r'([0-9]+(?:\.[0-9]+)?)(us|ms|min|s|h)', value)
    need(tokens and ''.join(a + b for a, b in tokens) == value.replace(' ', ''),
         'BOUNDED_SYSTEMD_DURATION_REQUIRED')
    return sum(float(amount) * units[unit] for amount, unit in tokens)


def verify(args):
    execution_guard()
    resource_guard()
    c = load_scope(args.operation_id)
    pins = load_pins()
    prior = predecessor_package(pins)
    result = package(isolated_input_path(args.archive), isolated_input_path(args.manifest), pins)
    receipt = decode(read_regular(AUTHORITY / 'scopes' / args.operation_id /
                                  'candidate-receipt.e70.v1.json', protected=True))
    need(receipt == {'receipt_version': 1, 'authority_id': AUTHORITY_ID,
                    'operation_id': args.operation_id, 'application_source': SOURCE,
                    'application_tree': TREE, 'archive_path': str(canonical_path(args.archive)),
                    'manifest_path': str(canonical_path(args.manifest)), 'package': result,
                    'audit_id': c['audit']['id']}, 'ROOT_LINUX_PACKAGE_RECEIPT_REQUIRED')
    if args.installed:
        installed_files(args.installed, pins['candidate_files'], Path(c['installed_candidate']))
    if args.predecessor_installed:
        installed_files(args.predecessor_installed, pins['predecessor_files'], Path(c['installed_predecessor']))
    need(history_snapshot() == c['historical_authority']
         and baseline_snapshot() == c['test_baseline'], 'POST_VERIFY_STATE_CHANGED')
    return {'result': 'E70_SOURCE_BOUND_PACKAGE_VERIFIED_NOT_NATIVE', 'candidate': result,
            'exact_predecessor': prior, 'installed_candidate': 'PASS' if args.installed else 'NOT_VERIFIED',
            'installed_predecessor': 'PASS' if args.predecessor_installed else 'NOT_VERIFIED',
            'schema_db_compatibility': 'NOT_VERIFIED', 'systemd_startup': 'NOT_VERIFIED',
            'installed_dependencies': 'NOT_VERIFIED',
            'cross_version_recovery': 'NOT_VERIFIED', 'production_authority': False,
            'provider_calls': 0, 'wechat_calls': 0, 'image_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    sub.add_parser('plan', help='Print frozen scope; read-only and not an approval.')
    p = sub.add_parser('issue', help='Separately authorized Root only; seals NEW e70 scope, no install/start.')
    for option in ('request', 'audit-id', 'operation-id', 'expires-at', 'tooling-source',
                   'tooling-tree', 'expected-code-sha256'):
        p.add_argument('--' + option, required=True)
    p = sub.add_parser('verify-package', help='Consume Root approval and verify exact Linux package; no writers.')
    for option in ('operation-id', 'archive', 'manifest'):
        p.add_argument('--' + option, required=True)
    p.add_argument('--installed')
    p.add_argument('--predecessor-installed')
    p = sub.add_parser('seal-package', help='Root seals approved exact Linux build output, no installation.')
    for option in ('operation-id', 'archive', 'manifest', 'expected-archive-sha256',
                   'expected-raw-manifest-sha256', 'expected-canonical-manifest-identity'):
        p.add_argument('--' + option, required=True)
    args = parser.parse_args()
    try:
        if args.mode == 'plan':
            result = {'authority_id': AUTHORITY_ID, 'source': SOURCE, 'tree': TREE,
                      'predecessor': PREDECESSOR, 'permissions': PERMISSIONS,
                      'authority_issued': False, 'scope': 'TEST_ONLY_RELEASE_AUTHORITY'}
        elif args.mode == 'issue':
            result = issue(args)
        elif args.mode == 'seal-package':
            result = seal_package(args)
        else:
            result = verify(args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Block, OSError, ValueError, KeyError, TypeError, tarfile.TarError,
            subprocess.SubprocessError) as exc:
        # Do not echo caller-controlled paths, request contents, or subprocess output.
        gate = str(exc) if isinstance(exc, Block) else type(exc).__name__
        print(json.dumps({'result': 'E70_TEST_AUTHORITY_BLOCKED', 'gate': gate,
                          'provider_calls': 0, 'wechat_calls': 0, 'image_calls': 0}))
        return 75 if gate == 'AUTHORITY_SIGNING_LOCKED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
