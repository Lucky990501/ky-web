"""Binding component tests; never evidence of live PRIMARY qualification."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import runtime_recovery_binding as b
from scripts.wechat_runtime_test_lifecycle_guard import Blocked


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.root=b.PRIMARY
        self.p=b.profile(self.root)
        self.pair={'application':dict(source=b.APP_SOURCE,tree=b.APP_TREE,
            path=str(self.p['home']/'releases/app')),
            'tooling':dict(path=str(self.p['home']/'shared/source-qualifications/tool'))}
        self.policy=dict(contract=b.VERSION,realm=self.p['realm'],environment='test',production_authority=False,
            pair_sha256='a'*64,python=str(self.p['home']/'shared/runtime/python311/bin/python'),
            post_write_recovery='EXACT_CURRENT_PAIR_ONLY',predecessor_rollback=False,
            native_pins={n:'b'*64 for n in ('runtime-pair.v1.json','approval.v2.json','scope.v1.json',
                                           'native-policy.v2.json','native-approval.v2.json')},
            services={r:dict(fragment_sha256='c'*64,dropins={},environment_files=[dict(
                path=str(self.p['home']/'shared/config/test-environment.env'),sha256='d'*64,
                uid=1000,gid=1000,mode=0o640),dict(path=str(self.p['home']/'shared/credentials/test-environment.secrets.env'),
                sha256='e'*64,uid=1000,gid=1000,mode=0o600)]) for r in b.ROLES})
        self.approval=dict(contract=b.VERSION,policy_sha256='f'*64,
            authorization='FORMAL_PRIMARY_FORWARD_RECOVERY_SOURCE_FIX_APPROVED',production_authority=False,
            code_sha256={n:'1'*64 for n in b.CODE})
        script=str(Path(self.pair['tooling']['path'])/'enterprise_agent_poc/scripts/runtime_recovery_operator.py')
        self.loaded={}
        for r,u in self.p['units'].items():
            entry={'api':['-m','uvicorn','app.main:app','--host','127.0.0.1','--port','18100'],
                   'mcp':['-m','app.platform_mcp.server'],'worker':['-m','app.worker']}[r]
            hook=lambda args:'{ path=/python ; argv[]='+' '.join(args)+' ; ignore_errors=no ; }'
            self.loaded[u]=dict(FragmentPath='/etc/systemd/system/'+u,DropInPaths='',User='lucky',Group='lucky',
                Slice=self.p['slice'],KillMode='control-group',WorkingDirectory=self.pair['application']['path']+'/enterprise_agent_poc',
                ExecStart=hook([self.policy['python'],'-B',*entry]),
                ExecStartPre=hook([self.policy['python'],'-B',script,'pre',r]),
                ExecStartPost=hook([self.policy['python'],'-B',script,'post',r]),
                EnvironmentFiles=' '.join(x['path']+' (ignore_errors=no)' for x in self.policy['services'][r]['environment_files']))
    def shape(self): return b.shape(self.policy,self.approval,'f'*64,self.pair,'a'*64,self.root)
    def loaded_check(self):
        def ctl(command,unit,flag,key,last):
            self.assertEqual((command,flag,last),('show','-p','--value'))
            return self.loaded[unit][key]
        with patch.object(b,'pinned_file') as files:
            b.validate_loaded(self.policy,self.p,self.pair,ctl)
            return files.call_count
    def bad_policy(self,key,value):
        self.policy[key]=value
        with self.assertRaises(Blocked): self.shape()
    def bad_loaded(self,key,value):
        self.loaded[self.p['units']['api']][key]=value
        with self.assertRaises(Blocked): self.loaded_check()
    def test_primary_shape(self):self.assertTrue(self.shape()['primary'])
    def test_isolated_shape(self):
        self.root=b.ISOLATED;self.p=b.profile(self.root);self.policy['realm']=self.p['realm']
        for e in self.policy['services'].values():e['environment_files']=[dict(path=str(self.root/'service.env'),sha256='d'*64,uid=0,gid=0,mode=0o400)]
        self.assertFalse(self.shape()['primary'])
    def test_production(self):self.bad_policy('environment','production')
    def test_production_authority(self):self.bad_policy('production_authority',True)
    def test_cross_realm(self):self.bad_policy('realm','ISOLATED_NATIVE_TEST_CONTEXT')
    def test_unknown_authority(self):
        self.root=Path('/etc/unapproved')
        with self.assertRaises(Blocked):self.shape()
    def test_d8a_postwrite_rejected(self):
        self.pair['application']['source']='d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7'
        with self.assertRaises(Blocked):self.shape()
    def test_wrong_application_tree(self):
        self.pair['application']['tree']='0'*40
        with self.assertRaises(Blocked):self.shape()
    def test_pair_mismatch(self):self.bad_policy('pair_sha256','0'*64)
    def test_predecessor_rollback(self):self.bad_policy('predecessor_rollback',True)
    def test_restore_older_database(self):self.bad_policy('post_write_recovery','RESTORE_PREDECESSOR')
    def test_unapproved_policy(self):
        self.approval['policy_sha256']='0'*64
        with self.assertRaises(Blocked):self.shape()
    def test_wrong_approval(self):
        self.approval['authorization']='unapproved'
        with self.assertRaises(Blocked):self.shape()
    def test_missing_code_pin(self):
        del self.approval['code_sha256']['runtime_recovery_binding.py']
        with self.assertRaises(Blocked):self.shape()
    def test_missing_scope_pin(self):
        del self.policy['native_pins']['scope.v1.json']
        with self.assertRaises(Blocked):self.shape()
    def test_wrong_python(self):self.bad_policy('python','/usr/bin/python')
    def test_missing_worker(self):
        del self.policy['services']['worker']
        with self.assertRaises(Blocked):self.shape()
    def test_cross_unit_dropin(self):
        self.policy['services']['api']['dropins']['/etc/systemd/system/other.service.d/x.conf']='0'*64
        with self.assertRaises(Blocked):self.shape()
    def test_unprotected_environment_path(self):
        self.policy['services']['api']['environment_files'][0]['path']='/tmp/config.env'
        with self.assertRaises(Blocked):self.shape()
    def test_writable_environment_by_others(self):
        self.policy['services']['api']['environment_files'][0]['mode']=0o666
        with self.assertRaises(Blocked):self.shape()
    def test_loaded_exact_entry(self):self.assertEqual(self.loaded_check(),9)
    def test_service_inactive_does_not_block_preflight(self):
        # No ActiveState or PID is queried before recovery, only identity.
        self.assertEqual(self.loaded_check(),9)
    def test_wrong_service_user(self):self.bad_loaded('User','root')
    def test_wrong_service_slice(self):self.bad_loaded('Slice','system.slice')
    def test_wrong_application_cwd(self):self.bad_loaded('WorkingDirectory','/old/d8a')
    def test_unknown_loaded_dropin(self):self.bad_loaded('DropInPaths','/etc/systemd/system/other.conf')
    def test_old_guard_hook(self):self.bad_loaded('ExecStartPre','{ argv[]=/python old_guard.py ; ignore_errors=no ; }')
    def test_missing_post_health_hook(self):self.bad_loaded('ExecStartPost','')
    def test_ignored_hook_failure(self):self.bad_loaded('ExecStartPre',self.loaded[self.p['units']['api']]['ExecStartPre'].replace('ignore_errors=no','ignore_errors=yes'))
    def test_extra_hook(self):
        v=self.loaded[self.p['units']['api']]['ExecStartPre'];self.bad_loaded('ExecStartPre',v+' '+v)
    def test_wrong_execution_entry(self):self.bad_loaded('ExecStart','{ argv[]=/bin/sh unsafe.sh ; ignore_errors=no ; }')
    def test_wrong_environment_order(self):
        v=self.policy['services']['api']['environment_files']
        self.bad_loaded('EnvironmentFiles',' '.join(x['path']+' (ignore_errors=no)' for x in reversed(v)))
    def test_optional_environment_not_accepted(self):
        self.bad_loaded('EnvironmentFiles',self.loaded[self.p['units']['api']]['EnvironmentFiles'].replace('ignore_errors=no','ignore_errors=yes'))
    def test_unknown_policy_field(self):self.bad_policy('skip_guard',True)


if __name__=='__main__':
    result=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(BindingTests))
    print(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
        skipped=len(result.skipped),evidence='COMPONENT_ONLY_NOT_PRIMARY_NATIVE')))
    raise SystemExit(not result.wasSuccessful())
