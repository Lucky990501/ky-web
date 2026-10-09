"""Versioned binding for the existing forward recovery operator.

No installer, Source switch, permission issuer, DB restore or fallback. Root
release control approves this document after the existing Native authority.
The installed Application location, not a CLI profile, selects the realm.
"""
import hashlib
from pathlib import Path
import re
import stat

from scripts.wechat_runtime_test_lifecycle_guard import need

VERSION = 'FORMAL_RUNTIME_FORWARD_RECOVERY_BINDING_V1'
PRIMARY = Path('/etc/enterprise-agent-test-exact-admin-v1')
ISOLATED = Path('/etc/enterprise-agent-native-isolated-v1')
APP_SOURCE = '28e061a114118a27609330125c756e193d3248a6'
APP_TREE = '8ff5e4a8ed26c32ba040b4fa27cf1d380f3c7541'
ROLES = ('api', 'mcp', 'worker')
CODE = ('runtime_recovery_operator.py', 'runtime_recovery_binding.py')


def profile(root):
    need(root in (PRIMARY, ISOLATED), 'RECOVERY_AUTHORITY_ROOT')
    primary = root == PRIMARY
    return dict(root=root, primary=primary,
        units={r:('enterprise-agent-test-' if primary else 'enterprise-agent-native-isolated-')+r+'.service' for r in ROLES},
        home=Path('/opt/enterprise-agent-workbench-test' if primary else '/opt/enterprise-agent-native-isolated'),
        api=18100 if primary else 28100, mcp=18101 if primary else 28101,
        slice='enterprise-agent-test.slice' if primary else 'enterprise-agent-native-isolated.slice',
        realm='PRIMARY_TEST' if primary else 'ISOLATED_NATIVE_TEST_CONTEXT')


def shape(policy, approval, policy_sha, pair, pair_sha, root):
    p = profile(root)
    need(set(policy) == {'contract', 'realm', 'environment', 'production_authority', 'pair_sha256',
        'native_pins', 'services', 'python', 'post_write_recovery', 'predecessor_rollback'}, 'RECOVERY_BINDING_SHAPE')
    need(policy['contract'] == VERSION and policy['realm'] == p['realm']
        and policy['environment'] == 'test' and policy['production_authority'] is False
        and policy['post_write_recovery'] == 'EXACT_CURRENT_PAIR_ONLY'
        and policy['predecessor_rollback'] is False, 'RECOVERY_BINDING_DOMAIN')
    need((pair['application']['source'], pair['application']['tree']) == (APP_SOURCE, APP_TREE),
        'RECOVERY_ATOMIC_APPLICATION_REQUIRED')
    need(policy['pair_sha256'] == pair_sha, 'RECOVERY_PAIR_PIN')
    need(set(approval) == {'contract', 'policy_sha256', 'authorization', 'production_authority', 'code_sha256'}
        and approval['contract'] == VERSION and approval['policy_sha256'] == policy_sha
        and approval['authorization'] == 'FORMAL_PRIMARY_FORWARD_RECOVERY_SOURCE_FIX_APPROVED'
        and approval['production_authority'] is False and set(approval['code_sha256']) == set(CODE),
        'RECOVERY_INDEPENDENT_APPROVAL')
    need(set(policy['native_pins']) == {'runtime-pair.v1.json', 'approval.v2.json',
        'scope.v1.json', 'native-policy.v2.json', 'native-approval.v2.json'}, 'RECOVERY_NATIVE_PIN_SET')
    need(set(policy['services']) == set(ROLES), 'RECOVERY_SERVICE_SET')
    need(isinstance(policy['python'], str) and policy['python'].startswith('/')
        and not re.search(r'\s', policy['python']), 'RECOVERY_PYTHON_PATH')
    if p['primary']:
        need(policy['python'] == str(p['home']/'shared/runtime/python311/bin/python'), 'RECOVERY_PRIMARY_PYTHON')
    for role, entry in policy['services'].items():
        need(set(entry) == {'fragment_sha256', 'dropins', 'environment_files'}, 'RECOVERY_UNIT_SHAPE')
        need(isinstance(entry['dropins'], dict) and isinstance(entry['environment_files'], list)
            and bool(entry['environment_files']), 'RECOVERY_UNIT_INPUTS')
        for path in entry['dropins']:
            location = Path(path)
            need(location.parent in (Path('/etc/systemd/system')/(p['units'][role]+'.d'),
                Path('/run/systemd/system.control')/(p['units'][role]+'.d'))
                and location.suffix == '.conf', 'RECOVERY_DROPIN_SCOPE')
        for item in entry['environment_files']:
            need(set(item) == {'path', 'sha256', 'uid', 'gid', 'mode'}, 'RECOVERY_ENV_SHAPE')
            path = Path(item['path'])
            allowed = (path.parent in (p['home']/'shared/config', p['home']/'shared/credentials')
                       if p['primary'] else path == root/'service.env')
            need(allowed and item['uid'] in (0, 1000) and item['gid'] in (0, 1000)
                and item['mode'] in (0o400, 0o440, 0o444, 0o600, 0o640), 'RECOVERY_ENV_SCOPE')
    return p


