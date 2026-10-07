"""Scoped references to Workbench's existing process-environment secret backend.

No vault, credential database, provisioning endpoint or .env loading. References
cannot select arbitrary environment names. Values remain server-process local.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import os
import re
from typing import Mapping

ENVIRONMENTS = {'test', 'development', 'production'}
PROVIDER = 'runtime_environment'


class SecretReferenceError(PermissionError):
    pass


def validate_reference(value: dict, tenant_id: str, *, environment: str | None = None) -> dict:
    if (not isinstance(value,dict) or set(value) != {'provider','tenant_id','environment','name'}
            or value['provider'] != PROVIDER or value['tenant_id'] != tenant_id
            or not isinstance(value['environment'],str)
            or value['environment'] not in ENVIRONMENTS
            or not isinstance(value['name'],str)
            or not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',value['name'])
            or (environment is not None and value['environment'] != environment)):
        raise SecretReferenceError('TENANT_SECRET_REFERENCE_BLOCKED')
    return dict(value)


def environment_key(reference: dict, tenant_id: str, environment: str, purpose: str) -> str:
    validate_reference(reference,tenant_id,environment=environment)
    if purpose not in ('wechat_app_secret','wechat_access_token'):
        raise SecretReferenceError('TENANT_SECRET_REFERENCE_BLOCKED')
    tenant_hash=hashlib.sha256(tenant_id.encode('utf-8')).hexdigest().upper()
    ref_hash=hashlib.sha256(reference['name'].encode('ascii')).hexdigest().upper()
    return f'WORKBENCH_SECRET_{environment.upper()}_{tenant_hash}_{purpose.upper()}_{ref_hash}'


@dataclass(repr=False)
class SecretLease:
    """Only server execution code may consume this lease; never an API result."""
    _values: dict[str,str] = field(repr=False)

    def __repr__(self):
        return '<SecretLease redacted>'

    @contextmanager
    def child_environment(self, base: Mapping[str,str] | None = None):
        if not self._values:
            raise SecretReferenceError('SECRET_LEASE_CLOSED')
        # Whitelist OS essentials. Never inherit unrelated provider/tenant secrets.
        env={k:v for k,v in (base or {}).items() if k.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP'}}
        env.update(self._values)
        try:
            yield env
        except Exception:
            # A child/executor exception can embed its credential-bearing URL.
            raise SecretReferenceError('SECRET_EXECUTION_SCOPE_FAILED') from None
        finally:
            env.clear()
            self.close()

    def redact(self, text: str) -> str:
        if not self._values:
            raise SecretReferenceError('SECRET_LEASE_CLOSED')
        for value in self._values.values():
            if value:
                text=text.replace(value,'[REDACTED]')
        return text

    def close(self):
        self._values.clear()


class TenantSecretReferences:
    def __init__(self, environment: str, source: Mapping[str,str] | None = None):
        if environment not in ENVIRONMENTS:
            raise SecretReferenceError('TENANT_SECRET_REFERENCE_BLOCKED')
        self.environment=environment
        self._source=os.environ if source is None else source

    def resolve_wechat(self, tenant_id: str, account: dict) -> SecretLease:
        values={'WECHAT_APP_ID':account['wechat_app_id']}
        found=False
        try:
            for field_name,purpose,env_name in (
                ('wechat_app_secret_ref','wechat_app_secret','WECHAT_APP_SECRET'),
                ('wechat_access_token_ref','wechat_access_token','WECHAT_ACCESS_TOKEN')):
                reference=account.get(field_name)
                if reference is None:
                    continue
                # Scope validation precedes any backend read; no ambient fallback.
                key=environment_key(reference,tenant_id,self.environment,purpose)
                value=self._source.get(key)
                if not isinstance(value,str) or not value.strip():
                    raise SecretReferenceError('WECHAT_CREDENTIAL_REFERENCE_UNAVAILABLE')
                values[env_name]=value
                found=True
            if not found:
                raise SecretReferenceError('WECHAT_CREDENTIAL_REFERENCE_UNAVAILABLE')
        except SecretReferenceError:
            values.clear()
            raise
        except Exception:
            values.clear()
            raise SecretReferenceError('WECHAT_CREDENTIAL_REFERENCE_UNAVAILABLE') from None
        return SecretLease(values)
