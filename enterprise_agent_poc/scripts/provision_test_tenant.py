"""Provision the one controlled tenant used by Phase A isolation gates.

No secret is emitted.  Supply the test administrator password through the
named environment variable only when performing an actual provision.
"""
from __future__ import annotations

import argparse
import json
import os
import hashlib
import re
import secrets
import stat
import subprocess
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent_catalog import CATALOG
from app.auth import hash_password
from app.domain import RuntimeProfile
from app.settings import settings
from app.store import POCStore
from scripts.receipt_row_canonicalization import VERSION as RECEIPT_VERSION, row_sha256, verify_catalog

TEST_TENANT_ID = "rag-isolation-test"
TEST_ADMIN_EMAIL = "admin@rag-isolation-test.invalid"

# A separate, native-approved operational mode. The legacy Phase A path below
# remains unchanged. No caller-selected Tenant, connection string or SQL file.
MINIMAL_CONTRACT = "TEST_ONLY_MINIMAL_PROVISION_V2"
MINIMAL_TENANT = "wechat-personal-center-common-cache-v1"
MINIMAL_USER = str(uuid.uuid5(uuid.NAMESPACE_URL, MINIMAL_TENANT + ':config-admin'))
MINIMAL_EMAIL = "config-admin-common-cache-v1@wechat-personal-center-test.invalid"
MINIMAL_PURPOSE = "WECHAT_PERSONAL_CENTER_CONFIG_V1"
APPLICATION_SOURCE = "d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7"
APPLICATION_TREE = "6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5"
PRIMARY_ROOT = Path('/opt/enterprise-agent-workbench-test')
NATIVE_SCOPE = Path('/etc/enterprise-agent-test-successor-wechat-d8a-common-cache-v1')
SCOPE_FILE = NATIVE_SCOPE / 'provision-scope.v2.json'
RECEIPT_FILE = PRIMARY_ROOT / 'release-evidence/wechat-d8a-common-cache-v1/provision-attempt-01/receipt.v2.json'
CREDENTIAL_FILE = PRIMARY_ROOT / 'shared/credentials/wechat-personal-center-user.d8a.common-cache.v1.env'
ALLOWED_OBJECTS = ['tenants', 'enterprise_configs', 'users']
MINIMAL_CONFIG = {'data_classification': MINIMAL_PURPOSE, 'tenant_label': MINIMAL_TENANT}


class MinimalProvisionBlocked(RuntimeError):
    """Only fixed public categories; never propagate credential-bearing errors."""


def require(value, code):
    if not value:
        raise MinimalProvisionBlocked(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def native_json(path):
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
                and not info.st_mode & 0o022, 'NATIVE_SCOPE_UNTRUSTED')
    require(path.is_file() and not path.stat().st_mode & 0o222, 'NATIVE_SCOPE_UNTRUSTED')
    return json.loads(path.read_bytes())


def tooling_identity():
    result = subprocess.run(['git', '-C', str(ROOT.parent), 'rev-parse', 'HEAD', 'HEAD^{tree}'],
                            capture_output=True, text=True, timeout=10)
    require(result.returncode == 0, 'TOOLING_IDENTITY_BLOCKED')
    source, tree = result.stdout.splitlines()
    clean = subprocess.run(['git', '-C', str(ROOT.parent), 'status', '--porcelain', '--untracked-files=no'],
                           capture_output=True, timeout=10)
    require(clean.returncode == 0 and not clean.stdout.strip(), 'DIRTY_TOOLING_SOURCE')
    return {'source': source, 'tree': tree}


