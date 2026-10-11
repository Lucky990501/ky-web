"""One approved customer operation. Default read-only; no application bootstrap.

Only the five customer tables are written. The protected append-only receipt
contains no credential. A pinned private anonymous pipe receives the credential
once, before transaction commit; failed handoff rolls back all database writes.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import select
import stat
import subprocess
import sys
import time
from uuid import UUID, NAMESPACE_URL, uuid5

ROOT = Path(__file__).absolute().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth import hash_password
from app.store import POCStore
from scripts import rollback_preflight as gate

VERSION = 'CUSTOMER_TENANT_SECURE_PROVISIONING_V1'
APP_SOURCE = '562f201e362f0b10ae4fe3a8dae9a8c4d9ef91d3'
APP_TREE = 'e5205965e57fce6ce2b65dd747a110d00f7f1b1a'
PRODUCTION_APPROVAL_ROOT = Path('/etc/enterprise-agent-customer-provisioning-v1')
CONFIG_FIELDS = {'brand_name', 'slogan', 'brand_style', 'primary_color', 'secondary_color',
                 'required_rules', 'forbidden_claims'}
TABLES = ('tenants', 'enterprise_configs', 'credit_accounts', 'users', 'tenant_agent_instances')


class ProvisionBlocked(RuntimeError):
    pass


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Never echo an accidentally supplied password/DSN as an invalid argument.
        self.exit(2, '{"status":"BLOCKED","first_failure_point":"INVALID_ARGUMENTS"}\n')


def need(condition, code):
    if not condition:
        raise ProvisionBlocked(code)


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def request(tenant_id, tenant_name, admin_email, admin_display_name, operation_id, config):
    need(isinstance(tenant_id, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{2,63}', tenant_id), 'TENANT_ID_INVALID')
    need(str(UUID(operation_id)) == operation_id, 'OPERATION_ID_INVALID')
    need(all(isinstance(v, str) and 1 <= len(v) <= 180 and v == v.strip()
             and not any(ord(c) < 32 for c in v) for v in (tenant_name, admin_display_name)), 'DISPLAY_NAME_INVALID')
    email = admin_email.strip().lower()
    need(len(email) <= 254 and re.fullmatch(r'[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}', email), 'ADMIN_EMAIL_INVALID')
    need(isinstance(config, dict) and set(config) <= CONFIG_FIELDS, 'CONFIRMED_CONFIG_FIELDS_REQUIRED')
    need(all((isinstance(v, str) and len(v) <= 2000) or
             (isinstance(v, list) and len(v) <= 50 and all(isinstance(x, str) and len(x) <= 2000 for x in v))
             for v in config.values()), 'CONFIRMED_CONFIG_INVALID')
    return dict(tenant_id=tenant_id, tenant_name=tenant_name, admin_email=email,
                admin_display_name=admin_display_name, operation_id=operation_id,
                config_sha256=gate.digest(config))


def git_identity(root):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL, text=True).strip()
    need(not git('status', '--porcelain', '--untracked-files=no'), 'TOOLING_TRACKED_SOURCE_DIRTY')
    git('ls-files', '--error-unmatch', 'scripts/provision_customer_tenant_v1.py', 'app/auth.py', 'app/store.py', 'app/product_store.py')
    return {'source': git('rev-parse', 'HEAD'), 'tree': git('rev-parse', 'HEAD^{tree}')}


def protected_path(path, owner, *, directory=False, mode=None):
    path = Path(path).absolute()
    need(path.resolve() == path, 'PROTECTED_PATH_SYMLINK')
    info = path.stat()
    need(info.st_uid == owner and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)), 'PROTECTED_PATH_OWNER')
    need(stat.S_IMODE(info.st_mode) == (mode if mode is not None else (0o700 if directory else 0o400)), 'PROTECTED_PATH_MODE')
    return path


def load_context(path, wanted):
    need(sys.platform == 'linux' and os.geteuid() != 0, 'NON_ROOT_LINUX_REQUIRED')
    need(not any(p.exists() or p.is_symlink() for p in (ROOT/'.env', ROOT.parent/'.env')), 'UNAPPROVED_ENVIRONMENT_FILE')
    raw = gate.read_json(path)
    need(set(raw) == {'version', 'scope', 'environment', 'production_authority', 'operator_uid',
                     'expires_at', 'request', 'application', 'tooling', 'code_pins', 'database',
                     'schema_sha256', 'legacy_agent_ids', 'receipt_dir', 'credential_handoff'}, 'APPROVAL_SCHEMA')
    need(raw['version'] == VERSION and raw['request'] == wanted, 'EXACT_OPERATION_APPROVAL_REQUIRED')
    need(type(raw['operator_uid']) is int and raw['operator_uid'] == os.geteuid(), 'EXACT_OPERATOR_REQUIRED')
    need(type(raw['expires_at']) is int and time.time() < raw['expires_at'] <= time.time()+86400, 'APPROVAL_EXPIRED_OR_UNBOUNDED')
    if raw['scope'] == 'ISOLATED_CUSTOMER_PROVISIONING_TEST':
        need(raw['environment'] == 'test' and raw['production_authority'] is False, 'ISOLATED_NOT_PRODUCTION')
        protected_path(path, os.geteuid())
        app = raw['application']
        need(set(app) == {'root', 'source', 'tree'} and Path(app['root']) == ROOT
             and {k: app[k] for k in ('source', 'tree')} == git_identity(ROOT), 'ISOLATED_SOURCE_IDENTITY')
    else:
        need(raw['scope'] == VERSION and raw['environment'] == 'production' and raw['production_authority'] is True, 'PRODUCTION_OPERATION_AUTHORITY_REQUIRED')
        need(Path(path).absolute() == PRODUCTION_APPROVAL_ROOT/(wanted['operation_id']+'.json'), 'PRODUCTION_APPROVAL_LOCATION')
        protected_path(PRODUCTION_APPROVAL_ROOT, 0, directory=True, mode=0o755)
        protected_path(path, 0, mode=0o444)
        app = raw['application']
        need(set(app) == {'base', 'release_id', 'source', 'tree', 'archive_sha256', 'manifest_sha256'}
             and (app['source'], app['tree']) == (APP_SOURCE, APP_TREE), 'EXACT_PRODUCTION_APPLICATION_REQUIRED')
        app_root, _ = gate.release_identity(Path(app['base']), app['release_id'], APP_SOURCE, app)
        # release_identity returns the verified enterprise_agent_poc directory.
        need((Path(app['base'])/'release-current').resolve() == app_root, 'PRODUCTION_RELEASE_CURRENT_MISMATCH')
        # No Test authority or isolated socket context can grant Production writes.
        need(raw['database'].get('database') != 'enterprise_agent_test', 'PRODUCTION_DATABASE_SCOPE')
    need(os.environ.get('APP_ENV') == raw['environment'], 'EXACT_ENVIRONMENT_REQUIRED')
    need(raw['tooling'] == git_identity(ROOT), 'EXACT_TOOLING_SOURCE_TREE_REQUIRED')
    pins = raw['code_pins']
    names = {'scripts/provision_customer_tenant_v1.py', 'app/auth.py', 'app/store.py', 'app/product_store.py'}
    need(isinstance(pins, dict) and set(pins) == names and all(file_sha(ROOT/name) == pin for name, pin in pins.items()), 'CODE_PIN_MISMATCH')
    if raw['scope'] == VERSION:
        need(all(file_sha(app_root/name) == pins[name] for name in names if name.startswith('app/')), 'APPLICATION_HELPER_PARITY_REQUIRED')
    receipts = protected_path(raw['receipt_dir'], os.geteuid(), directory=True)
    need(not receipts.is_relative_to(ROOT.parent)
         and (raw['scope'] != VERSION or not receipts.is_relative_to(app_root.parent)), 'RECEIPTS_OUTSIDE_SOURCE_REQUIRED')
    need(isinstance(raw['legacy_agent_ids'], list) and raw['legacy_agent_ids']
         and sorted(set(raw['legacy_agent_ids'])) == raw['legacy_agent_ids'], 'EXACT_LEGACY_AGENT_SCOPE_REQUIRED')
    return raw


def database_identity(conn):
    row = conn.execute("SELECT current_database() AS database,current_user AS role,"
                       "host(inet_server_addr()) AS address,current_setting('port')::int AS port,"
                       "current_setting('server_version_num')::int AS version,"
                       "(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS database_oid,"
                       "(SELECT system_identifier::text FROM pg_control_system()) AS system_identifier").fetchone()
    return dict(row)


def schema_snapshot(conn):
    # Full structural pin, not only a ledger/column-existence assertion.
    queries = {
        'tables': "SELECT c.relname,c.relkind::text,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m') ORDER BY c.relname",
        'policies': "SELECT tablename,policyname,permissive,roles,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname",
        'columns': "SELECT c.relname AS table_name,a.attname AS name,format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull AS not_null,pg_get_expr(d.adbin,d.adrelid) AS default_expr FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum WHERE n.nspname='public' AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped ORDER BY 1,2",
        'constraints': "SELECT t.relname AS table_name,c.conname AS name,pg_get_constraintdef(c.oid,true) AS definition,c.convalidated AS validated FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' ORDER BY 1,2",
        'indexes': "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1,2",
        'triggers': "SELECT c.relname AS table_name,t.tgname AS name,pg_get_triggerdef(t.oid,true) AS definition,t.tgenabled AS enabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY 1,2",
        'functions': "SELECT p.proname,pg_get_function_identity_arguments(p.oid) AS arguments,pg_get_functiondef(p.oid) AS definition FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e') ORDER BY 1,2",
        'extensions': "SELECT extname,extversion FROM pg_extension ORDER BY extname",
        'ledger': 'SELECT version,name,checksum FROM schema_migrations ORDER BY version',
    }
    return {key: [dict(row) for row in conn.execute(query)] for key, query in queries.items()}


def check_database(conn, context, wanted):
    need(database_identity(conn) == context['database'], 'EXACT_DATABASE_IDENTITY_REQUIRED')
    if context['scope'] == 'ISOLATED_CUSTOMER_PROVISIONING_TEST':
        root = Path(os.environ.get('STAGE1_POSTGRES_ROOT', '/nonexistent')).resolve()
        need(root.parent == Path('/private/tmp') and root.name.startswith('ky-web-stage1-postgres.')
             and (root/'stage1-isolated.marker').read_text().strip() == 'ky-web-stage1-local-only', 'MARKED_ISOLATED_DATABASE_REQUIRED')
        need(conn.execute('SHOW data_directory').fetchone()['data_directory'] == str(root/'cluster')
             and conn.execute('SHOW listen_addresses').fetchone()['listen_addresses'] == '', 'ISOLATED_SOCKET_ONLY')
    snapshot = schema_snapshot(conn)
    need(gate.digest(snapshot) == context['schema_sha256'], 'EXACT_SCHEMA_PIN_REQUIRED')
    from scripts import migrate
    try:
        history = [dict(r) for r in conn.execute('SELECT version,name,checksum,applied_at FROM schema_migrations ORDER BY version')]
        gate.check_history(history, migrate.migration_items())
    except gate.RollbackBlocked as error:
        raise ProvisionBlocked('SCHEMA_MIGRATION_CONTRACT_REJECTED:'+str(error)) from None
    need([r['version'] for r in snapshot['ledger']] == [f'{i:03}' for i in range(1,16)], 'EXACT_SCHEMA001_015_REQUIRED')
    columns = {(r['table_name'], r['name']): r for r in snapshot['columns']}
    need(not columns[('tenants', 'poc_api_key')]['not_null'] and
         ('users', 'account_status') in columns and ('agent_templates', 'definition_source') in columns, 'SAFE_INITIAL_SCHEMA_REQUIRED')
    keys = {(r['table_name'], r['type'], tuple(r['columns'])) for r in conn.execute(
        "SELECT t.relname AS table_name,c.contype::text AS type,ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num,ord) JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num ORDER BY k.ord) AS columns FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND c.contype IN ('p','u','f')")}
    need({('tenants','p',('id',)), ('users','p',('id',)), ('users','u',('email',)),
          ('enterprise_configs','p',('tenant_id',)), ('credit_accounts','p',('tenant_id',)),
          ('tenant_agent_instances','p',('tenant_id','agent_id'))} <= keys, 'SAFE_UNIQUENESS_CONSTRAINTS_REQUIRED')
    need(all((table,'f',('tenant_id',)) in keys for table in TABLES if table != 'tenants')
         and ('tenant_agent_instances','f',('agent_id',)) in keys, 'SAFE_TENANT_ASSOCIATIONS_REQUIRED')
    legacy = [r['id'] for r in conn.execute("SELECT id FROM agent_templates WHERE definition_source='legacy' ORDER BY id")]
    need(legacy == context['legacy_agent_ids'], 'LEGACY_AGENT_SCOPE_CHANGED')
    from app.agent_catalog import CATALOG
    templates = {r['id'] for r in conn.execute('SELECT id FROM agent_templates')}
    need(set(CATALOG) <= templates, 'CATALOG_BOOTSTRAP_REQUIRED_BEFORE_PROVISION')
    need(not conn.execute('SELECT 1 FROM tenants WHERE id=?', (wanted['tenant_id'],)).fetchone(), 'TENANT_ALREADY_EXISTS')
    need(not conn.execute('SELECT 1 FROM users WHERE lower(email)=?', (wanted['admin_email'],)).fetchone(), 'EMAIL_ALREADY_EXISTS')
    return {'tenant_count': conn.execute('SELECT count(*) AS n FROM tenants').fetchone()['n'], 'legacy_agent_ids': legacy}


def handoff_ready(fd, ack_fd, approval):
    try:
        need(type(fd) is int and fd > 2 and type(ack_fd) is int and ack_fd > 2
             and fd != ack_fd and isinstance(approval, dict), 'CREDENTIAL_HANDOFF_BLOCKED')
        need(set(approval) == {'mechanism', 'receiver_pid', 'receiver_uid', 'receiver_executable_sha256', 'receiver_cmdline_sha256'}
             and approval['mechanism'] == 'PRIVATE_ANONYMOUS_PIPE_V1', 'CREDENTIAL_HANDOFF_BLOCKED')
        channels = {}
        for endpoint,access in ((fd,os.O_WRONLY),(ack_fd,os.O_RDONLY)):
            info = os.fstat(endpoint)
            need(stat.S_ISFIFO(info.st_mode)
                 and re.fullmatch(r'pipe:\[\d+\]', os.readlink('/proc/self/fd/'+str(endpoint)))
                 and fcntl.fcntl(endpoint, fcntl.F_GETFL) & os.O_ACCMODE == access, 'CREDENTIAL_HANDOFF_BLOCKED')
            for standard in (0,1,2):
                standard_info = os.fstat(standard)
                need((standard_info.st_dev,standard_info.st_ino) != (info.st_dev,info.st_ino), 'CREDENTIAL_HANDOFF_BLOCKED')
            channels[(info.st_dev,info.st_ino)] = os.O_RDONLY if access == os.O_WRONLY else os.O_WRONLY
        need(len(channels) == 2, 'CREDENTIAL_HANDOFF_BLOCKED')
        pid = approval['receiver_pid']
        need(type(pid) is int and pid > 0 and pid != os.getpid(), 'CREDENTIAL_HANDOFF_BLOCKED')
        peer = Path('/proc')/str(pid)
        need(peer.stat().st_uid == approval['receiver_uid'] == os.geteuid()
             and file_sha(peer/'exe') == approval['receiver_executable_sha256']
             and file_sha(peer/'cmdline') == approval['receiver_cmdline_sha256'], 'CREDENTIAL_HANDOFF_BLOCKED')
        matched = set()
        for entry in (peer/'fd').iterdir():
            try:
                other = entry.stat()
                identity = (other.st_dev,other.st_ino)
                if identity in channels:
                    flags = int(next(line.split()[1] for line in (peer/'fdinfo'/entry.name).read_text().splitlines() if line.startswith('flags:')), 8)
                    if flags & os.O_ACCMODE == channels[identity]:
                        matched.add(identity)
            except (OSError, StopIteration):
                continue
        need(matched == set(channels), 'CREDENTIAL_HANDOFF_BLOCKED')
    except (OSError, KeyError, TypeError, ValueError):
        raise ProvisionBlocked('CREDENTIAL_HANDOFF_BLOCKED') from None


def send_credential(fd, ack_fd, context, wanted, password):
    handoff_ready(fd, ack_fd, context['credential_handoff'])
    payload = json.dumps({'operation_id': wanted['operation_id'], 'tenant_id': wanted['tenant_id'],
                          'admin_email': wanted['admin_email'], 'temporary_password': password}, separators=(',', ':')).encode()+b'\n'
    need(len(payload) <= os.fpathconf(fd, 'PC_PIPE_BUF'), 'CREDENTIAL_HANDOFF_BLOCKED')
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    try:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        need(bool(select.select([], [fd], [], 2)[1]), 'CREDENTIAL_HANDOFF_BLOCKED')
        need(os.write(fd, payload) == len(payload), 'CREDENTIAL_HANDOFF_BLOCKED')
        need(bool(select.select([ack_fd], [], [], 5)[0]), 'CREDENTIAL_HANDOFF_BLOCKED')
        acknowledgement = json.loads(os.read(ack_fd,4096))
        need(acknowledgement == {'operation_id':wanted['operation_id'],
             'credential_sha256':hashlib.sha256(payload).hexdigest()}, 'CREDENTIAL_HANDOFF_BLOCKED')
    except (OSError,ValueError,TypeError):
        raise ProvisionBlocked('CREDENTIAL_HANDOFF_BLOCKED') from None
    finally:
        fcntl.fcntl(fd, fcntl.F_SETFL, flags)


def other_row_pins(conn, tenant):
    pins = {}
    tables = [r['tablename'] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
    for table in tables:
        need(re.fullmatch(r'[a-z_][a-z0-9_]*', table), 'UNEXPECTED_SCHEMA_TABLE')
        field = 'id' if table == 'tenants' else 'tenant_id'
        exclusion = ' WHERE '+field+'<>?' if table in TABLES else ''
        # Only the digest leaves the database, never other tenants' config or credentials.
        pins[table] = conn.execute("SELECT encode(sha256(convert_to(COALESCE(string_agg(to_jsonb(t)::text,E'\\n' ORDER BY to_jsonb(t)::text),''),'UTF8')),'hex') AS pin FROM " + table + ' t'+exclusion, (tenant,) if exclusion else ()).fetchone()['pin']
    return pins


def verify_created(conn, wanted, config, admin_id, legacy, password_hash):
    tenant = dict(conn.execute('SELECT id,name,poc_api_key FROM tenants WHERE id=?', (wanted['tenant_id'],)).fetchone())
    need(tenant == {'id': wanted['tenant_id'], 'name': wanted['tenant_name'], 'poc_api_key': None}, 'TENANT_VERIFICATION_FAILED')
    need(json.loads(conn.execute('SELECT payload FROM enterprise_configs WHERE tenant_id=?', (wanted['tenant_id'],)).fetchone()['payload']) == config, 'CONFIG_VERIFICATION_FAILED')
    user = dict(conn.execute('SELECT id,tenant_id,email,display_name,role,account_status,password_hash FROM users WHERE tenant_id=?', (wanted['tenant_id'],)).fetchone())
    need(user == dict(id=admin_id, tenant_id=wanted['tenant_id'], email=wanted['admin_email'], display_name=wanted['admin_display_name'], role='enterprise_admin', account_status='enabled', password_hash=password_hash), 'ADMIN_VERIFICATION_FAILED')
    need(conn.execute('SELECT count(*) AS n FROM users WHERE tenant_id=?', (wanted['tenant_id'],)).fetchone()['n'] == 1, 'EXACT_ADMIN_COUNT_REQUIRED')
    need(conn.execute('SELECT balance FROM credit_accounts WHERE tenant_id=?', (wanted['tenant_id'],)).fetchone()['balance'] == 0, 'ZERO_CREDIT_REQUIRED')
    instances = [dict(r) for r in conn.execute('SELECT agent_id,status,agent_template_version_id,instance_id,overrides_json FROM tenant_agent_instances WHERE tenant_id=? ORDER BY agent_id', (wanted['tenant_id'],))]
    need(instances == [dict(agent_id=agent, status='disabled', agent_template_version_id=None, instance_id=None, overrides_json=None) for agent in legacy], 'DISABLED_LEGACY_INSTANCES_REQUIRED')
    need(not conn.execute('SELECT 1 FROM platform_admins WHERE user_id=?', (admin_id,)).fetchone(), 'PLATFORM_ADMIN_FORBIDDEN')
    return {'tenant_id': wanted['tenant_id'], 'admin_user_id': admin_id, 'role': 'enterprise_admin',
            'account_status': 'enabled', 'credit_balance': 0, 'disabled_agent_ids': legacy, 'poc_api_key_created': False}


def provision(store, approval_path, wanted, config, *, execute=False, credential_fd=None, credential_ack_fd=None):
    context = load_context(approval_path, wanted)
    need(store.is_postgres, 'POSTGRESQL_REQUIRED')
    need(gate.digest(config) == wanted['config_sha256'] and context['request'] == wanted, 'EXACT_OPERATION_APPROVAL_REQUIRED')
    with store.connection() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        need(conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'on', 'READONLY_PREFLIGHT_REQUIRED')
        planned = check_database(conn, context, wanted)
    resource_plan = {'tenants': 1, 'enterprise_configs': 1, 'credit_accounts': 1, 'users': 1,
                     'tenant_agent_instances': len(planned['legacy_agent_ids'])}
    need(not (Path(context['receipt_dir'])/(wanted['operation_id']+'.jsonl')).exists(), 'OPERATION_ALREADY_ATTEMPTED')
    if not execute:
        handoff_status = 'CREDENTIAL_HANDOFF_BLOCKED'
        try:
            handoff_ready(credential_fd,credential_ack_fd,context['credential_handoff'])
            handoff_status = 'READY'
        except ProvisionBlocked:
            pass
        return dict(status='dry_run_pass', read_only=True, operation_id=wanted['operation_id'],
                    **planned, creates=resource_plan, role='enterprise_admin', credit_balance=0,
                    agents='disabled', poc_api_key=None, credential_handoff_required=True,
                    credential_handoff_status=handoff_status, execution_ready=handoff_status=='READY', database_writes=0)
    need(time.time() < context['expires_at'], 'APPROVAL_EXPIRED')
    handoff_ready(credential_fd, credential_ack_fd, context['credential_handoff'])
    directory = protected_path(context['receipt_dir'], os.geteuid(), directory=True)
    dirfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            audit = os.open(wanted['operation_id']+'.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
            os.fsync(dirfd)
        except FileExistsError:
            raise ProvisionBlocked('OPERATION_ALREADY_ATTEMPTED') from None
    finally:
        os.close(dirfd)
    def record(phase, **extra):
        payload = dict(version=VERSION, operation_id=wanted['operation_id'], request_sha256=gate.digest(wanted),
                       tooling=context['tooling'], phase=phase, **extra)
        data = (json.dumps(payload, sort_keys=True)+'\n').encode()
        need(os.write(audit, data) == len(data), 'AUDIT_WRITE_FAILED')
        os.fsync(audit)
    committed = False
    try:
        record('INTENT', creates=resource_plan)
        # Credentials never enter argv/env, stdout, receipt, or ordinary logs.
        password = secrets.token_urlsafe(32)
        password_hash = hash_password(password)
        admin_id = str(uuid5(NAMESPACE_URL, VERSION+':'+wanted['operation_id']+':'+wanted['tenant_id']))
        with store.connection() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
            conn.execute("SET LOCAL lock_timeout='3s'")
            conn.execute("SET LOCAL statement_timeout='10s'")
            conn.execute('SELECT pg_advisory_xact_lock(331806,1)')
            conn.execute('LOCK TABLE agent_templates IN SHARE MODE')
            conn.execute('LOCK TABLE tenants,users IN SHARE ROW EXCLUSIVE MODE')
            check_database(conn, context, wanted)
            before = other_row_pins(conn, wanted['tenant_id'])
            conn.execute('INSERT INTO tenants(id,name,poc_api_key) VALUES (?,?,NULL)', (wanted['tenant_id'], wanted['tenant_name']))
            conn.execute('INSERT INTO enterprise_configs(tenant_id,payload) VALUES (?,?)', (wanted['tenant_id'], json.dumps(config, ensure_ascii=False)))
            conn.execute('INSERT INTO credit_accounts(tenant_id,balance) VALUES (?,0)', (wanted['tenant_id'],))
            conn.execute("INSERT INTO users(id,tenant_id,email,password_hash,display_name,role,account_status) VALUES (?,?,?,?,?,'enterprise_admin','enabled')", (admin_id, wanted['tenant_id'], wanted['admin_email'], password_hash, wanted['admin_display_name']))
            for agent in planned['legacy_agent_ids']:
                conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status) VALUES (?,?,'disabled')", (wanted['tenant_id'], agent))
            result = verify_created(conn, wanted, config, admin_id, planned['legacy_agent_ids'], password_hash)
            need(other_row_pins(conn, wanted['tenant_id']) == before, 'OTHER_TENANT_MUTATION_REJECTED')
            need(time.time() < context['expires_at'], 'APPROVAL_EXPIRED')
            need(load_context(approval_path, wanted) == context, 'APPROVAL_CHANGED_OR_REVOKED')
            send_credential(credential_fd, credential_ack_fd, context, wanted, password)
        committed = True
        record('COMMITTED', result=result)
        return dict(status='provisioned', operation_id=wanted['operation_id'], **result,
                    audit_receipt=wanted['operation_id']+'.jsonl', credential_handoff='ACKNOWLEDGED_ONCE')
    except Exception:
        # Ambiguous commit/transport errors are never labelled rolled back or
        # retried automatically. A new read-only review must settle the facts.
        record('COMMITTED_RECEIPT_PENDING' if committed else 'NOT_CONFIRMED', automatic_retry=False)
        raise
    finally:
        os.close(audit)


def main(argv=None):
    parser = SafeArgumentParser(description=__doc__)
    for name in ('tenant-id', 'tenant-name', 'admin-email', 'admin-display-name', 'operation-id'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--approval', type=Path, required=True)
    parser.add_argument('--enterprise-config', type=Path)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--dry-run', action='store_true')
    modes.add_argument('--execute', action='store_true')
    parser.add_argument('--credential-fd', type=int)
    parser.add_argument('--credential-ack-fd', type=int)
    args = parser.parse_args(argv)
    try:
        config = gate.read_json(args.enterprise_config) if args.enterprise_config else {}
        wanted = request(args.tenant_id, args.tenant_name, args.admin_email, args.admin_display_name, args.operation_id, config)
        database_url = os.environ.get('ENTERPRISE_POC_DATABASE_URL', '')
        need(database_url.startswith(('postgresql://', 'postgres://')), 'POSTGRESQL_REQUIRED')
        result = provision(POCStore(database_url), args.approval, wanted, config, execute=args.execute,
                           credential_fd=args.credential_fd,credential_ack_fd=args.credential_ack_fd)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, ProvisionBlocked) else 'PROVISION_INPUT_DATABASE_OR_IO_ERROR'
        print(json.dumps({'status': 'BLOCKED', 'first_failure_point': code}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
