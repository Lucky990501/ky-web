"""PG1-PG12: fail-closed post-staging exact predecessor release gate."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from scripts import release_predecessor_probe as probe
from scripts import release_verify
from scripts import release_binding_transition as transition


SWITCH = Path(__file__).resolve().parents[1] / "deploy/release_switch.sh"


@pytest.fixture
def isolated_gate(tmp_path, monkeypatch):
    base = tmp_path / "release-sandbox"
    predecessor_root = base / "releases/20260925-124d860/enterprise_agent_poc"
    predecessor_root.mkdir(parents=True)
    candidate = base / "releases/20260926-candidate/20260926-candidate.manifest.json"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(json.dumps({
        "skill_package_staging": {"package": {"slug": "wechat-official-account-writing"}},
        "deferred_skill": {"slug": "wechat-official-account-writing"},
    }))
    predecessor = predecessor_root.parent / "20260925-124d860.manifest.json"
    predecessor.write_text(json.dumps({"release_id": "20260925-124d860"}))
    (base / "release-current").symlink_to(predecessor_root)
    python = base / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    snapshot = base / "evidence/state.json"
    snapshot.parent.mkdir()
    snapshot.write_text("{}")
    events = []
    monkeypatch.setattr(probe.shutil, "which", lambda name: "/bin/redis-server")
    monkeypatch.setattr(release_verify, "verify_state", lambda *a, **k: events.append("live-state") or
                        {"campaign_binding": {"event-campaign-plan": "1.0.1"}})
    monkeypatch.setattr(release_verify, "registry_for", lambda *a: object())
    monkeypatch.setattr(transition, "package_only_state", lambda *a, **k: "EXACT")
    monkeypatch.setattr(release_verify, "phase_a_absence", lambda *a: events.append("agent-absent"))
    monkeypatch.setattr(release_verify, "smoke_config", lambda *a: {"tenant_id": "tenant-a"})
    monkeypatch.setattr(probe, "_redis_server", lambda directory: (object(), directory / "redis.sock"))
    monkeypatch.setattr(probe, "_stop", lambda process: None)
    monkeypatch.setattr(probe, "_environment", lambda *a: {})
    monkeypatch.setattr(probe, "_registry_and_agents", lambda *a: events.append("registry-agents"))
    monkeypatch.setattr(probe, "_api", lambda *a: events.append("api"))
    monkeypatch.setattr(probe, "_mcp", lambda *a: events.append("mcp"))
    monkeypatch.setattr(probe, "_worker", lambda *a: events.append("worker"))
    return base, candidate, predecessor, snapshot, events


def test_pg1_pg9_exact_staged_package_and_all_fresh_services_pass(isolated_gate):
    base, candidate, predecessor, snapshot, events = isolated_gate
    result = probe.probe(base, candidate, predecessor, snapshot)
    assert result["status"] == "post_staging_exact_predecessor_passed"
    assert result["package"] == "EXACT" and result["wechat_agent"] == "ABSENT"
    assert events == ["live-state", "agent-absent", "registry-agents", "api", "mcp",
                      "worker", "live-state"]


@pytest.mark.parametrize("component,category", [
    ("_registry_and_agents", "PREDECESSOR_REGISTRY_INIT"),
    ("_api", "PREDECESSOR_API_FRESH_START"),
    ("_mcp", "PREDECESSOR_MCP_FRESH_START"),
    ("_worker", "PREDECESSOR_WORKER_FRESH_START"),
    ("_registry_and_agents", "PREDECESSOR_EXISTING_AGENT_RESOLUTION"),
])
def test_pg2_to_pg6_fresh_component_failure_blocks_before_switch(isolated_gate, monkeypatch,
                                                                     component, category):
    base, candidate, predecessor, snapshot, events = isolated_gate
    def blocked(*args):
        raise probe.ProbeBlocked(category)
    monkeypatch.setattr(probe, component, blocked)
    with pytest.raises(probe.ProbeBlocked, match=category):
        probe.probe(base, candidate, predecessor, snapshot)
    assert (base / "release-current").resolve() == predecessor.parent / "enterprise_agent_poc"
    assert "live-state" in events and "worker" not in events


def test_pg7_unexpected_wechat_agent_visibility_blocks(isolated_gate, monkeypatch):
    base, candidate, predecessor, snapshot, _ = isolated_gate
    def visible(*args):
        raise release_verify.GateFailed("phase_a_agent_not_absent")
    monkeypatch.setattr(release_verify, "phase_a_absence", visible)
    with pytest.raises(release_verify.GateFailed, match="phase_a_agent_not_absent"):
        probe.probe(base, candidate, predecessor, snapshot)


def test_pg8_binding_drift_after_probe_blocks(isolated_gate, monkeypatch):
    base, candidate, predecessor, snapshot, _ = isolated_gate
    states = iter([{"campaign_binding": {"event-campaign-plan": "1.0.1"}},
                   {"campaign_binding": {"event-campaign-plan": "9.9.9"}}])
    monkeypatch.setattr(release_verify, "verify_state", lambda *a, **k: next(states))
    with pytest.raises(probe.ProbeBlocked, match="POST_STAGING_BINDING_DRIFT"):
        probe.probe(base, candidate, predecessor, snapshot)


def test_pg10_pg11_formal_switch_order_and_manual_recovery_boundary():
    source = SWITCH.read_text(encoding="utf-8")
    steps = ["release_migration_transition.py apply", "release_binding_transition.py --candidate-manifest",
             "verify_release post-staging-exact-predecessor predecessor-probe",
             "printf 'service-activation", 'ln -sfn "$release_root" "$current_link"',
             "verify_release technical-smoke smoke", "# RELEASE COMMIT POINT"]
    assert [source.index(step, source.index("trap 'fail_release' ERR")) for step in steps] == sorted(
        source.index(step, source.index("trap 'fail_release' ERR")) for step in steps)
    failure = source[source.index('if ! verify_release post-staging-exact-predecessor predecessor-probe; then'):
                     source.index("printf 'service-activation")]
    assert "PRODUCTION_DEPLOYMENT_MANUAL_RECOVERY_REQUIRED" in failure
    assert "verify_release evidence evidence" in failure
    assert "fail_release" not in failure and 'ln -sfn "$release_root"' not in failure


def test_pg12_legacy_binding_transition_path_remains_conditional():
    source = SWITCH.read_text(encoding="utf-8")
    assert 'if [[ "$declared_package_only" == yes ]]; then' in source
    assert "verify_release post-staging-exact-predecessor predecessor-probe" in source
    assert 'scripts/release_binding_transition.py --candidate-manifest' in source
