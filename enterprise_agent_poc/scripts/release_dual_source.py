"""Protected V1 binding of the existing Release helpers, never a path override.

Only the installed tooling location selects Production or the independent
rehearsal realm. No env/request/CLI value selects tooling or grants authority.
Historical helpers outside these locations keep their original ROOT behavior.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

VERSION = 'FORMAL_DUAL_SOURCE_RELEASE_BINDING_V1'
APP_SOURCE = 'f8b90319abd8afe95a5de2a7b91cf9c8768e67d3'
APP_TREE = 'ca77d553cc01750389d0f8ea34426cc38821fec1'
PRODUCTION = Path('/opt/enterprise-agent-workbench')
ISOLATED = Path('/opt/enterprise-agent-schema016-isolated')
TOOL = Path(__file__).resolve().parents[2]
CODE = ('deploy/release_switch.sh', 'scripts/release_dual_source.py',
        'scripts/release_schema016.py', 'scripts/release_verify.py',
        'scripts/release_manifest.py', 'scripts/build_release.py',
        'scripts/release_migration_transition.py', 'scripts/rollback_preflight.py',
        'scripts/release_runtime_recovery.py')


class BindingBlocked(ValueError):
    pass


def require(value, code):
    if not value:
        raise BindingBlocked(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), default=str).encode()).hexdigest()


def protected(path, *, directory=False):
    for p in (path, *path.parents):
        info = p.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == info.st_gid == 0
                and not info.st_mode & 0o022, 'dual_source_path_trust')
    mode = path.lstat().st_mode
    require(stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode), 'dual_source_regular_path')
    if not directory:
        require(not mode & 0o222, 'dual_source_readonly_file')


def read(path):
    protected(path)
    require(path.stat().st_size <= 4_000_000, 'dual_source_document_size')
    def unique(pairs):
        result = {}
        for k, v in pairs:
            require(k not in result, 'dual_source_duplicate_key')
            result[k] = v
        return result
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def git(*args):
    return subprocess.check_output(['/usr/bin/git', '--no-optional-locks', '-c',
        'safe.directory='+str(TOOL), '-C', str(TOOL), *args], timeout=30,
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null'})


def git_map(source):
    result = {}
    for record in git('ls-tree', '-r', '-z', source).split(b'\0'):
        if not record: continue
        header, raw_name = record.split(b'\t', 1)
        mode, kind, oid = header.decode().split()
        name = raw_name.decode('utf-8'); p = PurePosixPath(name)
        require(kind == 'blob' and mode in {'100644', '100755'} and not p.is_absolute()
                and '..' not in p.parts and '\\' not in name, 'dual_source_git_file_shape')
        result[name] = dict(sha256=hashlib.sha256(git('cat-file', 'blob', oid)).hexdigest(), mode=mode)
    return result


def profile():
    for base, env, realm in ((PRODUCTION, 'production', 'PRODUCTION'),
                            (ISOLATED, 'test', 'ISOLATED_RELEASE_VALIDATION')):
        if TOOL.parent == base/'shared/release-tooling':
            return dict(base=base, environment=env, realm=realm,
                authority=base/'shared/release-authority/schema016',
                prefix='enterprise-agent-' if base == PRODUCTION else 'enterprise-agent-schema016-isolated-',
                api_port=18090 if base == PRODUCTION else 28160)
    return None


def runtime_trust(base):
    # Root startup hooks must never import a service-user-writable Python lib.
    # Interpreter/stdlib/pth destinations are installed by release control,
    # not by this loader. No pip install or chmod occurs here.
    import sysconfig
    for root in (base/'venv',Path(sysconfig.get_path('stdlib'))):
        protected(root, directory=True)
        for item in root.rglob('*'):
            target=item.resolve()
            for p in (target,*target.parents):
                info=p.lstat()
                require(info.st_uid==info.st_gid==0 and not info.st_mode & 0o022,
                        'dual_source_python_runtime_trust')
    for raw in sys.path:
        path=Path(raw or '.').resolve()
        for p in (path,*path.parents):
            if p.exists():
                info=p.stat()
                require(info.st_uid==info.st_gid==0 and not info.st_mode & 0o022,
                        'dual_source_python_import_path_trust')


def load():
    p = profile()
    require(p is not None, 'dual_source_formal_install_location')
    protected(TOOL, directory=True)
    b = read(p['authority']/'binding.v1.json')
    approval = read(p['authority']/'approval.v1.json')
    require(set(b) == {'contract', 'environment', 'realm', 'application', 'tooling',
        'manifest_sha256', 'raw_manifest_sha256', 'exact_predecessor', 'recovery_contract',
        'code_pins', 'schema_catalogs', 'runtime_inputs'}, 'dual_source_binding_shape')
    require(b['contract'] == VERSION and b['environment'] == p['environment']
            and b['realm'] == p['realm'], 'dual_source_binding_realm')
    runtime_trust(p['base'])
    require(set(approval) == {'contract', 'binding_sha256', 'authorization', 'production_authority'}
        and approval['contract'] == VERSION and approval['binding_sha256'] == digest(b)
        and approval['authorization'] == ('PRODUCTION_DUAL_SOURCE_RELEASE_APPROVED' if p['base'] == PRODUCTION
            else 'BOUNDED_RELEASE_ENTRY_SCHEMA016_IMPLEMENTATION_APPROVED')
        and approval['production_authority'] is (p['base'] == PRODUCTION), 'dual_source_independent_approval')
    for side in ('application', 'tooling'):
        entry = b[side]
        require(set(entry) == {'source', 'tree', 'path', 'git_files'}, 'dual_source_identity_shape')
        require(all(isinstance(entry[k], str) and re.fullmatch('[a-f0-9]{40}', entry[k])
                    for k in ('source', 'tree')), 'dual_source_exact_git_identity')
        require(git('rev-parse', entry['source']+'^{tree}').decode().strip() == entry['tree'],
                'dual_source_git_tree')
        files = git_map(entry['source'])
        expected = files if side == 'tooling' else {k: v for k,v in files.items() if k in entry['git_files']}
        require(bool(expected) and entry['git_files'] == expected, 'dual_source_git_file_map')
    require((b['application']['source'], b['application']['tree']) == (APP_SOURCE, APP_TREE),
            'dual_source_application_identity')
    require(Path(b['tooling']['path']) == TOOL and TOOL.name == b['tooling']['source']
        and git('rev-parse', 'HEAD').decode().strip() == b['tooling']['source']
        and not git('status', '--porcelain', '--untracked-files=no').strip(), 'dual_source_tooling_identity')
    files = b['tooling']['git_files']
    actual = {}
    for item in TOOL.rglob('*'):
        relative = item.relative_to(TOOL)
        protected(item, directory=item.is_dir())
        if '.git' in relative.parts: continue
        if item.is_file():
            actual[relative.as_posix()] = dict(sha256=hashlib.sha256(item.read_bytes()).hexdigest(),
                mode='100755' if item.stat().st_mode & 0o111 else '100644')
    require(actual == files, 'dual_source_installed_tooling_bytes')
    require(set(b['code_pins']) == set(CODE) and all(
        b['code_pins'][name] == files['enterprise_agent_poc/'+name]['sha256'] for name in CODE),
        'dual_source_code_pins')
    root = Path(b['application']['path'])
    require(root.name == 'enterprise_agent_poc' and root.parent.parent == p['base']/'releases'
            and root.resolve() == root, 'dual_source_application_install_path')
    protected(root, directory=True)
    manifest_path=root.parent/(root.parent.name+'.manifest.json')
    manifest=read(manifest_path)
    require(digest(manifest)==b['manifest_sha256'] and
        hashlib.sha256(manifest_path.read_bytes()).hexdigest()==b['raw_manifest_sha256']
        and manifest['source_commit']==APP_SOURCE and manifest['release_id']==root.parent.name,
        'dual_source_manifest_identity')
    require(not (root.parent/'.env').exists() and not (root.parent/'.env').is_symlink(), 'dual_source_unapproved_env')
    require(set(b['application']['git_files'])==set(manifest['selected_files']), 'dual_source_application_file_set')
    # Check imported business bytes before importing ANY Application module.
    # Only static index is an approved build transformation, checked in bound().
    for name,pin in b['application']['git_files'].items():
        path=root.parent/name
        require(PurePosixPath(name).parts[0]=='enterprise_agent_poc' and path.resolve().is_relative_to(root),
                'dual_source_application_file_scope')
        protected(path)
        if name!='enterprise_agent_poc/app/static/index.html':
            require(hashlib.sha256(path.read_bytes()).hexdigest()==pin['sha256'], 'dual_source_application_git_bytes')
    actual_files={x.relative_to(root.parent).as_posix() for x in root.rglob('*') if x.is_file()}
    require(actual_files==set(b['application']['git_files']), 'dual_source_application_extra_files')
    require(b['recovery_contract'] == 'SCHEMA016_FORWARD_COMPATIBLE_RUNTIME_V1', 'dual_source_recovery_contract')
    require(set(b['schema_catalogs']) == {'015', '016'} and all(
        isinstance(v,str) and re.fullmatch('[a-f0-9]{64}',v) for v in b['schema_catalogs'].values()),
        'dual_source_catalog_pins')
    inputs = b['runtime_inputs']
    require(set(inputs) == {'environment_file', 'environment_sha256', 'database_identity', 'redis_identity',
        'dependency_snapshot_sha256', 'skill_runtime_sha256'}, 'dual_source_runtime_input_shape')
    require(inputs['environment_file'] == str(p['base']/'shared/enterprise-agent.env'), 'dual_source_environment_path')
    # Env is existing managed secret input: exact hash/metadata, NEVER print it.
    env = Path(inputs['environment_file'])
    for item in (env, *env.parents):
        info=item.lstat()
        require(info.st_uid == info.st_gid == 0 and not stat.S_ISLNK(info.st_mode)
                and not info.st_mode & 0o022, 'dual_source_environment_trust')
    require(env.is_file() and stat.S_IMODE(env.stat().st_mode)==0o600
        and hashlib.sha256(env.read_bytes()).hexdigest()==inputs['environment_sha256'], 'dual_source_environment_identity')
    require(all(re.fullmatch('[a-f0-9]{64}',inputs[k]) for k in (
        'dependency_snapshot_sha256','skill_runtime_sha256')), 'dual_source_dependency_identity')
    return b, p


def application_root(default):
    if profile() is None: return default
    b, _ = load()
    root = Path(b['application']['path'])
    # Imports of business modules use only the checked Application. Release
    # scripts use only this checked Tooling namespace, not caller PYTHONPATH.
    sys.path.insert(0, str(root))
    import scripts
    scripts.__path__ = [str(TOOL/'enterprise_agent_poc/scripts')]
    return root


def inputs_verified(binding, p):
    import importlib.metadata as metadata
    effective={name:metadata.version(name) for name in sorted({d.metadata['Name'] for d in metadata.distributions()})}
    inputs=binding['runtime_inputs']
    from urllib.parse import urlparse,parse_qs
    require(os.environ.get('APP_ENV')==p['environment'], 'dual_source_effective_environment')
    database=os.environ.get('ENTERPRISE_POC_DATABASE_URL','')
    require(hashlib.sha256(database.encode()).hexdigest()==inputs['database_identity']['dsn_sha256'],
            'dual_source_effective_database')
    if p['base']==ISOLATED:
        uri=urlparse(database); query=parse_qs(uri.query)
        require(uri.scheme=='postgresql' and not uri.netloc and uri.path.startswith('/schema016_native_')
            and len(query.get('host',[]))==1 and Path(query['host'][0]).resolve().is_relative_to(p['base']/'shared/test-data'),
            'dual_source_isolated_database_scope')
        redis=urlparse(os.environ.get('REDIS_URL',''))
        require(redis.scheme=='unix' and not redis.netloc and Path(redis.path).resolve().is_relative_to(p['base']/'shared/test-data')
            and parse_qs(redis.query).get('db')==['0'] and
            os.environ.get('ENTERPRISE_POC_TASK_QUEUE_NAMESPACE','').startswith('schema016-native-'),
            'dual_source_isolated_redis_scope')
    require(digest(effective)==inputs['dependency_snapshot_sha256'], 'dual_source_actual_dependencies')
    root=Path(binding['application']['path'])
    skill={name:hashlib.sha256((root/'integrations'/name).read_bytes()).hexdigest() for name in (
        'wechat-python-runtime.v1.json','wechat-python311-linux.v1.lock.json','artifacts/wechat-html-draft-1.0.0.zip')}
    require(digest(skill)==inputs['skill_runtime_sha256'], 'dual_source_skill_dependency_inputs')
    from redis import Redis
    client=Redis.from_url(os.environ['REDIS_URL'],socket_timeout=3,socket_connect_timeout=3,decode_responses=True)
    try:
        redis=inputs['redis_identity']
        require(set(redis)=={'url_sha256','server_version','namespace'} and
            hashlib.sha256(os.environ['REDIS_URL'].encode()).hexdigest()==redis['url_sha256']
            and client.ping() and client.info('server')['redis_version']==redis['server_version']
            and os.environ['ENTERPRISE_POC_TASK_QUEUE_NAMESPACE']==redis['namespace'], 'dual_source_actual_redis')
    finally: client.close()
    # This is NOT a Revision runtime seal or CREATE_DRAFT execution authority.
    return dict(dependencies='PASS', skill_dependency_inputs='PASS', skill_execution_authority=False)


def bound(manifest):
    from scripts.release_schema016 import PREDECESSOR, declaration
    b, p = load()
    inputs_verified(b,p)
    require(b['exact_predecessor'] == PREDECESSOR, 'dual_source_exact_predecessor')
    require(manifest['source_commit'] == APP_SOURCE and digest(manifest) == b['manifest_sha256'],
            'dual_source_manifest_identity')
    root = Path(b['application']['path'])
    path = root.parent/(manifest['release_id']+'.manifest.json')
    protected(path)
    require(hashlib.sha256(path.read_bytes()).hexdigest() == b['raw_manifest_sha256'], 'dual_source_raw_manifest')
    require(manifest['dual_source_release'] == declaration(b['tooling']['source'], b['tooling']['tree']),
            'dual_source_manifest_pair')
    from scripts.rollback_preflight import release_identity
    installed, _ = release_identity(p['base'], manifest['release_id'], APP_SOURCE)
    require(installed == root, 'dual_source_archive_application')
    require(set(b['application']['git_files']) == set(manifest['selected_files']), 'dual_source_application_file_set')
    from scripts import build_release
    old_repo = build_release.REPO
    try:
        build_release.REPO = TOOL
        index = build_release._static_asset_contract(APP_SOURCE)['packaged_index']
    finally: build_release.REPO = old_repo
    for name, pin in b['application']['git_files'].items():
        path = root.parent/name; protected(path)
        content = path.read_bytes()
        require(content == index if name == 'enterprise_agent_poc/app/static/index.html' else
                hashlib.sha256(content).hexdigest() == pin['sha256'], 'dual_source_application_git_bytes')
    return b, p


if __name__ == '__main__':
    try:
        require(len(sys.argv)==3 and sys.argv[1]=='shell-context' and
            re.fullmatch('[A-Za-z0-9._-]+',sys.argv[2]), 'dual_source_context_command')
        b,p=load(); root=Path(b['application']['path'])
        require(root.parent.name==sys.argv[2] and os.geteuid()==0, 'dual_source_context_release')
        # Values come from fixed profiles, not shell eval or supplied paths.
        print('\n'.join(map(str,(p['base'],root,TOOL/'enterprise_agent_poc',p['prefix'],
            p['api_port'],p['environment']))))
    except Exception as exc:
        print(json.dumps(dict(status='BLOCKED',check=str(exc) if isinstance(exc,BindingBlocked)
            else 'dual_source_input_or_io')),file=sys.stderr)
        raise SystemExit(2)
