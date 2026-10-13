"""Private POSIX fault injection; never real Root or approval qualification."""
import os
import stat

import pytest

from test_v3_trusted_record_bootstrap import (
    artifact, pair, controlled, package, arguments, raw, sealed_policy, tool, binding,
)


def consumer(p):
    return binding._p0_qualification(p['store'].qualifications, p['manifest']['runtime_only_release'],
        tool.EXACT, p['deployment'], p['evidence'], tool.sha(raw(p['evidence'])), 'e' * 64, now=50)


@pytest.mark.parametrize('point', ['intent', 'technical', 'audit', 'revoked', 'complete'])
def test_revoke_interruption_withdraws_before_audit_and_exact_retry_finishes(package, point):
    sealed_policy(package)
    store = package['store']
    args = arguments(package, 'revoke-policy')
    paths = {
        'intent': tool.operation_path(store, 'revoke-policy', 'intent'),
        'technical': store.audit / (args['grant']['audit_id'] + '.technical-decision.json'),
        'audit': store.audit / (args['grant']['audit_id'] + '.json'),
        'revoked': store.audit / (tool.EXACT['release_id'] + '.revoked.json'),
        'complete': tool.operation_path(store, 'revoke-policy', 'complete'),
    }
    store.fail_name = paths[point].name
    with pytest.raises(tool.CommitUnknown) as failure:
        tool.seal(**args)
    assert failure.value.action == 'revoke-policy' and failure.value.phase.startswith('ATTEMPTING:')
    assert not store.path('policy').exists()
    with pytest.raises(FileNotFoundError):
        consumer(package)
    with pytest.raises(tool.Blocked, match='REVOCATION_PENDING'):
        tool.seal(**arguments(package, 'seal-release-records'))
    with pytest.raises(tool.Blocked, match='REVOCATION_PENDING'):
        tool.seal(**arguments(package, 'revoke-policy', audit_id='DIFFERENT_ROOT_DECISION'))
    store.fail_name = None
    tool.seal(**args)
    archive = store.audit / (tool.EXACT['release_id'] + '.withdrawn-policy.json')
    assert archive.read_bytes() == raw(package['policy'])
    assert tool.operation_state(store, 'revoke-policy')['state'] == 'VERIFIED_COMMITTED'
    previous = list(store.writes)
    tool.seal(**args)
    assert store.writes == previous
    assert not store.path('policy').exists()
    with pytest.raises(FileNotFoundError):
        consumer(package)


def test_original_04_incomplete_revocation_no_longer_seals_deployment(package):
    sealed_policy(package)
    args = arguments(package, 'revoke-policy')
    package['store'].fail_name = args['grant']['audit_id'] + '.technical-decision.json'
    with pytest.raises(tool.CommitUnknown):
        tool.seal(**args)
    package['store'].fail_name = None
    with pytest.raises(tool.Blocked, match='REVOCATION_PENDING'):
        tool.seal(**arguments(package, 'seal-release-records'))
    assert not package['store'].path('deployment').exists()
    tool.seal(**args)


@pytest.mark.parametrize('side', ['before_rename', 'after_rename', 'directory_fsync', 'readback'])
def test_withdrawal_failure_retains_exact_recovery_direction(package, monkeypatch, side):
    sealed_policy(package)
    store = package['store']
    args = arguments(package, 'revoke-policy')
    original_rename, original_fsync, original_read = os.rename, os.fsync, store.read
    def fail_rename(source, target):
        if source == store.path('policy'):
            if side == 'before_rename':
                raise OSError('private injected interruption')
            original_rename(source, target)
            raise OSError('private injected interruption')
        return original_rename(source, target)
    def fail_fsync(fd):
        if not store.path('policy').exists() and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError('private injected interruption')
        return original_fsync(fd)
    def fail_read(path):
        if path.name == 'policy.json':
            raise OSError('private injected interruption')
        return original_read(path)
    with monkeypatch.context() as patch:
        if side in {'before_rename', 'after_rename'}: patch.setattr(os, 'rename', fail_rename)
        if side == 'directory_fsync': patch.setattr(os, 'fsync', fail_fsync)
        if side == 'readback': patch.setattr(store, 'read', fail_read)
        with pytest.raises(tool.CommitUnknown): tool.seal(**args)
    assert store.path('policy').exists() is (side == 'before_rename')
    with pytest.raises(tool.Blocked, match='REVOCATION_PENDING'):
        tool.seal(**arguments(package, 'seal-release-records'))
    tool.seal(**args)
    assert not store.path('policy').exists()
    assert tool.operation_state(store, 'revoke-policy')['state'] == 'VERIFIED_COMMITTED'


