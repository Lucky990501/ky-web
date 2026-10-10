"""Tenant account management over ProductStore + TenantSecretReferences backend."""
import json
from app.tenant_secret_reference import SecretReferenceError
from app.wechat_action_contract import account_config


class WechatAccountService:
    def __init__(self, product, environment, backend, *, connection_tester=None, network_allowed=False):
        self.product, self.environment, self.backend = product, environment, backend
        self.connection_tester, self.network_allowed = connection_tester, network_allowed
        if backend.environment != environment: raise SecretReferenceError('TENANT_SECRET_REFERENCE_BLOCKED')

    def authorize(self, principal, *, manage=False):
        with self.product._store.connection() as conn:
            self._authorize_conn(conn,principal,manage=manage)

    def _authorize_conn(self,conn,principal,*,manage=False):
        row = conn.execute('SELECT role,account_status FROM users WHERE id=? AND tenant_id=?' +
            (' FOR UPDATE' if manage and self.product._store.is_postgres else ''),
            (principal.user_id,principal.tenant_id)).fetchone()
        if not row or row['account_status'] != 'enabled' or manage and row['role'] != 'enterprise_admin':
            raise SecretReferenceError('WECHAT_CONFIG_PERMISSION_REQUIRED')

    def status(self, principal):
        self.authorize(principal)
        account = self.product._store.enterprise_config(principal.tenant_id).get('wechat_account')
        result = dict(wechat_app_id=None, account_display_name=None, app_secret_configured=False,
                      verification_status='unconfigured', secret_version=None, verified_at=None, verification_error_code=None)
        if not account: return result
        try:
            account = account_config(account, principal.tenant_id)
            result.update(wechat_app_id=account['wechat_app_id'],account_display_name=account['account_display_name'])
            state = self.backend.status(principal.tenant_id, account.get('wechat_app_secret_ref'), account['wechat_app_id'])
            result.update(app_secret_configured=True,verification_status=state['verification_status'],secret_version=state['version'],
                          verified_at=state.get('verified_at'), verification_error_code=state.get('verification_error_code'))
        except SecretReferenceError: pass
        return result

    def save(self, principal, payload, *, rotate_only=False):
        self.authorize(principal, manage=True)
        keys = {'app_secret'} if rotate_only else {'wechat_app_id','app_secret','account_display_name'}
        if not isinstance(payload,dict) or set(payload)-keys:
            raise SecretReferenceError('WECHAT_SECRET_INPUT_INVALID')
        payload=dict(payload)
        for name in ('wechat_app_id','account_display_name','app_secret'):
            if isinstance(payload.get(name),str): payload[name]=payload[name].strip()
        if not rotate_only and 'account_display_name' in payload and not payload['account_display_name']:
            payload['account_display_name']='微信公众号'
        secret = payload.get('app_secret')
        if secret is not None and not isinstance(secret,str): raise SecretReferenceError('WECHAT_SECRET_INPUT_INVALID')
        if isinstance(secret,str) and not secret.strip(): secret=None
        if rotate_only and secret is None: raise SecretReferenceError('WECHAT_SECRET_INPUT_INVALID')
        with self.product._store.connection() as conn:
            if not self.product._store.is_postgres: conn.execute('BEGIN IMMEDIATE')
            self._authorize_conn(conn,principal,manage=True)
            row = conn.execute('SELECT payload FROM enterprise_configs WHERE tenant_id=?' +
                (' FOR UPDATE' if self.product._store.is_postgres else ''), (principal.tenant_id,)).fetchone()
            if not row: raise SecretReferenceError('WECHAT_ACCOUNT_CONFIG_BLOCKED')
            config = json.loads(row['payload']); old = config.get('wechat_account') or {}
            if rotate_only and not old.get('wechat_app_secret_ref'): raise SecretReferenceError('WECHAT_CREDENTIAL_REFERENCE_UNAVAILABLE')
            account = account_config(dict(wechat_app_id=payload.get('wechat_app_id',old.get('wechat_app_id')),
                account_display_name=payload.get('account_display_name',old.get('account_display_name') or '微信公众号')),principal.tenant_id)
            expected = old.get('wechat_app_secret_ref')
            if expected and expected.get('provider') != 'wechat': expected=None  # Explicit migrate by supplying a new secret.
            reference = self.backend.save(principal.tenant_id,account['wechat_app_id'],secret,expected=expected)
            account['wechat_app_secret_ref'] = reference
            config['wechat_account'] = account
            # Only AppID/name/reference in ordinary configuration. Never plaintext,
            # ciphertext, verification proof or connected=true here.
            conn.execute('UPDATE enterprise_configs SET payload=? WHERE tenant_id=?',
                         (json.dumps(config,ensure_ascii=False),principal.tenant_id))
        self._audit(principal,'rotate' if rotate_only or expected else 'provision',reference['version'])
        return self.status(principal)

    def test_connection(self, principal):
        from app.tenant_secret_reference import TenantSecretReferences
        from app.wechat_connection import WechatCredentialTester, WechatConnectionError
        self.authorize(principal,manage=True)
        account=account_config(self.product._store.enterprise_config(principal.tenant_id).get('wechat_account'),principal.tenant_id)
        reference=account.get('wechat_app_secret_ref')
        lease=TenantSecretReferences(self.environment,backend=self.backend).resolve_wechat(principal.tenant_id,account)
        error_code=None
        try:
            if not self.network_allowed: raise SecretReferenceError('WECHAT_CONNECTION_DISABLED')
            tester=self.connection_tester or WechatCredentialTester()
            with lease.child_environment() as environment:
                # Handle only classified errors while lease is active; raw
                # executor exceptions are scrubbed by the existing lease.
                try: tester.verify(environment['WECHAT_APP_ID'],environment['WECHAT_APP_SECRET'])
                except WechatConnectionError as error: error_code=error.code
        finally:
            lease.close()
        with self.product._store.connection() as conn:
            if not self.product._store.is_postgres: conn.execute('BEGIN IMMEDIATE')
            self._authorize_conn(conn,principal,manage=True)
            row=conn.execute('SELECT payload FROM enterprise_configs WHERE tenant_id=?'+
                (' FOR UPDATE' if self.product._store.is_postgres else ''),(principal.tenant_id,)).fetchone()
            current=(json.loads(row['payload']).get('wechat_account') or {}) if row else {}
            if (current.get('wechat_app_id')!=account['wechat_app_id'] or current.get('wechat_app_secret_ref')!=reference):
                raise SecretReferenceError('WECHAT_SECRET_VERSION_CONFLICT')
            self.backend.record_verification(principal.tenant_id,reference,account['wechat_app_id'],error_code=error_code)
        self.product._store.log_event(None,'wechat.account.verification',dict(tenant_id=principal.tenant_id,
            environment=self.environment,actor_id=principal.user_id,version=reference['version'],
            status='failed' if error_code else 'connected',error_code=error_code))
        return self.status(principal)

    def revoke(self, principal):
        self.authorize(principal,manage=True)
        with self.product._store.connection() as conn:
            if not self.product._store.is_postgres: conn.execute('BEGIN IMMEDIATE')
            self._authorize_conn(conn,principal,manage=True)
            row = conn.execute('SELECT payload FROM enterprise_configs WHERE tenant_id=?' +
                (' FOR UPDATE' if self.product._store.is_postgres else ''),(principal.tenant_id,)).fetchone()
            if not row: raise SecretReferenceError('WECHAT_ACCOUNT_CONFIG_BLOCKED')
            config=json.loads(row['payload'])
            self.backend.revoke(principal.tenant_id)
            config.pop('wechat_account',None)
            conn.execute('UPDATE enterprise_configs SET payload=? WHERE tenant_id=?',(json.dumps(config,ensure_ascii=False),principal.tenant_id))
        self._audit(principal,'revoke',None)
        return self.status(principal)

    def _audit(self,principal,operation,version):
        self.product._store.log_event(None,'tenant_secret.'+operation,dict(
            contract='WECHAT_TENANT_SECRET_PROVISIONING_V1',tenant_id=principal.tenant_id,
            environment=self.environment,provider='wechat',actor_id=principal.user_id,version=version))
