"""Real encrypted backend + signed API/DB, mocked credential endpoint only."""
import io
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit

from test_wechat_secret_provisioning import SecretTests, APPID, SECRET, NEXT
from app.tenant_secret_reference import TenantSecretReferences, SecretReferenceError
from app.wechat_connection import WechatCredentialTester, WechatConnectionError, ERRORS


class PersonalConfigTests(unittest.TestCase):
    setUpClass=classmethod(SecretTests.setUpClass.__func__)
    tearDownClass=classmethod(SecretTests.tearDownClass.__func__)
    setUp=SecretTests.setUp
    tearDown=SecretTests.tearDown
    save=SecretTests.save
    account=SecretTests.account

    def tester(self,code=None,callback=None):
        calls=[]
        def verify(app_id,secret):
            calls.append((app_id,secret))
            if callback: callback()
            if code: raise WechatConnectionError(code)
        self.service.network_allowed=True
        self.service.connection_tester=SimpleNamespace(verify=verify)
        return calls

    def test_initial_state_and_optional_name_trim(self):
        initial=self.client.get('/api/v1/profile/wechat-account').json()
        self.assertEqual(initial['verification_status'],'unconfigured')
        response=self.client.put('/api/v1/profile/wechat-account',json={'wechat_app_id':' '+APPID+' ', 'app_secret':' '+SECRET+' ', 'account_display_name':' '})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['account_display_name'],'微信公众号')
        self.assertEqual(response.json()['wechat_app_id'],APPID)
        self.assertEqual(self.backend.resolve('tenant-a',self.account()['wechat_app_secret_ref'],APPID),SECRET)

    def test_connection_success_no_token_secret_or_reference_response(self):
        self.save();calls=self.tester()
        response=self.client.post('/api/v1/profile/wechat-account/test-connection',json={})
        self.assertEqual(response.status_code,200)
        self.assertEqual(calls,[(APPID,SECRET)])
        self.assertEqual(response.json()['verification_status'],'connected')
        self.assertIsNotNone(response.json()['verified_at'])
        self.assertNotIn(SECRET,response.text)
        self.assertNotIn('access_token',response.text)
        self.assertNotIn('secret_ref',response.text)
        TenantSecretReferences('test',backend=self.backend).require_connected('tenant-a',self.account())
        self.assertEqual(self.client.get('/api/v1/profile/wechat-account').json()['verification_status'],'connected')

    def test_each_classified_connection_failure_is_safe_and_not_connected(self):
        self.save()
        for code in ERRORS:
            with self.subTest(code=code):
                self.tester(code)
                response=self.client.post('/api/v1/profile/wechat-account/test-connection',json={})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['verification_status'],'failed')
                self.assertEqual(response.json()['verification_error_code'],code)
                self.assertNotIn(SECRET,response.text)
                with self.assertRaises(SecretReferenceError):
                    TenantSecretReferences('test',backend=self.backend).require_connected('tenant-a',self.account())

    def test_rotation_after_connected_resets_status(self):
        self.save();self.tester();self.service.test_connection(self.principal)
        response=self.client.post('/api/v1/profile/wechat-account/secret/rotate',json={'app_secret':NEXT})
        self.assertEqual(response.json()['verification_status'],'unverified')
        self.assertIsNone(response.json()['verified_at'])
        self.assertIsNone(response.json()['verification_error_code'])

    def test_app_id_change_empty_secret_keeps_value_invalidates_connected(self):
        self.save();self.tester();self.service.test_connection(self.principal)
        response=self.client.put('/api/v1/profile/wechat-account',json={'wechat_app_id':'wx'+'2'*16,'app_secret':''})
        self.assertEqual(response.json()['verification_status'],'unverified')
        account=self.account()
        self.assertEqual(self.backend.resolve('tenant-a',account['wechat_app_secret_ref'],account['wechat_app_id']),SECRET)

    def test_same_app_id_blank_secret_preserves_connected(self):
        self.save();self.tester();self.service.test_connection(self.principal)
        version=self.service.status(self.principal)['secret_version']
        response=self.client.put('/api/v1/profile/wechat-account',json={'wechat_app_id':APPID,'app_secret':' '})
        self.assertEqual(response.json()['verification_status'],'connected')
        self.assertEqual(response.json()['secret_version'],version)

    def test_stale_success_cannot_verify_new_rotation(self):
        self.save()
        self.tester(callback=lambda:self.service.save(self.principal,{'app_secret':NEXT}))
        response=self.client.post('/api/v1/profile/wechat-account/test-connection',json={})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.service.status(self.principal)['verification_status'],'unverified')

    def test_stale_failure_cannot_overwrite_new_config(self):
        self.save()
        self.tester('WECHAT_CONNECTION_TIMEOUT',callback=lambda:self.service.save(self.principal,{'app_secret':NEXT}))
        response=self.client.post('/api/v1/profile/wechat-account/test-connection',json={})
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.service.status(self.principal)['verification_status'],'unverified')

    def test_revoke_during_test_cannot_restore_connected(self):
        self.save();self.tester(callback=lambda:self.service.revoke(self.principal))
        response=self.client.post('/api/v1/profile/wechat-account/test-connection',json={})
        self.assertEqual(response.status_code,409)
        self.assertFalse(self.service.status(self.principal)['app_secret_configured'])

    def test_missing_configuration_blocks_before_verifier(self):
        calls=self.tester()
        self.assertEqual(self.client.post('/api/v1/profile/wechat-account/test-connection',json={}).status_code,422)
        self.assertEqual(calls,[])

    def test_unverified_draft_gate_remains_closed(self):
        self.save()
        with self.assertRaises(SecretReferenceError):
            TenantSecretReferences('test',backend=self.backend).require_connected('tenant-a',self.account())

    def test_network_disabled_blocks_before_verifier(self):
        self.save();calls=self.tester();self.service.network_allowed=False
        self.assertEqual(self.client.post('/api/v1/profile/wechat-account/test-connection',json={}).status_code,503)
        self.assertEqual(calls,[])
        self.assertEqual(self.service.status(self.principal)['verification_status'],'unverified')

    def test_unexpected_exception_is_redacted_and_never_certifies(self):
        self.save();self.tester(callback=lambda:(_ for _ in ()).throw(RuntimeError('private '+SECRET)))
        response=self.client.post('/api/v1/profile/wechat-account/test-connection',json={})
        self.assertEqual(response.status_code,503)
        self.assertNotIn(SECRET,response.text)
        self.assertEqual(self.service.status(self.principal)['verification_status'],'unverified')

    def test_login_and_current_db_permission_checked(self):
        self.save();calls=self.tester()
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/v1/profile/wechat-account/test-connection',json={}).status_code,401)
        self.assertEqual(calls,[])

    def test_role_removed_during_test_blocks_state_transition(self):
        self.save()
        def remove_role():
            with self.store.connection() as conn:conn.execute("UPDATE users SET role='member' WHERE id=?",(self.admin['id'],))
        self.tester(callback=remove_role)
        self.assertEqual(self.client.post('/api/v1/profile/wechat-account/test-connection',json={}).status_code,403)
        self.assertEqual(self.backend.status('tenant-a',self.account()['wechat_app_secret_ref'],APPID)['verification_status'],'unverified')

    def test_test_endpoint_rejects_forged_state_duplicate_keys_and_origin(self):
        self.save();calls=self.tester()
        for payload in ({'connected':True},{'app_secret':SECRET},{'tenant_id':'tenant-b'}):
            response=self.client.post('/api/v1/profile/wechat-account/test-connection',json=payload)
            self.assertEqual(response.status_code,422);self.assertNotIn(SECRET,response.text)
        self.assertEqual(self.client.post('/api/v1/profile/wechat-account/test-connection',content='{"connected":true,"connected":false}',headers={'content-type':'application/json'}).status_code,422)
        self.assertEqual(self.client.post('/api/v1/profile/wechat-account/test-connection',json={},headers={'origin':'https://untrusted.invalid'}).status_code,403)
        self.assertEqual(calls,[])

    def test_verification_audit_is_sanitized(self):
        self.save();self.tester()
        output=io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(output),contextlib.redirect_stderr(output):self.service.test_connection(self.principal)
        with self.store.connection() as conn:
            events=[dict(row) for row in conn.execute("SELECT * FROM execution_events WHERE event_type='wechat.account.verification'")]
        self.assertEqual(len(events),1)
        self.assertNotIn(SECRET,output.getvalue()+json.dumps(events))


