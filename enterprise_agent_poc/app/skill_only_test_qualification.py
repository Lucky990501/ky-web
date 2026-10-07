"""Native Test authority, never a Runtime Test result or public Run option.

The immutable approval is loaded by the existing controlled entry. API callers
cannot construct this authority or select its location. Normal resolution never
calls resolve_context; the separately sealed MCP must consume the same approval.
"""
from dataclasses import dataclass
import hashlib
import json
import os
import re
from uuid import UUID, uuid4

from app.agent_productization import MODEL_CONFIGS, TOOL_CAPABILITIES, canonical

MODE = 'SKILL_ONLY_TEST_QUALIFIED'
CONTRACT = 'CONTROLLED_SKILL_TEST_ELIGIBILITY_V1'
ENTRY = 'TEST_CONTROLLED_SKILL_ACTION_V1'
SKILL = 'wechat-html-draft'
ARTIFACT = '4a140c878ae7057583089a4410cd1a23c5c95664988e6dd9700f75ed51d3b18c'
LOCK = '3a5f482475c7cafe660df2d171f369a32832ac91e12e9b0ddffde5c5d6ac8abe'
TOOLS = {'skill_action_execute', 'wechat_prepare_authorize', 'wechat_create_draft_authorize'}
SCOPES = {'skills:execute', 'wechat:prepare', 'wechat:draft:create'}


def reject():
    raise PermissionError('SKILL_CONTROLLED_ACTION_AUTH_BLOCKED')


def is_qualified(context):
    return json.loads(context['tool_policy_snapshot']).get('eligibility_mode') == MODE


def validate(approval):
    """Qualification is a root-owned source-bound approval, not a request bool."""
    q = approval['qualification']
    keys = {'contract', 'eligibility_mode', 'environment', 'entry', 'tenant_id',
            'agent_id', 'agent_revision_id', 'skill_key', 'revision', 'action',
            'configuration_fingerprint', 'artifact_sha256', 'runtime_lock_sha256',
            'source_commit', 'source_tree', 'authority_id', 'provider_calls',
            'image_calls', 'wechat_calls'}
    try:
        if not isinstance(q, dict) or set(q) != keys:
            reject()
        if (q['contract'] != CONTRACT or q['eligibility_mode'] != MODE
                or q['environment'] != 'test' or approval['environment'] != 'test'
                or q['entry'] != ENTRY or q['action'] != 'PREPARE'
                or q['skill_key'] != SKILL or q['artifact_sha256'] != ARTIFACT
                or q['runtime_lock_sha256'] != LOCK):
            reject()
        for field in ('tenant_id', 'source_commit', 'source_tree', 'authority_id'):
            if q[field] != approval[field]:
                reject()
        for field in ('agent_id', 'agent_revision_id', 'revision'):
            if str(UUID(q[field])) != q[field]:
                reject()
        if not re.fullmatch('[a-f0-9]{64}', q['configuration_fingerprint']):
            reject()
        if any(type(q[k]) is not int or q[k] != 0 for k in ('provider_calls', 'image_calls', 'wechat_calls')):
            reject()
        # The qualified permission consumer is new code too: old b0 MCP would
        # reject configured instances. A mixed old listener is not compatible.
        if (approval['dispatch_source'], approval['dispatch_tree']) != (q['source_commit'], q['source_tree']):
            reject()
    except (KeyError, TypeError, ValueError):
        reject()
    return q


@dataclass(frozen=True)
class Authority:
    approval_json: str
    approval_identity: str
    request_action: str

    def receipt(self):
        approval = json.loads(self.approval_json)
        q = validate(approval)
        return dict(q, approval_identity=self.approval_identity,
                    caller_id=approval['user_id'], request_action=self.request_action,
                    negative_probe=self.request_action == 'CREATE_DRAFT')


def authority(approval, approval_sha, request):
    q = validate(approval)
    if (not re.fullmatch('[a-f0-9]{64}', approval_sha)
            or (request['tenant_id'], request['agent_id'], request['skill_key'], request['revision'])
            != (q['tenant_id'], q['agent_id'], q['skill_key'], q['revision'])
            or request['action'] not in ('PREPARE', 'CREATE_DRAFT')):
        reject()
    # CREATE_DRAFT is only the existing disabled negative credential probe,
    # never an execution grant. Action permissions remain independently checked.
    return Authority(canonical(approval), approval_sha, request['action'])


