"""Tenant account + action authorization over existing Runtime token/DB bindings.

This V1 resolves permissions and writes sanitized audit receipts only. It does
not execute --run, connect to WeChat, provision secrets or enable egress.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re

from app import wechat_skill
from app.security import TokenError
from app.tenant_secret_reference import SecretReferenceError, TenantSecretReferences, validate_reference

CONTRACT='WECHAT_SKILL_RUNTIME_CONTRACT_V1'
ACTIONS={
    'PREPARE':('wechat_prepare_authorize','wechat:prepare'),
    'CREATE_DRAFT':('wechat_create_draft_authorize','wechat:draft:create'),
}
ACCOUNT_FIELDS={'account_display_name','wechat_app_id','wechat_app_secret_ref','wechat_access_token_ref'}
RAW_CREDENTIAL_FIELDS={'wechatappsecret','wechataccesstoken','appidsecret','appsecret','accesstoken'}


class WechatActionError(PermissionError):
    pass


def identity(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def account_config(value: dict, tenant_id: str) -> dict:
    if (not isinstance(value,dict) or set(value)-ACCOUNT_FIELDS
            or not isinstance(value.get('account_display_name'),str)
            or not 0 < len(value['account_display_name'].strip()) <= 120
            or not isinstance(value.get('wechat_app_id'),str)
            or not re.fullmatch(r'wx[a-zA-Z0-9]{16}',value['wechat_app_id'])):
        raise WechatActionError('WECHAT_ACCOUNT_CONFIG_BLOCKED')
    result=dict(value)
    for name in ('wechat_app_secret_ref','wechat_access_token_ref'):
        if result.get(name) is not None:
            result[name]=validate_reference(result[name],tenant_id)
    return result


def validate_enterprise_wechat_config(payload: dict, tenant_id: str):
    """Reject WeChat plaintext aliases anywhere before ordinary config persistence."""
    def check(value, in_account=False):
        if isinstance(value,dict):
            for key,item in value.items():
                normalized=re.sub('[^a-z]','',str(key).lower())
                if normalized in RAW_CREDENTIAL_FIELDS:
                    raise ValueError('WECHAT_PLAINTEXT_CREDENTIAL_BLOCKED')
                if in_account and normalized in {'connected','verificationstatus','verificationbinding'}:
                    raise ValueError('WECHAT_SERVER_VERIFICATION_REQUIRED')
                if normalized in {'wechatappsecretref','wechataccesstokenref'} and item is not None:
                    try:
                        validate_reference(item,tenant_id)
                    except SecretReferenceError:
                        raise ValueError('WECHAT_ACCOUNT_CONFIG_BLOCKED') from None
                check(item,in_account or normalized=='wechataccount')
        elif isinstance(value,list):
            for item in value:check(item,in_account)
    check(payload)
    if payload.get('wechat_account') is not None:
        try:
            account_config(payload['wechat_account'],tenant_id)
        except (WechatActionError,SecretReferenceError):
            raise ValueError('WECHAT_ACCOUNT_CONFIG_BLOCKED') from None


def explicit_draft_intent(text: str) -> bool:
    # Treat quoted/example/informational instructions as data, not action grants.
    text=re.sub(r'```[\s\S]*?```|`[^`]*`','',text)
    text=re.sub(r'(?m)^\s*>.*$','',text)
    text=re.sub(r'“[^”]*”|「[^」]*」|"[^"]*"|\x27[^\x27]*\x27','',text)
    if re.search(r'(不要|不需要|不必|禁止|别|不能|无需|不|没|未).{0,12}(上传|创建|新建|放到|草稿)',text):
        return False
    if re.search(r'如何|怎么|教程|示例|演示|解释|介绍|是否|能否|假设|如果|例如|比如|可以.{0,10}吗',text):
        return False
    if re.search(r'(?:成功|失败|完成|结果|记录|了吗)|[吗么]\s*[？?]?$',text.strip()):
        return False
    # Require an imperative, not a keyword anywhere in history or article data.
    # Ambiguous/non-supported phrasing intentionally fails closed in V1.
    prefix=r'(?:(?:请(?:你)?|麻烦(?:你)?|劳烦|帮我|帮忙|现在|立即|直接)\s*){0,3}'
    object_phrase=r'(?:(?:把|将)[^:：，,\n]{1,32})?'
    action=r'(?:上传.{0,6}(?:公众号|草稿箱)|(?:创建|新建).{0,6}(?:公众号)?草稿|放到.{0,6}(?:公众号)?草稿箱)'
    return any(re.match('^'+prefix+object_phrase+action,clause.strip())
               for clause in re.split(r'[。！？!?；;\n]',text))


def trigger(text: str):
    if explicit_draft_intent(text):return 'CREATE_DRAFT'
    return wechat_skill.action_for_request(text) if wechat_skill.action_for_request(text) == 'PREPARE' else None


class WechatActionContract:
    def __init__(self, store, tokens, environment: str, secret_references=None, *, network_allowed=False):
        self.store,self.tokens,self.environment=store,tokens,environment
        self.secrets=secret_references or TenantSecretReferences(environment)
        self.network_allowed=network_allowed is True
        if self.secrets.environment != environment:
            raise SecretReferenceError('TENANT_SECRET_REFERENCE_BLOCKED')

    def _task(self, principal, task_id, action):
        from app.agent_execution import authorize_tool
        tool,scope=ACTIONS[action]
        if not principal.execution_context_id:
            raise TokenError('WECHAT_REQUIRES_PRODUCTIZED_CONTEXT')
        authorize_tool(self.store,principal,scope)
        with self.store.connection() as conn:
            row=conn.execute("SELECT t.*,c.tool_policy_snapshot,c.agent_template_version_id,a.slug AS agent_slug FROM tasks t JOIN task_agent_contexts m ON m.task_id=t.id JOIN agent_execution_contexts c ON c.id=m.context_id AND c.tenant_id=t.tenant_id AND c.agent_id=t.agent_id JOIN agent_templates a ON a.id=c.agent_id WHERE t.id=? AND t.tenant_id=? AND t.agent_id=? AND c.id=? AND t.status='running'",(task_id,principal.tenant_id,principal.agent_id,principal.execution_context_id)).fetchone()
            if not row or row['agent_slug'] != wechat_skill.AGENT_SLUG:
                raise WechatActionError('WECHAT_TASK_CONTEXT_BLOCKED')
            task=dict(row);policy=json.loads(task['tool_policy_snapshot'])
            expected=wechat_skill.verify_revision_contract()['artifact_sha256']
            refs=[x for x in policy['skill_refs'] if x['slug']==wechat_skill.SLUG]
            if len(refs)!=1 or refs[0]['version']!=wechat_skill.VERSION or refs[0]['checksum']!=expected:
                raise WechatActionError('WECHAT_SKILL_BINDING_BLOCKED')
            ref=refs[0]
            bound=conn.execute("SELECT v.checksum,v.status,p.sha256 FROM agent_template_version_skills b JOIN skill_versions v ON v.id=b.skill_version_id AND v.skill_id=b.skill_id JOIN skill_packages p ON p.skill_version_id=v.id WHERE b.agent_template_version_id=? AND b.skill_id=? AND b.skill_version_id=?",(task['agent_template_version_id'],ref['skill_id'],ref['id'])).fetchone()
            if not bound or bound['status']!='published' or bound['checksum']!=expected or bound['sha256']!=expected:
                raise WechatActionError('WECHAT_SKILL_BINDING_BLOCKED')
        if trigger(task['input_text']) is None:
            raise WechatActionError('WECHAT_SKILL_NOT_TRIGGERED')
        if action=='CREATE_DRAFT' and not explicit_draft_intent(task['input_text']):
            raise WechatActionError('WECHAT_UPLOAD_NOT_AUTHORIZED')
        return task,ref

    def resolve(self, bearer: str, task_scope: str, action: str) -> dict:
        if action not in ACTIONS:
            raise WechatActionError('WECHAT_ACTION_BLOCKED')
        _,scope=ACTIONS[action]
        principal=self.tokens.verify(bearer,scope)
        task_id=self.tokens.verify_task_scope(task_scope,principal.tenant_id)
        task,ref=self._task(principal,task_id,action)
        account_identity=None
        credential_error=None
        if action=='CREATE_DRAFT':
            try:
                value=self.store.enterprise_config(principal.tenant_id).get('wechat_account')
                account=account_config(value,principal.tenant_id)
                account_identity=identity(account)
                self.secrets.require_connected(principal.tenant_id,account)
                if not self.network_allowed:
                    raise WechatActionError('WECHAT_NETWORK_OPERATION_NOT_ALLOWED')
                lease=self.secrets.resolve_wechat(principal.tenant_id,account)
                # Permission resolution is not execution: immediately drop the lease.
                lease.close()
            except (WechatActionError,SecretReferenceError) as error:
                credential_error=error
        receipt=dict(contract=CONTRACT,tenant_id=principal.tenant_id,environment=self.environment,
            agent_id=principal.agent_id,agent_revision_id=task['agent_template_version_id'],
            task_id=task_id,skill_revision_id=ref['id'],skill_slug=ref['slug'],skill_version=ref['version'],
            skill_checksum=ref['checksum'],action=action,account_config_identity=account_identity,
            execution_status='BLOCKED_NOT_EXECUTED' if credential_error else 'PERMISSION_RESOLVED_NOT_EXECUTED',draft_media_id=None,
            network_targets=['api.weixin.qq.com:443'] if action=='CREATE_DRAFT' and not credential_error else [],
            timestamp=datetime.now(timezone.utc).isoformat())
        self.store.log_event(task.get('conversation_id'),'wechat.action.permission',receipt)
        if credential_error:
            raise credential_error
        return receipt

    def prepare_secret_injection(self, bearer: str, task_scope: str):
        """Server execution hook; returns a lease, never a model/API payload.

        Re-check authorization/config immediately before a future child launch.
        This task does not call or implement the WeChat network runner.
        """
        principal=self.tokens.verify(bearer,ACTIONS['CREATE_DRAFT'][1])
        task_id=self.tokens.verify_task_scope(task_scope,principal.tenant_id)
        self._task(principal,task_id,'CREATE_DRAFT')
        account=account_config(self.store.enterprise_config(principal.tenant_id).get('wechat_account'),principal.tenant_id)
        self.secrets.require_connected(principal.tenant_id,account)
        if not self.network_allowed: raise WechatActionError('WECHAT_NETWORK_OPERATION_NOT_ALLOWED')
        lease=self.secrets.resolve_wechat(principal.tenant_id,account)
        def current_check():
            current=self.tokens.verify(bearer,ACTIONS['CREATE_DRAFT'][1])
            self.tokens.verify_task_scope(task_scope,current.tenant_id)
            self._task(current,task_id,'CREATE_DRAFT')
            latest=account_config(self.store.enterprise_config(current.tenant_id).get('wechat_account'),current.tenant_id)
            if latest != account or not self.network_allowed:
                raise WechatActionError('WECHAT_ACCOUNT_CONFIG_BLOCKED')
            self.secrets.require_connected(current.tenant_id,latest)
        lease._current_check=current_check
        return lease
