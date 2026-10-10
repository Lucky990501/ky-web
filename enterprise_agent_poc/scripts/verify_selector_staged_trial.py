"""Explicit components only: NEVER issues Native authority or starts services."""
from contextlib import ExitStack
from datetime import datetime, timezone, timedelta
import copy
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import seeding_approval_selector as s, seeding_release_binding as b
from scripts import verify_seeding_approval_selector as old
from scripts.wechat_runtime_test_lifecycle_guard import Blocked


def fence_fixture():
    lines={
        'INPUT':['-P INPUT ACCEPT','-A INPUT -p tcp -m multiport --dports 18100,18101 -j WB_SELECTOR_TRIAL_IN'],
        'OUTPUT':['-P OUTPUT ACCEPT','-A OUTPUT -p tcp -m multiport --dports 18100,18101 -j WB_SELECTOR_TRIAL_OUT'],
        'WB_SELECTOR_TRIAL_IN':['-N WB_SELECTOR_TRIAL_IN','-A WB_SELECTOR_TRIAL_IN -i lo -j ACCEPT',
            '-A WB_SELECTOR_TRIAL_IN -p tcp -j REJECT --reject-with tcp-reset'],
        'WB_SELECTOR_TRIAL_OUT':['-N WB_SELECTOR_TRIAL_OUT','-A WB_SELECTOR_TRIAL_OUT -m owner --uid-owner 0 -j ACCEPT',
            '-A WB_SELECTOR_TRIAL_OUT -p tcp -m tcp --dport 18101 -m owner --uid-owner 1000 -j ACCEPT',
            '-A WB_SELECTOR_TRIAL_OUT -p tcp -j REJECT --reject-with tcp-reset']}
    return {k:[shlex.split(line) for line in v] for k,v in lines.items()}


class TrialDouble(old.ReleaseDouble):
    """REAL authority/state validators; filesystem/service execution are doubles."""
    authorize=b.NativeRelease.authorize
    require_trial=b.NativeRelease.require_trial
    require_native_acceptance=b.NativeRelease.require_native_acceptance
    require_final_state=b.NativeRelease.require_final_state
    def close_trial(self,state):
        raw=s.canonical(state)
        if 'receipt' in self.files and self.files['receipt']!=raw: raise Blocked('IMMUTABLE_COLLISION')
        self.files['receipt']=raw;self.hit('receipt')
    def trial_fence(self): b.verify_trial_fence(self.fence)
    def quiesce(self):
        self.authorize(self.purpose,self.state())
        super().quiesce()


class TrialTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.d=TrialDouble(temp.name);self.d.stage='trial';self.d.fence=fence_fixture();self.d.files={}
        fixture=old.BindingTests();fixture.setUp()
        self.d.policy=fixture.policy;self.d.policy['predecessor_approval_sha256']=s.sha(self.d.old)
        self.d.policy_sha=s.sha(s.canonical(self.d.policy));self.d.approval=fixture.approval
        self.d.approval['policy_sha256']=self.d.policy_sha
        self.x=s.Selector(self.d);self.now=datetime.now(timezone.utc)
        self.trial=dict(contract=b.TRIAL,authorization='SEEDING_SELECTOR_CONTROLLED_NATIVE_TRIAL_APPROVED',
            environment='test',production_authority=False,policy_sha256=self.d.policy_sha,
            issuance_approval_sha256=s.sha(s.canonical(self.d.approval)),
            **{k:copy.deepcopy(self.d.policy[k]) for k in ('applications','tooling','predecessor_approval_sha256','tenant_ids','operation_id')},
            database=s.DATABASE,code_sha256=self.d.approval['code_sha256'],
            recovery_pins=dict.fromkeys(('forward-recovery-policy.v1.json','forward-recovery-approval.v1.json',
                'runtime-pair.v1.json','scope.v1.json','approval.v2.json','native-policy.v2.json','native-approval.v2.json'),s.sha(b'PINNED_COMPONENT')),
            not_before=(self.now-timedelta(minutes=5)).isoformat(),execute_until=(self.now+timedelta(minutes=30)).isoformat(),
            recover_until=(self.now+timedelta(hours=2)).isoformat(),operations=b.TRIAL_ACTIONS,
            business_authority=False,test_write_authority=False,network_fence='ROOT_API_LOCAL_MCP_ONLY_V1',budget=s.ZERO)
        self.status='ACTIVE';self.files={}
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(b,'read',side_effect=self.read))
        stack.enter_context(patch.object(b.os,'geteuid',return_value=0))
        stack.enter_context(patch.dict(os.environ,{'APP_ENV':'test'}))
        original_exists=Path.exists
        stack.enter_context(patch.object(Path,'exists',autospec=True,
            side_effect=lambda p:True if p==b.ROOT or b.ROOT in p.parents else original_exists(p)))
        stack.enter_context(patch.object(Path,'is_file',side_effect=lambda p:True,autospec=True))
        # Native status uses these I/O doubles, not an invented Guard PASS.
        stack.enter_context(patch.object(b.NativeRelease,'trial_fence',side_effect=lambda:b.verify_trial_fence(self.d.fence)))
        stack.enter_context(patch.object(b.NativeRelease,'trial_runtime_bounds'))
        stack.enter_context(patch.object(b.NativeRelease,'trial_queue_boundary'))
    def read(self,path):
        if path==b.ACTIVE:return self.d.active_raw
        if path.parent==b.RECOVERY_AUTH:return b'PINNED_COMPONENT'
        names={'policy.v1.json':s.canonical(self.d.policy),'approval.v1.json':s.canonical(self.d.approval),
            'trial-approval.v1.json':s.canonical(self.trial),
            'trial-status.v1.json':s.canonical(dict(contract=b.TRIAL,trial_approval_sha256=s.sha(s.canonical(self.trial)),state=self.status)),
            'state.v1.json':s.canonical(self.d.state()),'trial-receipt.v1.json':self.d.files.get('receipt',b'{}'),
            '28e.approval.v1.json':s.issue_target(self.d.policy)}
        if path.name in self.files:return self.files[path.name]
        return names[path.name]
    def activate(self): self.x.prepare();return self.x.activate()
    def pair(self):return dict(application=self.d.policy['applications']['28e'],tooling=self.d.policy['tooling'])
    def final_approval_double(self):
        """Test Authority double ONLY; production code has NO such issuer."""
        evidence=dict(contract=s.VERSION,policy_sha256=self.d.policy_sha,status='NATIVE_RELEASE_ACCEPTED',
            checks=dict.fromkeys(('d8a_loader','28e_loader','switch_recovery','post_write_forward_recovery','api_mcp_worker','secret_preservation'),'PASS'),
            trial_receipt_sha256=s.sha(self.d.files['receipt']))
        self.files['native-acceptance.v1.json']=s.canonical(evidence)
        self.files['activation-approval.v1.json']=s.canonical(dict(contract=s.VERSION,policy_sha256=self.d.policy_sha,
            issuance_approval_sha256=s.sha(s.canonical(self.d.approval)),native_acceptance_sha256=s.sha(s.canonical(evidence)),
            authorization='SEEDING_SELECTOR_NATIVE_RELEASE_ACCEPTED_V1',production_authority=False))
    def test_trial_does_not_require_final_acceptance(self):
        state=self.activate();self.assertEqual(state['events'][-1]['stage'],'trial')
        self.assertEqual(state['trial_approval_sha256'],s.sha(s.canonical(self.trial)))
        self.assertNotIn('activation-approval.v1.json',self.files)
    def test_trial_prepare_calls_real_loader_adapter_interface(self):
        self.x.prepare();self.assertIn('loader',self.d.log)
    def test_native_quiesce_uses_trial_not_acceptance(self):
        self.x.prepare();self.d.purpose='activate'
        with patch.object(self.d,'ctl',create=True) as ctl,patch.object(self.d,'assert_quiesced'):
            b.NativeRelease.quiesce(self.d);self.assertEqual(ctl.call_count,3)
    def test_final_acceptance_cannot_be_trial_boolean(self):
        self.activate();self.x.close();self.d.stage='activation'
        self.files['activation-approval.v1.json']=s.canonical(dict(connected=True))
        with self.assertRaisesRegex(Blocked,'NATIVE_RELEASE_GATE_REQUIRED'):self.x.activate()
    def test_ordinary_startup_never_inherits_trial(self):
        self.activate();self.files['activation-approval.v1.json']=b'{}'
        with self.assertRaisesRegex(Blocked,'NATIVE_RELEASE_GATE_REQUIRED'):b.assert_selected(self.pair())
    def test_trial_startup_complete_component_path(self):
        self.activate()
        with patch.object(b,'verify_inherited_lock') as lock:
            b.assert_selected(self.pair(),trial=True);lock.assert_called_once()
    def test_trial_internal_startup_requires_lock(self):
        self.activate()
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(Blocked,'TRIAL_LOCK_REQUIRED'):b.assert_selected(self.pair(),trial=True)
    def test_trial_fake_service_hook_rejected(self):
        self.activate()
        with patch.object(Path,'read_text',return_value='0::/user.slice/session.scope'):
            with self.assertRaisesRegex(Blocked,'SERVICE_CONTEXT'):b.assert_selected(self.pair(),service_role='api')
    def test_trial_service_hook_cgroup_component(self):
        self.activate()
        with patch.object(Path,'read_text',return_value='0::/enterprise-agent-test.slice/enterprise-agent-test-api.service'):
            b.assert_selected(self.pair(),service_role='api')
    def test_trial_failed_startup_recovery(self):
        self.x.prepare();self.d.fault='start'
        with self.assertRaises(old.Interrupted):self.x.activate()
        state=self.x.recover();self.assertEqual(state['events'][-1]['direction'],'d8a')
        self.assertEqual(self.d.active_raw,self.d.old)
    def test_trial_new_writes_only_forward(self):
        self.activate();self.d.data='b'*64;self.d.admin=1;self.x.recover()
        self.assertEqual(self.d.current(),'28e');self.assertEqual(self.d.admin,0);self.assertIn('forward',self.d.log)
    def test_trial_recovery_only_permission(self):
        self.activate();self.status='RECOVERY_ONLY'
        with self.assertRaisesRegex(Blocked,'REVOKED'):self.x.activate()
        self.x.recover();self.x.close()
    def test_trial_revoked_all_rejects(self):
        self.activate();self.status='REVOKED'
        with self.assertRaisesRegex(Blocked,'REVOKED'):self.x.recover()
    def test_trial_expired_execute_not_recover(self):
        # Time progression, not mutating already-bound original approval bytes.
        self.activate()
        with patch.object(b,'datetime') as clock:
            clock.now.return_value=self.now+timedelta(hours=1);clock.fromisoformat=datetime.fromisoformat
            with self.assertRaisesRegex(Blocked,'EXPIRED'):self.x.activate()
            self.x.recover()
    def test_trial_recovery_window_expired(self):
        self.activate()
        with patch.object(b,'datetime') as clock:
            clock.now.return_value=self.now+timedelta(hours=3);clock.fromisoformat=datetime.fromisoformat
            with self.assertRaisesRegex(Blocked,'EXPIRED'):self.x.recover()
    def test_trial_approval_immutable_binding(self):
        self.activate();self.trial['execute_until']=(self.now+timedelta(minutes=40)).isoformat()
        with self.assertRaisesRegex(Blocked,'STATE_BINDING'):self.x.recover()
    def test_final_without_close_rejects(self):
        self.activate();self.d.stage='activation';self.files['activation-approval.v1.json']=b'{}'
        with self.assertRaises(Blocked):self.x.activate()
    def test_trial_closure_is_not_six_check_acceptance(self):
        self.activate();state=self.x.close();self.assertTrue(self.d.stopped)
        self.assertEqual(json.loads(self.d.files['receipt']),state)
        self.assertNotIn('checks',state);self.assertNotIn('native-acceptance.v1.json',self.files)
    def test_trial_close_interruption_idempotent(self):
        self.activate();self.d.fault='receipt'
        with self.assertRaises(old.Interrupted):self.x.close()
        raw=self.d.files['receipt'];self.x.close();self.assertEqual(self.d.files['receipt'],raw)
    def test_final_activation_requires_independent_approval_and_prefix(self):
        self.activate();self.x.close();self.final_approval_double();self.d.stage='activation'
        self.x.activate();b.assert_selected(self.pair())
        phases=[e['phase'] for e in self.d.state()['events']]
        self.assertIn('FINAL_AUTHORIZED',phases)
    def test_final_activation_after_no_write_recovery(self):
        self.activate();self.x.recover();self.x.close();self.final_approval_double();self.d.stage='activation'
        self.x.activate();self.assertEqual(self.d.current(),'28e')
    def test_final_activation_after_post_write_recovery(self):
        self.activate();self.d.data='b'*64;self.x.recover();self.x.close()
        self.final_approval_double();self.d.stage='activation';self.x.activate();self.assertEqual(self.d.data,'b'*64)
    def test_final_approval_incomplete_checks_rejected(self):
        self.activate();self.x.close();self.final_approval_double();self.d.stage='activation'
        evidence=json.loads(self.files['native-acceptance.v1.json']);evidence['checks']['post_write_forward_recovery']='NOT_VERIFIED'
        self.files['native-acceptance.v1.json']=s.canonical(evidence)
        approval=json.loads(self.files['activation-approval.v1.json']);approval['native_acceptance_sha256']=s.sha(s.canonical(evidence))
        self.files['activation-approval.v1.json']=s.canonical(approval)
        with self.assertRaisesRegex(Blocked,'NATIVE_RELEASE_GATE_REQUIRED'):self.x.activate()
    def test_final_receipt_tamper_rejected(self):
        self.activate();self.x.close();self.final_approval_double();self.d.stage='activation'
        self.d.files['receipt']+=b' '
        with self.assertRaisesRegex(Blocked,'REAL_TRIAL_RECEIPT_REQUIRED'):self.x.activate()
    def test_trial_cannot_restart_after_final_activation(self):
        self.activate();self.x.close();self.final_approval_double();self.d.stage='activation';self.x.activate()
        self.d.stage='trial'
        with self.assertRaisesRegex(Blocked,'STAGE_REUSE'):self.x.activate()
    def test_trial_cannot_publish(self):
        with self.assertRaisesRegex(Blocked,'SELECTOR_ACTION'):b.dispatch('trial-publish')
    def test_trial_fence_missing_blocks_before_stop(self):
        self.x.prepare();self.d.fence['OUTPUT']=[]
        with self.assertRaisesRegex(Blocked,'UNFENCED'):self.x.activate()
        self.assertFalse(self.d.stopped)
    def test_concurrent_trial_rejected(self):
        with self.d.lock():
            with self.assertRaisesRegex(Blocked,'CONCURRENT'):self.x.prepare()
    def test_formal_entry_dispatch_component(self):
        with patch.object(b,'NativeRelease',return_value=self.d):
            result=b.dispatch('trial-prepare')
        self.assertEqual(result['stage'],'trial');self.assertFalse(result['business_authority'])
        self.assertFalse(result['test_write_authority'])
    def test_trial_recover_interrupted_direction_persists(self):
        self.activate();self.d.data='b'*64;self.d.fault='forward'
        with self.assertRaises(old.Interrupted):self.x.recover()
        self.assertEqual(self.d.state()['events'][-1]['direction'],'28e')
        state=self.x.recover();self.assertEqual(state['events'][-1]['phase'],'RECOVERED')
        self.assertEqual(self.x.recover(),state)
    def test_final_activation_interrupt_recovery(self):
        self.activate();self.x.close();self.final_approval_double();self.d.stage='activation'
        self.d.fault='state:FINAL_AUTHORIZED'
        with self.assertRaises(old.Interrupted):self.x.activate()
        self.x.recover();self.assertEqual(self.d.current(),'d8a')
    def test_trial_startup_does_not_accept_unrelated_pair(self):
        self.activate();pair=copy.deepcopy(self.pair());pair['application']['tree']='0'*40
        with self.assertRaisesRegex(Blocked,'STARTUP_PAIR'):b.assert_selected(pair,trial=True)
    def test_trial_internal_recovery_keeps_global_lock_fd(self):
        self.activate();self.d.lock_fd=42;self.d.purpose='recover'
        with patch.object(self.d,'environment',return_value={},create=True),\
             patch.object(self.d,'command',return_value='{"status":"PASS"}',create=True) as command:
            b.NativeRelease.recovery(self.d,'recover')
            self.assertEqual(command.call_args.args[0][-1],'trial-forward-recover')
            self.assertEqual(command.call_args.kwargs['pass_fds'],(42,))
    def test_trial_closed_not_reopened_as_trial(self):
        self.activate();self.x.close()
        with self.assertRaises(Blocked):self.x.activate()


