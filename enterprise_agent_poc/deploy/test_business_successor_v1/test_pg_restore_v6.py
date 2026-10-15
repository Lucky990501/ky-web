"""Directed restore contract tests; actual PG proof is a separate Test fixture."""
import ast
import copy
import hashlib
from pathlib import Path
import types

import pytest
from test_successor import module

HERE=Path(__file__).parent


@pytest.fixture
def pair():
    return module('restore_controller_v6',HERE/'entry.py'),module('restore_helper_v6',HERE/'existing_recovery_bound.py')


@pytest.fixture
def contract(pair):
    c,h=pair
    return {'mode':h.PG_RESTORE_MODE,'database':'enterprise_agent_test','version':'16.6','port':55432,
        'business_executor':'enterprise_agent_test','maintenance_verifier':'eaw_test_admin',
        'maintenance_readonly':True,'maintenance_catalog_match':True,'business_public_create':True,
        'catalog':{'database':[{'owner':'enterprise_agent_test','acl':None}],
        'extensions':[{'extname':'vector','extversion':'0.8.6','schema':'public','owner':'eaw_test_admin'},
                      {'extname':'plpgsql','extversion':'1.0','schema':'pg_catalog','owner':'eaw_test_admin'}],
        'schemas':[{'nspname':'public','owner':'pg_database_owner'}],
        'relations':[{'owner':'enterprise_agent_test','acl':None}], 'functions':[], 'types':[],
        'default_acls':[], 'business_role':[dict(rolsuper=False,rolcreatedb=False,rolcreaterole=False,
            rolreplication=False,rolbypassrls=False)]}}


def test_exact_unchanged_extension_and_business_role_contract(pair,contract):
    c,h=pair
    assert h.validate_pg_contract(c,contract)==contract


@pytest.mark.parametrize('bad',['vector-version','vector-owner','vector-schema','other-extension',
    'public-owner','no-create','object-acl','default-acl','business-superuser','relation-owner',
    'maintenance-write','maintenance-mismatch','wrong-database','wrong-role','wrong-mode'])
def test_bad_restore_identity_schema_ownership_acl_rejected(pair,contract,bad):
    c,h=pair
    if bad.startswith('vector-'):
        contract['catalog']['extensions'][0][{'vector-version':'extversion','vector-owner':'owner','vector-schema':'schema'}[bad]]='wrong'
    elif bad=='other-extension':contract['catalog']['extensions'].append({'extname':'other'})
    elif bad=='public-owner':contract['catalog']['schemas'][0]['owner']='wrong'
    elif bad=='no-create':contract['business_public_create']=False
    elif bad=='object-acl':contract['catalog']['relations'][0]['acl']='CUSTOM_GRANT_NOT_CAPTURED'
    elif bad=='default-acl':contract['catalog']['default_acls']=[{'unrestorable':'grant'}]
    elif bad=='business-superuser':contract['catalog']['business_role'][0]['rolsuper']=True
    elif bad=='relation-owner':contract['catalog']['relations'][0]['owner']='admin'
    elif bad=='maintenance-write':contract['maintenance_readonly']=False
    elif bad=='maintenance-mismatch':contract['maintenance_catalog_match']=False
    elif bad=='wrong-database':contract['database']='production'
    elif bad=='wrong-role':contract['business_executor']='eaw_test_admin'
    elif bad=='wrong-mode':contract['mode']='SKIP_ALL_EXTENSIONS'
    with pytest.raises(c.Blocked):h.validate_pg_contract(c,contract)


TOC=b'''; real-format fixture, no authority
2; 3079 123 EXTENSION - vector 
300; 0 0 COMMENT - EXTENSION vector 
3; 1259 124 TABLE public fixture enterprise_agent_test
4; 0 124 TABLE DATA public fixture enterprise_agent_test
5; 1259 125 INDEX public fixture_idx enterprise_agent_test
'''


def test_only_exact_vector_entries_removed_all_business_objects_kept(pair):
    c,h=pair
    selected,p=h.business_restore_toc(c,TOC)
    assert p['selected_count']==3 and len(p['preserved_entries'])==2
    assert b'EXTENSION' not in selected and b'TABLE DATA public fixture' in selected
    assert hashlib.sha256(selected).hexdigest()==p['selected_toc_sha256']


