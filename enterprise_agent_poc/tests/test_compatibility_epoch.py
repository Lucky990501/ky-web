"""Permanent pre-Pilot Epoch; every PostgreSQL test uses a marked private DB."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

import psycopg
import pytest

from app.agent_productization import AgentCatalogError, AgentProductization
from app.product_store import ProductStore
from app.store import POCStore
from scripts import compatibility_epoch as epoch, migrate
from test_release_switch import pg_rollback_catalog, pg_rollback_harness, pg_gate, pg_entry

SQL = Path(__file__).resolve().parents[1] / 'migrations/postgres/011_platform_compatibility_epoch.sql'
BD_ROOT = Path('/private/tmp/ky-web-stage27.yFFOL6')
BD_ID = '20260914-bd04dcb'
BD_COMMIT = 'bd04dcb982bf0efe02a5a1a42dd162d94265da0b'
B930_ID = '20260914-b930e87'
B930_COMMIT = 'b930e876aa6919b869af1309d952c3e2e0a8469a'
B930_ARCHIVE_SHA = '195b3729643974c510fcfabff5e498b4211007cffab0bb968ee2f86cb5556470'
B930_MANIFEST_SHA = 'e2a3102e6fe5eaab80145f1b05b30b20fb68f267259ed33f7be7ebf70109817b'
B930_EVIDENCE_VERSION = 'current-b930-original-artifact-pg16-001-011-v1'
B930_EVIDENCE_SHA = '7d2a12e8a9f30fd3d6a69bf62d0dd2cdab7d67435f175db9eb7a3ed1624a4cfc'
INSERT = "INSERT INTO agent_templates(id,name,slug,description,icon,status,default_runtime_profile,credit_cost,skill_manifest,definition_source) VALUES ('epoch-pilot','Synthetic','epoch-pilot','Isolated','test','disabled','default',1,'{}','productized')"


@pytest.fixture
def local_control(tmp_path):
    store = POCStore(tmp_path/'local.db')
    store.seed_demo_data()
    ProductStore(store).initialize()
    control = AgentProductization(store)
    control.initialize()
    return control


def test_sqlite_service_and_direct_insert_fail_closed(local_control):
    c = local_control
    with pytest.raises(AgentCatalogError, match='compatibility_epoch_not_advanced'):
        c.create_template({'slug':'epoch-pilot','name':'Synthetic'}, None)
    with pytest.raises(Exception, match='compatibility_epoch_not_advanced'), c.store.connection() as conn:
        conn.execute(INSERT)
    with c.store.connection() as conn:
        assert epoch.read_state(conn, postgres=False)['epoch'] == 'legacy_v1'


def test_sqlite_explicit_advance_is_permanent_even_zero_rows(local_control):
    c = local_control
    with c.store.connection() as conn:
        conn.execute("UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,advanced_at=CURRENT_TIMESTAMP,advance_origin='controlled_advance',advanced_by_release_id='synthetic',advanced_by_source_commit=?", ('f'*40,))
    c.create_template({'slug':'epoch-pilot','name':'Synthetic'}, None)
    for sql in ("UPDATE platform_compatibility_state SET epoch='legacy_v1',epoch_rank=1", 'DELETE FROM platform_compatibility_state'):
        with pytest.raises(Exception), c.store.connection() as conn:
            conn.execute(sql)


@pytest.fixture
def pg_epoch(pg_rollback_harness):
    h = pg_rollback_harness
    assert BD_ROOT.is_dir()
    directory = h['base']/'releases'/BD_ID
    directory.mkdir()
    for suffix in ('tar.gz','manifest.json'):
        shutil.copyfile(BD_ROOT/f'{BD_ID}.{suffix}', directory/f'{BD_ID}.{suffix}')
    with tarfile.open(directory/f'{BD_ID}.tar.gz') as archive:
        archive.extractall(directory, filter='data')
    h['bd'] = directory/'enterprise_agent_poc'
    # Linux systemd/procfs boundary only. This is an independently packed
    # synthetic TEST helper, not a change to the original bd Artifact.
    helper=h['new']/'scripts/compatibility_epoch.py'
    helper.write_text(helper.read_text().replace('running_identity_check or require_running_release',
        'running_identity_check or (lambda source: None)'))
    h['pack']()
    return h


def approve_active_bd(h):
    link = h['base']/'release-current'
    link.unlink()
    link.symlink_to(h['bd'], target_is_directory=True)


def advance(h, **kwargs):
    return epoch.advance(h['base'], h['new'], h['store'].database_url,
                         running_identity_check=lambda source:None, **kwargs)


def original_b930_manifest():
    import hashlib, os
    root=Path(os.environ.get('CURRENT_APPLICATION_ARTIFACT_ROOT','/private/tmp/ky-web-stage210.G32W91')).resolve()
    assert root.parent==Path('/private/tmp') and root.stat().st_uid==os.getuid()
    manifest_path=root/f'{B930_ID}.manifest.json'
    manifest=json.loads(manifest_path.read_text())
    assert hashlib.sha256((root/f'{B930_ID}.tar.gz').read_bytes()).hexdigest()==B930_ARCHIVE_SHA
    assert manifest['release_id']==B930_ID and manifest['source_commit']==B930_COMMIT
    assert manifest['archive_sha256']==B930_ARCHIVE_SHA
    assert manifest['selected_file_count']==len(set(manifest['selected_files']))==87
    assert epoch_gate().digest(manifest)==B930_MANIFEST_SHA
    return root,manifest


def epoch_gate():
    from scripts import rollback_preflight
    return rollback_preflight


@pytest.fixture
def pg_current(pg_epoch):
    h=pg_epoch;origin,manifest=original_b930_manifest()
    directory=h['base']/'releases'/B930_ID;directory.mkdir()
    for suffix in ('tar.gz','manifest.json'):
        shutil.copyfile(origin/f'{B930_ID}.{suffix}',directory/f'{B930_ID}.{suffix}')
    with tarfile.open(directory/f'{B930_ID}.tar.gz') as archive:
        archive.extractall(directory,filter='data')
    h['b930']=directory/'enterprise_agent_poc'
    gate=h['gate'];d=json.loads((h['new']/'deploy/rollback_compatibility.json').read_text())
    reference={'release_id':B930_ID,'source_commit':B930_COMMIT}
    # Test the reviewed repository approval, never inject/relabel an approval
    # into the current application. Only synthetic tooling carries the record.
    record=next(t for t in d['approved_targets'] if t['release_id']==B930_ID)
    assert record['compatibility_evidence']['version']==B930_EVIDENCE_VERSION
    assert record['compatibility_evidence']['report_sha256']==B930_EVIDENCE_SHA
    assert record['manifest_sha256']==gate.digest(manifest)
    assert all(reference in item['allowed_targets'] for item in d['epoch_contract']['epochs'])
    gate.release_identity(h['base'],B930_ID,B930_COMMIT,record)
    return h


def activate_b930(h):
    link=h['base']/'release-current';link.unlink();link.symlink_to(h['b930'],target_is_directory=True)


def test_current_original_manifest_is_pinned_and_floor_declaration_unchanged():
    _,manifest=original_b930_manifest()
    d=json.loads((SQL.parents[2]/'deploy/rollback_compatibility.json').read_text())
    contract=epoch_gate().epoch_contract(d)
    assert contract['epochs'][0]['minimum_target']=={'release_id':'20260913-6abccad','source_commit':'6abccad4db3e4802810380fae2082a30473f229c'}
    assert contract['epochs'][1]['minimum_target']=={'release_id':BD_ID,'source_commit':BD_COMMIT}
    assert epoch_gate().digest(manifest)==B930_MANIFEST_SHA
    record=next(t for t in d['approved_targets'] if t['release_id']==B930_ID)
    assert record['known_migrations']==contract['schema_migrations']
    assert record['compatibility_evidence']['report_sha256']==B930_EVIDENCE_SHA
    assert record['compatibility_evidence']['version']==B930_EVIDENCE_VERSION
    assert record['compatibility_evidence']['postgres_major']==16
    assert record['compatibility_evidence']['target_identity_fingerprint']==epoch_gate().digest({k:v for k,v in record.items() if k!='compatibility_evidence'})
    with tarfile.open(original_b930_manifest()[0]/f'{B930_ID}.tar.gz') as archive:
        old_b930_json=json.loads(archive.extractfile('enterprise_agent_poc/deploy/rollback_compatibility.json').read())
    assert not any(t['release_id']==B930_ID for t in old_b930_json['approved_targets'])


def test_postgres_current_tooling_application_separation_and_permanent_floor(pg_current):
    h=pg_current;activate_b930(h);before=h['snapshot']()
    assert pg_gate(h)['compatibility_epoch']=='legacy_v1'
    result=advance(h);assert result['status']=='advanced'
    with h['store'].connection() as c:
        state=epoch.read_state(c)
        assert state['advanced_by_release_id']=='fixture-new'
        assert state['advanced_by_source_commit']=='f'*40!=B930_COMMIT
        assert not epoch.has_productized_data(c)
    assert (h['base']/'release-current').resolve()==h['b930']
    assert h['snapshot']()==before
    for rid,commit in ((BD_ID,BD_COMMIT),(B930_ID,B930_COMMIT)):
        assert pg_gate(h,target_id=rid,commit=commit)['compatibility_epoch']=='productized_v1'
    with pytest.raises(h['gate'].RollbackBlocked,match='below_data_compatibility_floor'):pg_gate(h)
    AgentProductization(h['store']).create_template({'slug':'synthetic-current','name':'Synthetic'},None)
    with pytest.raises(h['gate'].RollbackBlocked,match='below_data_compatibility_floor'):pg_gate(h)
    link=h['base']/'release-current';link.unlink();link.symlink_to(h['new'],target_is_directory=True)
    with pytest.raises(epoch.EpochBlocked,match='current_release_not_approved_productized_identity'):advance(h)
    with pytest.raises(h['gate'].RollbackBlocked,match='target_not_approved_for_compatibility_epoch'):
        pg_gate(h,target_id='fixture-new',commit='f'*40)


@pytest.mark.parametrize('tamper',['source','archive','archive_sha','manifest','file_bytes','selected_count','selected_set','cwd','inactive'])
def test_postgres_current_identity_tamper_blocks_before_advance(pg_current,tamper,monkeypatch):
    h=pg_current;activate_b930(h);before=h['snapshot']();manifest=h['b930'].parent/f'{B930_ID}.manifest.json'
    if tamper=='archive':
        p=h['b930'].parent/f'{B930_ID}.tar.gz';p.write_bytes(p.read_bytes()+b'fault')
    elif tamper=='file_bytes':
        p=h['b930']/'app/agent_productization.py';p.write_bytes(p.read_bytes()+b'\n# fault\n')
    elif tamper in ('source','archive_sha','manifest','selected_count','selected_set'):
        d=json.loads(manifest.read_text())
        if tamper=='source':d['source_commit']='0'*40
        elif tamper=='archive_sha':d['archive_sha256']='0'*64
        elif tamper=='selected_count':d['selected_file_count']=88
        elif tamper=='selected_set':d['selected_files'][0]=d['selected_files'][1]
        else:d['build_platform']='unapproved'
        manifest.write_text(json.dumps(d))
    elif tamper in ('cwd','inactive'):
        from types import SimpleNamespace
        monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='MainPID=99\nActiveState=active\n' if tamper=='cwd' else 'MainPID=0\nActiveState=inactive\n'))
        original=Path.resolve
        monkeypatch.setattr(Path,'resolve',lambda self,**kwargs:h['bd'] if str(self)=='/proc/99/cwd' else original(self,**kwargs))
    with pytest.raises((epoch.EpochBlocked,h['gate'].RollbackBlocked)):
        epoch.advance(h['base'],h['new'],h['store'].database_url,
            running_identity_check=epoch.require_running_release if tamper in ('cwd','inactive') else lambda source:None)
    with h['store'].connection() as c:assert epoch.read_state(c)['epoch']=='legacy_v1'
    assert h['snapshot']()==before and not h['events'].exists()


@pytest.mark.parametrize('tamper',['unknown012','checksum011','missing_epoch','corrupt_epoch'])
def test_postgres_current_approved_identity_still_requires_strict_epoch_history(pg_current,tamper):
    h=pg_current;activate_b930(h)
    if tamper in ('unknown012','checksum011'):h['store']=h['fresh_database'](fault=tamper)
    else:
        with h['store'].connection() as c:
            c.execute('ALTER TABLE platform_compatibility_state DISABLE TRIGGER USER')
            if tamper=='missing_epoch':c.execute('DELETE FROM platform_compatibility_state')
            else:
                checks=c.execute("SELECT conname FROM pg_constraint WHERE conrelid='platform_compatibility_state'::regclass AND contype='c'").fetchall()
                for r in checks:c.execute('ALTER TABLE platform_compatibility_state DROP CONSTRAINT '+r['conname'])
                c.execute("UPDATE platform_compatibility_state SET epoch='future_v9'")
    before=h['snapshot']()
    with pytest.raises((epoch.EpochBlocked,h['gate'].RollbackBlocked)):advance(h)
    assert h['snapshot']()==before and not h['events'].exists()


def test_postgres_001_011_first_apply_rerun_and_legacy_initialization(pg_epoch, capsys):
    h = pg_epoch
    before = h['snapshot']()
    with h['store'].connection() as conn:
        state = epoch.read_state(conn)
        assert state['epoch'] == 'legacy_v1' and state['epoch_rank'] == 1
        conn.execute(SQL.read_text())
    assert migrate.up(h['store']) == 0 and migrate.status(h['store']) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert len(result['migrations']) == 11 and result['pending'] == result['checksum_mismatch'] == 0
    assert result['unknown_history_versions'] == [] and h['snapshot']() == before
    assert pg_gate(h)['compatibility_epoch'] == 'legacy_v1'


def test_postgres_service_and_db_create_block_before_advance(pg_epoch):
    h = pg_epoch
    with pytest.raises(AgentCatalogError, match='compatibility_epoch_not_advanced'):
        AgentProductization(h['store']).create_template({'slug':'epoch-pilot','name':'Synthetic'}, None)
    with pytest.raises(psycopg.errors.RaiseException, match='compatibility_epoch_not_advanced'), h['store'].connection() as conn:
        conn.execute(INSERT)
    assert pg_gate(h)['compatibility_epoch'] == 'legacy_v1'


def test_postgres_actual_cli_advance_idempotency_and_zero_v2_permanent_floor(pg_epoch):
    h = pg_epoch
    approve_active_bd(h)
    before = h['snapshot']()
    args = [sys.executable, str(h['new']/'scripts/compatibility_epoch.py'), 'advance','--to','productized_v1']
    for expected in ('advanced','already_advanced'):
        p = subprocess.run(args,env=h['env'],capture_output=True,text=True,timeout=30)
        assert p.returncode == 0, p.stdout+p.stderr
        assert json.loads(p.stdout)['status'] == expected
    assert h['snapshot']() == before  # Migration / Registry / Package untouched.
    with h['store'].connection() as conn:
        state = epoch.read_state(conn)
        assert state['advanced_by_source_commit'] == 'f'*40 and state['advanced_by_release_id']=='fixture-new'
        assert not epoch.has_productized_data(conn)
    with pytest.raises(h['gate'].RollbackBlocked, match='rollback_target_below_data_compatibility_floor'):
        pg_gate(h)
    assert pg_gate(h,target_id=BD_ID,commit=BD_COMMIT)['status'] == 'rollback_preflight_passed'
    r = pg_entry(h)
    assert r.returncode == 2 and 'rollback_target_below_data_compatibility_floor' in r.stdout
    assert not h['events'].exists() and (h['base']/'release-current').resolve() == h['bd']


@pytest.mark.parametrize('sql', [
    "UPDATE platform_compatibility_state SET epoch='legacy_v1',epoch_rank=1,advanced_at=NULL,advanced_by_release_id=NULL,advanced_by_source_commit=NULL,advance_origin='initial_legacy'",
    'DELETE FROM platform_compatibility_state', 'TRUNCATE platform_compatibility_state',
    "UPDATE platform_compatibility_state SET epoch_rank=1",
    "UPDATE platform_compatibility_state SET contract_id='other'",
    "UPDATE platform_compatibility_state SET advanced_by_source_commit='0000000000000000000000000000000000000000'",
])
def test_postgres_monotonic_delete_rank_identity_protection(pg_epoch, sql):
    h = pg_epoch
    approve_active_bd(h)
    advance(h)
    with h['store'].connection() as conn:
        before = epoch.read_state(conn)
    with pytest.raises(psycopg.Error), h['store'].connection() as conn:
        conn.execute(sql)
    with h['store'].connection() as conn:
        assert epoch.read_state(conn) == before


def test_postgres_existing_productized_before011_initializes_upward_without_fake_identity(pg_epoch):
    h = pg_epoch
    store = h['fresh_database'](count=10)
    with store.connection() as conn:
        conn.execute(INSERT)
    assert migrate.up(store) == 0
    with store.connection() as conn:
        state = epoch.read_state(conn)
        assert state['epoch'] == 'productized_v1' and state['advance_origin'] == 'migration_detection'
        assert state['advanced_by_release_id'] is state['advanced_by_source_commit'] is None
        conn.execute(SQL.read_text())
        assert epoch.read_state(conn) == state


def test_postgres011_conflicting_legacy_revision_blocks_initialization_atomically(pg_epoch):
    from app.agent_productization import DEFAULTS
    h=pg_epoch;store=h['fresh_database'](count=10)
    fields={**DEFAULTS,'id':'synthetic-revision','agent_template_id':'synthetic-legacy',
            'revision':1,'status':'draft','configuration_fingerprint':'synthetic'}
    fields['tenant_override_schema']=json.dumps(fields['tenant_override_schema'])
    with store.connection() as c:
        c.execute("INSERT INTO agent_templates(id,name,slug,description,icon,status,default_runtime_profile,credit_cost,skill_manifest,definition_source) VALUES ('synthetic-legacy','Synthetic','synthetic-legacy','Isolated','test','disabled','default',1,'{}','legacy')")
        c.execute(f"INSERT INTO agent_template_versions({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",tuple(fields.values()))
    with pytest.raises(psycopg.errors.RaiseException,match='compatibility_epoch_data_contract_conflict'):
        migrate.up(store)
    with store.connection() as c:
        assert c.execute("SELECT to_regclass('public.platform_compatibility_state') AS name").fetchone()['name'] is None
        assert len(c.execute('SELECT * FROM schema_migrations').fetchall())==10


@pytest.mark.parametrize('fault',['missing','unknown','rank','scope','legacy_v2'])
def test_postgres_fault_injected_metadata_blocks(pg_epoch, fault):
    h = pg_epoch
    # Fault injection ONLY in this fresh synthetic DB; never real history rows.
    with h['store'].connection() as conn:
        conn.execute('ALTER TABLE platform_compatibility_state DISABLE TRIGGER USER')
        if fault == 'missing':
            conn.execute('DELETE FROM platform_compatibility_state')
        elif fault == 'legacy_v2':
            conn.execute('ALTER TABLE agent_templates DISABLE TRIGGER USER')
            conn.execute(INSERT)
        else:
            constraints = conn.execute("SELECT conname FROM pg_constraint WHERE conrelid='platform_compatibility_state'::regclass AND contype='c'").fetchall()
            for row in constraints:
                conn.execute('ALTER TABLE platform_compatibility_state DROP CONSTRAINT '+row['conname'])
            conn.execute({'unknown':"UPDATE platform_compatibility_state SET epoch='future_v9'",
                          'rank':"UPDATE platform_compatibility_state SET epoch_rank=9",
                          'scope':"UPDATE platform_compatibility_state SET scope='other_database_contract'"}[fault])
    with pytest.raises(h['gate'].RollbackBlocked):
        pg_gate(h)
    assert pg_entry(h).returncode == 2 and not h['events'].exists()


def test_postgres_disabled_template_does_not_lower_floor(pg_epoch):
    h = pg_epoch
    approve_active_bd(h)
    advance(h)
    control = AgentProductization(h['store'])
    control.create_template({'slug':'epoch-pilot','name':'Synthetic'}, None)
    assert pg_gate(h,target_id=BD_ID,commit=BD_COMMIT)['compatibility_epoch'] == 'productized_v1'
    with pytest.raises(h['gate'].RollbackBlocked,match='below_data_compatibility_floor'):
        pg_gate(h)


@pytest.mark.parametrize('fault',['policy','active_identity','archive','checksum','pending_evidence'])
def test_postgres_advance_preconditions_fail_without_state_change(pg_epoch, fault):
    h = pg_epoch
    if fault != 'active_identity':
        approve_active_bd(h)
    if fault == 'archive':
        p = h['bd'].parent/f'{BD_ID}.tar.gz';p.write_bytes(p.read_bytes()+b'fault')
    elif fault == 'checksum':
        h['store'] = h['fresh_database'](fault='checksum010')
    elif fault == 'pending_evidence':
        p = h['new']/'deploy/rollback_compatibility.json';d=json.loads(p.read_text())
        d['epoch_contract']['schema_compatibility_evidence']['status']='PENDING'
        p.write_text(json.dumps(d));h['pack']()
    with pytest.raises((epoch.EpochBlocked,h['gate'].RollbackBlocked)):
        advance(h,policy_enabled=fault=='policy')
    with h['store'].connection() as conn:
        assert epoch.read_state(conn)['epoch']=='legacy_v1'


def test_postgres_concurrent_advance_and_create_never_commit_v2_before_epoch(pg_epoch):
    h = pg_epoch
    control = AgentProductization(h['store'])
    approve_active_bd(h)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(advance,h) for _ in range(2)]
        outcomes = [f.result(timeout=30)['status'] for f in futures]
    assert sorted(outcomes)==['advanced','already_advanced']
    control.create_template({'slug':'epoch-pilot','name':'Synthetic'},None)
    with h['store'].connection() as conn:
        assert epoch.read_state(conn)['epoch']=='productized_v1'


def test_postgres_epoch_advance_transaction_rollback(pg_epoch):
    h = pg_epoch
    with pytest.raises(RuntimeError),h['store'].connection() as conn:
        conn.execute("UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,advanced_at=CURRENT_TIMESTAMP,advance_origin='controlled_advance',advanced_by_release_id='synthetic',advanced_by_source_commit=?",('f'*40,))
        raise RuntimeError('synthetic transaction failure')
    with h['store'].connection() as conn:
        assert epoch.read_state(conn)['epoch']=='legacy_v1'


@pytest.mark.parametrize('commit_advance',[False,True])
def test_postgres_create_waits_for_advance_commit_or_blocks_after_rollback(pg_epoch,commit_advance):
    from psycopg.rows import dict_row
    h=pg_epoch;control=AgentProductization(h['store'])
    with psycopg.connect(h['store'].database_url,row_factory=dict_row) as advancing:
        advancing.execute("UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,advanced_at=CURRENT_TIMESTAMP,advance_origin='controlled_advance',advanced_by_release_id='synthetic',advanced_by_source_commit=%s",('f'*40,))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(control.create_template,{'slug':'concurrent-pilot','name':'Synthetic'},None)
            time.sleep(.15)
            assert not future.done(), 'V2 must not commit before the Epoch transaction'
            if commit_advance:
                advancing.commit();assert future.result(timeout=5)['definition_source']=='productized'
            else:
                advancing.rollback()
                with pytest.raises(AgentCatalogError,match='compatibility_epoch_not_advanced'):future.result(timeout=5)


def test_postgres_backup_restore_preserves_epoch_and_floor(pg_epoch):
    import uuid
    from psycopg import sql
    from urllib.parse import urlparse,parse_qs,urlencode
    h=pg_epoch;approve_active_bd(h);advance(h)
    with h['store'].connection() as c:before=epoch.read_state(c)
    parsed=urlparse(h['store'].database_url);args={k:v[0] for k,v in parse_qs(parsed.query).items()}
    pg=Path('/private/tmp/ky-web-stage1-postgres.bqKYMg')
    assert args['host']==str(pg/'socket') and (pg/'stage1-isolated.marker').read_text().strip()=='ky-web-stage1-local-only'
    binaries=pg/'pg16/bin'
    command=[str(binaries/'pg_dump'),'--host',args['host'],'--port',args['port'],'--username',args['user'],parsed.path[1:]]
    backup=subprocess.run(command,capture_output=True,check=True).stdout
    # Full synthetic dump is transferred only in memory, never copied to disk.
    name='rollback_restore_'+uuid.uuid4().hex
    with psycopg.connect(dbname='postgres',**args,autocommit=True) as admin:
        assert admin.execute('SHOW listen_addresses').fetchone()[0]==''
        assert Path(admin.execute('SHOW data_directory').fetchone()[0]).resolve()==pg/'cluster'
        admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
    restored=subprocess.run([str(binaries/'psql'),'--no-psqlrc','--quiet','--set','ON_ERROR_STOP=1','--single-transaction',
        '--host',args['host'],'--port',args['port'],'--username',args['user'],name],input=backup,capture_output=True)
    assert restored.returncode==0,'Isolated restore failed'
    h['store']=POCStore(f'postgresql:///{name}?{urlencode(args)}')
    with h['store'].connection() as c:assert epoch.read_state(c)==before
    with pytest.raises(h['gate'].RollbackBlocked,match='below_data_compatibility_floor'):pg_gate(h)
    assert pg_gate(h,target_id=BD_ID,commit=BD_COMMIT)['status']=='rollback_preflight_passed'


def test_postgres_rerun_cannot_reset_missing_applied_epoch(pg_epoch):
    h=pg_epoch
    with h['store'].connection() as c:
        c.execute('ALTER TABLE platform_compatibility_state DISABLE TRIGGER USER')
        c.execute('DELETE FROM platform_compatibility_state')
    with pytest.raises(psycopg.errors.RaiseException,match='compatibility_epoch_missing'),h['store'].connection() as c:
        c.execute(SQL.read_text())


def test_running_identity_checks_all_three_active_service_cwds(monkeypatch,tmp_path):
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(subprocess,'run',lambda args,**kwargs:(calls.append(args) or SimpleNamespace(stdout='MainPID=99\nActiveState=active\n')))
    original=Path.resolve
    monkeypatch.setattr(Path,'resolve',lambda self,**kwargs:tmp_path if str(self)=='/proc/99/cwd' else original(self,**kwargs))
    epoch.require_running_release(tmp_path)
    assert len(calls)==3 and {c[-1] for c in calls}=={'enterprise-agent-api.service','enterprise-agent-mcp.service','enterprise-agent-worker.service'}
    with pytest.raises(epoch.EpochBlocked,match='current_service_release_identity'):
        epoch.require_running_release(tmp_path/'wrong')
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='MainPID=0\nActiveState=inactive\n'))
    with pytest.raises(epoch.EpochBlocked,match='current_service_not_active'):
        epoch.require_running_release(tmp_path)


def test_historical_sql001010_bytes_unchanged_against_original_bd_artifact():
    if not BD_ROOT.exists():
        pytest.skip('Explicit immutable original Artifact unavailable')
    with tarfile.open(BD_ROOT/f'{BD_ID}.tar.gz') as archive:
        for p in migrate.migration_files()[:10]:
            assert p.read_bytes()==archive.extractfile(f'enterprise_agent_poc/migrations/postgres/{p.name}').read()


def test_original_bd_v2_control_rollback_restart_and_forward_restore(pg_epoch, tmp_path):
    original_artifact_e2e(pg_epoch,tmp_path)


def test_original_b930_current_and_productized_artifact_compatibility(pg_current,tmp_path):
    original_artifact_e2e(pg_current,tmp_path,current_b930=True)


def original_artifact_e2e(pg_epoch,tmp_path,*,current_b930=False):
    """Real PG/Redis/API/MCP/Worker; model/session I/O is explicitly Fake."""
    import os
    import socket
    import httpx
    from app.auth import hash_password
    from app.task_queue import RedisTaskQueue
    h=pg_epoch; root=h['base']
    compatible=h['b930'] if current_b930 else h['bd']
    (root/'epoch-isolation.marker').write_text('ky-web-epoch-isolated-v1')
    redis_binary=Path('/private/tmp/ky-web-stage2-readiness.m7EWh0/redis-7.4.2/src/redis-server')
    assert redis_binary.is_file()
    def free_port():
        with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
    api_port,mcp_port=free_port(),free_port()
    env={**h['env'], 'ENTERPRISE_POC_OBJECT_STORAGE_DIR':str(root/'objects'),
        'ENTERPRISE_POC_OBJECT_STORAGE_PROVIDER':'local','ENTERPRISE_POC_TASK_QUEUE':'redis',
        'ENTERPRISE_POC_TASK_QUEUE_NAMESPACE':'isolated-epoch',
        'REDIS_URL':f'unix://{root}/redis.sock?db=0',
        'ENTERPRISE_POC_MCP_URL':f'http://127.0.0.1:{mcp_port}/mcp',
        'ENTERPRISE_POC_MCP_HOST':'127.0.0.1','ENTERPRISE_POC_MCP_PORT':str(mcp_port),
        'ENTERPRISE_POC_MODEL_BASE_URL':'http://127.0.0.1:1/',
        'ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA':'false',
        'ENTERPRISE_POC_AGENT_RUNTIME_TEST_PRODUCTION_ENABLED':'false',
        'ENTERPRISE_POC_AGENT_RUNTIME_TEST_ALLOWED_TENANT_IDS':'tenant-a',
        'ENTERPRISE_POC_AGENT_RUNTIME_TEST_ALLOWED_TEMPLATE_SLUGS':'social-content-agent',
        'ENTERPRISE_POC_AGENT_RUNTIME_TEST_TENANT_ID':'tenant-a',
        'EMBEDDING_PROVIDER':'local-hash','EMBEDDING_MODEL':'local-hash-v1','EMBEDDING_DIMENSION':'128'}
    # Synthetic account only; fixture has never read any real credential file.
    product=ProductStore(h['store'])
    product.create_user('tenant-a','epoch@example.invalid',hash_password('EpochSynthetic!2026'),'Synthetic','enterprise_admin')
    actor=product.user_by_email('epoch@example.invalid')['id']
    h['registry'].grant_platform_admin(actor)
    registry_before=h['snapshot']()
    processes={}; logs={}
    launcher=Path(__file__).with_name('epoch_service.py')
    def start(role,source,policy=False):
        log=(root/f'{role}-{len(logs)}.log').open('wb');logs[len(logs)]=log
        args=[sys.executable,str(launcher),role,'--source',str(source),'--root',str(root),'--port',str(api_port)]
        processes[role]=subprocess.Popen(args,cwd=source,env={**env,'ENTERPRISE_POC_AGENT_RUNTIME_TEST_PRODUCTION_ENABLED':str(policy).lower()},stdout=log,stderr=log)
    def stop(role):
        p=processes.pop(role,None)
        if p and p.poll() is None:
            p.terminate()
            try:p.wait(timeout=10)
            except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
    def ready():
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            try:
                if httpx.get(f'http://127.0.0.1:{api_port}/api/health').status_code==200:return
            except httpx.HTTPError:pass
            assert processes['api'].poll() is None, 'Isolated API process exited'
            time.sleep(.1)
        raise AssertionError('Isolated API readiness timeout')
    def login(client):
        assert client.post('/api/v1/auth/login',json={'account':'epoch@example.invalid','password':'EpochSynthetic!2026'}).status_code==200
    def poll_task(client,tid):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            result=client.get(f'/api/v1/tasks/{tid}').json()
            if result['status'] in {'completed','failed','cancelled'}:
                assert result['status']=='completed',result.get('error_code')
                return result
            time.sleep(.1)
        raise AssertionError('Isolated Worker task timeout')
    def history_snapshot():
        tables=('platform_compatibility_state','agent_templates','agent_template_versions','agent_template_version_skills',
            'agent_template_version_tools','agent_template_tests','agent_execution_contexts','conversation_agent_contexts',
            'task_agent_contexts','tasks','task_results','task_events','conversations','run_traces','messages',
            'credit_accounts','credit_transactions','tenant_agent_instances')
        with h['store'].connection() as c:
            return {t:sorted([dict(r) for r in c.execute(f'SELECT * FROM {t}')],key=lambda x:json.dumps(x,sort_keys=True,default=str)) for t in tables}
    evidence={}
    try:
        redis_log=(root/'redis.log').open('wb');logs['redis']=redis_log
        processes['redis']=subprocess.Popen([str(redis_binary),'--port','0','--unixsocket',str(root/'redis.sock'),
            '--unixsocketperm','700','--save','','--appendonly','no'],stdout=redis_log,stderr=redis_log)
        deadline=time.monotonic()+5
        while not (root/'redis.sock').exists() and time.monotonic()<deadline:time.sleep(.05)
        q=RedisTaskQueue(env['REDIS_URL'],'isolated-epoch');assert q.ping()
        # Original candidate itself under legacy_v1 for the b930-specific E2E;
        # preserve the historical bd/old-floor E2E and its evidence unchanged.
        with h['store'].connection() as c:
            assert epoch.read_state(c)['epoch']=='legacy_v1' and not epoch.has_productized_data(c)
        for role in ('api','mcp','worker'):start(role,compatible if current_b930 else h['old'],False)
        ready()
        with httpx.Client(base_url=f'http://127.0.0.1:{api_port}',timeout=10) as old_api:
            login(old_api)
            result=old_api.get('/api/v1/agents').json()
            old_agents=result['agents'] if isinstance(result,dict) else result
            assert {a['id'] for a in old_agents}=={'image-agent','copywriting-agent','campaign-agent'}
            legacy_task=old_api.post('/api/v1/agents/copywriting-agent/runs',json={'message':'Synthetic old Legacy on 011'})
            assert legacy_task.status_code==202;poll_task(old_api,legacy_task.json()['id'])
        for role in ('api','mcp','worker'):stop(role)
        start('api',compatible if current_b930 else Path(__file__).resolve().parents[1]);ready()
        with httpx.Client(base_url=f'http://127.0.0.1:{api_port}',timeout=10) as api:
            login(api)
            blocked=api.post('/api/v1/platform/agents',json={'slug':'social-content-agent','name':'Synthetic social content'})
            assert blocked.status_code==409 and blocked.json()['detail']=='compatibility_epoch_not_advanced'
            stop('api')
            for role in ('api','mcp','worker'):start(role,compatible,False)
            ready()
            if current_b930:activate_b930(h)
            else:approve_active_bd(h)
            # Local attestation: all explicit subprocess source/cwd are original
            # bd and alive; production CLI instead enforces actual systemd/procfs.
            assert all(processes[role].poll() is None for role in ('api','mcp','worker'))
            if current_b930:
                def native_running_identity(source):
                    assert source==compatible
                    for role in ('api','mcp','worker'):
                        p=processes[role];assert p.poll() is None
                        cwd=subprocess.run(['/usr/sbin/lsof','-a','-p',str(p.pid),'-d','cwd','-Fn'],capture_output=True,text=True,check=True)
                        assert 'n'+str(source) in cwd.stdout.splitlines()
                with pytest.raises(psycopg.errors.RaiseException,match='compatibility_epoch_not_advanced'),h['store'].connection() as c:
                    c.execute(INSERT)
                assert epoch.advance(h['base'],h['new'],h['store'].database_url,running_identity_check=native_running_identity)['status']=='advanced'
                with h['store'].connection() as c:
                    provenance=epoch.read_state(c)
                    assert provenance['advanced_by_release_id']=='fixture-new'!=B930_ID
                    assert provenance['advanced_by_source_commit']=='f'*40!=B930_COMMIT
                for rid,commit in ((BD_ID,BD_COMMIT),(B930_ID,B930_COMMIT)):
                    assert pg_gate(h,target_id=rid,commit=commit)['status']=='rollback_preflight_passed'
                with pytest.raises(h['gate'].RollbackBlocked,match='below_data_compatibility_floor'):pg_gate(h)
            else:assert advance(h)['status']=='advanced'
            for role in ('api','mcp','worker'):stop(role)
            # Every application process loads original immutable candidate bytes.
            start('api',compatible,True);start('mcp',compatible,True);start('worker',compatible,True);ready();login(api)
            r=api.post('/api/v1/platform/agents',json={'slug':'social-content-agent','name':'Synthetic social content'})
            assert r.status_code==201,r.text
            tid=r.json()['id']
            detail=api.post(f'/api/v1/platform/agents/{tid}/versions',json={'persona':'Synthetic fixed Persona','credit_cost':3,
                'knowledge_requirement':'none','asset_requirement':'none','enterprise_config_requirement':'optional'}).json()
            v=detail['versions'][0];endpoint=f"/api/v1/platform/agents/{tid}/versions/{v['id']}"
            with h['store'].connection() as c:
                skill=dict(c.execute("SELECT s.id AS skill_id,v.id AS skill_version_id FROM skills s JOIN skill_versions v ON v.skill_id=s.id WHERE s.slug='social-copywriting' AND v.status='published'").fetchone())
            assert api.put(endpoint+'/skills',json={'bindings':[skill]}).status_code==200
            assert api.put(endpoint+'/tools',json={'bindings':[{'tool_capability_id':'config_get','invocation_requirement':'optional'}]}).status_code==200
            assert api.post(endpoint+'/validation').status_code==200
            v=api.get(f'/api/v1/platform/agents/{tid}').json()['versions'][0]
            queued=api.post(endpoint+'/test',json={'configuration_fingerprint':v['configuration_fingerprint']})
            assert queued.status_code==202,queued.text
            test_task=queued.json()['task_id'];poll_task(api,test_task)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                with h['store'].connection() as c:
                    state=c.execute('SELECT status FROM agent_template_tests WHERE task_id=?',(test_task,)).fetchone()['status']
                if state in {'passed','failed','invalidated'}:break
                time.sleep(.05)
            with h['store'].connection() as c:
                test=dict(c.execute('SELECT * FROM agent_template_tests WHERE task_id=?',(test_task,)).fetchone())
                assert test['status']=='passed'
                evidence['worker_independent']=json.loads(test['result_json'])['worker_pid']!=processes['api'].pid
            assert evidence['worker_independent']
            assert api.post(endpoint+'/publish',json={'mode':'production'}).status_code==200
            assert api.put(f'/api/v1/platform/agents/{tid}/instances/tenant-a',json={'agent_template_version_id':v['id'],'overrides':{}}).status_code==200
            assert api.post(f'/api/v1/platform/agents/{tid}/instances/tenant-a/enable').status_code==200
            run=lambda conv=None:api.post('/api/v1/agents/social-content-agent/runs',json={'message':'Synthetic content','conversation_id':conv})
            first=run();assert first.status_code==202,first.text
            with h['store'].connection() as c:
                assert epoch.workload_status(c,q)['status']=='ROLLBACK_INCOMPLETE'
            done=poll_task(api,first.json()['id']);conv=done['conversation_id']
            second=run(conv);assert second.status_code==202,second.text
            poll_task(api,second.json()['id'])
            with h['store'].connection() as c:
                contexts=[dict(r) for r in c.execute('SELECT c.id,c.agent_template_version_id,c.configuration_fingerprint,c.credit_cost FROM task_agent_contexts m JOIN agent_execution_contexts c ON c.id=m.context_id WHERE m.task_id IN (?,?)',(first.json()['id'],second.json()['id']))]
                assert len(contexts)==2 and contexts[0]==contexts[1] and contexts[0]['credit_cost']==3
                credits=[dict(r) for r in c.execute('SELECT task_id,amount FROM credit_transactions WHERE task_id IN (?,?)',(first.json()['id'],second.json()['id']))]
                assert len(credits)==2 and all(r['amount']==-3 for r in credits)
            assert api.post(f'/api/v1/platform/agents/{tid}/instances/tenant-a/disable').status_code==200
            assert run().status_code==run(conv).status_code==404
            # Drain before stopping consumers; terminal duplicate delivery counts.
            deadline=time.monotonic()+5
            while q._client.llen(q.processing) and time.monotonic()<deadline:time.sleep(.05)
            with h['store'].connection() as c:assert epoch.workload_status(c,q)['status']=='quiesced'
            stop('worker')
            # Terminal rows do not excuse a recoverable Redis delivery/lease.
            q.enqueue(first.json()['id'])
            with h['store'].connection() as c:
                state=epoch.workload_status(c,q)
                assert state['status']=='ROLLBACK_INCOMPLETE' and state['recoverable_v2_deliveries']==1
            assert q.reserve(timeout=1)==first.json()['id']
            with h['store'].connection() as c:
                state=epoch.workload_status(c,q)
                assert state['status']=='ROLLBACK_INCOMPLETE' and state['active_v2_processing_leases']==1
            q.acknowledge(first.json()['id'])
            for role in ('api','mcp','worker'):stop(role)
            before=history_snapshot()
            for role in ('api','mcp','worker'):start(role,compatible,False)
            ready();login(api)
            assert api.get(f'/api/v1/conversations/{conv}').status_code==200
            assert api.get(f'/api/v1/platform/agents/{tid}').json()['versions'][0]['status']=='published'
            assert before==history_snapshot()
            # Policy disabled on a NEW Draft, not inferred from an enabled flag.
            draft=api.post(f'/api/v1/platform/agents/{tid}/versions',json={'from_version_id':v['id']}).json()['versions'][0]
            denied=api.post(f"/api/v1/platform/agents/{tid}/versions/{draft['id']}/test",json={'configuration_fingerprint':draft['configuration_fingerprint']})
            assert denied.status_code==409 and 'policy denied' in denied.json()['detail']
            legacy=api.get('/api/v1/agents').json()
            agents=legacy['agents'] if isinstance(legacy,dict) else legacy
            assert {a['id'] for a in agents}=={'image-agent','copywriting-agent','campaign-agent'}
            legacy_run=api.post('/api/v1/agents/copywriting-agent/runs',json={'message':'Synthetic legacy regression'})
            assert legacy_run.status_code==202;poll_task(api,legacy_run.json()['id'])
            for role in ('api','mcp','worker'):stop(role)
            for role in ('api','mcp','worker'):start(role,compatible,True)
            ready();login(api)
            assert api.post(f'/api/v1/platform/agents/{tid}/instances/tenant-a/enable').status_code==200
            restored=run(conv);assert restored.status_code==202,restored.text
            poll_task(api,restored.json()['id'])
            with h['store'].connection() as c:
                assert c.execute('SELECT context_id FROM task_agent_contexts WHERE task_id=?',(restored.json()['id'],)).fetchone()['context_id']==contexts[0]['id']
                assert c.execute('SELECT COUNT(*) AS n FROM credit_transactions WHERE task_id=?',(restored.json()['id'],)).fetchone()['n']==1
                state=epoch.read_state(c);assert state['epoch']=='productized_v1'
            # No original Artifact byte can have changed through startup/restart.
            d=json.loads((h['new']/'deploy/rollback_compatibility.json').read_text())
            h['gate'].release_identity(h['base'],BD_ID,BD_COMMIT,d['approved_targets'][1])
            h['gate'].release_identity(h['base'],h['old_id'],h['commit'],d['approved_targets'][0])
            evidence.update(status='PASS',runtime='FAKE model/session boundary; real persistence/queue',
                original_release_id=BD_ID,source_commit=BD_COMMIT,schema_applied=11,
                service_create_block='PASS',database_create_block='PASS',advance='PASS',
                productized_011='PASS',restart='PASS',control_plane_rollback='PASS',reenable='PASS',
                legacy_011='PASS',legacy_minimal_run='PASS',fixed_resume_context='PASS',credit_idempotency='PASS',
                legacy_old_011='PASS',policy_disable_and_reenable='PASS',
                skill_package_counts={k:len(v) for k,v in h['snapshot']()[0].items() if k!='schema_migrations'},production_touched=False)
            assert h['snapshot']()==registry_before
            if current_b930:
                record=next(t for t in d['approved_targets'] if t['release_id']==B930_ID)
                h['gate'].release_identity(h['base'],B930_ID,B930_COMMIT,record)
                assert pg_gate(h,target_id=B930_ID,commit=B930_COMMIT)['compatibility_epoch']=='productized_v1'
                assert pg_gate(h,target_id=BD_ID,commit=BD_COMMIT)['compatibility_epoch']=='productized_v1'
                with pytest.raises(h['gate'].RollbackBlocked,match='below_data_compatibility_floor'):pg_gate(h)
                _,manifest=original_b930_manifest()
                # NEW attestation, not relabeled/reused historical bd evidence.
                evidence={'version':B930_EVIDENCE_VERSION,'release_id':B930_ID,'source_commit':B930_COMMIT,
                    'archive_sha256':B930_ARCHIVE_SHA,'canonical_manifest_sha256':h['gate'].digest(manifest),'selected_file_count':87,
                    'postgres_major':16,'schema_baseline':'001-011','schema_lock_sha256':h['gate'].digest(d['epoch_contract']['schema_migrations']),
                    'epoch_scenarios':{'legacy_v1_zero_v2':'PASS','productized_v1_zero_v2':'PASS','productized_v1_with_disabled_history':'PASS'},
                    'checks':dict.fromkeys(('api','mcp','worker','legacy_minimal_run','service_create_block','database_create_block',
                        'controlled_advance','tooling_application_separation','native_process_cwd','restart','template_revision_create',
                        'disabled_history_read','runtime_test_persistence','productized_run_resume','credit_idempotency',
                        'rollback_allowed_target','minimum_bd_floor_unchanged','old_floor_rejected','forward_restore','artifact_bytes_unchanged'),'PASS'),
                    'legacy_result':'PASS: original three Agents and minimal Legacy execution',
                    'productized_result':'PASS: isolated control-plane and independent Redis Worker persistence',
                    'runtime_boundary':'FAKE model/session I/O and Skill discovery; not real Provider certification',
                    'worker_independent':True,'production_touched':False,'final_status':'PASS'}
            target=Path(os.environ.get('EPOCH_EVIDENCE_DIR',str(tmp_path)))/('b930-compatibility-e2e.json' if current_b930 else 'epoch-e2e.json')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(evidence,sort_keys=True,indent=2))
            if current_b930:
                import hashlib
                assert hashlib.sha256(target.read_bytes()).hexdigest()==B930_EVIDENCE_SHA
    finally:
        for role in tuple(processes):stop(role)
        for log in logs.values():log.close()
