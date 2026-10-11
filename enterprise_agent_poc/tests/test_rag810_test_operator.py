"""Component fault injection and real POSIX primitives, NOT Native acceptance."""
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sys
import os
import subprocess
import time
from types import SimpleNamespace

import pytest

from scripts import rag810_test_operator as op
# Native entry registers this already-pinned module before backend import.
sys.modules['rag810_test_operator'] = op
from scripts import rag810_test_operator_native as n


class ComponentBackend:
    """Explicit fake authority/services/data. Never emits a Native attestation."""
    binding = 'f' * 64
    def __init__(self, failure=None, marker=False, changed=False):
        self.failure = failure
        self.triggered = False
        self._events = []
        self.marker = marker
        self.record = {'business_writes_admitted': marker}
        self.changed = changed
        self.current = 'd8a'
        self.config = 'd8a'
        self.active = ['d8a'] * 3
        self.held = False
        self.calls = []

    def call(self, name):
        assert self.held, f'{name} outside global lock'
        self.calls.append(name)
        if self.failure == name and not self.triggered:
            self.triggered = True
            raise op.Blocked('RAG_OP_COMPONENT_INJECTED_FAILURE')

    @contextmanager
    def lock(self):
        assert not self.held
        self.held = True
        try: yield
        finally: self.held = False

    def authorize(self, action): self.call('authorize_' + action)
    def events(self): return copy.deepcopy(self._events)
    def admitted(self): return self.marker
    def append(self, event):
        self.call('append_' + event['phase'])
        op.validate_journal(self._events + [event], self.binding)
        self._events.append(copy.deepcopy(event))
    def exact_predecessor(self, live, selected=True):
        self.call('exact_predecessor_live' if live else 'exact_predecessor_stopped')
        assert not self.marker and not self.record['business_writes_admitted'] and not self.changed
        if selected: assert self.current == 'd8a'
        if live: assert self.active == ['d8a'] * 3
    def inspect_data(self):
        self.call('inspect_data')
        plan = {'mode': 'FORWARD_SUCCESSOR_KEEP_DATA' if self.changed or self.record['business_writes_admitted']
                else 'EXACT_D8A_RESTORE_REQUIRES_NATIVE_GATES', 'executes_recovery': False,
                'preserve_data': True, 'schema_down': False, 'synthetic_cleanup': False}
        return copy.deepcopy(self.record), plan
    def queue_check(self, initial): self.call('queue_initial' if initial else 'queue_recovery')
    def profiles_check(self, direction):
        self.call('profile_' + direction)
        assert self.config == direction
    def infra_health(self): self.call('infra_health')
    def capture_profiles(self): self.call('capture_profiles')
    def seal_live_operator(self): self.call('seal_live_operator')
    def quiesce(self):
        self.call('quiesce')
        self.active = []
    def assert_stopped(self):
        self.call('assert_stopped')
        assert not self.active
    def configure(self, direction):
        self.call('configure_' + direction)
        assert not self.active
        assert direction != 'd8a' or not self.marker
        self.config = direction
    def switch(self, direction):
        self.call('switch_' + direction)
        assert not self.active and self.config == direction
        self.current = direction
    def seal_admission(self):
        self.marker = True
        self.call('seal_admission')  # fault may leave the irrevocable marker
    def runtime_state(self, phase):
        self.call('runtime_' + phase)
        assert self.marker
        self.record['business_writes_admitted'] = True
    def start(self, direction):
        self.call('start_' + direction)
        assert self.config == self.current == direction
        assert direction != 'd8a' or not self.marker
        self.active = [direction] * 3
    def health(self, direction):
        self.call('health_' + direction)
        assert self.active == [direction] * 3
    def known_state(self):
        self.call('known_state')
        assert not self.active or set(self.active) == {self.current}
    def requeue_knowledge(self):
        self.call('requeue_knowledge')
        assert not self.active
    def capture_failure(self): self.call('capture_failure')


