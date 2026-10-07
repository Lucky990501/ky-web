"""External post-commit qualification seal; no install or authority activation."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.skill_python_runtime import git_identity, read_json, sha, run

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    contract = ROOT/'integrations/skill-only-test-qualification.v1.json'
    names = read_json(contract)['source_files']
    paths = ['enterprise_agent_poc/'+name for name in names] + ['enterprise_agent_poc/integrations/skill-only-test-qualification.v1.json']
    run(['git','diff','--exit-code','HEAD','--',*paths],cwd=ROOT.parent)
    tracked = run(['git','ls-files','--',*paths],cwd=ROOT.parent).decode().splitlines()
    if set(tracked) != set(paths):
        raise ValueError('UNSEALED_QUALIFICATION_SOURCE')
    value = dict(git_identity(ROOT), contract_sha256=sha(contract.read_bytes()),
        files={name:sha((ROOT/name).read_bytes()) for name in names})
    raw = (json.dumps(value,indent=2)+'\n').encode()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.output.exists():
        if args.output.read_bytes() != raw:
            raise ValueError('QUALIFICATION_SEAL_IMMUTABLE')
    else:
        with args.output.open('xb') as stream:
            stream.write(raw)
    print(json.dumps(dict(source_commit=value['source_commit'],source_tree=value['source_tree'],files=len(names))))
