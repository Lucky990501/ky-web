import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import stage2_failure_snapshot as snap
from scripts.release_test_environment import IsolatedServices


def test_projection_excludes_messages_and_sensitive_fields():
    raw={'error':'test_text_gate_identity secret-key-cookie-input','failure_stage':'runtime',
         'runtime_completed':False,'input_text':'SECRET','final_response':'SECRET',
         'worker_pid':42,'unknown':'SECRET','status':'failed'}
    result=snap.safe_payload(json.dumps(raw))
    assert result['safe_error_category']=='test_text_gate_identity'
    assert result['runtime_completed'] is False and result['worker_pid']==42
    assert 'SECRET' not in json.dumps(result) and 'secret-key' not in json.dumps(result)
    assert snap.safe_payload({'failure_stage':'secret-private-value','error':'arbitrary secret'})['failure_stage'] is None


def test_worker_exception_projection_never_exports_message():
    rows=snap.safe_worker_events('RuntimeError: SECRET-KEY\nSTAGE2_FAILURE type=TestTextGateDenied stage=runtime correlation=1234-abcd\nAuthorization: SECRET-KEY\nSTAGE2_FAILURE type=SecretKey stage=runtime correlation=1234-abcd')
    assert [r['exception_type'] for r in rows]==['RuntimeError','TestTextGateDenied']
    assert 'SECRET' not in json.dumps(rows) and 'SecretKey' not in json.dumps(rows)


@pytest.mark.skipif(os.name!='posix',reason='POSIX ownership and mode contract')
def test_durable_snapshot_is_exclusive_and_secure(tmp_path):
    tmp_path.chmod(0o700)
    receipt=snap.durable_json(tmp_path,'failure.json',{'safe':True})
    q=Path(receipt['path'])
    assert q.stat().st_mode&0o777==0o600 and receipt['sha256']==snap.sha(q.read_bytes())
    with pytest.raises(FileExistsError):snap.durable_json(tmp_path,'failure.json',{'safe':False})
    with pytest.raises(ValueError):snap.durable_json(tmp_path,'../escape.json',{})
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError):snap.durable_json(tmp_path,'other.json',{})


def test_snapshot_failure_does_not_mask_original_failure(monkeypatch,tmp_path):
    def broken(*a,**kw):raise RuntimeError('SECRET')
    monkeypatch.setattr(snap,'capture',broken)
    tmp_path.chmod(0o700)
    receipt=snap.try_capture('absent',tmp_path)
    if os.name=='posix':
        assert receipt and 'SECRET' not in Path(receipt['path']).read_text()
    # The original exception remains the caller's exception, independently of
    # whether durable error receipt creation itself succeeds.
    with pytest.raises(AssertionError,match='ORIGINAL_FAILURE'):
        try:raise AssertionError('ORIGINAL_FAILURE')
        finally:snap.try_capture('absent',tmp_path)


def test_runner_fallback_keeps_pytest_failure(monkeypatch,tmp_path):
    s=IsolatedServices();s.root=tmp_path;s.manifest=tmp_path/'manifest.json'
    calls=[]
    monkeypatch.setattr('scripts.release_test_environment.ISOLATED_PARENT',tmp_path)
    monkeypatch.setattr(s,'start_app',lambda:None)
    monkeypatch.setattr(s,'_run_pytest',lambda args:1)
    monkeypatch.setattr(snap,'try_capture',lambda *a,**kw:calls.append(kw) or None)
    assert s.run_pytest([])==1 and calls[0]['phase']=='pytest_failed'
    assert s.failure_evidence_directory.parent==tmp_path


def test_runner_fallback_keeps_startup_exception(monkeypatch,tmp_path):
    s=IsolatedServices();s.root=tmp_path;s.manifest=tmp_path/'manifest.json'
    monkeypatch.setattr('scripts.release_test_environment.ISOLATED_PARENT',tmp_path)
    def failed():raise RuntimeError('ORIGINAL_STARTUP_FAILURE')
    monkeypatch.setattr(s,'start_app',failed)
    calls=[]
    monkeypatch.setattr(snap,'try_capture',lambda *a,**kw:calls.append(kw) or None)
    with pytest.raises(RuntimeError,match='ORIGINAL_STARTUP_FAILURE'):s.run_pytest([])
    assert calls[0]['phase']=='stage2_startup' and isinstance(calls[0]['exception'],RuntimeError)


@pytest.mark.skipif(os.name!='posix',reason='POSIX evidence sink')
def test_external_sink_is_passed_to_fallback_before_cleanup(monkeypatch,tmp_path):
    sink=tmp_path/'durable';sink.mkdir(mode=0o700)
    s=IsolatedServices();s.root=tmp_path/'disposable';s.manifest=tmp_path/'manifest.json'
    monkeypatch.setenv('STAGE2_FAILURE_EVIDENCE_DIR',str(sink))
    monkeypatch.setattr(s,'start_app',lambda:None)
    monkeypatch.setattr(s,'_run_pytest',lambda args:1)
    calls=[]
    monkeypatch.setattr(snap,'try_capture',lambda *a,**kw:calls.append(a[1]) or None)
    assert s.run_pytest([])==1 and calls==[sink]
