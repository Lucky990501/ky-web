"""Real Bash, real POSIX lock/symlink and Registry; host/network boundaries only."""
import fcntl
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from scripts import release_binding_transition as transition
from tests.test_release_switch import switch_harness, run_switch, SCRIPT

FROM = {"campaign-planning": "1.2.0", "event-copywriting": "1.0.0"}
TO = {"event-campaign-plan": "1.0.1", "event-copywriting": "1.0.0"}


@pytest.fixture
def declared(switch_harness):
    h = switch_harness
    registry = h["registry"]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("campaign-planning/SKILL.md", "# isolated FROM fixture\n")
    item = registry.import_archive("campaign-planning", "1.2.0", "fixture", "", stream.getvalue(), "fixture")
    registry.publish(item["id"], "fixture")
    # Controlled fixture setup only, not a production recovery mechanism.
    with registry._store.connection() as conn:
        conn.execute("DELETE FROM agent_skill_bindings WHERE agent_id=?", ("campaign-agent",))
        conn.execute("UPDATE agent_templates SET skill_manifest=? WHERE id=?", ("{}", "campaign-agent"))
    for slug, version in FROM.items():
        registry.bind_agent("campaign-agent", slug, version, "fixture", allow_new_binding=True)
    predecessor = h["old"].parent / "old.manifest.json"
    candidate = h["candidate"].parent / "candidate.manifest.json"
    manifest = json.loads(candidate.read_text())
    packages = transition.candidate_packages(h["candidate"] / "skill_packages")
    manifest["binding_transition"] = {
        "schema_version": 1,
        "exact_predecessor": {**{k: json.loads(predecessor.read_text())[k] for k in ("release_id", "source_commit", "archive_sha256")}, "manifest_sha256": hashlib.sha256(predecessor.read_bytes()).hexdigest()},
        "transitions": [{"agent_id": "campaign-agent", "from_bindings": FROM, "to_bindings": TO,
                         "required_skill_identities": [{"slug": s, "version": v,
                             "artifact_sha256": packages[s, v]["artifact_sha256"],
                             "source_sha256": packages[s, v]["source_identity"]["files"][0]["sha256"]} for s, v in TO.items()]}]}
    candidate.write_text(json.dumps(manifest))
    return h


def state(h):
    with h["registry"]._read_connection() as conn:
        return (transition.registry_bindings(conn, "campaign-agent"), transition.registry_manifest(conn, "campaign-agent"))


def lock_released(h):
    with open(h["env"]["RELEASE_LOCK_FILE"], "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_cb1_successful_full_commit(declared):
    h = declared
    result = run_switch(h, preflight=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PRODUCTION_DEPLOYMENT_PASS" in result.stdout
    assert state(h) == (TO, TO)
    assert (h["base"] / "release-current").resolve() == h["candidate"]
    assert not list(h["base"].glob(".release-switch.*"))
    lock_released(h)


@pytest.mark.parametrize("fault", ["smoke", "cwd"], ids=["CB2", "CB3"])
def test_cb2_cb3_known_to_failure_rolls_back(declared, fault):
    h = declared
    result = run_switch(h, preflight=False, fault=fault)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PRODUCTION_DEPLOYMENT_ROLLED_BACK" in result.stderr
    assert '"status":"rolled_back"' in result.stdout
    assert state(h) == (FROM, FROM)
    assert (h["base"] / "release-current").resolve() == h["old"]
    assert h["events"].read_text().count("systemctl:restart") == 2
    assert '"rollback_state_verified"' in result.stdout
    assert not list(h["base"].glob(".release-switch.*"))
    lock_released(h)


@pytest.mark.parametrize("fault", ["binding", "manifest"])
def test_cb4_real_unknown_state_retains_evidence_without_code_only_rollback(declared, fault):
    h = declared
    result = run_switch(h, preflight=False, fault=fault)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PRODUCTION_DEPLOYMENT_MANUAL_RECOVERY_REQUIRED" in result.stderr
    assert "UNKNOWN_BINDING_STATE" in result.stdout
    assert '"status":"rolled_back"' not in result.stdout
    assert '"status":"switched"' not in result.stdout
    assert (h["base"] / "release-current").resolve() == h["candidate"]
    assert state(h) == (({}, TO) if fault == "binding" else (TO, {}))
    assert h["events"].read_text().count("systemctl:restart") == 1
    snapshots = list(h["base"].glob(".release-switch.*"))
    assert len(snapshots) == 1 and (snapshots[0] / "state.json").is_file()
    evidence = json.loads((snapshots[0] / "recovery-evidence.json").read_text())
    assert evidence["candidate"] == "candidate" and evidence["predecessor"] == "old"
    assert set(evidence["services"]) == {"api", "mcp", "worker"}
    assert evidence["registry"]["packages"] and evidence["registry"]["manifests"]
    assert "UNKNOWN_BINDING_STATE" in evidence["gate_logs"]["rollback-guard.log"]
    assert "technical-smoke.log" in evidence["gate_logs"]
    assert len(evidence["candidate_manifest_identity"]["canonical"]) == 64
    lock_released(h)


def test_cb5_runtime_commit_order(declared):
    result = run_switch(declared, preflight=False)
    assert result.returncode == 0, result.stdout + result.stderr
    trace = result.stderr
    positions = [trace.index(token) for token in (
        "+ verify_release technical-smoke smoke", "+ verify_release final-state state",
        "+ trap - ERR", "+ rm -rf", "+ final_status=PRODUCTION_DEPLOYMENT_PASS")]
    assert positions == sorted(positions)
    source = SCRIPT.read_text()
    assert source.index("# RELEASE COMMIT POINT") < source.rindex("trap - ERR") < source.rindex('rm -rf "$backup"')
    lock_released(declared)


def test_cb6_preflight_failure_no_mutation(declared):
    h = declared
    before = h["snapshot"]()
    result = run_switch(h, preflight=False, fault="skill")
    assert result.returncode != 0
    assert "PRODUCTION_DEPLOYMENT_PREFLIGHT_BLOCKED" in result.stderr
    assert h["snapshot"]() == before and state(h) == (FROM, FROM)
    assert not h["systemd"].exists()
    assert not list(h["base"].glob(".release-switch.*"))
    lock_released(h)


def test_recovery_verification_failure_never_claims_rolled_back(declared):
    h = declared
    h["env"]["SWITCH_TEST_RECOVERY_FAIL"] = "1"
    result = run_switch(h, preflight=False, fault="smoke")
    assert result.returncode == 1
    assert "PRODUCTION_DEPLOYMENT_MANUAL_RECOVERY_REQUIRED" in result.stderr
    assert '"status":"rolled_back"' not in result.stdout
    assert state(h) == (FROM, FROM)  # restore happened, verification did not pass
    snapshots = list(h["base"].glob(".release-switch.*"))
    assert len(snapshots) == 1 and (snapshots[0] / "recovery-evidence.json").is_file()
    lock_released(h)


@pytest.mark.parametrize("fault", ["", "smoke"])
def test_cb7_legacy_same_boundary(switch_harness, fault):
    h = switch_harness
    before = state(h)
    result = run_switch(h, preflight=False, fault=fault)
    assert result.returncode == (1 if fault else 0), result.stdout + result.stderr
    assert state(h) == before
    assert "technical_smoke_passed" in result.stdout if not fault else "PRODUCTION_DEPLOYMENT_ROLLED_BACK" in result.stderr
    assert (h["base"] / "release-current").resolve() == h["old" if fault else "candidate"]
    lock_released(h)
