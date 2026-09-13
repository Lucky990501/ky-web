"""Stage 2 real PostgreSQL 001–009 integration; private marked socket only."""
import json
import os
from uuid import uuid4

import psycopg
import pytest

from scripts import migrate
from test_agent_productization_postgres import pg_catalog
from test_agent_execution import attach,make_ready,context

pytestmark=pytest.mark.skipif(not os.environ.get('STAGE1_POSTGRES_ROOT'),reason='isolated PostgreSQL root not provided')


@pytest.fixture
def pg_execution(pg_catalog,tmp_path):
    return attach(pg_catalog,tmp_path)


def test_real_001_009_first_apply_status_idempotent(pg_execution,capsys):
    c=pg_execution
    assert migrate.status(c.store)==0
    state=json.loads(capsys.readouterr().out.splitlines()[-1])
    assert len(state['migrations'])==9 and state['pending']==state['checksum_mismatch']==0
    assert all(x['status']=='applied' for x in state['migrations'])
    with c.store.connection() as conn:
        before=[dict(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]
        tables={r['table_name'] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
        assert {'agent_execution_contexts','conversation_agent_contexts','task_agent_contexts'}<=tables
        conn.execute(migrate.migration_files()[-1].read_text())
    assert migrate.up(c.store)==0
    with c.store.connection() as conn:assert before==[dict(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]


def test_real_context_foreign_keys_identity_and_immutable(pg_execution):
    c=pg_execution;t,v=make_ready(c);task=c.product.create_task('tenant-a',c.actor,t,'x',None);ctx=context(c,task)
    for field in ('tenant_id','agent_id','instance_id','agent_template_version_id'):
        other={k:val for k,val in ctx.items() if k!='created_at'}
        other['id']=str(uuid4());other[field]='missing'
        with pytest.raises(psycopg.Error),c.store.connection() as conn:
            conn.execute(f"INSERT INTO agent_execution_contexts({','.join(other)}) VALUES ({','.join('?' for _ in other)})",tuple(other.values()))
    other={k:val for k,val in ctx.items() if k!='created_at'};other['id']=str(uuid4());other['tenant_id']='tenant-b'
    with pytest.raises(psycopg.errors.RaiseException),c.store.connection() as conn:
        conn.execute(f"INSERT INTO agent_execution_contexts({','.join(other)}) VALUES ({','.join('?' for _ in other)})",tuple(other.values()))
    for sql in ('UPDATE agent_execution_contexts SET credit_cost=99 WHERE id=?','DELETE FROM agent_execution_contexts WHERE id=?'):
        with pytest.raises(psycopg.errors.RaiseException,match='immutable'),c.store.connection() as conn:conn.execute(sql,(ctx['id'],))


def test_real_task_conversation_unique_and_cross_tenant_association(pg_execution):
    import asyncio
    c=pg_execution;t,v=make_ready(c);task=c.product.create_task('tenant-a',c.actor,t,'x',None);ctx=context(c,task)
    asyncio.run(c.tasks.execute(task));saved=c.product.task_for_worker(task['id'])
    with pytest.raises(psycopg.errors.UniqueViolation),c.store.connection() as conn:conn.execute('INSERT INTO task_agent_contexts VALUES (?,?)',(task['id'],ctx['id']))
    with pytest.raises(psycopg.errors.UniqueViolation),c.store.connection() as conn:conn.execute('INSERT INTO conversation_agent_contexts VALUES (?,?)',(saved['conversation_id'],ctx['id']))
    user=c.product.create_user('tenant-b','other@stage2.invalid','unused','Other','member')
    user_id=c.product.user_by_email('other@stage2.invalid')['id']
    other=c.product.create_task('tenant-b',user_id,'copywriting-agent','legacy',None)
    with pytest.raises(psycopg.errors.RaiseException,match='identity'),c.store.connection() as conn:conn.execute('INSERT INTO task_agent_contexts VALUES (?,?)',(other['id'],ctx['id']))
    with c.store.connection() as conn:
        conn.execute("INSERT INTO conversations(id,tenant_id,agent_id,runtime_profile_id,runtime_thread_id,runtime_version) VALUES ('cross-tenant','tenant-b',? ,?,'fixture','fixture')",(t,ctx['runtime_profile_id']))
    with pytest.raises(psycopg.errors.RaiseException,match='identity'),c.store.connection() as conn:conn.execute("INSERT INTO conversation_agent_contexts VALUES ('cross-tenant',?)",(ctx['id'],))


def test_real_task_context_transaction_rollback(pg_execution,monkeypatch):
    c=pg_execution;t,v=make_ready(c)
    with c.store.connection() as conn:before=[conn.execute(f'SELECT COUNT(*) AS n FROM {table}').fetchone()['n'] for table in ('tasks','agent_execution_contexts','task_agent_contexts')]
    resolve=c.resolver.resolve
    def fail(*args,**kwargs):resolve(*args,**kwargs);raise RuntimeError('isolated rollback')
    monkeypatch.setattr(c.resolver,'resolve',fail)
    with pytest.raises(RuntimeError):c.product.create_task('tenant-a',c.actor,t,'rollback',None)
    with c.store.connection() as conn:assert before==[conn.execute(f'SELECT COUNT(*) AS n FROM {table}').fetchone()['n'] for table in ('tasks','agent_execution_contexts','task_agent_contexts')]


def test_real_legacy_and_five_skill_bindings_unchanged(pg_execution):
    c=pg_execution;make_ready(c)
    with c.store.connection() as conn:
        for old in c.old_templates:
            row=dict(conn.execute('SELECT * FROM agent_templates WHERE id=?',(old['id'],)).fetchone())
            assert {k:row[k] for k in old}==old and row['definition_source']=='legacy'
        for old in c.old_instances:
            row=dict(conn.execute('SELECT * FROM tenant_agent_instances WHERE tenant_id=? AND agent_id=?',(old['tenant_id'],old['agent_id'])).fetchone())
            assert {k:row[k] for k in old}==old
        assert c.old_bindings==[dict(r) for r in conn.execute('SELECT * FROM agent_skill_bindings ORDER BY agent_id,skill_id')]
        assert len(c.old_bindings)==5
        assert c.old_skills==[dict(r) for r in conn.execute('SELECT * FROM skills ORDER BY id')]
        assert c.old_packages==[dict(r) for r in conn.execute('SELECT * FROM skill_packages ORDER BY id')]
