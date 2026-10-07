"""Workbench Native Skill V1 adapter; offline prepare and binding preparation.

Registry, Revision and Agent binding use the existing control plane. The upload
entry fails closed until a tenant-scoped secret/network execution broker exists.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SLUG = 'wechat-html-draft'
VERSION = '1.0.0'
NAME = '公众号文章排版与草稿上传'
AGENT_SLUG = 'wechat-official-account-writing'
ORIGINAL_SHA = 'a2f9aae56b1ad59cf9d4532fe9310bfb2827f43465d68e0a4db3746e8b2e14c3'
SOURCE = Path(__file__).resolve().parents[1] / 'skill_sources' / SLUG / VERSION
LOCK = Path(__file__).resolve().parents[1] / 'integrations/wechat-html-draft.dependencies.v1.json'
CONFIG_FIELDS = {'account','title','digest','html','cover','author','content_source_url','body_selector','image_map'}


class WechatSkillError(ValueError):
    pass


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_files(source: Path = SOURCE) -> dict:
    from app.bundled_skills import safe_path, secret_content
    files = {}
    for p in sorted(source.rglob('*')):
        if p.is_symlink():
            raise WechatSkillError('WECHAT_SKILL_SOURCE_BLOCKED')
        if not p.is_file():
            continue
        name = safe_path(p.relative_to(source).as_posix())
        raw = p.read_bytes()
        if b'\r' in raw or secret_content(raw):
            raise WechatSkillError('WECHAT_SKILL_SOURCE_BLOCKED')
        files[name] = (raw, '100644')
    if 'SKILL.md' not in files or 'scripts/wechat_draft.py' not in files:
        raise WechatSkillError('WECHAT_SKILL_SOURCE_BLOCKED')
    return files


def native_package(source: Path = SOURCE) -> bytes:
    from app.bundled_skills import deterministic_zip
    return deterministic_zip(SLUG, VERSION, source_files(source))


def revision_contract(source: Path = SOURCE) -> dict:
    files = source_files(source)
    return dict(slug=SLUG, name=NAME, version=VERSION, registry_revision_field='skill_versions.version',
        source_type='native_codex_zip', original_zip_sha256=ORIGINAL_SHA,
        source_root=f'skill_sources/{SLUG}/{VERSION}',
        files=[dict(path=n,sha256=digest(v[0]),git_mode=v[1]) for n,v in files.items()],
        artifact_sha256=digest(native_package(source)),
        entrypoint='scripts/wechat_draft.py', windows_helper='scripts/invoke_wechat.ps1',
        dependencies_sha256=digest(LOCK.read_bytes()),
        actions={'PREPARE':'--check','CREATE_DRAFT':'--run'},
        permissions={'PREPARE':['task_workspace:read_write','skill_assets:read'],
            'CREATE_DRAFT':['task_workspace:read_write','skill_assets:read',
                'controlled_image_fetch','wechat_api:443','wechat_secret_reference']},
        upload_gate='FORMAL_SECRET_AND_EGRESS_BROKER_REQUIRED',
        forbidden_actions=['PUBLISH','MASS_SEND','DELETE','UPDATE_EXISTING_DRAFT'],
        proposed_binding={'agent_slug':AGENT_SLUG,'skill_slug':SLUG,'version':VERSION})


def verify_revision_contract() -> dict:
    declaration = Path(__file__).resolve().parents[1] / 'integrations/wechat-html-draft.revision.v1.json'
    expected = json.loads(declaration.read_text(encoding='utf-8'))
    if expected != revision_contract():
        raise WechatSkillError('WECHAT_SKILL_REVISION_IDENTITY_BLOCKED')
    return expected


def import_revision(registry, actor: str) -> dict:
    """Import through the existing Registry API; no SQL, publish or auto-binding."""
    expected = verify_revision_contract()
    for skill in registry.list_skills():
        if skill['slug'] != SLUG:
            continue
        for version in skill['versions']:
            if version['version'] == VERSION:
                if version['checksum'] != expected['artifact_sha256']:
                    raise WechatSkillError('WECHAT_SKILL_REVISION_IMMUTABLE')
                registry.test_version(version['id'])
                return registry.version(version['id'])
    return registry.import_archive(SLUG, VERSION, NAME,
        '公众号 HTML 离线预检、素材准备与受控草稿创建；不发布或群发。', native_package(), actor)


def binding_plan(catalog, revision: dict, draft_revision_id: str) -> dict:
    """Read-only plan for an existing productized Agent's explicit draft revision."""
    if (revision.get('slug'), revision.get('version'), revision.get('status')) != (SLUG, VERSION, 'published'):
        raise WechatSkillError('WECHAT_SKILL_BINDING_BLOCKED')
    if revision.get('checksum') != verify_revision_contract()['artifact_sha256']:
        raise WechatSkillError('WECHAT_SKILL_BINDING_BLOCKED')
    matches = [x for x in catalog.list_templates() if x['slug'] == AGENT_SLUG]
    if len(matches) != 1 or matches[0].get('definition_source') != 'productized':
        raise WechatSkillError('WECHAT_SKILL_BINDING_BLOCKED')
    agent = catalog.detail(matches[0]['id'])
    drafts = [x for x in agent['versions'] if x['id'] == draft_revision_id and x['status'] == 'draft']
    if len(drafts) != 1:
        raise WechatSkillError('WECHAT_SKILL_BINDING_BLOCKED')
    bindings = [dict(skill_id=x['skill_id'], skill_version_id=x['skill_version_id'])
                for x in drafts[0]['skills'] if x['skill_id'] != revision['skill_id']]
    bindings.append(dict(skill_id=revision['skill_id'], skill_version_id=revision['id']))
    action_tools={'wechat_prepare_authorize','wechat_create_draft_authorize'}
    tool_bindings=[dict(tool_capability_id=x['tool_capability_id'],invocation_requirement=x['invocation_requirement'])
                   for x in drafts[0].get('tools',[]) if x['tool_capability_id'] not in action_tools]
    tool_bindings.extend(dict(tool_capability_id=tool,invocation_requirement='optional') for tool in sorted(action_tools))
    return dict(agent_id=agent['id'], agent_slug=AGENT_SLUG, agent_revision_id=draft_revision_id,
                bindings=bindings, apply_api='AgentProductization.bind_skills',
                tool_bindings=tool_bindings, allowed_actions=['PREPARE','CREATE_DRAFT'],
                runtime_binding_authorized=False, reason='PRIMARY_DEPENDENCY_ACCEPTANCE_REQUIRED')


