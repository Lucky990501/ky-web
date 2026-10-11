"""Actual PG/transactions/auth/API isolation; no live account or provider calls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import psycopg
from psycopg import sql
import pytest

from app import main
from app.auth import SessionIssuer, verify_password
from app.product_store import ProductStore
from app.settings import settings
from app.skill_registry import SkillRegistry
from app.store import POCStore
from scripts import migrate
from scripts import provision_customer_tenant_v1 as tool

pytestmark = pytest.mark.skipif(not os.environ.get('STAGE1_POSTGRES_ROOT'), reason='independent marked PG16.6 root required')


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    def deny(*a, **kw):
        raise AssertionError('REAL_PROVIDER_OR_EXTERNAL_HTTP_FORBIDDEN')
    async def deny_async(*a, **kw):
        deny()
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', deny)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', deny_async)


@pytest.fixture
def pg(tmp_path, approved_bundle):
    root = Path(os.environ['STAGE1_POSTGRES_ROOT']).resolve()
    assert root.parent == Path('/private/tmp') and root.name.startswith('ky-web-stage1-postgres.')
    assert root.stat().st_uid == os.getuid() and (root/'socket').stat().st_mode & 0o077 == 0
    assert (root/'stage1-isolated.marker').read_text().strip() == 'ky-web-stage1-local-only'
    args = dict(host=str(root/'socket'), port=54329, user='stage1_fixture')
    name = 'stage1_customer_'+uuid4().hex
    with psycopg.connect(dbname='postgres', **args, autocommit=True) as conn:
        assert conn.execute('SHOW server_version_num').fetchone()[0] == '160006'
        assert conn.execute('SHOW listen_addresses').fetchone()[0] == ''
        assert Path(conn.execute('SHOW data_directory').fetchone()[0]) == root/'cluster'
        conn.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
    store = POCStore(f'postgresql:///{name}?'+urlencode(args))
    try:
        assert migrate.up(store) == 0 and len(migrate.migration_files()) == 15
        # Fixture setup only. The provisioning tool must never invoke either.
        store.seed_demo_data()
        product = ProductStore(store)
        product.initialize()
        product.create_user('tenant-a', 'old-admin@isolated.invalid', main.hash_password('IsolatedOriginal!2026'), 'Old Admin', 'enterprise_admin')
        registry = SkillRegistry(store, tmp_path/'registry', approved_bundle)
        registry.initialize()
        receipts = tmp_path/'receipts'
        receipts.mkdir(mode=0o700)
        yield SimpleNamespace(store=store, product=product, registry=registry, receipts=receipts, tmp=tmp_path)
    finally:
        with psycopg.connect(dbname='postgres', **args, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


class Receiver:
    """Private two-pipe test receiver. No secret on stdout/files/argv/env."""
    def __init__(self, ack_mode='valid'):
        inbound, self.fd = os.pipe()
        self.ack_fd, ack_write = os.pipe()
        self.reply, reply_write = os.pipe()
        code = "import os,sys,json,hashlib; p=os.read(int(sys.argv[1]),4096); os.write(int(sys.argv[2]),p); a={'operation_id':json.loads(p)['operation_id'],'credential_sha256':hashlib.sha256(p).hexdigest()}; os.write(int(sys.argv[3]),json.dumps(a).encode())"
        if ack_mode == 'wrong_operation':
            code = code.replace("json.loads(p)['operation_id']", "'wrong-operation'")
        elif ack_mode == 'wrong_digest':
            code = code.replace('hashlib.sha256(p).hexdigest()', "'wrong-digest'")
        elif ack_mode == 'missing':
            code = code.replace('os.write(int(sys.argv[3]),json.dumps(a).encode())', 'pass')
        self.process = subprocess.Popen([sys.executable, '-B', '-c', code, str(inbound), str(reply_write),str(ack_write)],
            pass_fds=(inbound, reply_write,ack_write), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(inbound)
        os.close(reply_write)
        os.close(ack_write)
        peer = Path('/proc')/str(self.process.pid)
        # Wait for exec so command/executable pins describe the actual receiver.
        for _ in range(100):
            if code.encode() in (peer/'cmdline').read_bytes():
                break
            time.sleep(.01)
        self.approval = dict(mechanism='PRIVATE_ANONYMOUS_PIPE_V1', receiver_pid=self.process.pid,
            receiver_uid=os.getuid(), receiver_executable_sha256=tool.file_sha(peer/'exe'),
            receiver_cmdline_sha256=tool.file_sha(peer/'cmdline'))

    def consume(self):
        assert select.select([self.reply], [], [], 5)[0], 'private handoff missing'
        packet = json.loads(os.read(self.reply,4096))
        self.process.wait(timeout=5)
        return packet

    def close(self):
        os.close(self.fd)
        os.close(self.ack_fd)
        os.close(self.reply)
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)


@contextmanager
def receiver(ack_mode='valid'):
    value = Receiver(ack_mode)
    try:
        yield value
    finally:
        value.close()


def approval(pg, handoff=None, *, tenant=None, email=None, operation=None):
    tenant = tenant or 'customer-'+uuid4().hex[:12]
    config = {'brand_name': 'Synthetic Customer', 'slogan': 'Confirmed synthetic statement'}
    wanted = tool.request(tenant, 'Synthetic Customer', email or uuid4().hex+'@isolated.invalid', 'Synthetic Admin', operation or str(uuid4()), config)
    with pg.store.connection() as conn:
        value = dict(version=tool.VERSION, scope='ISOLATED_CUSTOMER_PROVISIONING_TEST', environment='test',
            production_authority=False, operator_uid=os.getuid(), expires_at=int(time.time())+3600,
            request=wanted, application={'root': str(tool.ROOT), **tool.git_identity(tool.ROOT)},
            tooling=tool.git_identity(tool.ROOT), code_pins={p: tool.file_sha(tool.ROOT/p) for p in
              ('scripts/provision_customer_tenant_v1.py','app/auth.py','app/store.py','app/product_store.py')},
            database=tool.database_identity(conn), schema_sha256=tool.gate.digest(tool.schema_snapshot(conn)),
            legacy_agent_ids=[r['id'] for r in conn.execute("SELECT id FROM agent_templates WHERE definition_source='legacy' ORDER BY id")],
            receipt_dir=str(pg.receipts), credential_handoff=handoff)
    path = pg.tmp/(wanted['operation_id']+'-'+uuid4().hex+'.approval.json')
    path.write_text(json.dumps(value))
    path.chmod(0o400)
    return SimpleNamespace(path=path, value=value, wanted=wanted, config=config)


def rewrite(a):
    a.path.chmod(0o600)
    a.path.write_text(json.dumps(a.value))
    a.path.chmod(0o400)


def counts(pg, tenant):
    with pg.store.connection() as conn:
        return {table: conn.execute('SELECT count(*) AS n FROM '+table+' WHERE '+('id' if table=='tenants' else 'tenant_id')+'=?', (tenant,)).fetchone()['n'] for table in tool.TABLES}


def test_default_dry_run_real_readonly_and_no_implicit_initialization(pg, monkeypatch, capsys):
    a = approval(pg)
    with pg.store.connection() as conn:
        before = tool.other_row_pins(conn, a.wanted['tenant_id'])
    monkeypatch.setattr(POCStore, 'initialize', lambda *a: pytest.fail('implicit store initialize'))
    monkeypatch.setattr(POCStore, 'seed_demo_data', lambda *a: pytest.fail('implicit seed'))
    monkeypatch.setattr(ProductStore, 'initialize', lambda *a: pytest.fail('implicit catalog initialize'))
    monkeypatch.setattr(tool.secrets, 'token_urlsafe', lambda *a: pytest.fail('dry-run generated credential'))
    monkeypatch.setenv('ENTERPRISE_POC_DATABASE_URL', pg.store.database_url)
    result = tool.provision(pg.store, a.path, a.wanted, a.config)
    assert result['status'] == 'dry_run_pass' and result['read_only'] and result['database_writes'] == 0
    assert not result['execution_ready'] and result['credential_handoff_status'] == 'CREDENTIAL_HANDOFF_BLOCKED'
    assert result['tenant_count'] == 2 and result['credit_balance'] == 0
    assert result['creates'] == dict(tenants=1,enterprise_configs=1,credit_accounts=1,users=1,tenant_agent_instances=3)
    # Default CLI path as well; the explicit config is confirmed, never inherited.
    config_path = pg.tmp/'config.json'
    config_path.write_text(json.dumps(a.config))
    args = ['--tenant-id', a.wanted['tenant_id'], '--tenant-name', a.wanted['tenant_name'],
        '--admin-email', a.wanted['admin_email'], '--admin-display-name', a.wanted['admin_display_name'],
        '--operation-id', a.wanted['operation_id'], '--approval', str(a.path), '--enterprise-config', str(config_path)]
    assert tool.main(args) == 0
    output = capsys.readouterr().out
    assert 'temporary_password' not in output and not list(pg.receipts.iterdir())
    with pg.store.connection() as conn:
        assert tool.other_row_pins(conn,a.wanted['tenant_id']) == before


def test_actual_atomic_provision_hash_zero_disabled_audit_and_no_other_tenant_mutation(pg, capsys):
    with receiver() as target:
        a = approval(pg, target.approval)
        with pg.store.connection() as conn:
            before = tool.other_row_pins(conn, a.wanted['tenant_id'])
        result = tool.provision(pg.store, a.path, a.wanted, a.config, execute=True, credential_fd=target.fd,credential_ack_fd=target.ack_fd)
        packet = target.consume()
    assert result['status'] == 'provisioned' and result['credential_handoff'] == 'ACKNOWLEDGED_ONCE'
    assert counts(pg,a.wanted['tenant_id']) == dict(tenants=1,enterprise_configs=1,credit_accounts=1,users=1,tenant_agent_instances=3)
    saved = pg.product.user_by_email(a.wanted['admin_email'])
    assert verify_password(packet['temporary_password'], saved['password_hash'])
    assert len(packet['temporary_password']) >= 40
    assert (saved['role'],saved['account_status']) == ('enterprise_admin','enabled')
    assert pg.store.enterprise_config(a.wanted['tenant_id']) == a.config
    with pg.store.connection() as conn:
        assert tool.other_row_pins(conn,a.wanted['tenant_id']) == before
        assert conn.execute('SELECT balance FROM credit_accounts WHERE tenant_id=?',(a.wanted['tenant_id'],)).fetchone()['balance'] == 0
    receipt = pg.receipts/result['audit_receipt']
    assert receipt.stat().st_mode & 0o077 == 0
    audit_text = receipt.read_text()
    assert [json.loads(line)['phase'] for line in audit_text.splitlines()] == ['INTENT','COMMITTED']
    assert packet['temporary_password'] not in audit_text+capsys.readouterr().out+json.dumps(result)
    pg.product.initialize()
    assert all(not item['enabled'] for item in pg.product.agents(a.wanted['tenant_id']))
    assert counts(pg,a.wanted['tenant_id'])['tenant_agent_instances'] == 3
    with pytest.raises(tool.ProvisionBlocked, match='TENANT_ALREADY_EXISTS'):
        tool.provision(pg.store,a.path,a.wanted,a.config)


@pytest.mark.parametrize('field', ['schema_sha256','tooling','code_pins','database','environment','expires_at'])
def test_identity_drift_and_expired_approval_fail_closed(pg, field):
    a = approval(pg)
    if field == 'schema_sha256': a.value[field] = '0'*64
    elif field == 'tooling': a.value[field]['source'] = '0'*40
    elif field == 'code_pins': a.value[field]['app/auth.py'] = '0'*64
    elif field == 'database': a.value[field]['database'] = 'wrong-database'
    elif field == 'environment': a.value[field] = 'production'
    else: a.value[field] = int(time.time())-1
    rewrite(a)
    with pytest.raises(tool.ProvisionBlocked):
        tool.provision(pg.store,a.path,a.wanted,a.config)
    assert all(n == 0 for n in counts(pg,a.wanted['tenant_id']).values())


def test_approval_file_mode_and_test_context_cannot_authorize_production(pg):
    a = approval(pg)
    a.path.chmod(0o600)
    with pytest.raises(tool.ProvisionBlocked, match='PROTECTED_PATH_MODE'):
        tool.provision(pg.store,a.path,a.wanted,a.config)
    a.path.chmod(0o400)
    a.value.update(scope=tool.VERSION,environment='production',production_authority=True)
    rewrite(a)
    with pytest.raises(tool.ProvisionBlocked, match='PRODUCTION_APPROVAL_LOCATION'):
        tool.provision(pg.store,a.path,a.wanted,a.config,execute=True)


def test_global_case_insensitive_email_collision_rejected(pg):
    a = approval(pg,email='OLD-ADMIN@isolated.invalid')
    with pytest.raises(tool.ProvisionBlocked,match='EMAIL_ALREADY_EXISTS'):
        tool.provision(pg.store,a.path,a.wanted,a.config)
    assert all(n == 0 for n in counts(pg,a.wanted['tenant_id']).values())


def test_unsafe_or_missing_credential_handoff_is_rejected_before_writes(pg):
    a = approval(pg)
    with pytest.raises(tool.ProvisionBlocked, match='CREDENTIAL_HANDOFF_BLOCKED'):
        tool.provision(pg.store,a.path,a.wanted,a.config,execute=True)
    assert not list(pg.receipts.iterdir()) and all(n == 0 for n in counts(pg,a.wanted['tenant_id']).values())
    with receiver() as target:
        a = approval(pg,target.approval)
        a.value['credential_handoff']['receiver_cmdline_sha256'] = '0'*64
        rewrite(a)
        with pytest.raises(tool.ProvisionBlocked,match='CREDENTIAL_HANDOFF_BLOCKED'):
            tool.provision(pg.store,a.path,a.wanted,a.config,execute=True,credential_fd=target.fd,credential_ack_fd=target.ack_fd)
        regular = pg.tmp/'unapproved-secret-file'
        fd = os.open(regular,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with pytest.raises(tool.ProvisionBlocked,match='CREDENTIAL_HANDOFF_BLOCKED'):
                tool.provision(pg.store,a.path,a.wanted,a.config,execute=True,credential_fd=fd,credential_ack_fd=target.ack_fd)
        finally:
            os.close(fd)
    assert all(n == 0 for n in counts(pg,a.wanted['tenant_id']).values())


@pytest.mark.parametrize('table', ['enterprise_configs','credit_accounts','users','tenant_agent_instances'])
def test_actual_pg_failure_rolls_back_entire_transaction_and_fresh_approved_retry(pg, table):
    with pg.store.connection() as conn:
        conn.execute("CREATE FUNCTION reject_customer_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'isolated injected failure'; END $$")
        conn.execute('CREATE TRIGGER fixture_failure BEFORE INSERT ON '+table+' FOR EACH ROW EXECUTE FUNCTION reject_customer_fixture()')
    with receiver() as target:
        a = approval(pg,target.approval)
        with pytest.raises(psycopg.errors.RaiseException):
            tool.provision(pg.store,a.path,a.wanted,a.config,execute=True,credential_fd=target.fd,credential_ack_fd=target.ack_fd)
        assert all(n == 0 for n in counts(pg,a.wanted['tenant_id']).values())
        with pytest.raises(tool.ProvisionBlocked, match='OPERATION_ALREADY_ATTEMPTED'):
            tool.provision(pg.store,a.path,a.wanted,a.config)
    with pg.store.connection() as conn:
        conn.execute('DROP TRIGGER fixture_failure ON '+table)
        conn.execute('DROP FUNCTION reject_customer_fixture()')
    with receiver() as target:
        fresh = approval(pg,target.approval,tenant=a.wanted['tenant_id'],email=a.wanted['admin_email'])
        assert tool.provision(pg.store,fresh.path,fresh.wanted,fresh.config,execute=True,credential_fd=target.fd,credential_ack_fd=target.ack_fd)['status'] == 'provisioned'
        target.consume()
    assert counts(pg,fresh.wanted['tenant_id'])['tenants'] == 1


def test_trigger_side_effects_outside_write_scope_are_rolled_back(pg):
    with pg.store.connection() as conn:
        conn.execute("CREATE FUNCTION unwanted_side_effect() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN INSERT INTO knowledge_documents(tenant_id,title,body) VALUES (NEW.id,'forbidden','not approved'); RETURN NEW; END $$")
        conn.execute('CREATE TRIGGER fixture_side_effect AFTER INSERT ON tenants FOR EACH ROW EXECUTE FUNCTION unwanted_side_effect()')
    with receiver() as target:
        a = approval(pg,target.approval)
        with pytest.raises(tool.ProvisionBlocked,match='OTHER_TENANT_MUTATION_REJECTED'):
            tool.provision(pg.store,a.path,a.wanted,a.config,execute=True,credential_fd=target.fd,credential_ack_fd=target.ack_fd)
    assert all(n == 0 for n in counts(pg,a.wanted['tenant_id']).values())
    with pg.store.connection() as conn:
        assert not conn.execute("SELECT 1 FROM knowledge_documents WHERE title='forbidden'").fetchone()


def test_legacy_trigger_cannot_silently_enable_customer_agent(pg):
    with pg.store.connection() as conn:
        conn.execute("CREATE FUNCTION unsafe_instance_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.status='enabled'; RETURN NEW; END $$")
        conn.execute('CREATE TRIGGER unsafe_instance BEFORE INSERT ON tenant_agent_instances FOR EACH ROW EXECUTE FUNCTION unsafe_instance_fixture()')
    with receiver() as target:
        a = approval(pg,target.approval)
        with pytest.raises(tool.ProvisionBlocked,match='DISABLED_LEGACY_INSTANCES_REQUIRED'):
            tool.provision(pg.store,a.path,a.wanted,a.config,execute=True,credential_fd=target.fd,credential_ack_fd=target.ack_fd)
    assert all(n == 0 for n in counts(pg,a.wanted['tenant_id']).values())


def test_all_existing_legacy_templates_get_disabled_instances(pg):
    with pg.store.connection() as conn:
        conn.execute("INSERT INTO agent_templates(id,name,slug,description,icon,status,default_runtime_profile,credit_cost,skill_manifest,allows_image_generation) VALUES ('extra-legacy','Extra','extra-legacy','Fixture','x','enabled','default',1,'{}',false)")
    with receiver() as target:
        a = approval(pg,target.approval)
        result = tool.provision(pg.store,a.path,a.wanted,a.config,execute=True,credential_fd=target.fd,credential_ack_fd=target.ack_fd)
        target.consume()
    assert 'extra-legacy' in result['disabled_agent_ids']
    assert counts(pg,a.wanted['tenant_id'])['tenant_agent_instances'] == 4


def test_concurrent_same_email_only_one_customer_commits(pg):
    email = uuid4().hex+'@isolated.invalid'
    with receiver() as left, receiver() as right:
        requests = [(approval(pg,left.approval,email=email),left),(approval(pg,right.approval,email=email),right)]
        def run(pair):
            a,target = pair
            try:
                return tool.provision(pg.store,a.path,a.wanted,a.config,execute=True,credential_fd=target.fd,credential_ack_fd=target.ack_fd)
            except (tool.ProvisionBlocked,psycopg.Error):
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(run,requests))
        assert sum(value is not None for value in outcomes) == 1
        for (a,target), outcome in zip(requests,outcomes):
            if outcome:
                target.consume()
                assert counts(pg,a.wanted['tenant_id'])['tenants'] == 1
            else:
                assert all(n == 0 for n in counts(pg,a.wanted['tenant_id']).values())


def test_real_login_member_knowledge_tenant_isolation_password_revoke_and_no_provider(pg, monkeypatch):
    with receiver() as target:
        a = approval(pg,target.approval)
        tool.provision(pg.store,a.path,a.wanted,a.config,execute=True,credential_fd=target.fd,credential_ack_fd=target.ack_fd)
        packet = target.consume()
    old = pg.product.user_by_email('old-admin@isolated.invalid')
    with pg.store.connection() as conn:
        conn.execute("INSERT INTO knowledge_files(id,tenant_id,name,status) VALUES ('foreign-file','tenant-a','private','uploaded')")
        conn.execute("INSERT INTO conversations(id,tenant_id,agent_id,runtime_profile_id,runtime_thread_id,runtime_version) VALUES ('foreign-conversation','tenant-a','copywriting-agent','fixture-profile','fixture-thread','fixture')")
        conn.execute("INSERT INTO conversation_owners(conversation_id,user_id,title) VALUES ('foreign-conversation',?,'Private')",(old['id'],))
    cfg = replace(settings,database_url=pg.store.database_url,environment='test',bootstrap_demo_data=False,
        token_secret='isolated-customer-auth-session-key',secure_cookies=False,object_storage_dir=pg.tmp/'objects')
    monkeypatch.setattr(main,'settings',cfg)
    monkeypatch.setattr(main,'store',pg.store)
    monkeypatch.setattr(main,'product_store',pg.product)
    monkeypatch.setattr(main,'skill_registry',pg.registry)
    monkeypatch.setattr(main,'sessions',SessionIssuer(cfg.token_secret))
    client = TestClient(main.app)  # No lifespan/bootstrap or external service start.
    try:
        logged_in = client.post('/api/v1/auth/login',json={'account':a.wanted['admin_email'],'password':packet['temporary_password']})
        assert logged_in.status_code == 200
        assert logged_in.json()['user']['role'] == 'enterprise_admin'
        assert logged_in.json()['user']['tenant_id'] == a.wanted['tenant_id']
        old_cookie = client.cookies.get('workbench_session')
        assert client.get('/api/v1/enterprise-config').json() == {**a.config,'brand_logo':None}
        assert client.get('/api/v1/enterprise-config?tenant_id=tenant-a').json() == {**a.config,'brand_logo':None}
        assert client.get('/api/v1/knowledge/files').status_code == 200
        assert client.get('/api/v1/knowledge/files/foreign-file').status_code == 404
        assert client.get('/api/v1/conversations/foreign-conversation').status_code == 404
        assert client.put('/api/v1/members/'+old['id']+'/role',json={'role':'member'}).status_code == 404
        assert client.put('/api/v1/members/'+old['id']+'/status',json={'status':'disabled'}).status_code == 404
        assert client.get('/api/v1/platform/agents').status_code == 403
        assert client.get('/api/v1/platform/skills').status_code == 403
        member = client.post('/api/v1/members',json={'display_name':'Own Member',
            'email':uuid4().hex+'@isolated.invalid','role':'member','tenant_id':'tenant-a'})
        assert member.status_code == 201
        assert pg.product.user_by_email(member.json()['member']['email'])['tenant_id'] == a.wanted['tenant_id']
        with pg.store.connection() as conn:
            assert conn.execute('SELECT balance FROM credit_accounts WHERE tenant_id=?',(a.wanted['tenant_id'],)).fetchone()['balance'] == 0
            assert not conn.execute('SELECT 1 FROM platform_admins WHERE user_id=?',(logged_in.json()['user']['id'],)).fetchone()
        for agent in ('campaign-agent','copywriting-agent','image-agent'):
            assert client.post('/api/v1/agents/'+agent+'/runs',json={'message':'unapproved provider request'}).status_code == 404
        changed = tool.secrets.token_urlsafe(32)
        assert client.put('/api/v1/me/password',json={'current_password':packet['temporary_password'],'new_password':changed}).status_code == 200
        assert client.post('/api/v1/auth/login',json={'account':a.wanted['admin_email'],'password':packet['temporary_password']}).status_code == 401
        client.cookies.set('workbench_session',old_cookie)
        assert client.get('/api/v1/me').status_code == 401
        client.cookies.clear()
        assert client.post('/api/v1/auth/login',json={'account':a.wanted['admin_email'],'password':changed}).status_code == 200
        with pg.store.connection() as conn:
            for table in ('tasks','run_traces','generations','knowledge_files','conversations'):
                assert not conn.execute('SELECT 1 FROM '+table+' WHERE tenant_id=?',(a.wanted['tenant_id'],)).fetchone()
        pg.product.initialize()
        assert all(not item['enabled'] for item in pg.product.agents(a.wanted['tenant_id']))
    finally:
        client.close()


def test_unsafe_schema_null_key_or_missing_email_unique_fails_closed(pg):
    with pg.store.connection() as conn:
        conn.execute('ALTER TABLE tenants ALTER COLUMN poc_api_key SET NOT NULL')
    a = approval(pg)
    with pytest.raises(tool.ProvisionBlocked,match='SAFE_INITIAL_SCHEMA_REQUIRED'):
        tool.provision(pg.store,a.path,a.wanted,a.config)
    with pg.store.connection() as conn:
        conn.execute('ALTER TABLE tenants ALTER COLUMN poc_api_key DROP NOT NULL')
        conn.execute('ALTER TABLE users DROP CONSTRAINT users_email_key')
    a = approval(pg)
    with pytest.raises(tool.ProvisionBlocked,match='SAFE_UNIQUENESS_CONSTRAINTS_REQUIRED'):
        tool.provision(pg.store,a.path,a.wanted,a.config)


def test_operation_identity_cannot_be_reused_for_another_tenant(pg):
    with receiver() as target:
        a = approval(pg,target.approval)
        tool.provision(pg.store,a.path,a.wanted,a.config,execute=True,credential_fd=target.fd,credential_ack_fd=target.ack_fd)
        target.consume()
    another = approval(pg,operation=a.wanted['operation_id'])
    with pytest.raises(tool.ProvisionBlocked,match='OPERATION_ALREADY_ATTEMPTED'):
        tool.provision(pg.store,another.path,another.wanted,another.config)
    assert all(n == 0 for n in counts(pg,another.wanted['tenant_id']).values())


def test_unknown_migration_and_modified_history_rejected_without_writes(pg):
    with pg.store.connection() as conn:
        conn.execute("UPDATE schema_migrations SET checksum=? WHERE version='015'",('0'*64,))
    a = approval(pg)
    with pytest.raises(tool.ProvisionBlocked,match='SCHEMA_MIGRATION_CONTRACT_REJECTED'):
        tool.provision(pg.store,a.path,a.wanted,a.config)
    with pg.store.connection() as conn:
        conn.execute("UPDATE schema_migrations SET checksum=? WHERE version='015'",(migrate.checksum(migrate.migration_files()[-1]),))
        conn.execute("INSERT INTO schema_migrations(version,name,checksum) VALUES ('016','unknown.sql',?)",('0'*64,))
    a = approval(pg)
    with pytest.raises(tool.ProvisionBlocked,match='SCHEMA_MIGRATION_CONTRACT_REJECTED'):
        tool.provision(pg.store,a.path,a.wanted,a.config)


def test_configuration_rejects_secrets_and_no_customer_identity_is_hardcoded():
    with pytest.raises(tool.ProvisionBlocked,match='CONFIRMED_CONFIG_FIELDS_REQUIRED'):
        tool.request('new-customer','Name','admin@isolated.invalid','Admin',str(uuid4()),{'wechat_account':{'app_secret':'isolated-not-a-real-secret'}})
    with pytest.raises(tool.ProvisionBlocked,match='TENANT_ID_INVALID'):
        tool.request('../tenant','Name','admin@isolated.invalid','Admin',str(uuid4()),{})


def test_cli_execute_uses_private_handoff_and_never_outputs_credential(pg, monkeypatch, capsys):
    monkeypatch.setenv('ENTERPRISE_POC_DATABASE_URL',pg.store.database_url)
    with receiver() as target:
        a = approval(pg,target.approval)
        config_path = pg.tmp/'confirmed-cli-config.json'
        config_path.write_text(json.dumps(a.config))
        args = ['--tenant-id',a.wanted['tenant_id'],'--tenant-name',a.wanted['tenant_name'],
            '--admin-email',a.wanted['admin_email'],'--admin-display-name',a.wanted['admin_display_name'],
            '--operation-id',a.wanted['operation_id'],'--approval',str(a.path),
            '--enterprise-config',str(config_path),'--execute','--credential-fd',str(target.fd),
            '--credential-ack-fd',str(target.ack_fd)]
        assert tool.main(args) == 0
        packet = target.consume()
    output = capsys.readouterr().out
    assert packet['temporary_password'] not in output
    assert json.loads(output.splitlines()[-1])['status'] == 'provisioned'
    assert verify_password(packet['temporary_password'],pg.product.user_by_email(a.wanted['admin_email'])['password_hash'])
    with pytest.raises(SystemExit):
        tool.main(args+['--password','isolated-accidental-input'])
    assert 'isolated-accidental-input' not in capsys.readouterr().err


@pytest.mark.parametrize('interruption',['receiver','approval'])
def test_actual_mid_transaction_handoff_or_approval_loss_rolls_back(pg, interruption):
    with pg.store.connection() as conn:
        conn.execute("CREATE FUNCTION pause_customer_fixture() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(1.5); RETURN NEW; END $$")
        conn.execute('CREATE TRIGGER pause_customer BEFORE INSERT ON users FOR EACH ROW EXECUTE FUNCTION pause_customer_fixture()')
    with receiver() as target:
        a = approval(pg,target.approval)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(tool.provision,pg.store,a.path,a.wanted,a.config,execute=True,credential_fd=target.fd,credential_ack_fd=target.ack_fd)
            found = False
            for _ in range(200):
                with pg.store.connection() as conn:
                    found = conn.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event='PgSleep') AS found").fetchone()['found']
                if found:
                    break
                time.sleep(.01)
            assert found, 'actual transaction never reached fault injection point'
            if interruption == 'receiver':
                target.process.terminate()
                target.process.wait(timeout=5)
            else:
                a.value['expires_at'] = int(time.time())-1
                rewrite(a)
            with pytest.raises(tool.ProvisionBlocked):
                future.result(timeout=10)
    assert all(n == 0 for n in counts(pg,a.wanted['tenant_id']).values())
    assert 'COMMITTED' not in (pg.receipts/(a.wanted['operation_id']+'.jsonl')).read_text()


@pytest.mark.parametrize('ack_mode',['wrong_operation','wrong_digest','missing'])
def test_unconfirmed_or_wrong_credential_ack_cannot_commit_customer(pg, ack_mode):
    with receiver(ack_mode) as target:
        a = approval(pg,target.approval)
        with pytest.raises(tool.ProvisionBlocked,match='CREDENTIAL_HANDOFF_BLOCKED'):
            tool.provision(pg.store,a.path,a.wanted,a.config,execute=True,
                           credential_fd=target.fd,credential_ack_fd=target.ack_fd)
        target.consume()
    assert all(n == 0 for n in counts(pg,a.wanted['tenant_id']).values())