def validate_scope(scope, environment, database_url, identity):
    fields = {'contract', 'authority_id', 'purpose', 'environment', 'application_source',
              'application_tree', 'tooling_source', 'tooling_tree', 'tenant_id', 'user_id',
              'user_email', 'allowed_objects', 'production_deploy_authority', 'budget', 'run_id', 'receipt_version', 'cleanup_authority'}
    require(isinstance(scope, dict) and set(scope) == fields, 'SCOPE_SCHEMA_BLOCKED')
    expected = {'contract': MINIMAL_CONTRACT, 'authority_id': 'WECHAT_PERSONAL_D8A_PRIMARY_SUCCESSOR_V1',
                'purpose': MINIMAL_PURPOSE, 'environment': 'test', 'application_source': APPLICATION_SOURCE,
                'application_tree': APPLICATION_TREE, 'tenant_id': MINIMAL_TENANT, 'user_id': MINIMAL_USER,
                'user_email': MINIMAL_EMAIL, 'allowed_objects': ALLOWED_OBJECTS, 'production_deploy_authority': False,
                'receipt_version': RECEIPT_VERSION, 'cleanup_authority': 'EXACT_RECEIPT_OWNED_OBJECTS_ONLY'}
    require(all(scope[key] == value for key, value in expected.items()), 'EXACT_SCOPE_BLOCKED')
    require(scope['production_deploy_authority'] is False and scope['budget'] == {'wechat': 0, 'provider': 0, 'image': 0}
            and all(type(value) is int for value in scope['budget'].values()), 'ZERO_BUDGET_BLOCKED')
    require(environment == 'test', 'PRODUCTION_MODE_BLOCKED')
    from urllib.parse import urlsplit
    db = urlsplit(database_url)
    require(db.scheme in ('postgresql', 'postgres') and (db.hostname, db.port, db.username, db.path)
            == ('127.0.0.1', 55432, 'enterprise_agent_test', '/enterprise_agent_test')
            and not db.query and not db.fragment, 'WRONG_DATABASE_BLOCKED')
    require(identity == {'source': scope['tooling_source'], 'tree': scope['tooling_tree']}
            and all(re.fullmatch('[a-f0-9]{40}', identity[key]) for key in ('source', 'tree')),
            'TOOLING_SOURCE_DRIFT')
    require(str(uuid.UUID(scope['run_id'])) == scope['run_id'], 'RUN_ID_BLOCKED')
    return scope


def authorize_minimal():
    require(os.name == 'posix', 'NATIVE_LINUX_REQUIRED')
    scope = native_json(SCOPE_FILE)
    validate_scope(scope, settings.environment, settings.database_url, tooling_identity())
    require(settings.data_dir == PRIMARY_ROOT / 'data/application', 'DATA_ROOT_BLOCKED')
    return scope


def database_identity(store):
    require(store.is_postgres, 'WRONG_DATABASE_BLOCKED')
    with store.connection() as conn:
        row = conn.execute('SELECT current_database() AS database, current_user AS db_role').fetchone()
        fields = conn.execute("SELECT table_name,column_name,ordinal_position,udt_name,datetime_precision,is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('tenants','users','enterprise_configs') ORDER BY table_name,ordinal_position").fetchall()
        verify_catalog(fields)
    require(dict(row) == {'database': 'enterprise_agent_test', 'db_role': 'enterprise_agent_test'},
            'LIVE_DATABASE_IDENTITY_BLOCKED')
    return dict(row)


def secret_paths():
    name = hashlib.sha256(MINIMAL_TENANT.encode()).hexdigest()
    directory = PRIMARY_ROOT / 'data/application/tenant-secrets/test'
    return [directory / (name + suffix) for suffix in ('.fernet', '.lock')]


def no_symlink(path):
    require(not any(item.is_symlink() for item in (path, *path.parents)), 'OWNED_PATH_BLOCKED')


def private_write(path, raw, mode):
    no_symlink(path)
    require(path.parent.is_dir(), 'OPERATOR_DIRECTORY_REQUIRED')
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), mode)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(mode)
    except Exception:
        path.unlink()  # O_EXCL proved this invocation created this exact path.
        raise


def created_rows(conn, lock=False):
    queries = {'tenants': ('SELECT * FROM tenants WHERE id=?', (MINIMAL_TENANT,)),
               'enterprise_configs': ('SELECT * FROM enterprise_configs WHERE tenant_id=?', (MINIMAL_TENANT,)),
               'users': ('SELECT * FROM users WHERE tenant_id=?', (MINIMAL_TENANT,))}
    return {table: [dict(row) for row in conn.execute(sql + (' FOR UPDATE' if lock else ''), params).fetchall()]
            for table, (sql, params) in queries.items()}


