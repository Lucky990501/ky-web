"""Real d8a authenticated profile API, synthetic codec/DB; no live credentials."""
import ast
import copy
import json
from pathlib import Path
from unittest.mock import Mock,patch
import unittest

import test_wechat_secret_provisioning as f  # Settings stub before imports.
from scripts import wechat_persistent_config_guard as g


class LifecycleTests(unittest.TestCase):
    setUpClass=classmethod(f.SecretTests.setUpClass.__func__)
    tearDownClass=classmethod(f.SecretTests.tearDownClass.__func__)

    def setUp(self):
        f.SecretTests.setUp(self)
        with self.store.connection() as conn:
            conn.execute("ALTER TABLE enterprise_configs ADD COLUMN updated_at TEXT NOT NULL DEFAULT '2026-10-09T00:00:00.000000Z'")
        self.original=self.row()
        self.pins={'enterprise_configs':sorted(g.raw_hash(r) for r in self.rows('enterprise_configs'))}
        self.scope=dict(contract=g.VERSION,environment='test',source=g.SOURCE,tree=g.TREE,tenant_id='tenant-a',
            tenant_kind='REGISTERED_PERSISTENT_TEST',baseline_row=copy.deepcopy(self.original),baseline_raw_sha256=g.raw_hash(self.original),
            actors={self.admin['id']:g.raw_hash(next(r for r in self.rows('users') if r['id']==self.admin['id']))},recovery_authority=None)
        self.proofs=[]
    def tearDown(self):f.SecretTests.tearDown(self)
    def rows(self,table):
        with self.store.connection() as conn:return [dict(r) for r in conn.execute('SELECT * FROM '+table)]
    def row(self):return next(r for r in self.rows('enterprise_configs') if r['tenant_id']=='tenant-a')
    def data(self):
        return {t:self.rows(t) for t in ('enterprise_configs','users','tenant_agent_instances','agent_templates','execution_events')}
    def status(self,tenant):
        self.assertEqual(tenant,'tenant-a')
        response=self.client.get('/api/v1/profile/wechat-account');self.assertEqual(response.status_code,200)
        return response.json()
    def native(self,data):
        # Full immutable fixture delegate for every other protected table/row.
        self.assertEqual(sorted(g.raw_hash(r) for r in data['enterprise_configs']),self.pins['enterprise_configs'])
        return dict(status='FULL_NATIVE_DELEGATE_PASS')
    def check(self):
        data=self.data();original=copy.deepcopy(data)
        result=g.verify_with_native(data,self.scope,self.proofs,self.pins,native_graph=self.native,status_reader=self.status)
        self.assertEqual(data,original,'Normalization must not mutate live DB/snapshot')
        return result
    def observe(self,operation,call):
        def api():
            response=call();value=response.json()
            return dict(http_status=response.status_code,authenticated_actor_id=self.admin['id'],
                **{k:value.get(k) for k in ('wechat_app_id','account_display_name','app_secret_configured','secret_version')})
        proof=g.observe_api_operation(self.scope,operation=operation,actor_id=self.admin['id'],before_reader=self.row,
            authenticated_api_call=api,after_reader=self.row,audit_reader=lambda:self.rows('execution_events')[-1])
        self.proofs.append(proof);return proof
    def save(self):
        return self.observe('save',lambda:self.client.put('/api/v1/profile/wechat-account',json={
            'wechat_app_id':f.APPID,'app_secret':f.SECRET,'account_display_name':'测试公众号'}))
    def reject(self,code=None):
        with self.assertRaises(g.Blocked) as error:self.check()
        if code:self.assertEqual(str(error.exception),code)

    def test_01_pristine_unchanged_pass(self):self.assertEqual(self.check()['status'],'PASS')
    def test_02_formal_save_pass(self):self.save();self.check()
    def test_03_refresh_name_preserves_secret_pass(self):
        self.save();old=self.backend.resolve('tenant-a',self.row_account()['wechat_app_secret_ref'],f.APPID)
        self.observe('save',lambda:self.client.put('/api/v1/profile/wechat-account',json={'app_secret':'','account_display_name':'刷新后的名称'}))
        self.check();self.assertEqual(self.status('tenant-a')['secret_version'],1)
        self.assertEqual(self.backend.resolve('tenant-a',self.row_account()['wechat_app_secret_ref'],f.APPID),old)
    def row_account(self):return json.loads(self.row()['payload'])['wechat_account']
    def test_04_rotation_pass(self):
        self.save();self.observe('rotate',lambda:self.client.post('/api/v1/profile/wechat-account/secret/rotate',json={'app_secret':f.NEXT}))
        self.check();self.assertEqual(self.status('tenant-a')['secret_version'],2)
    def test_05_revoke_pass(self):
        self.save();self.observe('revoke',lambda:self.client.delete('/api/v1/profile/wechat-account'))
        self.check();self.assertFalse(self.status('tenant-a')['app_secret_configured'])
    def test_06_observed_timestamp_change_pass(self):
        # ISOLATED trigger models an approved DB-managed update timestamp; no
        # product migration or claim that current PRIMARY has this trigger.
        with self.store.connection() as conn:conn.executescript("""
            CREATE TRIGGER fixture_config_time AFTER UPDATE OF payload ON enterprise_configs
            BEGIN UPDATE enterprise_configs SET updated_at='2026-10-09T00:00:01.000000Z' WHERE tenant_id=NEW.tenant_id; END;
        """)
        self.save();self.check();self.assertNotEqual(self.row()['updated_at'],self.original['updated_at'])
    def test_07_protected_identity_unchanged(self):
        before={t:self.rows(t) for t in ('users','tenant_agent_instances','agent_templates')}
        self.save();self.check();self.assertEqual(before,{t:self.rows(t) for t in before})
    def test_08_wrong_tenant_rejected(self):
        self.save();self.scope['tenant_id']='tenant-b';self.reject()
    def test_09_unobserved_legacy_save_fail_closed(self):
        f.SecretTests.save(self);self.reject('PERSISTENT_LEGACY_EVIDENCE_GAP')
    def test_10_sparse_audit_cannot_prove_display_name(self):
        f.SecretTests.save(self);audits=copy.deepcopy(self.rows('execution_events'))
        with self.store.connection() as conn:
            payload=json.loads(self.row()['payload']);payload['wechat_account']['account_display_name']='未经授权的名称'
            conn.execute('UPDATE enterprise_configs SET payload=? WHERE tenant_id=?',(json.dumps(payload,ensure_ascii=False),'tenant-a'))
        self.assertEqual(audits,self.rows('execution_events'))
        self.assertEqual(self.status('tenant-a')['secret_version'],1)
        self.reject('PERSISTENT_LEGACY_EVIDENCE_GAP')
    def test_11_post_observation_mutation_reject(self):
        self.save()
        with self.store.connection() as conn:
            value=json.loads(self.row()['payload']);value['wechat_account']['account_display_name']='非法篡改'
            conn.execute('UPDATE enterprise_configs SET payload=? WHERE tenant_id=?',(json.dumps(value,ensure_ascii=False),'tenant-a'))
        self.reject('PERSISTENT_UNAUTHORIZED_CURRENT_MUTATION')
    def test_12_non_wechat_mutation_reject(self):
        self.save()
        with self.store.connection() as conn:
            value=json.loads(self.row()['payload']);value['company_name']='非法改名'
            conn.execute('UPDATE enterprise_configs SET payload=? WHERE tenant_id=?',(json.dumps(value,ensure_ascii=False),'tenant-a'))
        self.reject('PERSISTENT_NON_WECHAT_MUTATION')
    def test_13_cross_tenant_secret_rejected_before_status_read(self):
        self.save()
        with self.store.connection() as conn:
            value=json.loads(self.row()['payload']);value['wechat_account']['wechat_app_secret_ref']['tenant_id']='tenant-b'
            conn.execute('UPDATE enterprise_configs SET payload=? WHERE tenant_id=?',(json.dumps(value),'tenant-a'))
        with patch.object(self,'status',side_effect=AssertionError('No cross-Tenant resolution')):
            with self.assertRaises(Exception):self.check()
    def test_14_synthetic_scope_never_exempted(self):
        self.save()
        with self.assertRaises(g.Blocked):g.normalize(self.data(),self.scope,self.proofs,self.pins,ephemeral_tenants=('tenant-a',),status_reader=self.status)
    def test_15_current_data_cannot_be_new_historical_fixture(self):
        self.save();self.scope['baseline_row']=self.row();self.scope['baseline_raw_sha256']=g.raw_hash(self.row())
        self.reject('PERSISTENT_HISTORICAL_WITNESS_MISMATCH')
    def test_16_existing_actor_identity_drift_reject(self):
        self.save()
        with self.store.connection() as conn:conn.execute('UPDATE users SET display_name=? WHERE id=?',('改了受保护身份',self.admin['id']))
        self.reject('PERSISTENT_ACTOR_IDENTITY_DRIFT')
    def test_17_member_no_observation(self):
        member=f.UserPrincipal(self.member['id'],'tenant-a','member',self.sessions.credential_version(self.member['password_hash']))
        self.client.cookies.set('workbench_session',self.sessions.issue(member))
        with self.assertRaises(g.Blocked):self.save()
        self.assertEqual(self.proofs,[])
    def test_18_audit_mutation_reject(self):
        self.save()
        with self.store.connection() as conn:conn.execute("UPDATE execution_events SET payload='{}' WHERE id=?",(self.proofs[0]['audit_id'],))
        self.reject('PERSISTENT_AUDIT_DRIFT')
    def test_19_broken_transition_chain_reject(self):
        self.save();self.observe('save',lambda:self.client.put('/api/v1/profile/wechat-account',json={'app_secret':''}))
        self.proofs[1]['before']=copy.deepcopy(self.original);self.reject()
    def test_20_wrong_source_reject(self):
        self.save();self.proofs[0]['source']='c'*40;self.reject()
    def test_21_wrong_environment_reject(self):
        self.save();self.scope['environment']='production';self.reject()
    def test_22_timestamp_tamper_reject(self):
        self.save()
        with self.store.connection() as conn:conn.execute('UPDATE enterprise_configs SET updated_at=? WHERE tenant_id=?',('2026-10-09T00:01:00.000000Z','tenant-a'))
        self.reject('PERSISTENT_UNAUTHORIZED_CURRENT_MUTATION')
    def test_23_reauthorization_needs_new_root_authority(self):
        f.SecretTests.save(self)
        self.observe('reauthorize',lambda:self.client.put('/api/v1/profile/wechat-account',json={'app_secret':''}))
        self.reject('PERSISTENT_LEGACY_EVIDENCE_GAP')
    def test_24_protected_reauthorization_preserves_connected_secret(self):
        f.SecretTests.save(self);self.service.network_allowed=True;self.service.connection_tester=f.SimpleNamespace(verify=lambda *_:None)
        self.service.test_connection(self.principal)
        self.scope['recovery_authority']=g.RECOVERY
        old=self.status('tenant-a')
        self.observe('reauthorize',lambda:self.client.put('/api/v1/profile/wechat-account',json={'app_secret':''}))
        self.check();self.assertEqual(old,self.status('tenant-a'));self.assertEqual(self.status('tenant-a')['secret_version'],1)
    def test_25_missing_verification_audit_reject(self):
        self.save();self.backend.server_connected_fixture('tenant-a')
        self.reject('PERSISTENT_VERIFICATION_EVIDENCE_GAP')
    def test_26_cleanup_then_actual_application_restart_pass(self):
        self.save();self.observe('revoke',lambda:self.client.delete('/api/v1/profile/wechat-account'))
        before=self.rows('tenant_agent_instances')
        for _ in range(3):self.product.initialize();self.check()
        self.assertEqual(before,self.rows('tenant_agent_instances'))
    def test_27_full_native_delegate_cannot_be_skipped(self):
        self.save();callback=Mock(side_effect=g.Blocked('OTHER_NATIVE_IDENTITY_FAILURE'))
        with self.assertRaisesRegex(g.Blocked,'OTHER_NATIVE_IDENTITY_FAILURE'):
            g.verify_with_native(self.data(),self.scope,self.proofs,self.pins,native_graph=callback,status_reader=self.status)
        callback.assert_called_once()
    def test_28_frontend_claim_is_not_authority(self):
        result=self.client.put('/api/v1/profile/wechat-account',json={'wechat_app_id':f.APPID,'app_secret':f.SECRET,'connected':True})
        self.assertEqual(result.status_code,422);self.check()
    def test_29_other_tenant_mutation_still_reaches_full_native(self):
        self.save()
        with self.store.connection() as conn:conn.execute("UPDATE enterprise_configs SET payload='{}' WHERE tenant_id='tenant-b'")
        with self.assertRaises(AssertionError):self.check()
    def test_30_timestamp_regression_rejected(self):
        self.save();self.proofs[0]['after']['updated_at']='2026-10-08T23:59:59.000000Z';self.reject()
    def test_32_missing_root_authority_rejects(self):
        from app import test_tenant_seeding as native_files
        with patch.object(native_files,'native_json',side_effect=FileNotFoundError()):
            with self.assertRaises(FileNotFoundError):g.load_authority()
    def test_33_preflight_preserves_source_schema_and_delegate(self):
        self.save();schema={'declared_fixture':'not_a_live_schema'};data=self.data()
        native=f.SimpleNamespace(SOURCE=g.SOURCE,TREE=g.TREE,load=lambda:{'schema015_fingerprint':g.raw_hash(schema),'table_set':list(data)},
            check_env=Mock(),source_identity=Mock(),snapshot=lambda:(schema,data),digest=g.raw_hash,receipt=lambda:None,graph=self.native)
        with patch.object(g,'load_authority',return_value=(self.scope,self.proofs,self.pins)):
            self.assertEqual(g.preflight_existing_native(native,status_reader=self.status)['status'],'PASS')
        native.check_env.assert_called_once();native.source_identity.assert_called_once()
    def test_34_preflight_schema_mismatch_fail_closed(self):
        native=f.SimpleNamespace(load=lambda:{'schema015_fingerprint':'incorrect','table_set':[]},check_env=Mock(),source_identity=Mock(),
            snapshot=lambda:({},self.data()),digest=g.raw_hash)
        with patch.object(g,'load_authority',side_effect=AssertionError('Must stop before authority read')):
            with self.assertRaisesRegex(g.Blocked,'PERSISTENT_NATIVE_SCHEMA_DRIFT'):g.preflight_existing_native(native,status_reader=self.status)
    def test_35_recovery_cannot_bless_unknown_legacy_timestamp(self):
        f.SecretTests.save(self)
        with self.store.connection() as conn:conn.execute('UPDATE enterprise_configs SET updated_at=? WHERE tenant_id=?',('2026-10-09T00:00:10.000000Z','tenant-a'))
        self.scope['recovery_authority']=g.RECOVERY
        self.observe('reauthorize',lambda:self.client.put('/api/v1/profile/wechat-account',json={'app_secret':''}))
        self.reject()
    def test_36_unapproved_observer_reject(self):
        self.save();self.proofs[0]['observer']='FRONTEND_DECLARATION';self.reject()
    def test_37_wrong_status_version_reject(self):
        self.save();original=self.status
        with patch.object(self,'status',side_effect=lambda tenant:dict(original(tenant),secret_version=999)):self.reject()
    def test_38_duplicate_observation_reject(self):
        self.save();self.proofs.append(copy.deepcopy(self.proofs[0]));self.reject('PERSISTENT_AUDIT_ORDER')
    def test_39_plaintext_config_reject(self):
        self.save()
        with self.store.connection() as conn:
            value=json.loads(self.row()['payload']);value['wechat_account']['app_secret']='SYNTHETIC_NOT_A_REAL_SECRET'
            conn.execute('UPDATE enterprise_configs SET payload=? WHERE tenant_id=?',(json.dumps(value),'tenant-a'))
        with self.assertRaises(Exception):self.check()
    def test_40_06_actual_schema_and_field_names_match_expected_contract(self):
        snapshot=json.loads((Path(__file__).parent/'fixtures/wechat_guard_readonly_snapshot_v1.json').read_bytes())
        self.assertEqual((snapshot['source'],snapshot['tree']),(g.SOURCE,g.TREE))
        self.assertEqual(tuple(c['name'] for c in snapshot['enterprise_configs']),g.canon.COLUMNS['enterprise_configs'])
        self.assertEqual([(c['udt'],c['nullable']) for c in snapshot['enterprise_configs']],[('text','NO'),('text','NO'),('timestamptz','NO')])
        self.assertEqual(snapshot['user_triggers'],[]);self.assertEqual(len(snapshot['protected_tables']),12)
        self.assertEqual(snapshot['protected_tables']['enterprise_configs'],['tenant_id'])
        self.save()
        record=json.loads(self.rows('execution_events')[-1]['payload'])
        self.assertEqual(sorted(record),snapshot['provision_event_fields'])
        self.assertEqual(sorted(self.row_account()),snapshot['config_account_fields'])
        self.assertEqual(sorted(self.row_account()['wechat_app_secret_ref']),snapshot['reference_fields'])
    def test_41_actual_d8a_no_trigger_save_does_not_change_timestamp(self):
        before=self.row()['updated_at'];self.save();self.assertEqual(self.row()['updated_at'],before);self.check()
    def test_42_06_sparse_connected_audit_cannot_be_upgraded_to_config_proof(self):
        snapshot=json.loads((Path(__file__).parent/'fixtures/wechat_guard_readonly_snapshot_v1.json').read_bytes())
        self.assertFalse(snapshot['config_transition_digest_present'])
        self.assertEqual(snapshot['formal_guard_result'],'EXISTING_FIXTURE_MUTATION:enterprise_configs')
        f.SecretTests.save(self);self.service.network_allowed=True;self.service.connection_tester=f.SimpleNamespace(verify=lambda *_:None)
        self.service.test_connection(self.principal)
        record=json.loads(self.rows('execution_events')[-1]['payload'])
        self.assertEqual(sorted(record),snapshot['verification_event_fields'])
        self.assertEqual((record['version'],record['status']),(snapshot['audit_secret_version'],snapshot['audit_verification_status']))
        self.reject('PERSISTENT_LEGACY_EVIDENCE_GAP')


