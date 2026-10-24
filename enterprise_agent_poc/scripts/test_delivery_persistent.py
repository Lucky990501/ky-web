"""Fixed new-Test callbacks for the existing delivery executor. No issuer.

Only the pinned bootstrap may load this module as Root. Offline tests inject
systemctl/health doubles; they never operate persistent units.
"""
import argparse
import os
import re
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
if hasattr(os, 'geteuid') and os.geteuid() == 0:
    if not globals().get('_VERIFIED_TEST_ENTRY'):
        raise SystemExit('ROOT_REQUIRES_VERIFIED_TEST_ENTRY')
    p = type('Pipeline', (), runpy.run_path(str(PROJECT/'scripts/test_delivery_pipeline.py'),
             init_globals={'_VERIFIED_TEST_ENTRY': True}))
else:
    from scripts import test_delivery_pipeline as p

BASE = Path('/opt/enterprise-agent-test-delivery')
CONTEXT = 'NEW_TEST_PERSISTENT_CALLBACKS_V1'
SOURCE801 = '801fd27a92f32ecc87e9aec1a8c253a8942d1e92'
TREE801 = 'c2daa36435c384b4d4836b8e2b3ed65fe3e05f09'
ARCHIVE801 = '9619fbc84512af93de9735527ccb44be12b00bd135d4da3a5675347b13a7601e'
MANIFEST801 = '27dcafea650b528ffae020c8a4f8f4f6f00946460be43fb6458fe50de66ab5df'
ROOT = Path('/private/tmp/ky-web-stage2-readiness.standard-b1-v1')
UNITS = {r: 'enterprise-agent-standard-test-'+r+'.service' for r in ('api', 'mcp', 'worker')}


def validate(request, policy, grant, decision, now):
    p.delivery_scope(request, policy, grant, now)
    p.require(request['context'] == policy['context'] == grant['context'] == decision['context'] == CONTEXT,
              'PERSISTENT_CONTEXT')
    p.require(policy['hostname'] == 'instance-mif0a40l' and policy['units'] == UNITS
              and policy['service_user'] == 'eaw-test', 'NEW_TEST_ONLY')
    p.require(request['mode'] in ('activate', 'recover') and grant['mode'] == request['mode']
              and request['primary_switch_authority'] is True
              and request['production_authority'] is False, 'EXACT_ACTION')
    p.require(decision['kind'] == 'ACTUAL_OWNER_AUTHORIZED_06_ROOT_DECISION_RECORD'
        and decision['technical_action'] == 'execute_new_test_persistent_v1'
        and decision['request_sha256'] == p.digest(request)
        and decision['policy_scope_sha256'] == p.digest({k:v for k,v in policy.items()
                                                if k != 'accepted_issuer_record_sha256'})
        and decision['actual_root_uid'] == 0 and decision['operator'] == policy['operator']
        and decision['host'] == policy['hostname'] and decision['owner_references']
        and decision['primary_switch_authority'] is True
        and decision['production_authority'] is False and decision['preview_authority'] is False
        and decision['provider_budget'] == 0 and decision['revoked'] is False
        and type(decision['uses']) is int and decision['uses'] == 1
        and type(decision['actual_root_uid']) is int
        and 0 < decision['expires_at']-decision['issued_at'] <= 14400
        and decision['issued_at'] <= grant['not_before'] <= now < grant['expires_at'] <= decision['expires_at'],
        'PERSISTENT_TECHNICAL_AUTHORITY')
    target = request['candidate']
    if request['mode'] == 'recover':
        p.require(target['source'] == SOURCE801 and target['tree'] == TREE801
            and request['artifact']['archive_sha256'] == ARCHIVE801
            and request['artifact']['manifest_sha256'] == MANIFEST801, 'EXACT_801_ONLY')
    else:
        p.require(target['git_parents'] == [SOURCE801]
            and request['observed_predecessor']['source'] == SOURCE801, 'EXACT_PREDECESSOR_REQUIRED')
    p.require(policy['operations_root'] == str(BASE/'operations')
              and policy['repository'] == str(BASE/'repository'), 'FIXED_CONTROLLER_PATHS')


