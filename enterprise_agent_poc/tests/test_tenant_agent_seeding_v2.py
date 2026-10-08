"""Pinned formal V2 tooling -> real receipt -> isolated API startup/cleanup.

Explicit external source is supplied by the verifier, never imported as a live
guard. Ordinary test discovery has no dependency on local historical evidence.
"""
import ast
import asyncio
from contextlib import asynccontextmanager, contextmanager, ExitStack
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_tenant_agent_seeding as v1  # Settings stub; no .env/key reads.
from app import test_tenant_seeding as policy
from scripts import receipt_row_canonicalization as canonical

TOOL_SOURCE='85a14513b6c863f8d506b86b3083a19d97b72799'
TOOL_TREE='9683c996acacd0417d91d8e38700c0da413fc80e'
CANON_SHA='1c62c7805b473abfb906f88642c36d4e77e0a7a6c9537f31f60e1ff0b8aff09a'
PROVISION_SHA='9b60b5be33fa002c1216b5b55281101e6c528b770cea351c488700ed47594fc6'
GUARD_SHA='1a09103e8befd4ec7aaaf44ea3f5674662667cdd9ad63f90c2bf10e31b5ddfac'


def sha(raw): return hashlib.sha256(raw).hexdigest()


def load_tool(path):
    assert sha(path.read_bytes()) == PROVISION_SHA, 'Formal Provision source drift'
    spec=importlib.util.spec_from_file_location('pinned_formal_v2_fixture',path)
    value=importlib.util.module_from_spec(spec)
    with patch.object(sys,'path',list(sys.path)): spec.loader.exec_module(value)
    return value


@contextmanager
def fixture_posix_credentials(predicate):
    """Windows-only stat emulation over exact ephemeral fixture credentials.

    No native permission acceptance is claimed. Actual file I/O/deletion still
    occurs in temporary fixtures; Linux executes the unchanged checks directly.
    """
    if os.name == 'posix':
        yield
        return
    original=type(Path()).stat
    def metadata(path,*args,**kwargs):
        info=original(path,*args,**kwargs)
        if predicate(path) and stat.S_ISREG(info.st_mode):
            fields={name:getattr(info,name) for name in dir(info) if name.startswith('st_')}
            fields.update(st_mode=stat.S_IFREG|0o600,st_uid=0)
            return SimpleNamespace(**fields)
        return info
    with patch.object(type(Path()),'stat',metadata),patch.object(os,'getuid',return_value=0,create=True):yield


class CanonicalIdentityTests(unittest.TestCase):
    def setUp(self):
        self.row=dict(id='opaque-test',name='测试',poc_api_key=None,created_at='2026-10-08T20:00:00.123456+08:00')
    def test_shared_module_is_exact_approved_bytes(self):
        self.assertEqual(sha(Path(canonical.__file__).read_bytes()),CANON_SHA)
    def test_v2_is_not_raw_json_hash(self):
        self.assertNotEqual(policy.digest(self.row),canonical.row_sha256('tenants',self.row))
    def test_native_datetime_and_pg_json_same_identity(self):
        native=dict(self.row,created_at=dt.datetime(2026,10,8,12,0,0,123456,tzinfo=dt.timezone.utc))
        self.assertEqual(canonical.row_sha256('tenants',self.row),canonical.row_sha256('tenants',native))
    def test_microsecond_drift_and_missing_fields(self):
        self.assertNotEqual(canonical.row_sha256('tenants',self.row),canonical.row_sha256('tenants',dict(self.row,created_at='2026-10-08T12:00:00.123457Z')))
        with self.assertRaises(ValueError):canonical.row_sha256('tenants',{k:v for k,v in self.row.items() if k!='created_at'})
    def test_wrong_version_never_falls_back(self):
        with self.assertRaises(ValueError):canonical.row_sha256('tenants',self.row,'V1')


