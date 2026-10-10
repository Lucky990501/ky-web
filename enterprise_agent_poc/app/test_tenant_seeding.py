"""Read-only startup exclusions from native Test authority/Provision evidence.

Not tenant config, a public parameter, an enablement grant or a new registry.
The operator installs a NEW Source-bound declaration; old scopes stay sealed.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit
from uuid import UUID

CONTRACT = 'TEST_TENANT_AUTO_SEEDING_EXCLUSIONS_V1'
REQUIRED_ENV = 'ENTERPRISE_POC_TEST_TENANT_SEEDING_POLICY_REQUIRED'
AUTHORITY_ROOT = Path('/etc/enterprise-agent-test-tenant-seeding-v1')
APPROVAL_PATH = AUTHORITY_ROOT / 'approval.v1.json'
EVIDENCE_ROOT = Path('/opt/enterprise-agent-workbench-test/release-evidence')
NATIVE_SCOPE_PARENT = Path('/etc')
PROJECT = Path(__file__).resolve().parents[1]
TARGET = dict(address='127.0.0.1', port=55432, database='enterprise_agent_test', db_role='enterprise_agent_test')
MINIMAL_OBJECTS = ['tenants', 'enterprise_configs', 'users']


def reject():
    raise RuntimeError('TEST_TENANT_SEEDING_POLICY_BLOCKED')


def require(value):
    if not value: reject()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, default=str).encode()).hexdigest()


def native_json(path, *, root_owned=True):
    """No symlink/writable authority; mutable evidence still requires exact SHA."""
    require(os.name == 'posix')
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not info.st_mode & 0o022
                and info.st_uid in ((0,) if root_owned else (0, os.getuid())))
        if root_owned: require(info.st_gid == 0)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o222 and info.st_size <= 131072)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        current = os.fstat(stream.fileno())
        require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino))
        raw = stream.read(131073)
    require(len(raw) <= 131072)
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def source_identity():
    from app.skill_python_runtime import git_identity
    return git_identity(PROJECT)


def check_database(conn, store):
    require(store.is_postgres)
    db = urlsplit(store.database_url)
    require(db.scheme in ('postgres', 'postgresql') and not db.query and not db.fragment
            and (db.hostname, db.port, db.username, db.path)
            == (TARGET['address'], TARGET['port'], TARGET['db_role'], '/' + TARGET['database']))
    # host(inet) is the canonical HOST, never a fragile string '/32' trim.
    row = conn.execute('SELECT host(inet_server_addr()) AS address, inet_server_port() AS port, '
        'current_database() AS database, current_user AS db_role').fetchone()
    require(dict(row) == TARGET)


def tenant_row_hash(scope, receipt, scope_file):
    """Version dispatch, not a raw-hash fallback for an unrecognized receipt."""
    if scope['contract'] == 'TEST_ONLY_MINIMAL_PROVISION_V1':
        require(scope_file.endswith('.v1.json') and not
                ({'receipt_version', 'cleanup_authority'} & set(scope) or
                 {'receipt_version', 'cleanup_authority'} & set(receipt)))
        return digest  # Immutable historical V1 row semantics only.
    require(scope['contract'] == 'TEST_ONLY_MINIMAL_PROVISION_V2')
    # The declared copy is versioned; its separate active native Scope binding
    # below must still exist. Neither copy may be an archived *.approved file.
    require(scope_file == 'provision-scope.v2.json')
    canonical_path = PROJECT / 'scripts/receipt_row_canonicalization.py'
    require(not canonical_path.is_symlink() and hashlib.sha256(canonical_path.read_bytes()).hexdigest()
            == '1c62c7805b473abfb906f88642c36d4e77e0a7a6c9537f31f60e1ff0b8aff09a')
    from scripts import receipt_row_canonicalization as canonical
    require(Path(canonical.__file__).resolve() == canonical_path.resolve())
    VERSION = canonical.VERSION
    require(set(scope) == {'contract', 'authority_id', 'purpose', 'environment', 'application_source',
        'application_tree', 'tooling_source', 'tooling_tree', 'tenant_id', 'user_id', 'user_email',
        'allowed_objects', 'production_deploy_authority', 'budget', 'run_id', 'receipt_version', 'cleanup_authority'})
    require(scope['receipt_version'] == receipt.get('receipt_version') == VERSION
            and scope['cleanup_authority'] == 'EXACT_RECEIPT_OWNED_OBJECTS_ONLY')
    require(set(receipt) == {'contract', 'receipt_version', 'status', 'scope_sha256', 'run_id',
        'tooling_source', 'tooling_tree', 'application_source', 'application_tree', 'tenant_id', 'user_id',
        'database', 'created_records', 'initially_absent_secret_paths', 'credential_path',
        'workspace_create', 'business_agent_enable', 'wechat_calls', 'provider_calls', 'image_calls'})
    require(all(isinstance(scope[k], str) and re.fullmatch('[a-f0-9]{40}', scope[k])
                for k in ('tooling_source', 'tooling_tree')))
    require(isinstance(scope['user_id'], str) and str(UUID(scope['user_id'])) == scope['user_id'])
    require(isinstance(scope['user_email'], str) and bool(scope['user_email']))
    require(set(receipt['created_records']) == set(MINIMAL_OBJECTS))
    for table, records in receipt['created_records'].items():
        require(isinstance(records, list) and len(records) == 1
                and set(records[0]) == {'primary_key', 'row_sha256'}
                and records[0]['primary_key'] == (scope['user_id'] if table == 'users' else scope['tenant_id'])
                and isinstance(records[0]['row_sha256'], str) and re.fullmatch('[a-f0-9]{64}', records[0]['row_sha256']))
    return lambda value: canonical.row_sha256('tenants', value, VERSION)


def authorized_exclusions(conn, store):
    # Production/development retain the original behavior without reading Test
    # files. The flag requests stronger validation, never grants an exemption.
    if os.environ.get('APP_ENV', 'development').lower() != 'test': return frozenset()
    mode = os.environ.get(REQUIRED_ENV, 'false').lower()
    require(mode in ('false', 'true'))
    if mode == 'false': return frozenset()
    try:
        from app.test_runtime_tooling import authority_root, ISOLATED_AUTHORITY, tooling_module
        if authority_root() == ISOLATED_AUTHORITY:
            parent = tooling_module('native_parent_contract')
            context = parent.load_context()
            parent.check_database(conn, store, context)
            state = context['state']
            rows = conn.execute('SELECT to_jsonb(t) AS row FROM tenants t').fetchall()
            require(len(rows) == 1 and rows[0]['row']['id'] == state['tenant_id']
                    and [parent.digest(rows[0]['row'])] == state['row_pins']['tenants'])
            return frozenset((state['tenant_id'],))
        check_database(conn, store)
        approval, _ = native_json(APPROVAL_PATH)
        require(set(approval) == {'contract', 'authority_id', 'environment', 'source_commit', 'source_tree',
            'database', 'production_deploy_authority', 'budget', 'exclusions'})
        require(approval['contract'] == CONTRACT and approval['environment'] == 'test'
                and approval['production_deploy_authority'] is False and approval['database'] == TARGET
                and approval['budget'] == dict(wechat=0, provider=0, image=0)
                and all(type(v) is int for v in approval['budget'].values()))
        require(all(isinstance(approval[k], str) and re.fullmatch('[a-f0-9]{40}', approval[k])
                    for k in ('source_commit', 'source_tree')))
        require(source_identity() == {k: approval[k] for k in ('source_commit', 'source_tree')})
        require(isinstance(approval['authority_id'], str) and re.fullmatch('[A-Za-z0-9_-]{1,128}', approval['authority_id']))
        require(isinstance(approval['exclusions'], list) and len(approval['exclusions']) <= 32)
        excluded = set()
        for entry in approval['exclusions']:
            fields = {'scope_file', 'scope_sha256', 'receipt_file', 'receipt_sha256'}
            require(set(entry) in (fields, fields | {'active_scope_file'}))
            require(isinstance(entry['scope_file'], str) and re.fullmatch(r'[A-Za-z0-9_-]+\.v[1-9][0-9]*\.json', entry['scope_file']))
            require(all(isinstance(entry[k], str) and re.fullmatch('[a-f0-9]{64}', entry[k])
                        for k in ('scope_sha256', 'receipt_sha256')))
            scope, sha = native_json(AUTHORITY_ROOT / entry['scope_file'])
            require(sha == entry['scope_sha256'])
            if scope['contract'] == 'TEST_ONLY_MINIMAL_PROVISION_V2':
                require(set(entry) == fields | {'active_scope_file'} and isinstance(entry['active_scope_file'], str))
                active = Path(entry['active_scope_file'])
                require(active.is_absolute() and active.parent.parent == NATIVE_SCOPE_PARENT
                        and re.fullmatch('[A-Za-z0-9_-]+', active.parent.name)
                        and active.parent.name.startswith('enterprise-agent-test-successor-')
                        and active.name == 'provision-scope.v2.json')
                current, active_sha = native_json(active)
                require(current == scope and active_sha == entry['scope_sha256'])
            else:
                require(set(entry) == fields)
            receipt_path = Path(entry['receipt_file'])
            require(receipt_path.is_absolute() and receipt_path.is_relative_to(EVIDENCE_ROOT)
                    and '..' not in receipt_path.parts and receipt_path.suffix == '.json')
            receipt, sha = native_json(receipt_path, root_owned=False)
            require(sha == entry['receipt_sha256'])
            row_hash = tenant_row_hash(scope, receipt, entry['scope_file'])
            require(scope['environment'] == 'test'
                and scope['authority_id'] == approval['authority_id']
                and scope['purpose'] == 'WECHAT_PERSONAL_CENTER_CONFIG_V1'
                and scope['application_source'] == approval['source_commit']
                and scope['application_tree'] == approval['source_tree']
                and scope['allowed_objects'] == MINIMAL_OBJECTS and scope['production_deploy_authority'] is False
                and scope['budget'] == approval['budget'] and all(type(v) is int for v in scope['budget'].values()))
            tenant = scope['tenant_id']
            require(isinstance(tenant, str) and re.fullmatch('[A-Za-z0-9_-]{1,128}', tenant) and tenant not in excluded)
            require(str(UUID(scope['run_id'])) == scope['run_id'])
            require(receipt['contract'] == scope['contract'] and receipt['status'] == 'PROVISIONED'
                and receipt['scope_sha256'] == digest(scope) and receipt['run_id'] == scope['run_id']
                and receipt['application_source'] == scope['application_source']
                and receipt['application_tree'] == scope['application_tree']
                and receipt['tooling_source'] == scope['tooling_source'] and receipt['tooling_tree'] == scope['tooling_tree']
                and receipt['tenant_id'] == tenant and receipt['user_id'] == scope['user_id']
                and receipt['database'] == {k: TARGET[k] for k in ('database', 'db_role')}
                and set(receipt['created_records']) == set(MINIMAL_OBJECTS)
                and all(type(receipt[k]) is int and receipt[k] == 0 for k in
                    ('workspace_create', 'business_agent_enable', 'wechat_calls', 'provider_calls', 'image_calls')))
            record = receipt['created_records']['tenants']
            require(len(record) == 1 and record[0]['primary_key'] == tenant
                and isinstance(record[0]['row_sha256'], str) and re.fullmatch('[a-f0-9]{64}', record[0]['row_sha256']))
            row = conn.execute('SELECT * FROM tenants WHERE id=?', (tenant,)).fetchone()
            if row is not None: require(row_hash(dict(row)) == record[0]['row_sha256'])
            # Absent after exact cleanup is safe. No data deletion or enablement
            # is inferred, and tenant names / editable config are never signals.
            excluded.add(tenant)
        return frozenset(excluded)
    except Exception:
        raise RuntimeError('TEST_TENANT_SEEDING_POLICY_BLOCKED') from None
