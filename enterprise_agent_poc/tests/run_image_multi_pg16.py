"""Targeted non-root local PG tests, never Native release validation."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    pg = Path('/home/lucky/.cache/enterprise-agent-test-runtime/postgresql-16.6/bin')
    if sys.platform != 'linux' or os.geteuid() == 0:
        raise RuntimeError('INDEPENDENT_NON_ROOT_LINUX_REQUIRED')
    assert subprocess.check_output([str(pg/'postgres'),'--version'],text=True).strip() == 'postgres (PostgreSQL) 16.6'
    root = Path(tempfile.mkdtemp(prefix='ky-web-stage1-postgres.image-multi-',dir='/private/tmp'))
    (root/'stage1-isolated.marker').write_text('ky-web-stage1-local-only')
    (root/'socket').mkdir(mode=0o700)
    subprocess.run([str(pg/'initdb'),'-D',str(root/'cluster'),'-U','stage1_fixture','-A','trust','--no-locale','--encoding=UTF8'],check=True,stdout=subprocess.DEVNULL)
    try:
        subprocess.run([str(pg/'pg_ctl'),'-D',str(root/'cluster'),'-l',str(root/'postgres.log'),'-o',f"-h '' -k {root/'socket'} -p 54329",'-w','start'],check=True)
        env = dict(os.environ,STAGE1_POSTGRES_ROOT=str(root),STAGE1_POSTGRES_PORT='54329',PYTHONDONTWRITEBYTECODE='1')
        return subprocess.run([sys.executable,'-B','-m','pytest','-q','-ra','tests/test_image_multi_artifact_postgres.py',
                               'tests/test_run_trace.py','tests/test_task_finalization.py','tests/test_tool_dependency_supersession.py',
                               'tests/test_reference_dependency_retry.py','tests/test_image_generation_provider.py',
                               'tests/test_chat_reference_image.py','tests/test_history.py','tests/test_product_auth.py',
                               'tests/test_stop_generation.py',*sys.argv[1:]],cwd=Path(__file__).resolve().parents[1],env=env).returncode
    finally:
        subprocess.run([str(pg/'pg_ctl'),'-D',str(root/'cluster'),'-m','fast','-w','stop'],check=True)
        print('ISOLATED_PG_STOPPED; retained evidence: '+str(root),flush=True)


if __name__ == '__main__':
    sys.exit(main())
