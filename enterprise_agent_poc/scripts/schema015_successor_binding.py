"""Exact 562 Schema015 runtime successor; prepares bindings, never approvals.

Execution stays in the existing candidate-controlled release/recovery entry.
The new protected qualification receipt is a proposed versioned contract: its
real issuance and Native evidence need separate approval. This module has no
issuer, installer, migration, service writer, or pointer mutation command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

CONTRACT_ID = "SCHEMA_015_562_RUNTIME_SUCCESSOR_V2"
PREDECESSOR = {
    "release_id": "20261007-562f201-reference-dependency-retry-v1",
    "source_commit": "562f201e362f0b10ae4fe3a8dae9a8c4d9ef91d3",
    "source_tree": "e5205965e57fce6ce2b65dd747a110d00f7f1b1a",
    "archive_sha256": "17f31cb139ef9a619e455e5999b407fc7cfd98b45c3fd3757912b16a2da39941",
    "raw_manifest_sha256": "eb6d382b3bc0b720d54349293d4f2afe73f372c8fd3838beb3eb86d557a9811c",
    "manifest_sha256": "f99d02e36e33e2d0df20127019d019c52f93b5fdb26ebd0105c306c2ae93968a",
}
TOOLING_FILES = (
    "scripts/schema015_successor_binding.py", "scripts/release_runtime_recovery.py",
    "scripts/schema_015_runtime_only.py", "scripts/release_manifest.py",
    "scripts/release_verify.py", "scripts/rollback_preflight.py", "deploy/release_switch.sh",
)
CHECKS = ("full_pytest", "stage2", "source_installation", "api_native", "mcp_native",
          "worker_native", "exact_predecessor_on_schema015", "pre_commit_failure_recovery",
          "post_commit_runtime_recovery", "business_data_preserved")
P0_CONTRACT = "SCHEMA015_P0_CONTROLLED_RELEASE_V3"
P0_SOURCE = "280ae327a9d4720c5cadfac38aba259c7ff161ae"
P0_TREE = "dc86c0e047cb9001e6ea0f88d32b129ad52f079b"
P0_CHECKS = {
    "candidate_regression": "REAL_CANDIDATE_TEST_RUN",
    "source_installation": "REAL_ARCHIVE_INSTALLATION",
    "pg_transactions": "REAL_POSTGRESQL_16_6",
    "pg_concurrency": "REAL_POSTGRESQL_16_6",
    "pg_permissions": "REAL_POSTGRESQL_16_6",
    "task_run_ownership": "REAL_POSTGRESQL_16_6",
    "orphan_task_pagination": "REAL_POSTGRESQL_16_6",
    "tenant_user_isolation": "REAL_POSTGRESQL_16_6",
    "api_sse_history": "REAL_ASGI_WITH_POSTGRESQL",
    "runtime_startup_health": "REAL_API_MCP_WORKER_PROCESSES",
    "production_schema015": "PRODUCTION_READONLY_POSTGRESQL_16_6",
    "production_backup_restore": "REAL_ENCRYPTED_BACKUP_RESTORE",
    "artifact_backup_integrity": "REAL_ARTIFACT_CHECKSUM",
    "exact_562_compatibility": "REAL_562_ON_SCHEMA015_WITH_PG_DATA",
    "pre_commit_failure_recovery": "REAL_FAILURE_RECOVERY_WITH_POSTGRESQL",
    "post_commit_runtime_recovery": "REAL_FAILURE_RECOVERY_WITH_POSTGRESQL",
    "business_data_preserved": "REAL_PERSISTENT_DATA_COMPARISON",
    "release_lock_audit": "REAL_RELEASE_LOCK_REHEARSAL",
}
P0_REPLACEMENTS = {
    "full_pytest": ["candidate_regression", "pg_transactions", "pg_concurrency", "pg_permissions"],
    "stage2": ["task_run_ownership", "orphan_task_pagination", "tenant_user_isolation", "api_sse_history"],
    "source_installation": ["source_installation", "artifact_backup_integrity"],
    "api_native": ["runtime_startup_health", "api_sse_history"],
    "mcp_native": ["runtime_startup_health"],
    "worker_native": ["runtime_startup_health", "task_run_ownership"],
    "exact_predecessor_on_schema015": ["exact_562_compatibility", "production_schema015"],
    "pre_commit_failure_recovery": ["pre_commit_failure_recovery", "release_lock_audit"],
    "post_commit_runtime_recovery": ["post_commit_runtime_recovery", "production_backup_restore"],
    "business_data_preserved": ["business_data_preserved"],
}
P0_RISKS = ["FULL_STANDARD_STAGE2_NOT_PROVEN", "DEDICATED_SYSTEMD_SERIAL_NATIVE_REHEARSAL_NOT_PROVEN",
            "REAL_PROVIDER_BUSINESS_QUALITY_NOT_PROVEN"]
P0_PRODUCTION_CHECKS = {"production_schema015", "production_backup_restore", "artifact_backup_integrity"}
P0_PG_CHECKS = {k for k, v in P0_CHECKS.items() if "POSTGRESQL" in v or "WITH_PG_DATA" in v}
P0_PG_CHECKS |= {"production_backup_restore", "business_data_preserved"}
P0_CHANGE_FILES = {*TOOLING_FILES, "tests/test_p0_lightweight_release_contract.py",
                   "deploy/p0_controlled_release.v3.proposal.json"}
COMMIT = re.compile(r"[0-9a-f]{40}")
HASH = re.compile(r"[0-9a-f]{64}")


class BindingBlocked(ValueError):
    pass


def require(condition, check):
    if not condition:
        raise BindingBlocked(check)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def is_v2(manifest):
    value = manifest.get("runtime_only_release")
    return isinstance(value, dict) and type(value.get("schema_version")) is int and value["schema_version"] == 2


def is_v3(manifest):
    value = manifest.get("runtime_only_release")
    return isinstance(value, dict) and type(value.get("schema_version")) is int and value["schema_version"] == 3


def is_exact_pair(manifest):
    return is_v2(manifest) or is_v3(manifest)


def validate(value):
    fixed = {
        "schema_version": 2, "contract_id": CONTRACT_ID, "release_mode": "RUNTIME_ONLY",
        "current_schema": "015", "target_schema": "015", "migration_action": "NONE",
        "migration015_state": "ALREADY_APPLIED", "pending": 0, "unknown": 0,
        "checksum_drift": 0, "migration015_blob": "96bba0ea64ade51670775cf6f69b806a2248342a",
        "recovery_contract": CONTRACT_ID, "recovery_mode": "PREDECESSOR_ON_SCHEMA_015",
        "post_commit_runtime_rollback": "ENABLED", "data_contract": "member_account_status_v1",
        "exact_predecessor": PREDECESSOR,
    }
    controlled = isinstance(value, dict) and type(value.get("schema_version")) is int and value["schema_version"] == 3
    if controlled:
        fixed.update(schema_version=3, contract_id=P0_CONTRACT, recovery_contract=P0_CONTRACT,
                     qualification_profile=P0_CONTRACT, business_base_source=P0_SOURCE, business_base_tree=P0_TREE,
                     maximum_approval_lifetime_seconds=86400)
    require(isinstance(value, dict) and set(value) == set(fixed) | {"successor"}, "successor_binding_fields")
    require(all(value[k] == v for k, v in fixed.items())
            and all(type(value[k]) is int for k in ("schema_version", "pending", "unknown", "checksum_drift")),
            "successor_binding_fixed_contract")
    successor = value["successor"]
    require(isinstance(successor, dict) and set(successor) == {
        "source_commit", "source_tree", "parent_source_commit", "tooling_pins"}, "successor_identity_fields")
    require(all(isinstance(successor[k], str) and COMMIT.fullmatch(successor[k])
                for k in ("source_commit", "source_tree", "parent_source_commit"))
            and successor["source_commit"] != PREDECESSOR["source_commit"], "successor_source_identity")
    if controlled:
        require(type(value["maximum_approval_lifetime_seconds"]) is int
                and successor["parent_source_commit"] == P0_SOURCE
                and successor["source_commit"] != P0_SOURCE, "p0_direct_successor_required")
    pins = successor["tooling_pins"]
    require(isinstance(pins, dict) and set(pins) == set(TOOLING_FILES)
            and all(isinstance(v, str) and HASH.fullmatch(v) for v in pins.values()), "successor_tooling_pins")
    return value


def declaration(root, manifest):
    value = validate(manifest.get("runtime_only_release"))
    require(value["successor"]["source_commit"] == manifest["source_commit"], "successor_manifest_source")
    for relative, expected in value["successor"]["tooling_pins"].items():
        path = root / relative
        require(path.is_file() and not path.is_symlink() and path.resolve() == path,
                "successor_tooling_path")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, "successor_tooling_file_identity")
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "qualification_duplicate_key")
        result[key] = value
    return result


def protected_json(path):
    """Root seal must have a fully trusted chain; there is no permissive fallback."""
    require(path.is_absolute(), "qualification_path")
    for parent in (path.parent, *path.parent.parents):
        observed = parent.lstat()
        require(stat.S_ISDIR(observed.st_mode) and observed.st_uid == 0
                and not observed.st_mode & 0o022, "qualification_trusted_ancestor")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        observed = os.fstat(descriptor)
        require(stat.S_ISREG(observed.st_mode) and observed.st_uid == 0
                and stat.S_IMODE(observed.st_mode) == 0o600 and observed.st_nlink == 1
                and observed.st_size <= 262144, "qualification_root_seal")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(262145)
        require(len(raw) <= 262144, "qualification_size")
        return json.loads(raw, object_pairs_hook=_pairs), hashlib.sha256(raw).hexdigest()
    finally:
        os.close(descriptor)


def qualification(base, root, manifest, schema_fingerprint, *, now=None):
    """Read-only additional gate; env approval pins and live DB gates still apply."""
    from scripts import rollback_preflight as gate
    value = declaration(root, manifest)
    gate.require(root.parent.parent == base / "releases" and base.resolve() == base,
                 "qualification_controlled_release")
    paths = base / "shared/release-qualifications"
    approved, _ = protected_json(paths / (manifest["release_id"] + ".approval.v1.json"))
    evidence, evidence_hash = protected_json(paths / (manifest["release_id"] + ".evidence.v1.json"))
    candidate = {
        "release_id": manifest["release_id"], **{k: value["successor"][k] for k in (
            "source_commit", "source_tree", "parent_source_commit")},
        "archive_sha256": manifest["archive_sha256"],
        "raw_manifest_sha256": hashlib.sha256((root.parent / (manifest["release_id"] + ".manifest.json")).read_bytes()).hexdigest(),
        "manifest_sha256": gate.digest(manifest),
    }
    if is_v3(manifest):
        return _p0_qualification(paths, value, candidate, approved, evidence, evidence_hash,
                                 schema_fingerprint, now=now)
    require(set(approved) == {"schema_version", "scope", "issuer_uid", "audit_id", "issued_at",
                             "expires_at", "binding_sha256", "candidate", "predecessor", "evidence_sha256"},
            "qualification_approval_fields")
    require(type(approved["schema_version"]) is int and approved["schema_version"] == 1
            and approved["scope"] == "SCHEMA015_EXACT_PAIR_RELEASE_RECOVERY"
            and type(approved["issuer_uid"]) is int and approved["issuer_uid"] == 0
            and isinstance(approved["audit_id"], str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", approved["audit_id"]),
            "qualification_approval_identity")
    instant = time.time() if now is None else now
    require(type(approved["issued_at"]) is int and type(approved["expires_at"]) is int
            and 0 < approved["issued_at"] <= instant < approved["expires_at"], "qualification_approval_expired")
    require(approved["candidate"] == candidate and approved["predecessor"] == PREDECESSOR
            and approved["binding_sha256"] == digest(value)
            and approved["evidence_sha256"] == evidence_hash, "qualification_approval_binding")
    expected = {"schema_version": 1, "scope": "ISOLATED_NATIVE_SCHEMA015_EXACT_PAIR",
                "candidate": candidate, "predecessor": PREDECESSOR, "binding_sha256": digest(value),
                "schema_fingerprint": schema_fingerprint, "schema": "015", "pending": 0,
                "migration_action": "NONE", "data_contract": "member_account_status_v1",
                "checks": dict.fromkeys(CHECKS, "PASS")}
    require(evidence == expected and type(evidence["schema_version"]) is int
            and type(evidence["pending"]) is int, "qualification_native_evidence_required")
    return {"status": "exact_pair_qualification_verified", "audit_id": approved["audit_id"],
            "expires_at": approved["expires_at"], "evidence_sha256": evidence_hash}


def _p0_qualification(paths, value, candidate, approved, evidence, evidence_hash, schema_fingerprint, *, now):
    """Consume three independently authorized Root-protected records; never issue one.

    Labels/hashes do not prove a test ran. Authorized operations must verify the
    actual source-bound reports before installing these records. Fake-reader
    component tests prove only this consumer, not PG, Root or release readiness.
    """
    policy, policy_hash = protected_json(paths / (candidate["release_id"] + ".policy-approval.v1.json"))
    instant = time.time() if now is None else now
    common = {"schema_version", "scope", "issuer_uid", "authority", "principal_id", "audit_id",
              "decision", "issued_at", "expires_at", "binding_sha256", "candidate", "predecessor"}
    require(set(policy) == common | {"replacements", "accepted_risks", "test_environment_id",
                                    "production_environment_id"}, "p0_policy_approval_fields")
    require(set(approved) == common | {"policy_approval_sha256", "policy_audit_id", "evidence_sha256"},
            "p0_deployment_approval_fields")
    for record, scope, authority in (
        (policy, "P0_EXACT_PAIR_POLICY_CHANGE", "PROJECT_OWNER_POLICY_AUTHORITY"),
        (approved, "P0_EXACT_PAIR_PRODUCTION_DEPLOYMENT", "PRODUCTION_RELEASE_AUTHORITY"),
    ):
        require(type(record["schema_version"]) is int and record["schema_version"] == 1
                and type(record["issuer_uid"]) is int and record["issuer_uid"] == 0
                and record["scope"] == scope and record["authority"] == authority
                and record["decision"] == "APPROVED"
                and all(isinstance(record[k], str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", record[k])
                        for k in ("principal_id", "audit_id")), "p0_actual_approval_required")
        require(type(record["issued_at"]) is int and type(record["expires_at"]) is int
                and 0 < record["issued_at"] <= instant < record["expires_at"]
                and record["expires_at"] - record["issued_at"] <= value["maximum_approval_lifetime_seconds"],
                "p0_approval_time_window")
        require(record["candidate"] == candidate and record["predecessor"] == PREDECESSOR
                and record["binding_sha256"] == digest(value), "p0_approval_exact_pair")
    require(approved["audit_id"] != policy["audit_id"] and approved["policy_audit_id"] == policy["audit_id"]
            and approved["policy_approval_sha256"] == policy_hash
            and approved["issued_at"] >= policy["issued_at"] and approved["expires_at"] <= policy["expires_at"]
            and approved["evidence_sha256"] == evidence_hash, "p0_independent_deployment_approval")
    require(policy["replacements"] == P0_REPLACEMENTS and policy["accepted_risks"] == P0_RISKS,
            "p0_explicit_replacement_and_risk_acceptance")
    environments = (policy["test_environment_id"], policy["production_environment_id"])
    require(all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{1,127}", v)
                for v in environments) and environments[0] != environments[1], "p0_environment_isolation")
    expected = {"schema_version": 1, "scope": "CONTROLLED_PG_API_SCHEMA015_EXACT_PAIR", "candidate": candidate,
                "predecessor": PREDECESSOR, "binding_sha256": digest(value), "schema_fingerprint": schema_fingerprint,
                "schema": "015", "pending": 0, "migration_action": "NONE", "data_contract": "member_account_status_v1",
                "replacements": P0_REPLACEMENTS, "uncovered_risks": P0_RISKS}
    require(set(evidence) == set(expected) | {"checks"} and all(evidence[k] == v for k, v in expected.items())
            and type(evidence["schema_version"]) is int and type(evidence["pending"]) is int,
            "p0_evidence_schema_identity")
    checks = evidence["checks"]
    require(isinstance(checks, dict) and set(checks) == set(P0_CHECKS), "p0_required_real_checks")
    for name, kind in P0_CHECKS.items():
        record = checks[name]
        require(isinstance(record, dict) and set(record) == {"status", "evidence_kind", "source_commit",
                "environment_id", "report_sha256", "run_id", "observed_at", "measurements"}
                and record["status"] == "PASS" and record["evidence_kind"] == kind
                and record["source_commit"] == candidate["source_commit"]
                and record["environment_id"] == environments[int(name in P0_PRODUCTION_CHECKS)]
                and isinstance(record["report_sha256"], str) and HASH.fullmatch(record["report_sha256"])
                and isinstance(record["run_id"], str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", record["run_id"])
                and type(record["observed_at"]) is int and 0 < record["observed_at"] <= instant
                and instant - record["observed_at"] <= 86400, "p0_real_evidence_required:" + name)
        facts = record["measurements"]
        require(isinstance(facts, dict) and type(facts.get("assertions")) is int and facts["assertions"] > 0
                and all(type(facts.get(k)) is int and facts[k] == 0 for k in ("failures", "errors", "skipped")),
                "p0_real_assertions_required:" + name)
        if name in P0_PG_CHECKS:
            require(facts.get("postgres_version") == "16.6" and facts.get("schema") == "015"
                    and facts.get("database_identity") == record["environment_id"], "p0_real_pg_required:" + name)
        if name == "production_schema015":
            require(all(type(facts.get(k)) is int and facts[k] == 0 for k in ("pending", "unknown", "checksum_drift"))
                    and facts.get("migration_action") == "NONE", "p0_production_ledger_required")
        if name == "runtime_startup_health":
            services = facts.get("services")
            require(isinstance(services, dict) and set(services) == {"api", "mcp", "worker"}
                    and all(isinstance(v, dict) and type(v.get("pid")) is int and v["pid"] > 0
                            and v.get("health") == "PASS" and v.get("source_commit") == candidate["source_commit"]
                            and v.get("source_tree") == candidate["source_tree"] for v in services.values()),
                    "p0_three_service_health_required")
        if name == "production_backup_restore":
            require(all(isinstance(facts.get(k), str) and HASH.fullmatch(facts[k])
                        for k in ("ciphertext_sha256", "restore_report_sha256"))
                    and facts.get("data_preserved") is True and facts.get("restore_environment_id") == environments[0],
                    "p0_restorable_backup_required")
        if name == "artifact_backup_integrity":
            require(facts.get("candidate_archive_sha256") == candidate["archive_sha256"]
                    and facts.get("predecessor_archive_sha256") == PREDECESSOR["archive_sha256"],
                    "p0_artifact_backup_identity")
        if name in {"exact_562_compatibility", "pre_commit_failure_recovery", "post_commit_runtime_recovery",
                    "business_data_preserved"}:
            require(facts.get("predecessor_source") == PREDECESSOR["source_commit"]
                    and facts.get("schema_rollback") is False and facts.get("data_preserved") is True,
                    "p0_real_exact_recovery_required:" + name)
        if name == "release_lock_audit":
            require(all(facts.get(k) is True for k in ("exclusive_global_lock", "preflight_before_mutation",
                                                       "failure_stop_verified")), "p0_release_lock_audit_required")
    return {"status": "p0_controlled_qualification_verified", "audit_id": approved["audit_id"],
            "policy_audit_id": policy["audit_id"], "expires_at": approved["expires_at"],
            "evidence_sha256": evidence_hash, "policy_approval_sha256": policy_hash,
            "qualification_profile": P0_CONTRACT, "native_acceptance_claim": False}


def prepare(repo, source, *, controlled=False):
    """Offline source declaration only. No protected qualification is created."""
    require(isinstance(source, str) and COMMIT.fullmatch(source), "prepare_exact_source_required")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args])
    require(git("rev-parse", source + "^{commit}").decode().strip() == source, "prepare_source_identity")
    subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", PREDECESSOR["source_commit"], source],
                   check=True, capture_output=True)
    parents = git("show", "-s", "--format=%P", source).decode().split()
    require(len(parents) == 1, "prepare_single_parent_required")
    if controlled:
        require(parents == [P0_SOURCE] and git("rev-parse", P0_SOURCE + "^{tree}").decode().strip() == P0_TREE,
                "prepare_p0_direct_parent_required")
        changed = git("diff-tree", "--no-commit-id", "--name-only", "-r", source).decode().splitlines()
        require(all(p.startswith("enterprise_agent_poc/") and p.removeprefix("enterprise_agent_poc/") in P0_CHANGE_FILES
                    for p in changed), "prepare_p0_business_and_schema_immutable")
    pins = {p: hashlib.sha256(git("show", source + ":enterprise_agent_poc/" + p)).hexdigest()
            for p in TOOLING_FILES}
    base_files = git("ls-tree", "-r", "--name-only", PREDECESSOR["source_commit"], "--", "enterprise_agent_poc/migrations/").decode().splitlines()
    own_files = git("ls-tree", "-r", "--name-only", source, "--", "enterprise_agent_poc/migrations/").decode().splitlines()
    require(own_files == base_files and all(git("rev-parse", source + ":" + p) == git("rev-parse", PREDECESSOR["source_commit"] + ":" + p)
                                          for p in base_files), "prepare_migrations_unchanged")
    legacy = json.loads(git("show", PREDECESSOR["source_commit"] + ":enterprise_agent_poc/deploy/schema_015_runtime_only.v1.json"))
    value = {**legacy, "schema_version": 2, "contract_id": CONTRACT_ID, "recovery_contract": CONTRACT_ID,
             "data_contract": "member_account_status_v1", "exact_predecessor": dict(PREDECESSOR),
             "successor": {"source_commit": source, "source_tree": git("rev-parse", source + "^{tree}").decode().strip(),
                           "parent_source_commit": parents[0], "tooling_pins": pins}}
    if controlled:
        value.update(schema_version=3, contract_id=P0_CONTRACT, recovery_contract=P0_CONTRACT,
                     qualification_profile=P0_CONTRACT, business_base_source=P0_SOURCE, business_base_tree=P0_TREE,
                     maximum_approval_lifetime_seconds=86400)
    return validate(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare",))
    parser.add_argument("--successor-source", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--qualification-mode", choices=("native-v2", "p0-controlled-v3"), default="native-v2")
    args = parser.parse_args(argv)
    try:
        value = prepare(Path(__file__).resolve().parents[2], args.successor_source,
                        controlled=args.qualification_mode == "p0-controlled-v3")
        require(not args.output.exists() and not args.output.is_symlink(), "prepare_output_exists")
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps({"status": "BINDING_PREPARED_NOT_RELEASE_AUTHORITY", "source": args.successor_source,
                          "binding_sha256": digest(value), "approval_issued": False}))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "check": str(exc) if isinstance(exc, BindingBlocked) else "prepare_input_or_git"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
