"""Revision-bound Python runtime; existing Registry identity, no second Registry.

Provisioning is an explicit operator operation. Resolution never installs,
downloads, resolves versions or accepts a model-supplied interpreter path.
The approval/seal must come from the release control plane, not a Task payload.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

PROJECT=Path(__file__).resolve().parents[1]
DESCRIPTOR='integrations/wechat-python-runtime.v1.json'


class SkillRuntimeError(PermissionError):
    def __init__(self):super().__init__('SKILL_RUNTIME_NOT_READY')


def sha(raw):return hashlib.sha256(raw).hexdigest()


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def safe_env():
    return {name:os.environ[name] for name in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','HOME')
            if name in os.environ}


def run(args, *, cwd=None):
    result=subprocess.run([str(arg) for arg in args],cwd=cwd,env=safe_env(),
                          capture_output=True,timeout=180,check=False)
    if result.returncode:raise SkillRuntimeError()
    return result.stdout


def git_identity(project):
    root=project.parent
    # Reject dirty executable application code even outside the small adapter
    # checksum set. Unrelated reports/.gitignore/release handoffs are preserved.
    run(['git','diff','--exit-code','HEAD','--','enterprise_agent_poc/app'],cwd=root)
    return dict(source_commit=run(['git','rev-parse','HEAD'],cwd=root).decode().strip(),
                source_tree=run(['git','rev-parse','HEAD^{tree}'],cwd=root).decode().strip())


def read_json(path):return json.loads(path.read_bytes())


def file_tree_digest(root):
    entries={}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise SkillRuntimeError()
        if path.is_file() and path.suffix!='.pyc':
            entries[path.relative_to(root).as_posix()]=sha(path.read_bytes())
    return sha(canonical(entries))


def probe(python, packages, venv=None):
    # -I ignores PYTHONPATH, user-site and Python environment injection.
    script='''import importlib,importlib.metadata,json,platform,sys,sysconfig
names=json.loads(sys.argv[1])
for name in ('requests','bs4','css_inline','PIL','tinycss2'):importlib.import_module(name)
print(json.dumps(dict(python=platform.python_version(),implementation=sys.implementation.name,
os=sys.platform,architecture=platform.machine(),glibc=platform.libc_ver(),
prefix=sys.prefix,base_prefix=sys.base_prefix,abi=sysconfig.get_config_var('SOABI'),
packages={name:importlib.metadata.version(name) for name in names},
installed=sorted(d.metadata['Name'].lower().replace('_','-') for d in importlib.metadata.distributions()),
imports='PASS')))'''
    # Base interpreter probe must not require Skill imports that it must not own.
    if venv is None:
        script='''import json,platform,sys,sysconfig
print(json.dumps(dict(python=platform.python_version(),implementation=sys.implementation.name,
os=sys.platform,architecture=platform.machine(),glibc=platform.libc_ver(),
abi=sysconfig.get_config_var('SOABI'))))'''
    value=json.loads(run([python,'-I','-B','-c',script,json.dumps(packages)]))
    if (not re.fullmatch(r'3\.11\.\d+',value['python']) or value['os']!='linux'
            or value['architecture']!='x86_64' or value['implementation']!='cpython'
            or not value['abi'].startswith('cpython-311-')
            or value['glibc'][0]!='glibc'
            or tuple(int(x) for x in value['glibc'][1].split('.')[:2]) < (2,28)):
        raise SkillRuntimeError()
    if venv is not None and (Path(value['prefix']).resolve()!=venv.resolve()
                            or value['prefix']==value['base_prefix']):raise SkillRuntimeError()
    return value


class SkillPythonRuntime:
    def __init__(self, root: Path, approval: dict, *, project: Path=PROJECT):
        # Trusted server/operator construction only, never request fields.
        self.root,self.project=root.absolute(),project.absolute()
        self.approval=dict(approval)

    def contract(self):
        descriptor_path=self.project/DESCRIPTOR
        raw=descriptor_path.read_bytes();descriptor=json.loads(raw)
        if sha(raw)!=self.approval['descriptor_sha256']:raise SkillRuntimeError()
        if (descriptor['skill_slug']!='wechat-html-draft' or descriptor['skill_version']!='1.0.0'
                or descriptor['runtime_revision']!=1
                or descriptor['target']!=dict(os='linux',architecture='x86_64',implementation='cpython',
                     python='3.11',abi='cp311',glibc_min='2.28')):raise SkillRuntimeError()
        if git_identity(self.project)!={key:self.approval[key] for key in ('source_commit','source_tree')}:
            raise SkillRuntimeError()
        for name,digest in descriptor['adapter_files'].items():
            path=self.project/name
            if path.is_symlink() or '..' in Path(name).parts or not path.resolve().is_relative_to(self.project.resolve()):
                raise SkillRuntimeError()
            if sha(path.read_bytes())!=digest:raise SkillRuntimeError()
        if sha(canonical(descriptor['adapter_files']))!=self.approval['adapter_digest']:
            raise SkillRuntimeError()
        lock_path=self.project/descriptor['lock_file'];lock_raw=lock_path.read_bytes()
        if sha(lock_raw)!=descriptor['lock_sha256'] or sha(lock_raw)!=self.approval['lock_sha256']:
            raise SkillRuntimeError()
        lock=json.loads(lock_raw)
        requirement_path=self.project/'integrations'/lock['requirements_file']
        requirements=requirement_path.read_bytes()
        expected=''.join(f"{p['package']}=={p['version']} --hash=sha256:{p['sha256']}\n"
                         for p in sorted(lock['packages'],key=lambda p:p['package'])).encode()
        if (lock['target']!=descriptor['target'] or requirements!=expected
                or sha(requirements)!=lock['requirements_sha256']):raise SkillRuntimeError()
        packages={p['package']:p['version'] for p in lock['packages']}
        if len(packages)!=len(lock['packages']) or any(not re.fullmatch('[a-f0-9]{64}',p['sha256'])
                or not p['filename'].endswith('.whl') or Path(p['filename']).name!=p['filename']
                or not p['official_sha_verified'] for p in lock['packages']):raise SkillRuntimeError()
        # Input and all 13 Source checksums remain the original Native V1 contract.
        native=read_json(self.project/'integrations/wechat-html-draft.revision.v1.json')
        if native['artifact_sha256']!=descriptor['artifact_sha256'] or native['artifact_sha256']!=self.approval['artifact_sha256']:
            raise SkillRuntimeError()
        for item in native['files']:
            path=self.project/native['source_root']/item['path']
            if path.is_symlink() or sha(path.read_bytes())!=item['sha256']:raise SkillRuntimeError()
        if sha((self.project/descriptor['artifact_file']).read_bytes())!=descriptor['artifact_sha256']:
            raise SkillRuntimeError()
        return descriptor,lock,packages

    def directory(self, revision):
        if (revision['slug']!='wechat-html-draft' or revision['version']!='1.0.0'
                or revision['status']!='published'
                or str(uuid.UUID(revision['id']))!=revision['id']):raise SkillRuntimeError()
        path=self.root/revision['slug']/revision['id']
        if self.approval['environment']!='test' or self.root!=Path(self.approval['managed_root']).absolute():
            raise SkillRuntimeError()
        # No venv in Source, Skill installation or Task directories. Root is
        # provisioned independently by the operator under shared/runtime/skills.
        if (self.root.resolve().is_relative_to(self.project.parent.resolve())
                or any(p.is_symlink() for p in (*self.root.parents,self.root,path.parent,path))):
            raise SkillRuntimeError()
        if not path.resolve().is_relative_to(self.root.resolve()):raise SkillRuntimeError()
        return path

    def binding(self, revision, descriptor):
        if revision['checksum']!=descriptor['artifact_sha256']:raise SkillRuntimeError()
        return dict(skill_id=revision['skill_id'],skill_revision_id=revision['id'],
            skill_slug=revision['slug'],skill_version=revision['version'],
            runtime_revision=descriptor['runtime_revision'],approval=self.approval)

    def resolve(self, revision, action):
        try:
            if action not in ('PREPARE','CREATE_DRAFT'):raise SkillRuntimeError()
            descriptor,lock,packages=self.contract();directory=self.directory(revision)
            binding=self.binding(revision,descriptor);receipt=read_json(directory/'receipt/runtime.json')
            python=directory/'venv/bin/python'
            if (read_json(directory/'receipt/binding.json')!=binding
                    or receipt['binding']!=binding or receipt['creation_status']!='READY'
                    or receipt['venv_identity']!=sha(canonical(binding))
                    or receipt['lock_sha256']!=descriptor['lock_sha256']
                    or receipt['wheel_hashes']!={p['filename']:p['sha256'] for p in lock['packages']}
                    or receipt['pip_check']!='PASS' or not python.is_file() or python.is_symlink()
                    or sha((directory/'lock/runtime-lock.json').read_bytes())!=descriptor['lock_sha256']
                    or sha((directory/'lock/requirements.txt').read_bytes())!=lock['requirements_sha256']
                    or file_tree_digest(directory/'venv')!=receipt['environment_files_sha256']):
                raise SkillRuntimeError()
            actual=probe(python,list(packages),directory/'venv')
            if (actual['packages']!=packages or set(actual['installed'])!=set(packages)
                    or actual['imports']!='PASS' or actual['python']!=receipt['python_version']):
                raise SkillRuntimeError()
            return python
        except SkillRuntimeError:raise
        except Exception:raise SkillRuntimeError() from None

    def install(self, revision, base_python, wheelhouse):
        """Explicit provisioning only; no API/Task calls this method."""
        try:
            descriptor,lock,packages=self.contract();directory=self.directory(revision)
            binding=self.binding(revision,descriptor)
            if directory.exists():
                self.resolve(revision,'PREPARE')
                return read_json(directory/'receipt/runtime.json')
            # Everything including platform + wheel hashes checked before writes.
            probe(base_python,[])
            if wheelhouse.is_symlink() or {p.name for p in wheelhouse.iterdir()}!={p['filename'] for p in lock['packages']}:
                raise SkillRuntimeError()
            for item in lock['packages']:
                wheel=wheelhouse/item['filename']
                if wheel.is_symlink() or sha(wheel.read_bytes())!=item['sha256']:raise SkillRuntimeError()
            directory.mkdir(parents=True,mode=0o700)
            (directory/'lock').mkdir();(directory/'receipt').mkdir()
            (directory/'receipt/binding.json').write_bytes(canonical(binding))
            (directory/'lock/runtime-lock.json').write_bytes((self.project/descriptor['lock_file']).read_bytes())
            (directory/'lock/requirements.txt').write_bytes((self.project/'integrations'/lock['requirements_file']).read_bytes())
            # No system-site packages, no bundled pip/setuptools in Skill venv.
            run([base_python,'-I','-B','-m','venv','--copies','--without-pip',directory/'venv'])
            python=directory/'venv/bin/python'
            pip=[base_python,'-I','-B','-m','pip','--isolated','--disable-pip-version-check','--python',python]
            run(pip+['install','--no-index','--no-cache-dir','--no-deps','--require-hashes',
                     '--only-binary=:all:','--find-links',wheelhouse,'-r',directory/'lock/requirements.txt'])
            run(pip+['check'])
            actual=probe(python,list(packages),directory/'venv')
            if actual['packages']!=packages or set(actual['installed'])!=set(packages):raise SkillRuntimeError()
            receipt=dict(binding=binding,skill_key=revision['slug'],revision=revision['version'],
                python_version=actual['python'],python_abi=actual['abi'],target=descriptor['target'],
                lock_sha256=descriptor['lock_sha256'],package_versions=packages,
                wheel_hashes={p['filename']:p['sha256'] for p in lock['packages']},
                venv_identity=sha(canonical(binding)),creation_status='READY',pip_check='PASS',imports='PASS',
                environment_files_sha256=file_tree_digest(directory/'venv'),
                created_at=datetime.now(timezone.utc).isoformat())
            (directory/'receipt/runtime.json').write_bytes(canonical(receipt))
            self.resolve(revision,'PREPARE')
            return receipt
        except SkillRuntimeError:raise
        except Exception:raise SkillRuntimeError() from None


def source_seal(project=PROJECT):
    """Post-commit attestation; external to the sealed commit to avoid recursion."""
    descriptor=read_json(project/DESCRIPTOR)
    value=git_identity(project)
    value.update(descriptor_sha256=sha((project/DESCRIPTOR).read_bytes()),
        lock_sha256=descriptor['lock_sha256'],artifact_sha256=descriptor['artifact_sha256'],
        adapter_digest=sha(canonical(descriptor['adapter_files'])),environment='test',
        managed_root='/opt/enterprise-agent-workbench-test/shared/runtime/skills')
    # Ensure approved files really are committed, not unstaged/staged replacements.
    paths=[str(Path('enterprise_agent_poc')/name) for name in descriptor['adapter_files']]
    paths.append(str(Path('enterprise_agent_poc')/DESCRIPTOR))
    run(['git','diff','--exit-code','HEAD','--',*paths],cwd=project.parent)
    listed=run(['git','ls-files','--',*paths],cwd=project.parent).decode().splitlines()
    if set(listed)!=set(path.replace('\\','/') for path in paths):raise SkillRuntimeError()
    SkillPythonRuntime(project.parent.parent/'unprovisioned-skill-runtime',value,project=project).contract()
    return value
