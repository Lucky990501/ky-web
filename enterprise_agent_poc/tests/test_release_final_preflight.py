"""PF1-PF6: real entry, POSIX lock/symlink, immutable bytes and Registry.

Only host process/network and PostgreSQL-schema boundaries use the switch
harness doubles. The final-preflight consumer and identity checks run unchanged.
"""
import fcntl
import json
from pathlib import Path

import pytest

from tests.test_release_switch import SCRIPT, switch_harness, run_switch
from tests.test_release_commit_boundary import declared, state, FROM, TO, lock_released


def assert_no_mutation(h, before):
    assert h['snapshot']() == before
    assert (h['base'] / 'release-current').resolve() == h['old']
    events = h['events'].read_text() if h['events'].exists() else ''
    assert '"--apply"' not in events
    assert 'systemctl:' not in events
    assert not h['systemd'].exists()
    assert not list(h['base'].glob('.release-switch.*'))
    lock_released(h)


def test_pf1_lock_before_final_preflight(declared):
    h = declared
    before = h['snapshot']()
    result = run_switch(h)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr.index('+ flock -n -E 75 9') < result.stderr.index('scripts/release_verify.py preflight')
    # The harness also attempts a second real flock in the verifier subprocess;
    # successful preflight requires it to see the script's lock as unavailable.
    records = [json.loads(line) for line in result.stdout.splitlines()]
    report = next(r for r in records if r.get('status') == 'final_preflight_passed')
    assert report['credentials'] == 'READY' and report['pending'] == 0
    assert report['migration'] == 'NONE' and report['exact_predecessor'] == 'old'
    assert {'slug': 'event-campaign-plan', 'version': '1.0.1'} in report['registry_exact_reusable']
    assert state(h) == (FROM, FROM)
    assert_no_mutation(h, before)


def test_pf2_final_preflight_before_snapshot_trap_and_all_mutations(declared):
    h = declared
    result = run_switch(h, preflight=False)
    assert result.returncode == 0, result.stdout + result.stderr
    trace = result.stderr
    positions = [trace.index(token) for token in (
        'scripts/release_verify.py preflight', '++ mktemp -d',
        '+ verify_release capture capture', "+ trap fail_release ERR",
        'scripts/release_binding_transition.py --candidate-manifest',
        '+ mkdir -p', '+ ln -sfn', '+ systemctl daemon-reload', '+ systemctl restart',
        '+ verify_release technical-smoke smoke', '+ verify_release final-state state',
        '+ trap - ERR')]
    assert positions == sorted(positions)
    events = h['events'].read_text()
    assert events.index('FINAL_PREFLIGHT_PASS') < events.index('"--apply"') < events.index('systemctl:')
    assert state(h) == (TO, TO)
    lock_released(h)


@pytest.mark.parametrize('fault', ['predecessor', 'preflight_cwd', 'registry', 'manifest', 'credentials', 'pending'])
def test_pf3_preflight_failure_zero_mutation(declared, fault):
    h = declared
    if fault == 'predecessor':
        (h['base'] / 'release-current').unlink()
        wrong = h['base'] / 'releases/wrong/enterprise_agent_poc'
        wrong.mkdir(parents=True)
        (wrong.parent / 'wrong.manifest.json').write_bytes((h['old'].parent / 'old.manifest.json').read_bytes())
        (h['base'] / 'release-current').symlink_to(wrong)
    elif fault == 'registry':
        with h['registry']._store.connection() as conn:
            conn.execute("UPDATE skill_versions SET checksum=? WHERE skill_id=(SELECT id FROM skills WHERE slug=?) AND version=?",
                         ('0' * 64, 'event-campaign-plan', '1.0.1'))
    elif fault == 'manifest':
        with h['registry']._store.connection() as conn:
            conn.execute("UPDATE agent_templates SET skill_manifest=? WHERE id=?", ('{}', 'campaign-agent'))
    before = h['snapshot']()
    link_before = (h['base'] / 'release-current').resolve()
    result = run_switch(h, preflight=False, fault=fault)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'PRODUCTION_DEPLOYMENT_PREFLIGHT_BLOCKED' in result.stderr
    assert 'FINAL_PREFLIGHT_PASS' not in h['events'].read_text()
    assert (h['base'] / 'release-current').resolve() == link_before
    # All writes in the corrupt fixture are setup, not performed by the entry.
    h['old'] = link_before
    assert_no_mutation(h, before)


