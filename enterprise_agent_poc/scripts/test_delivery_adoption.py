"""Versioned finalization hook for the existing external Root material installer.

No issuer, sudo, service command, copy operation or executable CLI. The installer
must pin this module BEFORE loading it, stage immutable materials using its
existing Git/EXCL/readback mechanism, and provide a real new Root decision.
Historical install_exact_materials.py / v6_exact_handoff_and_scope.py remain
unchanged. A successful receipt is installation acceptance, NEVER Native PASS.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import socket
import stat
import subprocess
import sys
import time
import uuid

ACTION = 'adopt_reusable_test_driver_v1'
PROFILE = 'PRIVATE_STAGE2_TMPFS_V2'


def need(ok, code):
    if not ok:
        raise ValueError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode()).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024**2), b''):
            h.update(part)
    return h.hexdigest()


def trust(path):
    p = Path(path)
    need(p.is_absolute() and p.resolve(strict=True) == p, 'CANONICAL_ROOT_PATH')
    for node in (p, *p.parents):
        s = node.lstat()
        need(s.st_uid == s.st_gid == 0 and not s.st_mode & 0o022
             and not stat.S_ISLNK(s.st_mode), 'ROOT_PATH_TRUST')
        need(not any(x.startswith('system.posix_acl') for x in os.listxattr(node)), 'ACL_TRUST')
    if p.is_file():
        need(p.stat().st_nlink == 1, 'SINGLE_LINK_REQUIRED')
    return p


def read(path):
    p = trust(path)
    need(p.is_file() and p.stat().st_size <= 1024**2, 'RECORD_SIZE')
    return decode_record(p.read_bytes())


def decode_record(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            need(key not in out, 'DUPLICATE_FIELD')
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=unique)


def validate_authority(request, decision, now):
    need(request['action'] == ACTION and request['environment'] == 'test', 'TEST_ONLY_ACTION')
    need(request['permissions'] == {'isolated_qualify': True, 'primary_switch': False,
         'production': False, 'preview': False, 'paid_calls': 0}, 'EXACT_SCOPE')
    need(request['profile'] == PROFILE and request['limits'] == {
        'scratch_bytes': 704*1024**2, 'aggregate_bytes': 768*1024**2,
        'host_reserve_bytes': 256*1024**2, 'min_free_fraction': .2,
        'memory_high_bytes': 1280*1024**2, 'memory_max_bytes': 1536*1024**2,
        'memory_swap_bytes': 0}, 'EXACT_PROFILE')
    need(decision['kind'] == 'ACTUAL_OWNER_AUTHORIZED_06_ROOT_DECISION_RECORD'
         and decision['technical_action'] == ACTION, 'EXISTING_ROOT_DECISION_REQUIRED')
    need(decision['request_sha256'] == digest(request), 'DECISION_REQUEST_PIN')
    need(decision['actual_root_uid'] == 0 and decision['owner_references']
         and decision['operator'] and decision['revoked'] is False
         and decision['uses'] == 1 and decision['production_authority'] is False
         and decision['provider_budget'] == 0, 'DECISION_SCOPE')
    need(type(decision['issued_at']) is int and type(decision['expires_at']) is int
         and decision['issued_at'] <= now < decision['expires_at']
         and 0 < decision['expires_at'] - decision['issued_at'] <= 14400, 'DECISION_EXPIRED')
    need(str(uuid.UUID(request['operation_id'])) == request['operation_id'], 'OPERATION_ID')
    need(request['host'] == decision['host'], 'HOST_MISMATCH')


def validate(request, decision, observed, now):
    """Pure contract check. observed must be collected by finalize, not a CLI input."""
    validate_authority(request, decision, now)
    need(observed['host'] == request['host'] == decision['host'], 'HOST_MISMATCH')
    need(observed['primary'] == request['primary'], 'PRIMARY_MISMATCH')
    need(observed['tooling'] == request['tooling'], 'TOOLING_IDENTITY')
    need(observed['files'] == request['files'] and observed['files'], 'TOOLING_FILE_PINS')
    need(observed['runtime'] == request['runtime'], 'RUNTIME_IDENTITY')
    need(observed['artifacts'] == request['artifacts'], 'ARTIFACT_IDENTITY')
    need(observed['operator'] == request['operator'] and observed['operator']['uid'] > 0
         and observed['operator']['gid'] > 0, 'OPERATOR_IDENTITY')
    need(observed['free'] - 1024**3 > observed['total']*.2, 'DISK_RESERVE')
    return {'contract': 'TEST_DRIVER_MATERIAL_ADOPTION_V1', 'status': 'MATERIALS_VERIFIED',
            'request_sha256': digest(request), 'operation_id': request['operation_id'],
            'tooling': observed['tooling'], 'profile': PROFILE,
            'native_pass': False, 'execution_authorized': False,
            'primary_changes': 0, 'production_changes': 0, 'preview_changes': 0}


def git(repo, *args):
    trust(Path(repo)/'.git')
    return subprocess.check_output(['/usr/bin/git', '--no-optional-locks', '-c',
        'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-C', str(repo), *args],
        env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_NOSYSTEM': '1',
             'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0'}, timeout=60)


def identity(repo, source):
    need(re.fullmatch('[a-f0-9]{40}', source) is not None, 'SOURCE_FORMAT')
    rows = git(repo, 'show', '-s', '--format=%H%n%T%n%P', source).decode().splitlines()
    return {'source': rows[0], 'tree': rows[1], 'parent': rows[2]}


def tree_pins(root):
    out = {}
    for path in trust(root).rglob('*'):
        trust(path)
        need(path.is_dir() or path.is_file(), 'SPECIAL_FILE')
        need('__pycache__' not in path.parts and path.suffix != '.pyc', 'UNLISTED_CACHE')
        if path.is_file():
            out[path.relative_to(root).as_posix()] = sha(path)
    return out


def readonly_node(path, owner, mode):
    """Historical data only: never import it or run Git/config/hooks from it."""
    p = Path(path)
    need(p.is_absolute() and p.resolve(strict=True) == p, 'PRIMARY_CANONICAL_PATH')
    s = p.lstat()
    need((s.st_uid, s.st_gid) == tuple(owner) and stat.S_IMODE(s.st_mode) == mode
         and not s.st_mode & 0o022 and not stat.S_ISLNK(s.st_mode), 'PRIMARY_READONLY_METADATA')
    need(p.is_dir() or (stat.S_ISREG(s.st_mode) and s.st_nlink == 1), 'PRIMARY_NODE_TYPE')
    return p


def running_cwd(unit):
    need(re.fullmatch(r'enterprise-agent-test-(api|mcp|worker)\.service', unit), 'PRIMARY_UNIT_SCOPE')
    def pid():
        value = subprocess.check_output(['/usr/bin/systemctl', 'show', unit,
            '--property=MainPID', '--value'], timeout=10).decode().strip()
        need(value.isdigit() and int(value) > 0, 'PRIMARY_NOT_RUNNING')
        return value
    before = pid()
    cwd = os.readlink('/proc/'+before+'/cwd')
    need(pid() == before, 'PRIMARY_PROCESS_CHANGED')
    return cwd


def historical_record(path):
    """Root-authored evidence may be group-readable, never group-writable."""
    p = Path(path)
    need(p.is_absolute() and p.resolve(strict=True) == p, 'HISTORICAL_RECORD_PATH')
    for node in (p, *p.parents):
        s = node.lstat()
        need(s.st_uid == 0 and not s.st_mode & 0o022 and not node.is_symlink(),
             'HISTORICAL_RECORD_OWNER')
        need(not any(k.startswith('system.posix_acl') for k in os.listxattr(node)),
             'HISTORICAL_RECORD_ACL')
    s = p.stat()
    need(p.is_file() and s.st_nlink == 1 and not s.st_mode & 0o222
         and s.st_size <= 1024**2, 'HISTORICAL_IMMUTABLE_RECORD')
    return p


def observe_primary(spec):
    """Cross-check existing Root-sealed Prepare inventory; no live Git execution.

    spec is part of the externally approved request/policy, NOT caller-observed
    expected values. A user-owned Release alone can never supply authority.
    """
    binding_path = historical_record(spec['binding_path']); prepare_path = historical_record(spec['prepare_path'])
    need(sha(binding_path) == spec['binding_sha256'] and sha(prepare_path) == spec['prepare_sha256'],
         'PRIMARY_INDEPENDENT_EVIDENCE_HASH')
    binding, prepared = decode_record(binding_path.read_bytes()), decode_record(prepare_path.read_bytes())
    need(binding['prepare_sha256'] == sha(prepare_path), 'PRIMARY_PREPARE_BINDING')
    ident = {'source': prepared['source'], 'tree': prepared['tree'], 'parent': prepared['git_parent']}
    need(all(binding[k] == ident[k] for k in ('source', 'tree'))
         and ident == spec['identity'], 'PRIMARY_SEALED_IDENTITY')
    pointer = Path(spec['pointer']); target = Path(spec['release'])
    need(str(pointer) == '/opt/enterprise-agent-workbench-test/current'
         and target.parent == pointer.parent/'releases', 'PRIMARY_PATH_SCOPE')
    need(pointer.is_symlink() and pointer.resolve(strict=True) == target, 'PRIMARY_POINTER')
    # Existing ancestor ACLs are observations, not permission to execute code.
    # Pin their exact metadata/ACL bytes in the approved read-only observation.
    for name, expected in spec['ancestors'].items():
        p = Path(name); s = p.lstat()
        actual = {'uid': s.st_uid, 'gid': s.st_gid, 'mode': stat.S_IMODE(s.st_mode),
            'acl': {k: hashlib.sha256(os.getxattr(p, k)).hexdigest()
                    for k in os.listxattr(p) if k.startswith('system.posix_acl')}}
        need(not p.is_symlink() and actual == expected, 'PRIMARY_ANCESTOR_IDENTITY')
    required = {str(p) for p in target.parents}
    need(required <= set(spec['ancestors']), 'PRIMARY_ANCESTOR_SET')
    owner = spec['owner']
    root = readonly_node(target, owner, 0o555)
    gitfile = readonly_node(root/'.git', owner, 0o444)
    gitdir = readonly_node(Path(spec['gitdir']), owner, 0o555)
    need(gitdir.parent == pointer.parent/'shared/source-identities', 'PRIMARY_GITDIR_SCOPE')
    need({str(p) for p in gitdir.parents} <= set(spec['ancestors']), 'PRIMARY_GITDIR_ANCESTORS')
    need(gitfile.read_text().strip() == 'gitdir: '+str(gitdir), 'PRIMARY_GITDIR_LINK')
    head = readonly_node(gitdir/'HEAD', owner, 0o444)
    need(head.read_text().strip() == ident['source'], 'PRIMARY_GIT_HEAD')
    files = prepared['files']
    need(files and '.git' in files, 'PRIMARY_INSTALLED_INVENTORY')
    for name, expected in files.items():
        relative = Path(name)
        need(not relative.is_absolute() and '..' not in relative.parts and '\\' not in name,
             'PRIMARY_INVENTORY_PATH')
        path = root/relative
        need(path.resolve(strict=True) == path and path.is_file() and not path.is_symlink(),
             'PRIMARY_INVENTORY_TYPE')
        need(sha(path) == expected, 'PRIMARY_INSTALLED_BYTES')
    need(set(spec['units']) == {'enterprise-agent-test-'+r+'.service' for r in ('api', 'mcp', 'worker')},
         'PRIMARY_SERVICE_SET')
    for unit in spec['units']:
        need(running_cwd(unit) == str(root/'enterprise_agent_poc'), 'PRIMARY_PROCESS_CWD')
    need(pointer.resolve(strict=True) == root and sha(prepare_path) == spec['prepare_sha256']
         and sha(binding_path) == spec['binding_sha256'], 'PRIMARY_CHANGED_DURING_OBSERVATION')
    return {**ident, 'path': str(root)}


def finalize(request_path, decision_path):
    """Called under existing installer lock; no self-signing or privilege elevation."""
    need(sys.platform == 'linux' and os.geteuid() == os.getegid() == 0
         and sys.flags.isolated and sys.dont_write_bytecode, 'EXTERNAL_ROOT_INSTALLER_REQUIRED')
    request, decision = read(request_path), read(decision_path)
    # Authenticate this adapter and request before loading ANY staged Tooling.
    need(decision['adapter_sha256'] == sha(trust(Path(__file__).resolve()))
         and decision['request_sha256'] == digest(request), 'FIRST_BYTE_AUTHORIZATION')
    validate_authority(request, decision, int(time.time()))
    need(socket.gethostname() == request['host'], 'HOST_MISMATCH')
    need(str(Path(sys.executable).resolve()) == decision['installer_python']
         and sha(trust(decision['installer_python'])) == decision['installer_python_sha256'],
         'INSTALLER_PYTHON_PIN')
    base = Path('/opt/enterprise-agent-workbench-test')
    for key in ('tooling_root', 'repository', 'receipt_directory'):
        need(trust(request[key]).is_relative_to(base) and Path(request[key]) != base, 'TEST_PATH_SCOPE')
    need(request['primary_pointer'] == str(base/'current'), 'TEST_POINTER_ONLY')
    root = trust(request['tooling_root']); repo = trust(request['repository'])
    pins = tree_pins(root)
    need(pins == request['files'], 'TOOLING_FILE_PINS')
    tool = identity(repo, request['tooling']['source'])
    need(tool == request['tooling'], 'TOOLING_IDENTITY')
    # Match every staged byte to the immutable Git subtree, not just caller hashes.
    listing = git(repo, 'ls-tree', '-r', '--name-only', tool['source'], '--', 'enterprise_agent_poc/').decode().splitlines()
    expected = {name.removeprefix('enterprise_agent_poc/'): hashlib.sha256(
        git(repo, 'show', tool['source']+':'+name)).hexdigest() for name in listing}
    need(expected == pins, 'COMPLETE_GIT_BLOB_SET')
    runtime = request['runtime']; runtime_root = trust(runtime['root'])
    need(tree_pins(runtime_root) == runtime['files'], 'COMPLETE_RUNTIME_PINS')
    for role in ('python', 'postgres', 'redis', 'pgvector_control'):
        path = trust(runtime[role])
        need(path.is_relative_to(runtime_root) and path.is_file(), 'RUNTIME_BOUNDARY')
    for role, flag, expected_version in (('python', '--version', runtime['python_version']),
            ('postgres', '--version', '16.6'), ('redis', '--version', '7.0.15')):
        output = subprocess.check_output([runtime[role], flag], timeout=10,
            env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent'}).decode().strip()
        need(re.search(r'(?<![\d.])'+re.escape(expected_version)+r'(?![\d.])', output), 'RUNTIME_VERSION')
    need(re.search(r"default_version\s*=\s*'0\.8\.6'", Path(runtime['pgvector_control']).read_text()), 'PGVECTOR_VERSION')
    first_primary = observe_primary(request['primary_observation'])
    primary = {k: first_primary[k] for k in ('source', 'tree', 'parent')}
    need(primary == request['primary'], 'PRIMARY_MISMATCH')
    need(identity(repo, primary['source']) == primary, 'PRIMARY_INDEPENDENT_GIT_IDENTITY')
    import pwd
    account = pwd.getpwnam(request['operator']['name'])
    operator = {'name': account.pw_name, 'uid': account.pw_uid, 'gid': account.pw_gid}
    # The existing driver verifier is reused AFTER complete byte checks.
    need(not any(n == 'scripts' or n.startswith('scripts.') for n in sys.modules),
         'CLEAN_INSTALLER_IMPORT_CONTEXT')
    module = runpy.run_path(str(root/'scripts/test_delivery_pipeline.py'),
        init_globals={'_VERIFIED_TEST_ENTRY': True})
    artifacts = {}
    for role in ('candidate', 'predecessor'):
        a = request['artifacts'][role]
        trust(a['archive']); trust(a['manifest'])
        got = module['artifact'](repo, a['source'], a['archive'], a['manifest'])
        need(identity(repo, a['source'])['tree'] == a['tree'], 'ARTIFACT_TREE')
        artifacts[role] = dict(a, archive_sha256=got['archive_sha256'], manifest_sha256=got['manifest_sha256'])
    need(artifacts['predecessor']['source'] == primary['source'], 'PREDECESSOR_IDENTITY')
    v = os.statvfs(request['receipt_directory'])
    observed = dict(host=socket.gethostname(), primary=primary, tooling=tool, files=pins,
        runtime=runtime, artifacts=artifacts, operator=operator,
        free=v.f_bavail*v.f_frsize, total=v.f_blocks*v.f_frsize)
    receipt = validate(request, decision, observed, int(time.time()))
    need(observe_primary(request['primary_observation']) == first_primary,
         'PRIMARY_CHANGED_DURING_ADOPTION')
    receipt.update(decision_sha256=sha(decision_path), adapter_sha256=sha(__file__),
                   actual_uid=os.geteuid(), recorded_at=int(time.time()))
    # No caller-selected overwrite, activation, grant issuance or profile PASS.
    dest = trust(request['receipt_directory'])/(request['operation_id']+'.adoption.json')
    raw = (json.dumps(receipt, sort_keys=True)+'\n').encode()
    fd = os.open(dest, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o444)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    need(trust(dest).read_bytes() == raw, 'RECEIPT_READBACK')
    fd = os.open(dest.parent, os.O_RDONLY|os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return receipt
