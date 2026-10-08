"""Test-only business lifecycle overlay; never an application or DB writer.

Sparse d8a audits are not config-write authority. A newly approved root observer
must witness an authenticated formal API operation; read-only snapshots cannot
mint that evidence. Historical fixture pins remain the structural authority.
"""
import copy
import hashlib
import json
from pathlib import Path
import re

from scripts import receipt_row_canonicalization as canon

VERSION='WECHAT_PERSISTENT_CONFIG_NATIVE_GUARD_V1'
TRANSITION='WECHAT_PERSISTENT_CONFIG_API_OBSERVATION_V1'
SOURCE='d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7'
TREE='6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5'
ROOT=Path('/etc/enterprise-agent-test-wechat-persistent-config-v1')
RECOVERY='AUTHENTICATED_API_REAUTHORIZE_EXISTING_PUBLIC_CONFIG_ONLY'
ROUTES={'save':('PUT','/api/v1/profile/wechat-account'),
    'reauthorize':('PUT','/api/v1/profile/wechat-account'),
    'rotate':('POST','/api/v1/profile/wechat-account/secret/rotate'),
    'revoke':('DELETE','/api/v1/profile/wechat-account')}

class Blocked(RuntimeError):pass

def need(value,code='PERSISTENT_CONFIG_AUTHORIZATION_BLOCKED'):
    if not value:raise Blocked(code)

def raw_hash(value):
    # Only the existing historical raw seal/audit representation, NOT V2 rows.
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,default=str).encode()).hexdigest()

def row_hash(value):return canon.row_sha256('enterprise_configs',value)

def payload(value):
    result=json.loads(value['payload']) if isinstance(value['payload'],str) else copy.deepcopy(value['payload'])
    need(type(result) is dict)
    from app.wechat_action_contract import validate_enterprise_wechat_config
    validate_enterprise_wechat_config(result,value['tenant_id'])
    return result

def immutable(value):
    result=payload(value);result.pop('wechat_account',None)
    return dict(tenant_id=value['tenant_id'],payload=result)

def account(value):
    result=payload(value).get('wechat_account')
    if result is None:return None
    from app.wechat_action_contract import account_config
    result=account_config(result,value['tenant_id'])
    need(set(result)=={'wechat_app_id','account_display_name','wechat_app_secret_ref'})
    ref=result['wechat_app_secret_ref']
    need(ref['environment']=='test' and ref['provider']=='wechat' and ref['name']=='wechat-account')
    return result

def actor(data,scope,identity):
    users=[r for r in data['users'] if r['id']==identity and r['tenant_id']==scope['tenant_id']]
    need(len(users)==1 and users[0]['role']=='enterprise_admin' and users[0]['account_status']=='enabled')
    need(raw_hash(users[0])==scope['actors'].get(identity),'PERSISTENT_ACTOR_IDENTITY_DRIFT')

def event(data,identity):
    values=[r for r in data['execution_events'] if r['id']==identity]
    need(len(values)==1)
    return values[0],json.loads(values[0]['payload'])

def validate_transition(data,scope,proof,expected_before):
    need(set(proof)=={'contract','environment','source','tree','tenant_id','actor_id','operation','method','path',
        'before','after','audit_id','audit_sha256','response','authentication','observer'})
    need(proof['contract']==TRANSITION and (proof['environment'],proof['source'],proof['tree'],proof['tenant_id'])
        ==('test',scope['source'],scope['tree'],scope['tenant_id']))
    need(proof['observer']=='ROOT_APPROVED_FORMAL_API_OBSERVER' and proof['authentication']=='EXISTING_SIGNED_SESSION_AND_DB_AUTHORIZATION')
    actor(data,scope,proof['actor_id'])
    op=proof['operation'];need(op in ROUTES and (proof['method'],proof['path'])==ROUTES[op])
    before,after=proof['before'],proof['after'];row_hash(before);row_hash(after)
    need(before['tenant_id']==after['tenant_id']==scope['tenant_id'])
    need(immutable(before)==immutable(after)==immutable(scope['baseline_row']),'PERSISTENT_NON_WECHAT_MUTATION')
    need(canon.timestamptz(after['updated_at'])>=canon.timestamptz(before['updated_at']),'PERSISTENT_TIMESTAMP_REGRESSION')
    if expected_before is None:
        need(op=='reauthorize' and scope['recovery_authority']==RECOVERY,'PERSISTENT_LEGACY_EVIDENCE_GAP')
        need(account(before)==account(after) and account(after) is not None)
        # No unexplained legacy timestamp is silently blessed as historical.
        need(canon.timestamptz(before['updated_at'])==canon.timestamptz(scope['baseline_row']['updated_at']))
    else:need(row_hash(before)==expected_before,'PERSISTENT_TRANSITION_CHAIN_BROKEN')
    audit,record=event(data,proof['audit_id'])
    need(raw_hash(audit)==proof['audit_sha256'],'PERSISTENT_AUDIT_DRIFT')
    need(set(record)=={'contract','tenant_id','environment','provider','actor_id','version'}
        and record['contract']=='WECHAT_TENANT_SECRET_PROVISIONING_V1'
        and (record['tenant_id'],record['environment'],record['provider'],record['actor_id'])
        ==(scope['tenant_id'],'test','wechat',proof['actor_id']))
    old,new=account(before),account(after)
    expected='revoke' if op=='revoke' else 'rotate' if old else 'provision'
    need(audit['event_type']=='tenant_secret.'+expected and audit['conversation_id'] is None)
    response=proof['response']
    need(set(response)=={'http_status','app_secret_configured','secret_version','wechat_app_id','account_display_name'})
    need(response['http_status']==200)
    if op=='revoke':
        need(new is None and record['version'] is None and response['secret_version'] is None and response['app_secret_configured'] is False)
        need(response['wechat_app_id'] is None and response['account_display_name'] is None)
    else:
        need(new is not None and type(record['version']) is int
            and record['version']==new['wechat_app_secret_ref']['version']==response['secret_version']
            and response['app_secret_configured'] is True)
        need((response['wechat_app_id'],response['account_display_name'])==(new['wechat_app_id'],new['account_display_name']))
        if old:
            previous=old['wechat_app_secret_ref']['version'];current=new['wechat_app_secret_ref']['version']
            need(current==previous+1 if op=='rotate' else current in (previous,previous+1))
    return row_hash(after)

