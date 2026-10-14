"""Independent secure-behavior assertions; failures are findings, not closure.

All processes are ordinary UID and all writes private. Systemd/Root/PG metadata
remain labelled fixture boundaries. No real approval, SSH or service is used.
"""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import shutil
import signal
import subprocess
import sys

import pytest
import lightweight_release_v1 as m
import lightweight_release_linux_v1 as linux
from test_lightweight_release_v1 import Adapter, NOW, evidence, event, plan_grant
from test_lightweight_release_linux_v1 import KernelAdapter


@pytest.mark.parametrize('fault', ['expiry', 'revocation'])
def test_recovery_revalidates_authority_after_preflight_before_actual_restore(tmp_path, monkeypatch, fault):
    plan, grant = plan_grant()
    grant['tooling_root'] = str(tmp_path/'tooling')
    monkeypatch.setattr(linux,'CONTROL',tmp_path/'control')
    # Independent authorization origin / Root metadata are fixture boundaries.
    # LinuxAdapter.authorize and verify_owner execute their actual implementation.
    monkeypatch.setattr(linux,'grant_read',lambda:grant)
    monkeypatch.setattr(linux,'owner_event',lambda p,g:event(p,g))
    (tmp_path/'journal').mkdir()
    class WaitAdapter(linux.LinuxAdapter):
        lock = KernelAdapter.lock
        def now(self):
            return self.clock
        def recovery_preflight(self, plan, snapshot):
            m.require(snapshot['predecessor'] == plan['predecessor'],'exact_recovery_target')
            if fault == 'expiry':
                self.clock = plan['windows']['recover_until']
            else:
                (self.op_root/'RECOVERY_REVOKED').write_bytes(b'isolated revocation marker')
        def activate(self,release):
            self.calls.append('activate:'+release['release_id'])
            self.active=release['release_id']
            (self.op_root/'actual-pointer.txt').write_text(self.active)
        def health(self,release):
            self.calls.append('health:'+release['release_id'])
        def data_preserved(self,plan,snapshot):
            self.calls.append('data_guard')
    a = WaitAdapter(grant,tmp_path)
    a.root,a.calls=tmp_path,[]
    a.clock = plan['windows']['recover_until']-1 if fault == 'expiry' else NOW
    a.active = plan['candidate']['release_id']
    j = m.Journal(tmp_path/'journal')
    j.add('ATTEMPT_STARTED', plan_sha256=m.sha(m.canonical(plan)))
    j.add('SNAPSHOT', snapshot={'predecessor':plan['predecessor'], 'data':'unchanged'})
    j.add('ACTIVATING')
    blocked = False
    try:
        m.Controller(a).recover(plan, grant, j)
    except m.Blocked:
        blocked = True
    # Control: fresh ACTUAL authorization now refuses, yet Controller already
    # mutated the target if it reused the preflight-before-wait result.
    with pytest.raises(m.Blocked):
        a.authorize(plan,grant,recovery=True)
    assert blocked and 'activate:old-v1' not in a.calls, {
        'fault':fault, 'actual_calls':a.calls, 'phase':j.read()[-1]['phase'],
        'authority_invalid_before_restore':True,
    }


def test_sigkill_before_commit_fsync_must_not_report_committed(tmp_path):
    (tmp_path/'journal').mkdir()
    plan, grant = plan_grant()
    (tmp_path/'plan.json').write_bytes(m.canonical(plan))
    (tmp_path/'grant.json').write_bytes(m.canonical(grant))
    code = '''import json,os,signal,sys
from pathlib import Path
import lightweight_release_v1 as m
from test_lightweight_release_linux_v1 import KernelAdapter
root=Path(sys.argv[1]); real=os.fsync
def kill_before_sync(fd):
    path=Path(os.readlink('/proc/self/fd/'+str(fd)))
    if path.is_file() and path.parent == root/'journal':
        if json.loads(path.read_bytes()).get('phase') == 'COMMITTED':
            os.kill(os.getpid(),signal.SIGKILL)
    return real(fd)
os.fsync=kill_before_sync
m.Controller(KernelAdapter(root)).execute(json.loads((root/'plan.json').read_bytes()),
    json.loads((root/'grant.json').read_bytes()),m.Journal(root/'journal'))
'''
    result = subprocess.run([sys.executable, '-B', '-c', code, str(tmp_path)],
                            env=os.environ.copy(), capture_output=True, timeout=15)
    assert result.returncode == -signal.SIGKILL, result.stderr.decode()
    status = m.Controller(Adapter(tmp_path)).status(m.Journal(tmp_path/'journal'))
    assert status['status'] != 'COMMITTED', {
        'writer_killed_before_file_fsync':True,'observed':status,
    }


