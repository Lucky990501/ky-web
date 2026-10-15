"""Only metadata reference regression; no live secret/SSH/Prepare execution."""
import ast
import copy
import json
import os
from pathlib import Path
import stat
import types

import pytest
from test_successor import module

HERE = Path(__file__).parent


@pytest.fixture
def c():
    return module('metadata_only_v4', HERE / 'entry.py')


@pytest.fixture
def metadata_fixture(c, monkeypatch, tmp_path):
    c.ROOT = tmp_path / 'test-root'; c.ROOT.mkdir()
    c.KEY = c.ROOT / 'shared/credentials/wechat-tenant-secret-d8a-seeding-approval-v1.key'
    c.SECRET = c.ROOT / 'data/application/tenant-secrets/test/fixture.fernet'
    c.METADATA_DIRECTORIES = tuple(tmp_path / name for name in ('persistent','recovery','seeding','d8a-contract'))
    for p in c.METADATA_DIRECTORIES: p.mkdir()
    c.SECRET_KEY_DECLARATION = c.METADATA_DIRECTORIES[-1] / 'common.py'
    c.CURRENT_SECRET_WITNESS = c.METADATA_DIRECTORIES[0] / 'current-state-witness.v1.json'
    c.SECRET_KEY_DROPINS = tuple(tmp_path / (role+'.conf') for role in ('api','mcp','worker'))
    required = [c.KEY, c.SECRET, c.ROOT/'shared/config/test-environment.env',
                c.ROOT/'shared/credentials/test-environment.secrets.env']
    for path in required:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'ISOLATED_FIXTURE_NOT_REAL_SECRET_DO_NOT_EMIT\n'); path.chmod(0o600)
    c.SECRET_KEY_DECLARATION.write_text('KEY_FILE=R/'+repr(str(c.KEY.relative_to(c.ROOT)))+'\n')
    for path in c.SECRET_KEY_DROPINS:
        path.write_text('Environment='+c.SECRET_KEY_ENV+'='+str(c.KEY)+'\n')
    c.CURRENT_SECRET_WITNESS.write_text(json.dumps({'encrypted_secret_record':{
        'path':str(c.SECRET),'sha256':c.sha(c.SECRET),'size':c.SECRET.stat().st_size}}))
    for name in ('approval.v1.json','policy.v1.json','terminal-task-witness.v1.json',
                 'primary_guard.py','wechat_persistent_config_guard.py','scripts/receipt_row_canonicalization.py'):
        p = c.METADATA_DIRECTORIES[0]/name; p.parent.mkdir(exist_ok=True); p.write_text('# fixture')
    # Synthetic ownership only: no test acquires Root or changes a system dir.
    real_lstat = Path.lstat
    def lstat(path):
        info = real_lstat(path)
        if path in c.METADATA_DIRECTORIES or path == Path('/tmp'):
            return types.SimpleNamespace(st_mode=stat.S_IFDIR|0o755,st_uid=0,st_gid=0,
                st_nlink=info.st_nlink,st_size=info.st_size)
        return info
    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(c,'native',lambda *a,**kw: None)
    monkeypatch.delenv(c.SECRET_KEY_ENV,raising=False)
    return c


def test_old_wrong_key_missing_reproduced_and_correct_key_pinned(metadata_fixture):
    c=metadata_fixture; correct=c.KEY; wrong=c.ROOT/'shared/credentials/wechat-tenant-secret-d8a-v1.key'
    c.KEY=wrong
    report=c.metadata_inventory()
    assert report['status']=='BLOCKED' and any(x['path']==str(wrong) for x in report['issues'])
    c.KEY=correct
    assert not wrong.exists()
    report=c.metadata_inventory()
    assert report['status']=='PASS' and str(correct) in c.metadata_pins()
    assert str(wrong) not in report['files']


@pytest.mark.parametrize('which',['key','ciphertext'])
@pytest.mark.parametrize('bad',['mode','symlink','hardlink'])
def test_current_secret_protection_not_weakened(metadata_fixture,which,bad):
    c=metadata_fixture; path=c.KEY if which=='key' else c.SECRET
    if bad=='mode': path.chmod(0o644)
    elif bad=='hardlink': os.link(path,path.with_name('extra-link'))
    else:
        old=path.with_name('original-preserved'); path.rename(old); path.symlink_to(old)
    report=c.metadata_inventory()
    assert report['status']=='BLOCKED' and any(x['path']==str(path) for x in report['issues'])