def test_commit_only_after_data_profile_health_and_record_pass():
    b = ComponentBackend()
    result = op.ExactOperator(b).activate()
    assert result['status'] == 'RAG_810_TEST_SWITCH_PASS'
    assert b._events[-1]['phase'] == 'COMMITTED'
    assert b.calls.index('seal_admission') < b.calls.index('runtime_ACTIVATING') < b.calls.index('start_810')
    assert b.calls.index('capture_profiles') > b.calls.index('infra_health')
    assert b.calls[-1] == 'append_COMMITTED'
    assert b.current == b.config == '810' and b.active == ['810'] * 3
    assert not b.held


@pytest.mark.parametrize('point', ['authorize_activate', 'exact_predecessor_live', 'inspect_data',
    'queue_initial', 'profile_d8a', 'infra_health'])
def test_final_preflight_failure_has_no_mutation(point):
    b = ComponentBackend(point)
    with pytest.raises(op.Blocked): op.ExactOperator(b).activate()
    assert b.current == b.config == 'd8a' and b.active == ['d8a'] * 3 and not b.marker
    assert not b._events and 'capture_profiles' not in b.calls


@pytest.mark.parametrize('point', ['quiesce', 'configure_810', 'switch_810', 'profile_810'])
def test_before_write_admission_restore_requires_full_predecessor(point):
    b = ComponentBackend(point)
    result = op.ExactOperator(b).activate()
    assert result == {'status': 'RAG_810_TEST_RESTORED_D8A', 'rag': 'KNOWN_UNAVAILABLE_NOT_HEALTHY'}
    assert b.current == b.config == 'd8a' and b.active == ['d8a'] * 3
    assert 'exact_predecessor_stopped' in b.calls and 'requeue_knowledge' not in b.calls
    assert not b.marker and not b.record['business_writes_admitted']


@pytest.mark.parametrize('point', ['seal_admission', 'runtime_ACTIVATING', 'start_810',
    'health_810', 'runtime_COMMITTED', 'append_COMMITTED'])
def test_admission_even_equal_data_never_rolls_back_old_worker(point):
    b = ComponentBackend(point)
    result = op.ExactOperator(b).activate()
    assert result['status'] == 'RAG_810_TEST_FORWARD_RECOVERED'
    assert b.marker and b.record['business_writes_admitted'] and b.current == b.config == '810'
    assert 'requeue_knowledge' in b.calls and 'switch_d8a' not in b.calls and 'start_d8a' not in b.calls


def test_no_second_activation_attempt():
    b = ComponentBackend()
    op.ExactOperator(b).activate()
    prior = list(b.calls)
    with pytest.raises(op.Blocked, match='RAG_OP_ATTEMPT_ALREADY_STARTED'):
        op.ExactOperator(b).activate()
    assert b.calls == prior + ['authorize_activate']


def test_unknown_live_source_capture_and_no_guessing():
    b = ComponentBackend()
    op.ExactOperator(b).activate()
    b.active = ['810', 'd8a', '810']
    before = len(b.calls)
    result = op.ExactOperator(b).recover()
    assert result['status'] == 'RAG_810_TEST_MANUAL_RECOVERY_REQUIRED'
    assert 'capture_failure' in b.calls[before:] and 'switch_d8a' not in b.calls[before:]
    assert 'quiesce' not in b.calls[before:]


def test_lost_marker_never_manufactured_for_recovery():
    b = ComponentBackend()
    op.ExactOperator(b).activate()
    b.marker = False
    result = op.ExactOperator(b).recover()
    assert result['status'] == 'RAG_810_TEST_MANUAL_RECOVERY_REQUIRED'
    assert not b.marker and 'switch_d8a' not in b.calls


@pytest.mark.parametrize('role', op.ROLES)
def test_mixed_source_rejected(role):
    states = {r: {'pid': 50+i, 'active': 'active', 'source': '810'} for i, r in enumerate(op.ROLES)}
    states[role]['source'] = 'd8a'
    with pytest.raises(op.Blocked, match='RAG_OP_MIXED_LIVE_SOURCE'): op.validate_live(states, '810')


def test_journal_unknown_or_reordered_phase_rejected():
    b = ComponentBackend()
    op.ExactOperator(b).activate()
    events = b.events()
    events[0]['phase'] = 'COMMITTED'
    with pytest.raises(op.Blocked, match='RAG_OP_EVENT_BINDING'): op.validate_journal(events, b.binding)


