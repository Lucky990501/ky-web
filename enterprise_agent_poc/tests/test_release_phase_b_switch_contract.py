"""Source-level guard on the Phase B lock/rollback/commit boundary.

Behavioral PostgreSQL and real Worker tests live in the adjacent suites.
"""
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "deploy/release_switch.sh"


def test_phase_b_transaction_stays_inside_release_lock_and_commit_boundary():
    source = SCRIPT.read_text(encoding="utf-8")
    points = [
        "flock -n -E 75 9",
        'scripts/release_agent_productization.py preflight',
        'backup=$(mktemp -d',
        'scripts/release_agent_productization.py stage',
        '    --candidate-manifest "$candidate_manifest" --release-operation-id',
        'scripts/release_agent_productization.py publish',
        'scripts/release_agent_productization.py enable',
        'verify_release technical-smoke smoke',
        'verify_release final-state state',
        'scripts/release_agent_productization.py commit',
        '# RELEASE COMMIT POINT:',
        'trap - ERR\nrm -rf "$backup"',
    ]
    offsets = [source.index(item) for item in points]
    assert offsets == sorted(offsets)


def test_phase_b_compensation_precedes_code_rollback():
    source = SCRIPT.read_text(encoding="utf-8")
    rollback = source[source.index("rollback() {"):source.index("fail_release() {")]
    assert rollback.index('scripts/release_agent_productization.py abort') < rollback.index(
        'verify_release rollback-guard rollback-guard'
    ) < rollback.index('ln -sfn "$previous" "$current_link"')
    assert "return 1" in rollback[rollback.index('scripts/release_agent_productization.py abort'):]