def test_all_missing_metadata_reported_together(metadata_fixture):
    c=metadata_fixture
    config=c.ROOT/'shared/config/test-environment.env'
    # Private fixture removals only; no actual machine files are touched.
    c.KEY.unlink(); config.unlink()
    missing=c.METADATA_DIRECTORIES[2]; missing.rmdir()
    report=c.metadata_inventory()
    found={i['path'] for i in report['issues']}
    assert {str(c.KEY),str(config),str(missing)}<=found
    assert report['status']=='BLOCKED'


def test_parent_write_or_link_rejected(metadata_fixture):
    c=metadata_fixture; c.KEY.parent.chmod(0o777)
    assert c.metadata_inventory()['status']=='BLOCKED'


@pytest.mark.parametrize('which',['declaration','api-dropin','process-env','cipher-witness'])
def test_runtime_contract_reference_drift_refused(metadata_fixture,which,monkeypatch):
    c=metadata_fixture
    if which=='declaration': c.SECRET_KEY_DECLARATION.write_text("KEY_FILE=R/'shared/credentials/wrong.key'\n")
    elif which=='api-dropin': c.SECRET_KEY_DROPINS[0].write_text('Environment='+c.SECRET_KEY_ENV+'=/wrong.key\n')
    elif which=='process-env': monkeypatch.setenv(c.SECRET_KEY_ENV,'/wrong.key')
    else:
        value=c.read(c.CURRENT_SECRET_WITNESS);value['encrypted_secret_record']['sha256']='0'*64
        c.CURRENT_SECRET_WITNESS.write_text(json.dumps(value))
    assert c.metadata_inventory()['status']=='BLOCKED'


def test_secret_bytes_never_in_metadata_report(metadata_fixture):
    c=metadata_fixture
    raw=json.dumps(c.metadata_inventory())
    assert 'ISOLATED_FIXTURE_NOT_REAL_SECRET' not in raw
    assert c.sha(c.KEY) in raw and c.sha(c.SECRET) in raw


def test_current_key_and_historical_dropin_bytes_unchanged_by_readback(metadata_fixture):
    c=metadata_fixture
    paths=[c.KEY,c.SECRET,c.SECRET_KEY_DECLARATION,c.CURRENT_SECRET_WITNESS,*c.SECRET_KEY_DROPINS]
    before={str(p):(c.sha(p),p.stat().st_mode) for p in paths}
    first=c.metadata_pins(); second=c.metadata_pins()
    assert first==second and before=={str(p):(c.sha(p),p.stat().st_mode) for p in paths}


def test_missing_metadata_fails_before_attempt_or_source_install(c,monkeypatch,tmp_path):
    c.OP=tmp_path/'not-created'
    monkeypatch.setattr(c,'authority',lambda *a: {})
    monkeypatch.setattr(c,'old_state',lambda: {})
    def fail(): raise c.Blocked('MISSING_METADATA_FIXTURE')
    monkeypatch.setattr(c,'metadata_pins',fail)
    monkeypatch.setattr(c,'source_identity',lambda *a: pytest.fail('metadata must close before install checks'))
    with pytest.raises(c.Blocked,match='MISSING_METADATA'): c.prepare()
    assert not c.OP.exists()


def test_backup_still_uses_exact_current_test_key_not_fake_or_prod(c):
    assert c.KEY==c.ROOT/'shared/credentials/wechat-tenant-secret-d8a-seeding-approval-v1.key'
    assert c.KEY!=c.ROOT/'shared/credentials/wechat-tenant-secret-d8a-v1.key'
    recovery=(HERE/'existing_recovery_bound.py').read_text()
    assert '"--passphrase-file", c.KEY' in recovery
    assert 'PRODUCTION' not in str(c.KEY)


def test_original_prepare_still_refuses_retry_and_old_records(c):
    fn=next(n for n in ast.parse((HERE/'entry.py').read_bytes()).body if isinstance(n,ast.FunctionDef) and n.name=='prepare')
    text=ast.unparse(fn)
    assert 'NO_RETRY_OR_OVERWRITE' in text
    assert text.index('metadata_pins()')<text.index('OP.mkdir(')
    assert 'METADATA_CHANGED_DURING_PREPARE' in text
