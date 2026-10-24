"""Versioned ISOLATED_CANDIDATE adapter for the existing Test delivery hook.

No installer/issuer or PRIMARY switch. Installed bootstrap must pin this module
before Root import. One existing sandbox Unit owns the private lifecycle; the
Root caller retains the real release lock, current authority and durable journal.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import runpy
import socket
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
if hasattr(os, 'geteuid') and os.geteuid() == 0:
    if not globals().get('_VERIFIED_TEST_ENTRY'):
        raise SystemExit('ROOT_REQUIRES_VERIFIED_TEST_ENTRY')
    p = SimpleNamespace(**runpy.run_path(str(PROJECT/'scripts/test_delivery_pipeline.py'),
                                        init_globals={'_VERIFIED_TEST_ENTRY': True}))
else:
    from scripts import test_delivery_pipeline as p
from scripts import test_delivery_adoption as adoption

CONTEXT = 'ISOLATED_CANDIDATE_CONTEXT_V1'
ACTION = 'execute_standard_test_isolated_candidate_v1'
INSTALL_ROOT = Path('/opt/enterprise-agent-test-delivery')
GLOBAL_LOCK = Path('/run/lock/enterprise-agent-test-successor-v1.lock')
STEPS = ['preflight', 'backup', 'install', 'activate', 'health', 'qualify', 'export']
CHECKS = tuple(x for x in p.CHECKS if x != 'old_reader_compatibility') + ('same_source_restore',)
REQUEST_LIMIT = 64*1024


def policy_scope_sha256(policy):
    # The decision cannot hash a policy containing its own raw record hash.
    # Bind every policy field except that single back-reference. No path,
    # executable, actor, lifetime or operation-store field is excluded.
    return p.digest({k:v for k,v in policy.items() if k != 'accepted_issuer_record_sha256'})


def isolated_plan(repo, source, tree, archive, manifest, observation, observation_sha256):
    request = p.delivery_plan(repo, source, tree, archive, manifest, observation,
        dict(proof_kind='INSTALLED_IDENTITY_OBSERVATION',
             identity=p.identity(repo, observation['primary_source']), evidence_sha256=observation_sha256))
    request.update(context=CONTEXT, target='ISOLATED_CANDIDATE', checks=list(CHECKS),
        primary_switch_authority=False, primary_backup='NOT_PERMITTED',
        recovery_kind='SAME_CANDIDATE_PRIVATE_DATA_ONLY')
    request['execution_scope'].update(context=CONTEXT, target='ISOLATED_CANDIDATE')
    request['rollback']['reason'] = ('PRIMARY rollback is outside this isolated operation. '
        'Only same-candidate private-data restore is checked; missing original894 artifacts '
        'remain a future PRIMARY switch gate, not this private-instance prerequisite.')
    validate_request(request)
    return request


def validate_request(request):
    p.require(len(p.canonical(request))+1 <= REQUEST_LIMIT, 'ISOLATED_REQUEST_SIZE')
    p.require(request['context'] == CONTEXT and request['target'] == 'ISOLATED_CANDIDATE'
        and request['contract'] == 'STANDARD_TEST_DELIVERY_V1' and request['action'] == 'test_delivery'
        and request['environment'] == 'test' and request['steps'] == STEPS, 'ISOLATED_CONTEXT_REQUIRED')
    p.require(request['production_authority'] is False and request['primary_switch_authority'] is False
        and request['primary_backup'] == 'NOT_PERMITTED'
        and request['calls'] == {'text': 0, 'image': 0, 'wechat': 0}
        and request['recovery_kind'] == 'SAME_CANDIDATE_PRIVATE_DATA_ONLY'
        and request['checks'] == list(CHECKS), 'NO_PRIMARY_OR_PAID_AUTHORITY')
    p.require(str(uuid.UUID(request['operation_id'])) == request['operation_id'], 'OPERATION_MISMATCH')
    p.require(request['limits'] == dict(aggregate_bytes=p.LIMIT, scratch_bytes=p.SCRATCH,
        input_bytes=p.INPUT_LIMIT, receipt_bytes=p.RECEIPT_LIMIT,
        host_reserve_bytes=p.HOST_RESERVE, min_free_fraction=.2), 'EXACT_RESOURCE_LIMITS')


def technical_scope(request, policy, grant, decision, now):
    """Consume a real independently authenticated record; never issue one.

    Caller reads protected files, checks their hashes and current revocation.
    No historical approval string or ordinary sudo session can satisfy this.
    """
    validate_request(request)
    p.delivery_scope(request, policy, grant, now)
    p.require(policy['context'] == grant['context'] == decision['context'] == CONTEXT
        and policy['target'] == grant['target'] == decision['target'] == 'ISOLATED_CANDIDATE', 'CONTEXT_AUTHORITY')
    p.require(decision['kind'] == 'ACTUAL_OWNER_AUTHORIZED_06_ROOT_DECISION_RECORD'
        and decision['technical_action'] == ACTION and type(decision['actual_root_uid']) is int and decision['actual_root_uid'] == 0
        and decision['request_sha256'] == p.digest(request)
        and decision['policy_scope_sha256'] == policy_scope_sha256(policy)
        and decision['host'] == policy['hostname'] and decision['operator'] == policy['operator']
        and isinstance(decision['owner_references'], list) and decision['owner_references']
        and all(isinstance(x, str) and len(x) >= 8 for x in decision['owner_references']), 'ACTUAL_TECHNICAL_DECISION')
    p.require(decision['production_authority'] is False and decision['preview_authority'] is False
        and decision['primary_switch_authority'] is False and type(decision['provider_budget']) is int and decision['provider_budget'] == 0
        and decision['revoked'] is False and type(decision['uses']) is int and decision['uses'] == 1,
        'TECHNICAL_SCOPE_EXPANSION')
    p.require(type(decision['issued_at']) is int and type(decision['expires_at']) is int
        and decision['issued_at'] <= grant['not_before'] <= now < grant['expires_at'] <= decision['expires_at']
        and 0 < decision['expires_at'] - decision['issued_at'] <= 14400, 'TECHNICAL_DECISION_EXPIRED')


@contextmanager
def release_lock():
    """Original installed lock path/inode; never create or replace the lock."""
    import fcntl
    p.require(sys.platform == 'linux', 'LINUX_LOCK_REQUIRED')
    path = GLOBAL_LOCK
    before = path.lstat()
    parent = path.parent.lstat()
    p.require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == parent.st_gid == 0
              and stat.S_IMODE(parent.st_mode) == 0o1777, 'LOCK_PARENT_TRUST')
    p.require(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0
        and stat.S_IMODE(before.st_mode) == 0o644 and before.st_nlink == 1
        and not any(x.startswith('system.posix_acl') for x in os.listxattr(path)), 'GLOBAL_LOCK_TRUST')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    def verify():
        fields = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_nlink')
        opened = os.fstat(fd); named = path.lstat()
        p.require(all(getattr(before, k) == getattr(opened, k) == getattr(named, k) for k in fields),
                  'GLOBAL_LOCK_INODE_CHANGED')
    try:
        verify()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise p.Block('TEST_GLOBAL_RELEASE_LOCK_BUSY') from None
        verify()
        yield verify
    finally:
        os.close(fd)


def bound_receipt(value, request, step):
    p.require(isinstance(value, dict) and value.get('status') == 'PASS'
        and value.get('context') == CONTEXT and value.get('stage') == step
        and value.get('operation_id') == request['operation_id']
        and value.get('request_sha256') == p.digest(request)
        and value.get('source') == request['candidate']['source']
        and value.get('artifact') == request['artifact'], 'ISOLATED_STEP_RECEIPT_BINDING')


def resource_admission(slice_state, available_ram):
    need = 1536*1024**2 + p.HOST_RESERVE
    p.require(slice_state['ActiveState'] == 'active'
        and all(str(slice_state[x]).isdigit() for x in ('MemoryCurrent', 'MemoryHigh', 'MemoryMax')),
        'TEST_SLICE_RESOURCE_IDENTITY')
    current, high, maximum = (int(slice_state[x]) for x in ('MemoryCurrent', 'MemoryHigh', 'MemoryMax'))
    p.require(high <= 3*1024**3 and maximum <= 4*1024**3 and high <= maximum
        and current+need <= high and available_ram >= need, 'TEST_MEMORY_RESERVE')


def runtime_receipts(policy, inventory, closure, material, profile):
    p.require(closure['status'] == 'PASS' and closure['runtime_inventory_sha256'] == p.digest(inventory)
        and closure['operator'] == policy['operator'] and closure['checks'] == {k: 'PASS' for k in
        ('dynamic_loader', 'python_imports', 'pgvector_load', 'redis_start', 'relocation')}, 'RUNTIME_CLOSURE_NOT_PROVEN')
    p.require(material['status'] == 'MATERIALS_VERIFIED' and material['context'] == CONTEXT
        and material['tooling'] == policy['tooling'], 'MATERIALS_NOT_ADOPTED')
    p.require(profile['status'] == 'PASS' and profile['sandbox_profile'] == 'PRIVATE_STAGE2_TMPFS_V2'
        and profile['driver_sha256'] == policy['driver_sha256'] and profile['checks'] == {k: 'PASS' for k in
        ('namespace', 'no_host_socket_access', 'write_containment', 'disk_exhaustion',
         'cgroup_termination', 'zero_egress', 'proc_secret_isolation')}, 'LINUX_PROFILE_NOT_QUALIFIED')
    p.require(policy['runtime_profile_sha256'] == p.digest(profile)
        and closure['sandbox_profile_sha256'] == p.digest(profile)
        and closure['runtime_root'] == policy['runtime_root']
        and closure['python_sha256'] == policy['python_sha256'], 'RUNTIME_PROFILE_CLOSURE_BINDING')


class SandboxBridge:
    """Only seven fixed messages to ONE existing systemd sandbox, never a shell.

    stdout is a bounded protocol, stderr is not a Root evidence sink. The old
    lifecycle keeps child logs in bounded private tmpfs; only safe hashes/status
    cross this pipe. Stop/collect this exact cgroup on all exit paths.
    """
    def __init__(self, command, unit, revalidate):
        self.command = command; self.unit = unit; self.revalidate = revalidate
        self.proc = None; self.events = queue.Queue(maxsize=2); self.reader = None
        self.total = 0; self.deadline = None; self.closed = False
        self.failure_receipt = None; self.binding = None

    def _read(self):
        try:
            while True:
                line = self.proc.stdout.readline(p.RECEIPT_LIMIT//2 + 1)
                if not line:
                    self.events.put_nowait(None); return
                self.total += len(line)
                # Quarter-budget leaves room for journal envelopes, request and
                # the final exported copy within the original1MiB Root receipt.
                if not line.endswith(b'\n') or self.total > p.RECEIPT_LIMIT//4:
                    self.events.put_nowait('OUTPUT_BOUND'); return
                self.events.put_nowait(line)
        except (OSError, queue.Full):
            # Missing result/reader exit is rejected by step(), never success.
            return

    def step(self, step, request, prior):
        p.require(not self.closed, 'ISOLATED_BRIDGE_CLOSED')
        self.revalidate()
        if self.proc is None:
            self.binding = dict(operation_id=request['operation_id'], request_sha256=p.digest(request))
            self.deadline = time.monotonic()+900
            self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
            self.reader = threading.Thread(target=self._read, daemon=True); self.reader.start()
        raw = p.canonical(dict(stage=step, request_sha256=p.digest(request), prior_sha256=p.digest(prior)))+b'\n'
        self.proc.stdin.write(raw); self.proc.stdin.flush()
        while True:
            self.revalidate()
            v = os.statvfs('/')
            p.require(v.f_bavail*v.f_frsize > v.f_blocks*v.f_frsize*.2+p.HOST_RESERVE, 'HOST_DISK_STOP_THRESHOLD')
            p.require(time.monotonic() < self.deadline, 'ISOLATED_UNIT_TIMEOUT')
            try:
                value = self.events.get(timeout=.2); break
            except queue.Empty:
                p.require(self.reader.is_alive(), 'ISOLATED_PROTOCOL_EOF')
        p.require(isinstance(value, bytes), 'ISOLATED_PROTOCOL_EOF_OR_BOUND')
        value = json.loads(value, object_pairs_hook=p.unique)
        if value.get('status') != 'PASS':
            self.failure_receipt = {k: value[k] for k in
                ('status', 'stage', 'failure_code', 'exception_class', 'stages') if k in value}
            code = value.get('failure_code', 'ISOLATED_STAGE_FAILED')
            p.require(isinstance(code, str) and re.fullmatch('[A-Z0-9_:-]{1,100}', code), 'INVALID_FAILURE_RECEIPT')
            raise p.Block('ISOLATED_STAGE_FAILED:'+code)
        bound_receipt(value, request, step)
        return value

    def close(self, *, expect_success=False):
        if self.closed or self.proc is None:
            return
        self.closed = True
        try:
            if self.proc.stdin:
                self.proc.stdin.close()  # EOF permits finally/PG stop before cgroup stop
            try:
                self.proc.wait(timeout=35)
            except subprocess.TimeoutExpired:
                pass
        finally:
            subprocess.run(['/usr/bin/systemctl', 'stop', self.unit], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=40)
            if self.proc.poll() is None:
                self.proc.kill(); self.proc.wait(timeout=10)
            self.reader.join(timeout=5)
            state = subprocess.run(['/usr/bin/systemctl', 'show', self.unit,
                '-pActiveState', '-pMainPID', '-pLoadState'], timeout=10, capture_output=True, text=True)
            fields = dict(line.split('=', 1) for line in state.stdout.splitlines())
            p.require((state.returncode == 0 or fields.get('LoadState') == 'not-found')
                and fields.get('MainPID') == '0' and fields.get('ActiveState') in ('inactive', 'failed'),
                      'ISOLATED_CGROUP_CLEANUP_NOT_PROVEN')
            p.require(not self.reader.is_alive(), 'ISOLATED_PROTOCOL_READER_STILL_RUNNING')
            if expect_success:
                p.require(self.proc.returncode == 0, 'ISOLATED_UNIT_EXIT_FAILED')
                try:
                    final = self.events.get_nowait()
                except queue.Empty:
                    raise p.Block('CLEANUP_RECEIPT_MISSING') from None
                p.require(isinstance(final, bytes), 'CLEANUP_RECEIPT_MISSING')
                final = json.loads(final, object_pairs_hook=p.unique)
                p.require(final == dict(status='CLOSED', context=CONTEXT,
                    **self.binding, private_services_stopped=True), 'CLEANUP_RECEIPT_BINDING')


def dispatch_context(request, callbacks, journal, revalidate, verify_lock):
    """Used by installed entry and offline tests; no alternative authority path."""
    validate_request(request)
    def fresh(req):
        verify_lock(); revalidate(req)
    def append(stage, value):
        verify_lock(); revalidate(request)
        journal(stage, value)
        verify_lock()
    return p.dispatch_delivery(request, callbacks, append, fresh)


def protected_preflight(request, policy, archive, manifest):
    validate_request(request)
    p.require(socket.gethostname() == policy['hostname'], 'LIVE_HOST_MISMATCH')
    p.require(policy['controller_python'] == '/usr/bin/python3.10'
        and p.sha(p.trusted(policy['controller_python'])) == policy['controller_python_sha256']
        and Path(sys.executable).resolve() == Path(policy['controller_python']).resolve(),
        'SYSTEM_CONTROLLER_PYTHON_REQUIRED')
    roots = []
    for name in ('tooling_root', 'runtime_root', 'operations_root'):
        path = p.trusted(policy[name], directory=True)
        p.require(path.is_relative_to(INSTALL_ROOT) and path != INSTALL_ROOT, 'INDEPENDENT_TEST_INSTALL_ROOT')
        roots.append(path)
    p.require(all(a != b and not a.is_relative_to(b) for a in roots for b in roots if a is not b),
              'SEPARATE_TOOLING_RUNTIME_OPERATIONS_ROOTS')
    p.require(Path(policy['runtime_root']) in [Path(x) for x in policy['runtime_roots']], 'RUNTIME_ROOT_NOT_IN_CLOSURE')
    for name in ('tooling_repository', 'source_repository'):
        repository = p.trusted(policy[name], directory=True)
        p.require((repository/'.git').is_dir() and not (repository/'.git/objects/info/alternates').exists()
            and not (repository/'.git/commondir').exists(), 'SELF_CONTAINED_PROTECTED_GIT_REQUIRED')
        adoption.tree_pins(repository/'.git')
    actual = adoption.tree_pins(policy['tooling_root'])
    absolute = {str(Path(policy['tooling_root'])/name): pin for name, pin in actual.items()}
    p.require(absolute == policy['tooling_files'] and p.digest(absolute) == policy['tooling_files_sha256']
        and str(Path(__file__).resolve()) in absolute, 'COMPLETE_TOOLING_PIN_SET')
    p.require(adoption.identity(policy['tooling_repository'], policy['tooling']['source'])['tree']
        == policy['tooling']['tree'], 'TOOLING_GIT_IDENTITY')
    p.trusted(policy['source_repository'], directory=True)
    p.require(p.identity(policy['source_repository'], request['candidate']['source']) == request['candidate'], 'CANDIDATE_GIT_IDENTITY')
    p.require(p.artifact(policy['source_repository'], request['candidate']['source'],
              p.trusted(archive), p.trusted(manifest)) == request['artifact'], 'ARTIFACT_RECHECK_MISMATCH')
    p.require(sum(Path(x).stat().st_size for x in (archive, manifest))
        + len(p.canonical(request))+1 <= p.INPUT_LIMIT, 'COMBINED_INPUT_BUDGET')
    runtime = adoption.runtime_inventory(policy['runtime_roots'], protected=True)
    p.require(p.digest(runtime) == policy['runtime_inventory_sha256'], 'RUNTIME_INVENTORY_MISMATCH')
    for name in ('python', 'qualification_driver'):
        file = p.trusted(policy[name])
        root = Path(policy['runtime_root'] if name == 'python' else policy['tooling_root'])
        p.require(file.is_relative_to(root), 'EXECUTABLE_OUTSIDE_ADOPTED_ROOT')
        expected = policy['python_sha256'] if name == 'python' else policy['driver_sha256']
        p.require(p.sha(file) == expected, 'EXECUTABLE_PIN_MISMATCH')
    p.require(Path(policy['qualification_driver']) == Path(policy['tooling_root'])/'scripts/test_delivery_isolated.py', 'FIXED_ISOLATED_DRIVER')
    for record, pin in (('runtime_closure_receipt', 'runtime_closure_receipt_sha256'),
                        ('material_adoption_receipt', 'material_adoption_receipt_sha256'),
                        ('native_adoption_receipt', 'native_adoption_receipt_sha256')):
        p.require(p.sha(p.trusted(policy[record])) == policy[pin], 'ADOPTION_RECEIPT_PIN')
    runtime_receipts(policy, runtime, p.read(policy['runtime_closure_receipt']),
                     p.read(policy['material_adoption_receipt']), p.read(policy['native_adoption_receipt']))
    import pwd
    account = pwd.getpwnam(policy['operator']['name'])
    p.require(policy['operator'] == {'name': account.pw_name, 'uid': account.pw_uid, 'gid': account.pw_gid}
        and policy['operator_uid'] == account.pw_uid > 0 and policy['operator_gid'] == account.pw_gid > 0,
        'REAL_NONROOT_OPERATOR_REQUIRED')
    p.require(set(policy['protected_service_units']) >= {
        'enterprise-agent-test-api.service', 'enterprise-agent-test-mcp.service',
        'enterprise-agent-test-worker.service'}, 'PROTECTED_SERVICES_REQUIRED')
    for unit in policy['protected_service_units']:
        p.require(re.fullmatch('[A-Za-z0-9_-]+[.]service', unit), 'SERVICE_NAME_INVALID')
        name = subprocess.check_output(['/usr/bin/systemctl', 'show', unit, '-pUser', '--value'], timeout=10).decode().strip() or 'root'
        p.require(account.pw_uid != (int(name) if name.isdigit() else pwd.getpwnam(name).pw_uid),
                  'OPERATOR_PROC_ISOLATION_REQUIRED')
    st = os.statvfs('/')
    p.storage_gate(st.f_bavail*st.f_frsize, st.f_blocks*st.f_frsize)
    slice_state = subprocess.check_output(['/usr/bin/systemctl', 'show', 'enterprise-agent-test.slice',
        '-pMemoryCurrent', '-pMemoryHigh', '-pMemoryMax', '-pActiveState'], timeout=10).decode()
    mem = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    resource_admission(dict(line.split('=', 1) for line in slice_state.splitlines()),
                       int(mem['MemAvailable'].split()[0])*1024)
    p.require(policy['primary_pointer'] == '/opt/enterprise-agent-workbench-test/current', 'ACTUAL_PRIMARY_OBSERVATION_REQUIRED')
    live = p.observe(policy)
    p.require(live == dict(request['observed_predecessor'], path=request['observation']['primary_path']), 'FRESH_PRIMARY_DRIFT')
    return live


def controller_preflight():
    p.require(sys.platform == 'linux' and os.geteuid() == 0, 'ADOPTED_ROOT_ENTRY_REQUIRED')
    p.require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'ISOLATED_PYTHON_REQUIRED')
    p.require(sys.flags.no_site, 'CONTROLLER_NO_SITE_REQUIRED')
    p.require('enterprise-agent-test.slice' in Path('/proc/self/cgroup').read_text(), 'TEST_CONTROLLER_SLICE_REQUIRED')


def execute(request_path, policy_path, grant_path, archive, manifest):
    controller_preflight()
    request = p.read(p.trusted(request_path)); policy = p.read(p.trusted(policy_path))
    def authority(req):
        current = p.read(p.trusted(policy_path)); grant = p.read(p.trusted(grant_path))
        p.require(current == policy, 'POLICY_CHANGED')
        record = p.trusted(grant['issuer_record_path'])
        p.require(p.sha(record) == current['accepted_issuer_record_sha256'] == grant['issuer_record_sha256'], 'ISSUER_RECORD_PIN')
        marker = Path(current['revocation_marker'])
        p.trusted(marker.parent, directory=True)
        p.require(not marker.exists() and not marker.is_symlink(), 'TECHNICAL_AUTHORITY_REVOKED')
        technical_scope(req, current, grant, p.read(record), int(time.time()))
        return grant
    with release_lock() as verify_lock:
        grant = authority(request)
        p.require(grant['expires_at']-time.time() >= 960, 'GRANT_RUNTIME_RESERVE')
        live = protected_preflight(request, policy, archive, manifest)
        p.require(authority(request)['expires_at']-time.time() >= 960, 'GRANT_RUNTIME_RESERVE')
        operations = p.trusted(policy['operations_root'], directory=True)
        op = operations/request['operation_id']
        command = p.sandbox_command(request, policy, op, Path(archive), Path(manifest), isolated_candidate=True)
        def monitor():
            verify_lock(); authority(request)
        bridge = SandboxBridge(command, 'test-delivery-'+request['operation_id']+'.service', monitor)
        append = p.delivery_journal(operations, request)
        claimed = False
        def journal(stage, payload):
            nonlocal claimed
            append(stage, payload)
            if stage == 'claim':
                claimed = True
        def callback(step, req, prior):
            if step == 'preflight':
                p.exclusive(op/'request.json', req); (op/'request.json').chmod(0o444)
            value = bridge.step(step, req, prior)
            p.require(p.observe(policy) == live, 'PRIMARY_CHANGED_DURING_ISOLATED_RUN')
            return value
        try:
            result = dispatch_context(request, callback, journal, authority, verify_lock)
            bridge.close(expect_success=True)
            authority(request); verify_lock()
            p.require(p.observe(policy) == live, 'PRIMARY_CHANGED_DURING_CLEANUP')
            export = dict(status='ISOLATED_CANDIDATE_COMPLETED_PENDING_TEST_ACCEPTANCE', context=CONTEXT,
                request_sha256=p.digest(request), candidate=request['candidate'], artifact=request['artifact'],
                artifact_references={'archive': str(Path(archive)), 'manifest': str(Path(manifest))},
                receipts=result['receipts'], primary_changes=0, production_changes=0, preview_changes=0,
                provider_calls=0, cleanup='EXACT_UNIT_STOPPED', test_native='PENDING_EXTERNAL_ACCEPTANCE')
            p.exclusive(op/'export.json', export)
            p.require(p.read(op/'export.json') == export, 'EXPORT_READBACK')
            return export
        except BaseException as exc:
            try:
                bridge.close()
            finally:
                # May be absent before claim. Never overwrite an earlier claim/failure.
                if claimed and op.is_dir() and not (op/'failure.json').exists():
                    p.exclusive(op/'failure.json', dict(status='FAILED_NO_AUTOMATIC_RETRY',
                        error=str(exc) if isinstance(exc, p.Block) else type(exc).__name__,
                        operation_id=request['operation_id'], request_sha256=p.digest(request),
                        safe_driver_failure=bridge.failure_receipt))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    execute_parser = sub.add_parser('execute-isolated')
    for name in ('request', 'policy', 'grant', 'archive', 'manifest'):
        execute_parser.add_argument('--'+name, type=Path, required=True)
    plan_parser = sub.add_parser('plan-isolated')
    for name in ('repo', 'archive', 'manifest', 'observation', 'output'):
        plan_parser.add_argument('--'+name, type=Path, required=True)
    for name in ('source', 'tree', 'observation-sha256'):
        plan_parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    if args.mode == 'execute-isolated':
        result = execute(args.request, args.policy, args.grant, args.archive, args.manifest)
    else:
        p.require(p.sha(args.observation) == args.observation_sha256, 'OBSERVATION_HASH')
        result = isolated_plan(args.repo, args.source, args.tree, args.archive, args.manifest,
                               p.read(args.observation), args.observation_sha256)
        p.exclusive(args.output, result)
        result = dict(status='UNSIGNED_ISOLATED_PLAN_NOT_AUTHORITY', request_sha256=p.digest(result))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
