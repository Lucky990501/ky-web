"""Versioned forward recovery adapter of the existing fixed root operator.

The old PRIMARY operator/seals remain immutable. Before-write checks bind the
exact installed pair, DB and unit files, but do not require failed services to
be active. After recovery, the same compatible Guard and health checks run.
This entry cannot switch Source, restore an old DB, publish or restart PRIMARY.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

sys.path.append(str(Path(__file__).resolve().parents[1]))
import scripts
scripts.__path__ = [str(Path(__file__).resolve().parent), *scripts.__path__]
from scripts import native_parent_contract as parent
from scripts import exact_test_admin_lifecycle as admin
from scripts import wechat_runtime_native_successor as native
from scripts.wechat_runtime_test_lifecycle_guard import need, Blocked

UNITS = {role: 'enterprise-agent-native-isolated-'+role+'.service' for role in ('api', 'mcp', 'worker')}


def systemctl(*args):
    return subprocess.run(['/usr/bin/systemctl', *args], check=True, capture_output=True,
                          text=True, timeout=60).stdout.strip()


def preflight():
    from app.test_tenant_seeding import native_json
    from app.test_runtime_tooling import load_pair, authority_root
    need(os.geteuid() == 0 and authority_root() == parent.ROOT and os.environ.get('APP_ENV') == 'test',
         'RECOVERY_ISOLATED_ROOT_ONLY')
    context = parent.load_context()
    pair, pair_sha = load_pair()
    policy, _ = native_json(parent.ROOT/'startup-policy.v1.json')
    need(set(policy) == {'contract', 'pair_sha256', 'units', 'environment_sha256', 'recovery_code_sha256'}
         and policy['contract'] == 'NATIVE_PROTECTED_FORWARD_RECOVERY_V1'
         and policy['pair_sha256'] == pair_sha and set(policy['units']) == set(UNITS)
         and policy['recovery_code_sha256'] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         'RECOVERY_APPROVED_SOURCE_PAIR')
    for role, unit in UNITS.items():
        unit_path = Path('/etc/systemd/system')/unit
        native.native_file(unit_path, policy['units'][role])
        need(systemctl('show', unit, '-p', 'FragmentPath', '--value') == str(unit_path), 'RECOVERY_UNIT_ORIGIN')
        need(not systemctl('show', unit, '-p', 'DropInPaths', '--value'), 'RECOVERY_UNAPPROVED_DROPIN')
    native.native_file(parent.ROOT/'service.env', policy['environment_sha256'])
    return context, policy


def health(pair):
    for role, unit in UNITS.items():
        need(systemctl('is-active', unit) == 'active', 'RECOVERY_SERVICE_INACTIVE:'+role)
        pid = int(systemctl('show', unit, '-p', 'MainPID', '--value'))
        need(pid > 1 and Path('/proc', str(pid), 'cwd').resolve() ==
             Path(pair['application']['path'])/'enterprise_agent_poc', 'RECOVERY_SERVICE_SOURCE:'+role)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://127.0.0.1:28100/api/health', timeout=5) as response:
        value = json.loads(response.read(16384))
    need(value.get('status') == 'ok' and value.get('environment') == 'test', 'RECOVERY_API_HEALTH')


def recover(action):
    from app.test_runtime_tooling import load_pair
    from app.store import POCStore
    from scripts.prepare_exact_admin_authority import write_json
    context, _ = preflight()
    pair, _ = load_pair()
    if action == 'status':
        result = native.NativeSuccessor().verify()
        health(pair)
        return result
    need(action in {'recover', 'startup'}, 'RECOVERY_ACTION')
    if action == 'recover':
        # Validate in-flight, already-written reservations without interpreting
        # their retained admission as a new execution lease.
        native.NativeSuccessor().verify(recovery=True, for_execution=True)
        for unit in UNITS.values():
            systemctl('stop', unit)
        for unit in UNITS.values():
            need(systemctl('show', unit, '-p', 'MainPID', '--value') == '0'
                and systemctl('show', unit, '-p', 'ActiveState', '--value') in {'inactive', 'failed'},
                'RECOVERY_PROCESS_NOT_QUIESCED')
        scope = admin.load_authority()
        store = POCStore(parent.DSN)
        _, data = parent.snapshot(store, context)
        history = native.audit_history(data, scope)
        tickets = admin.outstanding(history)
        pids = {p['process_id'] for _, p in history if p['action'] == 'operation_started' and p['ticket'] in tickets}
        need(all(not Path('/proc', str(pid)).exists() for pid in pids), 'RECOVERY_EXACT_PROCESS_STILL_EXISTS')
        if tickets:
            proof = dict(contract=admin.CONTRACT, run_id=scope['run_id'], application_source=scope['application_source'],
                application_tree=scope['application_tree'], last_audit_sha256=admin.event_hash(history[-1][0]),
                tickets=sorted(tickets), quiesced_process_ids=sorted(pids), observed_at=datetime.now(timezone.utc).isoformat(),
                independent_approval='ISOLATED_NATIVE_ROOT_QUIESCENCE_ATTESTATION_V1')
            # Replace only this operator's ephemeral proof, never a Receipt,
            # Scope, Approval or historical row pin. Atomic rename + fsync.
            temp = parent.ROOT/('dead-operation-proof.'+str(os.getpid())+'.tmp')
            write_json(temp, proof)
            os.replace(temp, parent.ROOT/'dead-operation-proof.v1.json')
            fd = os.open(parent.ROOT, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        service = admin.ExactTestAdmin(store, environment='test')
        service.revoke(**{k: scope[k] for k in ('principal_id', 'tenant_id', 'run_id')},
                       dead_operation_proof=admin.load_dead_operation_proof if tickets else None)
    result = native.NativeSuccessor().verify()
    need(result['active_test_platform_admin'] == 0, 'RECOVERY_ADMIN_NOT_ZERO')
    for role in ('mcp', 'api', 'worker'):
        systemctl('restart', UNITS[role])
    deadline = time.monotonic()+30
    while True:
        try:
            health(pair); break
        except Exception:
            if time.monotonic() >= deadline: raise Blocked('RECOVERY_HEALTH_NOT_READY') from None
            time.sleep(.25)
    result = native.NativeSuccessor().verify()
    return dict(status='PASS', operation=action, guard=result, primary_changes=0, database_restore=False)


if __name__ == '__main__':
    try:
        need(len(sys.argv) == 2, 'RECOVERY_ONE_ACTION_REQUIRED')
        print(json.dumps(recover(sys.argv[1]), sort_keys=True))
    except Exception as exc:
        print(json.dumps({'status': 'BLOCKED', 'code': str(exc) if isinstance(exc, Blocked)
                          else 'NATIVE_FORWARD_RECOVERY_FAILED'}))
        raise SystemExit(2)