class CredentialAdapterTests(unittest.TestCase):
    def test_adapter_matches_existing_native_skill_token_grant_contract(self):
        source=Path(__file__).parents[1]/'skill_sources/wechat-html-draft/1.0.0/scripts/wechat_draft.py'
        tree=ast.parse(source.read_bytes())
        api=next(node.value for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='API' for target in node.targets))
        self.assertEqual(ast.literal_eval(api),'https://api.weixin.qq.com/cgi-bin/')
        native=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='WeChat')
        initial=next(node for node in native.body if isinstance(node,ast.FunctionDef) and node.name=='__init__')
        call=next(node for node in ast.walk(initial) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='request')
        self.assertEqual([ast.literal_eval(arg) for arg in call.args],['GET','token'])
        parameters=next(keyword.value for keyword in call.keywords if keyword.arg=='params')
        fields={ast.literal_eval(key):value for key,value in zip(parameters.keys,parameters.values)}
        self.assertEqual(set(fields),{'grant_type','appid','secret'})
        self.assertEqual(ast.literal_eval(fields['grant_type']),'client_credential')

    def test_actual_main_error_mapper_preserves_safe_wechat_codes(self):
        source=Path(__file__).parents[1]/'app/main.py'
        tree=ast.parse(source.read_bytes())
        assignment=next(node for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='_CUSTOMER_ERROR_MESSAGES' for target in node.targets))
        function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='_error_code')
        namespace={'_CUSTOMER_ERROR_MESSAGES':ast.literal_eval(assignment.value)}
        exec(compile(ast.Module(body=[function],type_ignores=[]),str(source),'exec'),namespace)
        for status,code in [(503,'WECHAT_CONNECTION_DISABLED'),(403,'WECHAT_CONFIG_PERMISSION_REQUIRED'),(422,'WECHAT_SECRET_INPUT_INVALID')]:
            self.assertEqual(namespace['_error_code'](status,code,'/api/v1/profile/wechat-account'),code)
            self.assertNotEqual(namespace['_CUSTOMER_ERROR_MESSAGES'][code],code)

    def fake(self,value=None,status=200,error=None):
        observed=[]
        class Connection:
            def __init__(self,host,timeout):observed.append(('host',host,timeout))
            def set_debuglevel(self,level):observed.append(('debug',level))
            def request(self,method,path,headers):
                observed.append(('request',method,path))
                if error: raise error
            def getresponse(self):return SimpleNamespace(status=status,read=lambda maximum:json.dumps(value).encode())
            def close(self):observed.append(('closed',))
        return WechatCredentialTester(Connection),observed

    def test_fixed_token_contract_success_discards_token(self):
        tester,observed=self.fake({'access_token':'synthetic-token-not-returned','expires_in':7200})
        self.assertIsNone(tester.verify(APPID,SECRET))
        request=next(item for item in observed if item[0]=='request')
        self.assertEqual(request[1],'GET')
        self.assertEqual(urlsplit(request[2]).path,'/cgi-bin/token')
        self.assertEqual(parse_qs(urlsplit(request[2]).query),{'grant_type':['client_credential'],'appid':[APPID],'secret':[SECRET]})
        self.assertIn(('debug',0),observed);self.assertIn(('closed',),observed)

    def test_api_errors_are_classified_without_echo(self):
        for errcode,expected in [(40013,'WECHAT_CREDENTIAL_INVALID'),(40001,'WECHAT_CREDENTIAL_INVALID'),(40125,'WECHAT_CREDENTIAL_INVALID'),(40164,'WECHAT_IP_NOT_ALLOWED'),(45009,'WECHAT_SERVICE_UNAVAILABLE')]:
            tester,_=self.fake({'errcode':errcode,'errmsg':'private '+SECRET})
            with self.assertRaises(WechatConnectionError) as error:tester.verify(APPID,SECRET)
            self.assertEqual(error.exception.code,expected)
            self.assertNotIn(SECRET,str(error.exception))

    def test_timeout_http_and_malformed_response_fail_closed(self):
        for value,status,error,expected in [(None,200,TimeoutError('private '+SECRET),'WECHAT_CONNECTION_TIMEOUT'),({},503,None,'WECHAT_SERVICE_UNAVAILABLE'),({},200,None,'WECHAT_SERVICE_UNAVAILABLE'),([],200,None,'WECHAT_SERVICE_UNAVAILABLE')]:
            tester,_=self.fake(value,status,error)
            with self.assertRaises(WechatConnectionError) as caught:tester.verify(APPID,SECRET)
            self.assertEqual(caught.exception.code,expected)

# Fixture methods are reused, not its already-qualified suite duplicated here.
del SecretTests