def tenant_tables(conn):
    values = conn.execute("SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='tenant_id'").fetchall()
    names = sorted({row['table_name'] for row in values})
    require(all(re.fullmatch('[a-z_]+', name) for name in names), 'CATALOG_IDENTITY_BLOCKED')
    return names


def no_extra_objects(conn):
    for table in tenant_tables(conn):
        if table not in ALLOWED_OBJECTS:
            require(conn.execute(f'SELECT COUNT(*) AS n FROM {table} WHERE tenant_id=?', (MINIMAL_TENANT,)).fetchone()['n'] == 0,
                    'UNRECEIPTED_TENANT_OBJECTS')
    require(not conn.execute('SELECT 1 FROM platform_admins WHERE user_id=?', (MINIMAL_USER,)).fetchone(),
            'UNEXPECTED_PLATFORM_GRANT')


def minimal_plan(scope):
    return {'contract': MINIMAL_CONTRACT, 'tooling_source': scope['tooling_source'],
            'application_source': APPLICATION_SOURCE, 'tenant_id': MINIMAL_TENANT, 'user_id': MINIMAL_USER,
            'membership': 'users.tenant_id', 'role': 'enterprise_admin',
            'create': ALLOWED_OBJECTS, 'update': [], 'delete': [], 'workspace_create': 0,
            'business_agent_enable': 0, 'credit_seed': 0, 'migration_ledger_mutations': 0}


def provision_minimal(store, scope, execute=False):
    db = database_identity(store)
    no_symlink(RECEIPT_FILE)
    no_symlink(CREDENTIAL_FILE)
    for path in secret_paths():
        no_symlink(path)
        require(not path.exists(), 'PREEXISTING_SECRET_ARTIFACT')
    require(not RECEIPT_FILE.exists() and not CREDENTIAL_FILE.exists(), 'RUN_ARTIFACT_ALREADY_EXISTS')
    with store.connection() as conn:
        require(not any(created_rows(conn).values()), 'PREEXISTING_TENANT_BLOCKED')
        require(not conn.execute('SELECT 1 FROM users WHERE id=? OR email=?', (MINIMAL_USER, MINIMAL_EMAIL)).fetchone(),
                'PREEXISTING_USER_BLOCKED')
        no_extra_objects(conn)
    if not execute:
        return dict(minimal_plan(scope), status='dry_run', database=db)
    password = secrets.token_urlsafe(32)
    credential_created = receipt_created = False
    try:
        private_write(CREDENTIAL_FILE, ('TEST_APPLICATION_ACCOUNT=' + MINIMAL_EMAIL + '\nTEST_APPLICATION_PASSWORD=' + password + '\n').encode(), 0o600)
        credential_created = True
        with store.connection() as conn:
            require(not any(created_rows(conn).values()), 'PREEXISTING_TENANT_BLOCKED')
            # Existing Provision SQL, narrowed to the permitted objects only.
            conn.execute('INSERT INTO tenants(id,name,poc_api_key) VALUES (?,?,?)', (MINIMAL_TENANT, '[SYNTHETIC PRIMARY TEST] WeChat Config V1', None))
            conn.execute('INSERT INTO enterprise_configs(tenant_id,payload) VALUES (?,?)', (MINIMAL_TENANT, json.dumps(MINIMAL_CONFIG, ensure_ascii=False)))
            conn.execute('INSERT INTO users(id,tenant_id,email,password_hash,display_name,role) VALUES (?,?,?,?,?,?)',
                         (MINIMAL_USER, MINIMAL_TENANT, MINIMAL_EMAIL, hash_password(password), '[SYNTHETIC TEST] Config Admin', 'enterprise_admin'))
            rows = created_rows(conn)
            no_extra_objects(conn)
            require(all(len(value) == 1 for value in rows.values()), 'CREATED_OBJECT_SET_BLOCKED')
            receipt = {'contract': MINIMAL_CONTRACT, 'receipt_version': RECEIPT_VERSION, 'status': 'PROVISIONED', 'scope_sha256': digest(scope),
                       'run_id': scope['run_id'], 'tooling_source': scope['tooling_source'], 'tooling_tree': scope['tooling_tree'],
                       'application_source': APPLICATION_SOURCE, 'application_tree': APPLICATION_TREE,
                       'tenant_id': MINIMAL_TENANT, 'user_id': MINIMAL_USER, 'database': db,
                       'created_records': {table: [{'primary_key': MINIMAL_USER if table == 'users' else MINIMAL_TENANT,
                                                   'row_sha256': row_sha256(table,value[0])}] for table, value in rows.items()},
                       'initially_absent_secret_paths': [str(path) for path in secret_paths()],
                       'credential_path': str(CREDENTIAL_FILE), 'workspace_create': 0, 'business_agent_enable': 0,
                       'wechat_calls': 0, 'provider_calls': 0, 'image_calls': 0}
            private_write(RECEIPT_FILE, canonical(receipt) + b'\n', 0o400)
            receipt_created = True
        return {'status': 'PROVISIONED', **minimal_plan(scope), 'receipt': str(RECEIPT_FILE),
                'receipt_sha256': hashlib.sha256(RECEIPT_FILE.read_bytes()).hexdigest(), 'database': db}
    except Exception:
        # A lost commit acknowledgement is not proof of rollback. Keep the
        # receipt/private credential for exact-scope recovery unless absence
        # is independently proved. Never delete someone else's DB rows here.
        try:
            with store.connection() as conn:
                absent = not any(created_rows(conn).values())
        except Exception:
            absent = False
        if not absent:
            raise MinimalProvisionBlocked('PROVISION_OUTCOME_UNCERTAIN_RECEIPT_RETAINED') from None
        if receipt_created:
            RECEIPT_FILE.unlink()
        if credential_created:
            CREDENTIAL_FILE.unlink()
        raise MinimalProvisionBlocked('PROVISION_TRANSACTION_BLOCKED') from None
    finally:
        password = None