def resolve_context(resolver, conn, tenant, user, agent_id, proof):
    if resolver.settings.environment != 'test' or type(proof) is not Authority:
        reject()
    from app.controlled_skill_action import load_approval
    approved, approved_sha = load_approval()
    if canonical(approved) != proof.approval_json or approved_sha != proof.approval_identity:
        reject()
    receipt = proof.receipt()
    if (tenant, user, agent_id) != (receipt['tenant_id'], receipt['caller_id'], receipt['agent_id']):
        reject()
    from app.agent_availability import release_aborted
    if release_aborted(conn, agent_id, postgres=resolver.store.is_postgres):
        reject()
    member = conn.execute("SELECT role,account_status FROM users WHERE id=? AND tenant_id=?", (user, tenant)).fetchone()
    template = resolver.catalog._template(conn, agent_id)
    version = dict(resolver.catalog._version(conn, agent_id, receipt['agent_revision_id']))
    instance = conn.execute('SELECT * FROM tenant_agent_instances WHERE tenant_id=? AND agent_id=?' +
                            (' FOR UPDATE' if resolver.store.is_postgres else ''), (tenant, agent_id)).fetchone()
    if (not member or member['role'] != 'member' or member['account_status'] != 'enabled'
            or template['definition_source'] != 'productized'
            or template['slug'] != 'wechat-official-account-writing'
            or version['status'] not in ('draft', 'published')
            or version['configuration_fingerprint'] != receipt['configuration_fingerprint']
            or not instance or instance['status'] != 'configured'
            or instance['agent_template_version_id'] != version['id']
            or version['output_policy'] != 'text'):
        reject()
    if resolver.catalog._validation_errors(conn, version):
        reject()
    resolver._ready(conn, tenant, version)
    manifest, refs = resolver._skills(conn, version['id'])
    if (len(refs) != 1 or (refs[0]['slug'], refs[0]['id'], refs[0]['version'], refs[0]['checksum'])
            != (SKILL, receipt['revision'], '1.0.0', ARTIFACT)):
        reject()
    tools = [dict(row) for row in conn.execute('SELECT tool_capability_id,invocation_requirement FROM agent_template_version_tools WHERE agent_template_version_id=? ORDER BY tool_capability_id', (version['id'],))]
    if ({t['tool_capability_id'] for t in tools} != TOOLS
            or any(t['invocation_requirement'] != 'optional' for t in tools)):
        reject()
    # Snapshot declared model metadata for the unchanged DB schema only. It is
    # NOT runtime evidence. Qualified profiles cannot enter a model consumer.
    model = MODEL_CONFIGS.get(version['model_config_id'])
    if not model:
        reject()
    policy = dict(scopes=sorted(SCOPES), required_tools=[], bindings=tools,
                  skill_refs=refs, runtime_test=False, grounding={'enabled': False},
                  eligibility_mode=MODE, qualification_receipt=receipt,
                  model_execution_disabled=True,
                  display={k: template[k] for k in ('name', 'description', 'icon', 'slug')})
    context = dict(id=str(uuid4()), tenant_id=tenant, agent_id=agent_id,
        instance_id=instance['instance_id'], agent_template_version_id=version['id'],
        definition_source='productized', configuration_fingerprint=version['configuration_fingerprint'],
        persona_snapshot=version['persona'], runtime_provider=model['runtime_provider'],
        model_config_id=model['id'], model_provider_id_snapshot=model['provider'],
        model_id_snapshot=model['model'], reasoning_level_snapshot=model['reasoning_effort'],
        skill_manifest_snapshot=canonical(manifest), tool_policy_snapshot=canonical(policy),
        profile_hash_version='v2', **{k: version[k] for k in ('knowledge_requirement',
        'asset_requirement', 'enterprise_config_requirement', 'output_policy', 'credit_cost')})
    context['runtime_profile_id'] = 'v2-' + hashlib.sha256(canonical(context).encode()).hexdigest()[:24]
    conn.execute(f"INSERT INTO agent_execution_contexts({','.join(context)}) VALUES ({','.join('?' for _ in context)})", tuple(context.values()))
    return context