def pinned_file(path, sha, *, metadata=None):
    # Existing formal unit files are root-owned 0644, not executable authority.
    # Existing env files may be lucky-owned 0600/0640. Their exact bytes AND
    # declared ownership are approved; no shell sourcing of user-writable code.
    for item in (path, *path.parents):
        info = item.lstat()
        need(not stat.S_ISLNK(info.st_mode) and not info.st_mode & 0o022
            and info.st_uid in ((0, 1000) if metadata else (0,))
            and info.st_gid in ((0, 1000) if metadata else (0,)), 'RECOVERY_FILE_TRUST')
    info = path.stat()
    need(stat.S_ISREG(info.st_mode) and info.st_size <= 1048576, 'RECOVERY_REGULAR_FILE')
    if metadata:
        need((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
            (metadata['uid'], metadata['gid'], metadata['mode']), 'RECOVERY_ENV_METADATA')
    need(hashlib.sha256(path.read_bytes()).hexdigest() == sha, 'RECOVERY_FILE_SHA')


def argv(value):
    values = re.findall(r'argv\[\]=(.*?)\s*; ignore_errors=no', value)
    need(len(values) == 1, 'RECOVERY_SINGLE_SERVICE_HOOK_REQUIRED')
    return values[0].strip().split()


def validate_loaded(policy, p, pair, systemctl, *, verify_environment=True):
    script = str(Path(pair['tooling']['path'])/'enterprise_agent_poc/scripts/runtime_recovery_operator.py')
    for role, unit in p['units'].items():
        entry = policy['services'][role]; fragment = Path('/etc/systemd/system')/unit
        pinned_file(fragment, entry['fragment_sha256'])
        need(systemctl('show', unit, '-p', 'FragmentPath', '--value') == str(fragment), 'RECOVERY_UNIT_ORIGIN')
        paths = systemctl('show', unit, '-p', 'DropInPaths', '--value').split()
        need(len(paths) == len(entry['dropins']) and set(paths) == set(entry['dropins']), 'RECOVERY_EFFECTIVE_DROPINS')
        for path in paths: pinned_file(Path(path), entry['dropins'][path])
        for key, expected in {'User':'lucky', 'Group':'lucky', 'Slice':p['slice'], 'KillMode':'control-group',
            'WorkingDirectory':str(Path(pair['application']['path'])/'enterprise_agent_poc')}.items():
            need(systemctl('show', unit, '-p', key, '--value') == expected, 'RECOVERY_SERVICE_BINDING:'+key)
        for phase in ('pre', 'post'):
            need(argv(systemctl('show', unit, '-p', 'ExecStart'+phase.title(), '--value')) ==
                [policy['python'], '-B', script, phase, role], 'RECOVERY_COMPATIBLE_STARTUP_HOOK')
        entrypoint = {'api':['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(p['api'])],
            'mcp':['-m','app.platform_mcp.server'], 'worker':['-m','app.worker']}[role]
        need(argv(systemctl('show', unit, '-p', 'ExecStart', '--value')) ==
            [policy['python'], '-B', *entrypoint], 'RECOVERY_APPLICATION_ENTRYPOINT')
        raw = systemctl('show', unit, '-p', 'EnvironmentFiles', '--value')
        expected = ' '.join(x['path']+' (ignore_errors=no)' for x in entry['environment_files'])
        need(raw == expected, 'RECOVERY_ORDERED_ENVIRONMENT_FILES')
        if verify_environment:
            for item in entry['environment_files']:
                pinned_file(Path(item['path']), item['sha256'], metadata=item)


def load(pair, pair_sha, root, systemctl, *, verify_environment=True):
    from app.test_tenant_seeding import native_json
    from scripts.wechat_runtime_native_successor import native_file
    policy, sha = native_json(root/'forward-recovery-policy.v1.json')
    approval, _ = native_json(root/'forward-recovery-approval.v1.json')
    p = shape(policy, approval, sha, pair, pair_sha, root)
    scripts = Path(pair['tooling']['path'])/'enterprise_agent_poc/scripts'
    need(scripts.resolve() == Path(__file__).resolve().parent, 'RECOVERY_LOADED_TOOLING')
    for name, pin in approval['code_sha256'].items(): native_file(scripts/name, pin)
    for name, pin in policy['native_pins'].items(): native_file(root/name, pin)
    if p['primary']:
        # No Source switch is performed here. The independently approved release
        # install must already have selected the exact atomic Application.
        need((p['home']/'current').resolve() == Path(pair['application']['path']), 'RECOVERY_CURRENT_APPLICATION')
    validate_loaded(policy, p, pair, systemctl, verify_environment=verify_environment)
    return p, policy
