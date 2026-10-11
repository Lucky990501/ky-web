"""Native backend for exactly d8a -> frozen810, existing Test services only.

This file consumes authority; it cannot issue one. No --prepare/--seal command,
database writer, Production path, Selector modification or synthetic cleanup.
"""
from __future__ import annotations

import ast
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.request
from uuid import UUID, uuid4
from urllib.parse import urlsplit

from rag810_test_operator import APPLICATION, PREDECESSOR, ROLES, VERSION, Blocked, canonical, need, sha, validate_live, validate_journal

HOME = Path('/opt/enterprise-agent-workbench-test')
ROOT = Path('/etc/enterprise-agent-test-rag-successor-v1')
PRED = HOME / 'releases/20261008-d8a7814-wechat-personal-v1'
LOCK = Path('/run/lock/enterprise-agent-test-successor-v1.lock')
PY = HOME / 'shared/runtime/python311/bin/python'
FORMAL = Path('/etc/enterprise-agent-test-successor-chat-reference-v1/entry.py')
FORMAL_SHA = '5f91603551e38119a294de7ecdce457ef19d557e902c1af0cfc2726f49af6b9e'
OWNER = Path('/etc/enterprise-agent-test-successor-wechat-d8a-seeding-approval-v1/test_git_owner_context.py')
OWNER_SHA = 'a36ab71dade804fa8c46ef3c917dfb793e2815ba4e5976c62b6e7cabbdb32094'
HISTORY = Path('/etc/enterprise-agent-test-wechat-persistent-config-v1/primary_guard.py')
BOOT_SHA = 'fd14229de8d91fcbc9d02b4425ec671d2f1bfad2e7b9646e23a57419f98a24fe'
UNITS = {r: 'enterprise-agent-test-' + r + '.service' for r in ROLES}
DEST = {r: Path('/etc/systemd/system') / (UNITS[r] + '.d') / 'zzzzz-rag810-native-v1.conf' for r in ROLES}
CODE = ('rag810_test_operator.py', 'rag810_test_operator_native.py')
ENV_KEYS = {'APP_ENV', 'ENTERPRISE_POC_DATABASE_URL', 'REDIS_URL', 'ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA',
    'ENTERPRISE_POC_TASK_QUEUE', 'ENTERPRISE_POC_TASK_QUEUE_NAMESPACE', 'EMBEDDING_PROVIDER',
    'EMBEDDING_DIMENSION', 'ENTERPRISE_POC_MODEL_PROVIDER_ID', 'ENTERPRISE_POC_MODEL_BASE_URL'}


def parse_test_environment(raw):
    result = {}
    for line in raw.decode('utf-8').splitlines():
        if not line.strip() or line.lstrip().startswith('#'): continue
        key, sep, value = line.partition('=')
        key = key.strip()
        if key not in ENV_KEYS: continue  # never import shell/Python loader/provider secrets
        need(sep and key not in result, 'RAG_OP_ENV_DATA_SHAPE')
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in ('"', "'"): value = value[1:-1]
        result[key] = value
    # These are exact frozen810 Settings defaults, not new config mutations.
    for key, value in {'ENTERPRISE_POC_TASK_QUEUE_NAMESPACE': 'enterprise-agent',
                       'EMBEDDING_PROVIDER': 'local-hash', 'EMBEDDING_DIMENSION': '128'}.items():
        result.setdefault(key, value)
    required = {'APP_ENV': 'test', 'ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA': 'false',
        'ENTERPRISE_POC_TASK_QUEUE': 'redis', 'ENTERPRISE_POC_TASK_QUEUE_NAMESPACE': 'enterprise-agent',
        'EMBEDDING_PROVIDER': 'local-hash', 'EMBEDDING_DIMENSION': '128',
        'ENTERPRISE_POC_MODEL_PROVIDER_ID': 'disabled-test-provider',
        'ENTERPRISE_POC_MODEL_BASE_URL': 'http://127.0.0.1:9/disabled'}
    need(all(result.get(k) == v for k, v in required.items()), 'RAG_OP_ENV_ZERO_CALL_CONTRACT')
    db, redis = [urlsplit(result.get(k, '')) for k in ('ENTERPRISE_POC_DATABASE_URL', 'REDIS_URL')]
    need(db.scheme in ('postgres', 'postgresql') and not db.query and not db.fragment and
         (db.hostname, db.port, db.username, db.path) ==
         ('127.0.0.1', 55432, 'enterprise_agent_test', '/enterprise_agent_test'), 'RAG_OP_ENV_DB_TARGET')
    need(redis.scheme == 'redis' and not redis.query and not redis.fragment and
         (redis.hostname, redis.port, redis.username) == ('127.0.0.1', 56379, 'enterprise_agent_test') and
         redis.path in ('', '/0'), 'RAG_OP_ENV_REDIS_TARGET')
    return result


