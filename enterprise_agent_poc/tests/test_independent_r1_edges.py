"""R1 real mutation path, authority can change during the existing slow GET.

Root/Owner identity and systemd are labelled doubles. Private file replacement,
revocation files, actual LinuxAdapter.authorize/before_mutation/activate execute.
No actual Root, SSH, shared Test or Production authority is used.
"""
from pathlib import Path
import pytest
import lightweight_release_v1 as m
import lightweight_release_linux_v1 as linux
from test_lightweight_release_v1 import plan_grant, event, NOW


@pytest.mark.parametrize('fault', ['operation_revoked', 'machine_revoked'])
def test_revocation_during_owner_lookup_must_block_actual_restore_replace(tmp_path, monkeypatch, fault):
    p, g = plan_grant()
    g['tooling_root'] = str(tmp_path/'tooling')
    control = tmp_path/'control'
    control.mkdir()
    op = tmp_path/'operation'
    op.mkdir()
    units = tmp_path/'units'
    units.mkdir()
    for service in linux.SERVICES:
        (units/service).mkdir()
    monkeypatch.setattr(linux, 'BASE', tmp_path)
    monkeypatch.setattr(linux, 'CONTROL', control)
    monkeypatch.setattr(linux, 'trusted', lambda path, **kwargs: path)
    monkeypatch.setattr(linux, 'grant_read', lambda: g)
    monkeypatch.setattr(linux.LinuxAdapter, 'installed', lambda *args: {})
    monkeypatch.setattr(linux, 'run', lambda *args, **kwargs: b'')
    monkeypatch.setattr(linux, 'dropin_path', lambda service: units/service/'release.conf')
    calls = []
    def lookup(plan, grant):
        calls.append('owner_GET')
        # First callback prepares a temporary drop-in. Second callback precedes
        # its actual replace: revoke AFTER local checks, BEFORE GET returns.
        if len(calls) == 2:
            target = op/'RECOVERY_REVOKED' if fault == 'operation_revoked' else control/'REVOKED'
            target.write_bytes(b'independent revocation during slow lookup')
        return event(plan, grant)
    monkeypatch.setattr(linux, 'owner_event', lookup)
    a = linux.LinuxAdapter(g, op)
    a.now = lambda: NOW
    a.target(p['predecessor']).mkdir(parents=True)
    with pytest.raises(m.Blocked), a.mutation_guard(lambda: a.authorize(p, g, recovery=True)):
        a.activate(p['predecessor'])
    # Event provenance is mocked, but revocation storage and actual protected
    # mutation implementation are real and identical to the successor source.
    assert len(calls) == 2
    assert not list(units.rglob('release.conf')), {
        'fault': fault,
        'actual_replacements_after_revocation': [str(path.relative_to(tmp_path)) for path in units.rglob('release.conf')],
    }