def dropin(role, source):
    p.require(role in UNITS and len(source) == 40 and all(c in '0123456789abcdef' for c in source), 'UNIT_OR_SOURCE')
    project = ROOT/'releases'/source/'enterprise_agent_poc'
    return ('[Service]\nWorkingDirectory='+str(project)+'\n'
        'BindReadOnlyPaths='+str(BASE/'releases'/source/'enterprise_agent_poc')+':'+str(project)+'\n'
        'ExecStart=\nExecStart='+str(BASE/'runtime/python/bin/python')+' -I -B '+
        str(BASE/'tooling/enterprise_agent_poc/scripts/test_delivery_fixture.py')+
        ' --role '+role+' --project '+str(project)+' --config '+str(ROOT/'redis-e2e.manifest.json')+'\n')


def error_code(exc):
    return str(exc) if isinstance(exc,p.Block) and re.fullmatch('[A-Z0-9_:-]{1,120}',str(exc)) else type(exc).__name__


def systemctl(action, unit=None):
    p.require(action in ('stop', 'start', 'daemon-reload', 'show') and
              (unit in UNITS.values() if action != 'daemon-reload' else unit is None), 'FIXED_SERVICE_COMMAND')
    args = ['/usr/bin/systemctl', action]
    if unit:
        args.append(unit)
    if action == 'show':
        args += ['-pUser', '-pMainPID', '-pActiveState', '-pFragmentPath', '-pDropInPaths']
    result = subprocess.run(args, env={'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'},
                            capture_output=True, timeout=60)
    p.require(result.returncode == 0, 'SERVICE_COMMAND_FAILED')
    if action == 'show':
        return dict(line.split('=', 1) for line in result.stdout.decode().splitlines() if '=' in line)


