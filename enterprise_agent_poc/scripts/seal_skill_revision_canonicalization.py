"""New post-commit Source evidence, not a rewrite of any old Revision seal."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.skill_python_runtime import git_identity, run, sha
from app import wechat_skill
from app.skill_revision_identity import CANONICALIZATION_VERSION

FILES = (
    'app/skill_revision_identity.py', 'app/wechat_skill.py',
    'tests/test_skill_revision_canonicalization.py', 'tests/test_skill_only_test_qualification.py',
    'tests/fixtures/wechat_revision_linux_order.v1.json',
    'scripts/verify_skill_revision_canonicalization.py', 'scripts/seal_skill_revision_canonicalization.py',
    'tests/runtime_identity_fixture.py', 'tests/test_skill_python_runtime.py', 'scripts/verify_wechat_skill.py',
)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    paths = ['enterprise_agent_poc/' + name for name in FILES]
    tracked = run(['git', 'ls-files', '--', *paths], cwd=ROOT.parent).decode().splitlines()
    if set(tracked) != set(paths): raise ValueError('UNSEALED_CANONICAL_SOURCE')
    run(['git', 'diff', '--exit-code', 'HEAD', '--', *paths], cwd=ROOT.parent)
    declaration = wechat_skill.verify_revision_contract()
    descriptor = json.loads((ROOT / 'integrations/wechat-python-runtime.v1.json').read_bytes())
    artifact_sha = sha((ROOT / descriptor['artifact_file']).read_bytes())
    lock_sha = sha((ROOT / descriptor['lock_file']).read_bytes())
    if artifact_sha != declaration['artifact_sha256'] or lock_sha != descriptor['lock_sha256']:
        raise ValueError('SKILL_ARTIFACT_IDENTITY_DRIFT_BLOCKED')
    value = dict(git_identity(ROOT), parent_source='4ca21336bdd17a507d819457c2fc41c5e410846b',
        canonicalization_version=CANONICALIZATION_VERSION, persisted_identity_contract='UNCHANGED_NATIVE_REVISION_V1',
        files={name: sha((ROOT / name).read_bytes()) for name in FILES},
        declaration_sha256=sha((ROOT / 'integrations/wechat-html-draft.revision.v1.json').read_bytes()),
        artifact_sha256=artifact_sha, runtime_lock_sha256=lock_sha,
        runtime_descriptor_sha256=sha((ROOT / 'integrations/wechat-python-runtime.v1.json').read_bytes()),
        native_files=declaration['files'])
    raw = (json.dumps(value, indent=2) + '\n').encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        if args.output.read_bytes() != raw: raise ValueError('CANONICAL_SOURCE_SEAL_IMMUTABLE')
    else:
        with args.output.open('xb') as stream: stream.write(raw)
    print(json.dumps({key: value[key] for key in ('source_commit', 'source_tree',
        'canonicalization_version', 'artifact_sha256', 'runtime_lock_sha256', 'declaration_sha256')}))