def apply_binding_plan(catalog, revision: dict, draft_revision_id: str, actor: str) -> dict:
    """Authoring use only, behind existing platform-admin API authorization.

    Both operations target a draft. Failure leaves an unpublished draft and
    cannot authorize a live instance. No create/publish/enable call occurs here.
    """
    plan=binding_plan(catalog,revision,draft_revision_id)
    catalog.bind_skills(plan['agent_id'],draft_revision_id,plan['bindings'],actor)
    return catalog.bind_tools(plan['agent_id'],draft_revision_id,plan['tool_bindings'],actor)


def action_for_request(text: str) -> str | None:
    """Classification only, never an upload authorization capability."""
    if re.search(r'(不要|不需|不必|别).{0,5}(上传|草稿)',text):
        return 'PREPARE' if re.search(r'公众号|排版|HTML',text,re.I) else None
    if re.search(r'上传.{0,6}公众号|(?:创建|新建).{0,6}(?:公众号)?草稿',text):
        return 'CREATE_DRAFT'
    if re.search(r'公众号.{0,12}(?:文章|排版|标题|摘要|封面)|(?:写|排版).{0,8}公众号|公众号HTML',text,re.I):
        return 'PREPARE'
    return None


def workspace_path(root: Path, value: Path) -> Path:
    resolved = value.resolve()
    root = root.resolve(strict=True)
    if not resolved.is_relative_to(root) or root.is_relative_to(SOURCE.resolve()) or resolved.is_relative_to(SOURCE.resolve()):
        raise WechatSkillError('WORKSPACE_PATH_BLOCKED')
    return resolved


