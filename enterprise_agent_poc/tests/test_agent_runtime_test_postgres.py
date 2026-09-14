"""010 domain-only expansion; all historical row bytes/values preserved."""
import json
import shutil
from uuid import uuid4

import psycopg
import pytest

from scripts import migrate
from test_agent_productization_postgres import pg_catalog
from test_agent_productization import new_draft


@pytest.fixture
def pg_before010(tmp_path,approved_bundle,monkeypatch):
    baseline=tmp_path/'001-009';baseline.mkdir()
    for p in migrate.migration_files()[:9]:shutil.copy(p,baseline/p.name)
    with monkeypatch.context() as patch:
        patch.setattr(migrate,'MIGRATIONS',baseline)
        return pg_catalog.__wrapped__(tmp_path,approved_bundle,patch)


def test_real_010_preserves_rows_expands_only_status_and_is_idempotent(pg_before010,capsys):
    c=pg_before010;t,v=new_draft(c)
    with c.store.connection() as conn:
        for state in ('passed','failed','invalidated'):
            conn.execute("INSERT INTO agent_template_tests(id,agent_template_version_id,configuration_fingerprint,test_type,status,result_json) VALUES (?,?,'historical','runtime',?,?)",(str(uuid4()),v,state,json.dumps({'historical':state})))
        before=[dict(r) for r in conn.execute('SELECT * FROM agent_template_tests ORDER BY id')]
        keys_before=[dict(r) for r in conn.execute("SELECT conname,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid='agent_template_tests'::regclass AND conname<>'agent_template_tests_status_check' ORDER BY conname")]
        indexes_before=[dict(r) for r in conn.execute("SELECT indexname,indexdef FROM pg_indexes WHERE tablename='agent_template_tests' ORDER BY indexname")]
    assert migrate.up(c.store)==0
    assert migrate.status(c.store)==0
    state=json.loads(capsys.readouterr().out.splitlines()[-1])
    assert len(state['migrations'])==11 and state['pending']==state['checksum_mismatch']==0
    with c.store.connection() as conn:
        assert before==[dict(r) for r in conn.execute('SELECT * FROM agent_template_tests ORDER BY id')]
        assert keys_before==[dict(r) for r in conn.execute("SELECT conname,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid='agent_template_tests'::regclass AND conname<>'agent_template_tests_status_check' ORDER BY conname")]
        assert indexes_before==[dict(r) for r in conn.execute("SELECT indexname,indexdef FROM pg_indexes WHERE tablename='agent_template_tests' ORDER BY indexname")]
        for status in ('queued','running'):
            conn.execute("INSERT INTO agent_template_tests(id,agent_template_version_id,configuration_fingerprint,test_type,status) VALUES (?,?,'current','runtime',?)",(str(uuid4()),v,status))
    with pytest.raises(psycopg.errors.CheckViolation),c.store.connection() as conn:
        conn.execute("INSERT INTO agent_template_tests(id,agent_template_version_id,configuration_fingerprint,test_type,status) VALUES (?,?,'bad','runtime','unknown')",(str(uuid4()),v))
    assert migrate.up(c.store)==0
    with c.store.connection() as conn:conn.execute(migrate.migration_files()[-1].read_text())


def test_010_rejects_unexpected_history_without_rewriting_rows(pg_before010):
    c=pg_before010;t,v=new_draft(c);test_id=str(uuid4())
    # Fault-inject only the authorized CHECK in an isolated fixture database.
    with c.store.connection() as conn:
        conn.execute('ALTER TABLE agent_template_tests DROP CONSTRAINT agent_template_tests_status_check')
        conn.execute("ALTER TABLE agent_template_tests ADD CONSTRAINT agent_template_tests_status_check CHECK (status IN ('passed','failed','invalidated','unexpected'))")
        conn.execute("INSERT INTO agent_template_tests(id,agent_template_version_id,configuration_fingerprint,test_type,status) VALUES (?,?,'historical','runtime','unexpected')",(test_id,v))
        before=dict(conn.execute('SELECT * FROM agent_template_tests WHERE id=?',(test_id,)).fetchone())
    with pytest.raises(psycopg.errors.RaiseException,match='Unexpected historical'),c.store.connection() as conn:
        conn.execute(migrate.migration_files()[9].read_text())
    with c.store.connection() as conn:
        assert before==dict(conn.execute('SELECT * FROM agent_template_tests WHERE id=?',(test_id,)).fetchone())
        assert conn.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()['n']==9
