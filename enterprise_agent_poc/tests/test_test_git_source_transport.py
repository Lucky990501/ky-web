"""Deterministic fail-closed transport tests; no live Source/DB/service writes."""
import copy,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import test_git_source_transport as t

class TransportTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'APP_ENV':'test'});self.env.start();self.addCleanup(self.env.stop)
        self.files={'HEAD':'a'*64,'config':'b'*64}
        self.seal={'contract':'TEST_ONLY_GIT_SOURCE_TRANSPORT_V1','environment':'test',
            'production_deploy_authority':False,'source':t.SOURCE,'tree':t.TREE,'parent':t.PARENT,
            'base':t.BASE,'bundle_sha256':'c'*64,'source_files':self.files}
    def test_exact_request(self):t.fixed_request(t.REPO,t.SOURCE,t.TREE)
    def test_wrong_source(self):
        with self.assertRaisesRegex(t.SourceTransportBlocked,'WRONG_SOURCE'):t.fixed_request(t.REPO,'a'*40,t.TREE)
    def test_wrong_tree(self):
        with self.assertRaisesRegex(t.SourceTransportBlocked,'WRONG_TREE'):t.fixed_request(t.REPO,t.SOURCE,'a'*40)
    def test_foreign_repo(self):
        with self.assertRaisesRegex(t.SourceTransportBlocked,'UNAUTHORIZED'):t.fixed_request(Path('/tmp/foreign'),t.SOURCE,t.TREE)
    def test_production(self):
        with patch.dict(os.environ,{'APP_ENV':'production'}),self.assertRaisesRegex(t.SourceTransportBlocked,'PRODUCTION'):
            t.fixed_request(t.REPO,t.SOURCE,t.TREE)
    def test_sealed_payload(self):t.validate_seal(self.seal,'c'*64,self.files)
    def test_bundle_tamper(self):
        with self.assertRaisesRegex(t.SourceTransportBlocked,'BUNDLE_TAMPERED'):t.validate_seal(self.seal,'d'*64,self.files)
    def test_staging_tamper(self):
        with self.assertRaisesRegex(t.SourceTransportBlocked,'STAGING_TAMPERED'):t.validate_seal(self.seal,'c'*64,dict(self.files,config='e'*64))
    def test_staging_extra_file(self):
        with self.assertRaisesRegex(t.SourceTransportBlocked,'STAGING_TAMPERED'):t.validate_seal(self.seal,'c'*64,dict(self.files,extra='e'*64))
    def test_staging_missing_file(self):
        with self.assertRaisesRegex(t.SourceTransportBlocked,'STAGING_TAMPERED'):t.validate_seal(self.seal,'c'*64,{'HEAD':'a'*64})
    def test_sealed_source(self):
        with self.assertRaisesRegex(t.SourceTransportBlocked,'SEALED_SOURCE_DRIFT'):t.validate_seal(dict(self.seal,source='e'*40),'c'*64,self.files)
    def test_sealed_tree(self):
        with self.assertRaisesRegex(t.SourceTransportBlocked,'SEALED_SOURCE_DRIFT'):t.validate_seal(dict(self.seal,tree='e'*40),'c'*64,self.files)
    def test_sealed_parent(self):
        with self.assertRaisesRegex(t.SourceTransportBlocked,'SEALED_SOURCE_DRIFT'):t.validate_seal(dict(self.seal,parent='e'*40),'c'*64,self.files)
    def test_sealed_authority(self):
        with self.assertRaisesRegex(t.SourceTransportBlocked,'TRANSPORT_SEAL_REJECTED'):t.validate_seal(dict(self.seal,production_deploy_authority=True),'c'*64,self.files)
    def test_sealed_environment(self):
        with self.assertRaisesRegex(t.SourceTransportBlocked,'TRANSPORT_SEAL_REJECTED'):t.validate_seal(dict(self.seal,environment='production'),'c'*64,self.files)
    def test_mutable_module_rejected(self):
        with patch.object(os,'geteuid',return_value=0,create=True),self.assertRaisesRegex(t.SourceTransportBlocked,'MUTABLE_TOOLING'):
            t.verify()
    def test_non_root_rejected(self):
        with patch.object(os,'geteuid',return_value=1000,create=True),self.assertRaisesRegex(t.SourceTransportBlocked,'ROOT_VERIFICATION'):
            t.verify()

if __name__=='__main__':unittest.main()
