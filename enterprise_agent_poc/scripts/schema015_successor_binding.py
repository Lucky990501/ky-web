"""Exact 562 Schema015 runtime successor; prepares bindings, never approvals.

Execution stays in the existing candidate-controlled release/recovery entry.
The new protected qualification receipt is a proposed versioned contract: its
real issuance and Native evidence need separate approval. This module has no
issuer, installer, migration, service writer, or pointer mutation command.
"""
from __future__ import annotations

import argparse
import fcntl
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
STATE_CONTRACT = "SCHEMA015_P0_CONTROLLED_RELEASE_V3_STATE_V1"
STATE_BASE_SOURCE = "7ca9be5ecf912f95d363b93ca11a9b0b82ea4fb2"
STATE_BASE_TREE = "32cc824d7aa4dd65633e1d63816ffef76ced0c82"
GLOBAL_RELEASE_LOCK = Path("/run/lock/enterprise-agent-workbench-release.lock")
STATE_PRODUCER_FILES = (
    "scripts/v3_trusted_record_bootstrap.py", "scripts/runtime_policy_rollout.py",
    "scripts/schema015_successor_binding.py", "scripts/rollback_preflight.py",
    "scripts/verify_runtime_config.py", "scripts/release_manifest.py", "scripts/release_runtime_recovery.py",
    "app/settings.py", "deploy/rollback_compatibility.json",
)
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
    "production_schema015": "PRODUCTION_READONLY_POSTGRESQL_16_14",
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
STATE_CHANGE_FILES = {*P0_CHANGE_FILES,
    "scripts/v3_trusted_record_bootstrap.py", "scripts/v3_bootstrap_verified_entry.py",
    "tests/test_v3_trusted_record_bootstrap.py", "tests/test_v3_bootstrap_verified_entry.py",
    "tests/test_v3_bootstrap_approval_ids.py", "tests/test_v3_bootstrap_persistence.py",
    "tests/test_v3_bootstrap_frozen_consumer_boundary.py", "tests/test_v3_state_contract.py",
    "tests/test_v3_state_consumer_entry.py", "tests/test_v3_state_frozen_reader_rejection.py",
    "tests/test_v3_state_producer_consumer_joint.py",
    "deploy/V3_TRUSTED_RECORD_BOOTSTRAP_REVIEW_V1.md"}
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
    return isinstance(value, dict) and type(value.get("schema_version")) is int and value["schema_version"] in (3, 4)


def is_stateful(manifest):
    value = manifest.get("runtime_only_release")
    return isinstance(value, dict) and type(value.get("schema_version")) is int and value["schema_version"] == 4


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
    controlled = isinstance(value, dict) and type(value.get("schema_version")) is int and value["schema_version"] in (3, 4)
    stateful = controlled and value["schema_version"] == 4
    if controlled:
        fixed.update(schema_version=3, contract_id=P0_CONTRACT, recovery_contract=P0_CONTRACT,
                     qualification_profile=P0_CONTRACT, business_base_source=P0_SOURCE, business_base_tree=P0_TREE,
                     maximum_approval_lifetime_seconds=86400)
    if stateful:
        fixed.update(schema_version=4, contract_id=STATE_CONTRACT, recovery_contract=STATE_CONTRACT,
                     qualification_profile=STATE_CONTRACT, state_contract=STATE_CONTRACT,
                     security_base_source=STATE_BASE_SOURCE, security_base_tree=STATE_BASE_TREE)
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
                and successor["parent_source_commit"] == (STATE_BASE_SOURCE if stateful else P0_SOURCE)
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


def require_release_lock():
    """Prove the inherited FD9 owns the canonical lock for the entire operation.

    Acquiring on an inherited open-file description retains ownership in the
    release shell. No descriptor is unlocked or closed here. An independently
    opened contender must be excluded; a filename or environment flag is not
    proof of mutual exclusion. Standalone qualification without this lease is
    deliberately not execution authority.
    """
    path = GLOBAL_RELEASE_LOCK
    require(path.is_absolute() and path.resolve(strict=True) == path, "v3_global_lock_path")
    for parent in path.parents:
        info = parent.lstat()
        allowed_sticky = parent == path.parent and bool(info.st_mode & stat.S_ISVTX)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                and (not info.st_mode & 0o022 or allowed_sticky), "v3_global_lock_parent")
    observed, held = path.lstat(), os.fstat(9)
    require(stat.S_ISREG(observed.st_mode) and observed.st_uid == 0 and observed.st_nlink == 1
            and not observed.st_mode & 0o022
            and (observed.st_dev, observed.st_ino) == (held.st_dev, held.st_ino), "v3_global_lock_identity")
    try:
        fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise BindingBlocked("v3_global_lock_not_owned") from None
    contender = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        current = os.fstat(contender)
        require((current.st_dev, current.st_ino) == (held.st_dev, held.st_ino), "v3_global_lock_identity")
        try:
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        raise BindingBlocked("v3_global_lock_not_exclusive")
    finally:
        os.close(contender)