class Callbacks:
    def __init__(self, request, policy, authority, journal, command=systemctl):
        self.request, self.policy = request, policy
        self.authority, self.journal, self.command = authority, journal, command

    def audit(self, phase, action, result='PENDING'):
        # Fixed fields only: no env, argv, stderr, request body or credentials.
        self.journal(phase+':'+action, dict(operation_id=self.request['operation_id'],
            actor=self.policy['operator'], authorization_sha256=self.policy['accepted_issuer_record_sha256'],
            source=self.request['observed_predecessor'], target=self.request['candidate'],
            executor=self.policy['tooling'], at=int(time.time()), action=action, result=result))

    def effect(self, action, callback):
        self.authority(self.request)
        self.audit('intent', action)  # durable before any effect; I/O failure prevents effect
        try:
            value = callback()
        except Exception as exc:
            self.audit('result', action, 'FAILED_'+error_code(exc))
            raise
        self.authority(self.request)
        self.audit('result', action, 'PASS')
        return value

    def verify_live(self, expected, inactive=False, after=False):
        p.require(set(expected) == set(UNITS), 'EXACT_LIVE_SET')
        for role, unit in UNITS.items():
            row = self.command('show', unit)
            p.require(row['User'] == 'eaw-test', 'SERVICE_USER_DRIFT')
            loaded = [row['FragmentPath'], *row.get('DropInPaths', '').split()]
            for name in loaded:
                managed = '/etc/systemd/system/'+unit+'.d/90-standard-test-release.conf'
                if after and name == managed:
                    p.require(p.trusted(name).read_text() == dropin(role, self.request['candidate']['source']), 'MANAGED_UNIT_DRIFT')
                else:
                    p.require(name in self.policy['service_files'], 'UNPINNED_UNIT_CONFIGURATION')
            if inactive and row['ActiveState'] in ('inactive', 'failed'):
                continue
            p.require(row['ActiveState'] == 'active' and row['MainPID'].isdigit()
                      and int(row['MainPID']) > 0, 'SERVICE_NOT_ACTIVE')
            p.require(os.readlink('/proc/'+row['MainPID']+'/cwd') == expected[role], 'CURRENT_SOURCE_DRIFT')
            if after:
                import pwd
                p.require(os.stat('/proc/'+row['MainPID']).st_uid == pwd.getpwnam('eaw-test').pw_uid, 'RUNNING_UID_DRIFT')
                command = Path('/proc/'+row['MainPID']+'/cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
                expected_command = dropin(role,self.request['candidate']['source']).split('ExecStart=')[-1].strip().split()
                p.require(command == expected_command, 'RUNNING_COMMAND_DRIFT')

    def verify_current_bytes(self):
        # Read-only observation of namespace-mounted payload, not Root imports.
        import tarfile, hashlib
        archive = p.trusted(self.policy['current_archive'])
        manifest = p.trusted(self.policy['current_manifest'])
        prior = self.request['observed_predecessor']
        p.require(p.identity(self.policy['repository'], prior['source']) == prior
            and p.artifact(self.policy['repository'], prior['source'], archive, manifest) ==
                self.request['current_artifact'], 'CURRENT_ARTIFACT_IDENTITY')
        for role, unit in UNITS.items():
            row = self.command('show', unit)
            if row['ActiveState'] != 'active':
                p.require(self.request['mode'] == 'recover', 'CURRENT_NOT_ACTIVE')
                continue
            cwd = self.request['current_cwds'][role]
            # A failed multi-unit switch may still have801 processes. Their
            # exact old bytes must be checked against original801, not33fc.
            a = archive
            if cwd in (str(ROOT/'candidate/enterprise_agent_poc'),
                       str(ROOT/'releases'/SOURCE801/'enterprise_agent_poc')) and prior['source'] != SOURCE801:
                a = p.trusted(self.policy['recovery_archive'])
                p.require(p.sha(a) == ARCHIVE801, 'ORIGINAL_801_DRIFT')
            root = Path('/proc')/row['MainPID']/'root'/cwd.lstrip('/')
            with tarfile.open(a) as tf:
                for member in tf:
                    if member.isfile():
                        relative = Path(member.name).relative_to('enterprise_agent_poc')
                        p.require(p.sha(root/relative) == hashlib.sha256(tf.extractfile(member).read()).hexdigest(),
                                  'CURRENT_INSTALLED_BYTES_DRIFT')

    def switch(self):
        source = self.request['candidate']['source']
        for role in ('worker', 'api', 'mcp'):
            self.effect('stop'+role, lambda role=role: self.command('stop', UNITS[role]))
        for role, unit in UNITS.items():
            def write(role=role, unit=unit):
                parent = p.trusted(Path('/etc/systemd/system')/(unit+'.d'), directory=True)
                temp = parent/(self.request['operation_id']+'.tmp')
                # Only installer-created drop-in directories; never edit vendor units.
                fd = os.open(temp, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o644)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(dropin(role, source).encode()); stream.flush(); os.fsync(stream.fileno())
                os.replace(temp, parent/'90-standard-test-release.conf')
                directory = os.open(parent, os.O_RDONLY|os.O_DIRECTORY)
                try: os.fsync(directory)
                finally: os.close(directory)
            self.effect('bind'+role, write)
        self.effect('reload', lambda: self.command('daemon-reload'))
        for role in ('mcp', 'api', 'worker'):
            self.effect('start'+role, lambda role=role: self.command('start', UNITS[role]))

    def health(self):
        target = str(ROOT/'releases'/self.request['candidate']['source']/'enterprise_agent_poc')
        self.verify_live({r: target for r in UNITS}, after=True)
        from urllib.request import urlopen
        import json
        deadline = time.monotonic()+60
        while True:
            self.authority(self.request)
            try:
                with urlopen('http://127.0.0.1:18100/api/health', timeout=2) as response:
                    value = json.load(response)
                p.require(value['status'] == 'ok' and value['environment'] == 'test', 'HEALTH_CONTRACT')
                with socket.create_connection(('127.0.0.1',18101), timeout=2): pass
                return
            except (OSError, TimeoutError):
                p.require(time.monotonic() < deadline, 'HEALTH_TIMEOUT')
                time.sleep(.2)

    def run(self):
        # No implicit recovery on error/expiry. Recovery is a new authorized
        # operation referring to the preserved failed operation and current CWDs.
        req, policy = self.request, self.policy
        allowed = {str(ROOT/'candidate/enterprise_agent_poc'),
                   str(ROOT/'releases'/req['observed_predecessor']['source']/'enterprise_agent_poc')}
        if req['mode'] == 'recover':
            allowed.add(str(ROOT/'releases'/SOURCE801/'enterprise_agent_poc'))
        p.require(all(cwd in allowed for cwd in req['current_cwds'].values()), 'CURRENT_CWD_BINDING')
        if req['mode'] == 'recover':
            import uuid
            failed_id = req['failed_operation_id']
            p.require(str(uuid.UUID(failed_id)) == failed_id and failed_id != req['operation_id'], 'RECOVERY_OPERATION')
            failed = Path(policy['operations_root'])/failed_id
            original = p.read(p.trusted(failed/'request.json'))
            p.require(p.read(p.trusted(failed/'failure.json'))['request_sha256'] == p.digest(original)
                and original['candidate'] == req['observed_predecessor']
                and original['mode'] == 'activate', 'RECOVERY_FAILURE_BINDING')
        self.verify_live(req['current_cwds'], inactive=req['mode'] == 'recover')
        for name, pin in policy['service_files'].items():
            p.require(name.startswith('/etc/systemd/system/enterprise-agent-standard-test-')
                and p.sha(p.trusted(name)) == pin, 'UNIT_FILE_DRIFT')
        repo = p.trusted(policy['repository'], directory=True)
        self.verify_current_bytes()
        ident = p.identity(repo, req['candidate']['source'])
        p.require(ident == req['candidate'], 'CANDIDATE_IDENTITY')
        archive = p.trusted(policy['archive']); manifest = p.trusted(policy['manifest'])
        p.require(p.artifact(repo, ident['source'], archive, manifest) == req['artifact'], 'ARTIFACT_DRIFT')
        # No migration or DB restore is performed by these callbacks.
        p.require(p.schema_identity(repo, SOURCE801) == p.schema_identity(repo, ident['source']), 'MIGRATION_NOT_AUTHORIZED')
        proof = p.read(p.trusted(policy['data_compatibility_receipt']))
        p.require(p.sha(policy['data_compatibility_receipt']) == policy['data_compatibility_sha256']
            and proof['request_sha256'] == p.digest(req) and proof['target'] == ident
            and proof['status'] == 'PASS' and proof['backup_restore_verified'] is True
            and proof['current_data_compatible'] is True, 'DATA_RECOVERY_PROOF_REQUIRED')
        def stage():
            # Reuse existing checked extraction, never execute candidate code as Root.
            module = runpy.run_path(str(PROJECT/'scripts/test_delivery_native.py'))
            destination = BASE/'releases'/ident['source']
            p.trusted(destination.parent, directory=True)
            if destination.exists():
                # Prior staging/UNKNOWN is not silently assumed complete.
                p.require(p.read(p.trusted(destination/'installed.json')) ==
                          {'identity': ident, 'artifact': req['artifact']}, 'INSTALL_IDENTITY')
            else:
                module['safe_extract'](archive, manifest, ident, req['artifact'], destination)
                for file in destination.rglob('*'):
                    file.chmod(0o555 if file.is_dir() or file.stat().st_mode & 0o111 else 0o444)
                p.exclusive(destination/'installed.json', {'identity': ident, 'artifact': req['artifact']})
                destination.chmod(0o555)
            # Re-read every installed artifact member, including on idempotent staging.
            import tarfile, hashlib
            with tarfile.open(archive) as tf:
                expected = {m.name for m in tf if m.isfile()}
                actual = {f.relative_to(destination).as_posix() for f in destination.rglob('*') if f.is_file()}
                p.require(actual == expected | {'installed.json'}, 'INSTALLED_FILE_SET')
            with tarfile.open(archive) as tf:
                for member in tf:
                    if member.isfile():
                        p.require(p.sha(p.trusted(destination/member.name)) ==
                            hashlib.sha256(tf.extractfile(member).read()).hexdigest(), 'INSTALLED_BYTES_DRIFT')
        self.effect('stage', stage)
        self.switch()
        self.effect('health', self.health)
        return {'status':'CALLBACKS_COMPLETED_PENDING_FRESH_ACCEPTANCE', 'operation_id':req['operation_id'],
                'target':ident, 'mode':req['mode'], 'test_native':'NOT_QUALIFIED_BY_CALLBACKS', 'provider_calls':0}


def execute(request_path, policy_path, grant_path):
    context = type('Context', (), runpy.run_path(str(PROJECT/'scripts/test_delivery_context.py'),
                    init_globals={'_VERIFIED_TEST_ENTRY': True}))
    p.require(sys.platform == 'linux' and os.geteuid() == 0 and sys.flags.isolated
              and sys.flags.no_site and sys.dont_write_bytecode, 'PROTECTED_CONTROLLER_REQUIRED')
    request = p.read(p.trusted(request_path)); policy = p.read(p.trusted(policy_path))
    p.require(socket.gethostname() == policy['hostname'], 'HOST_IDENTITY')
    verify_installation(policy)
    def authority(req):
        current = p.read(p.trusted(policy_path)); grant = p.read(p.trusted(grant_path))
        p.require(current == policy, 'POLICY_DRIFT')
        record = p.trusted(grant['issuer_record_path'])
        p.require(p.sha(record) == policy['accepted_issuer_record_sha256'], 'ISSUER_PIN')
        marker = Path(policy['revocation_marker']); p.trusted(marker.parent, directory=True)
        p.require(not marker.exists() and not marker.is_symlink(), 'REVOKED')
        validate(req, policy, grant, p.read(record), int(time.time()))
    with context.release_lock() as verify_lock:
        def check(req): verify_lock(); authority(req)
        check(request)
        append = p.delivery_journal(policy['operations_root'], request)
        append('claim', {'request_sha256':p.digest(request)})
        p.exclusive(Path(policy['operations_root'])/request['operation_id']/'request.json', request)
        try:
            result = Callbacks(request, policy, check, append).run()
            p.exclusive(Path(policy['operations_root'])/request['operation_id']/'completion.json', result)
            return result
        except Exception as exc:
            p.exclusive(Path(policy['operations_root'])/request['operation_id']/'failure.json',
                {'status':'UNKNOWN_REQUIRES_AUTHORIZED_RECOVERY', 'reason':error_code(exc),
                 'operation_id':request['operation_id'], 'request_sha256':p.digest(request)})
            raise


def verify_installation(policy):
    """Read-only finalizer for the EXISTING authorized material installer.

    No files/permissions/decisions are created here. Repeated calls recheck the
    same bytes; they neither grant execution nor consume a release operation.
    """
    p.require(policy['context'] == CONTEXT and policy['environment'] == 'test'
              and policy['hostname'] == 'instance-mif0a40l' and policy['units'] == UNITS,
              'INSTALL_CONTEXT')
    p.require(p.identity(p.trusted(policy['repository'], directory=True), policy['tooling']['source']) ==
              policy['tooling'], 'INSTALLED_TOOLING_IDENTITY')
    root = p.trusted(BASE/'tooling/enterprise_agent_poc', directory=True)
    actual = {}
    for file in root.rglob('*'):
        p.require(not file.is_symlink() and file.suffix != '.pyc' and '__pycache__' not in file.parts,
                  'INSTALL_UNPINNED_CODE')
        p.trusted(file, directory=file.is_dir())
        if file.is_file(): actual[str(file)] = p.sha(file)
    p.require(actual == policy['tooling_files'] and str(root/'scripts/test_delivery_persistent.py') in actual
              and str(root/'scripts/test_delivery_verified_entry.py') in actual, 'INSTALL_COMPLETE_PIN_SET')
    from scripts import test_delivery_adoption as adoption
    runtime = adoption.runtime_inventory(policy['runtime_roots'], protected=True)
    closure = p.read(p.trusted(policy['runtime_closure_receipt']))
    p.require(p.digest(closure) == policy['runtime_profile_sha256']
        and closure['runtime_inventory_sha256'] == p.digest(runtime)
        and closure['status'] == 'PASS' and closure['checks'] == {k:'PASS' for k in
            ('dynamic_loader','python_imports','pgvector_load','redis_start','relocation')}, 'PERSISTENT_RUNTIME_CLOSURE')
    for path in (BASE/'operations', BASE/'releases', *(Path('/etc/systemd/system')/(u+'.d') for u in UNITS.values())):
        p.trusted(path, directory=True)
    p.require(p.sha(p.trusted(policy['recovery_archive'])) == ARCHIVE801
              and p.sha(p.trusted(policy['recovery_manifest'])) == MANIFEST801, 'RECOVERY_INSTALL_PINS')
    return {'status':'INSTALL_BYTES_VERIFIED_NOT_EXECUTION_AUTHORITY', 'tooling':policy['tooling']}


if __name__ == '__main__':
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['execute-persistent'])
    for key in ('request','policy','grant'): parser.add_argument('--'+key, required=True)
    args = parser.parse_args()
    try: print(json.dumps(execute(args.request,args.policy,args.grant)))
    except Exception as exc:
        print(json.dumps({'status':'BLOCKED', 'reason':str(exc) if isinstance(exc,p.Block) else type(exc).__name__}))
        raise SystemExit(2)
