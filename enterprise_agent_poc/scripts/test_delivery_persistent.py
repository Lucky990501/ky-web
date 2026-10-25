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
REUSE = 'EXISTING_RUNTIME_APPLICATION_UPGRADE_V1'
FULL_RUNTIME = 'COMPLETE_RUNTIME_ADOPTION_V1'
EXECUTOR790 = {'source':'79065f64e9f60965f916d209a826a754f690bab2',
              'tree':'db133561e053918e7d41a2e039122e849c4fbb56'}
SOURCE33 = '33fc8d4d3482c0a854008a8d7a272b9e3b0537b8'
TREE33 = 'fd54a444664e967845d77db8848607ca6e9bb6d8'
RUNTIME = BASE/'runtime'
TIMEZONE = RUNTIME/'postgresql-16.6/share/timezone'
ROOT = Path('/private/tmp/ky-web-stage2-readiness.standard-b1-v1')
UNITS = {r: 'enterprise-agent-standard-test-'+r+'.service' for r in ('api', 'mcp', 'worker')}
EFFECTS = ('stage', 'stopworker', 'stopapi', 'stopmcp', 'bindapi', 'bindmcp',
           'bindworker', 'reload', 'startmcp', 'startapi', 'startworker', 'health')


def runtime_contract(policy):
    value = policy.get('runtime_contract', FULL_RUNTIME)
    p.require(value in (FULL_RUNTIME, REUSE), 'RUNTIME_CONTRACT_VERSION')
    p.require((value == REUSE) == ('runtime_reuse_evidence' in policy), 'RUNTIME_CONTRACT_MIXED')
    if value == REUSE:
        p.require('runtime_closure_receipt' not in policy, 'RUNTIME_CONTRACT_MIXED')
    return value


def reuse_scope(policy, request=None):
    p.require(policy['context'] == CONTEXT and policy['environment'] == 'test'
        and policy['hostname'] == 'instance-mif0a40l' and policy['units'] == UNITS
        and socket.gethostname() == policy['hostname']
        and policy['service_user'] == 'eaw-test', 'RUNTIME_REUSE_CONTEXT')
    # Bind this successor to the reviewed executor, not a newly invented issuer.
    p.require(policy['tooling']['git_parents'] == [EXECUTOR790['source']], 'RUNTIME_REUSE_EXECUTOR_PARENT')
    if request is not None:
        activate = request['mode'] == 'activate'
        expected = (SOURCE33, TREE33) if activate else (SOURCE801, TREE801)
        predecessor = SOURCE801 if activate else SOURCE33
        p.require((request['candidate']['source'],request['candidate']['tree']) == expected
            and request['observed_predecessor']['source'] == predecessor,
            'RUNTIME_REUSE_EXACT_PAIR')
        if activate:
            p.require(request['candidate']['git_parents'] == [SOURCE801], 'RUNTIME_REUSE_EXACT_PAIR')


def reuse_evidence(policy):
    reuse_scope(policy)
    record = p.read(p.trusted(policy['runtime_reuse_evidence']))
    now = int(time.time())
    p.require(p.digest(record) == policy['runtime_profile_sha256']
        and record['contract'] == REUSE
        and record['status'] == 'OBSERVED_EXISTING_RUNTIME_NOT_FULL_QUALIFICATION'
        and record['environment'] == 'test' and record['hostname'] == policy['hostname']
        and record['runtime_root'] == str(RUNTIME) and record['runtime_roots'] == policy['runtime_roots']
        and str(RUNTIME) in record['runtime_roots']
        and record['executor_baseline'] == EXECUTOR790 and record['tooling'] == policy['tooling']
        and record['predecessor'] == {'source':SOURCE801,'tree':TREE801}
        and record['target'] == {'source':SOURCE33,'tree':TREE33,'git_parents':[SOURCE801]}
        and record['operator'] == policy['operator'] and record['units'] == UNITS
        and record['maintenance_exclusion'] == 'NO_CONCURRENT_RUNTIME_OR_UNIT_MAINTENANCE'
        and record['runtime_mutation_authority'] is False
        and type(record['issued_at']) is int and type(record['expires_at']) is int
        and record['issued_at'] <= now < record['expires_at']
        and 0 < record['expires_at']-record['issued_at'] <= 14400,
        'RUNTIME_REUSE_EVIDENCE')
    p.require(record['mountinfo_sha256'] == p.sha('/proc/self/mountinfo'), 'RUNTIME_MOUNT_DRIFT')
    return record


