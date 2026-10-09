"""Versioned adaptation of formal prepare-exact-admin-authority-173ce0c-v1.

Upstream SHA: 173ce0c289331c1c6091614a51d3e4e97e62eefe59b7fdca7bf9fc15c53e9bb0.
This issuer owns ONE new isolated predecessor. It cannot issue PRIMARY policy,
reuse a populated database, accept a DSN argument, or renew an existing lease.
Root release control must install an exact independent issuance request first.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4

sys.path.append(str(Path(__file__).resolve().parents[1]))
import scripts
scripts.__path__ = [str(Path(__file__).resolve().parent), *scripts.__path__]
from scripts import native_parent_contract as parent
from scripts import exact_test_admin_lifecycle as admin
from scripts import wechat_runtime_test_lifecycle_guard as lifecycle


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value, mode=0o444):
    raw = (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)+'\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def checkout(entry, role):
    from app.test_runtime_tooling import verify_checkout
    expected = parent.HOME / ('releases' if role == 'application' else 'shared/source-qualifications')
    lifecycle.need(set(entry) == {'path', 'source', 'tree'} and Path(entry['path']).parent == expected,
                   'ISSUER_SOURCE_LOCATION')
    root = Path(entry['path'])
    files = {p.relative_to(root).as_posix(): sha(p) for p in root.rglob('*')
             if '.git' not in p.relative_to(root).parts and p.is_file()}
    verify_checkout(root, dict(entry, files=files))
    result = subprocess.run(['git', '--no-optional-locks', '-C', str(root), 'status', '--porcelain',
        '--untracked-files=all'], check=True, capture_output=True, timeout=20)
    lifecycle.need(not result.stdout, 'ISSUER_DIRTY_SOURCE')
    # No workspace secrets are carried into the runtime; only generated test
    # credentials outside either checkout may exist.
    lifecycle.need(not any(Path(p).name == '.env' for p in files), 'ISSUER_ENV_FILE_REJECTED')
    return files


def bootstrap(store, pair, request):
    """Trusted provisioning on an attested empty cluster; no real account input.

    Uses formal migration, User, Registry, Agent authoring and epoch primitives.
    It never grants platform_admin or publishes/enables an Agent. The first
    configured Instance will be written by the formal Runtime Test executor.
    """
    from scripts import migrate, compatibility_epoch
    from app.store import POCStore
    from app.product_store import ProductStore
    from app.agent_productization import AgentProductization
    from app.agent_execution import ExecutionResolver
    from app.skill_registry import SkillRegistry
    from app import wechat_skill
    from app.skill_dispatch import add_dispatch_binding
    from app.auth import hash_password
    from app.settings import settings
    with store.connection() as conn:
        db = dict(conn.execute("SELECT host(inet_server_addr()) AS host,inet_server_port() AS port,"
            "current_database() AS name,current_user AS role,current_setting('server_version_num')::int AS server_version,"
            "(SELECT system_identifier::text FROM pg_control_system()) AS system_identifier").fetchone())
        lifecycle.need(db == request['database'], 'ISSUER_EXACT_EMPTY_CLUSTER')
        lifecycle.need(not conn.execute("SELECT 1 FROM pg_tables WHERE schemaname='public'").fetchone(),
                       'ISSUER_EXISTING_STATE_REJECTED')
    lifecycle.need(migrate.up(store) == 0, 'ISSUER_MIGRATION_FAILED')
    # Existing controlled epoch writer, protected by the empty-state proof and
    # one-shot root request. No epoch reset, migration modification or waiver.
    with store.connection() as conn:
        compatibility_epoch.read_state(conn, lock=True)
        lifecycle.need(not compatibility_epoch.has_productized_data(conn), 'ISSUER_EPOCH_ALREADY_HAS_DATA')
        compatibility_epoch._write_epoch_transition(conn._connection,
            'native-isolated-'+request['run_id'], pair['application']['source'])
    product = ProductStore(store); product.initialize()  # No tenant exists yet.
    tenant = 'native-isolated-'+request['run_id']
    password = secrets.token_urlsafe(32)
    with store.connection() as conn:
        conn.execute('INSERT INTO tenants(id,name,poc_api_key) VALUES (?,?,?)',
                     (tenant, 'Synthetic Native Fixture', secrets.token_urlsafe(40)))
        conn.execute('INSERT INTO enterprise_configs(tenant_id,payload) VALUES (?,?)',
                     (tenant, json.dumps({'data_classification': parent.REALM, 'brand_name': 'Synthetic Native Fixture'})))
    product.create_user(tenant, 'native-fixture@example.invalid', hash_password(password), 'Synthetic Native Principal', 'member')
    actor = product.user_by_email('native-fixture@example.invalid')['id']
    registry = SkillRegistry(store, settings.data_dir/'skill-registry',
                             Path(pair['application']['path'])/'enterprise_agent_poc/skill_packages')
    registry.initialize()
    catalog = AgentProductization(store, 'test'); catalog.initialize()
    item = catalog.create_template({'slug': wechat_skill.AGENT_SLUG, 'name': 'Synthetic Native Agent'}, actor)
    item = catalog.create_version(item['id'], {'persona': 'Synthetic native lifecycle verification'}, actor)
    agent, revision = item['id'], item['versions'][0]['id']
    imported = wechat_skill.import_revision(registry, actor)
    registry.publish(imported['id'], actor)  # Existing immutable Skill; NOT Agent Publish.
    skill = registry.version(imported['id'])
    wechat_skill.apply_binding_plan(catalog, skill, revision, actor)
    add_dispatch_binding(catalog, agent, revision, actor)
    resolver = ExecutionResolver(store, registry, catalog, settings, tenant)
    resolver.initialize_local(); catalog.execution_resolver = resolver
    catalog.validate(agent, revision, actor)
    credential = parent.HOME/'private/bootstrap-login.json'
    write_json(credential, {'account': 'native-fixture@example.invalid', 'password': password}, 0o600)
    return dict(tenant_id=tenant, principal_id=actor, agent_id=agent, revision_id=revision), skill


def issue():
    from app.test_tenant_seeding import native_json
    from app.test_runtime_tooling import APPLICATION, ISOLATED_CONTRACT
    from app.store import POCStore
    lifecycle.need(os.name == 'posix' and os.geteuid() == 0 and os.environ.get('APP_ENV') == 'test'
        and sys.dont_write_bytecode and os.environ.get('PYTHONDONTWRITEBYTECODE') == '1', 'ISSUER_ROOT_TEST_ONLY')
    lifecycle.need(os.environ.get('ENTERPRISE_POC_DATABASE_URL') == parent.DSN, 'ISSUER_FIXED_DATABASE')
    parent.require_runtime_environment()
    request, request_sha = native_json(parent.ROOT/'issuance-request.v1.json')
    lifecycle.need(set(request) == {'contract', 'authorization', 'application', 'tooling', 'database', 'run_id',
        'production_authority', 'provider_budget', 'issuer_sha256'} and request['contract'] == parent.REALM
        and request['authorization'] == 'NATIVE_PARENT_CHAIN_ISOLATION_REFACTOR_APPROVED'
        and request['production_authority'] is False and request['provider_budget'] == 0
        and request['issuer_sha256'] == sha(Path(__file__)), 'ISSUER_INDEPENDENT_REQUEST')
    from uuid import UUID
    lifecycle.need(str(UUID(request['run_id'])) == request['run_id'], 'ISSUER_RUN_ID')
    lifecycle.need(Path(request['application']['path'])/'enterprise_agent_poc' == APPLICATION
        and Path(request['tooling']['path'])/'enterprise_agent_poc' == admin.PROJECT, 'ISSUER_LOADED_SOURCE_PAIR')
    lifecycle.need(not (parent.ROOT/'runtime-pair.v1.json').exists(), 'ISSUER_NO_REISSUE')
    file_maps = {role: checkout(request[role], role) for role in ('application', 'tooling')}
    pair = {'contract': ISOLATED_CONTRACT, 'environment': 'test', 'production_authority': False,
            'authority': request['authorization']}
    for role in ('application', 'tooling'):
        file_sha = write_json(parent.ROOT/(role+'-files.v1.json'), file_maps[role])
        pair[role] = dict(request[role], files_sha256=file_sha)
    pair_sha = write_json(parent.ROOT/'runtime-pair.v1.json', pair)
    store = POCStore(parent.DSN)
    identity, skill = bootstrap(store, pair, request)
    receipt_sha = write_json(parent.ROOT/'bootstrap-receipt.v1.json', {
        'contract': 'ISOLATED_NATIVE_BOOTSTRAP_V1', 'empty_database_verified': True,
        'active_admins': 0, 'pair_sha256': pair_sha, 'identity': identity, 'request_sha256': request_sha})
    context = dict(contract=parent.VERSION, realm=parent.REALM, environment='test', production_authority=False,
        security=dict(pair_sha256=pair_sha, database=request['database'], issuer_sha256=request['issuer_sha256'],
            schema_sha256='0'*64, predecessor=dict(kind='ISOLATED_EMPTY_DATABASE',
            system_identifier=request['database']['system_identifier'], bootstrap_receipt_sha256=receipt_sha)),
        state=dict(identity, row_pins={}, credit_account={}))
    schema, data = parent.snapshot(store, context)
    lifecycle.need(not data['platform_admins'] and not data['tenant_agent_instances']
                   and not data['tasks'], 'ISSUER_NO_EXECUTION_OR_ADMIN')
    context['security']['schema_sha256'] = lifecycle.digest(schema)
    context['state']['row_pins'] = {t: sorted(lifecycle.digest(row) for row in rows) for t, rows in data.items()}
    lifecycle.need(len(data['credit_accounts']) == 1, 'ISSUER_SINGLE_CREDIT_ACCOUNT')
    context['state']['credit_account'] = data['credit_accounts'][0]
    parent.validate_context(context, pair, pair_sha)
    context_sha = write_json(parent.ROOT/'parent-context.v1.json', context)
    actor = lifecycle.one(data, 'users', 'id', identity['principal_id'])
    revision = lifecycle.one(data, 'agent_template_versions', 'id', identity['revision_id'])
    template = lifecycle.one(data, 'agent_templates', 'id', identity['agent_id'])
    now = datetime.now(timezone.utc)
    scope = dict(contract=admin.CONTRACT, environment='test', base_source=admin.BASE_SOURCE, base_tree=admin.BASE_TREE,
        **identity, principal_sha256=admin.principal_hash(actor), original_role='member',
        agent_slug='wechat-official-account-writing', fingerprint=revision['configuration_fingerprint'],
        run_id=request['run_id'], operations=sorted(admin.OPS), issued_at=now.isoformat(),
        expires_at=(now+timedelta(minutes=55)).isoformat(), production_authority=False, publication_authorized=False)
    for role in ('application', 'tooling'):
        scope[role+'_source'], scope[role+'_tree'] = request[role]['source'], request[role]['tree']
    admin.validate_scope(scope)
    scope_sha = write_json(parent.ROOT/'scope.v1.json', scope)
    runtime_scope = dict(contract=lifecycle.VERSION, environment='test', source=lifecycle.SOURCE, tree=lifecycle.TREE,
        tenant_id=identity['tenant_id'], agent_id=identity['agent_id'], agent_slug=scope['agent_slug'],
        revision_id=identity['revision_id'], fingerprint=scope['fingerprint'], actor_id=identity['principal_id'],
        actor_sha256=lifecycle.digest(actor), maximum_runtime_tests=8, publication_allowed=False,
        skill_id=skill['skill_id'], skill_revision_id=skill['id'], skill_sha256=skill['checksum'],
        model_config_id='codex-deepseek-v4-pro-high')
    lifecycle.identities(data, runtime_scope)
    from scripts.wechat_runtime_native_successor import VERSION
    policy = dict(contract=VERSION, application_source=scope['application_source'], application_tree=scope['application_tree'],
        files=file_maps['application'], scope_sha256=lifecycle.digest(scope), schema_fingerprint=lifecycle.digest(schema),
        table_set=sorted(data), runtime_scope=runtime_scope, anchors=dict(agent_templates=template,
        agent_template_versions=revision, tenant_agent_instances=None), parent_pins=context['state']['row_pins'],
        ordinary_principals=[identity['principal_id']], parent_files={}, import_files={})
    policy_sha = write_json(parent.ROOT/'native-policy.v2.json', policy)
    codes = ['scripts/exact_test_admin_lifecycle.py', 'scripts/run_exact_test_admin_lifecycle.py',
             'app/test_exact_admin_gate.py', 'app/test_runtime_tooling.py', 'app/product_service.py', 'app/worker.py', 'app/main.py']
    write_json(parent.ROOT/'approval.v2.json', dict(contract=admin.APPROVAL_CONTRACT, scope_sha256=scope_sha,
        pair_sha256=pair_sha, independent_approval=dict(authority='ISOLATED_NATIVE_RELEASE_CONTROL',
        authorization='NATIVE_PARENT_CHAIN_ISOLATION_REFACTOR_APPROVED', production_authority=False),
        code_sha256={p: sha((APPLICATION if p.startswith('app/') else admin.PROJECT)/p) for p in codes}))
    codes = ['wechat_runtime_native_successor', 'wechat_runtime_test_lifecycle_guard', 'receipt_row_canonicalization',
             'wechat_historical_prepare', 'wechat_historical_actions', 'native_parent_contract',
             'prepare_exact_admin_authority', 'runtime_recovery_operator']
    write_json(parent.ROOT/'native-approval.v2.json', dict(contract=VERSION, policy_sha256=policy_sha,
        parent_context_sha256=context_sha, independent_approval='NATIVE_PARENT_CHAIN_ISOLATION_REFACTOR_APPROVED',
        code_sha256={'scripts/'+p+'.py': sha(admin.PROJECT/'scripts'/(p+'.py')) for p in codes}))
    return {'status': 'ISOLATED_NATIVE_AUTHORITY_ISSUED', 'contract': parent.VERSION,
            'identity': identity, 'run_id': request['run_id'], 'parent_context_sha256': context_sha,
            'active_admins': 0, 'provider_calls': 0}


if __name__ == '__main__':
    try:
        print(json.dumps(issue(), sort_keys=True))
    except Exception as exc:
        print(json.dumps({'status': 'BLOCKED', 'code': str(exc) if isinstance(exc, lifecycle.Blocked)
                          else 'ISOLATED_NATIVE_ISSUANCE_FAILED'}))
        raise SystemExit(2)
