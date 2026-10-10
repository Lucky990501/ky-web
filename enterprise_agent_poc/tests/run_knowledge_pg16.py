"""Bounded local PG16.6 test launcher. No TCP, remote DSN, or provider calls."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    pg = Path('/home/lucky/.cache/enterprise-agent-test-runtime/postgresql-16.6/bin')
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise RuntimeError('ISOLATED_NON_ROOT_LINUX_REQUIRED')
    assert subprocess.check_output([str(pg/'postgres'), '--version'], text=True).strip() == 'postgres (PostgreSQL) 16.6'
    root = Path(tempfile.mkdtemp(prefix='ky-web-stage1-postgres.rag-', dir='/private/tmp'))
    (root/'stage1-isolated.marker').write_text('ky-web-stage1-local-only')
    (root/'socket').mkdir(mode=0o700)
    subprocess.run([str(pg/'initdb'), '-D', str(root/'cluster'), '-U', 'stage1_fixture', '-A', 'trust', '--no-locale', '--encoding=UTF8'], check=True, stdout=subprocess.DEVNULL)
    env = dict(os.environ, STAGE1_POSTGRES_ROOT=str(root), STAGE1_POSTGRES_PORT='54329', PYTHONDONTWRITEBYTECODE='1')
    started = False
    try:
        subprocess.run([str(pg/'pg_ctl'), '-D', str(root/'cluster'), '-l', str(root/'postgres.log'), '-o', f"-h '' -k {root/'socket'} -p 54329", '-w', 'start'], check=True)
        started = True
        return subprocess.run([sys.executable, '-B', '-m', 'pytest', '-q', 'tests/test_knowledge_schema015_postgres.py', *sys.argv[1:]], env=env, cwd=Path(__file__).resolve().parents[1]).returncode
    finally:
        if started:
            subprocess.run([str(pg/'pg_ctl'), '-D', str(root/'cluster'), '-m', 'fast', '-w', 'stop'], check=True)
        print('ISOLATED_PG_STOPPED; retained evidence: ' + str(root), flush=True)


if __name__ == '__main__':
    sys.exit(main())
