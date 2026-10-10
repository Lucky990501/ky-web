"""Native bindings for the existing formal release/recovery operator.

Requires separately root-approved policy, code pins, unit profiles and Native
rehearsal acceptance. This source commit itself grants NO install authority.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4
from urllib.parse import urlsplit

from scripts import seeding_approval_selector as s
from scripts.wechat_runtime_test_lifecycle_guard import need

HOME = Path('/opt/enterprise-agent-workbench-test')
AUTH = Path('/etc/enterprise-agent-test-tenant-seeding-v1')
ROOT = AUTH/'selector.v1'
ACTIVE = AUTH/'approval.v1.json'
LOCK = Path('/run/lock/enterprise-agent-test-successor-v1.lock')
GUARD = Path('/etc/enterprise-agent-test-wechat-persistent-config-v1/primary_guard.py')
FORMAL = Path('/etc/enterprise-agent-test-successor-chat-reference-v1/entry.py')
FORMAL_SHA = '5f91603551e38119a294de7ecdce457ef19d557e902c1af0cfc2726f49af6b9e'
BASE_TOOL = '15eb070abbec55c2f492b03be17e7e2b15ade891'
TRIAL = 'SEEDING_SELECTOR_CONTROLLED_NATIVE_TRIAL_V1'
RECOVERY_AUTH = Path('/etc/enterprise-agent-test-exact-admin-v1')
TRIAL_ACTIONS = ['prepare','activate','recover','close','status','startup','health']
CODE = ('seeding_approval_selector.py','seeding_release_binding.py',
        'runtime_recovery_operator.py','runtime_recovery_binding.py')
ROLES = ('api','mcp','worker')
PYTHON = HOME/'shared/runtime/python311/bin/python'


def trusted(path, *, writable=False):
    for p in (path, *path.parents):
        info = p.lstat()
        need(not stat.S_ISLNK(info.st_mode) and info.st_uid == info.st_gid == 0
             and not info.st_mode & 0o022, 'SELECTOR_PATH_TRUST')
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode) and (writable or not info.st_mode & 0o222), 'SELECTOR_FILE_MODE')


def read(path):
    trusted(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        a, b = os.fstat(stream.fileno()), path.lstat()
        need((a.st_ino,a.st_dev) == (b.st_ino,b.st_dev) and a.st_size <= 16*1024*1024, 'SELECTOR_FILE_IDENTITY')
        return stream.read()


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def atomic(path, raw, *, immutable=False):
    """Only caller-validated fixed targets; abandoned unique temps are inert."""
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == info.st_gid == 0
             and not info.st_mode & 0o022, 'SELECTOR_WRITE_PARENT_TRUST')
    need(not path.is_symlink(), 'SELECTOR_WRITE_SYMLINK')
    if path.exists():
        existing = read(path)
        if immutable:
            need(existing == raw, 'SELECTOR_IMMUTABLE_COLLISION')
            return
    temporary = path.parent/('.selector-'+str(uuid4())+'.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    if immutable:
        # link() supplies NO-REPLACE semantics; source/destination same filesystem.
        os.link(temporary, path)
        temporary.unlink()
    else:
        os.replace(temporary, path)
    fsync_dir(path.parent)


def shape(policy, approval, policy_sha):
    need(set(policy) == {'contract','environment','production_authority','operation_id',
        'applications','tooling','predecessor_approval_sha256','target_authority_id','tenant_ids',
        'guard_sha256','formal_switch_sha256','profiles'}, 'SELECTOR_POLICY_SHAPE')
    need(policy['contract'] == s.VERSION and policy['environment'] == 'test'
         and policy['production_authority'] is False, 'SELECTOR_POLICY_DOMAIN')
    need(str(UUID(policy['operation_id'])) == policy['operation_id'], 'SELECTOR_OPERATION_ID')
    need(set(policy['applications']) == set(s.IDENTITIES), 'SELECTOR_VERSION_SET')
    for version, item in policy['applications'].items():
        need(set(item) == {'source','tree','path','files_sha256'}
             and (item['source'],item['tree']) == s.IDENTITIES[version], 'SELECTOR_APPLICATION_BINDING')
        path = Path(item['path'])
        need(path.parent == HOME/'releases' and path.name not in ('','.','..'), 'SELECTOR_RELEASE_PATH')
    need(policy['applications']['d8a']['path'] == str(HOME/'releases/20261008-d8a7814-wechat-personal-v1'),
         'SELECTOR_PREDECESSOR_RELEASE')
    tool = policy['tooling']
    need(set(tool) == {'source','tree','path','files_sha256'}
         and Path(tool['path']).parent == HOME/'shared/source-qualifications', 'SELECTOR_TOOLING_BINDING')
    for key in ('source','tree'):
        need(isinstance(tool[key],str) and re.fullmatch('[a-f0-9]{40}',tool[key]), 'SELECTOR_EXACT_TOOLING')
    need(isinstance(policy['target_authority_id'],str)
         and re.fullmatch('[A-Za-z0-9_-]{1,128}',policy['target_authority_id']), 'SELECTOR_TARGET_AUTHORITY')
    need(isinstance(policy['tenant_ids'],list) and bool(policy['tenant_ids'])
         and len(set(policy['tenant_ids'])) == len(policy['tenant_ids'])
         and all(isinstance(t,str) and re.fullmatch('[A-Za-z0-9_-]{1,128}',t) for t in policy['tenant_ids']),
         'SELECTOR_TENANT_SCOPE')
    need(policy['formal_switch_sha256'] == FORMAL_SHA, 'SELECTOR_FORMAL_SWITCH_PIN')
    need(set(policy['profiles']) == set(s.IDENTITIES), 'SELECTOR_PROFILE_VERSIONS')
    for pins in policy['profiles'].values():
        need(set(pins) == set(ROLES), 'SELECTOR_PROFILE_ROLES')
    hashes = [policy['guard_sha256'],policy['predecessor_approval_sha256'], tool['files_sha256']]
    hashes += [v['files_sha256'] for v in policy['applications'].values()]
    hashes += [pin for pins in policy['profiles'].values() for pin in pins.values()]
    need(all(isinstance(h,str) and re.fullmatch('[a-f0-9]{64}',h) for h in hashes), 'SELECTOR_SHA256')
    need(set(approval) == {'contract','policy_sha256','authorization','production_authority','code_sha256'}
         and approval['contract'] == s.VERSION and approval['policy_sha256'] == policy_sha
         and approval['authorization'] == 'FORMAL_SEEDING_APPROVAL_VERSIONED_SELECTION_FIX_APPROVED'
         and approval['production_authority'] is False and set(approval['code_sha256']) == set(CODE),
         'SELECTOR_RELEASE_APPROVAL')
    need(all(re.fullmatch('[a-f0-9]{64}',x) for x in approval['code_sha256'].values()), 'SELECTOR_CODE_PINS')


def trial_time(value):
    need(isinstance(value,str) and value.endswith('+00:00'),'SELECTOR_TRIAL_UTC_REQUIRED')
    return datetime.fromisoformat(value)


def trial_shape(value, policy, issuance, policy_sha):
    fields={'contract','authorization','environment','production_authority','policy_sha256','issuance_approval_sha256',
        'applications','tooling','predecessor_approval_sha256','tenant_ids','database','code_sha256','recovery_pins',
        'operation_id','not_before','execute_until','recover_until','operations','business_authority',
        'test_write_authority','network_fence','budget'}
    need(set(value)==fields and value['contract']==TRIAL
         and value['authorization']=='SEEDING_SELECTOR_CONTROLLED_NATIVE_TRIAL_APPROVED'
         and value['environment']=='test' and value['production_authority'] is False
         and value['business_authority'] is False and value['test_write_authority'] is False
         and value['budget']==s.ZERO and all(type(v) is int for v in value['budget'].values()),'SELECTOR_TRIAL_DOMAIN')
    need(value['policy_sha256']==policy_sha and value['issuance_approval_sha256']==s.sha(read(ROOT/'approval.v1.json'))
         and all(value[k]==policy[k] for k in ('applications','tooling','predecessor_approval_sha256','tenant_ids','operation_id'))
         and value['database']==s.DATABASE and value['code_sha256']==issuance['code_sha256'], 'SELECTOR_TRIAL_EXACT_IDENTITY')
    need(value['operations']==TRIAL_ACTIONS and value['network_fence']=='ROOT_API_LOCAL_MCP_ONLY_V1',
         'SELECTOR_TRIAL_SCOPE')
    need(set(value['recovery_pins'])=={'forward-recovery-policy.v1.json','forward-recovery-approval.v1.json',
         'runtime-pair.v1.json','scope.v1.json','approval.v2.json','native-policy.v2.json','native-approval.v2.json'}
         and all(isinstance(v,str) and re.fullmatch('[a-f0-9]{64}',v) for v in value['recovery_pins'].values()),
         'SELECTOR_TRIAL_RECOVERY_PINS')
    start,execute,end=(trial_time(value[k]) for k in ('not_before','execute_until','recover_until'))
    need(0 < (execute-start).total_seconds() <= 14400 and execute <= end
         and (end-start).total_seconds() <= 86400,'SELECTOR_TRIAL_BOUNDED_WINDOW')


def verify_trial_fence(observed):
    """Read-only kernel fence attestation, not a new network controller.

    Rules must be installed separately under formal maintenance authority. Both
    IP families are checked, with jumps before any general ACCEPT/conntrack rule.
    Public reverse proxies cannot reach API/MCP; only root test executor reaches
    API and existing lucky service processes reach internal MCP.
    """
    for hook,suffix in (('INPUT','IN'),('OUTPUT','OUT')):
        rules=[r for r in observed[hook] if r[:2]==['-A',hook]]
        need(rules and rules[0]==shlex.split(f'-A {hook} -p tcp -m multiport --dports 18100,18101 -j WB_SELECTOR_TRIAL_{suffix}'),
             'SELECTOR_TRIAL_INGRESS_UNFENCED')
    expected={
        'WB_SELECTOR_TRIAL_IN':[
            '-N WB_SELECTOR_TRIAL_IN',
            '-A WB_SELECTOR_TRIAL_IN -i lo -j ACCEPT',
            '-A WB_SELECTOR_TRIAL_IN -p tcp -j REJECT --reject-with tcp-reset'],
        'WB_SELECTOR_TRIAL_OUT':[
            '-N WB_SELECTOR_TRIAL_OUT',
            '-A WB_SELECTOR_TRIAL_OUT -m owner --uid-owner 0 -j ACCEPT',
            '-A WB_SELECTOR_TRIAL_OUT -p tcp -m tcp --dport 18101 -m owner --uid-owner 1000 -j ACCEPT',
            '-A WB_SELECTOR_TRIAL_OUT -p tcp -j REJECT --reject-with tcp-reset']}
    for chain,lines in expected.items():
        need(observed[chain]==[shlex.split(line) for line in lines],'SELECTOR_TRIAL_INGRESS_UNFENCED')


def verify_inherited_lock():
    """Internal Trial recovery child shares the existing Selector flock FD."""
    import fcntl
    need(os.geteuid()==0,'SELECTOR_TRIAL_LOCK_REQUIRED')
    value=os.environ.get('SELECTOR_TRIAL_LOCK_FD','')
    need(value.isdigit(),'SELECTOR_TRIAL_LOCK_REQUIRED')
    fd=int(value); info=os.fstat(fd); actual=LOCK.lstat()
    need((info.st_dev,info.st_ino)==(actual.st_dev,actual.st_ino) and stat.S_ISREG(info.st_mode)
         and info.st_uid==info.st_gid==0 and not info.st_mode & 0o022,'SELECTOR_TRIAL_LOCK_REQUIRED')
    probe=os.open(LOCK,os.O_RDWR | os.O_NOFOLLOW)
    try:
        try: fcntl.flock(probe,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: pass
        else: need(False,'SELECTOR_TRIAL_LOCK_NOT_HELD')
        # The inherited open-file description itself must own the lock, not
        # merely name a file currently locked by a different operator.
        try: fcntl.flock(fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: need(False,'SELECTOR_TRIAL_LOCK_NOT_OWNED')
    finally: os.close(probe)


def validate_trial_queue(client, queue, tests):
    for key in (queue.knowledge_pending,queue.knowledge_processing):
        need(client.llen(key)==0,'SELECTOR_TRIAL_BUSINESS_BACKLOG')
    for key in (queue.pending,queue.processing):
        need(client.llen(key)<=1000,'SELECTOR_TRIAL_QUEUE_BOUND')
        need(set(client.lrange(key,0,-1))<=tests,'SELECTOR_TRIAL_BUSINESS_BACKLOG')


def assert_selected(pair, *, trial=False, service_role=None, recovery=False):
    """Additional fixed-approval check for existing PRIMARY startup/recovery.

    Old deployments without this versioned install retain 796a behavior. Once
    installed, malformed/missing state is NEVER treated as an optional bypass.
    """
    if not ROOT.exists() and not ROOT.is_symlink(): return
    policy_raw = read(ROOT/'policy.v1.json'); policy = json.loads(policy_raw)
    approval = json.loads(read(ROOT/'approval.v1.json'))
    shape(policy, approval, s.sha(policy_raw))
    need(all(pair['application'][k] == policy['applications']['28e'][k] for k in ('source','tree','path'))
         and all(pair['tooling'][k] == policy['tooling'][k] for k in ('source','tree','path')), 'SELECTOR_STARTUP_PAIR')
    backend = object.__new__(NativeRelease)
    backend.policy, backend.policy_sha, backend.approval = policy, s.sha(policy_raw), approval
    state = s.Selector(backend).load()
    need(state is not None, 'SELECTOR_STARTUP_STATE')
    event = state['events'][-1]
    # An ordinary CLI startup never inherits Trial from the environment or disk.
    # Service hooks may use Trial only inside the exact real systemd cgroup.
    is_trial = event.get('stage') == 'trial'
    if service_role is not None and is_trial:
        need(service_role in ROLES and os.geteuid()==0, 'SELECTOR_TRIAL_ROOT_SERVICE_HOOK')
        unit = 'enterprise-agent-test-'+service_role+'.service'
        need(any(line.split(':',2)[-1].rstrip().endswith('/'+unit)
             for line in Path('/proc/self/cgroup').read_text().splitlines()), 'SELECTOR_TRIAL_SERVICE_CONTEXT')
        trial = True
    if trial:
        need(is_trial, 'SELECTOR_TRIAL_STATE_REQUIRED')
        if service_role is None: verify_inherited_lock()
        backend.stage='trial'
        # Recovery-started service hooks use only the durable recovery direction.
        purpose='recover' if recovery or event['phase'] in {'RECOVERING','RECOVERED'} else 'startup'
        backend.require_trial(purpose,state)
        backend.trial_runtime_bounds(purpose)
        backend.trial_queue_boundary()
    else:
        backend.stage='activation'; backend.require_native_acceptance()
        need(not is_trial, 'SELECTOR_FINAL_ACTIVATION_REQUIRED')
        backend.require_final_state(state)
    need(event['direction'] == '28e' and event['phase'] in {'SELECTED','COMPLETE','RECOVERING','RECOVERED'},
         'SELECTOR_STARTUP_PENDING')
    need(read(ACTIVE) == read(ROOT/'28e.approval.v1.json') == s.issue_target(policy), 'SELECTOR_STARTUP_APPROVAL')
    s.approval(read(ACTIVE), '28e')


class NativeRelease:
    def __init__(self, *, stage='activation'):
        need(os.name == 'posix' and os.geteuid() == 0 and os.environ.get('APP_ENV') == 'test'
             and sys.dont_write_bytecode and os.environ.get('PYTHONDONTWRITEBYTECODE') == '1', 'SELECTOR_ROOT_TEST_ONLY')
        raw = read(ROOT/'policy.v1.json'); self.policy = json.loads(raw); self.policy_sha = s.sha(raw)
        self.approval = json.loads(read(ROOT/'approval.v1.json'))
        shape(self.policy,self.approval,self.policy_sha)
        need(stage in {'trial','activation'}, 'SELECTOR_STAGE')
        self.stage=stage; self.purpose='status'

    @contextmanager
    def lock(self):
        import fcntl
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            a,b = os.fstat(fd),LOCK.lstat()
            need((a.st_dev,a.st_ino)==(b.st_dev,b.st_ino) and stat.S_ISREG(a.st_mode)
                 and a.st_uid == a.st_gid == 0 and not a.st_mode & 0o022, 'SELECTOR_LOCK_IDENTITY')
            for parent in (LOCK.parent,*LOCK.parent.parents):
                info=parent.lstat()
                # Ubuntu's EXISTING /run/lock is root-owned sticky/writable.
                # Sticky protects the root-owned lock from unprivileged unlink;
                # this exception applies to this lock parent, NEVER Authority.
                need(stat.S_ISDIR(info.st_mode) and info.st_uid==info.st_gid==0 and
                     (not info.st_mode & 0o022 or (parent==Path('/run/lock') and info.st_mode & stat.S_ISVTX)),
                     'SELECTOR_LOCK_PARENT')
            try: fcntl.flock(fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: need(False,'SELECTOR_CONCURRENT_OPERATION')
            self.lock_fd=fd
            yield
        finally:
            self.lock_fd=None
            os.close(fd)

    def command(self, args, **kwargs):
        result = subprocess.run([str(x) for x in args],capture_output=True,text=True,timeout=120,**kwargs)
        # Never propagate command stderr (could contain environment/credentials).
        need(result.returncode == 0, 'SELECTOR_PINNED_COMMAND_FAILED')
        return result.stdout.strip()

    def validate(self):
        need(s.sha(read(ROOT/'policy.v1.json')) == self.policy_sha
             and json.loads(read(ROOT/'approval.v1.json')) == self.approval, 'SELECTOR_AUTHORITY_CHANGED')
        from app.test_runtime_tooling import verify_checkout
        items = dict(self.policy['applications'], tooling=self.policy['tooling'])
        for name, item in items.items():
            raw = read(ROOT/(name+'-files.v1.json'))
            need(s.sha(raw) == item['files_sha256'], 'SELECTOR_FILE_MAP_PIN')
            verify_checkout(Path(item['path']),{k:item[k] for k in ('source','tree','path')} | {'files':json.loads(raw)})
        scripts = Path(self.policy['tooling']['path'])/'enterprise_agent_poc/scripts'
        need(scripts == Path(__file__).resolve().parent, 'SELECTOR_LOADED_TOOLING')
        need(self.command(['/usr/bin/git','-c','safe.directory='+self.policy['tooling']['path'],
            '-C',self.policy['tooling']['path'],'rev-parse','HEAD^']) == BASE_TOOL,'SELECTOR_TOOLING_PARENT')
        for name,pin in self.approval['code_sha256'].items():
            need(s.sha(read(scripts/name)) == pin,'SELECTOR_CODE_PIN')
        need(s.sha(read(GUARD)) == self.policy['guard_sha256'], 'SELECTOR_PREDECESSOR_GUARD_PIN')
        need(s.sha(read(FORMAL)) == FORMAL_SHA, 'SELECTOR_FORMAL_SWITCH_PIN')
        for version in s.IDENTITIES:
            for role in ROLES:
                need(s.sha(read(ROOT/'profiles'/version/(role+'.conf'))) == self.policy['profiles'][version][role],
                     'SELECTOR_SERVICE_PROFILE_PIN')

    def require_native_acceptance(self):
        # Separate immutable activation approval: later Native acceptance never
        # edits either historical d8a approval OR the preparation approval.
        need((ROOT/'activation-approval.v1.json').is_file(),'SELECTOR_NATIVE_RELEASE_GATE_REQUIRED')
        approval=json.loads(read(ROOT/'activation-approval.v1.json'))
        need(set(approval)=={'contract','policy_sha256','issuance_approval_sha256','native_acceptance_sha256',
                            'authorization','production_authority'}
             and approval['contract']==s.VERSION and approval['policy_sha256']==self.policy_sha
             and approval['issuance_approval_sha256']==s.sha(read(ROOT/'approval.v1.json'))
             and approval['authorization']=='SEEDING_SELECTOR_NATIVE_RELEASE_ACCEPTED_V1'
             and approval['production_authority'] is False,'SELECTOR_NATIVE_RELEASE_GATE_REQUIRED')
        pin = approval['native_acceptance_sha256']
        need(isinstance(pin,str) and re.fullmatch('[a-f0-9]{64}',pin), 'SELECTOR_NATIVE_RELEASE_GATE_REQUIRED')
        raw = read(ROOT/'native-acceptance.v1.json'); value = json.loads(raw)
        need(s.sha(raw) == pin and set(value) == {'contract','policy_sha256','status','checks','trial_receipt_sha256'}
             and value['contract'] == s.VERSION and value['policy_sha256'] == self.policy_sha
             and value['status'] == 'NATIVE_RELEASE_ACCEPTED'
             and value['checks'] == dict.fromkeys(('d8a_loader','28e_loader','switch_recovery',
                 'post_write_forward_recovery','api_mcp_worker','secret_preservation'),'PASS'),
             'SELECTOR_NATIVE_RELEASE_GATE_REQUIRED')
        receipt_raw=read(ROOT/'trial-receipt.v1.json'); receipt=json.loads(receipt_raw)
        need(value['trial_receipt_sha256']==s.sha(receipt_raw)
             and receipt['policy_sha256']==self.policy_sha
             and receipt['operation_id']==self.policy['operation_id']
             and receipt['events'][-1]['phase']=='TRIAL_CLOSED'
             and receipt['events'][-1]['stage']=='trial'
             and receipt['trial_approval_sha256']==s.sha(read(ROOT/'trial-approval.v1.json')),
             'SELECTOR_REAL_TRIAL_RECEIPT_REQUIRED')
        # Validate the original chain without requiring a now-expired execution
        # lease. Final approval is independently issued over immutable evidence.
        reader=SimpleNamespace(state=lambda:receipt,policy=self.policy,policy_sha=self.policy_sha)
        s.Selector(reader).load()
        self.final_receipt=receipt

    def require_final_state(self,state):
        receipt=self.final_receipt
        need(state.get('trial_approval_sha256')==receipt['trial_approval_sha256']
             and state['events'][:len(receipt['events'])]==receipt['events']
             and len(state['events'])>len(receipt['events'])
             and state['events'][len(receipt['events'])]['phase']=='FINAL_AUTHORIZED',
             'SELECTOR_FINAL_TRIAL_PROVENANCE')

    def authorize(self, action, state):
        self.purpose=action
        if self.stage=='trial':
            self.require_trial(action,state)
            need(state is None or state['events'][-1].get('stage')=='trial','SELECTOR_TRIAL_STAGE_REUSE')
            need(not state or state['events'][-1]['phase']!='TRIAL_CLOSED' or action in {'status','close'},
                 'SELECTOR_TRIAL_ALREADY_CLOSED')
        elif action in {'activate','recover','close'} or (state and 'trial_approval_sha256' in state):
            need(action!='close','SELECTOR_CLOSE_TRIAL_ONLY')
            self.require_native_acceptance()
            if state and state['events'][-1].get('stage')=='trial':
                need(action=='activate' and state['events'][-1]['phase']=='TRIAL_CLOSED'
                     and s.canonical(state)==read(ROOT/'trial-receipt.v1.json'), 'SELECTOR_TRIAL_NOT_CLOSED')
            elif state: self.require_final_state(state)

    def require_trial(self, action, state):
        need(action in TRIAL_ACTIONS and os.geteuid()==0 and os.environ.get('APP_ENV')=='test',
             'SELECTOR_TRIAL_TEST_ROOT_ONLY')
        raw=read(ROOT/'trial-approval.v1.json'); value=json.loads(raw)
        need(s.sha(read(ROOT/'policy.v1.json'))==self.policy_sha,'SELECTOR_TRIAL_POLICY_CHANGED')
        trial_shape(value,self.policy,self.approval,self.policy_sha)
        self.trial_sha=s.sha(raw)
        need(state is None or state.get('trial_approval_sha256')==self.trial_sha,'SELECTOR_TRIAL_STATE_BINDING')
        status=json.loads(read(ROOT/'trial-status.v1.json'))
        need(set(status)=={'contract','trial_approval_sha256','state'} and status['contract']==TRIAL
             and status['trial_approval_sha256']==self.trial_sha,'SELECTOR_TRIAL_REVOCATION_IDENTITY')
        recovery=action in {'recover','close'}
        need(status['state'] in ({'ACTIVE','RECOVERY_ONLY'} if recovery else {'ACTIVE'}),'SELECTOR_TRIAL_REVOKED')
        now=datetime.now(timezone.utc)
        start,execute,end=(trial_time(value[k]) for k in ('not_before','execute_until','recover_until'))
        need(start<=now<(end if recovery else execute),'SELECTOR_TRIAL_EXPIRED')
        for name,pin in value['recovery_pins'].items():
            need(s.sha(read(RECOVERY_AUTH/name))==pin,'SELECTOR_TRIAL_RECOVERY_PIN')
        # No approval Boolean substitutes for kernel isolation. These are read-
        # only checks; separately approved operators establish the fence.
        self.trial_fence()
        self.trial=value

    def trial_fence(self):
        for binary in ('/usr/sbin/iptables','/usr/sbin/ip6tables'):
            observed={chain:[shlex.split(line) for line in self.command([binary,'-S',chain]).splitlines()]
                      for chain in ('INPUT','OUTPUT','WB_SELECTOR_TRIAL_IN','WB_SELECTOR_TRIAL_OUT')}
            verify_trial_fence(observed)

    def trial_runtime_bounds(self, purpose):
        deadline=trial_time(self.trial['recover_until' if purpose=='recover' else 'execute_until'])
        remaining=(deadline-datetime.now(timezone.utc)).total_seconds()
        for role in ROLES:
            value=self.ctl('show','enterprise-agent-test-'+role+'.service','-p','RuntimeMaxUSec','--value')
            tokens=re.findall(r'(\d+(?:\.\d+)?)(us|ms|min|s|h|d)',value)
            need(tokens and ''.join(n+u for n,u in tokens)==value.replace(' ',''),'SELECTOR_TRIAL_SERVICE_DEADLINE')
            duration=sum(float(n)*{'us':.000001,'ms':.001,'s':1,'min':60,'h':3600,'d':86400}[u] for n,u in tokens)
            need(0<duration<=remaining,'SELECTOR_TRIAL_SERVICE_DEADLINE')

    def trial_queue_boundary(self):
        # This never grants a test write/lease. Existing Native Guard and Worker
        # recheck exact formal test authorization; non-test backlog cannot run.
        from app.settings import Settings
        from app.task_queue import RedisTaskQueue
        _,_,data=self.snapshot()
        tests={r['task_id'] for r in data['agent_template_tests'] if r['test_type']=='runtime'}
        queue=RedisTaskQueue.from_settings(Settings.from_env())
        try:
            validate_trial_queue(queue._client,queue,tests)
        finally: queue._client.close()

    def close_trial(self,state):
        need(state['events'][-1]['phase']=='TRIAL_CLOSED' and self.stage=='trial','SELECTOR_TRIAL_INCOMPLETE')
        atomic(ROOT/'trial-receipt.v1.json',s.canonical(state),immutable=True)

    def state(self): return json.loads(read(ROOT/'state.v1.json')) if (ROOT/'state.v1.json').exists() else None
    def save(self,value): atomic(ROOT/'state.v1.json',s.canonical(value))
    def archive(self,version,raw): atomic(ROOT/(version+'.approval.v1.json'),raw,immutable=True)
    def version(self,version): return read(ROOT/(version+'.approval.v1.json'))
    def active(self): return read(ACTIVE)
    def install(self,raw): atomic(ACTIVE,raw)

    def current(self):
        path = (HOME/'current').resolve(strict=True)
        matches = [v for v,p in self.policy['applications'].items() if Path(p['path']) == path]
        need(len(matches)==1,'SELECTOR_CURRENT_RELEASE')
        return matches[0]

    def guard(self):
        need(s.sha(read(GUARD)) == self.policy['guard_sha256'], 'SELECTOR_PREDECESSOR_GUARD_PIN')
        spec = importlib.util.spec_from_file_location('selector_exact_predecessor_guard',GUARD)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def snapshot(self):
        guard = self.guard()
        _, policy, _, _ = guard.load_authority()
        need(guard.sha256(guard.BASE/'common.py') == policy['base_guard_code_sha256'], 'PERSISTENT_BASE_GUARD_DRIFT')
        need(guard.sha256(guard.BASE/'policy.v1.json') == policy['base_guard_policy_sha256'], 'PERSISTENT_BASE_POLICY_DRIFT')
        guard.base.load(); guard.base.check_env()
        target = urlsplit(os.environ['ENTERPRISE_POC_DATABASE_URL'])
        need((target.hostname,target.port,target.username,target.path) ==
             ('127.0.0.1',55432,'enterprise_agent_test','/enterprise_agent_test'), 'SELECTOR_DATABASE_TARGET')
        schema,data = guard.base.snapshot()  # existing READ ONLY repeatable-read collector
        need(sorted(r['id'] for r in data['tenants']) == sorted(self.policy['tenant_ids']), 'SELECTOR_EXACT_TENANTS')
        return guard,schema,data

    def fingerprint(self):
        guard,schema,data = self.snapshot()
        # Canonical order, no mutable fields excluded. Stored only as operation
        # baseline, NEVER as a replacement historical Guard/Receipt/Witness.
        return guard.digest(dict(schema=schema,data={k:sorted(v,key=guard.digest) for k,v in data.items()}))

    def predecessor(self):
        guard,schema,data = self.snapshot()
        _,policy,witness,task_witness = guard.load_authority()
        guard.source_identity()
        base = guard.read(guard.BASE/'policy.v1.json')
        need(guard.digest(schema)==base['schema015_fingerprint'] and set(data)==set(base['table_set']),
             'SELECTOR_PREDECESSOR_SCHEMA')
        need({str(p.relative_to(guard.RELEASE)):guard.sha256(p) for p in guard.RELEASE.rglob('*') if p.is_file()}
             == base['files'],'SELECTOR_PREDECESSOR_FILES')
        result = guard.validate_snapshot(data,policy,witness,task_witness,environment='test',
                                        source=s.IDENTITIES['d8a'][0],tree=s.IDENTITIES['d8a'][1])
        need(result['status']=='PASS' and result['active_test_platform_admin']==0,'SELECTOR_PREDECESSOR_INCOMPATIBLE')

    def ctl(self,*args): return self.command(['/usr/bin/systemctl',*args])
    def quiesce(self):
        self.authorize(self.purpose, self.state())
        for role in ('worker','api','mcp'): self.ctl('stop','enterprise-agent-test-'+role+'.service')
        self.assert_quiesced()
    def assert_quiesced(self):
        for role in ROLES:
            unit='enterprise-agent-test-'+role+'.service'
            need(self.ctl('show',unit,'-p','MainPID','--value')=='0'
                 and self.ctl('show',unit,'-p','ActiveState','--value') in {'inactive','failed'},'SELECTOR_NOT_QUIESCED')

    def switch(self,version):
        target = Path(self.policy['applications'][version]['path'])
        if (HOME/'current').resolve(strict=True)==target: return
        pending=HOME/'.current-successor-pending'
        if pending.is_symlink():
            need(pending.lstat().st_uid == 0 and pending.resolve() in
                 {Path(p['path']) for p in self.policy['applications'].values()},'SELECTOR_PENDING_POINTER')
            # This is the existing formal switch's own incomplete symlink only.
            pending.unlink(); fsync_dir(HOME)
        raw=read(FORMAL); need(s.sha(raw)==FORMAL_SHA,'SELECTOR_FORMAL_SWITCH_PIN')
        tree=ast.parse(raw)
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='switch_pointer']
        need(len(nodes)==1,'SELECTOR_FORMAL_SWITCH_ENTRY')
        namespace={'os':os,'c':SimpleNamespace(R=HOME,need=need,
            RELEASE=Path(self.policy['applications']['28e']['path']),
            OLD_RELEASE=Path(self.policy['applications']['d8a']['path']))}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(FORMAL),'exec'),namespace)
        namespace['switch_pointer'](target); fsync_dir(HOME)

    def configure(self,version):
        for role in ROLES:
            path=Path('/etc/systemd/system')/('enterprise-agent-test-'+role+'.service.d')/'zz-seeding-selector.v1.conf'
            if path.exists():
                need(s.sha(read(path)) in {p[role] for p in self.policy['profiles'].values()},'SELECTOR_FOREIGN_DROPIN')
            atomic(path,read(ROOT/'profiles'/version/(role+'.conf')))
        self.ctl('daemon-reload')

    def environment(self,version):
        env=os.environ.copy()
        env.update(PYTHONPATH=self.policy['applications'][version]['path']+'/enterprise_agent_poc:'+
                   self.policy['tooling']['path']+'/enterprise_agent_poc',PYTHONDONTWRITEBYTECODE='1',
                   ENTERPRISE_POC_TEST_TENANT_SEEDING_POLICY_REQUIRED='true')
        return env

    def loader(self,version):
        path=self.policy['applications'][version]['path']+'/enterprise_agent_poc'
        code="""import json,os
