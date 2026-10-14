"""Consumer-only isolated tests. Root readers/PG receipts are boundary doubles.

No real policy/deployment approval, PG qualification, backup, Native startup,
Production permission, Provider call or release is created by these tests.
"""
import copy
import json

import pytest

from scripts import release_manifest as manifests, release_runtime_recovery as recovery
from scripts import rollback_preflight as gate, schema015_successor_binding as binding
from test_schema015_successor_binding import pair, qualified, schema_boundaries


def v3(pair):
    base, root, manifest = pair
    value = manifest["runtime_only_release"]
    value.update(schema_version=3, contract_id=binding.P0_CONTRACT, recovery_contract=binding.P0_CONTRACT,
                 qualification_profile=binding.P0_CONTRACT, business_base_source=binding.P0_SOURCE,
                 business_base_tree=binding.P0_TREE, maximum_approval_lifetime_seconds=86400)
    value["successor"]["parent_source_commit"] = binding.P0_SOURCE
    (root.parent / "offline-fixture.manifest.json").write_text(json.dumps(manifest))
    return base, root, manifest


@pytest.fixture
def controlled(pair, monkeypatch):
    base, root, manifest = v3(pair)
    value = manifest["runtime_only_release"]
    candidate = {"release_id": manifest["release_id"],
                 **{k: value["successor"][k] for k in ("source_commit", "source_tree", "parent_source_commit")},
                 "archive_sha256": manifest["archive_sha256"],
                 "raw_manifest_sha256": binding.hashlib.sha256((root.parent / "offline-fixture.manifest.json").read_bytes()).hexdigest(),
                 "manifest_sha256": gate.digest(manifest)}
    common = {"schema_version": 1, "issuer_uid": 0, "principal_id": "FIXTURE_NOT_AN_AUTHORIZED_ACTOR",
              "decision": "APPROVED", "issued_at": 10, "expires_at": 100,
              "candidate": candidate, "predecessor": copy.deepcopy(binding.PREDECESSOR),
              "binding_sha256": binding.digest(value)}
    policy = {**copy.deepcopy(common), "scope": "P0_EXACT_PAIR_POLICY_CHANGE", "audit_id": "FIXTURE_POLICY_NOT_EFFECTIVE",
              "authority": "PROJECT_OWNER_POLICY_AUTHORITY", "replacements": copy.deepcopy(binding.P0_REPLACEMENTS),
              "accepted_risks": list(binding.P0_RISKS), "test_environment_id": "marked-private-test",
              "production_environment_id": "production-readonly-fixture"}
    approved = {**copy.deepcopy(common), "scope": "P0_EXACT_PAIR_PRODUCTION_DEPLOYMENT",
                "authority": "PRODUCTION_RELEASE_AUTHORITY", "audit_id": "FIXTURE_DEPLOYMENT_NOT_EFFECTIVE",
                "policy_approval_sha256": "1" * 64, "policy_audit_id": policy["audit_id"], "evidence_sha256": "2" * 64}
    evidence = {"schema_version": 1, "scope": "CONTROLLED_PG_API_SCHEMA015_EXACT_PAIR", "candidate": candidate,
                "predecessor": copy.deepcopy(binding.PREDECESSOR), "binding_sha256": binding.digest(value),
                "schema_fingerprint": "e" * 64, "schema": "015", "pending": 0, "migration_action": "NONE",
                "data_contract": "member_account_status_v1", "replacements": copy.deepcopy(binding.P0_REPLACEMENTS),
                "uncovered_risks": list(binding.P0_RISKS), "checks": {}}
    for name, kind in binding.P0_CHECKS.items():
        environment = policy["production_environment_id" if name in binding.P0_PRODUCTION_CHECKS else "test_environment_id"]
        facts = {"assertions": 1, "failures": 0, "errors": 0, "skipped": 0}
        if name in binding.P0_PG_CHECKS:
            version = "16.14" if name in binding.P0_PRODUCTION_CHECKS else "16.6"
            facts.update(postgres_version=version, schema="015", database_identity=environment)
        if name == "production_schema015":
            facts.update(pending=0, unknown=0, checksum_drift=0, migration_action="NONE")
        if name == "runtime_startup_health":
            facts["services"] = {r: {"pid": n + 1, "health": "PASS", "source_commit": candidate["source_commit"],
                                     "source_tree": candidate["source_tree"]} for n, r in enumerate(("api", "mcp", "worker"))}
        if name == "production_backup_restore":
            facts.update(ciphertext_sha256="3" * 64, restore_report_sha256="4" * 64,
                         data_preserved=True, restore_environment_id=policy["test_environment_id"])
        if name == "artifact_backup_integrity":
            facts.update(candidate_archive_sha256=candidate["archive_sha256"],
                         predecessor_archive_sha256=binding.PREDECESSOR["archive_sha256"])
        if name in {"exact_d906_compatibility", "pre_commit_failure_recovery", "post_commit_runtime_recovery", "business_data_preserved"}:
            facts.update(predecessor_source=binding.PREDECESSOR["source_commit"], schema_rollback=False, data_preserved=True)
        if name == "release_lock_audit":
            facts.update(exclusive_global_lock=True, preflight_before_mutation=True, failure_stop_verified=True)
        evidence["checks"][name] = {"status": "PASS", "evidence_kind": kind, "source_commit": candidate["source_commit"],
                                    "environment_id": environment, "report_sha256": "5" * 64,
                                    "run_id": "FIXTURE_NOT_A_REAL_RUN", "observed_at": 15, "measurements": facts}
    records = {"offline-fixture.approval.v1.json": (approved, "6" * 64),
               "offline-fixture.evidence.v1.json": (evidence, "2" * 64),
               "offline-fixture.policy-approval.v1.json": (policy, "1" * 64)}
    # Explicit fake trust boundary, never root-owned files or actual approvals.
    monkeypatch.setattr(binding, "protected_json", lambda path: records[path.name])
    return base, root, manifest, approved, evidence, policy, records


