"""Component/contract tests ONLY. No Native Loader, services, DB or Provider."""
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import seeding_approval_selector as s
from scripts import seeding_release_binding as b
from scripts.wechat_runtime_test_lifecycle_guard import Blocked


class Interrupted(BaseException): pass


class ReleaseDouble:
    """Durable state on disposable disk; release/Loader/Guard are explicit doubles."""
    def __init__(self, root):
        self.root=Path(root)
        self.policy=dict(operation_id='c7700f71-d0ea-4ab3-b919-307569202a8d',target_authority_id='COMPONENT_ONLY_28E')
        self.old=s.canonical(dict(contract=s.CONTRACT,authority_id='COMPONENT_ONLY_D8A',environment='test',
            source_commit=s.IDENTITIES['d8a'][0],source_tree=s.IDENTITIES['d8a'][1],database=s.DATABASE,
            production_deploy_authority=False,budget=s.ZERO,exclusions=[]))
        self.policy['predecessor_approval_sha256']=s.sha(self.old)
        self.policy_sha=s.sha(s.canonical(self.policy))
        self.active_raw=self.old; self.current_version='d8a'; self.busy=False
        self.stopped=False; self.data='a'*64; self.compatible=True; self.known=True
        self.admin=0; self.config='unchanged'; self.fault=None; self.log=[]
    @contextmanager
    def lock(self):
        if self.busy: raise Blocked('SELECTOR_CONCURRENT_OPERATION')
        self.busy=True
        try: yield
        finally: self.busy=False
    def hit(self,name):
        self.log.append(name)
        if self.fault==name:
            self.fault=None
            raise Interrupted(name)
    def validate(self):
        if not self.known: raise Blocked('SELECTOR_AUTHORITY_CHANGED')
    def authorize(self,action,state): pass  # legacy component double, not Native authority
    def state(self):
        path=self.root/'state.json'
        return json.loads(path.read_bytes()) if path.exists() else None
    def save(self,value):
        temp=self.root/'state.next';temp.write_bytes(s.canonical(value));os.replace(temp,self.root/'state.json')
        self.hit('state:'+value['events'][-1]['phase'])
    def archive(self,version,raw):
        path=self.root/(version+'.json')
        if path.exists():
            if path.read_bytes()!=raw: raise Blocked('SELECTOR_IMMUTABLE_COLLISION')
        else: path.write_bytes(raw)
        self.hit('archive:'+version)
    def version(self,version): return (self.root/(version+'.json')).read_bytes()
    def active(self):return self.active_raw
    def current(self):return self.current_version
    def predecessor(self):
        if not self.compatible:raise Blocked('SELECTOR_PREDECESSOR_INCOMPATIBLE')
        self.hit('predecessor')
    def fingerprint(self):return self.data
    def quiesce(self):self.stopped=True;self.hit('quiesce')
    def assert_quiesced(self):
        if not self.stopped:raise Blocked('SELECTOR_NOT_QUIESCED')
    def install(self,raw):self.active_raw=raw;self.hit('install')
    def switch(self,version):self.current_version=version;self.hit('switch')
    def configure(self,version):self.hit('configure')
    def loader(self,version):s.approval(self.active_raw,version);self.hit('loader')
    def start(self,version):self.stopped=False;self.hit('start')
    def forward(self):self.admin=0;self.stopped=False;self.hit('forward')
    def verify_running(self,version,raw):
        if (self.current_version,self.active_raw)!=(version,raw):raise Blocked('SELECTOR_RUNNING_PAIR')
        if self.stopped:raise Blocked('SELECTOR_HEALTH_FAILED')
        self.loader(version);self.hit('health')


class SelectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.b=ReleaseDouble(self.tmp.name);self.x=s.Selector(self.b)
    def prepare(self):return self.x.prepare()
    def activate(self):self.prepare();return self.x.activate()
    def test_prepare_no_selection(self):
        self.prepare();self.assertEqual(self.b.active_raw,self.b.old);self.assertEqual(self.b.current(),'d8a')
    def test_new_independent_declaration(self):
        self.prepare();new=s.approval(self.b.version('28e'),'28e')
        self.assertEqual(new['authority_id'],'COMPONENT_ONLY_28E');self.assertEqual(new['exclusions'],[])
    def test_old_authority_not_reused_for_issuance(self):
        self.b.policy['target_authority_id']='COMPONENT_ONLY_D8A'
        with self.assertRaisesRegex(Blocked,'NEW_AUTHORITY_REQUIRED'):self.prepare()
        self.assertIsNone(self.b.state());self.assertEqual(self.b.active_raw,self.b.old)
    def test_old_bytes_immutable(self):
        self.activate();self.x.recover();self.assertEqual(self.b.version('d8a'),self.b.old)
    def test_prepare_idempotent(self):self.assertEqual(self.prepare(),self.prepare())
    def test_prepare_archive_collision(self):
        (self.b.root/'28e.json').write_bytes(b'forged')
        with self.assertRaisesRegex(Blocked,'IMMUTABLE'):self.prepare()
    def test_bad_source(self):
        v=json.loads(self.b.old);v['source_commit']='0'*40;self.b.active_raw=s.canonical(v)
        with self.assertRaisesRegex(Blocked,'APPROVAL_SOURCE'):self.prepare()
    def test_bad_tree(self):
        v=json.loads(self.b.old);v['source_tree']='0'*40;self.b.active_raw=s.canonical(v)
        with self.assertRaisesRegex(Blocked,'APPROVAL_SOURCE'):self.prepare()
    def test_production(self):
        v=json.loads(self.b.old);v['environment']='production';self.b.active_raw=s.canonical(v)
        with self.assertRaisesRegex(Blocked,'APPROVAL_DOMAIN'):self.prepare()
    def test_production_authority(self):
        v=json.loads(self.b.old);v['production_deploy_authority']=True
        with self.assertRaises(Blocked):s.approval(s.canonical(v),'d8a')
    def test_wrong_database(self):
        v=json.loads(self.b.old);v['database']['port']=5432
        with self.assertRaises(Blocked):s.approval(s.canonical(v),'d8a')
    def test_nonzero_provider_budget(self):
        v=json.loads(self.b.old);v['budget']['provider']=1
        with self.assertRaises(Blocked):s.approval(s.canonical(v),'d8a')
    def test_boolean_budget(self):
        v=json.loads(self.b.old);v['budget']['provider']=False
        with self.assertRaises(Blocked):s.approval(s.canonical(v),'d8a')
    def test_nonempty_exclusions_not_silently_removed(self):
        v=json.loads(self.b.old);v['exclusions']=[{}]
        with self.assertRaisesRegex(Blocked,'ZERO_EXCLUSION'):s.approval(s.canonical(v),'d8a')
    def test_unknown_approval_field(self):
        v=json.loads(self.b.old);v['ignore_source']=True
        with self.assertRaises(Blocked):s.approval(s.canonical(v),'d8a')
    def test_old_pin_drift(self):
        self.b.policy['predecessor_approval_sha256']='0'*64
        with self.assertRaises(Blocked):self.prepare()
    def test_wrong_predecessor(self):
        self.b.current_version='28e'
        with self.assertRaises(Blocked):self.prepare()
    def test_target_selection(self):
        state=self.activate();self.assertEqual(state['events'][-1]['phase'],'COMPLETE')
        self.assertEqual(self.b.active(),self.b.version('28e'))
    def test_activate_idempotent(self):
        state=self.activate();self.assertEqual(self.x.activate(),state)
    def test_activate_requires_prepare(self):
        with self.assertRaises(Blocked):self.x.activate()
    def test_no_new_data_exact_rollback(self):
        self.activate();state=self.x.recover()
        self.assertEqual(state['events'][-1]['direction'],'d8a');self.assertEqual(self.b.active(),self.b.old)
    def test_new_lifecycle_must_forward(self):
        self.activate();self.b.data='b'*64;self.b.admin=1;state=self.x.recover()
        self.assertEqual(state['events'][-1]['direction'],'28e');self.assertIn('forward',self.b.log)
        self.assertEqual(self.b.admin,0);self.assertEqual(self.b.config,'unchanged')
    def test_old_guard_rejects_rollback(self):
        self.activate();self.b.compatible=False
        with self.assertRaisesRegex(Blocked,'PREDECESSOR_INCOMPATIBLE'):self.x.recover()
        self.assertEqual(self.b.current(),'28e')
    def test_unknown_active_file_reject(self):
        self.prepare();self.b.active_raw=b'forged'
        with self.assertRaisesRegex(Blocked,'UNKNOWN_ACTIVE'):self.x.activate()
    def test_forged_target_archive(self):
        self.prepare();v=json.loads(self.b.version('28e'));v['authority_id']='foreign'
        (self.b.root/'28e.json').write_bytes(s.canonical(v))
        with self.assertRaisesRegex(Blocked,'ISSUANCE'):self.x.activate()
    def test_concurrent_operation_rejects(self):
        with self.b.lock():
            with self.assertRaisesRegex(Blocked,'CONCURRENT'):self.prepare()
    def test_changed_authority_rejects(self):
        self.prepare();self.b.known=False
        with self.assertRaises(Blocked):self.x.activate()
    def test_operation_state_identity(self):
        self.prepare();v=self.b.state();v['operation_id']='different';self.b.save(v)
        with self.assertRaisesRegex(Blocked,'STATE_BINDING'):self.x.status()
    def test_history_hash_tamper(self):
        self.activate();v=self.b.state();v['events'][0]['at']='tampered';self.b.save(v)
        with self.assertRaisesRegex(Blocked,'STATE_EVENT'):self.x.status()
    def test_forged_phase_even_with_valid_chain(self):
        self.prepare();v=self.b.state();v['events'][0]['phase']='COMPLETE';self.b.save(v)
        with self.assertRaisesRegex(Blocked,'STATE_EVENT'):self.x.status()
    def test_prepared_recover_does_not_stop_services(self):
        self.prepare();before=list(self.b.log);self.x.recover();self.assertEqual(self.b.log,before)
    def test_failed_loader_stays_recoverable(self):
        self.prepare()
        with patch.object(self.b,'loader',side_effect=Blocked('REAL_LOADER_FAILED')):
            with self.assertRaises(Blocked):self.x.activate()
        self.assertEqual(self.b.state()['events'][-1]['phase'],'SWITCHING')
        self.assertEqual(self.x.recover()['events'][-1]['direction'],'d8a')
    def test_recovery_interrupted_then_persisted_resume(self):
        self.activate();self.b.fault='install'
        with self.assertRaises(Interrupted):self.x.recover()
        self.assertEqual(s.Selector(self.b).recover()['events'][-1]['phase'],'RECOVERED')
    def test_prepare_interrupted_archive_replay(self):
        self.b.fault='archive:28e'
        with self.assertRaises(Interrupted):self.prepare()
        self.assertIsNone(self.b.state());self.assertEqual(self.b.active(),self.b.old)
        self.assertEqual(self.prepare()['events'][-1]['phase'],'PREPARED')
    def test_persisted_recovery_new_selector_instance(self):
        self.prepare();self.b.fault='switch'
        with self.assertRaises(Interrupted):self.x.activate()
        self.assertEqual(self.b.state()['events'][-1]['phase'],'SWITCHING')
        result=s.Selector(self.b).recover()
        self.assertEqual(result['events'][-1]['phase'],'RECOVERED')
    def test_repeated_recovery_no_extra_events(self):
        self.activate();a=self.x.recover();self.assertEqual(self.x.recover(),a)
    def test_forward_failure_not_downgraded(self):
        self.activate();self.b.data='b'*64;self.b.fault='forward'
        with self.assertRaises(Interrupted):self.x.recover()
        self.assertEqual(self.b.state()['events'][-1]['direction'],'28e')
        self.assertEqual(self.x.recover()['events'][-1]['direction'],'28e')
    def test_recovery_data_drift_rejects_old_direction(self):
        self.activate();self.b.fault='state:RECOVERING'
        with self.assertRaises(Interrupted):self.x.recover()
        self.b.data='b'*64
        with self.assertRaisesRegex(Blocked,'DATA_DRIFT'):self.x.recover()
    def test_status_read_only(self):
        self.prepare();before=(self.b.root/'state.json').read_bytes();self.x.status()
        self.assertEqual((self.b.root/'state.json').read_bytes(),before)
    def test_status_complete_pair_mismatch(self):
        self.activate();self.b.active_raw=self.b.old
        with self.assertRaisesRegex(Blocked,'STATUS_PAIR_MISMATCH'):self.x.status()