def test_actual_activation_syncs_each_replaced_directory_before_success(tmp_path, monkeypatch):
    monkeypatch.setattr(linux, 'BASE', tmp_path)
    monkeypatch.setattr(linux, 'PYTHON', tmp_path/'venv/bin/python')
    monkeypatch.setattr(linux, 'trusted', lambda path, **_:path)  # Root metadata only.
    monkeypatch.setattr(linux.LinuxAdapter, 'installed', lambda *_:{})
    monkeypatch.setattr(linux, 'run', lambda *_a, **_kw:b'')  # Never call systemd.
    units = tmp_path/'units'
    units.mkdir()
    for service in linux.SERVICES:
        (units/service).mkdir()
    monkeypatch.setattr(linux, 'dropin_path', lambda service:units/service/'release.conf')
    real_sync, real_replace = os.fsync, os.replace
    calls = []
    def sync(fd):
        calls.append(('fsync', str(Path(os.readlink('/proc/self/fd/'+str(fd))))))
        return real_sync(fd)
    def replace(src,dst):
        result = real_replace(src,dst)
        calls.append(('replace',str(Path(dst).parent)))
        return result
    monkeypatch.setattr(os,'fsync',sync)
    monkeypatch.setattr(os,'replace',replace)
    plan,_ = plan_grant()
    adapter = linux.LinuxAdapter({'tooling_root':str(tmp_path)},tmp_path)
    adapter.target(plan['candidate']).mkdir(parents=True)
    adapter.activate(plan['candidate'])
    assert (tmp_path/'release-current').resolve() == adapter.target(plan['candidate'])
    missed = [path for i,(kind,path) in enumerate(calls) if kind=='replace'
              and ('fsync',path) not in calls[i+1:]]
    assert not missed, {'replaced_directories_without_post_replace_fsync':missed,'trace':calls}


def copy_entry_fixture(tmp_path):
    scripts = Path(linux.__file__).parent
    root = tmp_path/'entry'
    root.mkdir()
    for name in ('lightweight_release_v1.py','lightweight_release_linux_v1.py'):
        shutil.copyfile(scripts/name,root/name)
    return root


def test_rpc_first_import_cannot_execute_unlisted_bytecode(tmp_path):
    root=copy_entry_fixture(tmp_path)
    pinned = root/'lightweight_release_v1.py'
    before=hashlib.sha256(pinned.read_bytes()).hexdigest()
    marker=tmp_path/'UNPINNED_EXECUTED'
    payload=tmp_path/'payload.py'
    payload.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("ordinary-UID canary")\nraise SystemExit(77)\n')
    cache=importlib.util.cache_from_source(str(pinned))
    py_compile.compile(str(payload),cfile=cache,
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,doraise=True)
    result=subprocess.run([sys.executable,'-B',str(root/'lightweight_release_linux_v1.py'),'rpc'],
        input=b'{"action":"status","operation_id":"00000000-0000-0000-0000-000000000001"}',
        capture_output=True,timeout=10)
    assert hashlib.sha256(pinned.read_bytes()).hexdigest()==before
    assert not marker.exists(), {'source_pin_unchanged':True,'entry_exit':result.returncode,
                                 'canary_executed_before_root_and_inventory_gate':marker.exists()}


def test_forced_rpc_default_python_does_not_add_unpinned_cache(tmp_path):
    root=copy_entry_fixture(tmp_path)
    env={k:v for k,v in os.environ.items() if k not in {'PYTHONDONTWRITEBYTECODE','PYTHONPATH','PYTHONHOME'}}
    result=subprocess.run([sys.executable,str(root/'lightweight_release_linux_v1.py'),'rpc'],env=env,
        input=b'{"action":"status","operation_id":"00000000-0000-0000-0000-000000000001"}',
        capture_output=True,timeout=10)
    assert b'real_root_host' in result.stdout  # Ordinary UID, never effective Root.
    extra=list(root.rglob('*.pyc'))
    assert not extra, {'unlisted_cache_created_before_complete_inventory_guard':[str(p) for p in extra]}