def old_native_graph_proof(source):
    """Exact old graph AST demonstrates rejection, not a live native execution."""
    original=source.read_bytes()
    node=next(n for n in ast.parse(original).body if isinstance(n,ast.FunctionDef) and n.name=='graph')
    class OldGuardTests(LifecycleTests):
        def test_31_old_sealed_guard_rejects_config_new_overlay_passes(self):
            from types import SimpleNamespace
            pins=copy.deepcopy(self.pins)
            def need(value,code):
                if not value:raise g.Blocked(code)
            ns=dict(old=lambda:{},receipt=lambda:None,need=need,native=lambda _:None,
                read=lambda _:dict(row_hashes=pins),digest=g.raw_hash,
                P=Path('/synthetic-old-context'),o=SimpleNamespace(graph=lambda *_:dict(active_admins=0)))
            exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
            self.save()
            with self.assertRaisesRegex(g.Blocked,'EXISTING_FIXTURE_MUTATION:enterprise_configs'):ns['graph'](self.data())
            result=g.verify_with_native(self.data(),self.scope,self.proofs,self.pins,native_graph=ns['graph'],status_reader=self.status)
            self.assertEqual(result['status'],'PASS');self.assertEqual(original,source.read_bytes())
    # Run only the supplementary AST case, not inherited cases twice.
    return unittest.TestSuite([OldGuardTests('test_31_old_sealed_guard_rejects_config_new_overlay_passes')])