def interruption_test(point):
    def test(self):
        self.prepare();self.b.fault=point
        with self.assertRaises(Interrupted):self.x.activate()
        self.x.recover()
        self.assertEqual(self.b.current(),'d8a');self.assertEqual(self.b.active(),self.b.old)
        self.assertEqual(self.b.admin,0)
        self.assertFalse(self.b.stopped)
    return test
for point in ('state:QUIESCING','quiesce','state:QUIESCED','state:SWITCHING','install','switch','configure','loader',
              'state:SELECTED','start','health','state:COMPLETE'):
    setattr(SelectorTests,'test_interruption_'+point.replace(':','_'),interruption_test(point))


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.policy=dict(contract=s.VERSION,environment='test',production_authority=False,
            operation_id='c7700f71-d0ea-4ab3-b919-307569202a8d',
            applications={v:dict(source=i[0],tree=i[1],path=str(b.HOME/'releases'/(
                '20261008-d8a7814-wechat-personal-v1' if v=='d8a' else 'target-28e')),files_sha256='1'*64)
                for v,i in s.IDENTITIES.items()},
            tooling=dict(source='2'*40,tree='3'*40,path=str(b.HOME/'shared/source-qualifications/tool'),files_sha256='4'*64),
            predecessor_approval_sha256='5'*64,target_authority_id='TEST_ONLY_28E',tenant_ids=['exact-tenant'],
            guard_sha256='6'*64,formal_switch_sha256=b.FORMAL_SHA,
            profiles={v:dict.fromkeys(b.ROLES,'7'*64) for v in s.IDENTITIES})
        self.approval=dict(contract=s.VERSION,policy_sha256='a'*64,
            authorization='FORMAL_SEEDING_APPROVAL_VERSIONED_SELECTION_FIX_APPROVED',production_authority=False,
            code_sha256=dict.fromkeys(b.CODE,'b'*64))
    def check(self):b.shape(self.policy,self.approval,'a'*64)
    def test_exact_policy(self):self.check()
    def test_source_rejected(self):
        self.policy['applications']['28e']['source']='0'*40
        with self.assertRaises(Blocked):self.check()
    def test_tree_rejected(self):
        self.policy['applications']['28e']['tree']='0'*40
        with self.assertRaises(Blocked):self.check()
    def test_environment_rejected(self):
        self.policy['environment']='production'
        with self.assertRaises(Blocked):self.check()
    def test_production_authority_rejected(self):
        self.approval['production_authority']=True
        with self.assertRaises(Blocked):self.check()
    def test_unknown_code_pin(self):
        self.approval['code_sha256']['unknown.py']='c'*64
        with self.assertRaises(Blocked):self.check()
    def test_policy_pin_drift(self):
        self.approval['policy_sha256']='d'*64
        with self.assertRaises(Blocked):self.check()
    def test_no_wildcard_tenant(self):
        self.policy['tenant_ids']=['*']
        with self.assertRaises(Blocked):self.check()
    def test_no_wrong_release_root(self):
        self.policy['applications']['28e']['path']='/tmp/foreign'
        with self.assertRaises(Blocked):self.check()
    def test_no_wrong_tooling_root(self):
        self.policy['tooling']['path']='/tmp/foreign'
        with self.assertRaises(Blocked):self.check()
    def test_no_wrong_switch_code(self):
        self.policy['formal_switch_sha256']='0'*64
        with self.assertRaises(Blocked):self.check()
    def test_native_gate_not_granted_by_code(self):
        backend=object.__new__(b.NativeRelease);backend.approval=self.approval
        with self.assertRaisesRegex(Blocked,'NATIVE_RELEASE_GATE_REQUIRED'):backend.require_native_acceptance()
    def test_forged_native_gate(self):
        backend=object.__new__(b.NativeRelease);backend.approval=self.approval
        backend.policy_sha='a'*64
        with patch.object(Path,'is_file',return_value=True),patch.object(b,'read',return_value=b'{}'):
            with self.assertRaisesRegex(Blocked,'NATIVE_RELEASE_GATE_REQUIRED'):backend.require_native_acceptance()
    def gate_fixture(self):
        """Explicit doubles; these files are NEVER issued to Native Authority."""
        backend=object.__new__(b.NativeRelease);backend.policy=self.policy
        backend.policy_sha=s.sha(s.canonical(self.policy))
        self.approval['policy_sha256']=backend.policy_sha;backend.approval=self.approval
        trial_raw=b'COMPONENT_ONLY_TRIAL_APPROVAL'
        with tempfile.TemporaryDirectory() as root:
            double=ReleaseDouble(root);double.policy=self.policy;double.policy_sha=backend.policy_sha
            double.stage='trial';double.trial_sha=s.sha(trial_raw)
            selector=s.Selector(double);receipt=None
            for phase in ('PREPARED','QUIESCING','QUIESCED','SWITCHING','SELECTED','COMPLETE','TRIAL_CLOSED'):
                receipt=selector.checkpoint(receipt,phase,'d8a' if phase in ('PREPARED','QUIESCING','QUIESCED') else '28e',
                    None if phase in ('PREPARED','QUIESCING') else 'a'*64)
        evidence=dict(contract=s.VERSION,policy_sha256=backend.policy_sha,status='NATIVE_RELEASE_ACCEPTED',
            trial_receipt_sha256=s.sha(s.canonical(receipt)),
            checks=dict.fromkeys(('d8a_loader','28e_loader','switch_recovery',
                'post_write_forward_recovery','api_mcp_worker','secret_preservation'),'PASS'))
        activation=dict(contract=s.VERSION,policy_sha256=backend.policy_sha,
            issuance_approval_sha256=s.sha(s.canonical(self.approval)),native_acceptance_sha256=s.sha(s.canonical(evidence)),
            authorization='SEEDING_SELECTOR_NATIVE_RELEASE_ACCEPTED_V1',production_authority=False)
        files={b.ROOT/'policy.v1.json':s.canonical(self.policy),b.ROOT/'approval.v1.json':s.canonical(self.approval),
               b.ROOT/'trial-receipt.v1.json':s.canonical(receipt),b.ROOT/'trial-approval.v1.json':trial_raw,
               b.ROOT/'native-acceptance.v1.json':s.canonical(evidence),
               b.ROOT/'activation-approval.v1.json':s.canonical(activation)}
        return backend,files
    def test_exact_native_acceptance_contract_double(self):
        backend,files=self.gate_fixture()
        with patch.object(Path,'is_file',return_value=True),patch.object(b,'read',side_effect=files.__getitem__):
            backend.require_native_acceptance()
    def test_native_acceptance_hash_drift(self):
        backend,files=self.gate_fixture();files[b.ROOT/'native-acceptance.v1.json']+=b' '
        with patch.object(Path,'is_file',return_value=True),patch.object(b,'read',side_effect=files.__getitem__):
            with self.assertRaises(Blocked):backend.require_native_acceptance()
    def test_native_acceptance_wrong_policy(self):
        backend,files=self.gate_fixture();backend.policy_sha='0'*64
        with patch.object(Path,'is_file',return_value=True),patch.object(b,'read',side_effect=files.__getitem__):
            with self.assertRaises(Blocked):backend.require_native_acceptance()
    def test_native_issuance_approval_not_rewriteable(self):
        backend,files=self.gate_fixture();files[b.ROOT/'approval.v1.json']+=b' '
        with patch.object(Path,'is_file',return_value=True),patch.object(b,'read',side_effect=files.__getitem__):
            with self.assertRaises(Blocked):backend.require_native_acceptance()
    def startup_fixture(self):
        backend,files=self.gate_fixture()
        with tempfile.TemporaryDirectory() as root:
            double=ReleaseDouble(root);double.policy=self.policy;double.policy_sha=backend.policy_sha
            double.stage='activation';x=s.Selector(double)
            state=json.loads(files[b.ROOT/'trial-receipt.v1.json'])
            for phase in ('FINAL_AUTHORIZED','SWITCHING','SELECTED'):
                state=x.checkpoint(state,phase,'28e','a'*64)
        files[b.ROOT/'state.v1.json']=s.canonical(state)
        files[b.ACTIVE]=files[b.ROOT/'28e.approval.v1.json']=s.issue_target(self.policy)
        pair=dict(application=self.policy['applications']['28e'],tooling=self.policy['tooling'])
        return files,pair
    def test_startup_selected_pair_double(self):
        files,pair=self.startup_fixture()
        with patch.object(Path,'exists',return_value=True),patch.object(Path,'is_file',return_value=True),\
             patch.object(b,'read',side_effect=files.__getitem__):b.assert_selected(pair)
    def test_startup_wrong_tooling(self):
        files,pair=self.startup_fixture();pair['tooling']=dict(pair['tooling'],source='0'*40)
        with patch.object(Path,'exists',return_value=True),patch.object(b,'read',side_effect=files.__getitem__):
            with self.assertRaisesRegex(Blocked,'STARTUP_PAIR'):b.assert_selected(pair)
    def test_startup_tampered_state_chain(self):
        files,pair=self.startup_fixture();state=json.loads(files[b.ROOT/'state.v1.json'])
        state['events'][0]['at']='forged';files[b.ROOT/'state.v1.json']=s.canonical(state)
        with patch.object(Path,'exists',return_value=True),patch.object(Path,'is_file',return_value=True),\
             patch.object(b,'read',side_effect=files.__getitem__):
            with self.assertRaisesRegex(Blocked,'STATE_EVENT'):b.assert_selected(pair)
    def test_startup_wrong_active_approval(self):
        files,pair=self.startup_fixture();files[b.ACTIVE]=b'forged'
        with patch.object(Path,'exists',return_value=True),patch.object(Path,'is_file',return_value=True),\
             patch.object(b,'read',side_effect=files.__getitem__):
            with self.assertRaisesRegex(Blocked,'STARTUP_APPROVAL'):b.assert_selected(pair)
    def test_forward_interface_unchanged(self):
        backend=object.__new__(b.NativeRelease)
        with patch.object(backend,'recovery') as call:
            backend.forward();call.assert_called_once_with('recover')
    def test_target_startup_interface(self):
        backend=object.__new__(b.NativeRelease)
        with patch.object(backend,'recovery') as call:
            backend.start('28e');call.assert_called_once_with('startup')
    def test_formal_entry_reused(self):
        source=(Path(__file__).parent/'runtime_recovery_operator.py').read_text(encoding='utf-8')
        self.assertIn("startswith('seeding-')",source);self.assertIn('assert_selected(pair,',source)


