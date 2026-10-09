"""Run the existing marked PG fixture tests; not Native qualification."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--pg-bin',type=Path,required=True)
    args=parser.parse_args()
    assert sys.platform=='linux' and os.geteuid()!=0 and sys.dont_write_bytecode
    assert subprocess.check_output([args.pg_bin/'postgres','--version'],text=True).strip()=='postgres (PostgreSQL) 16.6'
    root=Path(tempfile.mkdtemp(prefix='ky-web-stage1-postgres.',dir='/private/tmp'))
    (root/'stage1-isolated.marker').write_text('ky-web-stage1-local-only')
    socket=root/'socket';socket.mkdir(mode=0o700)
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','STAGE1_POSTGRES_ROOT':str(root),'STAGE1_POSTGRES_PORT':'54329'}
    subprocess.run([args.pg_bin/'initdb','-D',root/'cluster','-U','stage1_fixture','-A','trust','--no-instructions'],check=True,stdout=subprocess.DEVNULL)
    subprocess.run([args.pg_bin/'pg_ctl','-D',root/'cluster','-l',root/'postgres.log','-o',
        f"-k {socket} -p 54329 -c listen_addresses='' -c shared_buffers=16MB",'-w','start'],check=True,stdout=subprocess.DEVNULL)
    try:
        result=subprocess.run([sys.executable,'-B','-m','pytest','-q','-p','no:cacheprovider',
            'tests/test_runtime_test_atomicity.py','tests/test_agent_runtime_test_lifecycle.py',
            'tests/test_release_scoped_runtime_test.py'],cwd=args.project,env=env)
        return result.returncode
    finally:
        subprocess.run([args.pg_bin/'pg_ctl','-D',root/'cluster','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL)
        print('COMPONENT_PG_CLUSTER_STOPPED_EVIDENCE_PRESERVED='+str(root),flush=True)


if __name__=='__main__':raise SystemExit(main())
