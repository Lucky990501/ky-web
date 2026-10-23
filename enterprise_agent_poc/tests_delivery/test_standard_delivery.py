"""Standard delivery preparation/sequencing; component doubles are NOT Native."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import test_delivery_pipeline as p
from scripts import test_delivery_adoption as a
from test_test_delivery_pipeline import prepared, bundle_git_repo


def standard(prepared):
    repo, out, r = prepared
    proof = {'proof_kind': 'INSTALLED_IDENTITY_OBSERVATION',
             'identity': r['observed_predecessor'], 'evidence_sha256': 'a' * 64}
    return p.delivery_plan(repo, r['candidate']['source'], r['candidate']['tree'],
        out/'candidate.tar.gz', out/'candidate.manifest.json', r['observation'], proof)


def test_installed_observation_does_not_invent_original_or_recovery(prepared):
    r = standard(prepared)
    assert not r['predecessor_original_artifact_available']
    assert not r['activation_ready'] and not r['recovery_ready']
    assert not r['rollback']['primary_recovery_ready']
    assert r['execution_scope']['actions'] == ['prepare', 'activate', 'recover', 'status']


def test_relabel_observation_as_original_rejects(prepared):
    repo, out, r = prepared
    proof = dict(proof_kind='ORIGINAL_RELEASE_ARTIFACT', identity=r['observed_predecessor'], evidence_sha256='a'*64)
    with pytest.raises(p.Block, match='ORIGINAL_PAIR_REQUIRED'):
        p.delivery_plan(repo, r['candidate']['source'], r['candidate']['tree'],
                        out/'candidate.tar.gz', out/'candidate.manifest.json', r['observation'], proof)


def test_source_independent_fixed_steps_and_receipts(prepared):
    r = standard(prepared); events = []; validations = []
    def journal(stage, data):
        events.append(stage)
    def step(stage, request, prior):
        return dict(status='PASS', operation_id=r['operation_id'], request_sha256=p.digest(r))
    result = p.dispatch_delivery(r, step, journal, lambda req: validations.append(req['operation_id']))
    assert events == ['claim'] + [x+':'+s for s in r['steps'] for x in ('intent', 'result')]
    assert len(validations) == 15 and result['production_authorized'] is False


@pytest.mark.parametrize('failed_stage', ['preflight', 'backup', 'install', 'activate', 'health', 'qualify', 'export'])
def test_interruption_never_replays_or_auto_restores(prepared, failed_stage):
    r = standard(prepared); events = []; calls = []
    def step(stage, req, receipts):
        calls.append(stage)
        if stage == failed_stage:
            raise OSError('injected interruption')
        return dict(status='PASS', operation_id=r['operation_id'], request_sha256=p.digest(r))
    with pytest.raises(OSError):
        p.dispatch_delivery(r, step, lambda stage, data: events.append(stage), lambda req: None)
    assert events[-1] == 'intent:' + failed_stage
    assert calls.count(failed_stage) == 1 and 'recover' not in calls


def test_claim_conflict_prevents_all_effects(prepared):
    r = standard(prepared); calls = []
    def conflict(*args):
        raise FileExistsError('same operation already attempted')
    with pytest.raises(FileExistsError):
        p.dispatch_delivery(r, lambda *args: calls.append(args), conflict, lambda req: None)
    assert calls == []


def test_revocation_after_step_prevents_success_receipt(prepared):
    r = standard(prepared); events = []; valid = True
    def validate(req):
        if not valid:
            raise p.Block('REVOKED')
    def step(*args):
        nonlocal valid
        valid = False
        return dict(status='PASS', operation_id=r['operation_id'], request_sha256=p.digest(r))
    with pytest.raises(p.Block, match='REVOKED'):
        p.dispatch_delivery(r, step, lambda stage, data: events.append(stage), validate)
    assert events == ['claim', 'intent:preflight']


@pytest.mark.parametrize('field,value', [('environment', 'production'), ('steps', ['activate']),
    ('calls', {'text': 1, 'image': 0, 'wechat': 0})])
def test_scope_expansion_rejected_before_claim(prepared, field, value):
    r = standard(prepared); r[field] = value; events = []
    with pytest.raises(p.Block):
        p.dispatch_delivery(r, lambda *args: None, lambda *args: events.append(args), lambda req: None)
    assert not events


def test_runtime_graph_is_readonly_and_never_self_adopts(tmp_path):
    root = tmp_path/'runtime'; root.mkdir(); (root/'python').write_bytes(b'not executable')
    before = (root/'python').stat().st_mode
    record = a.runtime_inventory([root])
    assert record['entries'][str(root/'python')]['sha256'] == a.sha(root/'python')
    assert not record['protected_paths_verified'] and not record['dependency_closure_proven']
    assert (root/'python').stat().st_mode == before


@pytest.mark.skipif(sys.platform != 'linux', reason='POSIX real symlink graph')
def test_runtime_link_target_must_be_in_inventory(tmp_path):
    root = tmp_path/'runtime'; root.mkdir(); target = tmp_path/'system-python'; target.write_bytes(b'python')
    (root/'python').symlink_to(target)
    with pytest.raises(ValueError, match='RUNTIME_LINK_OUTSIDE_CLOSURE'):
        a.runtime_inventory([root])
    value = a.runtime_inventory([root, target])
    assert value['entries'][str(root/'python')]['target'] == str(target)
    target.write_bytes(b'drift')
    assert a.digest(a.runtime_inventory([root, target])) != a.digest(value)


def test_dev_entry_rejects_root_or_wrong_parent(monkeypatch, tmp_path):
    import test_delivery_native as n
    from types import SimpleNamespace
    monkeypatch.setattr(n.os, 'geteuid', lambda: 0, raising=False)
    with pytest.raises(RuntimeError, match='DEV_NONROOT_ONLY'):
        n.dev_preflight(SimpleNamespace(root=tmp_path))


def test_dev_receipt_is_never_accepted_as_native():
    with pytest.raises(p.Block, match='DEV_IS_NOT_NATIVE'):
        p.qualification_result({'validation_level': 'DEV_INTEGRATION_PASS', 'test_native': 'NOT_EXECUTED'}, {})


def test_real_append_journal_claim_is_exclusive_and_hash_chained(tmp_path, monkeypatch):
    import uuid
    monkeypatch.setattr(p, 'trusted', lambda path, **kw: Path(path))  # component path, no Root authority
    r = {'operation_id': str(uuid.uuid4()), 'environment': 'test'}
    journal = p.delivery_journal(tmp_path, r)
    journal('claim', {'request_sha256': p.digest(r)})
    journal('intent:backup', {})
    op = tmp_path/r['operation_id']
    first = p.read(op/'claim.json'); second = p.read(op/'intent-backup.json')
    assert second['prior_sha256'] == p.digest(first)
    with pytest.raises(FileExistsError):
        p.delivery_journal(tmp_path, r)('claim', {})
    assert p.read(op/'claim.json') == first


def test_journal_fsync_unknown_preserves_written_evidence(tmp_path, monkeypatch):
    import uuid
    monkeypatch.setattr(p, 'trusted', lambda path, **kw: Path(path))
    r = {'operation_id': str(uuid.uuid4())}
    journal = p.delivery_journal(tmp_path, r)
    monkeypatch.setattr(p.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('injected fsync failure')))
    with pytest.raises(OSError):
        journal('claim', {})
    assert (tmp_path/r['operation_id']/'claim.json').is_file()
    with pytest.raises(FileExistsError):
        p.delivery_journal(tmp_path, r)('claim', {})


@pytest.mark.parametrize('fault', [None, 'source', 'actor', 'expires', 'revoked', 'issuer', 'production', 'runtime', 'tooling'])
def test_reusable_scope_consumer_never_accepts_expired_legacy_or_widened_scope(prepared, fault):
    r = standard(prepared)
    policy = dict(contract='STANDARD_TEST_DELIVERY_POLICY_V1', environment='test',
        hostname=r['observation']['hostname'], operator={'uid': 12345, 'gid': 12345, 'name': 'synthetic-test-operator'},
        tooling={'source': 'd'*40, 'tree': 'e'*40}, runtime_profile_sha256='f'*64, tooling_files_sha256='a'*64,
        actions=['prepare', 'activate', 'recover', 'status'], accepted_issuer_record_sha256='b'*64)
    grant = dict(action='test_delivery', operation_id=r['operation_id'], hostname=policy['hostname'],
        request_sha256=p.digest(r), policy_sha256=p.digest(policy), actor=policy['operator'],
        issuer_record_sha256='b'*64, actions=policy['actions'], production_authority=False,
        tooling=copy.deepcopy(policy['tooling']), runtime_profile_sha256=policy['runtime_profile_sha256'],
        preview_authority=False, not_before=100, expires_at=200, revoked=False, uses=1)
    if fault == 'source': r['candidate']['source'] = 'f'*40
    if fault == 'actor': grant['actor'] = {'uid': 0, 'name': 'root'}
    if fault == 'expires': grant['expires_at'] = 150
    if fault == 'revoked': grant['revoked'] = True
    if fault == 'issuer': grant['issuer_record_sha256'] = 'c'*64
    if fault == 'production': grant['production_authority'] = True
    if fault == 'runtime': grant['runtime_profile_sha256'] = '1'*64
    if fault == 'tooling': grant['tooling']['source'] = '1'*40
    if fault:
        with pytest.raises(p.Block): p.delivery_scope(r, policy, grant, 150)
    else:
        p.delivery_scope(r, policy, grant, 150)
