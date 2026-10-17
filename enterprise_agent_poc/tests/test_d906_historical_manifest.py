"""Actual pinned d906 readers and immutable candidate CLI regressions.

These marked, private artifact/PG tests do not issue authority or prove a
Production release. Genuine historical bytes are mandatory, never fixtures
which fabricate a historical Manifest or relax a frozen consumer.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from urllib.parse import urlencode
from uuid import uuid4

import pytest

from scripts import rollback_preflight as gate, schema015_successor_binding as binding
from scripts import build_release, release_runtime_recovery as recovery, release_verify

ROOT=Path(__file__).resolve().parents[1]
D906=binding.PREDECESSOR


@pytest.fixture(scope='module')
def immutable_pair(tmp_path_factory):
    archive_root=Path(os.environ['D906_HISTORICAL_ARTIFACT_FIXTURE'])
    assert archive_root.is_absolute() and archive_root.is_dir()
    rid=D906['release_id'];archive=archive_root/(rid+'.tar.gz');manifest=archive_root/(rid+'.manifest.json')
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==D906['archive_sha256']
    assert hashlib.sha256(manifest.read_bytes()).hexdigest()==D906['raw_manifest_sha256']
    base=tmp_path_factory.mktemp('exact-d906-immutable-pair')
    releases=base/'releases';releases.mkdir()
    prior=releases/rid;prior.mkdir()
    for path in (archive,manifest):shutil.copyfile(path,prior/path.name)
    with tarfile.open(archive) as tf:tf.extractall(prior,filter='data')
    source=subprocess.check_output(['git','-C',str(ROOT.parent),'rev-parse','HEAD'],text=True).strip()
    own=releases/'historical-contract-test';own.mkdir()
    result=build_release.build(source,own/'historical-contract-test.tar.gz')
    value=binding.prepare(ROOT.parent,source)
    current=build_release.write_manifest(result,own.name,own/(own.name+'.manifest.json'),runtime_only_release=value)
    with tarfile.open(own/(own.name+'.tar.gz')) as tf:tf.extractall(own,filter='data')
    return base,current


@pytest.fixture
def pair(tmp_path,immutable_pair):
    original,current=immutable_pair
    base=tmp_path/'isolated-pair'
    shutil.copytree(original,base)
    prior=base/'releases'/D906['release_id']/'enterprise_agent_poc'
    own=base/'releases'/current['release_id']/'enterprise_agent_poc'
    (base/'release-current').symlink_to(prior,target_is_directory=True)
    (base/'shared/release-qualifications').mkdir(parents=True)
    return base,own,prior,copy.deepcopy(current)


def test_real_d906_original_contract_retains_562_without_changing_current_target(pair):
    base,own,prior,current=pair
    actual,m=gate.release_identity(base,D906['release_id'],D906['source_commit'],D906)
    assert actual==prior and m['runtime_only_release']['exact_predecessor']['source_commit']=='562f201e362f0b10ae4fe3a8dae9a8c4d9ef91d3'
    assert binding.declaration(own,current)['exact_predecessor']==D906
    assert recovery.predecessor(base,own)==(prior,m)


def test_real_current_successor_contract_and_installed_pins_pass(pair):
    base,own,prior,current=pair
    actual,m=gate.release_identity(base,current['release_id'],current['source_commit'])
    assert actual==own and m==current
    assert binding.declaration(own,m)['successor']['source_commit']==m['source_commit']


@pytest.mark.parametrize('fault',['manifest_bytes','archive_bytes','installed_bytes','wrong_source','wrong_rid'])
def test_exact_historical_identity_and_bytes_reject_tampering(pair,fault):
    base,own,prior,current=pair
    rid=D906['release_id'];source=D906['source_commit']
    if fault=='manifest_bytes':
        p=prior.parent/(rid+'.manifest.json');p.write_bytes(p.read_bytes()+b' ')
    elif fault=='archive_bytes':
        p=prior.parent/(rid+'.tar.gz');p.write_bytes(p.read_bytes()+b'fault')
    elif fault=='installed_bytes':
        p=prior/'scripts/schema015_successor_binding.py';p.write_bytes(p.read_bytes()+b'\n# tampered\n')
    elif fault=='wrong_source':source='a'*40
    else:
        renamed=prior.parent.with_name('alias-d906');prior.parent.rename(renamed)
        (renamed/(rid+'.manifest.json')).rename(renamed/'alias-d906.manifest.json')
        (renamed/(rid+'.tar.gz')).rename(renamed/'alias-d906.tar.gz');rid='alias-d906'
    with pytest.raises(gate.RollbackBlocked):gate.release_identity(base,rid,source,D906)


def test_current_successor_cannot_recover_to_historical_562(pair):
    base,own,prior,m=pair
    m['runtime_only_release']['exact_predecessor']['source_commit']='562f201e362f0b10ae4fe3a8dae9a8c4d9ef91d3'
    with pytest.raises(binding.BindingBlocked,match='fixed_contract'):binding.declaration(own,m)


def test_current_successor_pin_mismatch_rejected(pair):
    base,own,prior,m=pair
    m['runtime_only_release']['successor']['tooling_pins']['scripts/rollback_preflight.py']='0'*64
    with pytest.raises(binding.BindingBlocked,match='file_identity'):binding.declaration(own,m)


@pytest.mark.parametrize('fault',['source','tree','pin'])
def test_full_release_preflight_rejects_current_identity_against_exact_approval_pins(pair,monkeypatch,fault):
    base,own,prior,m=pair
    path=own.parent/(m['release_id']+'.manifest.json')
    original=path.read_bytes()
    for key,value in {'SOURCE_COMMIT':m['source_commit'],'ARCHIVE_SHA256':m['archive_sha256'],
                      'RAW_MANIFEST_SHA256':hashlib.sha256(original).hexdigest(),
                      'CANONICAL_MANIFEST_SHA256':gate.digest(m)}.items():
        monkeypatch.setenv('RELEASE_EXPECTED_'+key,value)
    if fault=='source':m['source_commit']='a'*40
    elif fault=='tree':m['runtime_only_release']['successor']['source_tree']='b'*40
    else:m['runtime_only_release']['successor']['tooling_pins']['scripts/rollback_preflight.py']='c'*64
    path.write_text(json.dumps(m),encoding='utf-8')
    monkeypatch.setattr(release_verify,'ROOT',own)
    with pytest.raises(release_verify.GateFailed,match='approved_candidate_identity'):
        release_verify.final_preflight(base,path,prior.parent/(D906['release_id']+'.manifest.json'))


def test_formal_release_reads_exact_original_d906_before_service_boundary(pair,monkeypatch):
    base,own,prior,m=pair;path=own.parent/(m['release_id']+'.manifest.json')
    for key,value in {'SOURCE_COMMIT':m['source_commit'],'ARCHIVE_SHA256':m['archive_sha256'],
                      'RAW_MANIFEST_SHA256':hashlib.sha256(path.read_bytes()).hexdigest(),
                      'CANONICAL_MANIFEST_SHA256':gate.digest(m)}.items():monkeypatch.setenv('RELEASE_EXPECTED_'+key,value)
    monkeypatch.setattr(release_verify,'ROOT',own)
    # Explicit regression boundary: never pretend these are Native services.
    def stop_after_identity(_base):raise RuntimeError('EXACT_PAIR_PASSED_BEFORE_SERVICE_BOUNDARY')
    monkeypatch.setattr(release_verify,'service_state',stop_after_identity)
    with pytest.raises(RuntimeError,match='EXACT_PAIR_PASSED_BEFORE_SERVICE_BOUNDARY'):
        release_verify.final_preflight(base,path,prior.parent/(D906['release_id']+'.manifest.json'))


@pytest.fixture
def private_ledger(pair):
    import psycopg
    from psycopg import sql
    from app.store import POCStore
    from scripts import migrate
    pg=Path(os.environ['STAGE1_POSTGRES_ROOT'])
    assert pg.is_absolute() and pg.stat().st_uid==os.getuid()
    assert (pg/'stage1-isolated.marker').read_text().strip()=='ky-web-stage1-local-only'
    args=dict(dbname='postgres',host=str(pg/'socket'),port=int(os.environ.get('STAGE1_POSTGRES_PORT','54329')),user='stage1_fixture')
    name='history_d906_'+uuid4().hex
    with psycopg.connect(**args,autocommit=True) as admin:
        assert Path(admin.execute('SHOW data_directory').fetchone()[0])==pg/'cluster'
        assert admin.execute('SHOW listen_addresses').fetchone()[0]==''
        admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
    url='postgresql:///'+name+'?'+urlencode({k:args[k] for k in ('host','port','user')})
    store=POCStore(url);store.initialize();migrate.up(store)
    return url


@pytest.mark.parametrize('entry',['runtime_preflight','recovery_target'])
def test_real_linux_cli_uses_installed_pair_and_keeps_authority_guard(pair,private_ledger,entry):
    base,own,prior,m=pair;path=own.parent/(m['release_id']+'.manifest.json')
    script,mode=('schema_015_runtime_only.py','preflight') if entry=='runtime_preflight' else ('release_runtime_recovery.py','target')
    env={'PATH':os.environ['PATH'],'HOME':str(base),'APP_ENV':'test','PYTHONDONTWRITEBYTECODE':'1',
         'DEEPSEEK_API_KEY':'unused-isolated-placeholder','GATEWAY_API_TOKEN':'image-disabled',
         'EMBEDDING_PROVIDER':'local-hash','EMBEDDING_MODEL':'local-hash-v1','EMBEDDING_DIMENSION':'128',
         'ENTERPRISE_POC_DATABASE_URL':private_ledger,'ENTERPRISE_POC_DATA_DIR':str(base/'data'),
         'ENTERPRISE_POC_OBJECT_STORAGE_DIR':str(base/'objects')}
    result=subprocess.run([sys.executable,'-I','-B',str(own/'scripts'/script),mode,'--candidate-manifest',str(path)],env=env,capture_output=True,text=True,timeout=45)
    assert result.returncode==2,result.stdout+result.stderr
    outcome=json.loads(result.stdout)
    assert outcome['status']=='BLOCKED' and outcome['check']=='qualification_trusted_ancestor',outcome
    assert not any((base/'shared/release-qualifications').iterdir())


def test_frozen_historical_reader_failure_is_closed(pair,monkeypatch):
    base,own,prior,m=pair
    def unavailable(*args,**kwargs):raise subprocess.TimeoutExpired('frozen-d906-reader',15)
    monkeypatch.setattr(gate.subprocess,'run',unavailable)
    with pytest.raises(gate.RollbackBlocked,match='historical_d906_frozen_contract'):
        gate.release_identity(base,D906['release_id'],D906['source_commit'],D906)
