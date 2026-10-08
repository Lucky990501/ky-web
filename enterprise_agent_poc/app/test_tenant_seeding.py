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


def authorized_exclusions(conn, store):
    # Production/development retain the original behavior without reading Test
    # files. The flag requests stronger validation, never grants an exemption.
    if os.environ.get('APP_ENV', 'development').lower() != 'test': return frozenset()
    mode = os.environ.get(REQUIRED_ENV, 'false').lower()
    require(mode in ('false', 'true'))
    if mode == 'false': return frozenset()
    try:
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
            require(set(entry) == {'scope_file', 'scope_sha256', 'receipt_file', 'receipt_sha256'})
            require(isinstance(entry['scope_file'], str) and re.fullmatch(r'[A-Za-z0-9_-]+\.v[1-9][0-9]*\.json', entry['scope_file']))
            require(all(isinstance(entry[k], str) and re.fullmatch('[a-f0-9]{64}', entry[k])
                        for k in ('scope_sha256', 'receipt_sha256')))
            scope, sha = native_json(AUTHORITY_ROOT / entry['scope_file'])
            require(sha == entry['scope_sha256'])
            receipt_path = Path(entry['receipt_file'])
            require(receipt_path.is_absolute() and receipt_path.is_relative_to(EVIDENCE_ROOT)
                    and '..' not in receipt_path.parts and receipt_path.suffix == '.json')
            receipt, sha = native_json(receipt_path, root_owned=False)
            require(sha == entry['receipt_sha256'])
            require(scope['contract'] == 'TEST_ONLY_MINIMAL_PROVISION_V1' and scope['environment'] == 'test'
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
            if row is not None: require(digest(dict(row)) == record[0]['row_sha256'])
            # Absent after exact cleanup is safe. No data deletion or enablement
            # is inferred, and tenant names / editable config are never signals.
            excluded.add(tenant)
        return frozenset(excluded)
    except Exception:
        raise RuntimeError('TEST_TENANT_SEEDING_POLICY_BLOCKED') from None