def approved_receipt(scope):
    approval = native_json(NATIVE_SCOPE / 'provision-receipt.approval.v2.json')
    require(set(approval) == {'contract', 'scope_sha256', 'receipt_sha256'}
            and approval['contract'] == MINIMAL_CONTRACT and approval['scope_sha256'] == digest(scope),
            'RECEIPT_APPROVAL_BLOCKED')
    no_symlink(RECEIPT_FILE)
    raw = RECEIPT_FILE.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == approval['receipt_sha256'], 'RECEIPT_PIN_BLOCKED')
    receipt = json.loads(raw)
    require(receipt.get('receipt_version') == RECEIPT_VERSION, 'RECEIPT_VERSION_REJECTED')
    require(receipt['contract'] == MINIMAL_CONTRACT and receipt['status'] == 'PROVISIONED'
            and receipt['scope_sha256'] == digest(scope) and receipt['run_id'] == scope['run_id']
            and receipt['tenant_id'] == MINIMAL_TENANT and receipt['user_id'] == MINIMAL_USER
            and set(receipt['created_records']) == set(ALLOWED_OBJECTS)
            and receipt['tooling_source'] == scope['tooling_source'] and receipt['tooling_tree'] == scope['tooling_tree']
            and receipt['application_source'] == APPLICATION_SOURCE and receipt['application_tree'] == APPLICATION_TREE
            and receipt['initially_absent_secret_paths'] == [str(path) for path in secret_paths()]
            and receipt['credential_path'] == str(CREDENTIAL_FILE), 'EXACT_RECEIPT_BLOCKED')
    require(all(len(value) == 1 and value[0]['primary_key'] == (MINIMAL_USER if table == 'users' else MINIMAL_TENANT)
                for table, value in receipt['created_records'].items()), 'EXACT_RECEIPT_BLOCKED')
    return receipt