def verify(fixture):
    return binding.qualification(*fixture[:3], "e" * 64, now=20)


def test_explicit_v3_accepts_complete_consumer_fixture_not_native_authority(controlled):
    base, root, manifest, *_ = controlled
    result = verify(controlled)
    assert result["status"] == "p0_controlled_qualification_verified" and result["native_acceptance_claim"] is False
    assert result["policy_audit_id"] != result["audit_id"]
    assert binding.is_exact_pair(manifest) and binding.is_v3(manifest) and not binding.is_v2(manifest)
    assert manifests.validate_manifest_contract(manifest) == manifest
    assert recovery.declared(root, manifest)["exact_predecessor"] == binding.PREDECESSOR
    assert not (base / "shared").exists()  # No actual approval or audit issued.


@pytest.mark.parametrize("name", tuple(binding.P0_CHECKS))
def test_every_real_proof_is_mandatory_not_an_optional_native_skip(controlled, name):
    controlled[4]["checks"].pop(name)
    with pytest.raises(binding.BindingBlocked, match="required_real_checks"):
        verify(controlled)


@pytest.mark.parametrize("name", tuple(binding.P0_CHECKS))
def test_mock_and_fixture_evidence_never_pass_real_check(controlled, name):
    controlled[4]["checks"][name]["evidence_kind"] = "MOCK_FIXTURE_PASS"
    with pytest.raises(binding.BindingBlocked, match="real_evidence_required"):
        verify(controlled)


@pytest.mark.parametrize("name", tuple(binding.P0_PG_CHECKS))
def test_sqlite_or_wrong_pg_version_cannot_substitute_for_pg16(controlled, name):
    controlled[4]["checks"][name]["measurements"]["postgres_version"] = "sqlite"
    with pytest.raises(binding.BindingBlocked, match="real_pg_required"):
        verify(controlled)


def test_production_and_test_keep_separate_exact_postgres_versions(controlled):
    checks = controlled[4]["checks"]
    assert binding.P0_CHECKS["production_schema015"] == "PRODUCTION_READONLY_POSTGRESQL_16_14"
    for name in binding.P0_PG_CHECKS:
        expected = "16.14" if name in binding.P0_PRODUCTION_CHECKS else "16.6"
        assert checks[name]["measurements"]["postgres_version"] == expected
    assert verify(controlled)["status"] == "p0_controlled_qualification_verified"


@pytest.mark.parametrize("name", tuple(sorted(binding.P0_PG_CHECKS)))
def test_postgres_versions_cannot_be_swapped_between_production_and_test(controlled, name):
    checks = controlled[4]["checks"]
    checks[name]["measurements"]["postgres_version"] = (
        "16.6" if name in binding.P0_PRODUCTION_CHECKS else "16.14"
    )
    with pytest.raises(binding.BindingBlocked, match="real_pg_required:" + name):
        verify(controlled)


@pytest.mark.parametrize("version", ["16.13", "16.15", "17.0", "16", "16.14 (Ubuntu)"])
@pytest.mark.parametrize("name", ["production_schema015", "production_backup_restore"])
def test_production_version_is_exact_not_a_compatible_range(controlled, name, version):
    controlled[4]["checks"][name]["measurements"]["postgres_version"] = version
    with pytest.raises(binding.BindingBlocked, match="real_pg_required:" + name):
        verify(controlled)