def trial_interruption(point):
    def test(self):
        self.x.prepare();self.d.fault=point
        with self.assertRaises(old.Interrupted):self.x.activate()
        self.x.recover();self.assertFalse(self.d.stopped)
        self.assertEqual(self.d.current(),'d8a');self.assertEqual(self.d.active_raw,self.d.old)
    return test
for point in ('state:QUIESCING','quiesce','state:QUIESCED','state:SWITCHING','install','switch','configure','loader',
              'state:SELECTED','start','health','state:COMPLETE'):
    setattr(TrialTests,'test_trial_interruption_'+point.replace(':','_'),trial_interruption(point))


def bad_trial(name,mutate):
    def test(self):
        mutate(self.trial)
        with self.assertRaises((Blocked,ValueError)):self.x.prepare()
        self.assertEqual(self.d.active_raw,self.d.old)
    setattr(TrialTests,'test_bad_trial_'+name,test)
for name,mutate in {
    'source':lambda v:v['applications']['28e'].update(source='0'*40),
    'tree':lambda v:v['tooling'].update(tree='0'*40),
    'predecessor':lambda v:v.update(predecessor_approval_sha256='0'*64),
    'tenant':lambda v:v.update(tenant_ids=['other']),
    'database':lambda v:v.update(database=dict(s.DATABASE,port=5432)),
    'environment':lambda v:v.update(environment='production'),
    'production':lambda v:v.update(production_authority=True),
    'business':lambda v:v.update(business_authority=True),
    'write':lambda v:v.update(test_write_authority=True),
    'grant':lambda v:v.update(operations=[*b.TRIAL_ACTIONS,'grant']),
    'scope':lambda v:v.update(operation_id='00000000-0000-0000-0000-000000000000'),
    'code':lambda v:v.update(code_sha256={}),
    'approval':lambda v:v.update(issuance_approval_sha256='0'*64),
    'recovery':lambda v:v['recovery_pins'].update({'scope.v1.json':'0'*64}),
    'fence':lambda v:v.update(network_fence='DISABLED'),
}.items():bad_trial(name,mutate)


