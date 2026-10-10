"""Operator-only revision runtime tools. No PRIMARY call or automatic activation.

seal/make-descriptor are architecture artifact generation. install is a future
explicit local-to-the-approved-host operation, not a Task execution code path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.skill_python_runtime import (DESCRIPTOR,SkillPythonRuntime,canonical,
                                     read_json,sha,source_seal)
from app import wechat_skill


def write_new(path,raw):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('REVISION_ARTIFACT_IMMUTABLE')
    else:
        with path.open('xb') as stream:stream.write(raw)


def make_descriptor():
    native=wechat_skill.verify_revision_contract()
    artifact='integrations/artifacts/wechat-html-draft-1.0.0.zip'
    write_new(ROOT/artifact,wechat_skill.native_package())
    files=[
        'app/skill_python_runtime.py','app/wechat_skill.py','app/wechat_action_contract.py',
        'app/tenant_secret_reference.py','app/agent_productization.py','app/product_store.py',
        'app/platform_mcp/server.py','app/platform_mcp/service.py','app/security.py',
        'app/agent_execution.py','app/skill_registry.py','app/bundled_skills.py','app/skills.py',
        'app/store.py','app/domain.py','app/settings.py','scripts/verify_wechat_skill.py',
        'scripts/lock_wechat_python_runtime.py','scripts/wechat_revision_python_runtime.py',
        'tests/test_wechat_skill_integration.py','tests/test_wechat_action_contract.py',
        'tests/test_skill_python_runtime.py','integrations/wechat-html-draft.original.v1.json',
        'integrations/wechat-html-draft.revision.v1.json','integrations/wechat-html-draft.dependencies.v1.json',
        'integrations/wechat-python311-linux.v1.lock.json',
        'integrations/wechat-python311-linux.v1.requirements.txt',artifact,
    ]+[str(Path(native['source_root'])/item['path']).replace('\\','/') for item in native['files']]
    lock_path='integrations/wechat-python311-linux.v1.lock.json'
    descriptor=dict(contract='REVISION_BOUND_SKILL_PYTHON_RUNTIME_V1',skill_slug=wechat_skill.SLUG,
        skill_version=wechat_skill.VERSION,registry_revision_field='skill_versions.id',runtime_revision=1,
        target=read_json(ROOT/lock_path)['target'],artifact_file=artifact,
        artifact_sha256=native['artifact_sha256'],lock_file=lock_path,lock_sha256=sha((ROOT/lock_path).read_bytes()),
        actions=['PREPARE','CREATE_DRAFT'],runtime_layout='<managed-root>/<slug>/<skill_versions.id>/{venv,lock,receipt}',
        adapter_source_binding='post-commit external source_commit/source_tree seal; no self-referential commit hash',
        adapter_files={name:sha((ROOT/name).read_bytes()) for name in sorted(files)})
    raw=(json.dumps(descriptor,ensure_ascii=False,indent=2)+'\n').encode()
    write_new(ROOT/DESCRIPTOR,raw)
    print(json.dumps(dict(descriptor_sha256=sha(raw),lock_sha256=descriptor['lock_sha256'],
                         adapter_digest=sha(canonical(descriptor['adapter_files'])))))


def fetch_wheels(path):
    lock=read_json(ROOT/'integrations/wechat-python311-linux.v1.lock.json')
    path.mkdir(parents=True,exist_ok=True)
    if path.is_symlink():raise ValueError('WHEELHOUSE_BLOCKED')
    expected={item['filename'] for item in lock['packages']}
    if any(p.name not in expected or p.is_symlink() for p in path.iterdir()):raise ValueError('WHEELHOUSE_BLOCKED')
    for item in lock['packages']:
        wheel=path/item['filename']
        if wheel.exists():raw=wheel.read_bytes()
        else:
            if not item['url'].startswith('https://files.pythonhosted.org/'):raise ValueError('WHEEL_URL_BLOCKED')
            with urllib.request.urlopen(item['url'],timeout=60) as response:raw=response.read(20*1024*1024+1)
        if sha(raw)!=item['sha256']:raise ValueError('WHEEL_SHA_BLOCKED')
        write_new(wheel,raw)
    print(json.dumps(dict(wheels=len(expected),hashes='PASS',installed=False)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('make-descriptor')
    seal=commands.add_parser('seal');seal.add_argument('--output',type=Path,required=True)
    fetch=commands.add_parser('fetch-wheels');fetch.add_argument('--wheelhouse',type=Path,required=True)
    for name in ('install','verify'):
        cmd=commands.add_parser(name)
        cmd.add_argument('--approval',type=Path,required=True)
        cmd.add_argument('--revision',type=Path,required=True,help='Trusted export of existing published Registry row')
        cmd.add_argument('--managed-root',type=Path,required=True)
        if name=='install':
            cmd.add_argument('--base-python',type=Path,required=True)
            cmd.add_argument('--wheelhouse',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='make-descriptor':make_descriptor()
    elif args.command=='seal':
        value=source_seal();write_new(args.output,(json.dumps(value,indent=2)+'\n').encode());print(json.dumps(value))
    elif args.command=='fetch-wheels':fetch_wheels(args.wheelhouse)
    else:
        runtime=SkillPythonRuntime(args.managed_root,read_json(args.approval))
        revision=read_json(args.revision)
        if args.command=='install':print(json.dumps(runtime.install(revision,args.base_python,args.wheelhouse)))
        else:
            first=runtime.resolve(revision,'PREPARE');second=runtime.resolve(revision,'CREATE_DRAFT')
            print(json.dumps(dict(status='READY',python=str(first),actions_same_runtime=first==second,executed=False)))


if __name__=='__main__':main()
