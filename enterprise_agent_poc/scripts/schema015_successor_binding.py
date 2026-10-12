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


def prepare(repo, source):
    """Offline source declaration only. No protected qualification is created."""
    require(isinstance(source, str) and COMMIT.fullmatch(source), "prepare_exact_source_required")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args])
    require(git("rev-parse", source + "^{commit}").decode().strip() == source, "prepare_source_identity")
    subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", PREDECESSOR["source_commit"], source],
                   check=True, capture_output=True)
    parents = git("show", "-s", "--format=%P", source).decode().split()
    require(len(parents) == 1, "prepare_single_parent_required")
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
    return validate(value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare",))
    parser.add_argument("--successor-source", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        value = prepare(Path(__file__).resolve().parents[2], args.successor_source)
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
