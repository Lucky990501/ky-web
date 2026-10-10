"""Isolated SQLite + fake native authority. No live DB/service/network/key."""
import ast
import asyncio
from contextlib import asynccontextmanager, contextmanager
import copy
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import test_skill_dispatch as fixture  # Stub Settings BEFORE any .env import.
from app import test_tenant_seeding as policy
from app.agent_catalog import CATALOG

SOURCE='f'*40
TREE='e'*40
FAKE_EVIDENCE_ROOT=Path(tempfile.gettempdir()).resolve()/'synthetic-seeding-evidence'


def proof(store, tenant):
    with store.connection() as conn: row=dict(conn.execute('SELECT * FROM tenants WHERE id=?',(tenant,)).fetchone())
    scope=dict(contract='TEST_ONLY_MINIMAL_PROVISION_V1', authority_id='SYNTHETIC_NEW_SUCCESSOR_V1',
        purpose='WECHAT_PERSONAL_CENTER_CONFIG_V1', environment='test',application_source=SOURCE,
        application_tree=TREE,tooling_source='a'*40,tooling_tree='b'*40,tenant_id=tenant,user_id=str(uuid4()),
        run_id=str(uuid4()),allowed_objects=policy.MINIMAL_OBJECTS,production_deploy_authority=False,
        budget=dict(wechat=0,provider=0,image=0))
    receipt=dict(contract=scope['contract'],status='PROVISIONED',scope_sha256=policy.digest(scope),
        run_id=scope['run_id'],application_source=SOURCE,application_tree=TREE,tooling_source='a'*40,
        tooling_tree='b'*40,tenant_id=tenant,user_id=scope['user_id'],
        database={k:policy.TARGET[k] for k in ('database','db_role')},
        created_records={name:[dict(primary_key=tenant,row_sha256=policy.digest(row))] for name in policy.MINIMAL_OBJECTS},
        workspace_create=0,business_agent_enable=0,wechat_calls=0,provider_calls=0,image_calls=0)
    entry=dict(scope_file='provision-scope.v1.json',scope_sha256=policy.digest(scope),
        receipt_file=str(FAKE_EVIDENCE_ROOT/'synthetic-new-successor/provision-receipt.v1.json'),receipt_sha256=policy.digest(receipt))
    approval=dict(contract=policy.CONTRACT,authority_id=scope['authority_id'],environment='test',
        source_commit=SOURCE,source_tree=TREE,database=policy.TARGET,production_deploy_authority=False,
        budget=scope['budget'],exclusions=[entry])
    return approval,scope,receipt


@contextmanager
def installed(values):
    approval,scope,receipt=values
    def read(path,**kwargs):
        if path==policy.APPROVAL_PATH: return copy.deepcopy(approval),policy.digest(approval)
        if path==policy.AUTHORITY_ROOT/approval['exclusions'][0]['scope_file']: return copy.deepcopy(scope),policy.digest(scope)
        if path==Path(approval['exclusions'][0]['receipt_file']): return copy.deepcopy(receipt),policy.digest(receipt)
        raise FileNotFoundError()
    with patch.dict(os.environ,{'APP_ENV':'test',policy.REQUIRED_ENV:'true'}),\
        patch.object(policy,'EVIDENCE_ROOT',FAKE_EVIDENCE_ROOT),\
        patch.object(policy,'native_json',side_effect=read),\
        patch.object(policy,'check_database'),\
        patch.object(policy,'source_identity',return_value=dict(source_commit=SOURCE,source_tree=TREE)):
        yield


class SeedingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='synthetic-tenant-seeding-')
        self.root=Path(self.temp.name);self.store=fixture.POCStore(self.root/'fixture.db')
        with patch.dict(os.environ,{'APP_ENV':'development',policy.REQUIRED_ENV:'false'}):
            self.store.seed_demo_data();self.product=fixture.ProductStore(self.store);self.product.initialize()
        # Random exact identity, intentionally NOT a special tenant name/prefix.
        self.tenant='opaque_'+uuid4().hex
        with self.store.connection() as conn:
            conn.execute('INSERT INTO tenants(id,name,poc_api_key) VALUES (?,?,?)',(self.tenant,'[SYNTHETIC TEST] fixture',uuid4().hex))
            conn.execute('INSERT INTO enterprise_configs(tenant_id,payload) VALUES (?,?)',(self.tenant,json.dumps(
                dict(data_classification='WECHAT_PERSONAL_CENTER_CONFIG_V1',tenant_label=self.tenant))))
        self.values=proof(self.store,self.tenant)
    def tearDown(self): self.temp.cleanup()
    def rows(self,table):
        with self.store.connection() as conn:return [dict(row) for row in conn.execute('SELECT * FROM '+table)]
    def instances(self): return [row for row in self.rows('tenant_agent_instances') if row['tenant_id']==self.tenant]

    def test_01_synthetic_startup_zero_and_catalog_intact(self):
        with installed(self.values):self.product.initialize()
        self.assertEqual(self.instances(),[])
        self.assertEqual({row['id'] for row in self.rows('agent_templates')},set(CATALOG))
        self.assertTrue(all(not agent['enabled'] for agent in self.product.agents(self.tenant)))

    def test_02_repeated_actual_api_lifespan_stays_zero(self):
        path=Path(fixture.wechat_skill.__file__).parent/'main.py'
        node=next(n for n in ast.parse(path.read_bytes()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='lifespan')
        async def close(): pass
        namespace=dict(asynccontextmanager=asynccontextmanager,FastAPI=object,product_store=self.product,
            skill_registry=SimpleNamespace(initialize=Mock()),runtime=SimpleNamespace(close=close),
            settings=SimpleNamespace(environment='test',bootstrap_demo_data=False,task_queue='redis'))
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
        async def restart():
            for _ in range(3):
                async with namespace['lifespan'](None): self.assertEqual(self.instances(),[])
        with installed(self.values):asyncio.run(restart())

    def test_03_existing_instances_preserved(self):
        with self.store.connection() as conn:
            conn.execute("UPDATE tenant_agent_instances SET status='disabled' WHERE tenant_id='tenant-a' AND agent_id='image-agent'")
        before=self.rows('tenant_agent_instances')
        with installed(self.values):self.product.initialize();self.product.initialize()
        self.assertEqual(before,self.rows('tenant_agent_instances'))
        self.assertFalse(self.product.agent_enabled('tenant-a','image-agent'))
        self.assertTrue(self.product.agent_enabled('tenant-a','campaign-agent'))

    def test_04_production_ignores_test_authority(self):
        with patch.dict(os.environ,{'APP_ENV':'production',policy.REQUIRED_ENV:'true'}),\
            patch.object(policy,'native_json',side_effect=AssertionError('Production must not read Test policy')):
            self.product.initialize();self.product.initialize()
        self.assertEqual(len(self.instances()),3);self.assertTrue(all(row['status']=='enabled' for row in self.instances()))

    def test_05_development_demo_behavior_preserved(self):
        with patch.dict(os.environ,{'APP_ENV':'development',policy.REQUIRED_ENV:'true'}): self.product.initialize()
        self.assertEqual(len(self.instances()),3)

    def test_06_config_marker_and_name_are_not_authority(self):
        with patch.dict(os.environ,{'APP_ENV':'test',policy.REQUIRED_ENV:'false'}):self.product.initialize()
        self.assertEqual(len(self.instances()),3)

    def test_07_missing_required_policy_blocks_before_seeding(self):
        before=self.rows('tenant_agent_instances')
        with installed(self.values),patch.object(policy,'native_json',side_effect=FileNotFoundError('private details')):
            with self.assertRaisesRegex(RuntimeError,'^TEST_TENANT_SEEDING_POLICY_BLOCKED$'):self.product.initialize()
        self.assertEqual(before,self.rows('tenant_agent_instances'))

    def test_08_old_db8_scope_not_accepted(self):
        self.values[1]['application_source']='db8e23658baa6e4b707380e178aded561d3280f2'
        self.values[0]['exclusions'][0]['scope_sha256']=policy.digest(self.values[1])
        with installed(self.values):
            with self.assertRaises(RuntimeError):self.product.initialize()
        self.assertEqual(self.instances(),[])

    def test_09_current_source_mismatch_rejected(self):
        with installed(self.values),patch.object(policy,'source_identity',return_value=dict(source_commit='c'*40,source_tree=TREE)):
            with self.assertRaises(RuntimeError):self.product.initialize()

    def test_10_scope_and_receipt_sha_mismatch_rejected(self):
        for field in ('scope_sha256','receipt_sha256'):
            values=copy.deepcopy(self.values);values[0]['exclusions'][0][field]='0'*64
            with installed(values):
                with self.assertRaises(RuntimeError):self.product.initialize()

    def test_11_foreign_receipt_tenant_and_run_rejected(self):
        for field,value in (('tenant_id','foreign'),('run_id',str(uuid4())),('scope_sha256','0'*64)):
            values=copy.deepcopy(self.values);values[2][field]=value;values[0]['exclusions'][0]['receipt_sha256']=policy.digest(values[2])
            with installed(values):
                with self.assertRaises(RuntimeError):self.product.initialize()

    def test_12_production_or_positive_budget_scope_rejected(self):
        for field,value in (('environment','production'),('production_deploy_authority',True),('budget',dict(wechat=1,provider=0,image=0))):
            values=copy.deepcopy(self.values);values[1][field]=value;values[0]['exclusions'][0]['scope_sha256']=policy.digest(values[1])
            with installed(values):
                with self.assertRaises(RuntimeError):self.product.initialize()

    def test_13_tenant_row_drift_is_fail_closed(self):
        with self.store.connection() as conn:conn.execute('UPDATE tenants SET name=? WHERE id=?',('renamed without authority',self.tenant))
        with installed(self.values):
            with self.assertRaises(RuntimeError):self.product.initialize()
        self.assertEqual(self.instances(),[])

    def test_14_no_existing_instance_is_deleted_or_disabled(self):
        with self.store.connection() as conn:conn.execute("INSERT INTO tenant_agent_instances VALUES (?,?,'disabled')",(self.tenant,'image-agent'))
        before=self.rows('tenant_agent_instances')
        with installed(self.values):self.product.initialize()
        self.assertEqual(before,self.rows('tenant_agent_instances'))

    def test_15_absent_provisioned_tenant_after_exact_cleanup_safe(self):
        # Fixture-only exact deletion, NOT a workaround for unexpected Instances.
        with self.store.connection() as conn:conn.execute('DELETE FROM tenants WHERE id=?',(self.tenant,))
        with installed(self.values):self.product.initialize()
        self.assertEqual(self.instances(),[])

    def test_16_unregistered_tenant_not_exempt(self):
        with self.store.connection() as conn:conn.execute('INSERT INTO tenants VALUES (?,?,?)',('normal-unregistered','[SYNTHETIC TEST] misleading label','unused-key'))
        with installed(self.values):self.product.initialize()
        self.assertTrue(self.product.agent_enabled('normal-unregistered','campaign-agent'))

    def test_17_scope_path_traversal_and_foreign_receipt_path_rejected(self):
        for field,value in (('scope_file','../old-scope.v1.json'),('receipt_file','/etc/credential.json'),('receipt_file',str(policy.EVIDENCE_ROOT/'../credentials/x.json'))):
            values=copy.deepcopy(self.values);values[0]['exclusions'][0][field]=value
            with installed(values):
                with self.assertRaises(RuntimeError):self.product.initialize()

    def test_18_no_business_write_authority_from_receipt(self):
        for field in ('business_agent_enable','workspace_create','wechat_calls','provider_calls','image_calls'):
            values=copy.deepcopy(self.values);values[2][field]=1;values[0]['exclusions'][0]['receipt_sha256']=policy.digest(values[2])
            with installed(values):
                with self.assertRaises(RuntimeError):self.product.initialize()

    def test_19_existing_productized_binding_and_prepare_unchanged(self):
        fixture.DispatchTests.setUpClass();case=fixture.DispatchTests();case.setUp()
        try:
            with case.store.connection() as conn:
                conn.execute('INSERT INTO tenants VALUES (?,?,?)',(self.tenant,'[SYNTHETIC TEST] opaque fixture','unused-opaque-key'))
                tables=('tenant_agent_instances','agent_templates','agent_template_versions','agent_template_version_skills','agent_template_version_tools')
                before={table:[dict(row) for row in conn.execute('SELECT * FROM '+table)] for table in tables}
            with installed(proof(case.store,self.tenant)):case.product.initialize()
            with case.store.connection() as conn:
                after={table:[dict(row) for row in conn.execute('SELECT * FROM '+table)] for table in tables}
            self.assertEqual(before,after)
            self.assertEqual(case.result()['status'],'completed')
        finally:case.tearDown();fixture.DispatchTests.tearDownClass()

    def test_20_normal_chat_still_requires_instance(self):
        self.product.create_user(self.tenant,'synthetic-seeding@example.invalid','unused','Synthetic member','member')
        user=self.product.user_by_email('synthetic-seeding@example.invalid')
        with installed(self.values):self.product.initialize()
        with self.assertRaises(LookupError):self.product.create_task(self.tenant,user['id'],'campaign-agent','hello',None)


class DatabaseTests(unittest.TestCase):
    def test_exact_target_and_canonical_host_query(self):
        conn=Mock();conn.execute.return_value.fetchone.return_value=policy.TARGET
        store=SimpleNamespace(is_postgres=True,database_url='postgresql://enterprise_agent_test:unused@127.0.0.1:55432/enterprise_agent_test')
        policy.check_database(conn,store)
        self.assertIn('host(inet_server_addr())',conn.execute.call_args.args[0])
    def test_wrong_address_port_db_role_rejected_before_query(self):
        for url in ('postgresql://enterprise_agent_test@127.0.0.2:55432/enterprise_agent_test',
            'postgresql://enterprise_agent_test@127.0.0.1:5432/enterprise_agent_test',
            'postgresql://enterprise_agent_test@127.0.0.1:55432/production',
            'postgresql://other@127.0.0.1:55432/enterprise_agent_test'):
            conn=Mock()
            with self.assertRaises(RuntimeError):policy.check_database(conn,SimpleNamespace(is_postgres=True,database_url=url))
            conn.execute.assert_not_called()
    def test_actual_target_drift_rejected(self):
        conn=Mock();conn.execute.return_value.fetchone.return_value=dict(policy.TARGET,database='production')
        with self.assertRaises(RuntimeError):policy.check_database(conn,SimpleNamespace(is_postgres=True,database_url='postgresql://enterprise_agent_test@127.0.0.1:55432/enterprise_agent_test'))


class NativeFileTests(unittest.TestCase):
    """Fake POSIX ownership/stat boundary over a harmless local JSON fixture."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='synthetic-seeding-native-')
        self.actual=Path(self.temp.name)/'synthetic.json';self.actual.write_bytes(b'{"fixture":"synthetic"}')
        real=self.actual.stat()
        self.info=SimpleNamespace(st_mode=stat.S_IFREG|0o444,st_uid=0,st_gid=0,st_size=real.st_size,st_dev=real.st_dev,st_ino=real.st_ino)
        self.parent_info=SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0,st_gid=0)
        actual=self.actual;info=self.info;parent_info=self.parent_info
        class FakePath:
            parents=(SimpleNamespace(lstat=lambda:parent_info),)
            def lstat(self): return info
            def __fspath__(self): return str(actual)
        self.path=FakePath()
        self.server_os=SimpleNamespace(name='posix',getuid=lambda:1000,O_RDONLY=os.O_RDONLY,O_NOFOLLOW=0,
            open=os.open,fdopen=os.fdopen,fstat=os.fstat)
    def tearDown(self): self.temp.cleanup()
    def read(self,**kwargs):
        with patch.object(policy,'os',self.server_os):return policy.native_json(self.path,**kwargs)
    def test_root_owned_immutable_file_read(self):self.assertEqual(self.read()[0],{'fixture':'synthetic'})
    def test_file_owner_gid_writable_symlink_rejected(self):
        for attribute,value in (('st_uid',1000),('st_gid',1000),('st_mode',stat.S_IFREG|0o644),('st_mode',stat.S_IFLNK|0o444)):
            old=getattr(self.info,attribute);setattr(self.info,attribute,value)
            with self.assertRaises(RuntimeError):self.read()
            setattr(self.info,attribute,old)
    def test_untrusted_or_writable_ancestor_rejected(self):
        for attribute,value in (('st_uid',1000),('st_gid',1000),('st_mode',stat.S_IFDIR|0o775),('st_mode',stat.S_IFLNK|0o755)):
            old=getattr(self.parent_info,attribute);setattr(self.parent_info,attribute,value)
            with self.assertRaises(RuntimeError):self.read()
            setattr(self.parent_info,attribute,old)
    def test_non_posix_and_oversized_file_rejected(self):
        self.server_os.name='nt'
        with self.assertRaises(RuntimeError):self.read()
        self.server_os.name='posix';self.info.st_size=131073
        with self.assertRaises(RuntimeError):self.read()
    def test_file_identity_change_rejected(self):
        self.info.st_ino+=1
        with self.assertRaises(RuntimeError):self.read()
    def test_service_owned_receipt_allowed_not_authority(self):
        self.info.st_uid=1000;self.info.st_gid=1000
        with self.assertRaises(RuntimeError):self.read()
        self.assertEqual(self.read(root_owned=False)[0],{'fixture':'synthetic'})


def verify_existing_native_guard(case, source):
    """Supplementary explicit-source check, not a CI dependency on local evidence.

    The offline verifier adds this proof to the ordinary discoverable tests.
    Only graph's AST runs with synthetic dependencies; no live module import.
    """
    path=Path(source);raw=path.read_bytes()
    node=next(n for n in ast.parse(raw).body if isinstance(n,ast.FunctionDef) and n.name=='graph')
    tenant,user='opaque-native-guard-fixture','u'
    own=dict(tenants=dict(id=tenant,name='synthetic'),users=dict(id=user,tenant_id=tenant,role='enterprise_admin',account_status='enabled'),
        enterprise_configs=dict(tenant_id=tenant,payload=json.dumps(dict(data_classification='WECHAT_PERSONAL_CENTER_CONFIG_V1',tenant_label=tenant))))
    receipt=dict(tenant_id=tenant,user_id=user,created_records={name:[dict(row_sha256=policy.digest(row))] for name,row in own.items()})
    def require(value,code):
        if not value:raise RuntimeError(code)
    def legacy(data,base):return dict(active_admins=0)
    namespace=dict(old=lambda:{},receipt=lambda:receipt,copy=copy,json=json,digest=policy.digest,need=require,
        native=lambda p:None,read=lambda p:dict(row_hashes={name:[] for name in own}),
        P=Path('/synthetic-authority'),APP=Path('/absent-synthetic-app'),
        o=SimpleNamespace(APP=Path('/absent-synthetic-app'),graph=legacy),types=__import__('types'))
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),namespace)
    data={name:[row] for name,row in own.items()};data.update(platform_admins=[],execution_events=[],tenant_agent_instances=[])
    case.assertEqual(namespace['graph'](data)['synthetic_tenant'],tenant)
    data['tenant_agent_instances'].append(dict(tenant_id=tenant,agent_id='campaign-agent',status='enabled'))
    with case.assertRaisesRegex(RuntimeError,'UNAPPROVED_SYNTHETIC_TABLE:tenant_agent_instances'):namespace['graph'](data)
    case.assertEqual(raw,path.read_bytes(),'External native guard must remain unchanged')


if __name__=='__main__':unittest.main()
