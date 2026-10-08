"""Deterministic tooling safety fixtures, never a live database/provider."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('DEEPSEEK_API_KEY', 'unused-synthetic-fixture')
os.environ.setdefault('ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA', 'false')
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
spec = importlib.util.spec_from_file_location('minimal_provision_test_tool', PROJECT/'scripts/provision_test_tenant.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def scope():
    return {'contract':p.MINIMAL_CONTRACT,'authority_id':'WECHAT_PERSONAL_DB8_PRIMARY_SUCCESSOR_V1',
        'purpose':p.MINIMAL_PURPOSE,'environment':'test','application_source':p.APPLICATION_SOURCE,
        'application_tree':p.APPLICATION_TREE,'tooling_source':'a'*40,'tooling_tree':'b'*40,
        'tenant_id':p.MINIMAL_TENANT,'user_id':p.MINIMAL_USER,'user_email':p.MINIMAL_EMAIL,
        'allowed_objects':p.ALLOWED_OBJECTS,'production_deploy_authority':False,
        'budget':{'wechat':0,'provider':0,'image':0},'run_id':'30e36d4b-9f30-4bc7-bf76-e8edabb7de4d',
        'receipt_version':p.RECEIPT_VERSION,'cleanup_authority':'EXACT_RECEIPT_OWNED_OBJECTS_ONLY'}


class Connection:
    def __init__(self, db): self.db=db
    def execute(self, sql, params=()):
        return self.db.execute(sql.removesuffix(' FOR UPDATE'),params)


class Store:
    is_postgres=True
    def __init__(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
          PRAGMA foreign_keys=ON;
          CREATE TABLE tenants(id TEXT PRIMARY KEY,name TEXT,poc_api_key TEXT,created_at TEXT DEFAULT '2026-01-02T03:04:05.000000+00:00');
          CREATE TABLE enterprise_configs(tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),payload TEXT,updated_at TEXT DEFAULT '2026-01-02T03:04:05.000000+00:00');
          CREATE TABLE users(id TEXT PRIMARY KEY,tenant_id TEXT REFERENCES tenants(id),email TEXT UNIQUE,
             password_hash TEXT,display_name TEXT,role TEXT,account_status TEXT DEFAULT 'enabled',created_at TEXT DEFAULT '2026-01-02T03:04:05.000000+00:00',avatar_storage_key TEXT,avatar_mime_type TEXT);
          CREATE TABLE platform_admins(user_id TEXT PRIMARY KEY);
          CREATE TABLE credit_accounts(tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),balance INTEGER);
          CREATE TABLE agent_templates(id TEXT PRIMARY KEY);
          CREATE TABLE tenant_agent_instances(tenant_id TEXT REFERENCES tenants(id),agent_id TEXT,status TEXT);
          CREATE TABLE tasks(id TEXT PRIMARY KEY,tenant_id TEXT REFERENCES tenants(id));
        ''')
        self.db.execute("INSERT INTO tenants(id,name,poc_api_key) VALUES ('existing-tenant','Existing','unchanged-test-fixture')")
        self.db.execute("INSERT INTO enterprise_configs(tenant_id,payload) VALUES ('existing-tenant','{}')")
        self.db.execute("INSERT INTO users(id,tenant_id,email,password_hash,display_name,role) VALUES ('existing-user','existing-tenant','existing@example.invalid','unused-fixture-digest','Existing','member')")
        self.db.execute("INSERT INTO credit_accounts VALUES ('existing-tenant',100)")
        self.db.executemany('INSERT INTO agent_templates VALUES (?)',[(key,) for key in p.CATALOG])
        self.db.commit()

    @contextlib.contextmanager
    def connection(self):
        try:
            yield Connection(self.db)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise


class MinimalProvisionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='wechat-minimal-unit-')
        self.root=Path(self.temp.name)
        self.store=Store()
        self.scope=scope()
        self.patches=[
            patch.object(p,'RECEIPT_FILE',self.root/'receipt.json'),
            patch.object(p,'CREDENTIAL_FILE',self.root/'user.env'),
            patch.object(p,'secret_paths',lambda:[self.root/'synthetic.fernet',self.root/'synthetic.lock']),
            patch.object(p,'database_identity',lambda store:{'database':'enterprise_agent_test','db_role':'enterprise_agent_test'}),
            patch.object(p,'tenant_tables',lambda conn:['enterprise_configs','users','credit_accounts','tenant_agent_instances','tasks']),
            patch.object(p,'hash_password',lambda password:'unused-synthetic-digest'),
        ]
        for value in self.patches:value.start()

    def tearDown(self):
        for value in reversed(self.patches):value.stop()
        self.store.db.close()
        self.temp.cleanup()

    def provision(self): return p.provision_minimal(self.store,self.scope,execute=True)

    def approve(self):
        approval={'contract':p.MINIMAL_CONTRACT,'scope_sha256':p.digest(self.scope),
            'receipt_sha256':p.hashlib.sha256(p.RECEIPT_FILE.read_bytes()).hexdigest()}
        return patch.object(p,'native_json',lambda path:approval)

    def snapshot_existing(self):
        return {table:[dict(row) for row in self.store.db.execute('SELECT * FROM '+table+' ORDER BY 1')]
                for table in ('tenants','enterprise_configs','users','credit_accounts','agent_templates')}

    def test_minimal_only_allowed_objects(self):
        result=self.provision()
        self.assertEqual(result['create'],['tenants','enterprise_configs','users'])
        with self.store.connection() as conn:
            self.assertTrue(all(len(rows)==1 for rows in p.created_rows(conn).values()))
            p.no_extra_objects(conn)
        self.assertEqual({path.name for path in self.root.iterdir()},{'user.env','receipt.json'})

    def _no_agent(self, name):
        self.provision()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tenant_agent_instances WHERE agent_id=?',(name,)).fetchone()[0],0)

    def test_image_not_enabled(self):self._no_agent('image-agent')
    def test_copywriting_not_enabled(self):self._no_agent('copywriting-agent')
    def test_campaign_not_enabled(self):self._no_agent('campaign-agent')

    def test_no_workspace(self):
        with patch.object(p,'profile_id',side_effect=AssertionError('must not call legacy runtime builder')):
            self.provision()
        self.assertFalse((self.root/'runtime').exists())

    def test_default_legacy_behavior_unchanged(self):
        fake_settings=SimpleNamespace(database_url='unused-fixture',data_dir=self.root)
        with patch.object(p,'settings',fake_settings), patch.object(p,'POCStore',lambda value:self.store), \
             patch.object(p,'profile_id',lambda:'legacy-profile'), \
             patch.dict(os.environ,{'FIXTURE_PASSWORD':'unused-synthetic-password'}), \
             patch.object(sys,'argv',['provision','--execute','--password-env','FIXTURE_PASSWORD']), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(p.main(),0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tenant_agent_instances WHERE tenant_id=?',(p.TEST_TENANT_ID,)).fetchone()[0],3)
        self.assertEqual(self.store.db.execute('SELECT balance FROM credit_accounts WHERE tenant_id=?',(p.TEST_TENANT_ID,)).fetchone()[0],1000)
        self.assertTrue((self.root/'runtime'/p.TEST_TENANT_ID/'copywriting-agent/legacy-profile/.phase-a-control.json').is_file())

    def test_production_rejected(self):
        with self.assertRaisesRegex(p.MinimalProvisionBlocked,'PRODUCTION_MODE_BLOCKED'):
            p.validate_scope(self.scope,'production','postgresql://enterprise_agent_test@127.0.0.1:55432/enterprise_agent_test',{'source':'a'*40,'tree':'b'*40})

    def test_wrong_database_rejected(self):
        for value in ('postgresql://enterprise_agent_test@127.0.0.1:5432/enterprise_agent_test',
                      'postgresql://enterprise_agent_test@127.0.0.1:55432/production',
                      'postgresql://root@127.0.0.1:55432/enterprise_agent_test',
                      'postgresql://enterprise_agent_test@example.invalid:55432/enterprise_agent_test',
                      'sqlite:///unused-fixture'):
            with self.subTest(value=value),self.assertRaisesRegex(p.MinimalProvisionBlocked,'WRONG_DATABASE_BLOCKED'):
                p.validate_scope(self.scope,'test',value,{'source':'a'*40,'tree':'b'*40})

    def test_repeat_no_duplicates(self):
        self.provision()
        with self.assertRaises(p.MinimalProvisionBlocked):self.provision()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM tenants WHERE id=?',(p.MINIMAL_TENANT,)).fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM users WHERE tenant_id=?',(p.MINIMAL_TENANT,)).fetchone()[0],1)

    def test_exact_cleanup(self):
        self.provision()
        with self.approve():
            result=p.cleanup_minimal(self.store,self.scope,execute=True)
            again=p.cleanup_minimal(self.store,self.scope,execute=True)
        self.assertEqual(result['status'],'CLEAN')
        self.assertTrue(again['already_absent'])
        self.assertTrue(p.RECEIPT_FILE.exists())
        self.assertFalse(p.CREDENTIAL_FILE.exists())

    def test_cleanup_preserves_existing_tenant(self):
        before=self.snapshot_existing()
        self.provision()
        with self.approve():p.cleanup_minimal(self.store,self.scope,execute=True)
        self.assertEqual(before,self.snapshot_existing())

    def test_wrong_receipt_rejected(self):
        self.provision()
        with self.approve(),patch.object(p,'native_json',return_value={'contract':p.MINIMAL_CONTRACT,'scope_sha256':p.digest(self.scope),'receipt_sha256':'0'*64}):
            with self.assertRaisesRegex(p.MinimalProvisionBlocked,'RECEIPT_PIN_BLOCKED'):
                p.cleanup_minimal(self.store,self.scope,execute=True)
        self.assertTrue(self.store.db.execute('SELECT 1 FROM tenants WHERE id=?',(p.MINIMAL_TENANT,)).fetchone())

    def test_without_authorization_rejected(self):
        with patch.object(p,'native_json',side_effect=FileNotFoundError()), \
             patch.object(sys,'argv',['provision','--test-only-minimal-provision','--execute']), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(p.main(),2)
        self.assertIn('BLOCKED',output.getvalue())
        self.assertFalse(p.CREDENTIAL_FILE.exists())
        self.assertFalse(self.store.db.execute('SELECT 1 FROM tenants WHERE id=?',(p.MINIMAL_TENANT,)).fetchone())

    def test_tooling_source_drift_rejected(self):
        with self.assertRaisesRegex(p.MinimalProvisionBlocked,'TOOLING_SOURCE_DRIFT'):
            p.validate_scope(self.scope,'test','postgresql://enterprise_agent_test@127.0.0.1:55432/enterprise_agent_test',{'source':'c'*40,'tree':'b'*40})

    def test_arbitrary_tenant_and_extra_scope_rejected(self):
        for change in ({'tenant_id':'other-tenant'},{'database_url':'unused-fixture'},{'allowed_objects':['tenants','tasks']},{'budget':{'wechat':1,'provider':0,'image':0}}):
            with self.subTest(change=change),self.assertRaises(p.MinimalProvisionBlocked):
                p.validate_scope(dict(self.scope,**change),'test','postgresql://enterprise_agent_test@127.0.0.1:55432/enterprise_agent_test',{'source':'a'*40,'tree':'b'*40})

    def test_config_drift_blocks_cleanup(self):
        self.provision()
        self.store.db.execute('UPDATE enterprise_configs SET payload=? WHERE tenant_id=?',('{"foreign-change":true}',p.MINIMAL_TENANT))
        self.store.db.commit()
        with self.approve(),self.assertRaisesRegex(p.MinimalProvisionBlocked,'OWNERSHIP_OR_CONFIG_DRIFT'):
            p.cleanup_minimal(self.store,self.scope,execute=True)

    def test_unreceipted_task_blocks_cleanup(self):
        self.provision()
        self.store.db.execute('INSERT INTO tasks VALUES (?,?)',('unexpected-task',p.MINIMAL_TENANT))
        self.store.db.commit()
        with self.approve(),self.assertRaisesRegex(p.MinimalProvisionBlocked,'UNRECEIPTED_TENANT_OBJECTS'):
            p.cleanup_minimal(self.store,self.scope,execute=True)

    def test_dry_run_creates_nothing(self):
        before=self.snapshot_existing()
        result=p.provision_minimal(self.store,self.scope)
        self.assertEqual(result['workspace_create'],0)
        self.assertEqual(result['business_agent_enable'],0)
        self.assertEqual(before,self.snapshot_existing())
        self.assertEqual(list(self.root.iterdir()),[])

    def test_preexisting_tenant_rejected(self):
        self.store.db.execute('INSERT INTO tenants(id,name,poc_api_key) VALUES (?,?,?)',(p.MINIMAL_TENANT,'Existing not owned',None))
        self.store.db.commit()
        with self.assertRaisesRegex(p.MinimalProvisionBlocked,'PREEXISTING_TENANT_BLOCKED'):
            self.provision()

    def test_valid_exact_scope(self):
        self.assertIs(p.validate_scope(self.scope,'test','postgresql://enterprise_agent_test@127.0.0.1:55432/enterprise_agent_test',{'source':'a'*40,'tree':'b'*40}),self.scope)


if __name__=='__main__':unittest.main()
