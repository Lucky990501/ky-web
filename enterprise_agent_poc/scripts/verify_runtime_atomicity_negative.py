"""Native identity negatives in a private mount namespace; seals unchanged."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path('/opt/enterprise-agent-native-isolated')
AUTH=Path('/etc/enterprise-agent-native-isolated-v1')


def main():
    assert os.geteuid()==0 and sys.dont_write_bytecode
    pair=json.loads((AUTH/'runtime-pair.v1.json').read_text())
    scope=json.loads((AUTH/'scope.v1.json').read_text())
    env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
         **dict(line.split('=',1) for line in (AUTH/'service.env').read_text().splitlines())}
    cli=[sys.executable,'-B',str(Path(pair['tooling']['path'])/'enterprise_agent_poc/scripts/run_exact_test_admin_lifecycle.py'),
         'status','--run-id',scope['run_id']]
    recovery=[sys.executable,'-B',str(Path(pair['tooling']['path'])/'enterprise_agent_poc/scripts/runtime_recovery_operator.py'),'preflight']
    if sys.argv[1:2]==['--child']:
        candidate=Path(sys.argv[2]);target=Path(sys.argv[3])
        assert candidate.parent==ROOT/'private/native-negatives' and target.parent==AUTH
        subprocess.run(['mount','--bind',candidate,target],check=True)
        result=subprocess.run(recovery if target.name.startswith('forward-recovery-') else cli,env=env,text=True,capture_output=True)
        assert result.returncode==2 and json.loads(result.stdout)['status']=='BLOCKED'
        print(result.stdout);return
    assert not sys.argv[1:]
    output=ROOT/'private/native-negatives';output.mkdir(mode=0o700)
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in AUTH.iterdir() if p.is_file()}
    variants={
        'application_tree':('runtime-pair.v1.json',lambda d:d['application'].update(tree='f'*40)),
        'tooling_source':('runtime-pair.v1.json',lambda d:d['tooling'].update(source='e'*40)),
        'approval':('approval.v2.json',lambda d:d['independent_approval'].update(authorization='UNAUTHORIZED')),
        'tenant':('parent-context.v1.json',lambda d:d['state'].update(tenant_id='native-isolated-foreign')),
        'recovery_approval':('forward-recovery-approval.v1.json',lambda d:d.update(authorization='UNAUTHORIZED')),
        'recovery_code_pin':('forward-recovery-approval.v1.json',lambda d:d['code_sha256'].__setitem__('runtime_recovery_operator.py','0'*64)),
        'recovery_pair':('forward-recovery-policy.v1.json',lambda d:d.update(pair_sha256='f'*64)),
    }
    evidence={}
    for label,(name,mutate) in variants.items():
        data=json.loads((AUTH/name).read_text());mutate(data)
        candidate=output/(label+'.json')
        with candidate.open('x') as stream:json.dump(data,stream)
        candidate.chmod(0o444)
        result=subprocess.run(['unshare','--mount','--propagation','private',sys.executable,'-B',__file__,
            '--child',str(candidate),str(AUTH/name)],env=env,text=True,capture_output=True)
        assert result.returncode==0,(label,result.stderr[-1200:])
        evidence[label]=json.loads(result.stdout)
    result=subprocess.run(cli,env={**env,'APP_ENV':'production'},text=True,capture_output=True)
    assert result.returncode==2
    evidence['production']=json.loads(result.stdout)
    result=subprocess.run(recovery,env={**env,'APP_ENV':'production'},text=True,capture_output=True)
    assert result.returncode==2
    evidence['recovery_production']=json.loads(result.stdout)
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in AUTH.iterdir() if p.is_file()}
    result=subprocess.run(cli,env=env,text=True,capture_output=True)
    assert result.returncode==0 and json.loads(result.stdout)['active_test_platform_admin']==0
    result=subprocess.run(recovery,env=env,text=True,capture_output=True)
    assert result.returncode==0
    evidence['original_seals_unchanged']=True
    with (ROOT/'logs/atomicity-negatives.json').open('x') as stream:json.dump(evidence,stream,indent=2,sort_keys=True)
    print(json.dumps(evidence),flush=True)


if __name__=='__main__':main()