def trust(path, readonly=False):
    for item in (path, *path.parents):
        st = item.lstat()
        need(not stat.S_ISLNK(st.st_mode) and st.st_uid == st.st_gid == 0 and not st.st_mode & 0o022,
             'RAG_OP_PATH_TRUST')
    if readonly:
        st = path.lstat()
        need(stat.S_ISREG(st.st_mode) and not st.st_mode & 0o222, 'RAG_OP_READONLY_SOURCE')


def read(path, readonly=True):
    trust(path, readonly)
    need(stat.S_ISREG(path.lstat().st_mode), 'RAG_OP_REGULAR_FILE_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        a, b = os.fstat(stream.fileno()), path.lstat()
        need((a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) and a.st_size <= 4_000_000,
             'RAG_OP_READ_IDENTITY')
        return stream.read(4_000_001)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def fsync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def atomic(path, raw, immutable=False):
    """Fixed caller targets, NO-REPLACE for marker/events, durable writes."""
    trust(path.parent)
    need(not path.is_symlink(), 'RAG_OP_WRITE_SYMLINK')
    if path.exists():
        old = read(path)
        if immutable:
            need(old == raw, 'RAG_OP_IMMUTABLE_COLLISION')
            return
    temp = path.parent / ('.rag810-' + str(uuid4()) + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    if immutable:
        os.link(temp, path)
        temp.unlink()
    else:
        os.replace(temp, path)
    fsync(path.parent)


def profile(pair, role):
    need(role in ROLES and all(re.fullmatch('[A-Za-z0-9._/-]+', pair[s]['path'])
         for s in ('application', 'tooling')), 'RAG_OP_PROFILE_PATH_SHAPE')
    app = Path(pair['application']['path']) / 'enterprise_agent_poc'
    script = Path(pair['tooling']['path']) / 'enterprise_agent_poc/scripts/rag810_test_operator.py'
    value = '[Service]\nWorkingDirectory=' + str(app) + '\n'
    value += 'Environment=PYTHONPATH=' + str(app) + '\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n'
    value += 'Environment=APP_ENV=test\nEnvironment=ENTERPRISE_POC_TEST_RAG_NATIVE_REQUIRED=true\n'
    value += 'Environment=ENTERPRISE_POC_WECHAT_NETWORK_ALLOWED=false\n'
    value += 'ExecStartPre=\nExecStartPre=' + str(PY) + ' -B ' + str(script) + ' pre ' + role + '\n'
    value += 'ExecStartPost=\nExecStartPost=' + str(PY) + ' -B ' + str(script) + ' post ' + role + '\n'
    if role == 'mcp':
        value += 'Environment=ENTERPRISE_POC_SKILL_DISPATCH_CONFIG=' + str(ROOT / 'runtime/dispatch-config.v1.json') + '\n'
    return value.encode()


def validate_authority(policy, approval, acceptance, pair, pair_sha, policy_sha, acceptance_sha):
    need(set(policy) == {'contract', 'environment', 'production_authority', 'pair_sha256',
        'application', 'tooling', 'predecessor', 'operation_id', 'not_before', 'expires_at',
        'code_sha256', 'original_units', 'target_profiles', 'dependency_files', 'runtime_files',
        'original_seeding_approval_sha256', 'root_runtime', 'test_environment_file', 'budget'}, 'RAG_OP_POLICY_SHAPE')
    need(policy['contract'] == VERSION and policy['environment'] == 'test' and
         policy['production_authority'] is False and policy['pair_sha256'] == pair_sha and
         policy['application'] == APPLICATION and policy['predecessor'] == PREDECESSOR and
         policy['tooling'] == {k: pair['tooling'][k] for k in ('source', 'tree')} and
         {k: pair['application'][k] for k in ('source', 'tree')} == APPLICATION,
         'RAG_OP_EXACT_PAIR')
    need(str(UUID(policy['operation_id'])) == policy['operation_id'], 'RAG_OP_OPERATION_ID')
    need(policy['budget'] == {'provider': 0, 'wechat': 0, 'image': 0} and
         all(type(v) is int for v in policy['budget'].values()), 'RAG_OP_ZERO_BUDGET')
    need(set(policy['code_sha256']) == set(CODE) and set(policy['original_units']) == set(ROLES)
         and set(policy['target_profiles']) == set(ROLES), 'RAG_OP_CODE_PROFILE_SET')
    need(approval == {'contract': VERSION, 'policy_sha256': policy_sha,
        'authorization': 'RAG_810_EXACT_TEST_MAINTENANCE_INDEPENDENTLY_APPROVED',
        'production_authority': False, 'native_acceptance_sha256': acceptance_sha}, 'RAG_OP_ROOT_APPROVAL')
    need(acceptance == {'contract': VERSION, 'pair_sha256': pair_sha, 'application': APPLICATION,
        'tooling': policy['tooling'], 'predecessor': PREDECESSOR,
        'evidence_kind': 'ISOLATED_NATIVE_RELEASE_RECOVERY_ACCEPTED',
        'component_tests_are_native_evidence': False}, 'RAG_OP_NATIVE_ACCEPTANCE_REQUIRED')


class NativeBackend:
    def __init__(self):
        need(os.name == 'posix' and os.environ.get('APP_ENV') == 'test' and sys.dont_write_bytecode
             and os.environ.get('PYTHONDONTWRITEBYTECODE') == '1', 'RAG_OP_TEST_ENVIRONMENT')
        # Only the frozen, root-owned bootstrap may interpret the pair.
        raw_pair = json.loads(read(ROOT / 'runtime-pair.v1.json'))
        need({k: raw_pair['application'][k] for k in ('source', 'tree')} == APPLICATION, 'RAG_OP_APPLICATION_ID')
        app = Path(raw_pair['application']['path']) / 'enterprise_agent_poc'
        need(app.parent.parent == HOME / 'releases', 'RAG_OP_FIXED_APPLICATION_ROOT')
        bootstrap = app / 'app/test_runtime_tooling.py'
        need(sha(read(bootstrap)) == BOOT_SHA, 'RAG_OP_FROZEN_BOOTSTRAP')
        self.boot = module('_rag810_verified_bootstrap', bootstrap)
        self.pair, self.pair_sha = self.boot.load_pair()
        self.script_dir = Path(self.pair['tooling']['path']) / 'enterprise_agent_poc/scripts'
        need(Path(__file__).resolve() == self.script_dir / CODE[1], 'RAG_OP_LOADED_TOOLING')
        sys.path.insert(0, str(app))  # validated registered source, never caller PYTHONPATH
        self.guard = self.boot.tooling_module('test_rag_native_successor')
        self.policy, self.policy_sha = self.boot.native_json(ROOT / 'operator-policy.v1.json')
        self.approval, self.approval_sha = self.boot.native_json(ROOT / 'operator-approval.v1.json')
        self.acceptance, self.acceptance_sha = self.boot.native_json(ROOT / 'operator-native-acceptance.v1.json')
        self.binding = sha(canonical({'contract': VERSION, 'policy_sha256': self.policy_sha,
            'approval_sha256': self.approval_sha, 'pair_sha256': self.pair_sha}))
        self.events_dir = ROOT / 'operation-events'  # Root must pre-provision; no issuer here
        self.marker = ROOT / 'business-writes-admitted.v1.json'
        self.infra = {r: self.ctl('show', 'enterprise-agent-test-' + r + '.service', '-p', 'MainPID', '--value')
                      for r in ('postgresql', 'redis')}

    def command(self, args, timeout=110):
        result = subprocess.run([str(a) for a in args], capture_output=True, timeout=timeout)
        need(result.returncode == 0, 'RAG_OP_PROTECTED_COMMAND_FAILED')
        return result.stdout

    def ctl(self, *args):
        return self.command(['/usr/bin/systemctl', *args]).decode().strip()

    @contextmanager
    def lock(self):
        # Existing global inode; never create a replacement lock on ENOENT.
        need(os.geteuid() == 0, 'RAG_OP_ROOT_MAINTENANCE_REQUIRED')
        parent = LOCK.parent.stat()
        need(parent.st_uid == parent.st_gid == 0 and
             (not parent.st_mode & 0o002 or parent.st_mode & stat.S_ISVTX), 'RAG_OP_LOCK_PARENT_TRUST')
        fd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            st = os.fstat(fd)
            need(stat.S_ISREG(st.st_mode) and st.st_uid == st.st_gid == 0 and st.st_nlink == 1
                 and not st.st_mode & 0o022, 'RAG_OP_LOCK_TRUST')
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise Blocked('RAG_OP_RELEASE_SWITCH_LOCKED') from None
            need((LOCK.lstat().st_dev, LOCK.lstat().st_ino) == (st.st_dev, st.st_ino), 'RAG_OP_LOCK_REPLACED')
            self.lock_fd = fd
            self.lock_inode = st.st_ino
            self.lock_device = [os.major(st.st_dev), os.minor(st.st_dev)]
            yield
        finally:
            self.lock_fd = None
            os.close(fd)

    def authorize(self, action, hook=False):
        need(action in ('activate', 'recover', 'preflight', 'pre', 'post'), 'RAG_OP_ACTION')
        need((os.geteuid() in (0, 1000) if hook else os.geteuid() == 0), 'RAG_OP_OPERATOR_UID')
        need('enterprise-agent-test.slice' in Path('/proc/self/cgroup').read_text(), 'RAG_OP_TEST_RESOURCE_SLICE')
        validate_authority(self.policy, self.approval, self.acceptance, self.pair, self.pair_sha,
                           self.policy_sha, self.acceptance_sha)
        if os.geteuid() == 0:
            # Existing service Python/runner are lucky-owned. NEVER execute or
            # import their libraries as Root. Supervisor uses a separately
            # qualified protected tooling env, not a second business Runtime.
            runtime = self.policy['root_runtime']
            need(set(runtime) == {'prefix', 'manifest_sha256'} and
                 Path(runtime['prefix']).parent == ROOT / 'runtime' and
                 Path(sys.prefix) == Path(runtime['prefix']) and sys.flags.isolated,
                 'RAG_OP_PROTECTED_ROOT_INTERPRETER_REQUIRED')
            files, manifest_sha = self.boot.native_json(ROOT / 'root-runtime-files.v1.json')
            need(manifest_sha == runtime['manifest_sha256'] and isinstance(files, dict) and files,
                 'RAG_OP_ROOT_RUNTIME_MANIFEST')
            actual = {}
            for item in Path(runtime['prefix']).rglob('*'):
                trust(item)
                if item.is_file(): actual[item.relative_to(runtime['prefix']).as_posix()] = sha(read(item))
            need(actual == files, 'RAG_OP_ROOT_RUNTIME_FILES')
        # Existing TEST env is DATA, never execute the lucky-owned shell runner
        # as Root. Root-approved exact bytes + typed localhost/disabled checks.
        spec = self.policy['test_environment_file']
        need(set(spec) == {'path', 'sha256', 'kind'} and spec['kind'] == 'EXISTING_TEST_ENV_DATA_ONLY' and
             Path(spec['path']).parent == HOME / 'shared/config', 'RAG_OP_ENV_DATA_SOURCE')
        env_path = Path(spec['path'])
        for item in (env_path.parent, *env_path.parent.parents): trust(item)
        meta = env_path.lstat()
        need(stat.S_ISREG(meta.st_mode) and meta.st_uid in (0, 1000) and
             meta.st_gid in (0, 1000) and not meta.st_mode & 0o022 and meta.st_size <= 131072,
             'RAG_OP_ENV_DATA_TRUST')
        fd = os.open(env_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            need((os.fstat(stream.fileno()).st_dev, os.fstat(stream.fileno()).st_ino) ==
                 (meta.st_dev, meta.st_ino), 'RAG_OP_ENV_DATA_REPLACED')
            raw = stream.read(131073)
        need(len(raw) <= 131072 and sha(raw) == spec['sha256'], 'RAG_OP_ENV_DATA_PIN')
        os.environ.update(parse_test_environment(raw))
        now = datetime.now(timezone.utc)
        dates = []
        for key in ('not_before', 'expires_at'):
            need(isinstance(self.policy[key], str) and self.policy[key].endswith('+00:00'), 'RAG_OP_APPROVAL_TIME')
            dates.append(datetime.fromisoformat(self.policy[key]))
        need(dates[1] - dates[0] <= timedelta(hours=4) and dates[1] > dates[0], 'RAG_OP_MAINTENANCE_WINDOW')
        persistent_hook = hook and self.events() and self.events()[-1]['phase'] in ('COMMITTED', 'RECOVERED_810')
        if not persistent_hook and not dates[0] <= now < dates[1]:
            # Independent recovery-only window; never edit original approval.
            need(action == 'recover', 'RAG_OP_MAINTENANCE_WINDOW')
            window, _ = self.boot.native_json(ROOT / 'operator-recovery-window.v1.json')
            need(set(window) == {'contract', 'binding', 'authorization', 'not_before', 'expires_at'} and
                 window['contract'] == VERSION and window['binding'] == self.binding and
                 window['authorization'] == 'RAG_810_EXACT_TEST_RECOVERY_INDEPENDENTLY_APPROVED',
                 'RAG_OP_RECOVERY_WINDOW_BINDING')
            begin, end = [datetime.fromisoformat(window[k]) for k in ('not_before', 'expires_at')]
            need(begin.tzinfo == end.tzinfo == timezone.utc and begin <= now < end and
                 timedelta(0) < end - begin <= timedelta(hours=4), 'RAG_OP_RECOVERY_WINDOW_TIME')
        for name in CODE:
            need(sha(read(self.script_dir / name)) == self.policy['code_sha256'][name], 'RAG_OP_CODE_PIN')
        # All inherited units/dependencies are root-approved, exact byte inputs.
        for role in ROLES:
            observed = self.ctl('show', UNITS[role], '-p', 'FragmentPath', '-p', 'DropInPaths')
            fields = dict(line.split('=', 1) for line in observed.splitlines())
            paths = {fields['FragmentPath'], *fields['DropInPaths'].split()}
            expected = set(self.policy['original_units'][role])
            need(fields['FragmentPath'] == str(Path('/etc/systemd/system') / UNITS[role]) and
                 paths == expected | ({str(DEST[role])} if DEST[role].exists() else set()),
                 'RAG_OP_UNIT_GRAPH_DRIFT')
            for raw, pin in self.policy['original_units'][role].items():
                path = Path(raw)
                need(path.name.endswith('.conf') or path.name == UNITS[role], 'RAG_OP_UNIT_FILE')
                need(path.is_relative_to(Path('/etc/systemd/system')) or
                     path.is_relative_to(Path('/run/systemd/system.control')), 'RAG_OP_UNIT_ROOT')
                need(sha(read(path, readonly=False)) == pin, 'RAG_OP_ORIGINAL_UNIT_DRIFT')
            need(sha(profile(self.pair, role)) == self.policy['target_profiles'][role], 'RAG_OP_TARGET_PROFILE_PIN')
        required = {str(FORMAL): FORMAL_SHA, str(OWNER): OWNER_SHA}
        need(set(required) <= set(self.policy['dependency_files']), 'RAG_OP_DEPENDENCY_SET')
        for raw, pin in self.policy['dependency_files'].items():
            path = Path(raw)
            need(path in (FORMAL, OWNER) or path.is_relative_to(ROOT / 'runtime'), 'RAG_OP_DEPENDENCY_ROOT')
            need(required.get(raw) in (None, pin) and sha(read(path)) == pin, 'RAG_OP_DEPENDENCY_PIN')
        active = Path('/etc/enterprise-agent-test-tenant-seeding-v1/approval.v1.json')
        need(sha(read(active)) == self.policy['original_seeding_approval_sha256'], 'RAG_OP_HISTORICAL_APPROVAL_CHANGED')
        for name, pin in self.policy['runtime_files'].items():
            need(name in ('dispatch-config.v1.json', 'dispatch-seal.v1.json'), 'RAG_OP_RUNTIME_ARTIFACT')
            need(sha(read(ROOT / 'runtime' / name)) == pin, 'RAG_OP_RUNTIME_ARTIFACT_PIN')
        need(set(self.policy['runtime_files']) == {'dispatch-config.v1.json', 'dispatch-seal.v1.json'},
             'RAG_OP_RUNTIME_ARTIFACT_SET')
        seal, _ = self.boot.native_json(ROOT / 'runtime/dispatch-seal.v1.json')
        config, _ = self.boot.native_json(ROOT / 'runtime/dispatch-config.v1.json')
        need((seal['source_commit'], seal['source_tree']) == (APPLICATION['source'], APPLICATION['tree'])
             and config['dispatch_seal'] == seal, 'RAG_OP_RUNTIME_SOURCE_BINDING')
        for name, pin in seal['files'].items():
            need(not Path(name).is_absolute() and '..' not in Path(name).parts and
                 sha(read(Path(self.pair['application']['path']) / 'enterprise_agent_poc' / name)) == pin,
                 'RAG_OP_DISPATCH_CODE_PIN')
        trust(self.events_dir)

    def events(self):
        result = []
        for path in sorted(self.events_dir.glob('event-*.json')):
            need(path.name == 'event-' + f'{len(result):04}' + '.json', 'RAG_OP_EVENT_FILE_SEQUENCE')
            result.append(json.loads(read(path)))
        validate_journal(result, self.binding)
        return result

    def append(self, event):
        atomic(self.events_dir / ('event-' + f'{event["sequence"]:04}' + '.json'), canonical(event), True)

    def admitted(self):
        if not self.marker.exists(): return False
        need(json.loads(read(self.marker)) == {'contract': VERSION, 'binding': self.binding,
             'business_writes_admitted': True}, 'RAG_OP_ADMISSION_MARKER')
        return True

    def seal_admission(self):
        atomic(self.marker, canonical({'contract': VERSION, 'binding': self.binding,
            'business_writes_admitted': True}), True)

    def inspect_data(self):
        pair, pair_sha, record, plan = self.guard.native_state()
        need(pair == self.pair and pair_sha == self.pair_sha, 'RAG_OP_DATA_PAIR_DRIFT')
        return record, plan

    def runtime_state(self, phase):
        need(self.admitted(), 'RAG_OP_IMMUTABLE_ADMISSION_REQUIRED')
        record, _ = self.boot.native_json(ROOT / 'release-state.v1.json')
        need(phase in ('ACTIVATING', 'COMMITTED'), 'RAG_OP_RUNTIME_PHASE')
        record = dict(record, business_writes_admitted=True, phase=phase)
        # This is operation state, not an Approval/Scope or knowledge baseline.
        atomic(ROOT / 'release-state.v1.json', canonical(record))

    def snapshots(self):
        states = {}
        for role, unit in UNITS.items():
            fields = dict(line.split('=', 1) for line in self.ctl('show', unit, '-p', 'MainPID',
                '-p', 'ActiveState', '-p', 'WorkingDirectory', '-p', 'User', '-p', 'Slice').splitlines())
            need(fields['User'] == 'lucky' and fields['Slice'] == 'enterprise-agent-test.slice',
                 'RAG_OP_SERVICE_OWNER_SLICE')
            pid = int(fields['MainPID'])
            cwd = str((Path('/proc') / str(pid) / 'cwd').resolve()) if pid else fields['WorkingDirectory']
            source = next((v for v, p in (('d8a', PRED), ('810', Path(self.pair['application']['path'])))
                           if cwd == str(p / 'enterprise_agent_poc')), 'UNKNOWN')
            states[role] = {'pid': pid, 'active': fields['ActiveState'], 'source': source}
        return states

    def exact_predecessor(self, live, selected=True):
        if selected: need((HOME / 'current').resolve() == PRED, 'RAG_OP_EXACT_PREDECESSOR_POINTER')
        owner = module('_rag810_sealed_d8a_owner_context', OWNER)
        need(sha(read(OWNER)) == OWNER_SHA, 'RAG_OP_OWNER_CONTEXT_PIN')
        value = owner.identity('application')  # unchanged fixed privilege-drop reader, NOT Root Git
        need(value['source_commit'] == PREDECESSOR['source'] and value['source_tree'] == PREDECESSOR['tree'],
             'RAG_OP_EXACT_PREDECESSOR_IDENTITY')
        owner.final_application_files()  # unchanged full installed byte map
        record, plan = self.inspect_data()
        need(record['business_writes_admitted'] is False and not self.admitted() and
             plan['mode'] == 'EXACT_D8A_RESTORE_REQUIRES_NATIVE_GATES', 'RAG_OP_PREDECESSOR_DATA_DRIFT')
        if live:
            states = self.snapshots(); validate_live(states, 'd8a')
            need(all(s['pid'] > 1 and s['active'] == 'active' for s in states.values()), 'RAG_OP_PREDECESSOR_NOT_ACTIVE')

    def known_state(self):
        current = (HOME / 'current').resolve()
        direction = 'd8a' if current == PRED else '810' if current == Path(self.pair['application']['path']) else 'UNKNOWN'
        need(direction != 'UNKNOWN', 'RAG_OP_UNKNOWN_CURRENT')
        validate_live(self.snapshots(), direction)
        # Only absent/approved NEW drop-ins; no overwriting arbitrary profiles.
        for role, dest in DEST.items():
            need(not dest.exists() or read(dest) == profile(self.pair, role), 'RAG_OP_UNKNOWN_PROFILE')

    def assert_stopped(self):
        need(all(v['pid'] == 0 and v['active'] in ('inactive', 'failed') for v in self.snapshots().values()),
             'RAG_OP_NOT_QUIESCED')

    def _formal(self, name):
        need(sha(read(FORMAL)) == FORMAL_SHA, 'RAG_OP_FORMAL_SWITCH_PIN')
        tree = ast.parse(read(FORMAL).decode())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        need(name in ('switch_pointer', 'units'), 'RAG_OP_FORMAL_PRIMITIVE')
        context = SimpleNamespace(R=HOME, RELEASE=Path(self.pair['application']['path']), OLD_RELEASE=PRED,
                                  need=need, run=lambda args, **kwargs: self.command(args, kwargs.get('timeout', 110)))
        namespace = {'c': context, 'Path': Path, 'os': os}
        # Exact frozen function body, no prepare/migration/web/cleanup branch.
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])),
            str(FORMAL) + '::unchanged-rag810-' + name, 'exec'), namespace)
        return namespace[name]

    def quiesce(self): self._formal('units')('stop', 'worker', 'api', 'mcp')

    def switch(self, direction):
        self.assert_stopped()
        need(direction in ('d8a', '810'), 'RAG_OP_SWITCH_DIRECTION')
        need(direction != 'd8a' or not self.admitted(), 'RAG_OP_DOWNGRADE_AFTER_ADMISSION')
        self._formal('switch_pointer')(PRED if direction == 'd8a' else Path(self.pair['application']['path']))
        fsync(HOME)

    def capture_profiles(self):
        need(all(not p.exists() and not p.is_symlink() for p in DEST.values()), 'RAG_OP_NEW_PROFILE_COLLISION')
        atomic(ROOT / 'original-profiles-snapshot.v1.json', canonical({
            'binding': self.binding, 'original_units': self.policy['original_units'],
            'new_profiles_initially_absent': True}), True)
        self.seal_live_operator()  # only AFTER in-lock final preflight passes

    def seal_live_operator(self):
        need(isinstance(getattr(self, 'lock_fd', None), int), 'RAG_OP_LOCK_REQUIRED')
        atomic(ROOT / 'operator-live.v1.json', canonical({'binding': self.binding,
            'pid': os.getpid(), 'start': Path('/proc/self/stat').read_text().split()[21],
            'inode': self.lock_inode, 'device': self.lock_device}))

    def profiles_check(self, direction):
        for role, path in DEST.items():
            if direction == 'd8a': need(not path.exists() and not path.is_symlink(), 'RAG_OP_PREDECESSOR_PROFILE')
            else: need(read(path) == profile(self.pair, role), 'RAG_OP_TARGET_PROFILE')

    def configure(self, direction):
        self.assert_stopped()
        need(direction in ('d8a', '810'), 'RAG_OP_PROFILE_DIRECTION')
        snapshot = json.loads(read(ROOT / 'original-profiles-snapshot.v1.json'))
        need(snapshot == {'binding': self.binding, 'original_units': self.policy['original_units'],
              'new_profiles_initially_absent': True}, 'RAG_OP_PROFILE_RECOVERY_SNAPSHOT')
        if direction == 'd8a': need(not self.admitted(), 'RAG_OP_DOWNGRADE_AFTER_ADMISSION')
        for role, path in DEST.items():
            if direction == '810': atomic(path, profile(self.pair, role), True)
            elif path.exists():
                need(read(path) == profile(self.pair, role), 'RAG_OP_PROFILE_DELETE_IDENTITY')
                path.unlink(); fsync(path.parent)  # NEW operation-owned drop-in only
        self.ctl('daemon-reload')
        self.profiles_check(direction)

    def start(self, direction):
        if direction == '810': need(self.admitted(), 'RAG_OP_START_BEFORE_ADMISSION')
        else: need(direction == 'd8a' and not self.admitted(), 'RAG_OP_DOWNGRADE_AFTER_ADMISSION')
        self._formal('units')('start', 'mcp', 'api', 'worker')

    def queue(self):
        from app.settings import settings
        from app.task_queue import RedisTaskQueue
        need(settings.task_queue_namespace == 'enterprise-agent', 'RAG_OP_QUEUE_NAMESPACE')
        self.guard.native_state()  # includes exact Test DB/Redis target checks
        return RedisTaskQueue.from_settings(settings)

    def queue_check(self, initial):
        queue = self.queue()
        try:
            need(queue.ping(), 'RAG_OP_REDIS_HEALTH')
            tasks = [queue._client.llen(k) for k in (queue.pending, queue.processing)]
            need(tasks == [0, 0], 'RAG_OP_PROVIDER_TASK_QUEUE_NOT_EMPTY')
            if all(s['pid'] == 0 for s in self.snapshots().values()):
                need(not any(c['cmd'].lower() in ('brpoplpush', 'rpoplpush', 'blpop', 'brpop')
                             for c in queue._client.client_list()), 'RAG_OP_FOREIGN_QUEUE_CONSUMER')
            jobs = [v for k in (queue.knowledge_pending, queue.knowledge_processing)
                    for v in queue._client.lrange(k, 0, 1000)]
            need(len(jobs) <= 1000, 'RAG_OP_QUEUE_RETENTION_BOUND')
            if initial: need(not jobs, 'RAG_OP_PREDECESSOR_KNOWLEDGE_QUEUE_NOT_EMPTY')
            else: self.validate_jobs(jobs)
        finally: queue._client.close()

    def validate_jobs(self, jobs):
        from app.store import POCStore
        from app.settings import settings
        from app.product_store import ProductStore
        product = ProductStore(POCStore(settings.database_url))
        policy, _ = self.boot.native_json(ROOT / 'native-guard-policy.v1.json')
        for job in jobs:
            tenant, file_id = job.split(':', 1)
            need(tenant == policy['tenant_id'], 'RAG_OP_QUEUE_CROSS_TENANT')
            # Existing read-only file accessor; no synthetic receipt cleanup.
            item = product.knowledge_file(tenant, file_id)
            need(item is not None and item['uploaded_by'] in policy['user_ids'], 'RAG_OP_QUEUE_FILE_OWNER')

    def requeue_knowledge(self):
        self.assert_stopped(); self.queue_check(False)
        queue = self.queue()
        try:
            # Same lease recovery primitive as existing tasks, for knowledge.
            # Compare-and-swap BOTH lists and empty provider queues in Lua.
            # An intervening write fails BEFORE any lease item is moved.
            snapshot = [queue._client.lrange(k, 0, 1000) for k in
                        (queue.knowledge_processing, queue.knowledge_pending)]
            need(sum(map(len, snapshot)) <= 1000, 'RAG_OP_QUEUE_RETENTION_BOUND')
            self.validate_jobs([job for values in snapshot for job in values])
            script = """local expected=cjson.decode(ARGV[1]);
if redis.call('LLEN',KEYS[3])~=0 or redis.call('LLEN',KEYS[4])~=0 then
 return redis.error_reply('RAG_OP_PROVIDER_TASK_QUEUE_NOT_EMPTY') end;
for i=1,2 do local actual=redis.call('LRANGE',KEYS[i],0,-1);
 if #actual~=#expected[i] then return redis.error_reply('RAG_OP_QUEUE_CAS_DRIFT') end;
 for j=1,#actual do if actual[j]~=expected[i][j] then
  return redis.error_reply('RAG_OP_QUEUE_CAS_DRIFT') end end end;
local n=#expected[1]; for i=1,n do redis.call('RPOPLPUSH',KEYS[1],KEYS[2]) end; return n"""
            queue._client.eval(script, 4, queue.knowledge_processing, queue.knowledge_pending,
                               queue.pending, queue.processing, json.dumps(snapshot))
            need(queue._client.llen(queue.knowledge_processing) == 0, 'RAG_OP_QUEUE_LEASE_RECOVERY')
        finally: queue._client.close()

    def infra_health(self):
        for role, pid in self.infra.items():
            unit = 'enterprise-agent-test-' + role + '.service'
            need(pid != '0' and self.ctl('show', unit, '-p', 'MainPID', '--value') == pid and
                 self.ctl('show', unit, '-p', 'ActiveState', '--value') == 'active', 'RAG_OP_INFRA_CHANGED')
        # PG connectivity/schema/version checked by the actual read-only Guard.
        self.inspect_data()

    def assert_live_operator(self):
        value = json.loads(read(ROOT / 'operator-live.v1.json'))
        need(set(value) == {'binding', 'pid', 'start', 'inode', 'device'} and value['binding'] == self.binding and
             type(value['pid']) is int and value['pid'] > 1, 'RAG_OP_LIVE_OPERATOR_PROOF')
        proc = Path('/proc') / str(value['pid'])
        need(proc.stat().st_uid == 0 and proc.joinpath('stat').read_text().split()[21] == value['start'],
             'RAG_OP_LIVE_OPERATOR_PID')
        # Never read arbitrary Root argv or env; the lock's kernel owner alone
        # identifies this PID/immutable operation, without secrets in evidence.
        lines = [line.split() for line in Path('/proc/locks').read_text().splitlines()]
        need(any(len(row) >= 6 and row[1:4] == ['FLOCK', 'ADVISORY', 'WRITE'] and
                 row[4] == str(value['pid']) and row[5].split(':')[-1] == str(value['inode'])
                 and [int(x, 16) for x in row[5].split(':')[:2]] == value['device']
                 for row in lines), 'RAG_OP_KERNEL_LOCK_NOT_HELD')

    def health(self, direction):
        deadline = time.monotonic() + 55  # existing bounded readiness, no extension/retry budget
        while True:
            try:
                self.infra_health()
                validate_live(self.snapshots(), direction)
                need(all(s['pid'] > 1 and s['active'] == 'active' for s in self.snapshots().values()), 'RAG_OP_SERVICE_INACTIVE')
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open('http://127.0.0.1:18100/api/health', timeout=3) as response:
                    api = json.loads(response.read(16384))
                need(api['status'] == 'ok' and api['environment'] == 'test', 'RAG_OP_API_HEALTH')
                group = self.ctl('show', UNITS['mcp'], '-p', 'ControlGroup', '--value')
                need(group.startswith('/') and 'enterprise-agent-test.slice' in group and
                     len(Path('/sys/fs/cgroup' + group, 'cgroup.procs').read_text().split()) == 1,
                     'RAG_OP_MCP_SINGLETON')
                async def ping():
                    from mcp import ClientSession
                    from mcp.client.streamable_http import streamablehttp_client
                    async with streamablehttp_client('http://127.0.0.1:18101/mcp') as (r, w, _):
                        async with ClientSession(r, w) as session:
                            await session.initialize(); await session.send_ping()
                asyncio.run(asyncio.wait_for(ping(), 8))
                queue = self.queue()
                try:
                    need(queue.ping() and any(c['cmd'].lower() == 'brpoplpush' for c in queue._client.client_list()),
                         'RAG_OP_WORKER_LOOP')
                finally: queue._client.close()
                if direction == '810': self.guard.guard('api')
                return
            except Exception:
                if time.monotonic() >= deadline: raise Blocked('RAG_OP_BOUNDED_HEALTH_FAILED') from None
                time.sleep(.5)

    def capture_failure(self):
        atomic(ROOT / 'manual-recovery-evidence.v1.json', canonical({'contract': VERSION,
            'binding': self.binding, 'current': str((HOME / 'current').resolve()),
            'service_metadata': self.snapshots(), 'admission_marker': self.admitted(),
            'events': self.events(), 'credentials': 'NOT_CAPTURED', 'business_rows': 'NOT_CAPTURED'}))

    def service_hook(self, action, role):
        self.authorize(action, hook=True)
        group = self.ctl('show', UNITS[role], '-p', 'ControlGroup', '--value')
        need(group and group in Path('/proc/self/cgroup').read_text(), 'RAG_OP_HOOK_SERVICE_CGROUP')
        need(self.admitted(), 'RAG_OP_HOOK_ADMISSION_REQUIRED')
        events = self.events()
        need(events and events[-1]['phase'] in ('ADMITTED', 'STARTED', 'COMMITTED', 'RECOVERING', 'RECOVERED_810'),
             'RAG_OP_HOOK_PHASE')
        if events[-1]['phase'] not in ('COMMITTED', 'RECOVERED_810'):
            self.assert_live_operator()
        self.profiles_check('810')
        self.guard.guard(role)
        if action == 'post':
            # Full health belongs to lock-held Operator after all roles start;
            # post hooks cannot demand that later sequential roles are active.
            state = self.snapshots()[role]
            need(state['pid'] > 1 and state['source'] == '810', 'RAG_OP_HOOK_PROCESS_SOURCE')
        return {'status': 'RAG_810_TEST_HOOK_PASS', 'role': role}