def observe_reused_runtime(policy, record, command=None):
    """Read actual live executable mappings; no imports/probes as privileged app.

    Root-approved evidence must enumerate the runtime/stdlib roots AND external
    executable dependencies. Active processes are observations, not qualification.
    PG/Redis must remain active. API/MCP/Worker may be inactive during recovery.
    """
    import pwd
    import hashlib
    command = command or systemctl
    from scripts import test_delivery_adoption as adoption
    units = {**UNITS, **{r:'enterprise-agent-standard-test-'+r+'.service' for r in ('postgresql','redis')}}
    p.require(set(record['executables']) == set(units) and record['dependency_files'], 'RUNTIME_DEPENDENCY_SET')
    launchers = {str(BASE/'repository/enterprise_agent_poc/scripts/test_delivery_fixture.py'),
                 str(BASE/'tooling/enterprise_agent_poc/scripts/test_delivery_fixture.py')}
    p.require(set(record['launcher_files']) == launchers
        and all(name in record['dependency_files'] and record['dependency_files'][name] == pin
                for name,pin in record['launcher_files'].items())
        and set(record['persistent_processes']) == {'postgresql','redis'}, 'RUNTIME_LAUNCH_IDENTITY')
    for name, pin in record['dependency_files'].items():
        p.require(p.sha(adoption.trust(name)) == pin, 'RUNTIME_DEPENDENCY_DRIFT')
    for role, unit in units.items():
        row = command('show', unit)
        p.require(row['User'] == 'eaw-test', 'RUNTIME_SERVICE_USER')
        for name in [row['FragmentPath'], *row.get('DropInPaths','').split()]:
            managed = '/etc/systemd/system/'+unit+'.d/90-standard-test-release.conf'
            if role in UNITS and name == managed:
                text = p.trusted(name).read_text()
                p.require(text in (dropin(role,SOURCE801),dropin(role,SOURCE33)), 'RUNTIME_MANAGED_UNIT')
            else:
                p.require(name in policy['service_files']
                    and p.sha(p.trusted(name)) == policy['service_files'][name], 'RUNTIME_UNIT_DRIFT')
        if row['ActiveState'] in ('failed','inactive') and role in UNITS:
            continue
        p.require(row['ActiveState'] == 'active' and row['MainPID'].isdigit()
                  and int(row['MainPID']) > 0, 'RUNTIME_SERVICE_NOT_ACTIVE')
        proc = Path('/proc')/row['MainPID']
        def started(): return (proc/'stat').read_text().rsplit(')',1)[1].split()[19]
        before = started()
        if role in ('postgresql','redis'):
            p.require(record['persistent_processes'][role] ==
                {'pid':row['MainPID'], 'start_time':before,
                 'argv_sha256':hashlib.sha256((proc/'cmdline').read_bytes()).hexdigest()},
                'PERSISTENT_RUNTIME_PROCESS_DRIFT')
        p.require(proc.stat().st_uid == pwd.getpwnam('eaw-test').pw_uid, 'RUNTIME_PROCESS_UID')
        exe = os.readlink(proc/'exe')
        p.require(exe == record['executables'][role] and Path(exe).is_relative_to(RUNTIME), 'RUNTIME_EXECUTABLE')
        actual_exe, named_exe = (proc/'exe').stat(), adoption.trust(exe).stat()
        p.require((actual_exe.st_dev,actual_exe.st_ino) == (named_exe.st_dev,named_exe.st_ino),
                  'RUNTIME_EXECUTABLE_INODE')
        loaded = {exe}
        for line in (proc/'maps').read_text().splitlines():
            fields = line.split(None,5)
            if len(fields) == 6 and 'x' in fields[1] and fields[5].startswith('/'):
                loaded.add(fields[5])
                mapped = adoption.trust(fields[5]).stat()
                p.require((os.major(mapped.st_dev),os.minor(mapped.st_dev),mapped.st_ino) ==
                          (*[int(part,16) for part in fields[3].split(':')],int(fields[4])),
                          'RUNTIME_MAPPING_INODE')
        p.require(all(name in record['dependency_files'] for name in loaded), 'RUNTIME_UNPINNED_MAPPING')
        for name in loaded:
            p.require(p.sha(adoption.trust(name)) == record['dependency_files'][name], 'RUNTIME_LOADED_DRIFT')
        p.require(started() == before and command('show',unit) == row, 'RUNTIME_PROCESS_DRIFT')