@pytest.mark.parametrize('property', ['previous', 'binding', 'sequence', 'admitted'])
def test_journal_tamper_is_not_resumed(property):
    b = ComponentBackend()
    op.ExactOperator(b).activate()
    events = b.events()
    events[-1][property] = {'previous': '0'*64, 'binding': '0'*64, 'sequence': 100, 'admitted': False}[property]
    with pytest.raises(op.Blocked): op.validate_journal(events, b.binding)


def environment():
    return ('APP_ENV=test\nENTERPRISE_POC_DATABASE_URL=postgresql://enterprise_agent_test:fixture@127.0.0.1:55432/enterprise_agent_test\n'
        'REDIS_URL=redis://enterprise_agent_test:fixture@127.0.0.1:56379/0\n'
        'ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA=false\nENTERPRISE_POC_TASK_QUEUE=redis\n'
        'ENTERPRISE_POC_MODEL_PROVIDER_ID=disabled-test-provider\n'
        'ENTERPRISE_POC_MODEL_BASE_URL=http://127.0.0.1:9/disabled\n')


def test_environment_data_not_shell_code_or_provider_secret():
    result = n.parse_test_environment((environment() + 'PYTHONPATH=/tmp/evil\nLD_PRELOAD=/tmp/evil.so\nN1N_IMAGE_API_KEY=fixture\n').encode())
    assert not {'PYTHONPATH', 'LD_PRELOAD', 'N1N_IMAGE_API_KEY'} & set(result)
    assert result['EMBEDDING_PROVIDER'] == 'local-hash' and result['EMBEDDING_DIMENSION'] == '128'


@pytest.mark.parametrize('from_value,to_value', [
    ('127.0.0.1:55432', 'production:5432'), ('127.0.0.1:56379', 'production:6379'),
    ('/enterprise_agent_test\n', '/enterprise_agent_test?options=evil\n'),
    ('APP_ENV=test', 'APP_ENV=production'), ('disabled-test-provider', 'deepseek')])
def test_wrong_environment_rejected(from_value, to_value):
    with pytest.raises(op.Blocked): n.parse_test_environment(environment().replace(from_value, to_value).encode())


def pair():
    return {'application': dict(op.APPLICATION, path=str(n.HOME / 'releases/810-candidate')),
            'tooling': {'source': 'a'*40, 'tree': 'b'*40, 'path': str(n.HOME / 'shared/source-qualifications/operator')}}


def policy():
    p = pair()
    return {'contract': op.VERSION, 'environment': 'test', 'production_authority': False, 'pair_sha256': 'c'*64,
        'application': copy.deepcopy(op.APPLICATION), 'tooling': {k: p['tooling'][k] for k in ('source','tree')},
        'predecessor': copy.deepcopy(op.PREDECESSOR), 'operation_id': 'a792391d-73ad-4701-b21f-bf4ec622c4ef',
        'not_before': '2026-10-11T00:00:00+00:00', 'expires_at': '2026-10-11T01:00:00+00:00',
        'code_sha256': {k: '0'*64 for k in n.CODE}, 'original_units': {r: {} for r in op.ROLES},
        'target_profiles': {r:'0'*64 for r in op.ROLES}, 'dependency_files': {}, 'runtime_files': {},
        'original_seeding_approval_sha256': '0'*64, 'root_runtime': {}, 'test_environment_file': {},
        'budget': {'provider':0,'wechat':0,'image':0}}


def authority():
    p = policy()
    approval = {'contract': op.VERSION, 'policy_sha256': 'd'*64,
        'authorization':'RAG_810_EXACT_TEST_MAINTENANCE_INDEPENDENTLY_APPROVED',
        'production_authority':False, 'native_acceptance_sha256':'e'*64}
    accepted = {'contract':op.VERSION,'pair_sha256':'c'*64, 'application': copy.deepcopy(op.APPLICATION),
        'tooling':p['tooling'],'predecessor':copy.deepcopy(op.PREDECESSOR),
        'evidence_kind':'ISOLATED_NATIVE_RELEASE_RECOVERY_ACCEPTED','component_tests_are_native_evidence':False}
    return p, approval, accepted


