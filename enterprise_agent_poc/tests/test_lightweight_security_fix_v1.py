"""Additional R1-R4 edges; ordinary-user fixtures, not deployment qualification."""
import importlib
import importlib.util
import json
from pathlib import Path
import py_compile
import sys
import types

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import lightweight_release_v1 as m
import lightweight_release_linux_v1 as linux
import lightweight_release_verified_entry_v1 as entry
import v3_bootstrap_verified_entry as primitive
from test_lightweight_release_v1 import execution, plan_grant, evidence, NOW


def test_expired_authority_at_success_is_not_restored(tmp_path):
    a,c,j,p,g=execution(tmp_path,"health")
    original=a.health
    def health(release):
        original(release)
        if release['release_id']=='old-v1': a.clock=p['windows']['recover_until']
    a.health=health
    with pytest.raises(m.Blocked): c.execute(p,g,j)
    assert c.status(j)['status']=='RECOVERY_BLOCKED'
    assert not any(r['phase']=='RESTORED' for r in j.read())


def test_failed_directory_sync_is_unknown_not_committed(tmp_path,monkeypatch):
    j=m.Journal(tmp_path)
    j.add('COMMIT_PREPARED')
    monkeypatch.setattr(m,'sync_directory',lambda path: (_ for _ in ()).throw(OSError('fixture fsync failure')))
    with pytest.raises(OSError): j.add('COMMITTED')
    assert m.Controller(None).status(j)['status']=='UNKNOWN_INCOMPLETE_QUERY_ONLY'
    assert list(tmp_path.glob('.pending-*'))  # retained, not deleted/replayed


def test_captured_source_loader_never_reads_canary_bytecode(tmp_path,monkeypatch):
    root=tmp_path/'tooling'
    source=root/'enterprise_agent_poc/scripts/lightweight_release_v1.py'
    source.parent.mkdir(parents=True)
    raw=b'VALUE = 42\n'
    source.write_bytes(raw)
    malicious=tmp_path/'malicious.py'
    marker=tmp_path/'CANARY'
    malicious.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\nraise SystemExit(77)\n')
    py_compile.compile(str(malicious),cfile=importlib.util.cache_from_source(str(source)),
                       invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,doraise=True)
    monkeypatch.setattr(entry,'ROOT',root)
    finder=entry.source_closure({'enterprise_agent_poc/scripts/lightweight_release_v1.py':raw},
                               {'runtime_roots':[],'runtime_files':{}},primitive,())
    prior=list(sys.meta_path)
    # Different module alias avoids touching the test runner's loaded core.
    sys.modules.pop('scripts.lightweight_release_v1',None)
    old_scripts=sys.modules.pop('scripts',None)
    try:
        sys.meta_path[:]=[finder]
        module=importlib.import_module('scripts.lightweight_release_v1')
        assert module.VALUE==42 and not marker.exists()
    finally:
        sys.meta_path[:]=prior
        sys.modules.pop('scripts.lightweight_release_v1',None)
        sys.modules.pop('scripts',None)
        if old_scripts is not None: sys.modules['scripts']=old_scripts


def test_restore_guard_runs_before_each_actual_replace(tmp_path,monkeypatch):
    monkeypatch.setattr(linux,'BASE',tmp_path)
    monkeypatch.setattr(linux,'trusted',lambda path,**_:path)
    monkeypatch.setattr(linux.LinuxAdapter,'installed',lambda *_:{})
    monkeypatch.setattr(linux,'run',lambda *_a,**_k:b'')
    units=tmp_path/'units'; units.mkdir()
    for service in linux.SERVICES: (units/service).mkdir()
    monkeypatch.setattr(linux,'dropin_path',lambda service:units/service/'release.conf')
    p,_=plan_grant(); a=linux.LinuxAdapter({'tooling_root':str(tmp_path)},tmp_path)
    a.target(p['predecessor']).mkdir(parents=True)
    checks=[]
    def check():
        checks.append(1)
        if len(checks)==3: raise m.Blocked('fixture revoked after first mutation')
    with pytest.raises(m.Blocked),a.mutation_guard(check): a.activate(p['predecessor'])
    assert len(checks)==3 and not (tmp_path/'release-current').exists()
    assert len(list(units.rglob('release.conf')))==1


@pytest.mark.parametrize('field,value',[
    ('schema','014'),('scope','SUBSET'),('source_environment_id','other-host'),
    ('restore_environment_id','other-restore'),('backup_captured_at',NOW+1),
    ('data_preserved',False),('counts_and_hashes_verified',False)])
def test_restore_proof_scope_mismatch_rejected(field,value):
    p,_=plan_grant(); reports=evidence(p)
    old=p['backup']['restore_report_sha256']
    proof=json.loads(reports[old]); proof['bindings'][field]=value
    raw=m.canonical(proof); digest=m.sha(raw)
    p['backup']['restore_report_sha256']=digest; reports[digest]=raw
    item=p['evidence']['production_backup_restore']
    aggregate=json.loads(reports[item['sha256']]); aggregate['measurements']['restore_report_sha256']=digest
    raw=m.canonical(aggregate); item['sha256']=m.sha(raw); reports[item['sha256']]=raw
    with pytest.raises(m.Blocked): m.verify_evidence(p,reports,NOW)
