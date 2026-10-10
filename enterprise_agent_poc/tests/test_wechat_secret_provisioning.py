"""Fake/marked local secret backend only. No real key, WeChat or Provider calls."""
import asyncio
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_skill_dispatch as fixture  # Settings stub BEFORE any .env import.
from cryptography.fernet import Fernet
from fastapi import FastAPI,HTTPException,Cookie
from fastapi.testclient import TestClient
from app.auth import AuthenticationError,SessionIssuer,UserPrincipal
from app.tenant_secret_backend import ProtectedTenantSecretBackend
from app.tenant_secret_reference import TenantSecretReferences,SecretReferenceError
from app.wechat_account import WechatAccountService
from app.wechat_account_api import wechat_account_router
from app.wechat_action_contract import WechatActionContract,WechatActionError

APPID='wx'+'1'*16
SECRET='synthetic-secret-before-rotation'
NEXT='synthetic-secret-after-rotation'


class SyntheticBackend(ProtectedTenantSecretBackend):
    """Real encrypted-record implementation with a fake Test key loader."""
    def __init__(self,root,environment,key_file):
        super().__init__(root,environment,key_file,codec_loader=lambda:Fernet(Path(key_file).read_bytes()))
    def server_connected_fixture(self,tenant):
        # Trusted-server-success state is ONLY simulated in tests. No public
        # endpoint/boolean/input or production mint path can perform this write.
        with self._locked(tenant) as (path,codec):
            value=self._read(path,codec,tenant);value['verification_status']='connected';self._write(path,codec,value)


class SecretTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_temp=tempfile.TemporaryDirectory(prefix='wechat-secret-synthetic-seed-')
        cls.seed=fixture.POCStore(Path(cls.seed_temp.name)/'seed.db');cls.seed.seed_demo_data()
        cls.product=fixture.ProductStore(cls.seed);cls.product.initialize()
        for tenant,email,role in (('tenant-a','admin-secret@example.invalid','enterprise_admin'),
            ('tenant-a','member-secret@example.invalid','member'),('tenant-b','other-secret@example.invalid','enterprise_admin')):
            cls.product.create_user(tenant,email,'synthetic-unused-hash','[INTERNAL TEST] secret',role)
        cls.admin=cls.product.user_by_email('admin-secret@example.invalid')
        cls.member=cls.product.user_by_email('member-secret@example.invalid')
        cls.other=cls.product.user_by_email('other-secret@example.invalid')
    @classmethod
    def tearDownClass(cls): cls.seed_temp.cleanup()

    def setUp(self):
        import shutil
        self.temp=tempfile.TemporaryDirectory(prefix='wechat-secret-synthetic-');self.root=Path(self.temp.name)
        shutil.copyfile(self.seed.database_path,self.root/'fixture.db')
        self.store=fixture.POCStore(self.root/'fixture.db');self.product=fixture.ProductStore(self.store)
        (self.root/'keys').mkdir();self.key=self.root/'keys/key';self.key.write_bytes(Fernet.generate_key())
        self.backend=SyntheticBackend(self.root/'cipher/test','test',self.key)
        self.service=WechatAccountService(self.product,'test',self.backend)
        self.sessions=SessionIssuer('synthetic-session-secret-no-production-credentials')
        self.principal=UserPrincipal(self.admin['id'],'tenant-a','enterprise_admin')
        token=self.sessions.issue(UserPrincipal(self.admin['id'],'tenant-a','enterprise_admin',self.sessions.credential_version(self.admin['password_hash'])))
        # Actual existing product authentication function, no main singleton or
        # Provider manager imports. DB and signed-session checks are not mocked.
        main=Path(fixture.wechat_skill.__file__).parent/'main.py'
        node=next(n for n in ast.parse(main.read_bytes()).body if isinstance(n,ast.FunctionDef) and n.name=='current_user')
        ns=dict(Cookie=Cookie,HTTPException=HTTPException,UserPrincipal=UserPrincipal,
                AuthenticationError=AuthenticationError,sessions=self.sessions,product_store=self.product)
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(main),'exec'),ns)
        self.app=FastAPI();self.app.include_router(wechat_account_router(self.service,ns['current_user']))
        self.client=TestClient(self.app);self.client.cookies.set('workbench_session',token)
    def tearDown(self): self.client.close();self.temp.cleanup()
    def save(self,**change):
        return self.client.put('/api/v1/profile/wechat-account',json=dict(wechat_app_id=APPID,app_secret=SECRET,**change))
    def account(self): return self.store.enterprise_config('tenant-a')['wechat_account']

    def test_01_provision_persists_no_plaintext(self):
        response=self.save();self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['app_secret_configured']);self.assertEqual(response.json()['verification_status'],'unverified')
        self.assertNotIn(SECRET,response.text);self.assertNotIn(SECRET,json.dumps(self.account()))
        self.assertNotIn(SECRET,self.product._store.database_path.read_bytes().decode('latin1'))
        self.assertNotIn(SECRET,b''.join(p.read_bytes() for p in self.backend.root.glob('*.fernet')).decode('ascii'))
    def test_02_get_never_returns_secret_or_ciphertext(self):
        self.save();response=self.client.get('/api/v1/profile/wechat-account')
        self.assertNotIn('app_secret',set(response.json())-{'app_secret_configured'})
        self.assertNotIn(SECRET,response.text);self.assertNotIn('secret_ref',response.text)
    def test_03_api_write_visible_to_independent_mcp_process(self):
        self.save()
        code='''import sys,json,hashlib
from pathlib import Path
from cryptography.fernet import Fernet
from app.store import POCStore
from app.tenant_secret_backend import ProtectedTenantSecretBackend
from app.tenant_secret_reference import TenantSecretReferences
db,root,key=sys.argv[1:]
b=ProtectedTenantSecretBackend(root,'test',key,codec_loader=lambda:Fernet(Path(key).read_bytes()))
account=POCStore(db).enterprise_config('tenant-a')['wechat_account']
lease=TenantSecretReferences('test',backend=b).resolve_wechat('tenant-a',account)
with lease.child_environment() as env: print(hashlib.sha256(env['WECHAT_APP_SECRET'].encode()).hexdigest())
'''
        env={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP')}
        env['PYTHONPATH']=str(Path(fixture.wechat_skill.__file__).parents[1])
        value=subprocess.check_output([sys.executable,'-B','-c',code,str(self.store.database_path),str(self.backend.root),str(self.key)],env=env)
        import hashlib
        self.assertEqual(value.decode().strip(),hashlib.sha256(SECRET.encode()).hexdigest())
    def test_04_rotate_and_old_reference_invalid(self):
        self.save();old=self.account();before=old['wechat_app_secret_ref']['version']
        response=self.client.post('/api/v1/profile/wechat-account/secret/rotate',json={'app_secret':NEXT})
        self.assertEqual(response.status_code,200);self.assertGreater(response.json()['secret_version'],before)
        with self.assertRaises(SecretReferenceError): TenantSecretReferences('test',backend=self.backend).resolve_wechat('tenant-a',old)
    def test_05_revoke_invalidates_mcp_and_outstanding_lease(self):
        self.save();old=self.account();lease=TenantSecretReferences('test',backend=self.backend).resolve_wechat('tenant-a',old)
        self.assertEqual(self.client.delete('/api/v1/profile/wechat-account').status_code,200)
        with self.assertRaises(SecretReferenceError):
            with lease.child_environment(): pass
        with self.assertRaises(SecretReferenceError): TenantSecretReferences('test',backend=self.backend).resolve_wechat('tenant-a',old)
        self.assertFalse(self.client.get('/api/v1/profile/wechat-account').json()['app_secret_configured'])
    def test_06_empty_secret_keeps_old(self):
        self.save();old=self.account()
        response=self.client.put('/api/v1/profile/wechat-account',json={'wechat_app_id':APPID,'app_secret':''})
        self.assertEqual(response.status_code,200);self.assertEqual(old,self.account())
    def test_07_other_tenant_reference_rejected_before_read(self):
        self.save();account=self.account()
        with patch.object(self.backend,'resolve',side_effect=AssertionError('No cross-tenant read')):
            with self.assertRaises(SecretReferenceError): TenantSecretReferences('test',backend=self.backend).resolve_wechat('tenant-b',account)
    def test_08_environment_isolation(self):
        self.save();account=self.account()
        with self.assertRaises(SecretReferenceError): TenantSecretReferences('production',backend=self.backend).resolve_wechat('tenant-a',account)
    def test_09_member_cannot_write_even_with_forged_role(self):
        member=UserPrincipal(self.member['id'],'tenant-a','enterprise_admin')
        with self.assertRaises(SecretReferenceError): self.service.save(member,dict(wechat_app_id=APPID,app_secret=SECRET))
    def test_10_login_required(self):
        self.client.cookies.clear();self.assertEqual(self.save().status_code,401)
    def test_11_forged_connected_and_scopes_rejected(self):
        for field in ('connected','verification_status','tenant_id','environment','secret_ref'):
            self.assertEqual(self.save(**{field:True}).status_code,422)
        self.assertFalse(self.service.status(self.principal)['app_secret_configured'])
    def test_12_generic_config_cannot_forge_verification(self):
        for field in ('connected','verification_status','verification_binding'):
            with self.assertRaises(ValueError): self.product.update_enterprise_config('tenant-a',{'wechat_account':{field:'connected'}})
    def test_13_audit_and_errors_do_not_expose_values(self):
        log=io.StringIO()
        with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
            self.save();self.client.post('/api/v1/profile/wechat-account/secret/rotate',json={'app_secret':NEXT});self.client.delete('/api/v1/profile/wechat-account')
        with self.store.connection() as conn:
            audit=[dict(r) for r in conn.execute("SELECT * FROM execution_events WHERE event_type LIKE 'tenant_secret.%'")]
        self.assertNotIn(SECRET,log.getvalue()+json.dumps(audit));self.assertNotIn(NEXT,log.getvalue()+json.dumps(audit))
    def test_14_tampered_ciphertext_blocks(self):
        self.save();path=next(self.backend.root.glob('*.fernet'));path.write_bytes(b'not-a-valid-authenticated-token')
        with self.assertRaises(SecretReferenceError): TenantSecretReferences('test',backend=self.backend).resolve_wechat('tenant-a',self.account())
    def test_15_no_backend_key_fails_closed(self):
        service=WechatAccountService(self.product,'test',ProtectedTenantSecretBackend(self.root/'without-key','test',None))
        with self.assertRaises(SecretReferenceError): service.save(self.principal,dict(wechat_app_id=APPID,app_secret=SECRET))
        self.assertFalse((self.root/'without-key').exists())
    def test_16_lease_rotation_invalidation(self):
        self.save();lease=TenantSecretReferences('test',backend=self.backend).resolve_wechat('tenant-a',self.account())
        self.client.post('/api/v1/profile/wechat-account/secret/rotate',json={'app_secret':NEXT})
        with self.assertRaises(SecretReferenceError):
            with lease.child_environment(): pass
    def test_17_appid_change_invalidates_verification(self):
        self.save();self.backend.server_connected_fixture('tenant-a')
        self.assertEqual(self.service.status(self.principal)['verification_status'],'connected')
        self.client.put('/api/v1/profile/wechat-account',json={'wechat_app_id':'wx'+'2'*16,'app_secret':''})
        self.assertEqual(self.service.status(self.principal)['verification_status'],'unverified')
    def test_18_credential_errors_are_redacted(self):
        self.save()
        with patch.object(self.backend,'resolve',side_effect=RuntimeError(SECRET)):
            with self.assertRaises(SecretReferenceError) as caught: TenantSecretReferences('test',backend=self.backend).resolve_wechat('tenant-a',self.account())
        self.assertNotIn(SECRET,str(caught.exception))
    def test_19_verification_get_has_no_write(self):
        self.save();response=self.client.get('/api/v1/profile/wechat-account/verification-status')
        self.assertEqual(response.json()['verification_status'],'unverified')
        self.assertEqual(self.client.post('/api/v1/profile/wechat-account/verification-status',json={'connected':True}).status_code,405)
    def test_20_cross_tenant_user_identity_rejected(self):
        with self.assertRaises(SecretReferenceError): self.service.save(UserPrincipal(self.other['id'],'tenant-a','enterprise_admin'),dict(wechat_app_id=APPID,app_secret=SECRET))

    def test_21_expired_lease_denied(self):
        self.save();lease=TenantSecretReferences('test',backend=self.backend).resolve_wechat('tenant-a',self.account())
        lease._expires_at=0
        with self.assertRaises(SecretReferenceError):
            with lease.child_environment(): pass
        self.assertEqual(lease._values,{})

    def test_22_cross_origin_mutations_rejected(self):
        for method,path,payload in (('put','',dict(wechat_app_id=APPID,app_secret=SECRET)),
            ('post','/secret/rotate',dict(app_secret=NEXT)),('delete','',None)):
            kwargs=dict(headers={'Origin':'https://other.example.invalid'})
            if payload is not None: kwargs['json']=payload
            self.assertEqual(getattr(self.client,method)('/api/v1/profile/wechat-account'+path,**kwargs).status_code,403)
        self.assertFalse(self.service.status(self.principal)['app_secret_configured'])

    def test_23_duplicate_or_malformed_body_not_echoed(self):
        for body in ('{"app_secret":"'+SECRET+'","app_secret":"'+NEXT+'"}',SECRET,'[]'):
            response=self.client.put('/api/v1/profile/wechat-account',content=body,headers={'Content-Type':'application/json'})
            self.assertEqual(response.status_code,422);self.assertNotIn(SECRET,response.text);self.assertNotIn(NEXT,response.text)

    def test_24_ciphertext_transplant_not_a_tenant_identity(self):
        self.save()
        import hashlib
        path=self.backend.root/(hashlib.sha256(b'tenant-b').hexdigest()+'.fernet')
        path.write_bytes(next(self.backend.root.glob('*.fernet')).read_bytes())
        if os.name=='posix': path.chmod(0o600)
        account=self.account();account['wechat_app_secret_ref']['tenant_id']='tenant-b'
        with self.assertRaises(SecretReferenceError): TenantSecretReferences('test',backend=self.backend).resolve_wechat('tenant-b',account)

    def test_25_db_write_failure_keeps_old_reference_invalid(self):
        self.save();old=self.account()
        with self.store.connection() as conn:
            conn.execute("CREATE TRIGGER synthetic_fail BEFORE UPDATE ON enterprise_configs BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        response=self.client.post('/api/v1/profile/wechat-account/secret/rotate',json={'app_secret':NEXT})
        self.assertEqual(response.status_code,503);self.assertNotIn(NEXT,response.text)
        self.assertEqual(self.account(),old)
        with self.assertRaises(SecretReferenceError): TenantSecretReferences('test',backend=self.backend).resolve_wechat('tenant-a',old)

    def test_26_rotation_never_retains_connected(self):
        self.save();self.backend.server_connected_fixture('tenant-a')
        response=self.client.post('/api/v1/profile/wechat-account/secret/rotate',json={'app_secret':NEXT})
        self.assertEqual(response.json()['verification_status'],'unverified')


class GateTests(fixture.DispatchTests):
    """Reuse real Registry/Task/action gates; process/runtime remain mock."""
    def test_connected_gate_no_network_or_unverified(self):
        self.product.create_user('tenant-a','gate-admin@example.invalid','unused','[INTERNAL TEST] gate','enterprise_admin')
        admin=self.product.user_by_email('gate-admin@example.invalid')
        key=self.root/'synthetic-key';key.write_bytes(Fernet.generate_key())
        backend=SyntheticBackend(self.root/'private','test',key)
        service=WechatAccountService(self.product,'test',backend)
        service.save(UserPrincipal(admin['id'],'tenant-a','enterprise_admin'),dict(wechat_app_id=APPID,app_secret=SECRET))
        refs=TenantSecretReferences('test',backend=backend)
        contract=WechatActionContract(self.store,self.issuer,'test',refs,network_allowed=True)
        self.make_task('请创建公众号草稿')
        with self.assertRaisesRegex(SecretReferenceError,'NOT_CONNECTED'): contract.resolve(self.bearer,self.scope,'CREATE_DRAFT')
        backend.server_connected_fixture('tenant-a')
        contract.network_allowed=False
        with self.assertRaisesRegex(WechatActionError,'NETWORK_OPERATION'): contract.resolve(self.bearer,self.scope,'CREATE_DRAFT')
        contract.network_allowed=True
        self.assertEqual(contract.resolve(self.bearer,self.scope,'CREATE_DRAFT')['execution_status'],'PERMISSION_RESOLVED_NOT_EXECUTED')
        self.assertEqual(self.result(action='CREATE_DRAFT')['error_code'],'SKILL_ACTION_NOT_ALLOWED')
        lease=contract.prepare_secret_injection(self.bearer,self.scope)
        self.product.set_task(self.task['id'],'tenant-a','cancelled','cancelled','Synthetic cancellation')
        with self.assertRaises(SecretReferenceError):
            with lease.child_environment(): pass
        self.assertFalse(self.process_calls)
    def test_prepare_no_secret_or_connection_required(self):
        with patch.object(self.permission.secrets,'require_connected',side_effect=AssertionError('PREPARE must not read credentials')):
            self.assertEqual(self.result()['status'],'completed')

    def test_api_to_actual_mcp_service_in_independent_process(self):
        self.product.create_user('tenant-a','process-admin@example.invalid','unused','[INTERNAL TEST] process','enterprise_admin')
        principal=UserPrincipal(self.product.user_by_email('process-admin@example.invalid')['id'],'tenant-a','enterprise_admin')
        key=self.root/'fake-key';key.write_bytes(Fernet.generate_key())
        backend=SyntheticBackend(self.settings.data_dir/'tenant-secrets/test','test',key)
        service=WechatAccountService(self.product,'test',backend)
        app=FastAPI();app.include_router(wechat_account_router(service,lambda cookie:principal))
        with TestClient(app) as client:
            self.assertEqual(client.put('/api/v1/profile/wechat-account',json=dict(wechat_app_id=APPID,app_secret=SECRET)).status_code,200)
            backend.server_connected_fixture('tenant-a');self.make_task('请创建公众号草稿')
            code='''import sys,json,hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import test_skill_dispatch as fixture
from cryptography.fernet import Fernet
from app.tenant_secret_backend import ProtectedTenantSecretBackend
from app.tenant_secret_reference import TenantSecretReferences,SecretReferenceError
from app.wechat_action_contract import WechatActionContract,WechatActionError
p=json.load(sys.stdin)
s=SimpleNamespace(environment='test',data_dir=Path(p['data']),tenant_secret_key_file=Path(p['key']),wechat_network_allowed=True)
def backend(settings): return ProtectedTenantSecretBackend(settings.data_dir/'tenant-secrets/test','test',settings.tenant_secret_key_file,codec_loader=lambda:Fernet(settings.tenant_secret_key_file.read_bytes()))
svc=fixture.PlatformMCPService.__new__(fixture.PlatformMCPService)
svc._store=fixture.POCStore(p['db']);svc._tokens=fixture.RuntimeTokenIssuer('synthetic-dispatch-signing-material-not-a-real-secret');svc._settings=s
try:
    with patch('app.tenant_secret_backend.backend_from_settings',backend):
        receipt=svc.wechat_action_permission(p['bearer'],p['scope'],'CREATE_DRAFT')
        c=WechatActionContract(svc._store,svc._tokens,'test',TenantSecretReferences('test',backend=backend(s)),network_allowed=True)
        with c.prepare_secret_injection(p['bearer'],p['scope']).child_environment() as env: digest=hashlib.sha256(env['WECHAT_APP_SECRET'].encode()).hexdigest()
    print(json.dumps(dict(status=receipt['execution_status'],digest=digest)))
except (SecretReferenceError,WechatActionError): print(json.dumps(dict(status='BLOCKED')))
'''
            env={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP')}
            env['PYTHONPATH']=os.pathsep.join((str(Path(fixture.wechat_skill.__file__).parents[1]),str(Path(__file__).parent)))
            payload=dict(db=str(self.store.database_path),data=str(self.settings.data_dir),key=str(key),bearer=self.bearer,scope=self.scope)
            def child():
                run=subprocess.run([sys.executable,'-B','-c',code],input=json.dumps(payload).encode(),capture_output=True,env=env,timeout=30,check=True)
                self.assertNotIn(SECRET.encode(),run.stdout+run.stderr);self.assertNotIn(NEXT.encode(),run.stdout+run.stderr)
                return json.loads(run.stdout)
            import hashlib
            self.assertEqual(child(),dict(status='PERMISSION_RESOLVED_NOT_EXECUTED',digest=hashlib.sha256(SECRET.encode()).hexdigest()))
            self.assertEqual(client.post('/api/v1/profile/wechat-account/secret/rotate',json=dict(app_secret=NEXT)).status_code,200)
            self.assertEqual(child()['status'],'BLOCKED')  # No cached old connected or secret.
            backend.server_connected_fixture('tenant-a')
            self.assertEqual(child()['digest'],hashlib.sha256(NEXT.encode()).hexdigest())
            self.assertEqual(client.delete('/api/v1/profile/wechat-account').status_code,200)
            self.assertEqual(child()['status'],'BLOCKED')
        self.assertFalse(self.process_calls)


if __name__=='__main__': unittest.main()
