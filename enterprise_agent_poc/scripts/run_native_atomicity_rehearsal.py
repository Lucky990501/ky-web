"""Local Linux native rehearsal; never an installer for PRIMARY.

Private network namespace, marked PG16.6 cluster, root-owned source checkouts,
and distinct systemd units. This harness supplies no validation callbacks.
All status/prepare/grant/recover calls execute the real sealed CLI processes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path('/opt/enterprise-agent-native-isolated')
AUTH = Path('/etc/enterprise-agent-native-isolated-v1')
NS = 'enterprise-agent-native-isolated-v1'
PY = Path('/home/lucky/ky-web-release-test/enterprise_agent_poc/.venv/bin/python')
PG = Path('/home/lucky/.cache/enterprise-agent-test-runtime/postgresql-16.6/bin')
REDIS = Path('/home/lucky/.cache/enterprise-agent-test-runtime/redis-7.0.15/bin/redis-server')
REPO = None
EXACT_PAIR = None



def run(argv, *, check=True, env=None):
    result = subprocess.run([str(x) for x in argv], check=False, env=env, capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        # Only synthetic diagnostics are read. Environment, session, config
        # and login response are never included in failures.
        print(json.dumps({'command': Path(str(argv[-1])).name, 'exit': result.returncode,
                          'diagnostic': result.stderr[-2500:]}), flush=True)
        raise RuntimeError('COMMAND_FAILED')
    return result


def ns(argv, **kw): return run(['/usr/sbin/ip', 'netns', 'exec', NS, *argv], **kw)
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path, value, mode=0o444):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode() if isinstance(value, dict) else value.encode()
    with path.open('xb') as f: f.write(raw)
    path.chmod(mode)
    return digest(path)


def stage(role, source, tree):
    destination = ROOT/('releases/app' if role == 'application' else 'shared/source-qualifications/tool')
    destination.mkdir(parents=True); os.chown(destination, 1000, 1000)
    run(['/usr/sbin/runuser', '-u', 'lucky', '--', 'git', '-c', 'core.autocrlf=false',
         'clone', '--no-local', '--no-checkout', REPO, destination])
    for item in (destination, *destination.rglob('*')):
        os.chown(item, 0, 0)
    run(['git', '-C', destination, 'checkout', '--detach', source])
    identity = run(['git', '-C', destination, 'rev-parse', 'HEAD', 'HEAD^{tree}', 'HEAD^']).stdout.splitlines()
    parent = ('d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7' if role == 'application'
              else '1e11e1a4835a465963ec5bd88a5ed40d4b300086')
    assert identity == [source, tree, parent], 'EXACT_SOURCE_TREE_PARENT_REQUIRED'
    assert not run(['git','-C',destination,'status','--porcelain']).stdout
    for p in destination.rglob('*'):
        assert not p.is_symlink()
        p.chmod(0o755 if p.is_dir() else 0o555 if p.stat().st_mode & 0o111 else 0o444)
    destination.chmod(0o755)
    return dict(path=str(destination), source=source, tree=tree)


def setup():
    assert os.geteuid() == 0 and not ROOT.exists() and not AUTH.exists()
    assert run([PG/'postgres', '--version']).stdout.strip() == 'postgres (PostgreSQL) 16.6'
    assert Path('/proc/1/comm').read_text().strip() == 'systemd'
    run(['/usr/sbin/ip', 'netns', 'add', NS])
    ns(['/usr/sbin/ip', 'link', 'set', 'lo', 'up'])
    ROOT.mkdir(mode=0o755); AUTH.mkdir(mode=0o755)
    write(ROOT/'ISOLATED_NATIVE_REHEARSAL.marker', 'NATIVE_PARENT_CHAIN_ISOLATION_REFACTOR_APPROVED\n')
    for name in ('state', 'private', 'logs'):
        path = ROOT/name; path.mkdir(mode=0o700)
        if name != 'private': os.chown(path, 1000, 1000)
    application = stage('application', *EXACT_PAIR['application'])
    tooling = stage('tooling', *EXACT_PAIR['tooling'])
    write(ROOT/'rehearsal-identities.json', dict(application=application, tooling=tooling,
        qualification='EXACT_COMMIT_NATIVE_REHEARSAL_NOT_PRIMARY_INSTALL'))
    pgdata = ROOT/'state/pg'; pgsocket = ROOT/'state/socket'; pgsocket.mkdir(); os.chown(pgsocket,1000,1000)
    ns(['/usr/sbin/runuser', '-u', 'lucky', '--', PG/'initdb', '-D', pgdata, '-U', 'native_isolated_owner',
        '-A', 'trust', '--encoding=UTF8', '--locale=C', '--no-instructions'])
    write(ROOT/'state/pg-runtime.conf', f"data_directory='{pgdata}'\nlisten_addresses='127.0.0.1'\nport=56432\nunix_socket_directories='{pgsocket}'\nmax_connections=30\nshared_buffers='16MB'\n")
    slice_text = '[Unit]\nDescription=Isolated Native Test Resource Boundary\n[Slice]\nCPUQuota=200%\nMemoryHigh=3G\nMemoryMax=4G\nTasksMax=512\n'
    write(Path('/etc/systemd/system/enterprise-agent-native-isolated.slice'), slice_text)
    unit_base = f'[Service]\nUser=lucky\nGroup=lucky\nSlice=enterprise-agent-native-isolated.slice\nNetworkNamespacePath=/run/netns/{NS}\nKillMode=control-group\nTimeoutStopSec=15\nNoNewPrivileges=yes\n'
    write(Path('/etc/systemd/system/enterprise-agent-native-isolated-pg.service'), unit_base+
        f'ExecStart={PG}/postgres -D {pgdata} -c config_file={ROOT}/state/pg-runtime.conf\n')
    write(Path('/etc/systemd/system/enterprise-agent-native-isolated-redis.service'), unit_base+
        f'ExecStart={REDIS} --bind 127.0.0.1 --port 56479 --save "" --appendonly no --dir {ROOT}/state\n')
    run(['systemctl','daemon-reload'])
    run(['systemctl','start','enterprise-agent-native-isolated-pg.service','enterprise-agent-native-isolated-redis.service'])
    import psycopg
    def sql(command):
        return ns([PG/'psql', '-h','127.0.0.1','-p','56432','-U','native_isolated_owner','-d','postgres','-v','ON_ERROR_STOP=1','-c',command])
    for i in range(40):
        ready = ns([PG/'pg_isready','-h','127.0.0.1','-p','56432'],check=False)
        if ready.returncode == 0: break
        time.sleep(.25)
    sql('CREATE ROLE native_isolated LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')
    sql('CREATE DATABASE native_isolated OWNER native_isolated')
    ns([PG/'psql','-h','127.0.0.1','-p','56432','-U','native_isolated_owner','-d','native_isolated',
        '-v','ON_ERROR_STOP=1','-c','CREATE EXTENSION vector; GRANT EXECUTE ON FUNCTION pg_control_system() TO native_isolated'])
    cluster = ns([PG/'psql','-h','127.0.0.1','-p','56432','-U','native_isolated_owner','-d','native_isolated','-Atc',
        'SELECT system_identifier FROM pg_control_system()']).stdout.strip()
    finish_setup(application, tooling, cluster)


def archive_attempt(name):
    assert name in ('attempt3', 'attempt4', 'attempt5', 'attempt6') or (name.startswith('atomic-') and name[7:].isalnum())
    assert ROOT.resolve() == ROOT and AUTH.resolve() == AUTH
    assert (ROOT/'ISOLATED_NATIVE_REHEARSAL.marker').read_text().strip() == 'NATIVE_PARENT_CHAIN_ISOLATION_REFACTOR_APPROVED'
    assert json.loads((AUTH/'issuance-request.v1.json').read_text())['contract'] == 'ISOLATED_NATIVE_TEST_CONTEXT'
    names = ['enterprise-agent-native-isolated-'+role+'.service' for role in ('api','mcp','worker','pg','redis')]
    run(['systemctl','stop',*names])
    for name_unit in names:
        assert run(['systemctl','show',name_unit,'-p','MainPID','--value']).stdout.strip() == '0'
    archived_root=ROOT.with_name(ROOT.name+'-'+name)
    archived_auth=AUTH.with_name(AUTH.name+'-'+name)
    assert not archived_root.exists() and not archived_auth.exists()
    ROOT.rename(archived_root); AUTH.rename(archived_auth)
    unit_archive=archived_auth/'unit-sources';unit_archive.mkdir()
    for unit in [*names, 'enterprise-agent-native-isolated.slice']:
        path=Path('/etc/systemd/system')/unit
        assert path.is_file() and not path.is_symlink()
        path.rename(unit_archive/unit)
    run(['systemctl','daemon-reload'])
    run(['/usr/sbin/ip','netns','delete',NS])
    print('ISOLATED_ATTEMPT_ARCHIVED_WITHOUT_DATA_DELETION',flush=True)


def finish_setup(application, tooling, cluster):
    existing = json.loads((AUTH/'issuance-request.v1.json').read_text()) if (AUTH/'issuance-request.v1.json').exists() else None
    run_id = existing['run_id'] if existing else str(uuid4())
    environment = dict(APP_ENV='test', PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=application['path']+'/enterprise_agent_poc:'+tooling['path']+'/enterprise_agent_poc',
        ENTERPRISE_POC_DATABASE_URL='postgresql://native_isolated@127.0.0.1:56432/native_isolated',
        ENTERPRISE_POC_DATA_DIR=str(ROOT/'state/data'), ENTERPRISE_POC_OBJECT_STORAGE_DIR=str(ROOT/'state/objects'),
        ENTERPRISE_POC_SECRET_BACKEND_DIR=str(ROOT/'state/empty-secret-backend'),
        ENTERPRISE_POC_OBJECT_STORAGE_PROVIDER='local', ENTERPRISE_POC_TASK_QUEUE='redis',
        ENTERPRISE_POC_TASK_QUEUE_NAMESPACE='native-isolated-'+run_id, REDIS_URL='redis://127.0.0.1:56479/0',
        ENTERPRISE_POC_MCP_URL='http://127.0.0.1:28101/mcp', ENTERPRISE_POC_MCP_PORT='28101',
        ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA='false', ENTERPRISE_POC_TEST_TENANT_SEEDING_POLICY_REQUIRED='false',
        ENTERPRISE_POC_EXACT_TEST_ADMIN_REQUIRED='true', ENTERPRISE_POC_MODEL_PROVIDER_ID='deepseek',
        ENTERPRISE_POC_MODEL_ID='deepseek-v4-pro', ENTERPRISE_POC_REASONING_EFFORT='high',
        ENTERPRISE_POC_CODEX_API_KEY_ENV='DEEPSEEK_API_KEY',
        ENTERPRISE_POC_MODEL_BASE_URL='http://127.0.0.1:9/disabled', ENTERPRISE_POC_WECHAT_NETWORK_ALLOWED='false',
        ENTERPRISE_POC_AGENT_RUNTIME_TEST_TENANT_ID='native-isolated-'+run_id,
        ENTERPRISE_POC_TOKEN_SECRET=secrets.token_urlsafe(48), GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null',
        GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='safe.directory', GIT_CONFIG_VALUE_0=application['path'],
        GIT_CONFIG_KEY_1='safe.directory', GIT_CONFIG_VALUE_1=tooling['path'])
    issuer = Path(tooling['path'])/'enterprise_agent_poc/scripts/prepare_exact_admin_authority.py'
    request = dict(contract='ISOLATED_NATIVE_TEST_CONTEXT',
        authorization='NATIVE_PARENT_CHAIN_ISOLATION_REFACTOR_APPROVED', application=application, tooling=tooling,
        database=dict(host='127.0.0.1', port=56432, name='native_isolated', role='native_isolated',
                      system_identifier=cluster, server_version=160006), run_id=run_id,
        production_authority=False, provider_budget=0, issuer_sha256=digest(issuer))
    if existing:
        assert existing == request
    else:
        write(AUTH/'issuance-request.v1.json', request)
    clean = {'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME':str(ROOT/'private'), **environment}
    if not (AUTH/'native-approval.v2.json').exists():
        issued = ns([PY,'-B',issuer], env=clean, check=False)
        attempt = str(time.time_ns())
        write(ROOT/'logs'/('issuance-'+attempt+'.stdout'), issued.stdout)
        write(ROOT/'logs'/('issuance-'+attempt+'.stderr'), issued.stderr)
        print(issued.stdout, flush=True)
        assert issued.returncode == 0, 'FORMAL_ISSUANCE_FAILED'
    for item in (ROOT/'state', *(ROOT/'state').rglob('*')):
        os.chown(item, 1000, 1000)
    environment['ENTERPRISE_POC_TEST_TENANT_SEEDING_POLICY_REQUIRED'] = 'true'
    if (AUTH/'service.env').exists():
        environment = dict(line.split('=',1) for line in (AUTH/'service.env').read_text().splitlines())
    env_text = ''.join(f'{k}={v}\n' for k,v in environment.items())
    env_sha = digest(AUTH/'service.env') if (AUTH/'service.env').exists() else write(AUTH/'service.env', env_text, 0o400)
    tool_scripts = Path(tooling['path'])/'enterprise_agent_poc/scripts'
    cli = tool_scripts/'run_exact_test_admin_lifecycle.py'
    entries = dict(api=f'{PY} -B -m uvicorn app.main:app --host 127.0.0.1 --port 28100',
                   mcp=f'{PY} -B -m app.platform_mcp.server', worker=f'{PY} -B -m app.worker')
    units = {}
    unit_base = f'[Service]\nUser=lucky\nGroup=lucky\nSlice=enterprise-agent-native-isolated.slice\nNetworkNamespacePath=/run/netns/{NS}\nKillMode=control-group\nTimeoutStopSec=15\nNoNewPrivileges=yes\n'
    for role, entry in entries.items():
        units[role] = write(Path('/etc/systemd/system')/f'enterprise-agent-native-isolated-{role}.service',
            unit_base+f'WorkingDirectory={application["path"]}/enterprise_agent_poc\nEnvironmentFile={AUTH}/service.env\n'
            f'ExecStartPre={PY} -B {cli} status --run-id {run_id}\nExecStart={entry}\n')
    write(AUTH/'startup-policy.v1.json', dict(contract='NATIVE_PROTECTED_FORWARD_RECOVERY_V1',
        pair_sha256=digest(AUTH/'runtime-pair.v1.json'), units=units, environment_sha256=env_sha,
        recovery_code_sha256=digest(tool_scripts/'runtime_recovery_operator.py')))
    run(['systemctl','daemon-reload'])
    result = ns([PY,'-B',cli,'status','--run-id',run_id], env={'PATH':os.environ['PATH'], **environment}, check=False)
    print(result.stdout, flush=True)
    write(ROOT/'logs/native-initial-status.json', result.stdout)
    assert result.returncode == 0, 'NATIVE_INITIAL_STATUS_FAILED'
    print('NATIVE_ISSUANCE_AND_STATUS_PASS', flush=True)


def main():
    global REPO, PY, PG, REDIS, EXACT_PAIR
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--application-source', required=True)
    parser.add_argument('--application-tree', required=True)
    parser.add_argument('--tooling-source', required=True)
    parser.add_argument('--tooling-tree', required=True)
    parser.add_argument('--python', required=True)
    parser.add_argument('--pg-bin', required=True)
    parser.add_argument('--redis', required=True)
    parser.add_argument('--evidence-dir', required=True)
    parser.add_argument('--cases', default='ABCDEFGR')
    args=parser.parse_args()
    assert os.geteuid()==0 and sys.dont_write_bytecode and os.environ.get('PYTHONDONTWRITEBYTECODE')=='1'
    assert args.cases and all(c in 'ABCDEFGR' for c in args.cases) and len(set(args.cases))==len(args.cases)
    EXACT_PAIR={r:(getattr(args,r+'_source'),getattr(args,r+'_tree')) for r in ('application','tooling')}
    assert all(re.fullmatch('[0-9a-f]{40}',v) for pair in EXACT_PAIR.values() for v in pair)
    REPO=Path(args.repository).resolve();PY=Path(args.python);PG=Path(args.pg_bin);REDIS=Path(args.redis)
    assert REPO.is_dir() and REPO.name=='.git'
    assert all(p.is_absolute() and p.exists() for p in (PY,PG,REDIS))
    destination=Path(args.evidence_dir)
    assert destination.is_absolute() and not destination.exists()
    destination.mkdir(parents=True)
    for case in args.cases:
        print('START_EXACT_NATIVE_CASE='+case,flush=True)
        if ROOT.exists():
            # An archived fixture is never resealed. Preserve its data/contracts.
            archive_attempt('atomic-'+str(time.time_ns()))
        setup()
        scripts=ROOT/'shared/source-qualifications/tool/enterprise_agent_poc/scripts'
        result=subprocess.run(['/usr/sbin/ip','netns','exec',NS,'env','PYTHONDONTWRITEBYTECODE=1',
            str(PY),'-B',str(scripts/'verify_runtime_atomicity_native.py'),'--scenario',case],
            text=True,capture_output=True)
        (destination/(case+'.stdout')).write_text(result.stdout)
        (destination/(case+'.stderr')).write_text(result.stderr)
        evidence=ROOT/'logs'/('atomicity-'+case+'.json')
        if evidence.exists():shutil.copyfile(evidence,destination/(case+'.json'))
        if result.returncode:
            print(result.stdout[-2000:],flush=True);print(result.stderr[-2000:],flush=True)
            raise SystemExit(result.returncode)
        value=json.loads(evidence.read_text())
        assert value['result']=='PASS' and value['final_admin_count']==0
        assert all((value['source_pair'][r]['source'],value['source_pair'][r]['tree'])==EXACT_PAIR[r] for r in EXACT_PAIR)
        print('EXACT_NATIVE_CASE_PASS='+case,flush=True)
    # Private mount-namespace mutations exercise the actual loader; the original
    # root-approved contract files are unchanged. No PRIMARY artifacts are used.
    try:
        run(['systemctl','start','enterprise-agent-native-isolated-pg.service','enterprise-agent-native-isolated-redis.service'])
        for _ in range(40):
            if ns([PG/'pg_isready','-h','127.0.0.1','-p','56432'],check=False).returncode==0:break
            time.sleep(.25)
        result=ns([PY,'-B',scripts/'verify_runtime_atomicity_negative.py'],
            env={'PATH':os.environ['PATH'],'PYTHONDONTWRITEBYTECODE':'1'})
        (destination/'negatives.stdout').write_text(result.stdout)
        shutil.copyfile(ROOT/'logs/atomicity-negatives.json',destination/'negatives.json')
    finally:
        run(['systemctl','stop',*['enterprise-agent-native-isolated-'+r+'.service' for r in ('api','mcp','worker','pg','redis')]])
    print('EXACT_NATIVE_MATRIX_PASS='+str(destination),flush=True)


if __name__=='__main__':main()