@pytest.mark.parametrize("missing", ["offline-fixture.policy-approval.v1.json", "offline-fixture.approval.v1.json"])
def test_policy_and_deployment_are_separate_and_both_required(controlled, missing):
    controlled[6].pop(missing)
    with pytest.raises(KeyError): verify(controlled)


@pytest.mark.parametrize("role,key,value", [
    (5, "decision", "PROPOSED"), (3, "decision", "REVOKED"), (5, "authority", "platform_admin"),
    (3, "scope", "TEST_ONLY_NO_PRODUCTION"), (5, "issuer_uid", 1000), (3, "issuer_uid", False),
    (5, "expires_at", 20), (3, "issued_at", 21), (5, "audit_id", ""), (3, "principal_id", ""),
    (5, "expires_at", 100000), (3, "policy_approval_sha256", "0" * 64),
    (3, "policy_audit_id", "wrong"), (3, "audit_id", "FIXTURE_POLICY_NOT_EFFECTIVE"),
    (3, "evidence_sha256", "0" * 64), (5, "test_environment_id", "*"),
])
def test_unapproved_wrong_expired_or_nonindependent_decisions_rejected(controlled, role, key, value):
    controlled[role][key] = value
    with pytest.raises(binding.BindingBlocked): verify(controlled)


@pytest.mark.parametrize("role,section,key", [(3, "candidate", k) for k in
    ("source_commit", "source_tree", "parent_source_commit", "archive_sha256", "raw_manifest_sha256", "manifest_sha256")]
    + [(5, "predecessor", k) for k in binding.PREDECESSOR])
def test_all_approval_identity_and_manifest_drift_rejected(controlled, role, section, key):
    controlled[role][section] = copy.deepcopy(controlled[role][section])
    controlled[role][section][key] = "wrong"
    with pytest.raises(binding.BindingBlocked, match="exact_pair"):
        verify(controlled)


@pytest.mark.parametrize("key,value", [("schema", "016"), ("pending", 1), ("pending", False),
    ("migration_action", "APPLY"), ("schema_fingerprint", "0" * 64), ("scope", "ISOLATED_NATIVE_SCHEMA015_EXACT_PAIR")])
def test_schema_and_evidence_scope_never_downgraded(controlled, key, value):
    controlled[4][key] = value
    with pytest.raises(binding.BindingBlocked, match="schema_identity"): verify(controlled)


@pytest.mark.parametrize("fault", ["source", "environment", "future", "stale", "report", "run", "failed", "skipped", "zero"])
def test_source_bound_current_reports_and_real_success_assertions_required(controlled, fault):
    record = controlled[4]["checks"]["api_sse_history"]
    if fault == "source": record["source_commit"] = "f" * 40
    elif fault == "environment": record["environment_id"] = controlled[5]["production_environment_id"]
    elif fault == "future": record["observed_at"] = 21
    elif fault == "stale": record["observed_at"] = -90000
    elif fault == "report": record["report_sha256"] = "short"
    elif fault == "run": record["run_id"] = ""
    else: record["measurements"][{"failed": "failures", "skipped": "skipped", "zero": "assertions"}[fault]] = 0 if fault == "zero" else 1
    with pytest.raises(binding.BindingBlocked): verify(controlled)


@pytest.mark.parametrize("fault", ["backup_hash", "restore_hash", "restore_environment", "data_loss", "old_target",
    "down_schema", "no_api", "wrong_worker_source", "wrong_worker_tree", "no_health", "unlocked", "late_preflight", "not_stopped"])
def test_real_restore_startup_and_lock_cannot_be_replaced_by_migration_none(controlled, fault):
    checks = controlled[4]["checks"]
    backup = checks["production_backup_restore"]["measurements"]
    recover = checks["post_commit_runtime_recovery"]["measurements"]
    services = checks["runtime_startup_health"]["measurements"]["services"]
    lock = checks["release_lock_audit"]["measurements"]
    if fault == "backup_hash": backup["ciphertext_sha256"] = ""
    elif fault == "restore_hash": backup["restore_report_sha256"] = ""
    elif fault == "restore_environment": backup["restore_environment_id"] = controlled[5]["production_environment_id"]
    elif fault == "data_loss": recover["data_preserved"] = False
    elif fault == "old_target": recover["predecessor_source"] = recovery.PREDECESSOR["source_commit"]
    elif fault == "down_schema": recover["schema_rollback"] = True
    elif fault == "no_api": services.pop("api")
    elif fault == "wrong_worker_source": services["worker"]["source_commit"] = "f" * 40
    elif fault == "wrong_worker_tree": services["worker"]["source_tree"] = "f" * 40
    elif fault == "no_health": services["mcp"]["health"] = "FAIL"
    else: lock[{"unlocked": "exclusive_global_lock", "late_preflight": "preflight_before_mutation", "not_stopped": "failure_stop_verified"}[fault]] = False
    with pytest.raises(binding.BindingBlocked): verify(controlled)