def check_dependencies() -> dict:
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    if sys.version_info < (3,10):
        raise WechatSkillError('WECHAT_SKILL_RUNTIME_DEPENDENCY_BLOCKED')
    found = {}
    try:
        for name, version in lock['packages'].items():
            found[name] = importlib.metadata.version(name)
            if found[name] != version:
                raise WechatSkillError('WECHAT_SKILL_RUNTIME_DEPENDENCY_BLOCKED')
    except importlib.metadata.PackageNotFoundError:
        raise WechatSkillError('WECHAT_SKILL_RUNTIME_DEPENDENCY_BLOCKED') from None
    return found


def runtime_for_action(action: str, revision: dict, runtime) -> Path:
    """Both actions select the exact same registered Revision interpreter.

    Trusted server supplies the published Registry row and managed resolver;
    article/user config never selects the executable. No runtime -> fail closed.
    """
    from app.skill_python_runtime import SkillRuntimeError
    if runtime is None or revision is None:
        raise WechatSkillError('SKILL_RUNTIME_NOT_READY')
    try:
        return runtime.resolve(revision,action)
    except SkillRuntimeError:
        raise WechatSkillError('SKILL_RUNTIME_NOT_READY') from None


def execute(action: str, workspace: Path, config: Path, *, upload_requested=False,
            revision=None, runtime=None) -> dict:
    """Trusted caller supplies a per-task workspace, never a model-chosen root."""
    if action == 'CREATE_DRAFT':
        if not upload_requested:
            raise WechatSkillError('WECHAT_UPLOAD_NOT_AUTHORIZED')
        # No env fallback / example authority / dummy secrets / unscoped callback.
        raise WechatSkillError('WECHAT_SECRET_AND_EGRESS_CONTRACT_REQUIRED')
    if action != 'PREPARE':
        raise WechatSkillError('WECHAT_ACTION_BLOCKED')
    verify_revision_contract()
    root = workspace.resolve(strict=True)
    config = workspace_path(root, config)
    if not config.is_file():
        raise WechatSkillError('WORKSPACE_PATH_BLOCKED')
    try:
        data = json.loads(config.read_text(encoding='utf-8'))
        if not isinstance(data,dict) or set(data) - CONFIG_FIELDS:
            raise WechatSkillError('ARTICLE_CONFIG_FIELDS_BLOCKED')
    except (ValueError,OSError):
        raise WechatSkillError('ARTICLE_CONFIG_FIELDS_BLOCKED') from None
    run = workspace_path(root, root / 'run')
    python=runtime_for_action(action,revision,runtime)
    if root.is_relative_to(runtime.root.resolve()) or runtime.root.resolve().is_relative_to(root):
        raise WechatSkillError('WORKSPACE_PATH_BLOCKED')
    env = {k:v for k,v in os.environ.items() if k in
           {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP'}}
    env.update(WORKSPACE_ROOT=str(root), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    # Isolated Python omits script-dir from sys.path. Add only the verified,
    # server-owned Skill entry directory, never Task PYTHONPATH.
    bootstrap='import runpy,sys;from pathlib import Path;p=sys.argv.pop(1);sys.path.insert(0,str(Path(p).parent));sys.argv[0]=p;runpy.run_path(p,run_name="__main__")'
    result = subprocess.run([str(python),'-I','-B','-X','utf8','-c',bootstrap,str(SOURCE/'scripts/wechat_draft.py'),
                            str(config),'--check','--work-dir',str(run)],
                           cwd=root, env=env, capture_output=True, timeout=120)
    if result.returncode:
        raise WechatSkillError('WECHAT_PREPARE_BLOCKED')  # stderr can contain user data.
    verification = workspace_path(root, root/'verification.json')
    report = dict(status='OFFLINE_PREPARE_PASS', action='PREPARE', wechat_calls=0,
                  visual_verified=False, preflight='run/preflight.json', prepared='run/prepared.html')
    verification.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report