@pytest.mark.parametrize('tamper', ['policy', 'intent', 'grant'])
def test_revoke_resume_does_not_reinterpret_identity_or_rewrite_evidence(package, tamper):
    sealed_policy(package)
    args = arguments(package, 'revoke-policy')
    store = package['store']
    store.fail_name = args['grant']['audit_id'] + '.technical-decision.json'
    with pytest.raises(tool.CommitUnknown): tool.seal(**args)
    store.fail_name = None
    if tamper == 'policy':
        pending = store.audit / (tool.EXACT['release_id'] + '.revocation-' + args['grant_sha']) / 'policy.json'
        pending.write_bytes(raw(dict(package['policy'], audit_id='UNAUTHORIZED')))
    elif tamper == 'intent':
        path = tool.operation_path(store, 'revoke-policy', 'intent')
        value, _ = store.read(path)
        path.write_bytes(raw(dict(value, input_sha256={})))
    else:
        args = arguments(package, 'revoke-policy', expires_at=99)
    with pytest.raises(tool.Blocked): tool.seal(**args)
    assert not store.path('policy').exists()


def test_error_after_successful_protected_write_is_never_reported_zero_writes(package, monkeypatch):
    store = package['store']
    original = store.create
    def interrupted(path, data):
        original(path, data)
        if path == store.path('policy'):
            raise OSError('post-write failure')
    monkeypatch.setattr(store, 'create', interrupted)
    with pytest.raises(tool.CommitUnknown) as error: tool.seal(**arguments(package))
    assert error.value.phase == 'ATTEMPTING:' + store.path('policy').name
    assert store.path('policy').read_bytes() == raw(package['policy'])
    assert tool.operation_state(store, 'seal-policy')['state'] == 'PENDING_OR_UNKNOWN'
    with pytest.raises(tool.Blocked): tool.seal(**arguments(package, 'seal-release-records'))


def test_real_cli_mutation_is_blocked_until_frozen_consumer_contract_is_resolved(monkeypatch, capsys):
    # No Root action: replace Store constructor only to reach the explicit
    # candidate-wide seal prohibition, before any filesystem/TTY work.
    monkeypatch.setattr(tool, 'Store', lambda: object())
    assert tool.main(['seal-policy', '--git-repository', '/unread', '--archive', '/unread',
                      '--manifest', '/unread', '--seal']) == 2
    assert 'FROZEN_V3_CONSUMER_DOES_NOT_OBSERVE_COMMIT_UNCERTAINTY' in capsys.readouterr().out


@pytest.mark.parametrize('fault', ['fsync', 'readback'])
def test_completion_visibility_requires_independent_durable_readback(package, monkeypatch, fault):
    store = package['store']
    completion = tool.operation_path(store, 'seal-policy', 'complete')
    original_fsync, original_read = os.fsync, store.read
    def fail_fsync(fd):
        if completion.exists() and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError('completion directory durability unknown')
        return original_fsync(fd)
    def fail_read(path):
        if path == completion: raise OSError('completion readback unknown')
        return original_read(path)
    with monkeypatch.context() as patch:
        if fault == 'fsync': patch.setattr(os, 'fsync', fail_fsync)
        else: patch.setattr(store, 'read', fail_read)
        with pytest.raises(tool.CommitUnknown): tool.seal(**arguments(package))
        assert completion.exists()  # Existence is NOT a successful commit claim.
        with pytest.raises(tool.CommitUnknown, match='PROTECTED_COMMIT_UNKNOWN'):
            tool.operation_state(store, 'seal-policy')
        previous = list(store.writes)
        with pytest.raises(tool.CommitUnknown): tool.seal(**arguments(package, 'seal-release-records'))
        assert store.writes == previous
    # Only an independent successful protected read/fsync/read can resolve this
    # producer state. The separate frozen-consumer blocker is still unchanged.
    seen = []
    def record_fsync(fd):
        seen.append(os.fstat(fd).st_mode)
        return original_fsync(fd)
    monkeypatch.setattr(os, 'fsync', record_fsync)
    assert tool.operation_state(store, 'seal-policy')['state'] == 'VERIFIED_COMMITTED'
    assert len(seen) == 4
    assert sum(stat.S_ISDIR(mode) for mode in seen) == 2