@pytest.mark.parametrize("role,key", [(5, "replacements"), (5, "accepted_risks"), (4, "replacements"), (4, "uncovered_risks")])
def test_no_silent_native_replacement_or_unaccepted_risk(controlled, role, key):
    controlled[role][key] = {}
    with pytest.raises(binding.BindingBlocked): verify(controlled)


def test_v2_still_requires_original_native_map_and_reads_no_new_policy(qualified):
    base, root, manifest, _, evidence, seen = qualified
    evidence["checks"]["stage2"] = "NOT_VERIFIED"
    with pytest.raises(binding.BindingBlocked, match="native_evidence_required"):
        binding.qualification(base, root, manifest, "e" * 64, now=20)
    assert len(seen) == 2 and binding.is_v2(manifest) and not binding.is_v3(manifest)


def test_v3_uses_existing_schema_and_recovery_entry_without_new_runner(pair, monkeypatch):
    base, root, manifest = schema_boundaries(v3(pair), monkeypatch)
    visited = []
    monkeypatch.setattr(binding, "qualification", lambda *args: visited.append(args[2]["runtime_only_release"]["schema_version"]))
    result = recovery.verify_schema(base, root, binding.PREDECESSOR["release_id"], binding.PREDECESSOR["source_commit"], database_url="fixture-not-a-db")
    assert visited == [3] and result["pending"] == 0 and result["target_source_commit"] == binding.PREDECESSOR["source_commit"]


def test_actual_missing_protected_v3_records_fail_before_db_or_pointer_change(pair, monkeypatch):
    base, root, manifest = schema_boundaries(v3(pair), monkeypatch)
    current = base / "releases/prior/enterprise_agent_poc"
    current.mkdir(parents=True)
    (base / "release-current").symlink_to(current)
    called = []
    monkeypatch.setattr(gate, "read_history", lambda *a, **k: called.append("db"))
    with pytest.raises((OSError, binding.BindingBlocked)):
        recovery.verify_schema(base, root, binding.PREDECESSOR["release_id"], binding.PREDECESSOR["source_commit"], database_url="fixture-not-a-db")
    assert called == [] and (base / "release-current").resolve() == current


def test_old_qualification_schema_cannot_authorize_new_mode(pair):
    _, _, manifest = v3(pair)
    value = manifest["runtime_only_release"]
    value["successor"]["parent_source_commit"] = binding.PREDECESSOR["source_commit"]
    with pytest.raises(binding.BindingBlocked, match="direct_successor"):
        binding.validate(value)


@pytest.mark.parametrize("field,value", [("business_base_source", "f" * 40), ("business_base_tree", "f" * 40),
    ("qualification_profile", binding.CONTRACT_ID), ("maximum_approval_lifetime_seconds", 86400.0),
    ("maximum_approval_lifetime_seconds", 172800)])
def test_p0_profile_and_fixed_scope_are_not_free_form(pair, field, value):
    _, _, manifest = v3(pair)
    manifest["runtime_only_release"][field] = value
    with pytest.raises(binding.BindingBlocked): binding.validate(manifest["runtime_only_release"])


@pytest.mark.parametrize("name,field,value", [("production_schema015", "pending", 1),
    ("production_schema015", "unknown", 1), ("production_schema015", "checksum_drift", 1),
    ("production_schema015", "migration_action", "APPLY"), ("pg_permissions", "database_identity", "other-tenant-db"),
    ("artifact_backup_integrity", "predecessor_archive_sha256", "0" * 64)])
def test_real_ledger_database_and_backup_identities_cannot_drift(controlled, name, field, value):
    controlled[4]["checks"][name]["measurements"][field] = value
    with pytest.raises(binding.BindingBlocked): verify(controlled)


def test_proposal_is_not_an_effective_policy_and_requires_the_same_map():
    proposal = json.loads((binding.Path(__file__).resolve().parents[1] / "deploy/p0_controlled_release.v3.proposal.json").read_text())
    assert proposal["status"] == "PROPOSED_CODE_ONLY_NOT_APPROVED_NOT_RELEASE_READY"
    assert proposal["replacement_map"] == binding.P0_REPLACEMENTS
    assert proposal["uncovered_risks_requiring_owner_acceptance"] == binding.P0_RISKS
    assert set(proposal["required_evidence"]) == set(binding.P0_CHECKS)