def normalize(data,scope,proofs,pins,*,ephemeral_tenants=(),status_reader):
    """A: immutable witness/actor structure; B: authenticated business evidence.

    The caller MUST delegate the returned copy to the unchanged FULL native
    guard. All other Tenant rows, synthetic objects/bindings/audits stay intact.
    """
    need(set(scope)=={'contract','environment','source','tree','tenant_id','tenant_kind','baseline_row',
        'baseline_raw_sha256','actors','recovery_authority'})
    need(scope['contract']==VERSION and (scope['environment'],scope['source'],scope['tree'])==('test',SOURCE,TREE))
    tenant=scope['tenant_id']
    need(isinstance(tenant,str) and re.fullmatch('[A-Za-z0-9_-]{1,128}',tenant)
        and scope['tenant_kind']=='REGISTERED_PERSISTENT_TEST' and tenant not in ephemeral_tenants)
    baseline=scope['baseline_row'];row_hash(baseline)
    need(baseline['tenant_id']==tenant and raw_hash(baseline)==scope['baseline_raw_sha256']
        and scope['baseline_raw_sha256'] in pins['enterprise_configs'],'PERSISTENT_HISTORICAL_WITNESS_MISMATCH')
    need(scope['recovery_authority'] in (None,RECOVERY) and isinstance(scope['actors'],dict) and 0<len(scope['actors'])<=8)
    values=[r for r in data['enterprise_configs'] if r['tenant_id']==tenant]
    need(len(values)==1,'PERSISTENT_TENANT_IDENTITY_DRIFT');current=values[0];row_hash(current)
    need(immutable(current)==immutable(baseline),'PERSISTENT_NON_WECHAT_MUTATION')
    need(isinstance(proofs,list) and len(proofs)<=128)
    if not proofs:
        need(row_hash(current)==row_hash(baseline),'PERSISTENT_LEGACY_EVIDENCE_GAP')
    else:
        expected=None if proofs[0]['operation']=='reauthorize' else row_hash(baseline)
        ids=[]
        for proof in proofs:
            need(proof['audit_id'] not in ids and (not ids or proof['audit_id']>ids[-1]),'PERSISTENT_AUDIT_ORDER')
            expected=validate_transition(data,scope,proof,expected);ids.append(proof['audit_id'])
        need(row_hash(current)==expected,'PERSISTENT_UNAUTHORIZED_CURRENT_MUTATION')
    # Cross-Tenant references are rejected BEFORE this reader is called. Reader
    # must be authenticated formal API status, never a caller-supplied boolean.
    cfg=account(current)
    if cfg:
        state=status_reader(tenant)
        need(set(state)=={'wechat_app_id','account_display_name','app_secret_configured','verification_status',
            'secret_version','verified_at','verification_error_code'})
        need((state['wechat_app_id'],state['account_display_name'],state['secret_version'])
            ==(cfg['wechat_app_id'],cfg['account_display_name'],cfg['wechat_app_secret_ref']['version'])
            and state['app_secret_configured'] is True)
        need(state['verification_status'] in ('unverified','connected','failed'))
        if state['verification_status']!='unverified':
            matches=[]
            for r in data['execution_events']:
                if r['event_type']!='wechat.account.verification':continue
                e=json.loads(r['payload'])
                if e.get('tenant_id')==tenant and e.get('version')==state['secret_version']:matches.append((r,e))
            need(bool(matches),'PERSISTENT_VERIFICATION_EVIDENCE_GAP');r,e=max(matches,key=lambda pair:pair[0]['id'])
            actor(data,scope,e['actor_id'])
            need(set(e)=={'tenant_id','environment','actor_id','version','status','error_code'}
                and e['environment']=='test' and e['status']==state['verification_status']
                and e['error_code']==state['verification_error_code'] and state['verified_at'] is not None)
    cut=copy.deepcopy(data)
    cut['enterprise_configs']=[copy.deepcopy(baseline) if r['tenant_id']==tenant else r for r in cut['enterprise_configs']]
    return cut