def verify_reused_runtime(policy, *, inventory=True):
    record = reuse_evidence(policy)
    if inventory:
        from scripts import test_delivery_adoption as adoption
        observed = adoption.runtime_inventory(policy['runtime_roots'], protected=True,
                                               timezone_hardlinks=str(TIMEZONE))
        p.require(p.digest(observed) == record['runtime_inventory_sha256'], 'EXISTING_RUNTIME_DRIFT')
    observe_reused_runtime(policy, record)
    return record


def recovery_evidence(request, policy):
    """Read an immutable, independently grant-pinned snapshot; never repair history.

    Root ownership alone is not the evidence binding: the NEW recovery request
    pins every original record byte, and therefore its independent grant does too.
    The caller holds the existing global lock throughout this read and execution.
    """
    import uuid
    opid = request['failed_operation_id']
    p.require(str(uuid.UUID(opid)) == opid and opid != request['operation_id'], 'RECOVERY_OPERATION')
    directory = p.trusted(Path(policy['operations_root'])/opid, directory=True)
    pins = request['recovery_evidence']
    p.require(isinstance(pins, dict) and pins, 'RECOVERY_EVIDENCE_REQUIRED')
    actual = {f.name: p.sha(p.trusted(f)) for f in directory.iterdir()}
    p.require(actual == pins, 'RECOVERY_EVIDENCE_DRIFT')
    p.require('completion.json' not in actual, 'RECOVERY_TERMINAL_PRESENT')
    original = p.read(directory/'request.json')
    p.require(original['operation_id'] == opid and original['context'] == CONTEXT
        and original['environment'] == 'test' and original['action'] == 'test_delivery'
        and original['calls'] == {'text':0,'image':0,'wechat':0}
        and original['primary_switch_authority'] is True
        and original['production_authority'] is False, 'RECOVERY_ORIGINAL_SCOPE')
    if original['mode'] == 'activate':
        p.require(original['candidate'] == request['observed_predecessor']
            and original['candidate']['git_parents'] == [SOURCE801]
            and original['observed_predecessor']['source'] == SOURCE801
            and original['observed_predecessor']['tree'] == TREE801
            and original['artifact'] == request['current_artifact']
            and original['current_artifact']['archive_sha256'] == ARCHIVE801
            and original['current_artifact']['manifest_sha256'] == MANIFEST801,
            'RECOVERY_FAILURE_BINDING')
    else:
        # A further independently authorized recovery may reference the last
        # interrupted recovery. It is NEVER a replay of that operation/claim.
        p.require(original['mode'] == 'recover' and original['candidate'] == request['candidate']
            and original['artifact'] == request['artifact']
            and original['observed_predecessor'] == request['observed_predecessor']
            and original['current_artifact'] == request['current_artifact']
            and original['recovery_evidence'], 'RECOVERY_FAILURE_BINDING')
    # A later attempt is the only permissible next link. Even a failed or
    # pending sibling must be inspected/referenced, never silently bypassed.
    for other in directory.parent.iterdir():
        if other.name in (opid, request['operation_id']):
            continue
        p.trusted(other, directory=True)
        saved = other/'request.json'
        if saved.exists() or saved.is_symlink():
            value = p.read(p.trusted(saved))
            p.require(value.get('failed_operation_id') != opid, 'RECOVERY_ALREADY_REFERENCED')
    records = ['claim']
    for action in EFFECTS:
        records += ['intent:'+action, 'result:'+action]
    seen = {'request.json'}
    previous = None
    identity = None
    tail = None
    stopped = False
    has_intent = False
    for stage in records:
        name = stage.replace(':', '-')+'.json'
        if name not in actual:
            stopped = True
            continue
        p.require(not stopped, 'RECOVERY_AUDIT_GAP')
        row = p.read(directory/name)
        p.require(set(row) == {'operation_id','request_sha256','stage','prior_sha256','payload'}
            and row['operation_id'] == opid and row['request_sha256'] == p.digest(original)
            and row['stage'] == stage and row['prior_sha256'] == previous, 'RECOVERY_AUDIT_CHAIN')
        payload = row['payload']
        if stage == 'claim':
            p.require(payload == {'request_sha256':p.digest(original)}, 'RECOVERY_CLAIM')
        else:
            phase, action = stage.split(':')
            p.require(set(payload) == {'operation_id','actor','authorization_sha256','source','target',
                'executor','at','action','result'} and payload['operation_id'] == opid
                and payload['source'] == original['observed_predecessor']
                and payload['target'] == original['candidate'] and payload['action'] == action
                and type(payload['at']) is int and payload['at'] > 0, 'RECOVERY_INTENT_BINDING')
            current = {k:payload[k] for k in ('actor','authorization_sha256','executor')}
            p.require(type(current['actor']['uid']) is int and current['actor']['uid'] > 0
                and type(current['actor']['gid']) is int and current['actor']['gid'] > 0
                and re.fullmatch('[a-f0-9]{64}', current['authorization_sha256'])
                and all(re.fullmatch('[a-f0-9]{40}', current['executor'][k]) for k in ('source','tree')),
                'RECOVERY_AUDIT_IDENTITY')
            if identity is None: identity = current
            p.require(current == identity, 'RECOVERY_AUDIT_IDENTITY')
            if phase == 'intent':
                p.require(payload['result'] == 'PENDING', 'RECOVERY_INTENT_STATE')
                has_intent = True
            else:
                p.require(payload['result'] == 'PASS' or
                    re.fullmatch('FAILED_[A-Za-z0-9_:-]{1,120}',payload['result']), 'RECOVERY_RESULT_STATE')
                if payload['result'] != 'PASS': stopped = True
                p.require(not (action == 'health' and payload['result'] == 'PASS'),
                          'RECOVERY_SUCCESS_OR_AMBIGUOUS_TERMINAL')
        seen.add(name)
        previous, tail = p.digest(row), stage
    p.require('claim.json' in seen, 'RECOVERY_CLAIM_REQUIRED')
    if 'failure.json' in actual:
        failure = p.read(directory/'failure.json')
        p.require(failure['request_sha256'] == p.digest(original)
            and failure['operation_id'] == opid
            and failure['status'] == 'UNKNOWN_REQUIRES_AUTHORIZED_RECOVERY', 'RECOVERY_FAILURE_BINDING')
        seen.add('failure.json')
        kind = 'TRUSTED_FAILURE_RECORD'
    else:
        p.require(has_intent, 'RECOVERY_INTENT_REQUIRED')
        kind = 'TRUSTED_UNFINISHED_INTENT_WITHOUT_FAILURE'
    if 'recovery-observation.json' in actual:
        observation = p.read(directory/'recovery-observation.json')
        p.require(original['mode'] == 'recover'
            and observation['request_sha256'] == p.digest(original)
            and observation['observed'] == original['recovery_observation']
            and observation['provenance']['original_operation_id'] == original['failed_operation_id']
            and observation['provenance']['evidence_sha256'] == p.digest(original['recovery_evidence']),
            'RECOVERY_OBSERVATION_BINDING')
        seen.add('recovery-observation.json')
    p.require(seen == set(actual), 'RECOVERY_CONFLICTING_RECORDS')
    return {'kind':kind, 'original_operation_id':opid, 'original_mode':original['mode'],
            'original_status':'UNKNOWN', 'audit_head':previous, 'last_stage':tail,
            'evidence_sha256':p.digest(pins)}


