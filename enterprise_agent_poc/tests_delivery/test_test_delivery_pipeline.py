"""Tool contract tests only; never label these native PG/Worker evidence."""
import copy
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tarfile
import time
import uuid

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import test_delivery_pipeline as p


def commit(repo):
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'fixture'], check=True)
    return subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()


@pytest.fixture
def bundle_git_repo(tmp_path):
    repo=tmp_path/'git-fixture';repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    for key,value in [('user.name','Fixture'),('user.email','fixture@example.invalid'),('core.autocrlf','false')]:
        subprocess.run(['git','-C',str(repo),'config',key,value],check=True)
    project=repo/'enterprise_agent_poc'
    shutil.copytree(Path(p.PROJECT)/'skill_packages',project/'skill_packages')
    static=project/'app/static';static.mkdir(parents=True)
    (project/'app/main.py').write_text('# fixture\n')
    (static/'workbench.js').write_bytes(b'console.log("fixture");\n')
    (static/'workbench.css').write_bytes(b'body {display:block;}\n')
    (static/'index.html').write_bytes(b'<link href="/static/workbench.css?v=__CSS_SHA256_V1_">'
                                   b'<script src="/static/workbench.js?v=__JS_SHA256_V1__"></script>')
    commit(repo)
    return repo


@pytest.fixture
def prepared(bundle_git_repo, tmp_path):
    repo = bundle_git_repo
    m = repo / 'enterprise_agent_poc/migrations/postgres'
    m.mkdir(parents=True); (m / '001.sql').write_text('select 1;')
    old = commit(repo)
    (repo / 'enterprise_agent_poc/app/main.py').write_text('# new immutable candidate\n')
    new = commit(repo); tree = p.identity(repo, new)['tree']
    out = tmp_path / 'artifact'
    p.package(repo, new, tree, out)
    obs = {'environment': 'test', 'hostname': 'fixture-test',
           'primary_source': old, 'primary_tree': p.identity(repo, old)['tree'],
           'primary_path': '/opt/test/releases/previous', 'observed_at': 1}
    request = p.plan(repo, new, tree, out/'candidate.tar.gz', out/'candidate.manifest.json', obs)
    return repo, out, request


def test_real_builder_plan_preserves_actual_predecessor_and_git_parent(prepared):
    repo, out, r = prepared
    assert r['rollback']['migration_files_unchanged']
    assert not r['rollback']['primary_recovery_ready']
    assert r['candidate']['source'] != r['observed_predecessor']['source']
    assert r['candidate']['git_parents'] == [r['observed_predecessor']['source']]
    assert r['artifact']['archive_sha256'] == p.sha(out/'candidate.tar.gz')
    assert r['calls'] == {'text': 0, 'image': 0, 'wechat': 0}


def test_next_source_and_non_git_parent_use_identical_tool(prepared, tmp_path):
    repo, out, r = prepared
    first_tool_sha = p.sha(Path(p.__file__))
    (repo/'enterprise_agent_poc/app/main.py').write_text('# next source\n')
    new = commit(repo); tree = p.identity(repo, new)['tree']; out2 = tmp_path/'second'
    p.package(repo, new, tree, out2)
    nxt = p.plan(repo, new, tree, out2/'candidate.tar.gz', out2/'candidate.manifest.json', r['observation'])
    assert nxt['candidate']['git_parents'] == [r['candidate']['source']]
    assert nxt['observed_predecessor'] == r['observed_predecessor']
    assert p.sha(Path(p.__file__)) == first_tool_sha
    assert nxt['operation_id'] != r['operation_id']


def test_changed_migration_does_not_promise_rollback(prepared, tmp_path):
    repo, out, r = prepared
    (repo/'enterprise_agent_poc/migrations/postgres/002.sql').write_text('select 2;')
    new=commit(repo); tree=p.identity(repo,new)['tree']; dst=tmp_path/'schema'
    p.package(repo,new,tree,dst)
    value=p.plan(repo,new,tree,dst/'candidate.tar.gz',dst/'candidate.manifest.json',r['observation'])
    assert not value['rollback']['migration_files_unchanged']
    assert not value['rollback']['primary_recovery_ready']


def test_archive_tamper_rejects_before_extraction(prepared):
    repo,out,r=prepared
    with (out/'candidate.tar.gz').open('ab') as f:f.write(b'tamper')
    with pytest.raises(p.Block, match='ARTIFACT_IDENTITY'):
        p.artifact(repo,r['candidate']['source'],out/'candidate.tar.gz',out/'candidate.manifest.json')