# POSIX filesystem primitives need their real platform. Windows runs only the
# portable component cases; the official component count is from Linux/root.
if os.name=='posix':
    class PosixFilesTests(unittest.TestCase):
        def setUp(self):
            self.assertEqual(os.geteuid(),0,'Run POSIX file component suite as local isolated root')
            self.temp=tempfile.TemporaryDirectory(prefix='seeding-selector-component-',dir='/run')
            self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        def test_atomic_readonly_bytes(self):
            path=self.root/'active.json';b.atomic(path,b'old');b.atomic(path,b'new')
            self.assertEqual(b.read(path),b'new');self.assertEqual(path.stat().st_mode & 0o777,0o444)
        def test_immutable_idempotent(self):
            path=self.root/'old.json';b.atomic(path,b'old',immutable=True);inode=path.stat().st_ino
            b.atomic(path,b'old',immutable=True);self.assertEqual(path.stat().st_ino,inode)
            with self.assertRaisesRegex(Blocked,'IMMUTABLE_COLLISION'):b.atomic(path,b'other',immutable=True)
            self.assertEqual(b.read(path),b'old')
        def test_atomic_replace_interruption(self):
            path=self.root/'active.json';b.atomic(path,b'old')
            with patch.object(b.os,'replace',side_effect=Interrupted('replace')):
                with self.assertRaises(Interrupted):b.atomic(path,b'new')
            self.assertEqual(b.read(path),b'old')
            b.atomic(path,b'new');self.assertEqual(b.read(path),b'new')
        def test_directory_fsync_interruption(self):
            path=self.root/'active.json';b.atomic(path,b'old')
            with patch.object(b,'fsync_dir',side_effect=Interrupted('fsync')):
                with self.assertRaises(Interrupted):b.atomic(path,b'new')
            self.assertEqual(b.read(path),b'new')
            b.atomic(path,b'new');self.assertEqual(b.read(path),b'new')
        def test_symlink_rejected(self):
            target=self.root/'target';b.atomic(target,b'protected');path=self.root/'link';path.symlink_to(target)
            with self.assertRaises(Blocked):b.atomic(path,b'unsafe')
            self.assertEqual(b.read(target),b'protected')
        def test_writable_ancestor_rejected(self):
            sub=self.root/'sub';sub.mkdir(mode=0o777);sub.chmod(0o777)
            with self.assertRaisesRegex(Blocked,'WRITE_PARENT_TRUST'):b.atomic(sub/'file',b'x')
        def test_writable_authority_rejected(self):
            path=self.root/'file';path.write_bytes(b'x');path.chmod(0o644)
            with self.assertRaises(Blocked):b.read(path)
        def test_real_flock_conflict(self):
            one=object.__new__(b.NativeRelease);two=object.__new__(b.NativeRelease)
            with patch.object(b,'LOCK',self.root/'lock'):
                with one.lock():
                    with self.assertRaisesRegex(Blocked,'CONCURRENT_OPERATION'):
                        with two.lock():pass
                with two.lock():pass
        def test_trial_real_inherited_flock(self):
            one=object.__new__(b.NativeRelease)
            with patch.object(b,'LOCK',self.root/'lock'):
                with one.lock(),patch.dict(os.environ,{'SELECTOR_TRIAL_LOCK_FD':str(one.lock_fd)}):
                    b.verify_inherited_lock()
        def test_trial_wrong_open_description_rejected(self):
            one=object.__new__(b.NativeRelease)
            with patch.object(b,'LOCK',self.root/'lock'):
                with one.lock():
                    other=os.open(b.LOCK,os.O_RDWR)
                    try:
                        with patch.dict(os.environ,{'SELECTOR_TRIAL_LOCK_FD':str(other)}):
                            with self.assertRaisesRegex(Blocked,'LOCK_NOT_OWNED'):b.verify_inherited_lock()
                    finally:os.close(other)
        def test_trial_unlocked_descriptor_rejected(self):
            path=self.root/'lock';path.touch(mode=0o600);fd=os.open(path,os.O_RDWR)
            try:
                with patch.object(b,'LOCK',path),patch.dict(os.environ,{'SELECTOR_TRIAL_LOCK_FD':str(fd)}):
                    with self.assertRaisesRegex(Blocked,'LOCK_NOT_HELD'):b.verify_inherited_lock()
            finally:os.close(fd)


if __name__=='__main__':
    result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
        skipped=len(result.skipped),evidence='COMPONENT_ONLY_NATIVE_LOADER_NOT_VERIFIED')))
    raise SystemExit(not result.wasSuccessful())
