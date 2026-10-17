"""Bounded Native V2 execution connector, not an issuer or release operator.

Snapshot/pg_dump/OpenSSL CMS and stream-to-pg_restore are retained from the
historical BACKUP_EXECUTION/RESTORE_EXECUTION tools. No old policy is consumed.
Real private execution requires unchanged Native decisions exported by the
authorized operator and a fresh Root-side status. Fixture output stays fixture.
"""
from __future__ import annotations
import argparse,datetime,fcntl,hashlib,importlib.util,io,json,os,re,select,socket,stat,subprocess,sys,tarfile,time,zipfile
from pathlib import Path,PurePosixPath

D906='d9063ee43bc65e821457221ad824f5dde84fe408'
D906_TREE='3a612584d278925afbcd1e2004fd47016fc9b6ce'
PROD=Path('/opt/enterprise-agent-workbench')
PROD_HOST='iv-yekqs6ez9cnu262hhsg0'
TEST_HOST='iv-yew0uhfksgbw80bm48mv'
PROFILE='BATCH1_NATIVE_V2_EXECUTION_V1'
MAX_BYTES=512*1024*1024
ASSERTIONS=0

class Blocked(ValueError):pass
def need(ok,code):
    global ASSERTIONS
    if not ok:raise Blocked(code)
    ASSERTIONS+=1
def sha(raw):return hashlib.sha256(raw).hexdigest()
def enc(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=False)+'\n').encode()
def parse(raw):
    def pairs(rows):
        v={}
        for k,x in rows:need(k not in v,'DUPLICATE_JSON_KEY');v[k]=x
        return v
    return json.loads(raw,object_pairs_hook=pairs)
def put(path,value,mode=0o600):
    raw=value if isinstance(value,bytes) else enc(value)
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode),'wb') as f:
        f.write(raw);f.flush();os.fsync(f.fileno())
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)
    need(path.read_bytes()==raw,'DURABLE_READBACK')
    return sha(raw)
def read(path,uid=None,limit=MAX_BYTES,mode=None):
    need(path.is_absolute() and path.parent.resolve(strict=True)==path.parent,'CANONICAL_PARENT')
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as f:
        s=os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_size<=limit
             and (uid is None or s.st_uid==uid) and not s.st_mode&0o022
             and (mode is None or stat.S_IMODE(s.st_mode)==mode),'PROTECTED_INPUT')
        raw=f.read(limit+1);end=os.fstat(f.fileno())
    after=path.lstat()
    need((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)==(end.st_dev,end.st_ino,end.st_size,end.st_mtime_ns)
         ==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'INPUT_READ_RACE')
    return raw
def command(args,timeout=30,**kw):
    p=subprocess.run([str(x) for x in args],capture_output=True,timeout=timeout,**kw)
    need(p.returncode==0,'SYSTEM_TOOL_FAILED:'+Path(str(args[0])).name)
    return p.stdout