class FenceTests(unittest.TestCase):
    def test_exact_fence_component(self):b.verify_trial_fence(fence_fixture())
    def test_prior_accept_rejects(self):
        value=fence_fixture();value['OUTPUT'].insert(1,shlex.split('-A OUTPUT -j ACCEPT'))
        with self.assertRaises(Blocked):b.verify_trial_fence(value)
    def test_public_proxy_uid_rejects(self):
        value=fence_fixture();value['WB_SELECTOR_TRIAL_OUT'][1][-3]='33'
        with self.assertRaises(Blocked):b.verify_trial_fence(value)
    def test_queue_rejects_non_test_tasks(self):
        queue=SimpleNamespace(knowledge_pending='kp',knowledge_processing='kx',pending='p',processing='x')
        client=SimpleNamespace(llen=lambda k:1 if k=='p' else 0,lrange=lambda *a:['customer-task'])
        with self.assertRaisesRegex(Blocked,'BUSINESS_BACKLOG'):b.validate_trial_queue(client,queue,{'formal-test-task'})
    def test_queue_formal_task_is_not_execution_grant(self):
        queue=SimpleNamespace(knowledge_pending='kp',knowledge_processing='kx',pending='p',processing='x')
        client=SimpleNamespace(llen=lambda k:1 if k=='p' else 0,lrange=lambda *a:['formal-test-task'])
        b.validate_trial_queue(client,queue,{'formal-test-task'})
    def test_kernel_fence_both_families_are_read(self):
        backend=object.__new__(b.NativeRelease)
        with patch.object(backend,'command',side_effect=lambda args:'\n'.join(shlex.join(r) for r in fence_fixture()[args[-1]])) as command:
            backend.trial_fence()
            self.assertEqual({c.args[0][0] for c in command.call_args_list},{'/usr/sbin/iptables','/usr/sbin/ip6tables'})
            self.assertEqual(command.call_count,8)
    def test_infinite_service_lifetime_rejected(self):
        backend=object.__new__(b.NativeRelease)
        backend.trial={'execute_until':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
        with patch.object(backend,'ctl',return_value='infinity'):
            with self.assertRaisesRegex(Blocked,'SERVICE_DEADLINE'):backend.trial_runtime_bounds('startup')
    def test_bounded_service_lifetime(self):
        backend=object.__new__(b.NativeRelease)
        backend.trial={'execute_until':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
        with patch.object(backend,'ctl',return_value='5min'):backend.trial_runtime_bounds('startup')
    def test_service_lifetime_cannot_outlive_lease(self):
        backend=object.__new__(b.NativeRelease)
        backend.trial={'execute_until':(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat()}
        with patch.object(backend,'ctl',return_value='5min'):
            with self.assertRaisesRegex(Blocked,'SERVICE_DEADLINE'):backend.trial_runtime_bounds('startup')


if __name__=='__main__':
    result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    print(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
        evidence='STAGED_TRIAL_COMPONENT_ONLY_NOT_NATIVE_ACCEPTANCE')))
    raise SystemExit(not result.wasSuccessful())
