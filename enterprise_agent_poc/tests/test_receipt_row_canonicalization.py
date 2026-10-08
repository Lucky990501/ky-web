"""V2 semantic identity tests: no provider, live DB, secrets or existing writes."""
import datetime as dt
import json
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import receipt_row_canonicalization as c
from test_wechat_minimal_provision import MinimalProvisionTests, scope, p

class CanonicalTests(unittest.TestCase):
    def setUp(self):
        self.r={'id':'synthetic','name':'测试','poc_api_key':None,
                'created_at':dt.datetime(2026,1,2,11,4,5,123456,tzinfo=dt.timezone(dt.timedelta(hours=8)))}
    def test_datetime_guard_match(self):
        self.assertEqual(c.row_sha256('tenants',self.r),c.row_sha256('tenants',dict(self.r,created_at='2026-01-02T11:04:05.123456+08:00')))
    def test_read_order_match(self):
        self.assertEqual(c.row_bytes('tenants',self.r),c.row_bytes('tenants',dict(reversed(list(self.r.items())))))
    def test_json_order_match(self):
        a={'tenant_id':'synthetic','updated_at':self.r['created_at'],'payload':'{"z":1,"a":{"b":null,"a":2}}'}
        self.assertEqual(c.row_sha256('enterprise_configs',a),c.row_sha256('enterprise_configs',dict(a,payload='{"a":{"a":2,"b":null},"z":1}')))
    def test_timestamp_change_mismatch(self):
        self.assertNotEqual(c.row_sha256('tenants',self.r),c.row_sha256('tenants',dict(self.r,created_at=self.r['created_at']+dt.timedelta(microseconds=1))))
    def test_uuid_null(self):
        value=uuid.UUID('2102295e-9a76-596c-9f92-0e14f4e63de6')
        self.assertEqual(c.encode(c.json_value({'id':value,'null':None})),b'{"id":"2102295e-9a76-596c-9f92-0e14f4e63de6","null":null}')
    def test_multiroot_order_match(self):
        other=dict(self.r,id='second')
        self.assertEqual(c.rows_sha256('tenants',[self.r,other]),c.rows_sha256('tenants',[other,self.r]))
    def test_content_change_mismatch(self):
        self.assertNotEqual(c.row_sha256('tenants',self.r),c.row_sha256('tenants',dict(self.r,name='changed')))
    def test_duplicate_retained(self):
        self.assertNotEqual(c.rows_sha256('tenants',[self.r]),c.rows_sha256('tenants',[self.r,self.r]))
    def test_naive_reject(self):
        with self.assertRaisesRegex(ValueError,'NAIVE'):c.row_bytes('tenants',dict(self.r,created_at=dt.datetime(2026,1,2)))
    def test_unknown_field_reject(self):
        with self.assertRaisesRegex(ValueError,'FIELD'):c.row_bytes('tenants',dict(self.r,extra='x'))
    def test_wrong_version_reject(self):
        with self.assertRaisesRegex(ValueError,'VERSION'):c.row_bytes('tenants',self.r,'V1')
    def test_precision_and_timezone(self):
        self.assertEqual(c.timestamptz('2026-01-02T11:04:05+08:00'),'2026-01-02T03:04:05.000000Z')

class V2SafetyTests(MinimalProvisionTests):
    def test_old_receipt_preserved(self):
        old=self.root/'receipt.v1.json';old.write_bytes(b'v1 immutable evidence')
        self.provision()
        self.assertEqual(old.read_bytes(),b'v1 immutable evidence')
        before=p.RECEIPT_FILE.read_bytes()
        with self.assertRaises(p.MinimalProvisionBlocked):self.provision()
        self.assertEqual(before,p.RECEIPT_FILE.read_bytes())
    def test_receipt_version_reject(self):
        self.provision();value=json.loads(p.RECEIPT_FILE.read_bytes());value['receipt_version']='V1'
        p.RECEIPT_FILE.chmod(0o600);p.RECEIPT_FILE.write_bytes(p.canonical(value))
        with self.approve(),self.assertRaisesRegex(p.MinimalProvisionBlocked,'VERSION'):p.cleanup_minimal(self.store,self.scope,execute=True)
    def test_wrong_environment_reject(self):
        with self.assertRaises(p.MinimalProvisionBlocked):p.validate_scope(dict(self.scope,environment='production'),'test','postgresql://enterprise_agent_test@127.0.0.1:55432/enterprise_agent_test',{'source':'a'*40,'tree':'b'*40})
    def test_wrong_version_scope_reject(self):
        with self.assertRaises(p.MinimalProvisionBlocked):p.validate_scope(dict(self.scope,receipt_version='V1'),'test','postgresql://enterprise_agent_test@127.0.0.1:55432/enterprise_agent_test',{'source':'a'*40,'tree':'b'*40})
    def test_timestamp_drift_blocks_cleanup(self):
        self.provision();self.store.db.execute("UPDATE enterprise_configs SET updated_at='2026-01-02T03:04:06+00:00' WHERE tenant_id=?",(p.MINIMAL_TENANT,));self.store.db.commit()
        with self.approve(),self.assertRaisesRegex(p.MinimalProvisionBlocked,'OWNERSHIP'):p.cleanup_minimal(self.store,self.scope,execute=True)
    def test_replay_after_cleanup_reject(self):
        self.provision()
        with self.approve():p.cleanup_minimal(self.store,self.scope,execute=True)
        with self.assertRaisesRegex(p.MinimalProvisionBlocked,'ARTIFACT'):self.provision()

del MinimalProvisionTests  # The inherited suite runs once here, not twice.
if __name__=='__main__': unittest.main()