def revoked_secret_artifacts():
    paths = secret_paths()
    for path in paths:
        no_symlink(path)
        if path.exists():
            info = path.stat()
            require(path.is_file() and stat.S_IMODE(info.st_mode) == 0o600
                    and info.st_uid in (0, os.getuid()), 'SECRET_ARTIFACT_OWNER_BLOCKED')
    if paths[0].exists():
        from app.tenant_secret_backend import backend_from_settings
        backend = backend_from_settings(settings)
        # Only the receipt-owned Synthetic Tenant; no other Tenant is resolved.
        with backend._locked(MINIMAL_TENANT) as (path, codec):
            record = backend._read(path, codec, MINIMAL_TENANT)
        require(record and record['state'] == 'revoked' and record['secret'] is None
                and record['scope']['environment'] == 'test', 'SYNTHETIC_SECRET_NOT_REVOKED')
    return [path for path in paths if path.exists()]


def cleanup_minimal(store, scope, execute=False):
    db = database_identity(store)
    receipt = approved_receipt(scope)
    files = revoked_secret_artifacts()
    no_symlink(CREDENTIAL_FILE)
    if CREDENTIAL_FILE.exists():
        info = CREDENTIAL_FILE.stat()
        require(CREDENTIAL_FILE.is_file() and stat.S_IMODE(info.st_mode) == 0o600
                and info.st_uid in (0, os.getuid()), 'CREDENTIAL_FILE_OWNER_BLOCKED')
    with store.connection() as conn:
        rows = created_rows(conn, lock=True)
        no_extra_objects(conn)
        absent = not any(rows.values())
        if not absent:
            require(all(len(value) == 1 for value in rows.values()), 'PARTIAL_RECEIPT_OBJECT_SET')
            require(all(row_sha256(table,value[0]) == receipt['created_records'][table][0]['row_sha256']
                        for table, value in rows.items()), 'OWNERSHIP_OR_CONFIG_DRIFT')
        if not execute:
            return {'status': 'cleanup_dry_run', 'tenant_id': MINIMAL_TENANT, 'user_id': MINIMAL_USER,
                    'delete': [] if absent else ALLOWED_OBJECTS, 'owned_secret_files': len(files), 'database': db}
        if not absent:
            require(conn.execute('DELETE FROM users WHERE id=? AND tenant_id=?', (MINIMAL_USER, MINIMAL_TENANT)).rowcount == 1, 'EXACT_USER_DELETE_BLOCKED')
            require(conn.execute('DELETE FROM enterprise_configs WHERE tenant_id=?', (MINIMAL_TENANT,)).rowcount == 1, 'EXACT_CONFIG_DELETE_BLOCKED')
            require(conn.execute('DELETE FROM tenants WHERE id=?', (MINIMAL_TENANT,)).rowcount == 1, 'EXACT_TENANT_DELETE_BLOCKED')
    for path in files:
        path.unlink()
    if CREDENTIAL_FILE.exists():
        CREDENTIAL_FILE.unlink()
    with store.connection() as conn:
        require(not any(created_rows(conn).values()), 'CLEANUP_RESIDUE')
        no_extra_objects(conn)
        admins = conn.execute('SELECT COUNT(*) AS n FROM platform_admins').fetchone()['n']
    require(not any(path.exists() for path in secret_paths()) and not CREDENTIAL_FILE.exists(), 'CLEANUP_FILE_RESIDUE')
    require(admins == 0, 'ACTIVE_TEST_PLATFORM_ADMIN_NOT_ZERO')
    return {'status': 'CLEAN', 'tenant_id': MINIMAL_TENANT, 'user_id': MINIMAL_USER,
            'remaining_owned_objects': 0, 'ACTIVE_TEST_PLATFORM_ADMIN': admins, 'database': db,
            'receipt_retained': str(RECEIPT_FILE), 'already_absent': absent}


def profile_id() -> str:
    return RuntimeProfile.build(
        tenant_id=TEST_TENANT_ID,
        agent_id="copywriting-agent",
        model_provider_id=settings.model_provider_id,
        model_id=settings.model_id,
        reasoning_effort=settings.reasoning_effort,
        skill_manifest=CATALOG["copywriting-agent"].skill_manifest,
    ).id


