"""Reusable Test candidate preparation. NOT a Production or PRIMARY switch entry.

Reuses build_release and release_manifest unchanged. Root adoption and execution
are deliberately separate from local package/plan generation. The operation
driver must already be independently approved and pinned by the Test operator.
No candidate Source, predecessor Source or credential is compiled into this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import subprocess
import sys
import tarfile
import threading
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
if hasattr(os, 'geteuid') and os.geteuid() == 0 and not globals().get('_VERIFIED_TEST_ENTRY'):
    raise SystemExit('ROOT_REQUIRES_VERIFIED_TEST_ENTRY')
sys.path.insert(0, str(PROJECT))
from scripts import build_release
from scripts.release_manifest import validate_manifest_contract

VERSION = 'TEST_DELIVERY_PLAN_V2'
LIMIT = 768 * 1024**2
HOST_RESERVE = 256 * 1024**2
SCRATCH = 704 * 1024**2
INPUT_LIMIT = 63 * 1024**2
RECEIPT_LIMIT = 1024**2
CHECKS = ('pg', 'redis', 'worker', 'mcp', 'authentication', 'portals', 'task',
          'sse_history', 'five_turns', 'full_text', 'idempotency', 'cancel',
          'unknown_recovery', 'tenant_user_isolation', 'old_reader_compatibility')


class Block(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise Block(reason)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def unique(pairs):
    out = {}
    for key, value in pairs:
        require(key not in out, 'DUPLICATE_JSON_FIELD')
        out[key] = value
    return out


def read(path):
    p = Path(path)
    require(p.is_file() and not p.is_symlink() and p.stat().st_size <= RECEIPT_LIMIT, 'JSON_INPUT_INVALID')
    return json.loads(p.read_bytes(), object_pairs_hook=unique)


def exclusive(path, value):
    raw = canonical(value) + b'\n'
    require(len(raw) <= RECEIPT_LIMIT, 'RECEIPT_TOO_LARGE')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    if sys.platform == 'linux':
        parent_fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)


def git(repo, *args):
    # Never run candidate hooks or inherit Git environment overrides.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    return subprocess.check_output(['git', '--no-optional-locks', '-c', 'core.hooksPath=',
                                   '-C', str(repo), *args], env=env, timeout=60)


def identity(repo, source):
    require(isinstance(source, str) and re.fullmatch('[0-9a-f]{40}', source), 'EXACT_SOURCE_REQUIRED')
    rows = git(repo, 'show', '-s', '--format=%H%n%T%n%P', source).decode().strip().splitlines()
    require(rows[0] == source, 'SOURCE_MISMATCH')
    return {'source': rows[0], 'tree': rows[1], 'git_parents': rows[2].split() if len(rows) > 2 else []}


def schema_identity(repo, source):
    names = git(repo, 'ls-tree', '-r', '--name-only', source, '--',
                'enterprise_agent_poc/migrations/postgres/').decode().splitlines()
    require(names, 'MIGRATION_SET_MISSING')
    return {p: hashlib.sha256(git(repo, 'show', source + ':' + p)).hexdigest() for p in names}


def artifact(repo, source, archive, manifest):
    """Verify original production-format artifact, full Git bytes and modes.

    Static cache substitution is the exact existing builder transformation.
    No candidate Python is imported and no archive member is extracted here.
    """
    archive, manifest = Path(archive), Path(manifest)
    require(not archive.is_symlink() and archive.is_file(), 'ARCHIVE_PATH_INVALID')
    require(archive.stat().st_size + manifest.stat().st_size <= INPUT_LIMIT, 'INPUT_BUDGET_EXCEEDED')
    m = validate_manifest_contract(read(manifest))
    require(m['source_commit'] == source and m['archive_sha256'] == sha(archive), 'ARTIFACT_IDENTITY_MISMATCH')
    previous = build_release.REPO
    try:
        build_release.REPO = Path(repo)
        selected = build_release.preflight(source)
        static_contract = build_release._static_asset_contract(source)
    finally:
        build_release.REPO = previous
    require(m['selected_files'] == selected, 'ARCHIVE_ALLOWLIST_MISMATCH')
    modes, blobs = {}, {}
    for record in git(repo, 'ls-tree', '-r', '-z', source).split(b'\0'):
        if record:
            header, name = record.split(b'\t', 1)
            modes[name.decode()] = header.split()[0].decode()
            blobs[name.decode()] = header.split()[2].decode()
    with tarfile.open(archive) as tf:
        require(tf.pax_headers.get('comment') == source, 'ARCHIVE_SOURCE_COMMENT')
        seen, files, expanded = set(), set(), 0
        for member in tf:
            p = PurePosixPath(member.name)
            require(member.name not in seen, 'DUPLICATE_ARCHIVE_MEMBER')
            seen.add(member.name)
            require(not p.is_absolute() and '..' not in p.parts and '\\' not in member.name
                    and p.parts and p.parts[0] == 'enterprise_agent_poc'
                    and (member.isfile() or member.isdir()), 'UNSAFE_ARCHIVE_MEMBER')
            if not member.isfile():
                continue
            require(member.name in selected, 'UNLISTED_ARCHIVE_FILE')
            expanded += member.size
            require(expanded <= INPUT_LIMIT, 'EXPANSION_BUDGET_EXCEEDED')
            expected = (static_contract['packaged_index'] if member.name == build_release.STATIC_INDEX
                        else git(repo, 'cat-file', 'blob', blobs[member.name]))
            require(tf.extractfile(member).read() == expected, 'GIT_ARCHIVE_BYTES_MISMATCH:' + member.name)
            require((member.mode & 0o111) == (int(modes[member.name], 8) & 0o111), 'GIT_MODE_MISMATCH')
            files.add(member.name)
        require(files == set(selected), 'ARCHIVE_FILE_SET_MISMATCH')
    return {'archive_sha256': sha(archive), 'manifest_sha256': sha(manifest),
            'manifest_identity': digest(m), 'expanded_bytes': expanded,
            'selected_file_count': len(selected), 'build_platform': m['build_platform']}


def package(repo, source, tree, output):
    ident = identity(repo, source)
    require(ident['tree'] == tree, 'TREE_MISMATCH')
    output = Path(output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    previous = build_release.REPO
    git_environment = {k: v for k, v in os.environ.items() if k.startswith('GIT_')}
    try:
        build_release.REPO = Path(repo)
        # Existing git-archive builder, explicit Linux-compatible text conversion.
        # Do not modify repository/global Git config or normalize verified bytes.
        for key in git_environment:
            os.environ.pop(key)
        os.environ.update(GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='core.autocrlf',
                          GIT_CONFIG_VALUE_0='false', GIT_CONFIG_KEY_1='core.eol', GIT_CONFIG_VALUE_1='lf')
        result = build_release.build(source, output / 'candidate.tar.gz')
        build_release.write_manifest(result, 'test-' + source, output / 'candidate.manifest.json')
    finally:
        build_release.REPO = previous
        for key in list(os.environ):
            if key.startswith('GIT_'):
                os.environ.pop(key)
        os.environ.update(git_environment)
    verified = artifact(repo, source, output / 'candidate.tar.gz', output / 'candidate.manifest.json')
    receipt = {'status': 'PACKAGE_VERIFIED_NOT_NATIVE', **ident, **verified}
    exclusive(output / 'package-receipt.json', receipt)
    return receipt


def plan(repo, source, tree, archive, manifest, observation, predecessor_archive=None, predecessor_manifest=None):
    """Observation is a proposal input, NEVER an authority or fresh runtime proof."""
    candidate = identity(repo, source)
    require(candidate['tree'] == tree, 'TREE_MISMATCH')
    require(set(observation) == {'environment', 'hostname', 'primary_source', 'primary_tree',
                                'primary_path', 'observed_at'}, 'OBSERVATION_FIELDS')
    require(observation['environment'] == 'test', 'TEST_ONLY')
    predecessor = identity(repo, observation['primary_source'])
    require(predecessor['tree'] == observation['primary_tree'], 'PREDECESSOR_TREE_MISMATCH')
    verified = artifact(repo, source, archive, manifest)
    old_schema, new_schema = schema_identity(repo, predecessor['source']), schema_identity(repo, source)
    result = {'contract': VERSION, 'operation_id': str(uuid.uuid4()), 'environment': 'test',
            'action': 'isolated_qualify', 'candidate': candidate, 'observed_predecessor': predecessor,
            'observation': observation, 'artifact': verified, 'checks': list(CHECKS),
            'limits': {'aggregate_bytes': LIMIT, 'scratch_bytes': SCRATCH,
                       'input_bytes': INPUT_LIMIT, 'receipt_bytes': RECEIPT_LIMIT,
                       'host_reserve_bytes': HOST_RESERVE, 'min_free_fraction': 0.2},
            'rollback': {'target': predecessor, 'migration_files_unchanged': old_schema == new_schema,
                         'primary_recovery_ready': False,
                         'reason': 'Actual ledger, old-reader and protected recovery proof still required; never restore DB automatically'},
            'calls': {'text': 0, 'image': 0, 'wechat': 0},
            'production_authority': False, 'primary_switch_authority': False}
    require(bool(predecessor_archive) == bool(predecessor_manifest), 'PREDECESSOR_ARTIFACT_PAIR_REQUIRED')
    if predecessor_archive:
        result['predecessor_artifact'] = artifact(repo, predecessor['source'], predecessor_archive, predecessor_manifest)
        require(sum(Path(p).stat().st_size for p in (archive, manifest, predecessor_archive, predecessor_manifest)) <= INPUT_LIMIT,
                'COMBINED_INPUT_BUDGET')
    return result


def delivery_plan(repo, source, tree, archive, manifest, observation, predecessor):
    """Versioned handoff to the EXISTING Test installer/recovery maintainer.

    Identity observation is not archive provenance or recovery authority. This
    unsigned plan never makes a current pointer writable or creates a grant.
    The stable actions are reusable; only per-operation identities change.
    """
    result = plan(repo, source, tree, archive, manifest, observation)
    mode = predecessor.get('proof_kind')
    require(mode in ('ORIGINAL_RELEASE_ARTIFACT', 'INSTALLED_IDENTITY_OBSERVATION'),
            'PREDECESSOR_PROOF_KIND')
    require(predecessor['identity'] == result['observed_predecessor'], 'PREDECESSOR_IDENTITY')
    require(predecessor.get('evidence_sha256') and re.fullmatch('[a-f0-9]{64}', predecessor['evidence_sha256']),
            'PREDECESSOR_EVIDENCE_HASH')
    if mode == 'ORIGINAL_RELEASE_ARTIFACT':
        require(predecessor.get('archive') and predecessor.get('manifest'), 'ORIGINAL_PAIR_REQUIRED')
        result['predecessor_artifact'] = artifact(repo, predecessor['identity']['source'],
                                                predecessor['archive'], predecessor['manifest'])
    else:
        require(not any(k in predecessor for k in ('archive', 'manifest')), 'OBSERVATION_NOT_ORIGINAL_ARCHIVE')
    result.update(contract='STANDARD_TEST_DELIVERY_V1', action='test_delivery',
                  predecessor_proof=predecessor,
                  steps=['preflight', 'backup', 'install', 'activate', 'health', 'qualify', 'export'],
                  execution_scope={'environment': 'test', 'actions': ['prepare', 'activate', 'recover', 'status'],
                      'binding': ['operation_id', 'request_sha256', 'policy_sha256', 'host', 'actor',
                                  'candidate', 'artifact', 'observed_predecessor', 'runtime_profile_sha256'],
                      'issuer': 'EXISTING_AUTHORIZED_TEST_EXECUTOR_ONLY', 'paid_calls': 0,
                      'production': False, 'preview': False},
                  activation_ready=False, recovery_ready=False,
                  predecessor_original_artifact_available=mode == 'ORIGINAL_RELEASE_ARTIFACT')
    result['rollback'].update(reason='Read-only observed identity is NOT an original archive or executable rollback proof; '
        'restore-tested backup, exact installed predecessor acceptance and data compatibility require separate verification')
    return result


def delivery_journal(operations_root, request):
    """Existing executor's exclusive operation claim and append-only receipts.

    The executor owns the global release lock across dispatch, including callback
    effects; this operation claim additionally prevents duplicate SSH requests.
    Any mkdir/fsync/readback uncertainty remains visible and is never replayed.
    """
    root = trusted(operations_root, directory=True)
    opid = request['operation_id']
    require(str(uuid.UUID(opid)) == opid, 'OPERATION_MISMATCH')
    operation = root / opid
    prior = None
    def append(stage, payload):
        nonlocal prior
        require(re.fullmatch(r'(claim|intent:[a-z]+|result:[a-z]+)', stage), 'JOURNAL_STAGE')
        if stage == 'claim':
            operation.mkdir(mode=0o700)  # existing/PENDING/UNKNOWN => reject, not replay
        record = {'operation_id': opid, 'request_sha256': digest(request),
                  'stage': stage, 'prior_sha256': prior, 'payload': payload}
        dest = operation / (stage.replace(':', '-') + '.json')
        exclusive(dest, record)
        require(read(dest) == record, 'JOURNAL_COMMIT_UNKNOWN')
        prior = digest(record)
    return append


def delivery_scope(request, policy, grant, now):
    """Consume, never issue, a current record from the existing Test authority.

    Existing protected executor must authenticate the issuer record and actor,
    read these inputs from trusted storage and hold its release lock throughout
    dispatch. This check is also called between every effect/receipt, not once
    before SSH/TTY waiting. No Source is compiled into the consumer.
    """
    require(request['contract'] == 'STANDARD_TEST_DELIVERY_V1' and request['action'] == 'test_delivery'
            and policy['contract'] == 'STANDARD_TEST_DELIVERY_POLICY_V1'
            and request['environment'] == policy['environment'] == 'test', 'TEST_DELIVERY_CONTRACT')
    require(policy['hostname'] == request['observation']['hostname'] == grant['hostname'], 'HOST_BINDING_MISMATCH')
    require(grant['action'] == 'test_delivery' and grant['operation_id'] == request['operation_id']
            and grant['request_sha256'] == digest(request) and grant['policy_sha256'] == digest(policy),
            'DELIVERY_GRANT_BINDING')
    require(grant['actor'] == policy['operator'] and type(policy['operator']['uid']) is int
            and type(policy['operator']['gid']) is int
            and policy['operator']['uid'] > 0 and policy['operator']['gid'] > 0,
            'DELIVERY_ACTOR_IDENTITY')
    require(grant['tooling'] == policy['tooling']
            and all(re.fullmatch('[a-f0-9]{40}', policy['tooling'][key]) for key in ('source', 'tree'))
            and grant['runtime_profile_sha256'] == policy['runtime_profile_sha256']
            and re.fullmatch('[a-f0-9]{64}', policy['runtime_profile_sha256'])
            and re.fullmatch('[a-f0-9]{64}', policy['tooling_files_sha256']), 'DELIVERY_EXECUTABLE_BINDING')
    require(grant['issuer_record_sha256'] == policy['accepted_issuer_record_sha256']
            and re.fullmatch('[a-f0-9]{64}', grant['issuer_record_sha256']), 'EXISTING_ISSUER_REQUIRED')
    require(grant['actions'] == policy['actions'] == ['prepare', 'activate', 'recover', 'status']
            and grant['production_authority'] is False and grant['preview_authority'] is False
            and request['calls'] == {'text': 0, 'image': 0, 'wechat': 0}, 'DELIVERY_SCOPE_EXPANSION')
    require(type(grant['not_before']) is int and type(grant['expires_at']) is int
            and grant['not_before'] <= now < grant['expires_at']
            and 0 < grant['expires_at'] - grant['not_before'] <= 14400
            and grant['revoked'] is False and type(grant['uses']) is int and grant['uses'] == 1,
            'DELIVERY_GRANT_EXPIRED_OR_REVOKED')


def dispatch_delivery(request, run_step, journal, revalidate):
    """Thin sequencing hook for the existing protected Test release executor.

    NOT a standalone Root entry. Caller must hold its existing global release
    lock, authenticate/pin this hook BEFORE import, and supply its fixed reviewed
    release/backup/recovery adapter. Neither callback can come from request JSON.
    journal must durably append/read back under that lock; it cannot overwrite.
    No automatic re-execution of an interrupted install/switch/backup is allowed.
    Existing Recovery owns data compatibility and requires its own authorization.
    """
    require(request['contract'] == 'STANDARD_TEST_DELIVERY_V1' and
            request['environment'] == 'test' and request['calls'] == {'text': 0, 'image': 0, 'wechat': 0},
            'STANDARD_TEST_SCOPE')
    require(request['steps'] == ['preflight', 'backup', 'install', 'activate', 'health', 'qualify', 'export'],
            'FIXED_DELIVERY_STEPS')
    # No resume-by-replay. status/recovery are existing-executor operations on
    # this original ID, not a new deployment after SSH disconnect.
    revalidate(request)
    journal('claim', {'request_sha256': digest(request), 'operation_id': request['operation_id']})
    receipts = {}
    for step in request['steps']:
        revalidate(request)
        journal('intent:' + step, {'request_sha256': digest(request)})
        # Failure from callback or fsync/readback deliberately leaves intent
        # pending. Never label that as NO_WRITE or initiate a blind rollback.
        result = run_step(step, request, dict(receipts))
        require(isinstance(result, dict) and result.get('status') == 'PASS'
                and result.get('operation_id') == request['operation_id']
                and result.get('request_sha256') == digest(request), 'STEP_RECEIPT_BINDING:' + step)
        revalidate(request)
        journal('result:' + step, result)
        receipts[step] = result
    return {'status': 'TEST_DELIVERY_COMPLETED_PENDING_PRODUCTION_REVIEW',
            'operation_id': request['operation_id'], 'receipts': receipts, 'production_authorized': False}


def trusted(path, directory=False):
    p = Path(path)
    require(p.is_absolute() and p.resolve(strict=True) == p, 'TRUST_PATH_NOT_CANONICAL')
    for item in (p, *p.parents):
        st = item.lstat()
        require(not stat.S_ISLNK(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022,
                'ROOT_PATH_TRUST_REQUIRED')
    require(p.is_dir() if directory else p.is_file(), 'TRUST_PATH_TYPE')
    # POSIX ACLs can grant access not visible in group mode. Fail if uninspectable.
    if hasattr(os, 'listxattr'):
        for item in (p, *p.parents):
            require(not any(x in os.listxattr(item) for x in
                            ('system.posix_acl_access', 'system.posix_acl_default')), 'ACL_REVIEW_REQUIRED')
    return p


def authority(request, policy, grant, now):
    require(request['contract'] == VERSION and request['environment'] == policy['environment'] == 'test', 'TEST_ONLY')
    require(request['action'] == grant['action'] == 'isolated_qualify', 'ACTION_NOT_ADOPTED')
    require(not request['production_authority'] and not request['primary_switch_authority'], 'NO_SWITCH_AUTHORITY')
    require(request['calls'] == {'text': 0, 'image': 0, 'wechat': 0}, 'ZERO_COST_REQUIRED')
    require(request['limits'] == {'aggregate_bytes': LIMIT, 'scratch_bytes': SCRATCH,
        'input_bytes': INPUT_LIMIT, 'receipt_bytes': RECEIPT_LIMIT,
        'host_reserve_bytes': HOST_RESERVE, 'min_free_fraction': 0.2}, 'EXACT_LIMITS_REQUIRED')
    require(set(request['checks']) == set(CHECKS) and len(request['checks']) == len(CHECKS), 'QUALIFICATION_CHECKS_REQUIRED')
    require(grant['request_sha256'] == digest(request) and grant['policy_sha256'] == digest(policy), 'GRANT_BINDING_MISMATCH')
    require(grant['operation_id'] == request['operation_id'] and str(uuid.UUID(request['operation_id'])) == request['operation_id'], 'OPERATION_MISMATCH')
    require(type(grant['not_before']) is int and type(grant['expires_at']) is int
            and grant['not_before'] <= now < grant['expires_at']
            and 0 < grant['expires_at'] - grant['not_before'] <= 4 * 3600, 'GRANT_TIME_INVALID')
    require(grant['revoked'] is False and grant['uses'] == 1 and grant['issuer_record'], 'GRANT_REVOKED_OR_INVALID')
    require(policy['hostname'] == request['observation']['hostname'], 'HOST_BINDING_MISMATCH')


def observe(policy):
    require(sys.platform == 'linux' and socket.gethostname() == policy['hostname'], 'LIVE_HOST_MISMATCH')
    pointer = Path(policy['primary_pointer'])
    target = pointer.resolve(strict=True)
    release_root = Path(policy['release_root'])
    require(str(release_root) == '/opt/enterprise-agent-workbench-test/releases', 'TEST_RELEASE_ROOT')
    require(target.is_relative_to(release_root) and target != release_root, 'PRIMARY_PATH_OUTSIDE_TEST')
    # Historical PRIMARY is evidence, not Root-executable Tooling. Never run
    # Root Git against its user-owned Gitfile/config. Reuse sealed observation.
    from scripts.test_delivery_adoption import observe_primary
    observed = observe_primary(policy['primary_observation'])
    require(observed['path'] == str(target), 'PRIMARY_OBSERVATION_PATH')
    return {'source': observed['source'], 'tree': observed['tree'],
            'git_parents': observed['parent'].split(), 'path': observed['path']}


def storage_gate(free, total):
    require(total > 0 and free - LIMIT - HOST_RESERVE > total * 0.2, 'RESERVED_DISK_FLOOR_BLOCKED')


def qualification_result(result, request):
    require(result.get('validation_level') == 'PROTECTED_ISOLATED_QUALIFICATION'
            and result.get('test_native') == 'PENDING_EXTERNAL_ACCEPTANCE', 'DEV_IS_NOT_NATIVE')
    require(result['operation_id'] == request['operation_id']
            and result['source'] == request['candidate']['source']
            and result['tree'] == request['candidate']['tree'], 'RESULT_IDENTITY_MISMATCH')
    require(result['provider_calls'] == 0 and result['production_changes'] == 0
            and result['preview_changes'] == 0 and result['primary_changes'] == 0, 'ISOLATION_VIOLATION')
    require(result['backend'] == 'postgresql-redis-worker-mcp' and result['checks'] == {x: 'PASS' for x in CHECKS}, 'INCOMPLETE_NATIVE_RESULT')
    require(type(result['disk_peak_bytes']) is int and 0 <= result['disk_peak_bytes'] <= LIMIT
            and isinstance(result['min_root_free_fraction'], (int, float))
            and .2 < result['min_root_free_fraction'] <= 1, 'RUNTIME_SPACE_GATE')
    return result


def sandbox_command(request, policy, op, archive, manifest, predecessor_archive=None, predecessor_manifest=None):
    """Fixed non-Root sandbox; no caller-supplied command or shell interpolation.

    The reviewed driver receives a read-only request and production-format package.
    ALL scratch writes, including PG/WAL, Redis, HOME and logs, belong in /private/tmp.
    704MiB tmpfs + <=63MiB inputs + <=1MiB Root receipt <=768MiB reservation.
    The tmpfs is additionally charged to the existing Test cgroup memory budget.
    """
    runtime = PurePosixPath(policy['runtime_root'])
    tooling = PurePosixPath(policy['tooling_root'])
    python = PurePosixPath(policy['python']).relative_to(runtime)
    driver = PurePosixPath(policy['qualification_driver']).relative_to(tooling)
    op, archive, manifest = (PurePosixPath(x.as_posix()) for x in (op, archive, manifest))
    require(predecessor_archive is not None and predecessor_manifest is not None, 'PREDECESSOR_ARTIFACT_REQUIRED')
    predecessor_archive, predecessor_manifest = (PurePosixPath(x.as_posix()) for x in (predecessor_archive, predecessor_manifest))
    require(policy['slice'] == 'enterprise-agent-test.slice', 'TEST_SLICE_REQUIRED')
    require(policy['driver_protocol'] == 'QUALIFICATION_DRIVER_PROTOCOL_V1', 'DRIVER_PROTOCOL_NOT_ADOPTED')
    # All /opt and /home data is hidden; only approved runtime/tooling/input are rebound.
    props = [
        'Slice=enterprise-agent-test.slice', 'CPUQuota=100%', 'MemoryHigh=1280M',
        'MemoryMax=1536M', 'MemorySwapMax=0', 'TasksMax=128', 'RuntimeMaxSec=900',
        'TimeoutStopSec=30', 'KillMode=control-group', 'SendSIGKILL=yes',
        'NoNewPrivileges=yes', 'PrivateNetwork=yes', 'PrivateDevices=yes',
        'ProtectProc=invisible', 'ProcSubset=pid',
        'ProtectSystem=strict', 'ProtectHome=yes', 'ProtectControlGroups=yes',
        'ProtectKernelTunables=yes', 'ProtectKernelModules=yes',
        'RestrictSUIDSGID=yes', 'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6',
        'CapabilityBoundingSet=', 'UMask=0077', 'WorkingDirectory=/private/tmp',
        f'TemporaryFileSystem=/private/tmp:rw,nosuid,nodev,size={SCRATCH},mode=0700,uid={policy["operator_uid"]},gid={policy["operator_gid"]}',
        'InaccessiblePaths=-/opt -/home -/root -/run -/tmp -/etc -/var/lib -/var/log',
        'ReadWritePaths=/private/tmp',
        f'BindReadOnlyPaths={runtime}:/runtime {tooling}:/tooling {archive}:/input/archive.tar.gz {manifest}:/input/manifest.json {op / "request.json"}:/input/request.json {predecessor_archive}:/input/predecessor.tar.gz {predecessor_manifest}:/input/predecessor.manifest.json /etc/passwd:/etc/passwd /etc/group:/etc/group',
    ]
    require(all(not any(c in str(p) for c in (' ', '\n', ':', '\\')) for p in
                (runtime, tooling, archive, manifest, predecessor_archive, predecessor_manifest, op)), 'SANDBOX_PATH_ENCODING')
    return ['/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--collect',
            '--unit=test-delivery-' + request['operation_id'],
            '--uid=' + str(policy['operator_uid']), '--gid=' + str(policy['operator_gid']),
            *[x for p in props for x in ('-p', p)],
            '/usr/bin/env', '-i', 'PATH=/runtime/bin:/usr/bin:/bin', 'HOME=/private/tmp',
            'TMPDIR=/private/tmp', 'PYTHONDONTWRITEBYTECODE=1', 'APP_ENV=test', 'TEST_DELIVERY_SANDBOX=v1',
            'ENTERPRISE_POC_MODEL_PROVIDER_ID=disabled-test-provider',
            'ENTERPRISE_POC_IMAGE_PROVIDER_ID=disabled-test-provider',
            'ENTERPRISE_POC_WECHAT_NETWORK_ALLOWED=false',
            '/runtime/' + python.as_posix(), '-I', '-B', '/tooling/' + driver.as_posix(),
            '--request', '/input/request.json', '--archive', '/input/archive.tar.gz',
            '--manifest', '/input/manifest.json', '--predecessor-archive', '/input/predecessor.tar.gz',
            '--predecessor-manifest', '/input/predecessor.manifest.json', '--root', '/private/tmp']


def bounded_run(command, unit, root_floor, revalidate):
    """Capture at most 1MiB and stop this exact transient unit on failure.

    No retries and no arbitrary service operations. The disk limit is backed by
    tmpfs; monitoring detects unrelated host growth, it is not the hard quota.
    """
    data = bytearray(); errors = []
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
    def collect():
        try:
            while chunk := proc.stdout.read(8192):
                if len(data) + len(chunk) > RECEIPT_LIMIT // 2:
                    errors.append('DRIVER_OUTPUT_LIMIT'); return
                data.extend(chunk)
        except OSError:
            errors.append('DRIVER_OUTPUT_FAILED')
    reader = threading.Thread(target=collect, daemon=True); reader.start()
    deadline = time.monotonic() + 930
    try:
        while proc.poll() is None:
            revalidate()
            st = os.statvfs('/')
            if st.f_bavail * st.f_frsize <= root_floor:
                errors.append('HOST_DISK_STOP_THRESHOLD')
            if errors or time.monotonic() >= deadline:
                raise Block(errors[0] if errors else 'DRIVER_TIMEOUT')
            time.sleep(.2)
        reader.join(timeout=5)
        require(not reader.is_alive() and not errors, 'DRIVER_OUTPUT_FAILED')
        result = json.loads(data, object_pairs_hook=unique)
        if proc.returncode != 0:
            stage = result.get('failed_stage', 'UNKNOWN')
            require(isinstance(stage, str) and re.fullmatch('[A-Z0-9_:-]{1,80}', stage), 'DRIVER_FAILED')
            raise Block('DRIVER_STAGE_FAILED:' + stage)
        return result
    finally:
        # Even on SIGPIPE/output excess the entire isolated cgroup is stopped.
        subprocess.run(['/usr/bin/systemctl', 'stop', unit], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=40, check=False)
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        reader.join(timeout=5)


def _execute_locked(request_path, policy_path, grant_path, archive_path, manifest_path, predecessor_archive_path, predecessor_manifest_path):
    """Only installed trusted tooling and a live independently granted operation.

    No sudo, authority creation, downloads, service switching or paid retries.
    The adopted driver owns real qualification, not this contract's offline tests.
    """
    require(sys.platform == 'linux' and os.geteuid() == 0, 'ADOPTED_ROOT_ENTRY_REQUIRED')
    request = read(trusted(request_path)); policy = read(trusted(policy_path)); grant = read(trusted(grant_path))
    for filename, expected in policy['tooling_files'].items():
        require(sha(trusted(filename)) == expected, 'TOOLING_PIN_MISMATCH')
    require(str(Path(__file__).resolve()) in policy['tooling_files'], 'ENTRY_NOT_ADOPTED')
    authority(request, policy, grant, int(time.time()))
    # No ongoing run may overrun the grant. This is still a single-use operation.
    require(grant['expires_at'] - time.time() >= 960, 'GRANT_RUNTIME_RESERVE')
    trusted(policy['runtime_root'], directory=True); trusted(policy['tooling_root'], directory=True)
    runtime = trusted(policy['python']); driver = trusted(policy['qualification_driver'])
    require(sha(runtime) == policy['python_sha256'] and sha(driver) == policy['driver_sha256'], 'DEPENDENCY_PIN_MISMATCH')
    require(type(policy['operator_uid']) is int and policy['operator_uid'] > 0
            and type(policy['operator_gid']) is int and policy['operator_gid'] > 0, 'NONROOT_REQUIRED')
    # Same-UID proc access can expose another service's environment despite a
    # private network. A generic sandbox must use an independently adopted UID.
    import pwd
    require(policy['protected_service_units'], 'PROTECTED_SERVICES_REQUIRED')
    for unit in policy['protected_service_units']:
        require(re.fullmatch('[A-Za-z0-9_-]+[.]service', unit), 'SERVICE_NAME_INVALID')
        user = subprocess.check_output(['/usr/bin/systemctl', 'show', unit, '-pUser', '--value'], timeout=10).decode().strip() or 'root'
        protected_uid = int(user) if user.isdigit() else pwd.getpwnam(user).pw_uid
        require(policy['operator_uid'] != protected_uid, 'OPERATOR_PROC_ISOLATION_REQUIRED')
    archive = trusted(archive_path); manifest = trusted(manifest_path)
    require(sha(archive) == request['artifact']['archive_sha256']
            and sha(manifest) == request['artifact']['manifest_sha256'], 'TRANSFER_HASH_MISMATCH')
    actual = artifact(trusted(policy['source_repository'], directory=True), request['candidate']['source'], archive, manifest)
    require(actual == request['artifact'], 'ARTIFACT_RECHECK_MISMATCH')
    require(identity(policy['source_repository'], request['candidate']['source']) == request['candidate'], 'CANDIDATE_GIT_IDENTITY')
    require(predecessor_archive_path and predecessor_manifest_path and request.get('predecessor_artifact'),
            'PREDECESSOR_ARTIFACT_REQUIRED')
    prior_archive = trusted(predecessor_archive_path); prior_manifest = trusted(predecessor_manifest_path)
    require(artifact(policy['source_repository'], request['observed_predecessor']['source'], prior_archive, prior_manifest)
            == request['predecessor_artifact'], 'PREDECESSOR_ARTIFACT_RECHECK')
    require(sum(p.stat().st_size for p in (archive, manifest, prior_archive, prior_manifest)) <= INPUT_LIMIT,
            'COMBINED_INPUT_BUDGET')
    live = observe(policy)
    require(live['source'] == request['observed_predecessor']['source']
            and live['tree'] == request['observed_predecessor']['tree']
            and live['path'] == request['observation']['primary_path'], 'FRESH_PRIMARY_DRIFT')
    space = os.statvfs('/')
    storage_gate(space.f_bavail * space.f_frsize, space.f_blocks * space.f_frsize)
    operations = trusted(policy['operations_root'], directory=True)
    require(policy['native_adoption_receipt_sha256'] == sha(trusted(policy['native_adoption_receipt'])), 'LINUX_PROFILE_NOT_QUALIFIED')
    adoption = read(policy['native_adoption_receipt'])
    require(adoption['status'] == 'PASS' and adoption.get('sandbox_profile') == 'PRIVATE_STAGE2_TMPFS_V2' and adoption['checks'] == {
        k: 'PASS' for k in ('namespace', 'no_host_socket_access', 'write_containment',
                          'disk_exhaustion', 'cgroup_termination', 'zero_egress', 'proc_secret_isolation')}
        and adoption['driver_sha256'] == policy['driver_sha256'], 'LINUX_PROFILE_NOT_QUALIFIED')
    command = sandbox_command(request, policy, operations / request['operation_id'], archive, manifest, prior_archive, prior_manifest)
    # One operation: exclusive mkdir is the claim; a failed/unknown run is never replayed.
    op = operations / request['operation_id']; op.mkdir(mode=0o700)
    exclusive(op / 'attempted.json', {'request_sha256': digest(request), 'grant_sha256': sha(grant_path), 'time': int(time.time())})
    exclusive(op / 'request.json', request); (op / 'request.json').chmod(0o444)
    try:
        result = bounded_run(command, 'test-delivery-' + request['operation_id'] + '.service',
                             space.f_blocks * space.f_frsize * .2 + HOST_RESERVE,
                             lambda: authority(request, read(trusted(policy_path)),
                                               read(trusted(grant_path)), int(time.time())))
        qualification_result(result, request)
        require(observe(policy) == live, 'PRIMARY_CHANGED_DURING_TEST')
        authority(request, read(trusted(policy_path)), read(trusted(grant_path)), int(time.time()))
        exclusive(op / 'result.json', {'status': 'ISOLATED_QUALIFICATION_PASS_NOT_PRIMARY_DEPLOYED',
                                     'request_sha256': digest(request), 'result': result})
        return result
    except BaseException as exc:
        # Redacted exception category only; no traceback/DSN/credentials.
        exclusive(op / 'failure.json', {'status': 'BLOCKED_NO_AUTOMATIC_RETRY',
                  'error': str(exc) if isinstance(exc, Block) else type(exc).__name__})
        raise


def execute(request_path, policy_path, grant_path, archive_path, manifest_path, predecessor_archive_path=None, predecessor_manifest_path=None):
    require(sys.platform == 'linux' and os.geteuid() == 0, 'ADOPTED_ROOT_ENTRY_REQUIRED')
    import fcntl
    policy = read(trusted(policy_path))
    operations = trusted(policy['operations_root'], directory=True)
    fd = os.open(operations / '.qualification.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o077
                and s.st_nlink == 1, 'QUALIFICATION_LOCK_TRUST')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Block('TEST_QUALIFICATION_ALREADY_RUNNING') from None
        return _execute_locked(request_path, policy_path, grant_path, archive_path, manifest_path, predecessor_archive_path, predecessor_manifest_path)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    for mode in ('package', 'plan', 'verify', 'delivery-plan'):
        p = sub.add_parser(mode); p.add_argument('--repo', type=Path, required=True)
        p.add_argument('--source', required=True); p.add_argument('--tree', required=True)
        if mode == 'package': p.add_argument('--output', type=Path, required=True)
        else:
            p.add_argument('--archive', type=Path, required=True); p.add_argument('--manifest', type=Path, required=True)
        if mode in ('plan', 'delivery-plan'):
            p.add_argument('--observation', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
            p.add_argument('--predecessor-archive', type=Path); p.add_argument('--predecessor-manifest', type=Path)
        if mode == 'delivery-plan':
            p.add_argument('--predecessor-proof', type=Path, required=True)
    p = sub.add_parser('execute')
    for key in ('request', 'policy', 'grant', 'archive', 'manifest', 'predecessor-archive', 'predecessor-manifest'):
        p.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'execute': result = execute(args.request, args.policy, args.grant, args.archive, args.manifest, args.predecessor_archive, args.predecessor_manifest)
    elif args.mode == 'package': result = package(args.repo, args.source, args.tree, args.output)
    elif args.mode == 'verify':
        require(identity(args.repo, args.source)['tree'] == args.tree, 'TREE_MISMATCH')
        result = artifact(args.repo, args.source, args.archive, args.manifest)
    elif args.mode == 'delivery-plan':
        result = delivery_plan(args.repo, args.source, args.tree, args.archive, args.manifest,
                               read(args.observation), read(args.predecessor_proof))
        exclusive(args.output, result)
        result = {'status': 'UNSIGNED_DELIVERY_PLAN_NOT_AUTHORITY', 'request_sha256': digest(result)}
    else:
        result = plan(args.repo, args.source, args.tree, args.archive, args.manifest, read(args.observation), args.predecessor_archive, args.predecessor_manifest)
        exclusive(args.output, result)
        result = {'status': 'UNSIGNED_PLAN_NOT_AUTHORITY', 'request_sha256': digest(result)}
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'BLOCKED', 'error': str(exc) if isinstance(exc, Block) else type(exc).__name__}))
        raise SystemExit(2)
