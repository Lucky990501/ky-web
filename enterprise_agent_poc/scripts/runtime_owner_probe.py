"""Fixed Root-owned helper executed only as lucky. No install/mutation APIs."""
import json,os,shutil,sys
from pathlib import Path
R=Path('/opt/enterprise-agent-workbench-test')
P=Path('/etc/enterprise-agent-test-successor-wechat-personal-db8-owner-context-v2')
APP=R/'releases/20261008-db8e236-wechat-personal-canonical-v2/enterprise_agent_poc'
PROJECT=R/'shared/source/wechat-revision-e7e96b1-v1/enterprise_agent_poc'
assert os.geteuid()==1000 and os.environ.get('APP_ENV')=='test'
assert shutil.which('git')=='/usr/bin/git'
seal=json.loads((P/'owner-context-seal.v1.json').read_bytes())
sys.path.insert(0,str(APP))
from app.skill_python_runtime import SkillPythonRuntime,git_identity
application=git_identity(APP);runtime_identity=git_identity(PROJECT)
assert application==dict(source_commit='db8e23658baa6e4b707380e178aded561d3280f2',source_tree='04b6774d9e7a95e87871ea9c2e360805988a8e13')
assert runtime_identity==dict(source_commit='e7e96b1a959d8631dc9dcd5c24939fa483ac134a',source_tree='a86b9e4d2f4785f1cbef5ae5e654c54cf20a5f75')
runtime=SkillPythonRuntime(R/'shared/runtime/skills',seal['runtime_approval'],project=PROJECT)
python=runtime.resolve(seal['revision'],'PREPARE')
receipt=json.loads((python.parent.parent.parent/'receipt/runtime.json').read_bytes())
print(json.dumps({'uid':os.geteuid(),'application':application,'runtime':runtime_identity,
    'python':str(python),'venv_identity':receipt['venv_identity'],'contract_resolve':'PASS'},sort_keys=True))
