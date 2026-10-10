"""Versioned forward recovery adapter of the existing fixed root operator.

The old PRIMARY operator/seals remain immutable. Before-write checks bind the
exact installed pair, DB and unit files, but do not require failed services to
be active. After recovery, the same compatible Guard and health checks run.
Ordinary recovery cannot switch Source, restore an old DB or publish. The
separate seeding-* actions only select the approved d8a/28e pair through the
pinned formal switch, behind an additional Native release acceptance gate.
PRIMARY requires an exact forward-recovery binding; old seals are not edited.
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
import asyncio

sys.path.append(str(Path(__file__).resolve().parents[1]))
import scripts
scripts.__path__ = [str(Path(__file__).resolve().parent), *scripts.__path__]
from scripts import native_parent_contract as parent
from scripts import exact_test_admin_lifecycle as admin
from scripts import wechat_runtime_native_successor as native
from scripts import runtime_recovery_binding as binding
from scripts.wechat_runtime_test_lifecycle_guard import need, Blocked

UNITS = {role: 'enterprise-agent-native-isolated-'+role+'.service' for role in ('api', 'mcp', 'worker')}


def systemctl(*args):
    return subprocess.run(['/usr/bin/systemctl', *args], check=True, capture_output=True,
                          text=True, timeout=60).stdout.strip()


def preflight(*, service_hook=False):
    from app.test_tenant_seeding import native_json
    from app.test_runtime_tooling import load_pair, authority_root
    need((os.geteuid() in (0, 1000) if service_hook else os.geteuid() == 0)
        and os.environ.get('APP_ENV') == 'test', 'RECOVERY_TEST_OPERATOR_REQUIRED')
    root = authority_root()
    pair, pair_sha = load_pair()
    if root == binding.PRIMARY or (root/'forward-recovery-policy.v1.json').exists():
        p, policy = binding.load(pair, pair_sha, root, systemctl, verify_environment=not service_hook)
        if root == binding.PRIMARY:
            from scripts.seeding_release_binding import assert_selected
            assert_selected(pair)
        return p, policy
    # Immutable earlier isolated fixtures retain their original V1 contract.
    # PRIMARY can never use this fallback, even when its V2 approval is absent.
    need(root == parent.ROOT and not service_hook, 'RECOVERY_FORMAL_BINDING_REQUIRED')
    parent.load_context()
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
    return binding.profile(root), policy


def health(pair, p, *, role=None):
    roles = (role,) if role else binding.ROLES
    for current in roles:
        unit = p['units'][current]
        need(systemctl('show', unit, '-p', 'ActiveState', '--value') in ({'active','activating'} if role else {'active'}),
             'RECOVERY_SERVICE_INACTIVE:'+current)
        pid = int(systemctl('show', unit, '-p', 'MainPID', '--value'))
        need(pid > 1 and Path('/proc', str(pid), 'cwd').resolve() ==
             Path(pair['application']['path'])/'enterprise_agent_poc', 'RECOVERY_SERVICE_SOURCE:'+current)
    if role not in (None, 'api'):
        if role == 'mcp':
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
            async def check_mcp():
                async with streamablehttp_client(f'http://127.0.0.1:{p["mcp"]}/mcp') as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize(); await session.send_ping()
            async def bounded():
                await asyncio.wait_for(check_mcp(), timeout=8)
            asyncio.run(bounded())
        else:
            from redis import Redis
            client = Redis.from_url(os.environ['REDIS_URL'], decode_responses=True)
            try:
                need(client.ping() and any(c['cmd'].lower() in ('brpoplpush','rpoplpush')
                    for c in client.client_list()), 'RECOVERY_WORKER_QUEUE_LOOP')
            finally: client.close()
        return
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f'http://127.0.0.1:{p["api"]}/api/health', timeout=5) as response:
        value = json.loads(response.read(16384))
    need(value.get('status') == 'ok' and value.get('environment') == 'test', 'RECOVERY_API_HEALTH')


def protected_state(store, service, scope):
    """Read existing ledger and Config, not a new persisted witness or permit."""
    from scripts.wechat_runtime_test_lifecycle_guard import digest
    with store.connection() as conn:
        need(store.is_postgres, 'RECOVERY_POSTGRES_REQUIRED')
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        service._database(conn)
        history = service._history(conn, scope)
        rows = conn.execute('SELECT to_jsonb(t) AS row FROM enterprise_configs t ORDER BY tenant_id').fetchall()
        config_sha = digest([r['row'] for r in rows])
    return history, config_sha


def recover(action, role=None):
    from app.test_runtime_tooling import load_pair
    from app.store import POCStore
    from scripts.prepare_exact_admin_authority import write_json
    if action in ('pre', 'post'):
        need(role in binding.ROLES, 'RECOVERY_SERVICE_ROLE')
        p, _ = preflight(service_hook=True)
        pair, _ = load_pair()
        result = native.NativeSuccessor().verify()
        if action == 'post':
            deadline = time.monotonic()+40
            while True:
                try: health(pair, p, role=role); break
                except Exception:
                    if time.monotonic() >= deadline: raise Blocked('RECOVERY_ROLE_HEALTH_NOT_READY') from None
                    time.sleep(.25)
        return dict(status='PASS', phase=action, role=role, guard=result)
    need(role is None and action in {'preflight','status','recover','startup'}, 'RECOVERY_ACTION')
    p, _ = preflight()
    pair, _ = load_pair()
    if action == 'status':
        result = native.NativeSuccessor().verify()
        health(pair, p)
        return result
    if action == 'preflight':
        return native.NativeSuccessor().verify(recovery=True, for_execution=True)
    scope = admin.load_authority()
    store = POCStore(os.environ['ENTERPRISE_POC_DATABASE_URL'])
    service = admin.ExactTestAdmin(store, environment='test')
    _, config_before = protected_state(store, service, scope)
    if action == 'recover':
        # Validate in-flight, already-written reservations without interpreting
        # their retained admission as a new execution lease.
        native.NativeSuccessor().verify(recovery=True, for_execution=True)
        for unit in p['units'].values():
            systemctl('stop', unit)
        for unit in p['units'].values():
            need(systemctl('show', unit, '-p', 'MainPID', '--value') == '0'
                and systemctl('show', unit, '-p', 'ActiveState', '--value') in {'inactive', 'failed'},
                'RECOVERY_PROCESS_NOT_QUIESCED')
        # Revalidate after quiescence, before the existing protected transaction.
        native.NativeSuccessor().verify(recovery=True, for_execution=True)
        history, _ = protected_state(store, service, scope)
        tickets = admin.outstanding(history)
        pids = {p['process_id'] for _, p in history if p['action'] == 'operation_started' and p['ticket'] in tickets}
        need(all(not Path('/proc', str(pid)).exists() for pid in pids), 'RECOVERY_EXACT_PROCESS_STILL_EXISTS')
        if tickets:
            proof = dict(contract=admin.CONTRACT, run_id=scope['run_id'], application_source=scope['application_source'],
                application_tree=scope['application_tree'], last_audit_sha256=admin.event_hash(history[-1][0]),
                tickets=sorted(tickets), quiesced_process_ids=sorted(pids), observed_at=datetime.now(timezone.utc).isoformat(),
                independent_approval=('EXACT_API_PROCESS_QUIESCENCE_ATTESTED_BY_06' if p['primary']
                                      else 'ISOLATED_NATIVE_ROOT_QUIESCENCE_ATTESTATION_V1'))
            # Replace only this operator's ephemeral proof, never a Receipt,
            # Scope, Approval or historical row pin. Atomic rename + fsync.
            temp = p['root']/('dead-operation-proof.'+str(os.getpid())+'.tmp')
            write_json(temp, proof)
            os.replace(temp, p['root']/'dead-operation-proof.v1.json')
            fd = os.open(p['root'], os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        service.revoke(**{k: scope[k] for k in ('principal_id', 'tenant_id', 'run_id')},
                       dead_operation_proof=admin.load_dead_operation_proof if tickets else None)
    result = native.NativeSuccessor().verify()
    need(result['active_test_platform_admin'] == 0, 'RECOVERY_ADMIN_NOT_ZERO')
    # Units/env must still be the approved exact pair after any prior failure.
    preflight()
    for role in ('mcp', 'api', 'worker'):
        systemctl('restart', p['units'][role])
    deadline = time.monotonic()+30
    while True:
        try:
            health(pair, p); break
        except Exception:
            if time.monotonic() >= deadline: raise Blocked('RECOVERY_HEALTH_NOT_READY') from None
            time.sleep(.25)
    result = native.NativeSuccessor().verify()
    _, config_after = protected_state(store, service, scope)
    need(config_after == config_before, 'RECOVERY_CONFIG_CHANGED')
    return dict(status='PASS', operation=action, guard=result, realm=p['realm'],
                config_unchanged=True, database_restore=False, source_switch=False)


if __name__ == '__main__':
    try:
        need(len(sys.argv) in (2, 3), 'RECOVERY_ONE_ACTION_REQUIRED')
        if sys.argv[1].startswith('seeding-'):
            need(len(sys.argv) == 2, 'SELECTOR_ONE_ACTION_REQUIRED')
            from scripts.seeding_release_binding import dispatch
            result = dispatch(sys.argv[1].removeprefix('seeding-'))
        else:
            result = recover(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None)
        print(json.dumps(result, sort_keys=True))
    except Exception as exc:
        print(json.dumps({'status': 'BLOCKED', 'code': str(exc) if isinstance(exc, Blocked)
                          else 'NATIVE_FORWARD_RECOVERY_FAILED'}))
        raise SystemExit(2)