def test_wrong_tree_rejects_without_output(prepared, tmp_path):
    repo,out,r=prepared
    with pytest.raises(p.Block, match='TREE_MISMATCH'):
        p.package(repo,r['candidate']['source'],'0'*40,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_no_overwrite_and_duplicate_json(tmp_path):
    target=tmp_path/'receipt.json';p.exclusive(target,{'ok':1})
    with pytest.raises(FileExistsError):p.exclusive(target,{'ok':2})
    target.write_text('{"a":1,"a":2}')
    with pytest.raises(p.Block,match='DUPLICATE'):p.read(target)


def test_builder_process_eol_pinned_without_changing_git_config(bundle_git_repo,tmp_path):
    repo=bundle_git_repo
    (repo/'.gitattributes').write_bytes(b'*.example text=auto\n')
    directory=repo/'enterprise_agent_poc/deploy';directory.mkdir()
    (directory/'fixture.example').write_bytes(b'first\nsecond\n')
    source=commit(repo)
    subprocess.run(['git','-C',str(repo),'config','core.eol','crlf'],check=True)
    target=tmp_path/'eol';p.package(repo,source,p.identity(repo,source)['tree'],target)
    with tarfile.open(target/'candidate.tar.gz') as tf:
        assert tf.extractfile('enterprise_agent_poc/deploy/fixture.example').read()==b'first\nsecond\n'
    assert subprocess.check_output(['git','-C',str(repo),'config','--get','core.eol']).strip()==b'crlf'


@pytest.fixture
def scope():
    r={'contract':p.VERSION,'environment':'test','action':'isolated_qualify',
       'production_authority':False,'primary_switch_authority':False,
       'calls':{'text':0,'image':0,'wechat':0},'operation_id':str(uuid.uuid4()),
       'observation':{'hostname':'test-host'},'checks':list(p.CHECKS),
       'limits':{'aggregate_bytes':p.LIMIT,'scratch_bytes':p.SCRATCH,'input_bytes':p.INPUT_LIMIT,
                 'receipt_bytes':p.RECEIPT_LIMIT,'host_reserve_bytes':p.HOST_RESERVE,'min_free_fraction':.2}}
    policy={'environment':'test','hostname':'test-host'}
    g={'action':'isolated_qualify','request_sha256':p.digest(r),'policy_sha256':p.digest(policy),
       'operation_id':r['operation_id'],'not_before':100,'expires_at':3700,
       'revoked':False,'uses':1,'issuer_record':'fixture-not-a-real-approval'}
    return r,policy,g


def test_valid_offline_scope_contract(scope):p.authority(*scope,101)


@pytest.mark.parametrize('field,value',[
    ('request_sha256','0'*64),('policy_sha256','0'*64),('operation_id',str(uuid.uuid4())),
    ('action','activate-primary'),('not_before',102),('expires_at',101),
    ('expires_at',100000),('revoked',True),('uses',2),('issuer_record','')])
def test_grant_exactness(scope,field,value):
    r,policy,g=scope;g[field]=value
    with pytest.raises(p.Block):p.authority(r,policy,g,101)


@pytest.mark.parametrize('field,value',[
    ('environment','production'),('production_authority',True),('primary_switch_authority',True),
    ('calls',{'text':1,'image':0,'wechat':0}),('checks',['pg']),('limits',{})])
def test_scope_does_not_expand(scope,field,value):
    r,policy,g=scope;r[field]=value;g['request_sha256']=p.digest(r)
    with pytest.raises(p.Block):p.authority(r,policy,g,101)


def test_space_includes_cap_and_host_reserve():
    total=40*1024**3;threshold=total*.2+p.LIMIT+p.HOST_RESERVE
    with pytest.raises(p.Block):p.storage_gate(threshold,total)
    p.storage_gate(threshold+4096,total)


def test_sandbox_default_denial_and_no_root_candidate(scope):
    r,_,_=scope
    policy={'runtime_root':'/trusted/runtime','tooling_root':'/trusted/tools',
        'python':'/trusted/runtime/bin/python','qualification_driver':'/trusted/tools/driver.py',
        'operator_uid':1000,'operator_gid':1000,'slice':'enterprise-agent-test.slice',
        'driver_protocol':'QUALIFICATION_DRIVER_PROTOCOL_V1'}
    cmd=p.sandbox_command(r,policy,Path('/operations/one'),Path('/inputs/archive'),Path('/inputs/manifest'),Path('/inputs/prior'),Path('/inputs/prior-manifest'))
    assert '--uid=1000' in cmd and 'PrivateNetwork=yes' in cmd and 'MemorySwapMax=0' in cmd
    assert 'ProtectSystem=strict' in cmd and 'ReadWritePaths=/private/tmp' in cmd
    assert all('restart' not in x for x in cmd)
    assert f'size={p.SCRATCH}' in next(x for x in cmd if x.startswith('TemporaryFileSystem='))
    policy['slice']='system.slice'
    with pytest.raises(p.Block):p.sandbox_command(r,policy,Path('/op'),Path('/a'),Path('/m'),Path('/pa'),Path('/pm'))


@pytest.mark.parametrize('field,value', [('backend','sqlite'),('provider_calls',1),('primary_changes',1),
    ('checks',{'pg':'PASS'}),('min_root_free_fraction',.19),('disk_peak_bytes',p.LIMIT+1),('source','0'*40)])
def test_native_result_cannot_be_mock_or_incomplete(scope,field,value):
    r,_,_=scope;r['candidate']={'source':'a'*40,'tree':'b'*40}
    result={'operation_id':r['operation_id'],'source':'a'*40,'tree':'b'*40,
        'backend':'postgresql-redis-worker-mcp','provider_calls':0,'production_changes':0,
        'preview_changes':0,'primary_changes':0,'checks':{x:'PASS' for x in p.CHECKS},
        'disk_peak_bytes':0,'min_root_free_fraction':.3}
    result[field]=value
    with pytest.raises(p.Block):p.qualification_result(result,r)


def test_execute_local_without_adoption_has_no_write(tmp_path):
    with pytest.raises(p.Block,match='ADOPTED_ROOT_ENTRY_REQUIRED'):
        p.execute(tmp_path/'r',tmp_path/'p',tmp_path/'g',tmp_path/'a',tmp_path/'m')
    assert not list(tmp_path.iterdir())
