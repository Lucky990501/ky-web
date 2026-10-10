"""Revision-aware execution bridge inside the existing Task/MCP lifecycle.

Adapters provide validated input/output semantics, not another runner/service.
Only trusted server registration selects adapters, runtimes and fixed argv.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid

from app.agent_execution import authorize_tool
from app.security import TokenError
from app.skill_python_runtime import SkillRuntimeError, canonical, sha

CONTRACT='SKILL_REVISION_RUNTIME_DISPATCH_V1'
BOOTSTRAP='import runpy,sys;from pathlib import Path;p=sys.argv.pop(1);sys.path.insert(0,str(Path(p).parent));sys.argv[0]=p;runpy.run_path(p,run_name="__main__")'
CODES={'SKILL_REVISION_NOT_FOUND','SKILL_RUNTIME_NOT_READY','SKILL_ACTION_NOT_ALLOWED',
       'SKILL_EXECUTION_FAILED','SKILL_RESULT_INVALID','SKILL_INPUT_INVALID'}


class SkillDispatchError(PermissionError):
    def __init__(self,code):
        self.code=code if code in CODES else 'SKILL_EXECUTION_FAILED'
        super().__init__(self.code)


def safe_arguments(value):
    """For existing Run traces: never persist article/assets/stdout/env."""
    if not isinstance(value,dict):return {}
    result={}
    action=value.get('action')
    if action in ('PREPARE','CREATE_DRAFT'):result['action']=action
    key=value.get('skill_key')
    if isinstance(key,str):result['skill_key_identity']=sha(key.encode())
    try:
        revision=value.get('revision')
        if isinstance(revision,str) and str(uuid.UUID(revision))==revision:result['revision']=revision
    except ValueError:pass
    return result


def failure(code,action=None):
    return dict(status='failed',action=action if action in ('PREPARE','CREATE_DRAFT') else None,
        artifact_refs=[],summary=code,verification={},receipt_ref=None,error_code=code)


@dataclass(frozen=True)
class RuntimeEntry:
    python: Path
    skill_root: Path
    runtime_root: Path
    identity: str
    source_root: Path | None=None


@dataclass(frozen=True)
class ActionRegistration:
    skill_key: str
    version: str
    checksum: str
    action: str
    scope: str
    entrypoint: str
    options: tuple[str,...]
    adapter: object
    runtime: object
    permission: object
    enabled: bool=True


def add_dispatch_binding(catalog,agent_id,draft_id,actor):
    """Existing authoring API authorization remains mandatory; no auto-publish."""
    detail=catalog.detail(agent_id)
    revision=next((v for v in detail['versions'] if v['id']==draft_id and v['status']=='draft'),None)
    if not revision:raise SkillDispatchError('SKILL_ACTION_NOT_ALLOWED')
    tools=[dict(tool_capability_id=t['tool_capability_id'],invocation_requirement=t['invocation_requirement'])
           for t in revision['tools'] if t['tool_capability_id']!='skill_action_execute']
    tools.append(dict(tool_capability_id='skill_action_execute',invocation_requirement='optional'))
    return catalog.bind_tools(agent_id,draft_id,tools,actor)


class SkillActionDispatcher:
    def __init__(self,store,tokens,registry,data_dir,registrations,*,runner=None,source_identity=None):
        self.store,self.tokens,self.registry=store,tokens,registry
        self.data_dir=Path(data_dir)
        self.registrations={(r.skill_key,r.version,r.action):r for r in registrations}
        self.runner=runner or subprocess.run
        self.source_identity=source_identity or {}

    def _task(self,bearer,task_scope):
        principal=self.tokens.verify(bearer,'skills:execute')
        task_id=self.tokens.verify_task_scope(task_scope,principal.tenant_id)
        authorize_tool(self.store,principal,'skills:execute')
        with self.store.connection() as conn:
            row=conn.execute("SELECT t.*,c.tool_policy_snapshot,c.runtime_profile_id FROM tasks t JOIN task_agent_contexts m ON m.task_id=t.id JOIN agent_execution_contexts c ON c.id=m.context_id AND c.tenant_id=t.tenant_id AND c.agent_id=t.agent_id WHERE t.id=? AND t.tenant_id=? AND t.agent_id=? AND c.id=? AND t.status='running'",(task_id,principal.tenant_id,principal.agent_id,principal.execution_context_id)).fetchone()
        if not row or not row['run_id'] or not row['conversation_id']:
            raise SkillDispatchError('SKILL_ACTION_NOT_ALLOWED')
        task=dict(row);task['_context_id']=principal.execution_context_id
        trace=self.store.run_trace(task['run_id'],principal.tenant_id)
        if (not trace or trace['status']!='running' or trace['agent_id']!=principal.agent_id
                or trace['conversation_id']!=task['conversation_id']):
            raise SkillDispatchError('SKILL_ACTION_NOT_ALLOWED')
        return principal,task,json.loads(task['tool_policy_snapshot'])

    def _revision(self,task,policy,key,revision_id):
        if (not isinstance(key,str) or not re.fullmatch('[a-z][a-z0-9-]{0,63}',key)
                or not isinstance(revision_id,str)):
            raise SkillDispatchError('SKILL_REVISION_NOT_FOUND')
        try:
            if str(uuid.UUID(revision_id))!=revision_id:raise ValueError()
        except ValueError:raise SkillDispatchError('SKILL_REVISION_NOT_FOUND') from None
        refs=[ref for ref in policy['skill_refs'] if ref['slug']==key and ref['id']==revision_id]
        if len(refs)!=1:raise SkillDispatchError('SKILL_REVISION_NOT_FOUND')
        ref=refs[0]
        with self.store.connection() as conn:
            bound=conn.execute("SELECT v.id FROM agent_template_version_skills b JOIN skill_versions v ON v.id=b.skill_version_id AND v.skill_id=b.skill_id JOIN skill_packages p ON p.skill_version_id=v.id JOIN agent_execution_contexts c ON c.agent_template_version_id=b.agent_template_version_id WHERE c.id=? AND b.skill_id=? AND b.skill_version_id=? AND v.status='published' AND v.checksum=? AND p.sha256=v.checksum",(task['_context_id'],ref['skill_id'],revision_id,ref['checksum'])).fetchone()
        if not bound:raise SkillDispatchError('SKILL_REVISION_NOT_FOUND')
        try:
            revision=self.registry.version(revision_id)
            if (revision['slug']!=key or revision['version']!=ref['version']
                    or revision['checksum']!=ref['checksum'] or revision['status']!='published'):
                raise SkillDispatchError('SKILL_REVISION_NOT_FOUND')
            package=Path(revision['storage_path'])
            if package.is_symlink() or not package.resolve().is_relative_to(self.registry.packages_root.resolve()):
                raise SkillDispatchError('SKILL_REVISION_NOT_FOUND')
            self.registry.test_version(revision_id)
        except SkillDispatchError:raise
        except Exception:raise SkillDispatchError('SKILL_REVISION_NOT_FOUND') from None
        return revision

    def _workspace(self,principal,task,entry,invocation):
        names=(principal.tenant_id,principal.agent_id,principal.runtime_profile_id,task['id'])
        if any(not re.fullmatch('[a-zA-Z0-9_-]{1,128}',name) for name in names):
            raise SkillDispatchError('SKILL_ACTION_NOT_ALLOWED')
        profile=self.data_dir/'runtime'/names[0]/names[1]/names[2]/'workspace'
        workspace=profile/'tasks'/names[3]/invocation
        protected=(Path(__file__).resolve().parents[2],entry.source_root or entry.skill_root,
                   entry.skill_root,entry.runtime_root,self.registry.data_root)
        if (any(workspace.resolve().is_relative_to(root.resolve()) for root in protected)
                or any(p.is_symlink() for p in (*workspace.parents,workspace))):
            raise SkillDispatchError('SKILL_RUNTIME_NOT_READY')
        workspace.mkdir(parents=True,mode=0o700,exist_ok=False)
        return workspace,workspace.relative_to(profile).as_posix()

    def _active(self,task):
        with self.store.connection() as conn:
            row=conn.execute("SELECT status,stage FROM tasks WHERE id=? AND tenant_id=?",(task['id'],task['tenant_id'])).fetchone()
        if not row or row['status']!='running' or row['stage']=='cancelling':
            raise SkillDispatchError('SKILL_ACTION_NOT_ALLOWED')

    @staticmethod
    def _result(output,workspace,relative):
        try:
            if not isinstance(output,dict) or set(output)!={'artifact_refs','summary','verification'}:raise ValueError()
            if not isinstance(output['summary'],str) or not 0<len(output['summary'])<=300:raise ValueError()
            verification=output['verification']
            if (not isinstance(verification,dict) or any(not re.fullmatch('[a-z_]{1,64}',key)
                    or type(value) not in (bool,int) for key,value in verification.items())):raise ValueError()
            refs=output['artifact_refs']
            if not isinstance(refs,list) or len(refs)>20:raise ValueError()
            for ref in refs:
                if set(ref)!={'ref','mime_type','size_bytes','sha256'}:raise ValueError()
                prefix='workspace:'+relative+'/'
                if not ref['ref'].startswith(prefix):raise ValueError()
                name=ref['ref'][len(prefix):]
                if any(part in ('','.','..') or not re.fullmatch('[a-zA-Z0-9_.-]+',part) for part in name.split('/')):raise ValueError()
                path=workspace/name
                if path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()) or not path.is_file():raise ValueError()
                if (not re.fullmatch('[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+',ref['mime_type'])
                        or type(ref['size_bytes']) is not int or ref['size_bytes']!=path.stat().st_size
                        or ref['sha256']!=sha(path.read_bytes())):raise ValueError()
            return output
        except Exception:raise SkillDispatchError('SKILL_RESULT_INVALID') from None

    def execute(self,bearer,task_scope,skill_key,revision_id,action,payload):
        principal=task=None;receipt=None;workspace=None
        phase='authorization'
        try:
            principal,task,policy=self._task(bearer,task_scope)
            invocation=str(uuid.uuid4())
            receipt=dict(contract=CONTRACT,id=invocation,tenant_id=principal.tenant_id,
                agent_id=principal.agent_id,task_id=task['id'],run_id=task['run_id'],
                action=action if action in ('PREPARE','CREATE_DRAFT') else None,
                dispatch_source=self.source_identity,start=datetime.now(timezone.utc).isoformat(),
                end=None,status='running',exit_status=None,artifact_refs=[])
            phase='revision';revision=self._revision(task,policy,skill_key,revision_id)
            receipt.update(skill_key=revision['slug'],revision=revision['id'],version=revision['version'],
                           artifact_identity=revision['checksum'])
            registration=self.registrations.get((revision['slug'],revision['version'],action))
            if not registration or registration.checksum!=revision['checksum']:
                raise SkillDispatchError('SKILL_ACTION_NOT_ALLOWED')
            phase='action_permission'
            authorize_tool(self.store,principal,registration.scope)
            registration.permission(bearer,task_scope,action)
            if not registration.enabled:raise SkillDispatchError('SKILL_ACTION_NOT_ALLOWED')
            phase='input';validated=registration.adapter.validate_input(payload)
            phase='runtime';entry=registration.runtime.resolve(revision,action)
            receipt['runtime_identity']=entry.identity
            phase='workspace';workspace,relative=self._workspace(principal,task,entry,invocation)
            receipt['workspace_identity']=sha(canonical(dict(task=task['id'],invocation=invocation,profile=principal.runtime_profile_id)))
            config=registration.adapter.materialize(validated,workspace)
            script=entry.skill_root/registration.entrypoint
            if (script.is_symlink() or not script.is_file()
                    or not script.resolve().is_relative_to(entry.skill_root.resolve())):
                raise SkillDispatchError('SKILL_RUNTIME_NOT_READY')
            argv=[str(entry.python),'-I','-B','-X','utf8','-c',BOOTSTRAP,str(script),str(config),
                  *registration.options,'--work-dir',str(workspace/'run')]
            # No inherited PATH/PYTHONPATH/HOME credentials, proxy or env dump.
            import os
            env={key:os.environ[key] for key in ('SYSTEMROOT','WINDIR') if key in os.environ}
            (workspace/'tmp').mkdir()
            env.update(WORKSPACE_ROOT=str(workspace),TMP=str(workspace/'tmp'),TEMP=str(workspace/'tmp'))
            self._active(task);phase='execution'
            result=self.runner(argv,cwd=workspace,env=env,capture_output=True,timeout=120,shell=False)
            receipt['exit_status']=result.returncode
            if result.returncode:raise SkillDispatchError('SKILL_EXECUTION_FAILED')
            self._active(task)
            self._task(bearer,task_scope)
            self._revision(task,policy,skill_key,revision_id)
            registration.runtime.resolve(revision,action)
            phase='normalization'
            output=self._result(registration.adapter.normalize(workspace,relative),workspace,relative)
            receipt.update(status='completed',artifact_refs=output['artifact_refs'])
            output.update(status='completed',action=action,receipt_ref='skill-receipt:'+invocation)
            return output
        except (SkillDispatchError,SkillRuntimeError,TokenError,PermissionError) as error:
            code=getattr(error,'code',None) or ('SKILL_RUNTIME_NOT_READY' if isinstance(error,SkillRuntimeError)
                else 'SKILL_ACTION_NOT_ALLOWED')
            output=failure(code,action)
            if receipt:
                receipt.update(status='failed',error_code=code,failure_phase=phase)
                output['receipt_ref']='skill-receipt:'+receipt['id']
            return output
        except Exception:
            code='SKILL_RESULT_INVALID' if phase=='normalization' else 'SKILL_EXECUTION_FAILED'
            output=failure(code,action)
            if receipt:
                receipt.update(status='failed',error_code=code,failure_phase=phase)
                output['receipt_ref']='skill-receipt:'+receipt['id']
            return output
        finally:
            if receipt:
                receipt['end']=datetime.now(timezone.utc).isoformat()
                # Receipt contains identities/status only, never payload/stdout.
                try:
                    self.store.log_event(task['conversation_id'],'skill.execution',receipt)
                    if workspace:
                        target=workspace/'receipt.json'
                        if target.is_symlink() or not target.resolve().is_relative_to(workspace.resolve()):
                            raise ValueError()
                        target.write_bytes(canonical(receipt))
                except Exception:raise SkillDispatchError('SKILL_EXECUTION_FAILED') from None