class Authority:
    """Reader of the existing Native issuer archive. Never issues a decision."""
    def __init__(self,directory,pin,fixture_root=None):
        self.directory=Path(directory);self.fixture=fixture_root is not None
        self.uid=os.getuid() if self.fixture else 0
        self.base=Path(fixture_root) if self.fixture else PROD
        if self.fixture:
            need(os.uname().nodename!=PROD_HOST and self.base.is_relative_to(Path(os.environ.get('TMPDIR','/tmp')))
                 and self.base.name.startswith('native-v2-fixture-') and self.base.stat().st_uid==os.getuid(), 'FIXTURE_ONLY_CONTEXT')
        else:
            for p in (self.directory,*self.directory.parents):
                s=p.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022,'ROOT_TRUSTED_ARCHIVE')
        raw=self._record('request.json');need(sha(raw)==pin,'EXACT_NATIVE_REQUEST_HASH')
        self.p=parse(raw);self.pin=pin;self.c=self.p['candidate'];self.x=self.p['native_execution']
        need(self.p['kind']=='B38_NATIVE_V2_MANUAL_ISSUANCE_V1' and self.p['fixture'] is self.fixture,'NATIVE_REQUEST_KIND')
        need(self.p['predecessor']['source_commit']==D906 and self.p['predecessor']['source_tree']==D906_TREE,'EXACT_D906_PREDECESSOR')
        need(self.x['profile']==PROFILE and self.x['helper_sha256']==sha(Path(__file__).read_bytes()),'EXACT_EXECUTION_HELPER')
        need(re.fullmatch('[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',self.x['operation_id'])
             and self.x['operation_id'] not in {'b5ec498a-32c0-4c20-8939-3377a93ccc7b'},'FRESH_NATIVE_OPERATION')
        need(self.x['production_switch'] is False and self.x['production_database_write'] is False
             and self.x['provider_calls']==0,'QUALIFICATION_ONLY_SCOPE')
        need(re.fullmatch('[A-Za-z0-9._-]{1,128}',self.c['release_id'])
             and all(re.fullmatch('[0-9a-f]{40}',self.c[k]) for k in ('source_commit','source_tree','parent_source_commit')),'CANDIDATE_IDENTITY')
        self.private=Path(self.x['private_root'])
        need(self.private.parent==(Path(os.environ.get('TMPDIR','/tmp')) if self.fixture else Path('/tmp'))
             and self.private.name=='ea-native-v2-'+self.x['operation_id'],'EXACT_PRIVATE_ROOT')
        spec=importlib.util.spec_from_file_location('unchanged_native_issuer',Path(__file__).with_name('native_v2_record_issuer.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        need(self.p['issuer_sha256']==sha(Path(module.__file__).read_bytes()) and self.p['predecessor']==module.PRIOR,'UNCHANGED_ISSUER_AND_EXACT_D906_PINS')
        self.issuer=None
        if self.fixture or os.uname().nodename==PROD_HOST:
            self.issuer=module.Issuer(str(self.directory/'request.json'),pin,str(self.base) if self.fixture else None)
        self.check()
    def _record(self,name):
        mode=0o600 if self.fixture or os.uname().nodename==PROD_HOST else 0o444
        return read(self.directory/name,self.uid,262144,mode)
    def check(self,seconds=0):
        need(not os.path.lexists(self.directory/'revoked.json'),'NATIVE_AUTHORITY_REVOKED')
        need(sha(self._record('request.json'))==self.pin,'REQUEST_DRIFT')
        now=int(time.time());records={}
        for role in ('owner','root'):
            raw=self._record(role+'.json');v=parse(raw)
            need(v['kind']==('FIXTURE_NOT_AUTHORITY' if self.fixture else 'ACTUAL_NATIVE_V2_HUMAN_DECISION')
                 and v['role']==role and v['principal']=='Lucky' and v['request_sha256']==self.pin
                 and v['issued_at']<=now and now+seconds<v['expires_at']
                 and v['original_sha256']==sha(v['original'].encode()),'NATIVE_ROLE_SOURCE_TIME:'+role)
            original=parse(v['original']);prefix='FIXTURE-' if self.fixture else ''
            need(original['role']==role and original['request']==self.p
                 and original['confirmation'].startswith(prefix+role.upper()+' '+self.pin+' ')
                 and v['uid']==self.uid and v['tty'].startswith('/dev/') and v['pid']>0,'NATIVE_ORIGINAL:'+role)
            if not self.fixture:need(v['host']==PROD_HOST,'NATIVE_SOURCE_HOST')
            records[role]=(v,sha(raw))
        owner,root=records['owner'][0],records['root'][0]
        need(root['owner_sha256']==records['owner'][1]
             and root['expires_at']<=root['issued_at']+3600
             and owner['expires_at']<=owner['issued_at']+86400
             and now+seconds+3600<root['maintenance_expires_at']<=min(root['issued_at']+14400,owner['expires_at']), 'NATIVE_TIME_AND_RECOVERY_RESERVE')
        self.root_sha=records['root'][1];self.owner_sha=records['owner'][1]
        if self.issuer:
            for role in ('owner','root'):
                _,raw=self.issuer.valid(role);need(sha(raw)==records[role][1],'FROZEN_NATIVE_ISSUER_READBACK')
        if not self.fixture and os.uname().nodename!=PROD_HOST:
            s=parse(self._record('live-status.json'))
            need(s['kind']=='NATIVE_V2_LIVE_STATUS_NOT_AUTHORITY' and s['request_sha256']==self.pin
                 and s['root_sha256']==self.root_sha and s['operation_id']==self.x['operation_id']
                 and s['source_commit']==self.c['source_commit'] and s['unrevoked'] is True and s['issuer_verified'] is True
                 and s['observed_at']<=now<s['observed_at']+60,'FRESH_ROOT_SIDE_NATIVE_STATUS_REQUIRED')
        return records
    def production(self):
        need(self.fixture or (os.uname().nodename==PROD_HOST and os.getuid()==os.getgid()==0),'PRODUCTION_ROOT_REQUIRED')
    def isolated(self):
        need(os.uname().nodename==TEST_HOST and os.getuid()==1000
             and 'enterprise-agent-test.slice' in Path('/proc/self/cgroup').read_text()
             and {n for _,n in socket.if_nameindex()}=={'lo'},'ISOLATED_TEST_SLICE_ONLY')

def artifact(auth,archive,manifest,destination,install=False,prior=False):
    ident=auth.p['predecessor'] if prior else auth.c
    raw=read(Path(manifest));m=parse(raw);data=read(Path(archive))
    need(sha(raw)==ident['raw_manifest_sha256'] and sha(data)==ident['archive_sha256']==m['archive_sha256']
         and m['source_commit']==ident['source_commit'] and m['release_id']==ident['release_id'],'EXACT_ARTIFACT_BYTES')
    canonical=sha(json.dumps(m,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
    need(canonical==ident['manifest_sha256'],'EXACT_CANONICAL_MANIFEST')
    if not prior:
        b=m['runtime_only_release'];need(b['schema_version']==2 and b['successor']['source_tree']==ident['source_tree']
          and b['successor']['parent_source_commit']==ident['parent_source_commit']
          and b['exact_predecessor']['source_commit']==D906
          and sha(json.dumps(b,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())==auth.p['binding_sha256'],'NATIVE_V2_BINDING')
    if install:
        need(not os.path.lexists(destination),'ONE_TIME_INSTALL_TARGET_EXISTS')
        destination.mkdir(mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        files={};seen=set()
        for item in tf.getmembers():
            p=PurePosixPath(item.name)
            need(not p.is_absolute() and '..' not in p.parts and '\\' not in item.name and p.parts[0]=='enterprise_agent_poc'
                 and item.name not in seen and (item.isdir() or item.isfile()),'ARCHIVE_PATH_OR_TYPE')
            seen.add(item.name)
            if not item.isfile():continue
            need(item.size<=8*1024*1024,'ARCHIVE_MEMBER_SIZE');content=tf.extractfile(item).read();target=destination/Path(*p.parts)
            if install:
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True);put(target,content,0o600|(item.mode&0o111))
            need(read(target)==content,'INSTALLED_BYTES');files[item.name]=sha(content)
    need(set(files)==set(m['selected_files']),'EXACT_SELECTED_FILES')
    if not prior:
        expected={'deploy/release_switch.sh','scripts/release_manifest.py','scripts/release_runtime_recovery.py','scripts/release_verify.py',
                  'scripts/rollback_preflight.py','scripts/schema015_successor_binding.py','scripts/schema_015_runtime_only.py'}
        need(set(m['runtime_only_release']['successor']['tooling_pins'])==expected,'COMPLETE_FROZEN_CONSUMER_PINS')
        for name,pin in m['runtime_only_release']['successor']['tooling_pins'].items():
            need(files['enterprise_agent_poc/'+name]==pin==sha((Path(__file__).resolve().parents[1]/name).read_bytes()),'UNCHANGED_CONSUMER_PIN')
        sys.path.insert(0,str(destination/'enterprise_agent_poc'))
        from scripts import schema015_successor_binding as binding
        binding.declaration(destination/'enterprise_agent_poc',m)
    return m,files

class Snapshot:
    # Retains the existing pg_export_snapshot + read-only psql protocol.
    def __init__(self,args,log):
        self.p=subprocess.Popen([str(x) for x in args],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,
            text=True,bufsize=1,env={'PATH':'/usr/bin:/bin','LC_ALL':'C','PGOPTIONS':'-c default_transaction_read_only=on -c statement_timeout=300000'})
    def one(self,sql):
        need(self.p.poll() is None,'READONLY_SESSION_EXIT')
        self.p.stdin.write(sql+'\n');self.p.stdin.flush()
        need(select.select([self.p.stdout],[],[],30)[0],'READONLY_QUERY_TIMEOUT')
        line=self.p.stdout.readline().strip();need(bool(line),'READONLY_QUERY_FAILED');return line
    def close(self):
        if self.p.poll() is None:
            try:self.p.stdin.write('ROLLBACK;\n');self.p.stdin.close();self.p.wait(timeout=10)
            except BaseException:self.p.kill();self.p.wait(timeout=10)
def aggregates(s):
    names=json.loads(s.one("SELECT coalesce(json_agg(tablename ORDER BY tablename),'[]'::json) FROM pg_tables WHERE schemaname='public';"))
    rows=[]
    for name in names:
        need(re.fullmatch('[a-z][a-z0-9_]*',name),'TABLE_IDENTIFIER')
        rows.append(json.loads(s.one("SELECT json_build_object('table','"+name+"','rows',count(*),'row_digest_md5',md5(coalesce(string_agg(row_to_json(r)::text,E'\\n' ORDER BY row_to_json(r)::text COLLATE \"C\"),''))) FROM public.\""+name+'\" r;')))
    return rows
def wait_pipeline(processes,auth,seconds,cipher=None):
    deadline=time.monotonic()+seconds
    try:
        while any(p.poll() is None for p in processes):
            auth.check();need(time.monotonic()<deadline,'PIPELINE_TIMEOUT_NO_RETRY')
            if cipher:need(cipher.stat().st_size<MAX_BYTES,'CIPHERTEXT_LIMIT')
            time.sleep(.1)
        need(all(p.returncode==0 for p in processes),'PIPELINE_EXIT_OR_EOF_FAILURE')
    finally:
        for p in processes:
            if p.poll() is None:p.kill();p.wait(timeout=10)
def pg(auth,role):
    root=Path(auth.x['restore_pg_bin' if role=='restore' else 'backup_pg_bin'])
    expected=Path('/opt/enterprise-agent-workbench-test/shared/runtime/postgresql-16.6/bin') if auth.fixture or role=='restore' else Path('/usr/lib/postgresql/16/bin')
    need(root==expected,'PG_TOOL_PATH')
    version=command([root/'pg_dump','--version']).decode()
    need(('16.6' if auth.fixture or role=='restore' else '16.14') in version,'PG_TOOL_VERSION')
    return root
def psql_args(auth,root,restore=False):
    if restore:
        return [root/'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-h',restore_host(auth),'-p','25432','-U','native_restore','-d','native_restored']
    if auth.fixture:
        socket=Path(auth.x['fixture_source_socket']);need(socket.is_relative_to(auth.base),'FIXTURE_PG_SCOPE')
        return [root/'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-h',socket,'-p','54328','-U','native_source','-d','native_source']
    return ['/usr/sbin/runuser','-u','postgres','--',root/'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-d','enterprise_agent']
def restore_host(auth):
    # Cross-version psycopg and libpq support this private-netns loopback
    # endpoint; no host/PRIMARY socket, DNS lookup or public interface is used.
    return '127.0.0.1'
def backup(auth):
    auth.production();auth.check(360);root=pg(auth,'backup')
    target=auth.base/'backups'/('native-v2-'+auth.x['operation_id'])
    cert=Path(auth.x['recipient_certificate']);need(sha(read(cert))==auth.x['recipient_sha256'],'RECIPIENT_PIN')
    need('Public-Key: (3072 bit)' in command(['/usr/bin/openssl','x509','-in',cert,'-noout','-text']).decode(),'RSA3072_RECIPIENT_REQUIRED')
    need(not os.path.lexists(target),'BACKUP_ALREADY_ATTEMPTED')
    expected=auth.base/'releases'/auth.p['predecessor']['release_id']/'enterprise_agent_poc'
    need((auth.base/'release-current').resolve()==expected,'CURRENT_EXACT_D906')
    if not auth.fixture:
        prior_manifest=expected.parent/(auth.p['predecessor']['release_id']+'.manifest.json')
        need(sha(read(prior_manifest))==auth.p['predecessor']['raw_manifest_sha256'],'EXACT_LIVE_D906_MANIFEST')
    if not auth.fixture:
        for unit in ('enterprise-agent-api','enterprise-agent-mcp','enterprise-agent-worker'):
            info=dict(x.split('=',1) for x in command(['systemctl','show',unit,'-p','MainPID','-p','ActiveState','-p','SubState']).decode().splitlines())
            need(info['ActiveState']=='active' and info['SubState']=='running'
                 and Path('/proc/'+info['MainPID']+'/cwd').resolve()==expected,'D906_LIVE_SERVICE_IDENTITY')
    lock=auth.base/'release.lock' if auth.fixture else Path('/run/lock/enterprise-agent-workbench-release.lock')
    info=lock.lstat();need(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1,'RELEASE_LOCK_METADATA')
    if not auth.fixture:need(stat.S_IMODE(info.st_mode)==0o644 and [info.st_dev,info.st_ino]==auth.x['release_lock_identity'],'EXACT_RELEASE_LOCK')
    fd=os.open(lock,os.O_RDWR|os.O_NOFOLLOW);snapshot=None
    try:
        opened=os.fstat(fd);need((opened.st_dev,opened.st_ino)==(info.st_dev,info.st_ino),'RELEASE_LOCK_RACE')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);auth.check(360)
        target.mkdir(mode=0o700);put(target/'backup.claim.json',{'request_sha256':auth.pin,'operation_id':auth.x['operation_id']})
        with (target/'process.log').open('xb') as log:
            snapshot=Snapshot(psql_args(auth,root),log)
            exported=snapshot.one("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL TIME ZONE 'UTC'; SELECT pg_export_snapshot();")
            need(re.fullmatch('[0-9A-Fa-f-]+',exported),'SNAPSHOT_IDENTIFIER')
            need(int(snapshot.one('SELECT pg_database_size(current_database());'))<MAX_BYTES,'BOUNDED_DATABASE_SIZE')
            server=snapshot.one("SELECT current_setting('server_version_num');")
            need(server==('160006' if auth.fixture else '160014') and snapshot.one("SELECT current_setting('transaction_read_only');")=='on','REAL_READONLY_PG_VERSION')
            tables=aggregates(snapshot)
            ledger=json.loads(snapshot.one("SELECT json_agg(row_to_json(x)) FROM (SELECT version,name,checksum FROM schema_migrations ORDER BY version) x;"))
            need([x['version'] for x in ledger]==['%03d'%i for i in range(1,16)],'SCHEMA015')
            registry=json.loads(snapshot.one("SELECT coalesce(json_agg(row_to_json(x)),'[]'::json) FROM (SELECT s.slug,v.version,p.sha256,p.storage_path,p.size_bytes FROM skills s JOIN skill_versions v ON v.skill_id=s.id JOIN skill_packages p ON p.skill_version_id=v.id WHERE v.status='published' ORDER BY s.slug,v.version) x;"))
            need(auth.x['registry_packages'] is True and len(registry)<=256
                 and sum(x['size_bytes'] for x in registry)<=64*1024*1024,'BOUNDED_REGISTRY_SCOPE')
            package_files={};(target/'packages').mkdir(mode=0o700)
            for row in registry:
                path=Path(row['storage_path']);allowed=auth.base if auth.fixture else PROD/'shared/runtime-data/skill-registry/packages'
                need(path.is_relative_to(allowed) and re.fullmatch('[0-9a-f]{64}',row['sha256']),'REGISTRY_PATH_WHITELIST')
                content=read(path,limit=8*1024*1024);need(len(content)==row['size_bytes'] and sha(content)==row['sha256'],'REGISTRY_PACKAGE_BYTES')
                name='packages/'+row['sha256']+'.zip'
                if name not in package_files:put(target/name,content,0o400)
                package_files[name]={'sha256':sha(content),'bytes':len(content)}
            summary={'source_commit':auth.c['source_commit'],'source_tree':auth.c['source_tree'],'operation_id':auth.x['operation_id'],
                     'snapshot_id':exported,'tables':tables,'ledger':ledger,'registry_packages':registry,'read_only':True,'fixture':auth.fixture}
            summary_sha=put(target/'snapshot.readonly.summary.json',summary)
            with os.fdopen(os.open(target/'database.dump.cms',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400),'wb') as out:
                encrypt=subprocess.Popen(['/usr/bin/openssl','cms','-encrypt','-aes-256-cbc','-binary','-stream','-outform','DER',str(cert)],stdin=subprocess.PIPE,stdout=out,stderr=log)
                prefix=[] if auth.fixture else ['/usr/sbin/runuser','-u','postgres','--']
                dump_args=[root/'pg_dump','--format=custom','--no-owner','--no-privileges','--lock-wait-timeout=10s','--snapshot='+exported]
                dump_args+=['--host='+str(auth.x['fixture_source_socket']),'--port=54328','--username=native_source','--dbname=native_source'] if auth.fixture else ['--dbname=enterprise_agent']
                dump=subprocess.Popen([str(x) for x in prefix+dump_args],stdout=encrypt.stdin,stderr=log,env={'PATH':'/usr/bin:/bin','LC_ALL':'C','PGOPTIONS':'-c default_transaction_read_only=on'})
                encrypt.stdin.close();wait_pipeline([dump,encrypt],auth,300,target/'database.dump.cms');out.flush();os.fsync(out.fileno())
        snapshot.close();snapshot=None;auth.check()
        raw=read(target/'database.dump.cms');need(0<len(raw)<MAX_BYTES,'CIPHER_SIZE')
        receipt=dict(status='ENCRYPTED_BACKUP_COMPLETE_RESTORE_NOT_PROVEN',fixture=auth.fixture,source_commit=auth.c['source_commit'],source_tree=auth.c['source_tree'],
          operation_id=auth.x['operation_id'],request_sha256=auth.pin,root_sha256=auth.root_sha,owner_sha256=auth.owner_sha,
          predecessor_source=D906,ciphertext_sha256=sha(raw),ciphertext_size=len(raw),summary_sha256=summary_sha,recipient_sha256=auth.x['recipient_sha256'],
          pipeline_exit=[dump.returncode,encrypt.returncode],package_files=package_files,business_database_writes=0,plaintext_dump_files=0)
        put(target/'backup.receipt.json',receipt);return target
    except BaseException:
        if target.exists() and not (target/'backup.failed.json').exists():put(target/'backup.failed.json',{'status':'STOP_RETAIN_NO_RETRY'})
        raise
    finally:
        if snapshot:snapshot.close()
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)

def transfer_context(auth,target):
    auth.check();receipt=parse(read(target/'backup.receipt.json'))
    need(receipt['status']=='ENCRYPTED_BACKUP_COMPLETE_RESTORE_NOT_PROVEN' and receipt['request_sha256']==auth.pin
         and receipt['operation_id']==auth.x['operation_id'] and receipt['root_sha256']==auth.root_sha
         and receipt['pipeline_exit']==[0,0] and not (target/'backup.failed.json').exists(),'BACKUP_NOT_COMPLETE')
    files={n:{'sha256':sha(read(target/n)),'bytes':len(read(target/n))} for n in ('database.dump.cms','snapshot.readonly.summary.json','backup.receipt.json')}
    files.update(receipt['package_files'])
    return dict(profile=PROFILE,fixture=auth.fixture,request_sha256=auth.pin,source_commit=auth.c['source_commit'],source_tree=auth.c['source_tree'],operation_id=auth.x['operation_id'],root_sha256=auth.root_sha,files=files)
def receive_verified(auth,context,pin):
    auth.isolated();auth.check();raw=read(Path(context));need(sha(raw)==pin,'TRANSFER_CONTEXT_HASH');v=parse(raw)
    need(set(v)=={'profile','fixture','request_sha256','source_commit','source_tree','operation_id','root_sha256','files'}
         and v['profile']==PROFILE and v['fixture'] is auth.fixture and v['request_sha256']==auth.pin
         and v['root_sha256']==auth.root_sha and v['operation_id']==auth.x['operation_id']
         and (v['source_commit'],v['source_tree'])==(auth.c['source_commit'],auth.c['source_tree'])
         and {'database.dump.cms','snapshot.readonly.summary.json','backup.receipt.json'}<=set(v['files'])
         and all(n in {'database.dump.cms','snapshot.readonly.summary.json','backup.receipt.json'} or re.fullmatch('packages/[0-9a-f]{64}\\.zip',n) for n in v['files']), 'TRANSFER_IDENTITY')
    incoming=auth.private/'incoming'
    for name,pin in v['files'].items():
        data=read(incoming/name);need(len(data)==pin['bytes'] and sha(data)==pin['sha256'],'TRANSFER_BYTES:'+name)
    r=parse(read(incoming/'backup.receipt.json'));s=parse(read(incoming/'snapshot.readonly.summary.json'))
    need(r['status']=='ENCRYPTED_BACKUP_COMPLETE_RESTORE_NOT_PROVEN' and r['fixture'] is s['fixture'] is auth.fixture
         and r['pipeline_exit']==[0,0] and r['root_sha256']==auth.root_sha and r['operation_id']==s['operation_id']==auth.x['operation_id']
         and r['source_commit']==s['source_commit']==auth.c['source_commit'] and r['business_database_writes']==0
         and r['source_tree']==s['source_tree']==auth.c['source_tree'] and r['request_sha256']==auth.pin
         and r['ciphertext_sha256']==v['files']['database.dump.cms']['sha256']
         and r['summary_sha256']==v['files']['snapshot.readonly.summary.json']['sha256'] and s['read_only'] is True,'RECEIPTS_COMPLETE')
    need(set(v['files'])=={'database.dump.cms','snapshot.readonly.summary.json','backup.receipt.json'}|set(r['package_files'])
         and all(v['files'][n]==pin for n,pin in r['package_files'].items()),'REGISTRY_TRANSFER_COMPLETE')
    return v,r,s
def mirror_registry(auth,summary):
    root=auth.private/'mirror/shared/runtime-data/skill-registry'
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    for row in summary['registry_packages']:
        need(re.fullmatch('[a-z0-9][a-z0-9-]{0,127}',row['slug'])
             and re.fullmatch('[0-9]+\\.[0-9]+\\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?',row['version']),'REGISTRY_SLUG_AND_VERSION')
        original=Path(row['storage_path'])
        original_data=auth.base/'initial-runtime-data' if auth.fixture else PROD/'shared/runtime-data'
        need(original.is_relative_to(original_data/'skill-registry/packages'),'PRIVATE_REGISTRY_MOUNT_SCOPE')
        content=read(auth.private/'incoming/packages'/(row['sha256']+'.zip'),limit=8*1024*1024)
        need(sha(content)==row['sha256'] and len(content)==row['size_bytes'],'MIRROR_PACKAGE_PIN')
        destination=auth.private/'mirror/shared/runtime-data'/original.relative_to(original_data);destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if not destination.exists():put(destination,content)
        published=root/'published'/row['slug']/row['version'];published.mkdir(mode=0o700,parents=True,exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            need(sum(x.file_size for x in z.infolist())<=32*1024*1024,'REGISTRY_UNPACK_SIZE')
            for item in z.infolist():
                p=PurePosixPath(item.filename)
                need(not p.is_absolute() and '..' not in p.parts and '\\' not in item.filename and p.parts[0]==row['slug']
                     and not stat.S_ISLNK(item.external_attr>>16),'REGISTRY_ZIP_PATH')
                if item.is_dir():continue
                target=published/Path(*p.parts[1:]);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                put(target,z.read(item),0o600|((item.external_attr>>16)&0o111))
def restore(auth,context,pin):
    v,r,summary=receive_verified(auth,context,pin)  # all failures BEFORE restore claim
    transfer=parse(read(auth.private/'transfer.complete.json'))
    need(transfer=={'status':'TRANSFER_COMPLETE_BYTES_AND_RECEIPTS_VERIFIED','fixture':auth.fixture,'context_sha256':pin,
        'request_sha256':auth.pin,'root_sha256':auth.root_sha,'operation_id':auth.x['operation_id']},'RECEIVER_COMPLETION_REQUIRED')
    sources=auth.base if auth.fixture else auth.private
    for prior,identity in ((False,auth.c),(True,auth.p['predecessor'])):
        location=sources/'releases'/identity['release_id']
        artifact(auth,location/(identity['release_id']+'.tar.gz'),location/(identity['release_id']+'.manifest.json'),location,prior=prior)
        for row in summary['ledger']:
            need(re.fullmatch('[0-9]{3}',row['version']) and re.fullmatch('[A-Za-z0-9_.-]+',row['name']),'MIGRATION_IDENTIFIER')
            migration=location/'enterprise_agent_poc/migrations/postgres'/(row['version']+'_'+row['name'])
            raw=read(migration)
            # Same pure-byte compatibility as migrate.migration_checksums /
            # compatibility_status; do not import application settings in -I -B -S.
            canonical=raw.replace(b'\r\n',b'\n').replace(b'\r',b'\n')
            need(row['checksum'] in {sha(raw),sha(canonical.replace(b'\n',b'\r\n'))},
                 'EXACT_PAIR_RESTORED_LEDGER_SOURCE')
    root=pg(auth,'restore');target=auth.private/'restore';key=auth.private/'recipient/key.pem';cert=auth.private/'recipient/recipient.crt'
    read(key,os.getuid(),mode=0o600);need(sha(read(cert))==r['recipient_sha256'],'PRIVATE_RECIPIENT_MATCH')
    need(not os.path.lexists(target),'RESTORE_CLAIM_ALREADY_ATTEMPTED');auth.check(60)
    target.mkdir(mode=0o700);put(target/'restore.claim.json',{'context_sha256':pin,'operation_id':auth.x['operation_id']})
    started=False;snapshot=None
    try:
        command([root/'initdb','-D',target/'pgdata','-U','native_restore','-A','trust','--encoding=UTF8','--locale=C'],60)
        command([root/'pg_ctl','-D',target/'pgdata','-l',target/'pg.log','-o',"-h 127.0.0.1 -k '' -p 25432 -c shared_buffers=16MB -c max_connections=20",'-w','start'],45);started=True
        command([root/'createdb','-h',restore_host(auth),'-p','25432','-U','native_restore','native_restored'])
        with (target/'restore.log').open('xb') as log:
            decrypt=subprocess.Popen(['/usr/bin/openssl','cms','-decrypt','-binary','-inform','DER','-in',str(auth.private/'incoming/database.dump.cms'),'-inkey',str(key)],stdout=subprocess.PIPE,stderr=log)
            loader=subprocess.Popen([str(root/'pg_restore'),'-h',restore_host(auth),'-p','25432','-U','native_restore','-d','native_restored','--no-owner','--no-acl','--single-transaction','--exit-on-error'],stdin=decrypt.stdout,stdout=log,stderr=log)
            decrypt.stdout.close();wait_pipeline([decrypt,loader],auth,300)
            snapshot=Snapshot(psql_args(auth,root,True),log);snapshot.one("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL TIME ZONE 'UTC'; SELECT 'READY';")
            actual=aggregates(snapshot)
            put(target/'data.compare.json',{'expected':summary['tables'],'actual':actual,'match':actual==summary['tables']})
            need(actual==summary['tables'],'RESTORED_BUSINESS_DATA_DRIFT')
            ledger=json.loads(snapshot.one("SELECT json_agg(row_to_json(x)) FROM (SELECT version,name,checksum FROM schema_migrations ORDER BY version) x;"))
            need(ledger==summary['ledger'],'RESTORED_SCHEMA_LEDGER_DRIFT');snapshot.close();snapshot=None
        auth.check()
        command([root/'pg_ctl','-D',target/'pgdata','-m','fast','-w','stop'],45);started=False
        mirror_registry(auth,summary)
        result=dict(fixture=auth.fixture,source_commit=auth.c['source_commit'],source_tree=auth.c['source_tree'],status='ISOLATED_RESTORE_DATA_MATCH',operation_id=auth.x['operation_id'],data_preserved=True,assertions=ASSERTIONS,failures=0,errors=0,skipped=0,context_sha256=pin,plaintext_dump_files=0,private_pg_stopped=True,production_changes=0,native_runtime_recovery='NOT_EXECUTED_BY_RESTORE')
        put(target/'restore.complete.json',result);return result
    except BaseException:
        put(target/'restore.failed.json',{'status':'STOP_RETAIN_FAILED_CLAIM_NO_RETRY'});raise
    finally:
        if snapshot:snapshot.close()
        if started:command([root/'pg_ctl','-D',target/'pgdata','-m','fast','-w','stop'],45)

def main():
    need(sys.flags.isolated and sys.dont_write_bytecode and sys.flags.no_site,'SYSTEM_PYTHON_I_B_S')
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['status','install','materialize-private','backup','transfer-context','verify-transfer','restore'])
    p.add_argument('--authority',required=True);p.add_argument('--request-sha256',required=True);p.add_argument('--fixture-root')
    p.add_argument('--archive');p.add_argument('--manifest');p.add_argument('--context');p.add_argument('--context-sha256')
    p.add_argument('--predecessor-archive');p.add_argument('--predecessor-manifest')
    a=p.parse_args();auth=Authority(a.authority,a.request_sha256,a.fixture_root);result={}
    if a.action=='status':
        auth.production();result=dict(kind='NATIVE_V2_LIVE_STATUS_NOT_AUTHORITY',request_sha256=auth.pin,root_sha256=auth.root_sha,source_commit=auth.c['source_commit'],operation_id=auth.x['operation_id'],observed_at=int(time.time()),unrevoked=True,issuer_verified=auth.issuer is not None,fixture=auth.fixture)
    elif a.action=='materialize-private':
        auth.isolated();auth.check(60)
        root=auth.base if auth.fixture else auth.private
        (root/'releases').mkdir(mode=0o700,exist_ok=True)
        counts={}
        for old,identity,archive,manifest in ((False,auth.c,a.archive,a.manifest),(True,auth.p['predecessor'],a.predecessor_archive,a.predecessor_manifest)):
            dest=root/'releases'/identity['release_id'];m,files=artifact(auth,archive,manifest,dest,not dest.exists(),old)
            for suffix,path in (('.tar.gz',archive),('.manifest.json',manifest)):
                target=dest/(identity['release_id']+suffix)
                if not target.exists():put(target,read(Path(path)))
            counts[identity['source_commit']]=len(files)
        result={'status':'PRIVATE_EXACT_PAIR_MATERIALIZED','fixture':auth.fixture,'files':counts,'production_changes':0}
    elif a.action=='install':
        auth.production();auth.check(60);dest=auth.base/'releases'/auth.c['release_id'];m,files=artifact(auth,a.archive,a.manifest,dest,True)
        put(dest/(auth.c['release_id']+'.manifest.json'),read(Path(a.manifest)));put(dest/(auth.c['release_id']+'.tar.gz'),read(Path(a.archive)))
        result=dict(status='INACTIVE_INSTALL_BYTES_VERIFIED',fixture=auth.fixture,files=len(files),source_commit=auth.c['source_commit'],source_tree=auth.c['source_tree'],active_pointer_changed=False)
        put(dest/'install.complete.json',result)
    elif a.action in ('backup','transfer-context'):
        auth.production();dest=auth.base/'releases'/auth.c['release_id']
        artifact(auth,dest/(auth.c['release_id']+'.tar.gz'),dest/(auth.c['release_id']+'.manifest.json'),dest)
        target=backup(auth) if a.action=='backup' else auth.base/'backups'/('native-v2-'+auth.x['operation_id'])
        result=transfer_context(auth,target)
    elif a.action=='verify-transfer':
        receive_verified(auth,a.context,a.context_sha256)
        result={'status':'TRANSFER_COMPLETE_BYTES_AND_RECEIPTS_VERIFIED','fixture':auth.fixture,'context_sha256':a.context_sha256,
                'request_sha256':auth.pin,'root_sha256':auth.root_sha,'operation_id':auth.x['operation_id']}
        target=auth.private/'transfer.complete.json'
        if target.exists():need(parse(read(target))==result,'TRANSFER_COMPLETION_CONFLICT')
        else:put(target,result)
    else:result=restore(auth,a.context,a.context_sha256)
    print(json.dumps(result,sort_keys=True),flush=True)
if __name__=='__main__':
    try:main()
    except BaseException as exc:
        print(json.dumps({'status':'STOP_RETAIN_NO_RETRY','error_type':type(exc).__name__,'first_failure_point':str(exc) if isinstance(exc,Blocked) else 'SYSTEM_OR_INPUT_FAILURE'}),flush=True)
        raise SystemExit(1)
