"""Full CLI fixture backup -> transport checks -> real private PostgreSQL restore.

All Native records explicitly use the existing FIXTURE_NOT_AUTHORITY lane.
No real grant, Production data, Provider call or Production switch is made.
"""
import hashlib,json,os,shutil,subprocess,sys,tempfile,time,tarfile
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlencode
import pytest
from scripts import native_v2_record_issuer as issuer

PROJECT=Path(__file__).resolve().parents[1]
ENTRY=PROJECT/'scripts/native_v2_execution.py'
PG=Path('/opt/enterprise-agent-workbench-test/shared/runtime/postgresql-16.6/bin')
RID='20261017-3a557a8-stage2-admission-native-v2'
ARCHIVE_SHA='2fff703ddc06ca385e7ffafebe8d788910fc853563c91c8840eef8610e5c3450'
MANIFEST_SHA='38ecf9d236a3f69e9c27340084cb8e0d7686d9b95642027ebc4177203b905846'
def sha(b):return hashlib.sha256(b).hexdigest()
def enc(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=False)+'\n').encode()
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    path.write_bytes(value if isinstance(value,bytes) else enc(value));path.chmod(0o600)
def run(args,**kw):return subprocess.run([str(x) for x in args],capture_output=True,timeout=kw.pop('timeout',90),**kw)

# Exact metadata from the retained d906 read-only ledger / immutable archive.
# Synthetic isolated DB only; never a Production backup or authority.
D906_CRLF_LEDGER={'001': ('workbench_v1.sql', '8872cf35cb070c885fc9fc486e75e49a014ddc2f61a088ceca981a50208cb4d7'), '002': ('user_profiles.sql', 'b2106089644f8fce35fedf0ad220aedbafc9020b70da3ab7d9f4edd215d17b91'), '003': ('agent_templates.sql', '79c9840b4d6d46353b94bb3aed3b09f546fe7fdc15c9bd04acecd475d968fcfd'), '004': ('knowledge_v1.sql', '79c03968ae3f50384cdeb28b8d3b7f17fa435933a00fde8bffc8dc28cbf86a74'), '005': ('skill_registry_v1.sql', '40486c0e59f337de0faaead1d57008026ac81f11c7d354e36143185a6eb89732'), '006': ('embedding_profiles.sql', 'ae1db2a4428aa38762fef98d189f23295fd1241a3d57a6ca756d1c92285cb0e7'), '007': ('history_storage_indexes.sql', 'd051a826539acbd4e39f326bfde982f60bbf78e9944c7895a6e59e9e7e099abb')}

