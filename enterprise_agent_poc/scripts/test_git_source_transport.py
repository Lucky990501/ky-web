"""TEST ONLY: verify a fixed Root-owned Git bundle/bare source for Binding.

Not an installer, arbitrary repo gateway, Production authority or codec.
The operator stages exact Git objects through the existing Bundle workflow.
"""
import hashlib,json,os,stat,subprocess
from pathlib import Path

NATIVE=Path('/etc/enterprise-agent-test-successor-wechat-d8a-common-cache-v1')
REPO=NATIVE/'source.git'
BUNDLE=NATIVE/'application.bundle'
SEAL=NATIVE/'source-transport-seal.v1.json'
GIT=Path('/usr/bin/git')
SOURCE='d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7'
TREE='6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5'
PARENT='9e6daef89a0aa6bd10bb8788fbc73207d11dccd5'
BASE='db8e23658baa6e4b707380e178aded561d3280f2'

class SourceTransportBlocked(PermissionError):pass
def need(ok,code):
    if not ok:raise SourceTransportBlocked(code)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def trusted(path,readonly=False):
    for item in (path,*path.parents):
        info=item.lstat()
        need(not stat.S_ISLNK(info.st_mode) and info.st_uid==info.st_gid==0
             and not info.st_mode&0o022,'UNTRUSTED_ROOT_STAGING')
    if readonly:need(path.is_file() and not path.stat().st_mode&0o222,'WRITABLE_STAGING_FILE')
def fixed_request(repo,source,tree):
    need(os.environ.get('APP_ENV')=='test','PRODUCTION_TRANSPORT_REJECTED')
    need(repo==REPO,'UNAUTHORIZED_REPOSITORY')
    need(source==SOURCE,'WRONG_SOURCE')
    need(tree==TREE,'WRONG_TREE')
def file_map():
    trusted(REPO)
    need(not (REPO/'objects/info/alternates').exists(),'EXTERNAL_GIT_OBJECTS_REJECTED')
    result={}
    for path in REPO.rglob('*'):
        trusted(path,readonly=path.is_file())
        if path.is_file():result[str(path.relative_to(REPO))]=sha(path)
    return result
def validate_seal(seal,bundle_sha,files):
    need(seal['contract']=='TEST_ONLY_GIT_SOURCE_TRANSPORT_V1'
         and seal['environment']=='test' and seal['production_deploy_authority'] is False,'TRANSPORT_SEAL_REJECTED')
    need((seal['source'],seal['tree'],seal['parent'],seal['base'])==(SOURCE,TREE,PARENT,BASE),'SEALED_SOURCE_DRIFT')
    need(seal['bundle_sha256']==bundle_sha,'BUNDLE_TAMPERED')
    need(seal['source_files']==files,'STAGING_TAMPERED')
def verify(repo=REPO,source=SOURCE,tree=TREE):
    fixed_request(repo,source,tree)
    need(os.geteuid()==0,'ROOT_VERIFICATION_REQUIRED')
    module=Path(__file__)
    need(module.parent==NATIVE,'ROOT_MUST_NOT_EXECUTE_MUTABLE_TOOLING')
    trusted(module,readonly=True);trusted(SEAL,readonly=True);trusted(BUNDLE,readonly=True);trusted(GIT)
    seal=json.loads(SEAL.read_bytes())
    need(seal['module_sha256']==sha(module) and seal['git_sha256']==sha(GIT),'TRANSPORT_CODE_DRIFT')
    before=file_map();validate_seal(seal,sha(BUNDLE),before)
    # No inherited lucky HOME/config/hooks/filters or SUDO_UID. The only repo
    # used by the Root reader and local upload-pack is the sealed Root bare src.
    env={'PATH':'/usr/bin:/bin','HOME':'/nonexistent','APP_ENV':'test','GIT_CONFIG_NOSYSTEM':'1',
         'GIT_CONFIG_GLOBAL':'/dev/null','GIT_OPTIONAL_LOCKS':'0'}
    prefix=[str(GIT),'--git-dir='+str(REPO),'-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null']
    result=subprocess.run(prefix+['show','-s','--format=%H %T %P',SOURCE],env=env,capture_output=True,text=True,timeout=15)
    need(result.returncode==0 and result.stdout.strip()==SOURCE+' '+TREE+' '+PARENT,'EXACT_ROOT_SOURCE_IDENTITY_REJECTED')
    lineage=subprocess.run(prefix+['merge-base','--is-ancestor',BASE,SOURCE],env=env,capture_output=True,timeout=15)
    need(lineage.returncode==0,'LINEAGE_REJECTED')
    need(before==file_map() and seal['bundle_sha256']==sha(BUNDLE),'STAGING_REPLACED_DURING_VERIFICATION')
    return {'status':'ROOT_GIT_SOURCE_TRANSPORT_PASS','repo':str(REPO),'source':SOURCE,'tree':TREE,
            'parent':PARENT,'lineage':'PASS','bundle_sha256':seal['bundle_sha256'],
            'root_reads_lucky_config':False,'production_deploy_authority':False}
