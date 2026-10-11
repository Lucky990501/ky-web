"""Fresh non-root, socket-only PG16.6 and an actual synthetic Git identity."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    source = Path(__file__).resolve().parents[1]
    pg = Path('/home/lucky/.cache/enterprise-agent-test-runtime/postgresql-16.6/bin')
    if sys.platform != 'linux' or os.geteuid() == 0 or not source.is_relative_to(Path('/var/tmp')):
        raise RuntimeError('NEW_ISOLATED_NON_ROOT_SOURCE_REQUIRED')
    assert subprocess.check_output([str(pg/'postgres'), '--version'], text=True).strip() == 'postgres (PostgreSQL) 16.6'
    # Synthetic Git identity is used only by ISOLATED_CUSTOMER_PROVISIONING_TEST.
    if not (source/'.git').exists():
        (source/'.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
        subprocess.run(['git', 'init', '-q', str(source)], check=True)
        for key, value in [('user.name', 'Isolated Fixture'), ('user.email', 'fixture@isolated.invalid'), ('core.autocrlf', 'false')]:
            subprocess.run(['git', '-C', str(source), 'config', key, value], check=True)
        subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(source), 'commit', '-qm', 'synthetic provisioning fixture source'], check=True)
    root = Path(tempfile.mkdtemp(prefix='ky-web-stage1-postgres.customer-', dir='/private/tmp'))
    (root/'stage1-isolated.marker').write_text('ky-web-stage1-local-only')
    (root/'socket').mkdir(mode=0o700)
    subprocess.run([str(pg/'initdb'), '-D', str(root/'cluster'), '-U', 'stage1_fixture', '-A', 'trust', '--no-locale', '--encoding=UTF8'], check=True, stdout=subprocess.DEVNULL)
    started = False
    try:
        subprocess.run([str(pg/'pg_ctl'), '-D', str(root/'cluster'), '-l', str(root/'postgres.log'), '-o', f"-h '' -k {root/'socket'} -p 54329", '-w', 'start'], check=True)
        started = True
        env = dict(os.environ, STAGE1_POSTGRES_ROOT=str(root), STAGE1_POSTGRES_PORT='54329', PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([sys.executable, '-B', '-m', 'pytest', '-q', 'tests/test_customer_tenant_provisioning_postgres.py', *sys.argv[1:]], cwd=source, env=env).returncode
    finally:
        if started:
            subprocess.run([str(pg/'pg_ctl'), '-D', str(root/'cluster'), '-m', 'fast', '-w', 'stop'], check=True)
        print('ISOLATED_PG_STOPPED; evidence retained: '+str(root), flush=True)


if __name__ == '__main__':
    sys.exit(main())
