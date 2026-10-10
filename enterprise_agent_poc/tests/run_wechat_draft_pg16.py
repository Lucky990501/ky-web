"""Explicit isolated Linux launcher; never accepts a DB URL or a remote host."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

PG=Path('/home/lucky/.cache/enterprise-agent-test-runtime/postgresql-16.6/bin')
ROOT=Path(__file__).resolve().parents[1]


def main():
    if sys.platform!='linux' or os.geteuid()==0:raise RuntimeError('ISOLATED_NON_ROOT_LINUX_REQUIRED')
    if subprocess.check_output([str(PG/'postgres'),'--version'],text=True).strip()!='postgres (PostgreSQL) 16.6':
        raise RuntimeError('EXACT_PG_16_6_REQUIRED')
    root=Path(tempfile.mkdtemp(prefix='ky-web-stage1-postgres.wechat016-',dir='/private/tmp'))
    (root/'stage1-isolated.marker').write_text('ky-web-stage1-local-only')
    (root/'socket').mkdir(mode=0o700)
    subprocess.run([str(PG/'initdb'),'-D',str(root/'cluster'),'-U','stage1_fixture','-A','trust','--no-locale','--encoding=UTF8'],check=True,stdout=subprocess.DEVNULL)
    env=dict(os.environ,STAGE1_POSTGRES_ROOT=str(root),STAGE1_POSTGRES_PORT='54329',PYTHONDONTWRITEBYTECODE='1',
             DEEPSEEK_API_KEY='unit-test-placeholder',GATEWAY_API_TOKEN='unit-test-placeholder')
    print('ISOLATED_PG_ROOT='+str(root),flush=True)
    started=False
    try:
        subprocess.run([str(PG/'pg_ctl'),'-D',str(root/'cluster'),'-l',str(root/'postgres.log'),'-o',
                        f"-h '' -k {root/'socket'} -p 54329",'-w','start'],check=True)
        started=True
        result=subprocess.run([sys.executable,'-B','-m','pytest','-q','tests/test_wechat_draft_operations_postgres.py',*sys.argv[1:]],cwd=ROOT,env=env)
        return result.returncode
    finally:
        if started:subprocess.run([str(PG/'pg_ctl'),'-D',str(root/'cluster'),'-m','fast','-w','stop'],check=True)
        print('ISOLATED_CLUSTER_STOPPED; evidence retained at '+str(root),flush=True)


if __name__=='__main__':sys.exit(main())
