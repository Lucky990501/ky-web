"""Post-commit dispatch Source seal; never edits the old runtime seal/lock."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.skill_python_runtime import git_identity,read_json,sha


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    contract=ROOT/'integrations/skill-dispatch.v1.json'
    paths=read_json(contract)['dispatch_files']
    value=dict(git_identity(ROOT),contract_sha256=sha(contract.read_bytes()),
               files={name:sha((ROOT/name).read_bytes()) for name in paths})
    from app.skill_python_runtime import run
    tracked=run(['git','ls-files','--',*['enterprise_agent_poc/'+name for name in paths]],cwd=ROOT.parent).decode().splitlines()
    if set(tracked)!=set('enterprise_agent_poc/'+name for name in paths):raise ValueError('UNSEALED_DISPATCH_SOURCE')
    run(['git','diff','--exit-code','HEAD','--',*tracked,'enterprise_agent_poc/integrations/skill-dispatch.v1.json'],cwd=ROOT.parent)
    raw=(json.dumps(value,indent=2)+'\n').encode()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.output.exists():
        if args.output.read_bytes()!=raw:raise ValueError('DISPATCH_SEAL_IMMUTABLE')
    else:
        with args.output.open('xb') as stream:stream.write(raw)
    print(json.dumps(dict(source_commit=value['source_commit'],source_tree=value['source_tree'],
                         contract_sha256=value['contract_sha256'],files=len(paths))))


if __name__=='__main__':main()