def planned_resources() -> dict:
    return {
        "tenant_id": TEST_TENANT_ID,
        "user_id": f"phase-a-{uuid.uuid5(uuid.NAMESPACE_URL, TEST_TENANT_ID + ':admin')}",
        "runtime_profile_id": profile_id(),
        "created_resources": ["tenant", "enterprise_config", "credit_account", "test_admin", "tenant_agent_instances", "runtime_workspace"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="仅显示将创建的资源（默认安全模式）。")
    parser.add_argument("--execute", action="store_true", help="确认执行写入。")
    parser.add_argument("--password-env", default="PHASE_A_TEST_PASSWORD")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--test-only-minimal-provision', action='store_true')
    mode.add_argument('--exact-test-tenant-cleanup', action='store_true')
    args = parser.parse_args()
    if args.test_only_minimal_provision or args.exact_test_tenant_cleanup:
        try:
            scope = authorize_minimal()
            store = POCStore(settings.database_url)
            operation = cleanup_minimal if args.exact_test_tenant_cleanup else provision_minimal
            result = operation(store, scope, execute=args.execute and not args.dry_run)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except Exception as error:
            category = str(error) if isinstance(error, MinimalProvisionBlocked) else 'MINIMAL_PROVISION_BLOCKED'
            print(json.dumps({'status': 'BLOCKED', 'category': category}))
            return 2
    if args.dry_run or not args.execute:
        print(json.dumps({"status": "dry_run", **planned_resources()}, ensure_ascii=False))
        return 0
    password = os.environ.get(args.password_env)
    if not password:
        raise RuntimeError(f"缺少临时测试密码环境变量 {args.password_env}；脚本不会读取或输出密码文件。")
    store = POCStore(settings.database_url)
    if not store.is_postgres:
        raise RuntimeError("生产受控租户工具仅允许 PostgreSQL。")
    resource = planned_resources()
    with store.connection() as conn:
        existing = conn.execute("SELECT 1 FROM tenants WHERE id=?", (TEST_TENANT_ID,)).fetchone()
        if existing:
            raise RuntimeError("受控测试租户已存在；请先使用 cleanup_test_tenant.py --dry-run 审核。")
        user_id = resource["user_id"]
        conn.execute("INSERT INTO tenants(id,name,poc_api_key) VALUES (?,?,?)", (TEST_TENANT_ID, "Phase A 隔离测试租户", None))
        conn.execute("INSERT INTO enterprise_configs(tenant_id,payload) VALUES (?,?)", (TEST_TENANT_ID, json.dumps({"data_classification": "phase_a_isolation_test", "tenant_label": "rag-isolation-test"}, ensure_ascii=False)))
        conn.execute("INSERT INTO credit_accounts(tenant_id,balance) VALUES (?,?)", (TEST_TENANT_ID, 1000))
        conn.execute("INSERT INTO users(id,tenant_id,email,password_hash,display_name,role) VALUES (?,?,?,?,?,?)", (user_id, TEST_TENANT_ID, TEST_ADMIN_EMAIL, hash_password(password), "Phase A 测试管理员", "enterprise_admin"))
        templates = {row["id"] for row in conn.execute("SELECT id FROM agent_templates").fetchall()}
        missing = set(CATALOG) - templates
        if missing:
            raise RuntimeError(f"缺少 Agent 模板 {sorted(missing)}；请先执行迁移/应用初始化。")
        for agent_id in CATALOG:
            conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status) VALUES (?,?,'enabled')", (TEST_TENANT_ID, agent_id))
    runtime_root = settings.data_dir / "runtime" / TEST_TENANT_ID / "copywriting-agent" / resource["runtime_profile_id"]
    runtime_root.mkdir(parents=True, exist_ok=False)
    (runtime_root / ".phase-a-control.json").write_text(json.dumps({"tenant_id": TEST_TENANT_ID, "profile_id": resource["runtime_profile_id"]}), encoding="utf-8")
    print(json.dumps({"status": "provisioned", **resource}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