from app import test_tenant_seeding as p
from app.store import POCStore
if os.environ['SELECTOR_EXPECTED_VERSION']=='28e':
 from app.test_runtime_tooling import authority_root,ISOLATED_AUTHORITY
 assert authority_root()!=ISOLATED_AUTHORITY
store=POCStore(os.environ['ENTERPRISE_POC_DATABASE_URL'])
with store.connection() as conn:
 conn.execute('SET TRANSACTION READ ONLY')
 assert p.authorized_exclusions(conn,store)==frozenset()
print(json.dumps({'status':'PASS','identity':p.source_identity()}))
"""
        env=self.environment(version); env['SELECTOR_EXPECTED_VERSION']=version
        value=json.loads(self.command(['/usr/sbin/runuser','--preserve-environment','-u','lucky','--',
            PYTHON,'-B','-c',code],cwd=path,env=env))
        need(value==dict(status='PASS',identity=dict(zip(('source_commit','source_tree'),s.IDENTITIES[version]))),
             'SELECTOR_REAL_LOADER_REQUIRED')

    def recovery(self,action):
        script=Path(self.policy['tooling']['path'])/'enterprise_agent_poc/scripts/runtime_recovery_operator.py'
        env=self.environment('28e'); kwargs={}
        if self.stage=='trial':
            self.require_trial('recover' if self.purpose in {'recover','close'} else 'startup', self.state())
            need(isinstance(self.lock_fd,int),'SELECTOR_TRIAL_LOCK_REQUIRED')
            env['SELECTOR_TRIAL_LOCK_FD']=str(self.lock_fd); kwargs['pass_fds']=(self.lock_fd,)
            action={'recover':'trial-forward-recover','startup':'trial-startup','status':'trial-status'}[action]
            if action=='trial-status' and self.purpose in {'recover','close'}: action='trial-recovery-status'
        result=json.loads(self.command([PYTHON,'-B',script,action],env=env,
            cwd=self.policy['applications']['28e']['path']+'/enterprise_agent_poc',**kwargs))
        need(result.get('status')=='PASS','SELECTOR_FORWARD_RECOVERY_FAILED')
    def forward(self): self.recovery('recover')
    def start(self,version):
        if version=='28e': self.recovery('startup')
        else:
            for role in ('mcp','api','worker'): self.ctl('restart','enterprise-agent-test-'+role+'.service')
    def verify_running(self,version,raw):
        need(self.current()==version and self.active()==raw,'SELECTOR_RUNNING_PAIR')
        self.loader(version)
        if version=='28e': self.recovery('status')
        else: need(self.guard().verify(topology=True)['status']=='PASS','SELECTOR_PREDECESSOR_HEALTH')


def dispatch(action):
    trial=action.startswith('trial-')
    if trial: action=action.removeprefix('trial-')
    need(action in ({'prepare','activate','recover','status','close'} if trial else {'prepare','activate','recover','status'}),
         'SELECTOR_ACTION')
    backend=NativeRelease(stage='trial' if trial else 'activation')
    result=getattr(s.Selector(backend),action)()
    return dict(status='PASS',contract=s.VERSION,operation=action,
                stage=backend.stage,state=result,install_authority='SEPARATE_NATIVE_RELEASE_GATE',
                business_authority=False,test_write_authority=False)
