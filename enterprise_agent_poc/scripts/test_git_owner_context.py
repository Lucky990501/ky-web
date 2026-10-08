"""TEST ONLY: fixed owner read/identity probe, never an install authority."""
import hashlib,json,os,stat,subprocess
from pathlib import Path

R=Path('/opt/enterprise-agent-workbench-test')
NATIVE=Path('/etc/enterprise-agent-test-successor-wechat-personal-db8-owner-context-v1')
GIT=Path('/usr/bin/git');SUDO=Path('/usr/bin/sudo')
PY=R/'shared/runtime/python311/bin/python'
OWNER_UID=1000
REPOS={
 'application':(R/'releases/20261008-db8e236-wechat-personal-canonical-v2','db8e23658baa6e4b707380e178aded561d3280f2','04b6774d9e7a95e87871ea9c2e360805988a8e13'),
 'runtime':(R/'shared/source/wechat-revision-e7e96b1-v1','e7e96b1a959d8631dc9dcd5c24939fa483ac134a','a86b9e4d2f4785f1cbef5ae5e654c54cf20a5f75')}

class OwnerContextBlocked(PermissionError):pass
def require(ok,code):
 if not ok:raise OwnerContextBlocked(code)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def trusted(path,readonly=True):
 for p in (path,*path.parents):
  s=p.lstat();require(not stat.S_ISLNK(s.st_mode) and s.st_uid==s.st_gid==0 and not s.st_mode&0o022,'ROOT_NATIVE_TRUST_REJECTED')
 require(path.is_file() and (not readonly or not path.stat().st_mode&0o222),'ROOT_NATIVE_TRUST_REJECTED')
def native_module():
 path=Path(__file__);require(path.parent==NATIVE,'ROOT_MUST_NOT_EXECUTE_WRITABLE_TOOLING')
 trusted(path)
def authorize():
 require(os.environ.get('APP_ENV')=='test','PRODUCTION_OWNER_CONTEXT_REJECTED')
 require(os.geteuid()==0,'ROOT_CALLER_REQUIRED');native_module()
 for binary in (GIT,SUDO):trusted(binary,readonly=False)
 seal_path=NATIVE/'owner-context-seal.v1.json';trusted(seal_path)
 seal=json.loads(seal_path.read_bytes())
 require(seal['contract']=='TEST_ONLY_GIT_OWNER_CONTEXT_V1' and seal['environment']=='test' and seal['production_deploy_authority'] is False,'OWNER_SEAL_REJECTED')
 require(seal['module_sha256']==sha(Path(__file__)) and seal['git_sha256']==sha(GIT),'OWNER_CODE_OR_GIT_DRIFT')
 require(seal['owner_uid']==OWNER_UID and seal['git_executable']==str(GIT),'OWNER_SEAL_REJECTED')
 return seal
def repo_stamp(key):
 require(key in REPOS,'NON_AUTHORIZED_REPOSITORY_REJECTED')
 path=REPOS[key][0]
 require(path.is_dir() and not any(p.is_symlink() for p in (path,*path.parents)),'REPOSITORY_PATH_REJECTED')
 s=path.stat();require(s.st_uid==OWNER_UID and not s.st_mode&0o022,'REPOSITORY_OWNER_REJECTED')
 return (s.st_dev,s.st_ino,s.st_uid,path.resolve())
def execute(args):
 # Fixed argv; sudo drops privilege before Git/Python starts. No shell,
 # SUDO_UID spoofing, arbitrary script/repo input, credentials or parent env.
 p=subprocess.run([str(SUDO),'-n','-u','lucky','--',*map(str,args)],
                  env={'PATH':'/usr/bin:/bin','APP_ENV':'test','PYTHONDONTWRITEBYTECODE':'1'},
                  capture_output=True,text=True,timeout=60)
 require(p.returncode==0,'OWNER_READ_COMMAND_REJECTED')
 return p.stdout
def identity(key,expected_commit=None,expected_tree=None):
 authorize();require(key in REPOS,'NON_AUTHORIZED_REPOSITORY_REJECTED')
 path,commit,tree=REPOS[key]
 require(expected_commit in (None,commit),'WRONG_COMMIT_REJECTED')
 require(expected_tree in (None,tree),'WRONG_TREE_REJECTED')
 before=repo_stamp(key)
 prefix=[GIT,'--no-optional-locks','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null','-C',path]
 value=execute(prefix+['rev-parse','HEAD','HEAD^{tree}']).splitlines()
 require(value==[commit,tree],'EXACT_OWNER_COMMIT_TREE_REJECTED')
 require(not execute(prefix+['diff','--no-ext-diff','--no-textconv','--exit-code','HEAD','--','enterprise_agent_poc/app']).strip(),'DIRTY_OWNER_SOURCE_REJECTED')
 require(before==repo_stamp(key),'OWNER_REPOSITORY_REPLACED')
 return {'source_commit':commit,'source_tree':tree,'owner_uid':OWNER_UID,'git_executable':str(GIT)}
def final_application_files():
 trusted(NATIVE/'target-files.v1.json');expected=json.loads((NATIVE/'target-files.v1.json').read_bytes())
 path=REPOS['application'][0];before=repo_stamp('application')
 require(not any(p.is_symlink() for p in path.rglob('*')),'INSTALLED_SYMLINK_REJECTED')
 actual={str(p.relative_to(path)):sha(p) for p in path.rglob('*') if p.is_file()}
 require(actual==expected and before==repo_stamp('application'),'FINAL_INSTALLED_CONTENT_REJECTED')
 return hashlib.sha256(json.dumps(actual,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def runtime_probe():
 seal=authorize();application=identity('application');runtime=identity('runtime')
 files_before=final_application_files()
 helper=NATIVE/'runtime-owner-probe.py';trusted(helper)
 require(sha(helper)==seal['probe_sha256'],'OWNER_PROBE_CODE_DRIFT')
 stamps={k:repo_stamp(k) for k in REPOS}
 value=json.loads(execute([PY,'-I','-B',helper]))
 require(value['uid']==OWNER_UID and value['application']=={k:application[k] for k in ('source_commit','source_tree')}
         and value['runtime']=={k:runtime[k] for k in ('source_commit','source_tree')},'OWNER_PROBE_IDENTITY_REJECTED')
 require(value['python']==str(R/'shared/runtime/skills/wechat-html-draft'/seal['revision']['id']/'venv/bin/python')
         and value['venv_identity']==seal['venv_identity'],'OWNER_PROBE_RUNTIME_REJECTED')
 require(stamps=={k:repo_stamp(k) for k in REPOS} and files_before==final_application_files(),'VALIDATION_REPLACEMENT_REJECTED')
 identity('application');identity('runtime')
 return dict(value,status='GIT_OWNER_CONTEXT_PASS',root_final_content_sha256=files_before,
             root_install_authority='UNCHANGED',sudo_uid_required=False)