@pytest.mark.parametrize('ledger_case',['canonical_lf','d906_crlf','restored_ledger_drift'])
def test_native_full_cli_backup_transfer_restore_and_failed_claim_boundaries(tmp_path,ledger_case):
    # Provided via the existing pinned Test fixture inventory, not a download.
    artifacts=Path(os.environ['NATIVE_EXECUTION_FIXTURE_ARTIFACT_DIR'])
    context_path=artifacts/'context.json'
    ctx=json.loads(context_path.read_bytes()) if context_path.exists() else {'candidate':{'release_id':RID,'archive_sha256':ARCHIVE_SHA,'raw_manifest_sha256':MANIFEST_SHA}}
    rid=ctx['candidate']['release_id'];archive=artifacts/(rid+'.tar.gz');manifest=artifacts/(rid+'.manifest.json')
    assert sha(archive.read_bytes())==ctx['candidate']['archive_sha256'] and sha(manifest.read_bytes())==ctx['candidate']['raw_manifest_sha256']
    m=json.loads(manifest.read_bytes());base=Path(tempfile.mkdtemp(prefix='native-v2-fixture-',dir=os.environ['TMPDIR']));base.chmod(0o700)
    op=str(uuid4());private=Path(os.environ['TMPDIR'])/('ea-native-v2-'+op);private.mkdir(mode=0o700)
    recipient=private/'recipient';recipient.mkdir(mode=0o700)
    key=recipient/'key.pem';cert=recipient/'recipient.crt'
    generated=run(['/usr/bin/openssl','req','-x509','-newkey','rsa:3072','-nodes','-keyout',key,'-out',cert,'-days','1','-subj','/CN=NATIVE-ISOLATED-FIXTURE-NOT-AUTHORITY'])
    assert generated.returncode==0;key.chmod(0o600);cert.chmod(0o600)
    socket=base/'source-socket';socket.mkdir(mode=0o700)
    assert run([PG/'initdb','-D',base/'pgdata','-U','native_source','-A','trust','--encoding=UTF8','--locale=C']).returncode==0
    assert run([PG/'pg_ctl','-D',base/'pgdata','-l',base/'source-pg.log','-o',"-h '' -k "+str(socket)+' -p 54328 -c shared_buffers=16MB -c max_connections=20','-w','start']).returncode==0
    results=[];started=True
    try:
        assert run([PG/'createdb','-h',socket,'-p','54328','-U','native_source','native_source']).returncode==0
        env={k:os.environ[k] for k in ('PATH','HOME','TMPDIR','LANG','PYTHONDONTWRITEBYTECODE') if k in os.environ}
        env.update(APP_ENV='test',EMBEDDING_PROVIDER='local-hash',ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA='false',
            EMBEDDING_DIMENSION='1536',ENTERPRISE_POC_DATA_DIR=str(base/'initial-runtime-data'),ENTERPRISE_POC_OBJECT_STORAGE_DIR=str(base/'initial-objects'),
            ENTERPRISE_POC_DATABASE_URL='postgresql:///native_source?'+urlencode({'host':str(socket),'port':54328,'user':'native_source'}))
        migrated=run([sys.executable,'-B',PROJECT/'scripts/migrate.py','up'],env=env,cwd=PROJECT)
        assert migrated.returncode==0,migrated.stderr.decode(errors='replace')
        if ledger_case!='canonical_lf':
            statements=[]
            for version,(name,checksum) in D906_CRLF_LEDGER.items():
                raw=(PROJECT/'migrations/postgres'/(version+'_'+name)).read_bytes()
                assert sha(raw.replace(b'\r\n',b'\n').replace(b'\r',b'\n').replace(b'\n',b'\r\n'))==checksum
                statements.append(f"UPDATE schema_migrations SET checksum='{checksum}' WHERE version='{version}' AND name='{name}';")
            updated=run([PG/'psql','-X','-h',socket,'-p','54328','-U','native_source','-d','native_source','-v','ON_ERROR_STOP=1','-c',''.join(statements)])
            assert updated.returncode==0,updated.stderr.decode(errors='replace')
        sql="INSERT INTO tenants(id,name,poc_api_key) VALUES ('native-isolated-fixture','SYNTHETIC-NO-CUSTOMER','INVALID-FIXTURE');"
        seeded=run([PG/'psql','-X','-h',socket,'-p','54328','-U','native_source','-d','native_source','-v','ON_ERROR_STOP=1','-c',sql])
        assert seeded.returncode==0,seeded.stderr.decode(errors='replace')
        # Complete the existing tenant bootstrap before taking a snapshot,
        # as for an already running predecessor. Do not ignore startup writes.
        bootstrap=run([sys.executable,'-B','-c','from app.main import store,product_store,skill_registry;store.initialize();product_store.initialize();skill_registry.initialize()'],env=env,cwd=PROJECT)
        assert bootstrap.returncode==0,bootstrap.stderr.decode(errors='replace')
        for directory in ('shared/release-state/native-v2-manual-issuance','shared/release-qualifications','releases','backups'):(base/directory).mkdir(mode=0o700,parents=True)
        save(base/'release.lock',b'')
        prior=base/'releases'/issuer.PRIOR['release_id']/'enterprise_agent_poc';prior.mkdir(parents=True)
        for suffix,k in (('.tar.gz','archive_sha256'),('.manifest.json','raw_manifest_sha256')):
            data=(artifacts/(issuer.PRIOR['release_id']+suffix)).read_bytes();assert sha(data)==issuer.PRIOR[k]
            save(prior.parent/(issuer.PRIOR['release_id']+suffix),data)
        with tarfile.open(prior.parent/(issuer.PRIOR['release_id']+'.tar.gz')) as tf:tf.extractall(prior.parent,filter='data')
        (base/'release-current').symlink_to(prior)
        c={'release_id':rid,'source_commit':m['source_commit'],'source_tree':m['runtime_only_release']['successor']['source_tree'],
           'parent_source_commit':m['runtime_only_release']['successor']['parent_source_commit'],'archive_sha256':ctx['candidate']['archive_sha256'],'raw_manifest_sha256':ctx['candidate']['raw_manifest_sha256'],
           'manifest_sha256':sha(json.dumps(m,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())}
        p={'kind':'B38_NATIVE_V2_MANUAL_ISSUANCE_V1','fixture':True,'business_base_source':issuer.B38,'candidate':c,'predecessor':issuer.PRIOR,
           'report_paths':{x:str(base/'shared/release-evidence'/rid/(x+'.json')) for x in issuer.CHECKS},
           'binding_sha256':sha(json.dumps(m['runtime_only_release'],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()),
           'schema_fingerprint':'7'*64,'issuer_sha256':sha((PROJECT/'scripts/native_v2_record_issuer.py').read_bytes()),'principal':'Lucky',
           'owner_seconds':86400,'root_seconds':3600,'maintenance_seconds':14400,'recovery_seconds':3600,'remaining_work_seconds':120,
           'review_text':'EXPLICIT NATIVE EXECUTION FIXTURE. NO REAL AUTHORITY. NO PRODUCTION DATA.',
           'fees':{'automatic_paid_retries':0,'real_provider_requests':0},
           'native_execution':{'profile':'BATCH1_NATIVE_V2_EXECUTION_V1','helper_sha256':sha(ENTRY.read_bytes()),'recovery_helper_sha256':sha((PROJECT/'scripts/native_v2_recovery_probe.py').read_bytes()),'operation_id':op,'private_root':str(private),
             'production_switch':False,'production_database_write':False,'provider_calls':0,'registry_packages':True,'recipient_certificate':str(cert),'recipient_sha256':sha(cert.read_bytes()),
             'backup_pg_bin':str(PG),'restore_pg_bin':str(PG),'fixture_source_socket':str(socket)}}
        audit=base/'shared/release-state/native-v2-manual-issuance'/rid;audit.mkdir(mode=0o700)
        save(audit/'request.json',p);pin=sha((audit/'request.json').read_bytes());now=int(time.time())
        def decision(role):
            original=enc({'role':role,'request':p,'confirmation':'FIXTURE-'+role.upper()+' '+pin+' '+'0'*64}).decode()
            return dict(kind=issuer.FIXTURE,role=role,principal='Lucky',request_sha256=pin,issued_at=now,expires_at=now+(86400 if role=='owner' else 3600),
                original=original,original_sha256=sha(original.encode()),uid=os.getuid(),pid=os.getpid(),tty='/dev/FIXTURE-NOT-AUTHORITY',host=os.uname().nodename)
        owner=decision('owner');save(audit/'owner.json',owner)
        root=decision('root');root.update(owner_sha256=sha((audit/'owner.json').read_bytes()),maintenance_expires_at=now+14400);save(audit/'root.json',root)
        def cli(action,*extra,ok=True):
            args=[sys.executable,'-I','-B','-S',ENTRY,action,'--authority',audit,'--request-sha256',pin,'--fixture-root',base,*extra]
            r=run(args,timeout=180);out=json.loads(r.stdout);results.append({'action':action,'exit_code':r.returncode,'result':out})
            assert (r.returncode==0)==ok,(out,r.stderr.decode(errors='replace'))
            return out
        # Explicit synthetic decisions only: prove rejection before any claim.
        save(audit/'root.json',{**root,'expires_at':now-1})
        assert cli('status',ok=False)['first_failure_point']=='NATIVE_ROLE_SOURCE_TIME:root'
        save(audit/'root.json',{**root,'maintenance_expires_at':now+3599})
        assert cli('status',ok=False)['first_failure_point']=='NATIVE_TIME_AND_RECOVERY_RESERVE'
        save(audit/'root.json',root)
        original_request=(audit/'request.json').read_bytes()
        save(audit/'request.json',original_request+b' ')
        assert cli('status',ok=False)['first_failure_point']=='EXACT_NATIVE_REQUEST_HASH'
        save(audit/'request.json',original_request)
        assert not (base/'backups'/('native-v2-'+op)).exists() and not (private/'restore').exists()
        cli('install','--archive',archive,'--manifest',manifest)
        cli('install','--archive',archive,'--manifest',manifest,ok=False)
        cli('backup');cli('backup',ok=False)
        context=cli('transfer-context');context_file=private/'transfer.json';save(context_file,context);context_pin=sha(context_file.read_bytes())
        incoming=private/'incoming';incoming.mkdir(mode=0o700)
        # Interrupted/missing receipt: even an exit0 sender cannot create claim.
        cli('restore','--context',context_file,'--context-sha256',context_pin,ok=False)
        assert not (private/'restore').exists()
        backup=base/'backups'/('native-v2-'+op)
        for name in context['files']:save(incoming/name,(backup/name).read_bytes())
        cipher=incoming/'database.dump.cms';good=cipher.read_bytes();save(cipher,good+b'TAMPER')
        cli('verify-transfer','--context',context_file,'--context-sha256',context_pin,ok=False)
        assert not (private/'restore').exists();save(cipher,good)
        cli('restore','--context',context_file,'--context-sha256',context_pin,ok=False) # no receiver completion
        assert not (private/'restore').exists()
        # Re-seal only explicit fixture transfer metadata for each negative.
        # No real receipt, Claim or original backup is changed or removed.
        original_summary=(incoming/'snapshot.readonly.summary.json').read_bytes()
        original_receipt=(incoming/'backup.receipt.json').read_bytes()
        def reseal_fixture(summary):
            nonlocal context,context_pin
            save(incoming/'snapshot.readonly.summary.json',summary)
            receipt=json.loads(original_receipt);receipt['summary_sha256']=sha((incoming/'snapshot.readonly.summary.json').read_bytes())
            save(incoming/'backup.receipt.json',receipt)
            for name in ('snapshot.readonly.summary.json','backup.receipt.json'):
                data=(incoming/name).read_bytes();context['files'][name]={'bytes':len(data),'sha256':sha(data)}
            save(context_file,context);context_pin=sha(context_file.read_bytes())
            save(private/'transfer.complete.json',{'status':'TRANSFER_COMPLETE_BYTES_AND_RECEIPTS_VERIFIED','fixture':True,
                'context_sha256':context_pin,'request_sha256':pin,'root_sha256':sha((audit/'root.json').read_bytes()),'operation_id':op})
        for field,value in (('checksum','f'*64),('version','999'),('name','wrong.sql')):
            summary=json.loads(original_summary);summary['ledger'][0][field]=value;reseal_fixture(summary)
            rejected=cli('restore','--context',context_file,'--context-sha256',context_pin,ok=False)
            assert rejected['first_failure_point'] in ('EXACT_PAIR_RESTORED_LEDGER_SOURCE','SYSTEM_OR_INPUT_FAILURE')
            assert not (private/'restore').exists()
        reseal_fixture(json.loads(original_summary))
        # Both exact immutable slots reject changed installed migration bytes.
        for identity in (c,issuer.PRIOR):
            migration=base/'releases'/identity['release_id']/'enterprise_agent_poc/migrations/postgres/001_workbench_v1.sql'
            original=migration.read_bytes();save(migration,original+b'\n-- TAMPER\n')
            rejected=cli('restore','--context',context_file,'--context-sha256',context_pin,ok=False)
            assert rejected['first_failure_point']=='INSTALLED_BYTES' and not (private/'restore').exists()
            save(migration,original)
        if ledger_case=='restored_ledger_drift':
            summary=json.loads(original_summary)
            summary['ledger'][0]['checksum']=sha((PROJECT/'migrations/postgres/001_workbench_v1.sql').read_bytes())
            reseal_fixture(summary)
            rejected=cli('restore','--context',context_file,'--context-sha256',context_pin,ok=False)
            assert rejected['first_failure_point']=='RESTORED_SCHEMA_LEDGER_DRIFT'
            assert (private/'restore/restore.claim.json').exists() and (private/'restore/restore.failed.json').exists()
            failed=(private/'restore/restore.failed.json').read_bytes()
            assert cli('restore','--context',context_file,'--context-sha256',context_pin,ok=False)['first_failure_point']=='RESTORE_CLAIM_ALREADY_ATTEMPTED'
            assert (private/'restore/restore.failed.json').read_bytes()==failed
            (tmp_path/'NATIVE_ENTRY_AUDIT.json').write_text(json.dumps({'fixture':True,'ledger_case':ledger_case,'base':str(base),'private_root':str(private),'entry_results':results,'real_provider_calls':0,'production_changes':0},indent=2))
            return
        cli('verify-transfer','--context',context_file,'--context-sha256',context_pin)
        completed=cli('restore','--context',context_file,'--context-sha256',context_pin)
        assert completed['data_preserved'] is True and completed['fixture'] is True
        if ledger_case=='d906_crlf' and os.environ.get('NATIVE_RECOVERY_HANDSHAKE_DIR'):
            hand=Path(os.environ['NATIVE_RECOVERY_HANDSHAKE_DIR'])
            save(hand/'native-fixture-ready.json',{'base':str(base),'private':str(private),'authority':str(audit),'request_sha256':pin,'fixture':True})
            deadline=time.monotonic()+230
            while not (hand/'native-fixture-done.json').exists():
                assert time.monotonic()<deadline,'Native fixture controller did not close';time.sleep(.2)
            result=json.loads((hand/'native-fixture-done.json').read_bytes());assert result['exit_code']==0,result
        cli('restore','--context',context_file,'--context-sha256',context_pin,ok=False)
        # A consumed failed claim is never cleared or retried.
        save(private/'restore/restore.failed.json',{'kind':'EXPLICIT FAILED-CLAIM NEGATIVE FIXTURE'})
        cli('restore','--context',context_file,'--context-sha256',context_pin,ok=False)
        save(audit/'revoked.json',{'fixture':True});cli('status',ok=False)
        (tmp_path/'NATIVE_ENTRY_AUDIT.json').write_text(json.dumps({'fixture':True,'ledger_case':ledger_case,'base':str(base),'private_root':str(private),'entry_results':results,'real_provider_calls':0,'production_changes':0},indent=2))
    finally:
        if started:assert run([PG/'pg_ctl','-D',base/'pgdata','-m','fast','-w','stop']).returncode==0