@pytest.mark.parametrize('line',[
    b'9; 0 0 EXTENSION - untrusted', b'9; 0 0 SCHEMA - public enterprise_agent_test',
    b'9; 0 0 ACL public fixture enterprise_agent_test',b'9; 0 0 EVENT TRIGGER - arbitrary admin',
    b'9; 0 0 TABLE other fixture enterprise_agent_test',b'9; 0 0 TABLE public fixture admin',
    b'9; 0 0 PUBLICATION - all_tables enterprise_agent_test',b'9; 0 0 FOREIGN DATA WRAPPER - x admin',
    b'3; 0 0 TABLE public duplicate enterprise_agent_test', b'NOT_A_TOC_ENTRY'])
def test_every_other_privilege_schema_or_toc_mismatch_rejected(pair,line):
    c,h=pair
    with pytest.raises(c.Blocked):h.business_restore_toc(c,TOC+line+b'\n')


def test_missing_vector_cannot_be_blindly_skipped(pair):
    c,h=pair
    with pytest.raises(c.Blocked,match='EXACT_VECTOR'):
        h.business_restore_toc(c,b'3; 1259 124 TABLE public fixture enterprise_agent_test\n')


def test_business_pg_env_unchanged_and_no_admin_restore_or_role_elevation(pair,monkeypatch):
    c,h=pair
    monkeypatch.setenv('ENTERPRISE_POC_DATABASE_URL','postgresql://enterprise_agent_test:fixture@127.0.0.1:55432/enterprise_agent_test')
    assert h.pg_env()['PGUSER']=='enterprise_agent_test'
    tree=ast.parse((HERE/'existing_recovery_bound.py').read_bytes())
    f={n.name:ast.unparse(n) for n in tree.body if isinstance(n,ast.FunctionDef)}
    assert '--use-list=' in f['restore_database'] and 'EXPLICIT_PG_RESTORE_MODE_AUTHORITY' in f['restore_database']
    assert 'ACTUAL_RESTORED_PG_OWNER_ACL_EXTENSION_SCHEMA' in f['restore_database']
    assert 'ALTER ROLE' not in (HERE/'existing_recovery_bound.py').read_text()
    assert 'DROP EXTENSION' not in (HERE/'existing_recovery_bound.py').read_text()
    assert 'pg_restore_contract' in f['capture'] and 'business_restore_toc' in f['capture']
    assert f['restore'].index('BUSINESS_AND_REGISTRY_PRESERVED')<f['restore'].index('restore_database(')


def test_maintenance_credential_is_exact_test_file_and_readonly_only():
    raw=(HERE/'runtime_probe.py').read_text()
    fn=next(n for n in ast.parse(raw).body if isinstance(n,ast.FunctionDef) and n.name=='pg_restore_contract')
    text=ast.unparse(fn)
    assert 'shared/credentials/postgresql-admin-password' in text
    assert 'default_transaction_read_only=on' in text and 'REPEATABLE READ READ ONLY' in text
    assert 'EXISTING_TEST_MAINTENANCE_CREDENTIAL_PROTECTION' in text
    assert 'MAINTENANCE_READONLY_CATALOG_MISMATCH' in text
    assert 'ALTER ROLE' not in text and 'CREATE EXTENSION' not in text and 'DROP' not in text


@pytest.mark.parametrize('bad',[None,'mode','symlink'])
def test_gpg_profile_stays_in_private_approved_backup_not_root_home(pair,monkeypatch,tmp_path,bad):
    c,h=pair;h.BACKUP=tmp_path/'private-backup';h.BACKUP.mkdir(mode=0o700)
    monkeypatch.delenv('GNUPGHOME',raising=False)
    real=Path.lstat
    def ls(p):
        s=real(p)
        if p==Path('/tmp'):return types.SimpleNamespace(st_mode=0o40755,st_uid=0)
        return s
    monkeypatch.setattr(Path,'lstat',ls)
    called=[];monkeypatch.setattr(c,'run',lambda argv,**kw:called.append(argv) or b'fixture')
    home=h.BACKUP/'gnupg'
    if bad=='mode':home.mkdir(mode=0o755)
    elif bad=='symlink':home.symlink_to(tmp_path,target_is_directory=True)
    if bad:
        with pytest.raises(c.Blocked):h.crypt(c,b'fixture')
        assert not called
    else:
        assert h.crypt(c,b'fixture')==b'fixture'
        assert '--no-options' in called[0] and called[0][called[0].index('--homedir')+1]==home
        assert home.stat().st_mode&0o777==0o700