@pytest.mark.parametrize('mismatch',[False,True])
def test_protected_backup_restore_proof_must_bind_exact_selected_backup(tmp_path,monkeypatch,mismatch):
    plan,grant=plan_grant()
    report_bytes=evidence(plan)
    old_item=plan['evidence']['production_backup_restore']
    report=json.loads(report_bytes[old_item['sha256']])
    backup_a=b'ISOLATED_SNAPSHOT_A_NOT_PRODUCTION'
    backup_b=b'ISOLATED_DIFFERENT_SNAPSHOT_B_NOT_PROVEN_BY_REPORT_A'
    # Enrich only the producer fixture to the new mandatory object contract.
    # Original 01 source is preserved separately; all assertions below remain.
    bindings={'ciphertext_sha256':m.sha(backup_a),'schema':'015',
        'scope':'ALL_PRODUCTION_DATABASE_TABLES','source_environment_id':plan['host'],
        'restore_environment_id':'independent-test-fixture-only','backup_captured_at':NOW-20,
        'data_preserved':True,'counts_and_hashes_verified':True}
    proof=m.canonical({'purpose':'FULL_DATABASE_RESTORE_VALIDATION','status':'PASS',
        'source':plan['candidate']['source'],'bindings':bindings,'restored_at':NOW-15,'valid_until':NOW+1000})
    proof_sha=m.sha(proof)
    report_bytes[proof_sha]=proof
    report['environment']='REAL_ENCRYPTED_BACKUP_RESTORE'
    report['measurements']={**bindings,'restore_report_sha256':proof_sha,
        'restored_at':NOW-15,'valid_until':NOW+1000}
    encoded=m.canonical(report)
    old_item['sha256']=m.sha(encoded)
    report_bytes[old_item['sha256']]=encoded
    selected=backup_b if mismatch else backup_a
    (tmp_path/'backups').mkdir()
    (tmp_path/'backups/selected.enc').write_bytes(selected)
    plan['backup']={'relative_path':'selected.enc','ciphertext_sha256':m.sha(selected),
        'captured_at':NOW-20,'valid_until':NOW+1000,'restore_report_sha256':proof_sha,
        'restore_environment_id':'independent-test-fixture-only','schema':'015',
        'scope':'ALL_PRODUCTION_DATABASE_TABLES'}
    protected=tmp_path/'shared/release-qualifications/ordinary-v1/reports'
    protected.mkdir(parents=True)
    for item in plan['evidence'].values():
        target=protected/(item['sha256']+'.json')
        target.write_bytes(report_bytes[item['sha256']]); target.chmod(0o400)
    target=protected/(proof_sha+'.json')
    target.write_bytes(proof); target.chmod(0o400)
    monkeypatch.setattr(linux,'BASE',tmp_path)
    monkeypatch.setattr(linux,'trusted',lambda path,**_:path)  # Fake Root ownership only.
    monkeypatch.setattr(linux.LinuxAdapter,'installed',lambda *_:{})
    monkeypatch.setattr(linux.LinuxAdapter,'health',lambda *_:None)
    monkeypatch.setattr(linux.LinuxAdapter,'snapshot',lambda *_a,**_kw:{'ledger':'isolated'})
    monkeypatch.setattr(linux.time,'time',lambda:NOW)
    adapter=linux.LinuxAdapter({'tooling_root':str(tmp_path)},tmp_path)
    adapter.target(plan['predecessor']).mkdir(parents=True)
    (tmp_path/'release-current').symlink_to(adapter.target(plan['predecessor']))
    if mismatch:
        with pytest.raises(m.Blocked):
            m.verify_evidence(plan,report_bytes,NOW)
            linux.qualified_reports(plan,report_bytes)
            adapter.preflight(plan)
    else:
        m.verify_evidence(plan,report_bytes,NOW)
        linux.qualified_reports(plan,report_bytes)
        adapter.preflight(plan)


@pytest.mark.parametrize('path',[
    'enterprise_agent_poc/app/main.py',
    'enterprise_agent_poc/app/agent_execution.py',
    'enterprise_agent_poc/skill_packages/example/SKILL.md',
    'enterprise_agent_poc/migrations/postgres/016_example.sql',
    'enterprise_agent_poc/app/product_store.py',
])
def test_high_risk_nonstatic_paths_still_reject(path):
    with pytest.raises(m.Blocked,match='high_risk_or_unclassified'):
        m.ordinary_diff({path:{'sha256':'a','mode':0o644}},
                        {path:{'sha256':'b','mode':0o644}},[path])