def protected_durable_json(path):
    """Independent existing-byte reconciliation, never an approval writer.

    A visible completion/receipt cannot prove its writer's final fsync. Read,
    independently flush the same protected inode and directory, then reread.
    Failure remains fail-closed; this does not fill missing records or reissue
    decisions. The caller retains the global lock through authorization use.
    """
    value, fingerprint = protected_json(path)
    first = path.lstat()
    for item in (path, path.parent):
        fd = os.open(item, os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if item == path.parent else 0))
        try:
            info = os.fstat(fd)
            if item == path:
                require((info.st_dev, info.st_ino) == (first.st_dev, first.st_ino), "v3_durable_inode_changed")
            os.fsync(fd)
        finally:
            os.close(fd)
    again, after_hash = protected_json(path)
    after = path.lstat()
    require((first.st_dev, first.st_ino) == (after.st_dev, after.st_ino)
            and again == value and after_hash == fingerprint, "v3_durable_readback_changed")
    return value, fingerprint


def state_paths(paths, rid, action, suffix):
    require(isinstance(rid, str) and re.fullmatch(r"[A-Za-z0-9._-]+", rid)
            and rid not in {".", ".."}, "v3_state_release_id")
    require(action in {"seal-policy", "seal-release-records", "revoke-policy"}
            and suffix in {"intent", "complete", "reconciled"}, "v3_state_operation")
    return paths.parent / "release-state/v3-record-bootstrap" / (rid + "." + action + "." + suffix + ".json")


def reject_revocation(paths, rid):
    audit = state_paths(paths, rid, "revoke-policy", "intent").parent
    require(not any(audit.glob(rid + ".revocation-*"))
            and not any(audit.glob(rid + ".revoke-policy.*"))
            and not (audit / (rid + ".revoked.json")).exists()
            and not (audit / (rid + ".revoked.json")).is_symlink(), "v3_policy_revoking_or_revoked")