def load_authority():
    """Root approval pins observations; API/request JSON can never grant scope."""
    from app.test_tenant_seeding import native_json
    approval,_=native_json(ROOT/'approval.v1.json')
    need(set(approval)=={'contract','scope_file','scope_sha256','base_pins_file','base_pins_sha256','observations'})
    need(approval['contract']==VERSION)
    def load(path,expected):
        value,digest=native_json(path);need(digest==expected);return value
    need(approval['scope_file']=='scope.v1.json')
    scope=load(ROOT/'scope.v1.json',approval['scope_sha256'])
    original=Path(approval['base_pins_file'])
    need(original.is_absolute() and original.parent.parent==Path('/etc')
        and original.parent.name.startswith('enterprise-agent-test-successor-')
        and original.name=='predecessor-live-protected.v1.json')
    pins=load(original,approval['base_pins_sha256'])['row_hashes']
    proofs=[]
    need(isinstance(approval['observations'],list) and len(approval['observations'])<=128)
    for entry in approval['observations']:
        need(set(entry)=={'file','sha256'} and isinstance(entry['file'],str)
            and re.fullmatch(r'[A-Za-z0-9_-]+\.v1\.json',entry['file']))
        proofs.append(load(ROOT/'observations'/entry['file'],entry['sha256']))
    return scope,proofs,pins

def verify_with_native(data,scope,proofs,pins,*,native_graph,status_reader,ephemeral_tenants=()):
    cut=normalize(data,scope,proofs,pins,ephemeral_tenants=ephemeral_tenants,status_reader=status_reader)
    result=native_graph(cut)  # NEVER replace full graph validation with layer B.
    return dict(status='PASS',contract=VERSION,legacy_initial_seal_rewritten=False,native=result)

def preflight_existing_native(native,*,status_reader):
    """06's read-only adapter, after new wrapper/code identity approval.

    No ProductStore initialization, premature old graph verify, DB writes,
    service operations or automatic recovery. Snapshot schema must still equal
    the real native policy; absent authority/evidence fails closed.
    """
    policy=native.load();native.check_env();native.source_identity()
    schema,data=native.snapshot()
    need(native.digest(schema)==policy['schema015_fingerprint'] and set(data)==set(policy['table_set']),
        'PERSISTENT_NATIVE_SCHEMA_DRIFT')
    scope,proofs,pins=load_authority()
    need((native.SOURCE,native.TREE)==(scope['source'],scope['tree']),'PERSISTENT_NATIVE_SOURCE_DRIFT')
    receipt=native.receipt()
    return verify_with_native(data,scope,proofs,pins,native_graph=native.graph,status_reader=status_reader,
        ephemeral_tenants=(receipt['tenant_id'],) if receipt else ())

def observe_api_operation(scope,*,operation,actor_id,before_reader,authenticated_api_call,after_reader,audit_reader):
    """For an approved ROOT observer ONLY; no file-writing/auto-authorization.

    Callbacks must be sealed operator implementations: exact source/identity,
    signed-session formal API over loopback, read-only consistent DB snapshots.
    No API credential/secret is recorded. Caller stages a NEW observation for
    separate root approval; a standalone snapshot cannot produce this receipt.
    """
    need(operation in ROUTES)
    before=before_reader();response=authenticated_api_call()
    need(response['http_status']==200 and response['authenticated_actor_id']==actor_id)
    after=after_reader();audit=audit_reader()
    return dict(contract=TRANSITION,environment='test',source=scope['source'],tree=scope['tree'],tenant_id=scope['tenant_id'],
        actor_id=actor_id,operation=operation,method=ROUTES[operation][0],path=ROUTES[operation][1],
        before=before,after=after,audit_id=audit['id'],audit_sha256=raw_hash(audit),
        response={k:response[k] for k in ('http_status','app_secret_configured','secret_version','wechat_app_id','account_display_name')},
        authentication='EXISTING_SIGNED_SESSION_AND_DB_AUTHORIZATION',observer='ROOT_APPROVED_FORMAL_API_OBSERVER')
