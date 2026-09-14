"""Subprocess test boundary, never imported by production application code."""
import argparse
import asyncio
import os
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('role',choices=('api','mcp','worker'))
    parser.add_argument('--source',required=True)
    parser.add_argument('--root',required=True)
    parser.add_argument('--port',type=int,required=True)
    args=parser.parse_args()
    root=Path(args.root).resolve()
    assert root.is_relative_to(Path('/private/tmp')) and root.stat().st_uid==os.getuid()
    assert (root/'epoch-isolation.marker').read_text()=='ky-web-epoch-isolated-v1'
    pg=Path('/private/tmp/ky-web-stage1-postgres.bqKYMg')
    assert (pg/'stage1-isolated.marker').read_text().strip()=='ky-web-stage1-local-only'
    db=urlparse(os.environ['ENTERPRISE_POC_DATABASE_URL']); query=parse_qs(db.query)
    assert not db.hostname and query['host']==[str(pg/'socket')] and db.path.startswith('/rollback_')
    assert os.environ['ENTERPRISE_POC_DATA_DIR']==str(root/'shared/runtime-data')
    assert os.environ['REDIS_URL']==f'unix://{root}/redis.sock?db=0'
    assert os.environ['ENTERPRISE_POC_MODEL_BASE_URL']=='http://127.0.0.1:1/'
    import psycopg
    with psycopg.connect(os.environ['ENTERPRISE_POC_DATABASE_URL'],options='-c default_transaction_read_only=on') as c:
        assert c.execute('SHOW listen_addresses').fetchone()[0]==''
        assert Path(c.execute('SHOW data_directory').fetchone()[0]).resolve()==pg/'cluster'
    source=Path(args.source).resolve()
    assert source==Path('/Users/lucky/Projects/ky-web/enterprise_agent_poc') or source.is_relative_to(root/'releases')
    sys.path.insert(0,str(source))
    if args.role=='mcp':
        from app.platform_mcp.server import create_mcp
        create_mcp().run(transport='streamable-http')
        return
    import app.main as api
    from app.domain import RuntimeSession,RuntimeTurn
    async def create(profile,instructions):
        return RuntimeSession('epoch-fake-'+uuid4().hex,profile.id)
    async def resume(profile,thread_id,**kwargs):
        return RuntimeSession(thread_id,profile.id)
    async def turn(session,message):
        await asyncio.sleep(.4)
        return RuntimeTurn(session.thread_id,'隔离 Data Contract 模拟最终响应；不属于真实 Provider 验收。')
    async def close():pass
    # Keep the normal provider object / API→Redis→Worker→finalization chain,
    # replacing only model/session I/O and explicitly labeled Skill discovery.
    api.runtime.create_session=create
    api.runtime.resume_session=resume
    api.runtime.run_turn=turn
    api.runtime.close=close
    api.runtime.startup_events=lambda p:tuple({'event':'skill_discovered','skill':s,'fixture':'fake-runtime'} for s in p.skill_manifest)
    if args.role=='worker':
        from app.worker import run
        asyncio.run(run())
    else:
        import uvicorn
        uvicorn.run(api.app,host='127.0.0.1',port=args.port,log_level='warning')


if __name__=='__main__':main()