def integration_suite(provision_source, native_guard_source, frozen_base_guard):
    """All cross-contract cases use the actual pinned Provision functions."""
    class V2IntegrationTests(unittest.TestCase):
        def setUp(self):
            self.stack=ExitStack();self.addCleanup(self.stack.close)
            self.temp=self.stack.enter_context(tempfile.TemporaryDirectory(prefix='seeding-v2-contract-'))
            self.root=Path(self.temp);self.p=load_tool(provision_source)
            self.store=v1.fixture.POCStore(self.root/'fixture.db')
            # PostgreSQL's exact receipt field set in an ISOLATED SQLite fixture,
            # not a product-schema/migration change. Values are aware ISO times.
            with self.store.connection() as conn:
                conn.executescript('''
                    CREATE TABLE tenants(id TEXT PRIMARY KEY,name TEXT NOT NULL,poc_api_key TEXT,
                        created_at TEXT NOT NULL DEFAULT '2026-10-08T12:00:00.123456Z');
                    CREATE TABLE enterprise_configs(tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT '2026-10-08T12:00:00.123456Z');
                    CREATE TABLE users(id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES tenants(id),email TEXT UNIQUE NOT NULL,
                        password_hash TEXT NOT NULL,display_name TEXT NOT NULL,role TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT '2026-10-08T12:00:00.123456Z',avatar_storage_key TEXT,
                        avatar_mime_type TEXT,account_status TEXT NOT NULL DEFAULT 'enabled');
                    CREATE TABLE platform_admins(user_id TEXT PRIMARY KEY);
                    INSERT INTO tenants(id,name,poc_api_key) VALUES('existing-tenant','Existing','unused-test-key');
                    INSERT INTO enterprise_configs(tenant_id,payload) VALUES('existing-tenant','{}');
                ''')
            self.product=v1.fixture.ProductStore(self.store)
            with patch.dict(os.environ,{'APP_ENV':'development',policy.REQUIRED_ENV:'false'}):self.product.initialize()
            with self.store.connection() as conn:
                conn.execute("UPDATE tenant_agent_instances SET status='disabled' WHERE tenant_id='existing-tenant' AND agent_id='image-agent'")
            self.before=self.rows('tenant_agent_instances')
            evidence=self.root/'evidence';evidence.mkdir()
            self.active_scope=self.root/'native/enterprise-agent-test-successor-fixture-v2/provision-scope.v2.json'
            for name,value in dict(APPLICATION_SOURCE=v1.SOURCE,APPLICATION_TREE=v1.TREE,
                NATIVE_SCOPE=self.active_scope.parent,SCOPE_FILE=self.active_scope,
                RECEIPT_FILE=evidence/'provision-receipt.v2.json',CREDENTIAL_FILE=self.root/'user.env',
                hash_password=lambda _: 'unused-synthetic-digest',
                secret_paths=lambda:[self.root/'synthetic.fernet',self.root/'synthetic.lock'],
                database_identity=lambda _: {k:policy.TARGET[k] for k in ('database','db_role')},
                tenant_tables=self.tenant_tables).items():self.stack.enter_context(patch.object(self.p,name,value))
            # Only the future application identity is rebound in this fake
            # authority. SQL, receipt schema and V2 canonicalizer are original.
            self.scope=dict(contract=self.p.MINIMAL_CONTRACT,authority_id='WECHAT_PERSONAL_DB8_PRIMARY_SUCCESSOR_V1',
                purpose=self.p.MINIMAL_PURPOSE,environment='test',application_source=v1.SOURCE,application_tree=v1.TREE,
                tooling_source=TOOL_SOURCE,tooling_tree=TOOL_TREE,tenant_id=self.p.MINIMAL_TENANT,user_id=self.p.MINIMAL_USER,
                user_email=self.p.MINIMAL_EMAIL,allowed_objects=self.p.ALLOWED_OBJECTS,production_deploy_authority=False,
                budget=dict(wechat=0,provider=0,image=0),run_id='30e36d4b-9f30-4bc7-bf76-e8edabb7de4d',
                receipt_version=canonical.VERSION,cleanup_authority='EXACT_RECEIPT_OWNED_OBJECTS_ONLY')
            self.p.validate_scope(self.scope,'test','postgresql://enterprise_agent_test@127.0.0.1:55432/enterprise_agent_test',dict(source=TOOL_SOURCE,tree=TOOL_TREE))
            store=self.store
            class ToolStore:
                @contextmanager
                def connection(inner):
                    with store.connection() as conn:
                        class Connection:
                            def execute(inner,sql,params=()):return conn.execute(sql.removesuffix(' FOR UPDATE'),params)
                        yield Connection()
            self.tool_store=ToolStore()
            result=self.p.provision_minimal(self.tool_store,self.scope,execute=True)
            self.assertEqual(result['status'],'PROVISIONED')
            self.receipt=json.loads(self.p.RECEIPT_FILE.read_bytes())
            self.entry=dict(scope_file='provision-scope.v2.json',scope_sha256='',
                receipt_file=str(self.p.RECEIPT_FILE),receipt_sha256='',active_scope_file=str(self.active_scope))
            self.approval=dict(contract=policy.CONTRACT,authority_id=self.scope['authority_id'],environment='test',
                source_commit=v1.SOURCE,source_tree=v1.TREE,database=policy.TARGET,production_deploy_authority=False,
                budget=self.scope['budget'],exclusions=[self.entry])
            self.seal();self.revoked=False
            self.stack.enter_context(fixture_posix_credentials(lambda path:path==self.p.CREDENTIAL_FILE))

        def rows(self,table):
            with self.store.connection() as conn:return [dict(row) for row in conn.execute('SELECT * FROM '+table)]
        def tenant_tables(self,conn):
            names=[row['name'] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return [name for name in names if 'tenant_id' in {row['name'] for row in conn.execute('PRAGMA table_info('+name+')')}]
        def seal(self):
            self.scope_raw=self.p.canonical(self.scope)+b'\n';self.receipt_raw=self.p.canonical(self.receipt)+b'\n'
            self.entry.update(scope_sha256=sha(self.scope_raw),receipt_sha256=sha(self.receipt_raw))
        def rescope(self):
            self.receipt['scope_sha256']=self.p.digest(self.scope);self.seal()
        def native_read(self,path,**kwargs):
            if path==policy.APPROVAL_PATH:return copy.deepcopy(self.approval),sha(self.p.canonical(self.approval))
            if path==policy.AUTHORITY_ROOT/self.entry['scope_file']:
                return copy.deepcopy(self.scope),sha(self.scope_raw)
            if path==self.active_scope:
                if self.revoked:raise FileNotFoundError('Active Scope revoked')
                return copy.deepcopy(self.scope),sha(self.scope_raw)
            if path==self.p.RECEIPT_FILE:return copy.deepcopy(self.receipt),sha(self.receipt_raw)
            raise AssertionError('Unexpected authority read')
        @contextmanager
        def authorized(self):
            with patch.dict(os.environ,{'APP_ENV':'test',policy.REQUIRED_ENV:'true'}),\
                patch.object(policy,'EVIDENCE_ROOT',self.root/'evidence'),\
                patch.object(policy,'NATIVE_SCOPE_PARENT',self.root/'native'),\
                patch.object(policy,'native_json',side_effect=self.native_read),patch.object(policy,'check_database'),\
                patch.object(policy,'source_identity',return_value=dict(source_commit=v1.SOURCE,source_tree=v1.TREE)):yield
        def start(self):
            with self.authorized():self.product.initialize()
        def zero(self):
            self.assertEqual([row for row in self.rows('tenant_agent_instances') if row['tenant_id']==self.p.MINIMAL_TENANT],[])
        def blocked(self):
            with self.assertRaisesRegex(RuntimeError,'^TEST_TENANT_SEEDING_POLICY_BLOCKED$'):self.start()
            self.zero();self.assertEqual(self.before,self.rows('tenant_agent_instances'))
        def approve_cleanup(self):
            return patch.object(self.p,'native_json',return_value=dict(contract=self.p.MINIMAL_CONTRACT,
                scope_sha256=self.p.digest(self.scope),receipt_sha256=sha(self.p.RECEIPT_FILE.read_bytes())))

        def test_01_formal_receipt_to_api_startup_zero(self):
            with self.store.connection() as conn:rows=self.p.created_rows(conn)
            for table,values in rows.items():
                self.assertEqual(canonical.row_sha256(table,values[0]),self.receipt['created_records'][table][0]['row_sha256'])
            self.assertEqual(self.p.RECEIPT_FILE.read_bytes(),self.receipt_raw)
            self.assertNotEqual(sha(self.scope_raw),self.receipt['scope_sha256'])  # Raw pin != canonical Scope hash.
            self.start();self.zero();self.assertEqual(self.before,self.rows('tenant_agent_instances'))
            self.assertEqual({row['id'] for row in self.rows('agent_templates')},set(v1.CATALOG))
        def test_02_actual_api_lifespan_three_starts(self):
            path=Path(v1.fixture.wechat_skill.__file__).parent/'main.py'
            node=next(n for n in ast.parse(path.read_bytes()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='lifespan')
            async def close():pass
            namespace=dict(asynccontextmanager=asynccontextmanager,FastAPI=object,product_store=self.product,
                skill_registry=SimpleNamespace(initialize=Mock()),runtime=SimpleNamespace(close=close),
                settings=SimpleNamespace(environment='test',bootstrap_demo_data=False,task_queue='redis'))
            exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
            async def restart():
                for _ in range(3):
                    async with namespace['lifespan'](None):self.zero()
            with self.authorized():asyncio.run(restart())
            self.assertEqual(self.before,self.rows('tenant_agent_instances'))
        def test_03_v2_raw_row_hash_rejected(self):
            row=next(row for row in self.rows('tenants') if row['id']==self.p.MINIMAL_TENANT)
            self.receipt['created_records']['tenants'][0]['row_sha256']=policy.digest(row);self.seal();self.blocked()
        def test_04_wrong_hash_rejected(self):
            self.receipt['created_records']['tenants'][0]['row_sha256']='0'*64;self.seal();self.blocked()
        def test_05_wrong_tenant_rejected(self):
            self.receipt['tenant_id']='foreign';self.seal();self.blocked()
        def test_06_wrong_source_rejected(self):
            self.scope['application_source']=self.receipt['application_source']='c'*40;self.rescope();self.blocked()
        def test_07_wrong_environment_rejected(self):
            self.scope['environment']='production';self.rescope();self.blocked()
        def test_08_forged_scope_raw_pin_rejected(self):
            self.scope['user_id']='11111111-1111-4111-8111-111111111111'
            self.scope_raw=self.p.canonical(self.scope);self.blocked()
        def test_09_revoked_active_scope_rejected(self):
            self.revoked=True;self.blocked()
        def test_10_archived_scope_cannot_replace_revoked_active_scope(self):
            self.entry['scope_file']='provision-scope.approved.v2.json';self.blocked()
        def test_11_v1_downgrade_markers_rejected(self):
            self.scope['contract']=self.receipt['contract']='TEST_ONLY_MINIMAL_PROVISION_V1'
            self.entry.pop('active_scope_file')
            self.entry['scope_file']='provision-scope.v1.json';self.rescope();self.blocked()
        def test_12_v1_downgrade_stripped_version_v2_path_rejected(self):
            self.scope['contract']=self.receipt['contract']='TEST_ONLY_MINIMAL_PROVISION_V1'
            self.entry.pop('active_scope_file')
            self.scope.pop('receipt_version');self.scope.pop('cleanup_authority');self.receipt.pop('receipt_version')
            self.rescope();self.blocked()
        def test_13_v1_downgrade_v2_hash_not_reinterpreted(self):
            self.scope['contract']=self.receipt['contract']='TEST_ONLY_MINIMAL_PROVISION_V1'
            self.entry.pop('active_scope_file')
            self.scope.pop('receipt_version');self.scope.pop('cleanup_authority');self.receipt.pop('receipt_version')
            self.entry['scope_file']='provision-scope.v1.json';self.rescope();self.blocked()
        def test_14_unknown_contract_and_version_rejected(self):
            for place,key,value in ((self.scope,'contract','TEST_ONLY_MINIMAL_PROVISION_V3'),
                (self.receipt,'receipt_version','V1'),(self.scope,'receipt_version','V3')):
                old=place[key];place[key]=value;self.rescope();self.blocked();place[key]=old
            self.rescope();self.start();self.zero()
        def test_15_wrong_cleanup_authority_rejected(self):
            self.scope['cleanup_authority']='ANY_TENANT';self.rescope();self.blocked()
        def test_16_wrong_record_identity_or_extra_record_rejected(self):
            record=self.receipt['created_records']['users'][0];record['primary_key']='foreign';self.seal();self.blocked()
            record['primary_key']=self.p.MINIMAL_USER
            self.receipt['created_records']['users'].append(dict(record));self.seal();self.blocked()
        def test_17_scope_extra_field_or_wildcard_rejected(self):
            self.scope['synthetic']=True;self.rescope();self.blocked();self.scope.pop('synthetic')
            self.scope['tenant_id']='*';self.rescope();self.blocked()
        def test_18_receipt_pin_rejected(self):
            self.entry['receipt_sha256']='0'*64;self.blocked()
        def test_19_tenant_microsecond_drift_rejected(self):
            with self.store.connection() as conn:conn.execute('UPDATE tenants SET created_at=? WHERE id=?',('2026-10-08T12:00:00.123457Z',self.p.MINIMAL_TENANT))
            self.blocked()
        def test_20_timezone_only_change_keeps_v2_identity(self):
            with self.store.connection() as conn:conn.execute('UPDATE tenants SET created_at=? WHERE id=?',('2026-10-08T20:00:00.123456+08:00',self.p.MINIMAL_TENANT))
            self.start();self.zero()
        def test_21_production_semantics_preserved(self):
            with patch.dict(os.environ,{'APP_ENV':'production',policy.REQUIRED_ENV:'true'}),\
                patch.object(policy,'native_json',side_effect=AssertionError('No Test read in Production')):self.product.initialize()
            own=[row for row in self.rows('tenant_agent_instances') if row['tenant_id']==self.p.MINIMAL_TENANT]
            self.assertEqual(len(own),3);self.assertTrue(all(row['status']=='enabled' for row in own))
        def test_22_exact_cleanup_and_idempotency(self):
            self.start();before=self.p.RECEIPT_FILE.read_bytes()
            with self.approve_cleanup():
                result=self.p.cleanup_minimal(self.tool_store,self.scope,execute=True)
                again=self.p.cleanup_minimal(self.tool_store,self.scope,execute=True)
            self.assertEqual(result['status'],'CLEAN');self.assertTrue(again['already_absent'])
            self.assertEqual(before,self.p.RECEIPT_FILE.read_bytes());self.assertFalse(self.p.CREDENTIAL_FILE.exists())
            self.start();self.zero();self.assertEqual(self.before,self.rows('tenant_agent_instances'))
            with self.assertRaises(self.p.MinimalProvisionBlocked):self.p.provision_minimal(self.tool_store,self.scope,execute=True)
        def test_23_cleanup_row_drift_rejected(self):
            with self.store.connection() as conn:conn.execute('UPDATE enterprise_configs SET updated_at=? WHERE tenant_id=?',('2026-10-08T12:00:00.123457Z',self.p.MINIMAL_TENANT))
            with self.approve_cleanup(),self.assertRaisesRegex(self.p.MinimalProvisionBlocked,'OWNERSHIP_OR_CONFIG_DRIFT'):
                self.p.cleanup_minimal(self.tool_store,self.scope,execute=True)
        def test_24_old_9e6_failure_reproduced_v2_path_fixed(self):
            old=ModuleType('frozen_9e6_seeding_guard');old.__file__=policy.__file__
            exec(compile(frozen_base_guard,'git-9e6-test_tenant_seeding.py','exec'),old.__dict__)
            active=self.entry.pop('active_scope_file')  # Original four-field declaration for the original failure.
            with self.authorized(),patch.multiple(old,native_json=self.native_read,check_database=Mock(),
                source_identity=lambda:dict(source_commit=v1.SOURCE,source_tree=v1.TREE),EVIDENCE_ROOT=self.root/'evidence'),\
                patch.object(policy,'authorized_exclusions',old.authorized_exclusions):
                with self.assertRaises(RuntimeError):self.product.initialize()
            self.entry['active_scope_file']=active
            self.start();self.zero()
        def test_25_v2_native_guard_still_rejects_instances(self):
            verify_native_v2(self,native_guard_source,self)
        def test_26_wrong_application_tree_rejected(self):
            self.scope['application_tree']=self.receipt['application_tree']='c'*40;self.rescope();self.blocked()
        def test_27_receipt_tooling_identity_mismatch_rejected(self):
            self.receipt['tooling_source']='c'*40;self.seal();self.blocked()
        def test_28_unknown_receipt_field_rejected(self):
            self.receipt['synthetic']=True;self.seal();self.blocked()
        def test_29_missing_v2_active_binding_rejected(self):
            self.entry.pop('active_scope_file');self.blocked()
        def test_30_archived_or_foreign_active_scope_path_rejected(self):
            for path in (self.active_scope.with_name('provision-scope.approved.v2.json'),
                self.root/'native/ordinary-user/provision-scope.v2.json'):
                self.entry['active_scope_file']=str(path);self.blocked()
        def test_31_active_scope_pin_mismatch_rejected(self):
            original=self.native_read
            def changed(path,**kwargs):
                value,raw_sha=original(path,**kwargs)
                return (value,'0'*64) if path==self.active_scope else (value,raw_sha)
            with patch.object(self,'native_read',side_effect=changed):self.blocked()
        def test_32_canonical_code_drift_rejected_before_hashing(self):
            original=type(Path()).read_bytes;target=Path(canonical.__file__)
            def drift(path):return b'altered canonical source' if path==target else original(path)
            with patch.object(type(Path()),'read_bytes',drift),patch.object(canonical,'row_sha256') as hasher:
                self.blocked();hasher.assert_not_called()

    return unittest.defaultTestLoader.loadTestsFromTestCase(V2IntegrationTests)


def verify_native_v2(case,path,fixture):
    raw=path.read_bytes();case.assertEqual(sha(raw),GUARD_SHA)
    node=next(n for n in ast.parse(raw).body if isinstance(n,ast.FunctionDef) and n.name=='graph')
    names=('tenants','users','enterprise_configs','tenant_agent_instances','platform_admins','execution_events')
    data={name:fixture.rows(name) for name in names};tenant=fixture.p.MINIMAL_TENANT
    cut={name:[row for row in rows if row.get('tenant_id')!=tenant and not(name=='tenants' and row['id']==tenant)] for name,rows in data.items()}
    def require(value,code):
        if not value:raise RuntimeError(code)
    def original(data,base):return dict(active_admins=0)
    namespace=dict(old=lambda:{},receipt=lambda:fixture.receipt,copy=copy,json=json,row_sha256=canonical.row_sha256,
        digest=policy.digest,need=require,native=lambda _:None,read=lambda _:dict(row_hashes={name:sorted(policy.digest(row) for row in rows) for name,rows in cut.items()}),
        P=Path('/synthetic-authority'),APP=Path('/absent-synthetic-app'),
        o=SimpleNamespace(APP=Path('/absent-synthetic-app'),graph=original),types=__import__('types'))
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
    case.assertEqual(namespace['graph'](data)['synthetic_tenant'],tenant)
    data['tenant_agent_instances'].append(dict(tenant_id=tenant,agent_id='image-agent',status='enabled'))
    with case.assertRaisesRegex(RuntimeError,'UNAPPROVED_SYNTHETIC_TABLE:tenant_agent_instances'):namespace['graph'](data)
    case.assertEqual(raw,path.read_bytes())
