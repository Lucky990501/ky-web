"""Post-commit controlled-entry identity; never changes b0/e7 sealed artifacts."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.skill_python_runtime import git_identity, read_json, run, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    contract = ROOT / 'integrations/controlled-skill-action.v1.json'
    descriptor = read_json(contract)
    paths = descriptor['source_files']
    tracked = run(['git', 'ls-files', '--', *['enterprise_agent_poc/' + name for name in paths]], cwd=ROOT.parent).decode().splitlines()
    if set(tracked) != {'enterprise_agent_poc/' + name for name in paths}:
        raise ValueError('UNSEALED_CONTROLLED_ACTION_SOURCE')
    run(['git', 'diff', '--exit-code', 'HEAD', '--', *tracked, 'enterprise_agent_poc/integrations/controlled-skill-action.v1.json'], cwd=ROOT.parent)
    value = dict(git_identity(ROOT), contract_sha256=sha(contract.read_bytes()),
                 files={name: sha((ROOT / name).read_bytes()) for name in paths},
                 dispatch_compatibility=descriptor['dispatch_compatibility'],
                 runtime_compatibility=descriptor['runtime_compatibility'])
    raw = (json.dumps(value, indent=2) + '\n').encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        if args.output.read_bytes() != raw:
            raise ValueError('CONTROLLED_ACTION_SEAL_IMMUTABLE')
    else:
        with args.output.open('xb') as stream:
            stream.write(raw)
    print(json.dumps(dict(source_commit=value['source_commit'], source_tree=value['source_tree'],
                         contract_sha256=value['contract_sha256'], files=len(paths))))


if __name__ == '__main__': main()