def recovery_lifecycle(original, pins, directory, observed):
    """An approved snapshot cannot authorize an impossible audit/runtime pair."""
    started = {a for a in EFFECTS if 'intent-'+a+'.json' in pins}
    passed = {a for a in EFFECTS if 'result-'+a+'.json' in pins and
              p.read(directory/('result-'+a+'.json'))['payload']['result'] == 'PASS'}
    source = original['candidate']['source']
    target_cwd = str(ROOT/'releases'/source/'enterprise_agent_poc')
    for role in UNITS:
        row = observed['services'][role]
        if original['mode'] == 'activate':
            before = {'state':'active','cwd':original['current_cwds'][role]}
            old_config = {SOURCE801,'ORIGINAL_801_UNIT'}
        else:
            before = original['recovery_observation']['services'][role]
            old_config = {original['recovery_observation']['managed_sources'][role]}
        stop, start, bind = 'stop'+role, 'start'+role, 'bind'+role
        if stop not in started:
            p.require(row['state'] == before['state'] and row['cwd'] == before['cwd'],
                      'RECOVERY_UNEXPLAINED_SERVICE_STATE')
        elif start not in started:
            p.require(row['state'] != 'active' or
                      (stop not in passed and row['cwd'] == before['cwd']), 'RECOVERY_STOP_STATE_CONFLICT')
        elif row['state'] == 'active':
            p.require(row['cwd'] == target_cwd, 'RECOVERY_START_STATE_CONFLICT')
        allowed_config = {source} if bind in passed else old_config | ({source} if bind in started else set())
        p.require(observed['managed_sources'][role] in allowed_config, 'RECOVERY_BIND_STATE_CONFLICT')


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
    if runtime_contract(policy) == REUSE:
        reuse_scope(policy, request)


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
    readonly_units = tuple('enterprise-agent-standard-test-'+r+'.service' for r in ('postgresql','redis'))
    p.require(action in ('stop', 'start', 'daemon-reload', 'show') and
              (unit in (*UNITS.values(), *readonly_units) if action == 'show' else
               unit in UNITS.values() if action != 'daemon-reload' else unit is None), 'FIXED_SERVICE_COMMAND')
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

    def recovery_observation(self):
        """Fresh read-only state, independently bound by the recovery grant.

        Neither PID disappearance nor an audit tail proves what systemd did.
        All live processes, on-disk overrides and both possible release payloads
        are inspected before any recovery effect. No arbitrary third version.
        """
        import pwd
        import tarfile
        import hashlib
        req, policy = self.request, self.policy
        source = req['observed_predecessor']['source']
        rows, overrides, releases = {}, {}, {}
        original_cwd = str(ROOT/'candidate/enterprise_agent_poc')
        old_cwd = str(ROOT/'releases'/SOURCE801/'enterprise_agent_poc')
        new_cwd = str(ROOT/'releases'/source/'enterprise_agent_poc')
        for role, unit in UNITS.items():
            row = self.command('show', unit)
            p.require(row['User'] == 'eaw-test' and
                all(name in policy['service_files'] for name in
                    [row['FragmentPath'], *row.get('DropInPaths','').split()]), 'RECOVERY_LOADED_UNIT_DRIFT')
            p.require(row['ActiveState'] in ('active','inactive','failed'), 'RECOVERY_TRANSITIONING_SERVICE')
            pid = int(row['MainPID'])
            if row['ActiveState'] != 'active':
                p.require(pid == 0, 'RECOVERY_INACTIVE_PID')
                cwd = None
            else:
                p.require(pid > 0 and os.stat('/proc/'+str(pid)).st_uid == pwd.getpwnam('eaw-test').pw_uid,
                          'RUNNING_UID_DRIFT')
                cwd = os.readlink('/proc/'+str(pid)+'/cwd')
                p.require(cwd in (original_cwd,old_cwd,new_cwd) and cwd == req['current_cwds'][role],
                          'CURRENT_SOURCE_DRIFT')
                argv = Path('/proc/'+str(pid)+'/cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
                expected = dropin(role,source).split('ExecStart=')[-1].strip().split()
                expected[expected.index('--project')+1] = cwd
                p.require(argv == expected, 'RUNNING_COMMAND_DRIFT')
                p.require(p.sha(Path('/proc/'+str(pid)+'/exe')) ==
                          p.sha(BASE/'runtime/python/bin/python'), 'RUNNING_EXE_DRIFT')
            rows[role] = {'state':row['ActiveState'], 'pid':pid, 'cwd':cwd}
            managed = Path('/etc/systemd/system')/(unit+'.d')/'90-standard-test-release.conf'
            if managed.exists() or managed.is_symlink():
                text = p.trusted(managed).read_text()
                matches = [s for s in (SOURCE801,source) if text == dropin(role,s)]
                p.require(len(matches) == 1, 'RECOVERY_MANAGED_CONFIGURATION_DRIFT')
                overrides[role] = matches[0]
            else:
                overrides[role] = 'ORIGINAL_801_UNIT'
        for version, archive_name in ((SOURCE801,'recovery_archive'),(source,'current_archive')):
            destination = BASE/'releases'/version
            if not destination.exists() and not destination.is_symlink():
                releases[version] = 'ABSENT'
                continue
            p.trusted(destination, directory=True)
            archive = p.trusted(policy[archive_name])
            with tarfile.open(archive) as tf:
                expected = {m.name for m in tf if m.isfile()}
            actual = {f.relative_to(destination).as_posix() for f in destination.rglob('*') if f.is_file()}
            p.require(actual == expected | {'installed.json'}, 'RECOVERY_RELEASE_FILE_SET')
            for f in destination.rglob('*'): p.trusted(f, directory=f.is_dir())
            with tarfile.open(archive) as tf:
                for member in tf:
                    if member.isfile():
                        p.require(p.sha(destination/member.name) ==
                            hashlib.sha256(tf.extractfile(member).read()).hexdigest(), 'RECOVERY_RELEASE_BYTES')
            receipt = p.read(p.trusted(destination/'installed.json'))
            expected_identity = req['candidate'] if version == SOURCE801 else req['observed_predecessor']
            expected_artifact = req['artifact'] if version == SOURCE801 else req['current_artifact']
            p.require(receipt == {'identity':expected_identity,'artifact':expected_artifact}, 'RECOVERY_RELEASE_RECEIPT')
            releases[version] = p.digest(receipt)
        all_old = all(r['state'] == 'active' and r['cwd'] in (original_cwd,old_cwd) for r in rows.values())
        old_config = all(s in (SOURCE801,'ORIGINAL_801_UNIT') for s in overrides.values())
        if all_old and old_config:
            self.health_transport()  # unhealthy801 is NOT a no-op success
            state = 'ALREADY_HEALTHY_801_NO_SERVICE_EFFECT'
        elif all(r['state'] == 'active' and r['cwd'] == new_cwd for r in rows.values()):
            state = 'TARGET_RUNNING_UNKNOWN_NOT_SUCCESS'
        elif any(r['state'] != 'active' for r in rows.values()):
            state = 'PARTIALLY_OR_FULLY_STOPPED_UNKNOWN'
        else:
            state = 'MIXED_RUNTIME_OR_CONFIGURATION_UNKNOWN'
        return {'classification':state, 'services':rows, 'managed_sources':overrides, 'releases':releases,
                'service_files':{name:p.sha(p.trusted(name)) for name in policy['service_files']}}

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
        self.health_transport()
        if runtime_contract(self.policy) == REUSE:
            verify_reused_runtime(self.policy)

    def health_transport(self):
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
        self.authority(req)
        evidence = recovery_evidence(req, policy) if req['mode'] == 'recover' else None
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
        if evidence:
            p.require(p.sha(p.trusted(policy['recovery_archive'])) == ARCHIVE801
                and p.sha(p.trusted(policy['recovery_manifest'])) == MANIFEST801, 'ORIGINAL_801_DRIFT')
            observed = self.recovery_observation()
            p.require(observed == req['recovery_observation'], 'RECOVERY_OBSERVATION_DRIFT')
            original_directory = Path(policy['operations_root'])/req['failed_operation_id']
            recovery_lifecycle(p.read(p.trusted(original_directory/'request.json')),
                               req['recovery_evidence'], original_directory, observed)
            self.authority(req)
            # This is a NEW observation in the NEW operation, not a fabricated
            # historical failure or a relabeling of UNKNOWN as failure.
            p.exclusive(Path(policy['operations_root'])/req['operation_id']/'recovery-observation.json',
                        {'request_sha256':p.digest(req), 'provenance':evidence, 'observed':observed})
            if observed['classification'] == 'ALREADY_HEALTHY_801_NO_SERVICE_EFFECT':
                return {'status':'ALREADY_HEALTHY_801_NO_SERVICE_EFFECT', 'operation_id':req['operation_id'],
                        'target':ident, 'mode':'recover', 'recovery':evidence, 'provider_calls':0}
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
        if evidence:
            # Staging may add the801 release, but must not alter any live unit
            # or process. Recheck immediately before the first service effect.
            p.require(recovery_evidence(req,policy) == evidence, 'RECOVERY_EVIDENCE_DRIFT')
            self.verify_live(req['current_cwds'], inactive=True)
            self.verify_current_bytes()
            fresh = self.recovery_observation()
            p.require({k:v for k,v in fresh.items() if k != 'releases'} ==
                      {k:v for k,v in observed.items() if k != 'releases'}, 'RECOVERY_PRESTOP_DRIFT')
        self.switch()
        self.effect('health', self.health)
        return {'status':'CALLBACKS_COMPLETED_PENDING_FRESH_ACCEPTANCE', 'operation_id':req['operation_id'],
                'target':ident, 'mode':req['mode'], 'recovery':evidence,
                'test_native':'NOT_QUALIFIED_BY_CALLBACKS', 'provider_calls':0}


def execute(request_path, policy_path, grant_path):
    context = type('Context', (), runpy.run_path(str(PROJECT/'scripts/test_delivery_context.py'),
                    init_globals={'_VERIFIED_TEST_ENTRY': True}))
    p.require(sys.platform == 'linux' and os.geteuid() == 0 and sys.flags.isolated
              and sys.flags.no_site and sys.dont_write_bytecode, 'PROTECTED_CONTROLLER_REQUIRED')
    request = p.read(p.trusted(request_path)); policy = p.read(p.trusted(policy_path))
    p.require(socket.gethostname() == policy['hostname'], 'HOST_IDENTITY')
    def authority(req):
        current = p.read(p.trusted(policy_path)); grant = p.read(p.trusted(grant_path))
        p.require(current == policy, 'POLICY_DRIFT')
        record = p.trusted(grant['issuer_record_path'])
        p.require(p.sha(record) == policy['accepted_issuer_record_sha256'], 'ISSUER_PIN')
        marker = Path(policy['revocation_marker']); p.trusted(marker.parent, directory=True)
        p.require(not marker.exists() and not marker.is_symlink(), 'REVOKED')
        validate(req, policy, grant, p.read(record), int(time.time()))
        if runtime_contract(policy) == REUSE:
            # Fresh expiry/dependency checks through stop/start and recovery too.
            verify_reused_runtime(policy, inventory=False)
    with context.release_lock() as verify_lock:
        verify_lock()
        verify_installation(policy)
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
    # Independently compare the installed set to Git, not merely to another
    # policy-provided list of hashes that might describe a different Source.
    import hashlib
    prefix = 'enterprise_agent_poc/'
    names = p.git(policy['repository'], 'ls-tree', '-r', '--name-only',
                  policy['tooling']['source'], '--', prefix).decode().splitlines()
    expected = {str(root/name.removeprefix(prefix)):hashlib.sha256(p.git(policy['repository'],
        'show', policy['tooling']['source']+':'+name)).hexdigest() for name in names}
    p.require(actual == expected, 'INSTALL_GIT_BLOB_SET')
    verify_runtime_installation(policy)
    for path in (BASE/'operations', BASE/'releases', *(Path('/etc/systemd/system')/(u+'.d') for u in UNITS.values())):
        p.trusted(path, directory=True)
    p.require(p.sha(p.trusted(policy['recovery_archive'])) == ARCHIVE801
              and p.sha(p.trusted(policy['recovery_manifest'])) == MANIFEST801, 'RECOVERY_INSTALL_PINS')
    return {'status':'INSTALL_BYTES_VERIFIED_NOT_EXECUTION_AUTHORITY', 'tooling':policy['tooling']}


def verify_runtime_installation(policy):
    if runtime_contract(policy) == REUSE:
        p.require(p.identity(policy['repository'], EXECUTOR790['source']) ==
            {**EXECUTOR790,'git_parents':['3f683774e412ed405fb321c4b97a0d273d39f2b0']},
            'RUNTIME_REUSE_EXECUTOR_BASELINE')
        verify_reused_runtime(policy)
        return
    # Original complete-runtime acceptance remains unchanged, including nlink=1.
    from scripts import test_delivery_adoption as adoption
    runtime = adoption.runtime_inventory(policy['runtime_roots'], protected=True)
    closure = p.read(p.trusted(policy['runtime_closure_receipt']))
    p.require(p.digest(closure) == policy['runtime_profile_sha256']
        and closure['runtime_inventory_sha256'] == p.digest(runtime)
        and closure['status'] == 'PASS' and closure['checks'] == {k:'PASS' for k in
            ('dynamic_loader','python_imports','pgvector_load','redis_start','relocation')}, 'PERSISTENT_RUNTIME_CLOSURE')


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