def test_exact_authority_shape_component_only():
    p, a, e = authority()
    n.validate_authority(p,a,e,pair(),'c'*64,'d'*64,'e'*64)


@pytest.mark.parametrize('key,value', [('application', {'source':'0'*40,'tree':'0'*40}),
    ('predecessor',{'source':'0'*40,'tree':'0'*40}), ('environment','production'),
    ('production_authority',True), ('budget',{'provider':1,'wechat':0,'image':0})])
def test_wrong_policy_rejected(key, value):
    p,a,e = authority(); p[key] = value
    with pytest.raises(op.Blocked): n.validate_authority(p,a,e,pair(),'c'*64,'d'*64,'e'*64)


@pytest.mark.parametrize('key,value', [('authorization','OLD_2C91_APPROVED'),('policy_sha256','0'*64),
    ('native_acceptance_sha256','0'*64),('production_authority',True)])
def test_no_old_or_unbound_approval(key,value):
    p,a,e = authority(); a[key] = value
    with pytest.raises(op.Blocked): n.validate_authority(p,a,e,pair(),'c'*64,'d'*64,'e'*64)


def test_component_evidence_cannot_be_native_acceptance():
    p,a,e = authority(); e['evidence_kind'] = '149_COMPONENT_PASS'
    with pytest.raises(op.Blocked, match='RAG_OP_NATIVE_ACCEPTANCE_REQUIRED'):
        n.validate_authority(p,a,e,pair(),'c'*64,'d'*64,'e'*64)


@pytest.mark.parametrize('role', op.ROLES)
def test_profile_fixed_existing_services_and_target_source(role):
    result = n.profile(pair(),role).decode()
    assert '810-candidate/enterprise_agent_poc' in result
    assert ' pre '+role in result and ' post '+role in result
    assert 'ENTERPRISE_POC_WECHAT_NETWORK_ALLOWED=false' in result
    assert 'ENTERPRISE_POC_TEST_RAG_NATIVE_REQUIRED=true' in result
    assert 'APP_ENV=test' in result


def test_profile_injection_rejected():
    value = pair(); value['application']['path'] += '\nEnvironment=APP_ENV=production'
    with pytest.raises(op.Blocked, match='RAG_OP_PROFILE_PATH_SHAPE'): n.profile(value,'api')


def test_real_posix_switch_reuses_actual_sealed_formal_body_only_in_temp(tmp_path, monkeypatch):
    # Native authority/DB/services are NOT exercised by this primitive test.
    old, new = tmp_path/'d8a', tmp_path/'810'
    old.mkdir(); new.mkdir(); (tmp_path/'current').symlink_to(old)
    backend = object.__new__(n.NativeBackend)
    backend.pair = {'application':{'path':str(new)}}
    monkeypatch.setattr(n,'HOME',tmp_path); monkeypatch.setattr(n,'PRED',old)
    if not n.FORMAL.exists(): pytest.fail('Required read-only sealed formal primitive unavailable')
    assert n.sha(n.read(n.FORMAL)) == n.FORMAL_SHA
    fn = backend._formal('switch_pointer')
    fn(new); assert (tmp_path/'current').resolve() == new
    fn(old); assert (tmp_path/'current').resolve() == old
    with pytest.raises(op.Blocked,match='POINTER_TARGET'): fn(tmp_path/'other')


def test_no_database_migration_or_synthetic_cleanup_source():
    raw = Path(n.__file__).read_text(encoding='utf-8')
    for forbidden in ['DELETE FROM','INSERT INTO','UPDATE ', 'DROP TABLE','migrate.py',
                      '--exact-test-tenant-cleanup','nginx.service','provision_test_tenant']:
        assert forbidden not in raw
    assert "'enterprise-agent-test-'" in raw
    assert 'enterprise-agent-native-isolated-' not in raw