@pytest.mark.parametrize('field', ['SOURCE_COMMIT', 'ARCHIVE_SHA256', 'RAW_MANIFEST_SHA256', 'CANONICAL_MANIFEST_SHA256'])
@pytest.mark.parametrize('bad', ['', '0'])
def test_approved_identity_pins_required_and_exact(declared, field, bad):
    h = declared
    h['env']['RELEASE_EXPECTED_' + field] = bad * (40 if field == 'SOURCE_COMMIT' else 64)
    before = h['snapshot']()
    result = run_switch(h, preflight=False)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'PRODUCTION_DEPLOYMENT_PREFLIGHT_BLOCKED' in result.stderr
    assert_no_mutation(h, before)


def test_installed_bytes_drift_blocks_before_mutation(declared):
    h = declared
    (h['candidate'] / 'pyproject.toml').write_text('# drift\n')
    before = h['snapshot']()
    result = run_switch(h, preflight=False)
    assert result.returncode == 2
    assert_no_mutation(h, before)


def test_missing_candidate_package_is_only_classified_stageable(declared):
    h = declared
    with h['registry']._store.connection() as conn:
        row = conn.execute("SELECT v.id FROM skill_versions v JOIN skills s ON s.id=v.skill_id WHERE s.slug=? AND v.version=?",
                           ('event-campaign-plan', '1.0.0')).fetchone()
        conn.execute('DELETE FROM skill_packages WHERE skill_version_id=?', (row['id'],))
        conn.execute('DELETE FROM skill_versions WHERE id=?', (row['id'],))
    before = h['snapshot']()
    result = run_switch(h)
    assert result.returncode == 0, result.stdout + result.stderr
    report = next(json.loads(line) for line in result.stdout.splitlines() if 'final_preflight_passed' in line)
    assert report['registry_stageable'] == [{'slug': 'event-campaign-plan', 'version': '1.0.0'}]
    assert_no_mutation(h, before)


@pytest.mark.parametrize('fault', ['schema', 'contract', 'schema_pending'])
def test_final_preflight_rejects_schema_and_contract_drift(declared, fault):
    h = declared
    before = h['snapshot']()
    result = run_switch(h, preflight=False, fault=fault)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'PRODUCTION_DEPLOYMENT_PREFLIGHT_BLOCKED' in result.stderr
    assert_no_mutation(h, before)


def test_pf4_no_outer_lock_handoff(declared):
    source = SCRIPT.read_text()
    for forbidden in ('LOCK_ALREADY_HELD', 'SKIP_LOCK', 'NO_FLOCK', 'INHERITED_LOCK_FD'):
        assert forbidden not in source
    assert source.count('flock -n -E 75 9') == 1
    result = run_switch(declared, preflight=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'final_preflight_passed' in result.stdout and 'PRODUCTION_DEPLOYMENT_PASS' in result.stdout


def test_pf5_lock_conflict_prevents_preflight_and_mutation(declared):
    h = declared
    before = h['snapshot']()
    with Path(h['env']['RELEASE_LOCK_FILE']).open('a+') as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run_switch(h, preflight=False)
    assert result.returncode == 75 and 'RELEASE_SWITCH_LOCKED' in result.stderr
    assert not h['events'].exists()
    assert_no_mutation(h, before)


@pytest.mark.parametrize('fault', ['', 'preflight_cwd'])
def test_pf6_legacy_same_in_lock_preflight(switch_harness, fault):
    h = switch_harness
    before = h['snapshot']()
    result = run_switch(h, preflight=False, fault=fault)
    assert result.returncode == (2 if fault else 0), result.stdout + result.stderr
    assert result.stderr.index('+ flock -n -E 75 9') < result.stderr.index('scripts/release_verify.py preflight')
    if fault:
        assert_no_mutation(h, before)
    else:
        events = h['events'].read_text()
        assert events.index('FINAL_PREFLIGHT_PASS') < events.index('"--apply"') < events.index('systemctl:')
        assert h['snapshot']() == before  # Legacy keeps Registry/bindings intact.
        lock_released(h)