def check_context(conn, context, *, environment):
    """MCP and Task consumers re-read native authority and exact current binding."""
    if environment != 'test':
        reject()
    from app.controlled_skill_action import load_approval
    approval, approval_sha = load_approval()
    q = validate(approval)
    policy = json.loads(context['tool_policy_snapshot'])
    receipt = policy.get('qualification_receipt')
    if not isinstance(receipt, dict):
        reject()
    expected = Authority(canonical(approval), approval_sha, receipt.get('request_action')).receipt()
    if (receipt != expected or policy.get('eligibility_mode') != MODE
            or policy.get('runtime_test') is not False or policy.get('model_execution_disabled') is not True
            or set(policy['scopes']) != SCOPES or policy['required_tools']
            or receipt['request_action'] not in ('PREPARE', 'CREATE_DRAFT')
            or (context['tenant_id'], context['agent_id'], context['agent_template_version_id'], context['configuration_fingerprint'])
            != (q['tenant_id'], q['agent_id'], q['agent_revision_id'], q['configuration_fingerprint'])):
        reject()
    user = conn.execute("SELECT role,account_status FROM users WHERE id=? AND tenant_id=?", (receipt['caller_id'], q['tenant_id'])).fetchone()
    instance = conn.execute('SELECT * FROM tenant_agent_instances WHERE instance_id=? AND tenant_id=? AND agent_id=?',
                            (context['instance_id'], q['tenant_id'], q['agent_id'])).fetchone()
    version = conn.execute('SELECT status,configuration_fingerprint FROM agent_template_versions WHERE id=? AND agent_template_id=?', (q['agent_revision_id'], q['agent_id'])).fetchone()
    if (not user or user['role'] != 'member' or user['account_status'] != 'enabled'
            or not instance or instance['status'] != 'configured'
            or instance['agent_template_version_id'] != q['agent_revision_id']
            or not version or version['status'] not in ('draft', 'published')
            or version['configuration_fingerprint'] != q['configuration_fingerprint']):
        reject()
    skills = conn.execute('SELECT s.slug,v.id,v.status,v.checksum,p.sha256 FROM agent_template_version_skills b JOIN skills s ON s.id=b.skill_id JOIN skill_versions v ON v.id=b.skill_version_id AND v.skill_id=b.skill_id JOIN skill_packages p ON p.skill_version_id=v.id WHERE b.agent_template_version_id=?', (q['agent_revision_id'],)).fetchall()
    actual = [dict(row) for row in conn.execute('SELECT tool_capability_id,invocation_requirement FROM agent_template_version_tools WHERE agent_template_version_id=? ORDER BY tool_capability_id', (q['agent_revision_id'],))]
    if (len(skills) != 1 or (skills[0]['slug'], skills[0]['id'], skills[0]['status'], skills[0]['checksum'], skills[0]['sha256'])
            != (SKILL, q['revision'], 'published', ARTIFACT, ARTIFACT)
            or actual != policy['bindings'] or {t['tool_capability_id'] for t in actual} != TOOLS):
        reject()
    return receipt


def authorize_scope(conn, context, scope):
    receipt = check_context(conn, context, environment=os.environ.get('APP_ENV'))
    # Only the one signed running controlled Task's scopes, never arbitrary MCP.
    task = conn.execute("SELECT t.input_text FROM tasks t JOIN task_agent_contexts m ON m.task_id=t.id WHERE m.context_id=? AND t.tenant_id=? AND t.user_id=? AND t.agent_id=? AND t.status='running'", (context['id'], receipt['tenant_id'], receipt['caller_id'], receipt['agent_id'])).fetchone()
    from app.controlled_skill_action import TASK_MARKER
    permitted = {'skills:execute', 'wechat:prepare'} if receipt['request_action'] == 'PREPARE' else {'skills:execute', 'wechat:draft:create'}
    if not task or not task['input_text'].endswith(TASK_MARKER) or scope not in permitted:
        reject()
