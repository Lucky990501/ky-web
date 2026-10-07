"""Read-only host binding to separately sealed dispatch and runtime Sources."""
from __future__ import annotations

import json
from pathlib import Path
import stat

from app.skill_dispatch import ActionRegistration,RuntimeEntry,SkillActionDispatcher,SkillDispatchError
from app.skill_python_runtime import (SkillPythonRuntime,canonical,git_identity,read_json,sha)
from app.skill_registry import SkillRegistry
from app.wechat_action_contract import WechatActionContract
from app.wechat_prepare_action import WechatPrepareAdapter

PROJECT=Path(__file__).resolve().parents[1]
CONTRACT_PATH=PROJECT/'integrations/skill-dispatch.v1.json'


def check_dispatch_source(seal):
    contract=read_json(CONTRACT_PATH)
    if (git_identity(PROJECT)!={k:seal[k] for k in ('source_commit','source_tree')}
            or sha(CONTRACT_PATH.read_bytes())!=seal['contract_sha256']):
        raise SkillDispatchError('SKILL_RUNTIME_NOT_READY')
    if set(seal['files'])!=set(contract['dispatch_files']):raise SkillDispatchError('SKILL_RUNTIME_NOT_READY')
    for name,digest in seal['files'].items():
        path=PROJECT/name
        if path.is_symlink() or not path.resolve().is_relative_to(PROJECT.resolve()) or sha(path.read_bytes())!=digest:
            raise SkillDispatchError('SKILL_RUNTIME_NOT_READY')
    return contract


class PythonRevisionBinding:
    def __init__(self,runtime):self.runtime=runtime

    def resolve(self,revision,action):
        python=self.runtime.resolve(revision,action)
        descriptor,_,_=self.runtime.contract()
        native=read_json(self.runtime.project/'integrations/wechat-html-draft.revision.v1.json')
        root=self.runtime.project/native['source_root']
        # Actual Linux deployment must seal the read-only Skill installation.
        # Windows fixtures substitute this runtime boundary, not the dispatcher.
        for path in (root,*root.rglob('*')):
            if path.is_symlink() or path.stat().st_mode & (stat.S_IWUSR|stat.S_IWGRP|stat.S_IWOTH):
                raise SkillDispatchError('SKILL_RUNTIME_NOT_READY')
        return RuntimeEntry(python,root,self.runtime.root,
            sha(canonical(self.runtime.binding(revision,descriptor))),self.runtime.project.parent)


def from_settings(store,tokens,settings):
    try:
        if settings.environment!='test' or not settings.skill_dispatch_config:
            raise SkillDispatchError('SKILL_RUNTIME_NOT_READY')
        path=Path(settings.skill_dispatch_config)
        if path.is_symlink() or not path.is_absolute():raise SkillDispatchError('SKILL_RUNTIME_NOT_READY')
        config=read_json(path)
        if set(config)!={'contract','dispatch_seal','runtime_project','runtime_root','runtime_approval'} or config['contract']!='SKILL_REVISION_RUNTIME_DISPATCH_V1':
            raise SkillDispatchError('SKILL_RUNTIME_NOT_READY')
        contract=check_dispatch_source(config['dispatch_seal'])
        if config['runtime_approval']!=contract['runtime_compatibility']:
            raise SkillDispatchError('SKILL_RUNTIME_NOT_READY')
        if not Path(config['runtime_root']).is_absolute() or not Path(config['runtime_project']).is_absolute():
            raise SkillDispatchError('SKILL_RUNTIME_NOT_READY')
        runtime=SkillPythonRuntime(Path(config['runtime_root']),config['runtime_approval'],project=Path(config['runtime_project']))
        binding=PythonRevisionBinding(runtime)
        permission=WechatActionContract(store,tokens,settings.environment).resolve
        registrations=[ActionRegistration('wechat-html-draft','1.0.0',contract['runtime_compatibility']['artifact_sha256'],
            action,scope,'scripts/wechat_draft.py',('--check',),WechatPrepareAdapter(),binding,permission,enabled)
            for action,scope,enabled in (('PREPARE','wechat:prepare',True),('CREATE_DRAFT','wechat:draft:create',False))]
        registry=SkillRegistry(store,settings.data_dir/'skill-registry',PROJECT/'skill_packages')
        return SkillActionDispatcher(store,tokens,registry,settings.data_dir,registrations,
            source_identity={k:config['dispatch_seal'][k] for k in ('source_commit','source_tree')})
    except SkillDispatchError:raise
    except Exception:raise SkillDispatchError('SKILL_RUNTIME_NOT_READY') from None
