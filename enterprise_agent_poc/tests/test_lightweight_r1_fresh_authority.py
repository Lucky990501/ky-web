"""Bounded local post-Owner-query authorization edges, not real Root acceptance."""
import pytest
import lightweight_release_v1 as m
import lightweight_release_linux_v1 as linux
from test_lightweight_release_v1 import plan_grant, event, NOW


@pytest.mark.parametrize('fault', ['grant_changed', 'grant_revoked', 'recovery_expired', 'machine_expired'])
def test_post_owner_lookup_authority_is_fresh_before_first_write(tmp_path, monkeypatch, fault):
    plan, grant = plan_grant()
    grant['tooling_root'] = str(tmp_path/'tooling')
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
    monkeypatch.setattr(linux.LinuxAdapter, 'installed', lambda *args: {})
    monkeypatch.setattr(linux, 'run', lambda *args, **kwargs: b'')
    monkeypatch.setattr(linux, 'dropin_path', lambda service: units/service/'release.conf')
    fresh = dict(grant)
    reads = []
    def read():
        reads.append(1)
        return fresh
    monkeypatch.setattr(linux, 'grant_read', read)
    adapter = linux.LinuxAdapter(grant, op)
    clock = [NOW]
    adapter.now = lambda: clock[0]
    adapter.target(plan['predecessor']).mkdir(parents=True)
    def lookup(p, g):
        if fault == 'grant_changed':
            fresh['owner_github_id'] += 1
        elif fault == 'grant_revoked':
            fresh['revoked'] = True
        elif fault == 'recovery_expired':
            clock[0] = plan['windows']['recover_until']
        else:
            clock[0] = grant['expires_at']
        return event(p, g)
    monkeypatch.setattr(linux, 'owner_event', lookup)
    with pytest.raises(m.Blocked), adapter.mutation_guard(lambda: adapter.authorize(plan, grant, recovery=True)):
        adapter.activate(plan['predecessor'])
    assert not list(units.rglob('release*'))
    if fault in {'grant_changed', 'grant_revoked'}:
        assert len(reads) == 2  # Protected grant re-read, not the cached object.