def state_qualification(paths, value, candidate, record_hashes, *, actions=("seal-policy", "seal-release-records")):
    """Only explicitly reconciled, exact existing records are consumable."""
    require_release_lock()
    rid = candidate["release_id"]
    reject_revocation(paths, rid)
    require(isinstance(actions, tuple) and actions and len(set(actions)) == len(actions)
            and set(actions) <= {"seal-policy", "seal-release-records"}, "v3_state_operation")
    needed = set()
    if "seal-policy" in actions:
        needed.add("policy")
    if "seal-release-records" in actions:
        needed.update(("deployment", "evidence"))
    require(needed <= set(record_hashes) <= {"policy", "deployment", "evidence"}, "v3_state_record_set")
    suffixes = {"policy": ".policy-approval.v1.json", "deployment": ".approval.v1.json",
                "evidence": ".evidence.v1.json"}
    technical_ids = set()
    authority_ids = set()
    for name in set(record_hashes) & {"policy", "deployment"}:
        record, observed = protected_json(paths / (rid + suffixes[name]))
        require(observed == record_hashes[name], "v3_state_authority_record_changed")
        authority_ids.add(record["audit_id"])
    for action in actions:
        require(action in {"seal-policy", "seal-release-records"}, "v3_state_operation")
        names = ("policy",) if action == "seal-policy" else ("deployment", "evidence")
        try:
            # Require the explicit reconciliation receipt BEFORE considering
            # publication bytes. Its own durability is independently verified.
            receipt, _ = protected_durable_json(state_paths(paths, rid, action, "reconciled"))
            intent, intent_hash = protected_durable_json(state_paths(paths, rid, action, "intent"))
            completion, completion_hash = protected_durable_json(state_paths(paths, rid, action, "complete"))
            expected_hashes = {name: record_hashes[name] for name in names}
            require(completion == {"state": "PREPARED", "action": action, "intent_sha256": intent_hash},
                    "v3_state_completion")
            require(intent.get("state_contract") == STATE_CONTRACT
                    and intent.get("action") == action and intent.get("candidate") == candidate
                    and intent.get("workflow") == "V3_ROOT_MANUAL_ATTESTATION_STATE_V2"
                    and intent.get("state") == "CONSUMED_FOR_RECORD_SEAL_ONLY"
                    and isinstance(intent.get("technical_grant_sha256"), str)
                    and HASH.fullmatch(intent["technical_grant_sha256"])
                    and isinstance(intent.get("input_sha256"), dict)
                    and all(intent["input_sha256"].get(name) == fingerprint for name, fingerprint in expected_hashes.items()),
                    "v3_state_intent")
            producer = intent.get("producer")
            require(isinstance(producer, dict) and set(producer) == {"source_commit", "source_tree", "code_pins"}
                    and producer["source_commit"] == candidate["source_commit"]
                    and producer["source_tree"] == candidate["source_tree"]
                    and isinstance(producer["code_pins"], dict)
                    and set(producer["code_pins"]) == set(STATE_PRODUCER_FILES)
                    and all(isinstance(pin, str) and HASH.fullmatch(pin) for pin in producer["code_pins"].values())
                    and all(producer["code_pins"].get(name) == pin
                            for name, pin in value["successor"]["tooling_pins"].items()
                            if name in producer["code_pins"]), "v3_state_producer_identity")
            audit_id = intent.get("technical_audit_id")
            require(isinstance(audit_id, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", audit_id)
                    and audit_id not in technical_ids | authority_ids, "v3_state_approval_id_reuse")
            technical_ids.add(audit_id)
            audit = state_paths(paths, rid, action, "intent").parent
            grant, grant_hash = protected_durable_json(audit / (audit_id + ".technical-decision.json"))
            require(set(grant) == {"schema_version", "workflow", "authority", "decision", "principal_id",
                "audit_id", "issued_at", "expires_at", "host", "operator", "action", "candidate",
                "binding_sha256", "producer", "input_sha256", "independent_decision_references",
                "production_switch_authorized", "business_database_write_authorized"}
                and grant_hash == intent["technical_grant_sha256"]
                and type(grant["schema_version"]) is int and grant["schema_version"] == 1
                and grant["workflow"] == intent["workflow"]
                and grant["authority"] == "PRODUCTION_ROOT_MAINTENANCE_AUTHORITY"
                and grant["decision"] == "APPROVED" and grant["audit_id"] == audit_id
                and all(isinstance(grant[name], str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", grant[name])
                        for name in ("principal_id", "operator", "host"))
                and all(grant[name] == intent.get(name) for name in (
                    "host", "operator", "action", "candidate", "producer", "input_sha256", "independent_decision_references"))
                and grant["binding_sha256"] == digest(value)
                and grant["production_switch_authorized"] is False
                and grant["business_database_write_authorized"] is False,
                "v3_state_technical_decision")
            require(type(intent.get("observed_at")) is int
                and type(grant["issued_at"]) is int and type(grant["expires_at"]) is int
                and 0 < grant["issued_at"] <= intent["observed_at"] < grant["expires_at"]
                and grant["expires_at"] - grant["issued_at"] <= 86400,
                "v3_state_historical_grant_window")
            references = grant["independent_decision_references"]
            require(isinstance(references, dict) and set(references) == set(intent["input_sha256"])
                and all(isinstance(reference, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", reference)
                        for reference in references.values()), "v3_state_independent_references")
            markers = [audit / (audit_id + ".json")]
            markers += [audit / ("decision-" + record_hashes[name] + ".json") for name in names if name != "evidence"]
            for marker in markers:
                marker_value, marker_hash = protected_durable_json(marker)
                require(marker_value == intent and marker_hash == intent_hash, "v3_state_audit_marker")
            require(receipt == {"schema_version": 1, "contract_id": STATE_CONTRACT,
                "scope": "DURABILITY_RECONCILIATION", "state": "VERIFIED_COMMITTED",
                "action": action, "candidate": candidate, "binding_sha256": digest(value),
                "intent_sha256": intent_hash, "completion_sha256": completion_hash,
                "record_sha256": expected_hashes}
                and type(receipt["schema_version"]) is int, "v3_state_reconciliation")
            for name in names:
                _, observed = protected_durable_json(paths / (rid + suffixes[name]))
                require(observed == record_hashes[name], "v3_state_authority_record_changed")
        except (OSError, KeyError, TypeError) as exc:
            raise BindingBlocked("v3_state_pending_or_commit_unknown") from exc
    reject_revocation(paths, rid)
    require_release_lock()
    return {"state_contract": STATE_CONTRACT, "state": "VERIFIED_COMMITTED"}


def qualification(base, root, manifest, schema_fingerprint, *, now=None):
    """No business mutation; V4 also independently flushes existing state bytes.

    Environment approval pins and live DB gates still apply. No authority record
    is created or changed by this consumer's durability reconciliation.
    """
    from scripts import rollback_preflight as gate
    value = declaration(root, manifest)
    gate.require(root.parent.parent == base / "releases" and base.resolve() == base,
                 "qualification_controlled_release")
    paths = base / "shared/release-qualifications"
    if is_stateful(manifest):
        require_release_lock()
        reject_revocation(paths, manifest["release_id"])
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
    if value.get("schema_version") == 4:
        require_release_lock()
        reject_revocation(paths, candidate["release_id"])
    result = _p0_decisions(paths, value, candidate, approved, evidence, evidence_hash, schema_fingerprint, now=now)
    if value.get("schema_version") == 4:
        _, approved_hash = protected_json(paths / (candidate["release_id"] + ".approval.v1.json"))
        result.update(state_qualification(paths, value, candidate, {
            "policy": result["policy_approval_sha256"], "deployment": approved_hash, "evidence": evidence_hash}))
    return result


def _p0_decisions(paths, value, candidate, approved, evidence, evidence_hash, schema_fingerprint, *, now):
    """Consume three independently authorized Root-protected records; never issue one.

    Labels/hashes do not prove a test ran. Authorized operations must verify the
    actual source-bound reports before installing these records. Fake-reader
    component tests prove only this consumer, not PG, Root or release readiness.
    """
    policy, policy_hash = protected_json(paths / (candidate["release_id"] + ".policy-approval.v1.json"))
    instant = time.time() if now is None else now
    common = {"schema_version", "scope", "issuer_uid", "authority", "principal_id", "audit_id",
              "decision", "issued_at", "expires_at", "binding_sha256", "candidate", "predecessor"}
    stateful = value.get("schema_version") == 4
    if stateful:
        common.add("state_contract")
    require(set(policy) == common | {"replacements", "accepted_risks", "test_environment_id",
                                    "production_environment_id"}, "p0_policy_approval_fields")
    require(set(approved) == common | {"policy_approval_sha256", "policy_audit_id", "evidence_sha256"},
            "p0_deployment_approval_fields")
    for record, scope, authority in (
        (policy, "P0_EXACT_PAIR_POLICY_CHANGE", "PROJECT_OWNER_POLICY_AUTHORITY"),
        (approved, "P0_EXACT_PAIR_PRODUCTION_DEPLOYMENT", "PRODUCTION_RELEASE_AUTHORITY"),
    ):
        require(type(record["schema_version"]) is int and record["schema_version"] == (2 if stateful else 1)
                and (not stateful or record["state_contract"] == STATE_CONTRACT)
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
    if stateful:
        expected.update(schema_version=2, state_contract=STATE_CONTRACT)
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
            expected_postgres = "16.14" if name in P0_PRODUCTION_CHECKS else "16.6"
            require(facts.get("postgres_version") == expected_postgres and facts.get("schema") == "015"
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
            "qualification_profile": STATE_CONTRACT if stateful else P0_CONTRACT, "native_acceptance_claim": False}


def prepare(repo, source, *, controlled=False, controlled_state=False):
    """Offline source declaration only. No protected qualification is created."""
    require(isinstance(source, str) and COMMIT.fullmatch(source), "prepare_exact_source_required")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args])
    require(git("rev-parse", source + "^{commit}").decode().strip() == source, "prepare_source_identity")
    subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", PREDECESSOR["source_commit"], source],
                   check=True, capture_output=True)
    parents = git("show", "-s", "--format=%P", source).decode().split()
    require(len(parents) == 1, "prepare_single_parent_required")
    if controlled_state:
        require(parents == [STATE_BASE_SOURCE]
                and git("rev-parse", STATE_BASE_SOURCE + "^{tree}").decode().strip() == STATE_BASE_TREE,
                "prepare_state_direct_parent_required")
        changed = git("diff-tree", "--no-commit-id", "--name-only", "-r", source).decode().splitlines()
        require(all(p.startswith("enterprise_agent_poc/") and p.removeprefix("enterprise_agent_poc/") in STATE_CHANGE_FILES
                    for p in changed), "prepare_state_business_and_schema_immutable")
    elif controlled:
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
    if controlled or controlled_state:
        value.update(schema_version=3, contract_id=P0_CONTRACT, recovery_contract=P0_CONTRACT,
                     qualification_profile=P0_CONTRACT, business_base_source=P0_SOURCE, business_base_tree=P0_TREE,
                     maximum_approval_lifetime_seconds=86400)
    if controlled_state:
        value.update(schema_version=4, contract_id=STATE_CONTRACT, recovery_contract=STATE_CONTRACT,
                     qualification_profile=STATE_CONTRACT, state_contract=STATE_CONTRACT,
                     security_base_source=STATE_BASE_SOURCE, security_base_tree=STATE_BASE_TREE)
    return validate(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare",))
    parser.add_argument("--successor-source", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--qualification-mode", choices=("native-v2", "p0-controlled-v3", "p0-controlled-v3-state-v1"), default="native-v2")
    args = parser.parse_args(argv)
    try:
        value = prepare(Path(__file__).resolve().parents[2], args.successor_source,
                        controlled=args.qualification_mode == "p0-controlled-v3",
                        controlled_state=args.qualification_mode == "p0-controlled-v3-state-v1")
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