def test_real_flock_nonblocking_existing_inode_with_mock_root_metadata(tmp_path, monkeypatch):
    path = tmp_path/'component-only.lock'
    path.touch(mode=0o600)
    original_stat, original_fstat = Path.stat, n.os.fstat
    def root_stat(p, *a, **k):
        st = original_stat(p, *a, **k)
        if p == tmp_path: return SimpleNamespace(st_uid=0,st_gid=0,st_mode=st.st_mode)
        return st
    def root_fstat(fd):
        st = original_fstat(fd)
        return SimpleNamespace(st_uid=0,st_gid=0,st_mode=st.st_mode,st_nlink=st.st_nlink,
                               st_dev=st.st_dev,st_ino=st.st_ino)
    monkeypatch.setattr(n,'LOCK',path)
    monkeypatch.setattr(n.os,'geteuid',lambda:0)  # authority simulation, NOT native privilege
    monkeypatch.setattr(Path,'stat',root_stat)
    monkeypatch.setattr(n.os,'fstat',root_fstat)
    a,b = object.__new__(n.NativeBackend),object.__new__(n.NativeBackend)
    with a.lock():
        with pytest.raises(op.Blocked,match='RAG_OP_RELEASE_SWITCH_LOCKED'):
            with b.lock(): pytest.fail('Concurrent lock acquired')
    with b.lock(): assert b.lock_inode == path.lstat().st_ino


@pytest.fixture
def independent_redis(tmp_path):
    """Temporary Unix socket/port0, NO primary Redis, NO DB/Registry writes."""
    from redis import Redis
    binary = Path('/opt/enterprise-agent-workbench-test/shared/runtime/redis-7.0.15/bin/redis-server')
    assert binary.exists(), 'Independent Redis primitive runtime unavailable'
    assert 'v=7.0.15' in subprocess.check_output([str(binary),'--version'],text=True)
    fixture = tmp_path/'independent-redis'
    fixture.mkdir(mode=0o700)
    (fixture/'isolated-component.marker').write_text('rag810-redis-primitive-only')
    process = subprocess.Popen([str(binary),'--port','0','--unixsocket',str(fixture/'redis.sock'),
        '--unixsocketperm','700','--save','','--appendonly','no','--dir',str(fixture),
        '--logfile',str(fixture/'redis.log')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    client = Redis(unix_socket_path=str(fixture/'redis.sock'),decode_responses=True)
    try:
        deadline=time.monotonic()+5
        while True:
            try:
                if client.ping(): break
            except Exception:
                assert time.monotonic()<deadline and process.poll() is None, 'Independent Redis not ready'
                time.sleep(.05)
        yield client
    finally:
        client.close()
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.parametrize('drift',[False,True])
def test_real_redis_atomic_lease_preservation_and_cas(independent_redis,monkeypatch,drift):
    client=independent_redis
    keys=['fixture:knowledge:processing','fixture:knowledge:pending','fixture:tasks:pending','fixture:tasks:processing']
    client.rpush(keys[0],'fixture-tenant:f1','fixture-tenant:f2')
    client.rpush(keys[1],'fixture-tenant:f3')
    queue=SimpleNamespace(_client=client,knowledge_processing=keys[0],knowledge_pending=keys[1],
                          pending=keys[2],processing=keys[3])
    backend=object.__new__(n.NativeBackend)
    monkeypatch.setattr(backend,'assert_stopped',lambda:None)  # no service claim
    monkeypatch.setattr(backend,'queue_check',lambda initial:None)  # no native authority claim
    monkeypatch.setattr(backend,'validate_jobs',lambda jobs:None)  # mock metadata, NO database
    monkeypatch.setattr(backend,'queue',lambda:queue)
    original=client.eval
    def eval(*a,**k):
        if drift: client.lpush(keys[0],'fixture-tenant:unapproved-intervening')
        return original(*a,**k)
    monkeypatch.setattr(client,'eval',eval)
    if drift:
        from redis.exceptions import ResponseError
        with pytest.raises(ResponseError,match='RAG_OP_QUEUE_CAS_DRIFT'): backend.requeue_knowledge()
        assert client.lrange(keys[0],0,-1)==['fixture-tenant:unapproved-intervening','fixture-tenant:f1','fixture-tenant:f2']
        assert client.lrange(keys[1],0,-1)==['fixture-tenant:f3']
    else:
        backend.requeue_knowledge()
        assert client.llen(keys[0])==0
        assert sorted(client.lrange(keys[1],0,-1))==['fixture-tenant:f1','fixture-tenant:f2','fixture-tenant:f3']
