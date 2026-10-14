"""Candidate bounded ordinary-release controller. NOT installed/enabled in Production.

No issuer, general shell API, migrator, secret export or candidate bootstrap.
The existing V2/V3 consumers are unchanged. Adoption needs separate policy and
bounded machine-install approval; every version needs one independently read
Owner event. Terminal receipts cannot be promoted from fixture to Production.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import time
import uuid

CONTRACT = "ORDINARY_RELEASE_MACHINE_EXECUTION_V1"
BASE = Path("/opt/enterprise-agent-workbench")
CONTROL = BASE / "shared/lightweight-release-v1"
LOCK = Path("/run/lock/enterprise-agent-workbench-release.lock")
HOST = "iv-yekqs6ez9cnu262hhsg0"
HASH = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
RID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}")
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
EVIDENCE = (
    "candidate_regression", "source_installation", "pg_transactions",
    "pg_concurrency", "pg_permissions", "task_run_ownership",
    "orphan_task_pagination", "tenant_user_isolation", "api_sse_history",
    "runtime_startup_health", "production_schema015", "production_backup_restore",
    "artifact_backup_integrity", "exact_predecessor_compatibility",
    "pre_commit_failure_recovery", "post_commit_runtime_recovery",
    "business_data_preserved", "release_lock_audit",
)
# Reuse is a proposed NEW contract, never a V3 PASS. Report origin is retained.
REUSABLE = {"pg_transactions", "pg_concurrency", "pg_permissions",
            "task_run_ownership", "orphan_task_pagination",
            "exact_predecessor_compatibility", "pre_commit_failure_recovery",
            "post_commit_runtime_recovery"}
TERMINAL = {"COMMITTED", "RESTORED", "FAILED_BEFORE_SWITCH", "RECOVERY_BLOCKED"}


class Blocked(RuntimeError):
    pass


def require(value, check):
    if not value:
        raise Blocked(check)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def identity(value):
    require(set(value) == {"release_id", "source", "tree", "archive_sha256",
                           "raw_manifest_sha256", "canonical_manifest_sha256"}, "identity_fields")
    require(isinstance(value["release_id"], str) and RID.fullmatch(value["release_id"])
            and value["release_id"] not in {".", ".."}, "release_id")
    for key in ("source", "tree"):
        require(isinstance(value[key], str) and COMMIT.fullmatch(value[key]), "git_identity")
    for key in ("archive_sha256", "raw_manifest_sha256", "canonical_manifest_sha256"):
        require(isinstance(value[key], str) and HASH.fullmatch(value[key]), "artifact_identity")


def operation(value):
    require(isinstance(value, str), "operation_id")
    try:
        require(str(uuid.UUID(value)) == value, "operation_id")
    except ValueError:
        raise Blocked("operation_id") from None
    return value


def member_path(value):
    path = PurePosixPath(value)
    require(isinstance(value, str) and value == path.as_posix() and not path.is_absolute()
            and path.parts and path.parts[0] == "enterprise_agent_poc"
            and not any(p in {".", ".."} for p in path.parts)
            and "\\" not in value and "\x00" not in value
            and len(value) <= 240 and len(path.parts) <= 12, "archive_member_path")
    return path


def inspect_package(archive_bytes, raw_manifest, expected):
    """Complete member/byte/mode checks before extraction, not extractall()."""
    identity(expected)
    require(sha(archive_bytes) == expected["archive_sha256"], "archive_hash")
    require(sha(raw_manifest) == expected["raw_manifest_sha256"], "raw_manifest_hash")
    manifest = json.loads(raw_manifest)
    require(sha(canonical(manifest)) == expected["canonical_manifest_sha256"], "canonical_hash")
    require(manifest["release_id"] == expected["release_id"]
            and manifest["source_commit"] == expected["source"]
            and manifest["archive_sha256"] == expected["archive_sha256"], "manifest_identity")
    forbidden = {"forward_migrations", "binding_transition", "skill_package_staging",
                 "deferred_skill", "agent_productization_transition"}
    require(not forbidden.intersection(manifest), "high_risk_manifest")
    selected = manifest["selected_files"]
    require(isinstance(selected, list) and len(selected) == len(set(selected))
            and len(selected) == manifest["selected_file_count"], "file_inventory")
    files, directories, unpacked = {}, set(), 0
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        for info in tar:
            name = info.name.rstrip("/") if info.isdir() else info.name
            path = member_path(name)
            require(name not in files and name not in directories, "duplicate_member")
            require(not info.pax_headers or set(info.pax_headers) <= {"comment"}, "pax_metadata")
            if info.isdir():
                directories.add(name)
                continue
            require(info.isfile() and info.mode in {0o644, 0o755}
                    and 0 <= info.size <= 32 * 1024 * 1024, "member_type_mode_size")
            # Enforce before allocating/decompressing, not after a gzip bomb.
            require(unpacked + info.size <= MAX_UNPACKED_BYTES and len(files) < 512, "unpacked_limit")
            unpacked += info.size
            handle = tar.extractfile(info)
            require(handle is not None, "member_read")
            data = handle.read()
            require(len(data) == info.size, "member_size")
            files[name] = {"sha256": sha(data), "mode": info.mode, "data": data}
            for parent in path.parents:
                if str(parent) != ".":
                    directories.add(str(parent))
    require(set(files) == set(selected) and len(files) <= 512, "members_match_manifest")
    require(unpacked <= MAX_UNPACKED_BYTES, "unpacked_limit")
    return manifest, files, directories


def ordinary_diff(before, after, declared):
    """Conservative first fast lane. No 'no migration => ordinary' inference."""
    changed = sorted(k for k in set(before) | set(after)
                     if k not in before or k not in after
                     or (before[k]["sha256"], before[k]["mode"])
                     != (after[k]["sha256"], after[k]["mode"]))
    require(changed == declared and bool(changed), "declared_diff_exact")
    require(all(k.startswith("enterprise_agent_poc/app/static/") for k in changed),
            "high_risk_or_unclassified_change")
    require(all(k in after and after[k]["mode"] == 0o644 for k in changed), "static_file_mode")
    return changed


def verify_plan(plan, grant, now):
    require(plan["contract"] == grant["contract"] == CONTRACT, "contract")
    operation(plan["operation_id"])
    require(plan["host"] == grant["host"] == HOST, "host")
    require(plan["class"] == "STATIC_UI_COMPATIBLE" and plan["migration_action"] == "NONE"
            and plan["schema"] == "015" and plan["automatic_retry"] is False, "ordinary_scope")
    require(plan["provider_task_budget"] == 0 and plan["database_write_authorized"] is False,
            "business_side_effect_budget")
    identity(plan["candidate"])
    identity(plan["predecessor"])
    require(plan["candidate"]["source"] != plan["predecessor"]["source"]
            and plan["candidate"]["release_id"] != plan["predecessor"]["release_id"], "distinct_candidate")
    require(plan["tooling"] == grant["tooling"], "fixed_tooling_pin")
    require(grant["adoption_status"] == "APPROVED_AND_INSTALLED"
            and grant["revoked"] is False, "machine_adoption_required")
    require(grant["mode"] in {"PILOT", "ENABLED"}, "machine_mode")
    require(grant["issued_at"] <= now < grant["expires_at"]
            and grant["expires_at"] - grant["issued_at"] <= 30 * 86400, "bounded_machine_authority")
    if grant["mode"] == "PILOT":
        require(sha(canonical(plan)) in grant["pilot_plan_hashes"], "pilot_exact_plan")
    else:
        cycles = grant["acceptance_cycles"]
        require(len(cycles) == 3 and len({r["operation_id"] for r in cycles}) == 3
                and len({r["source"] for r in cycles}) == 3
                and all(r["environment"] == "PRODUCTION" and r["status"] == "COMMITTED"
                        and r["owner_confirmations"] == 1 and r["manual_deployment_steps"] == 0
                        and HASH.fullmatch(r["receipt_sha256"]) for r in cycles)
                and any(r["exact_restore_rehearsal"] is True for r in cycles), "three_real_cycles_required")
    times = plan["windows"]
    require(times["not_before"] <= now and now < times["publish_until"]
            < times["recover_until"] <= grant["expires_at"], "publication_window")
    require(times["publish_until"] - times["not_before"] <= 900
            and times["recover_until"] - times["publish_until"] >= 600
            and 0 < plan["timeout_seconds"] <= 900, "recovery_time_reserve")
    require(plan["recovery_authorized"] is True, "separate_exact_recovery_scope")


def verify_evidence(plan, reports, now):
    """Preserve source attribution, raw bytes and applicability, never rewrite PASS."""
    require(set(plan["evidence"]) == set(EVIDENCE), "18_evidence_required")
    for name in EVIDENCE:
        item = plan["evidence"][name]
        raw = reports[item["sha256"]]
        require(sha(raw) == item["sha256"], "evidence_hash")
        report = json.loads(raw)
        require(report["check"] == name and report["status"] == "PASS"
                and report["source"] == item["origin_source"]
                and report["created_at"] <= now <= item["valid_until"], "evidence_identity_time")
        require(report["environment"] not in {"FIXTURE", "STUB", "SQLITE"}, "real_evidence_required")
        if item["mode"] == "CURRENT":
            require(report["source"] == plan["candidate"]["source"], "current_source_evidence")
        else:
            require(item["mode"] == "REUSE" and name in REUSABLE
                    and COMMIT.fullmatch(item["origin_source"]), "reuse_scope")
            require(item["applicability"] == {
                "unchanged_nonstatic_bytes": True, "dependency_change": False,
                "schema_change": False, "exact_predecessor": plan["predecessor"]}, "reuse_applicability")
        require(item["valid_until"] - report["created_at"] <= 7 * 86400, "evidence_max_age")


def owner_body(plan):
    return f"APPROVE {CONTRACT}\nPLAN_SHA256={sha(canonical(plan))}\nOPERATION_ID={plan['operation_id']}"


def verify_owner(plan, grant, event, now, *, recovery=False):
    require(event["user"]["id"] == grant["owner_github_id"]
            and event["user"]["login"] == grant["owner_github_login"], "real_owner_identity")
    require(event["body"] == owner_body(plan) and type(event["id"]) is int and event["id"] > 0
            and event["issue_url"] == grant["owner_issue_url"], "owner_exact_plan")
    created = int(dt.datetime.fromisoformat(event["created_at"].replace("Z", "+00:00")).timestamp())
    require(event["updated_at"] == event["created_at"] and plan["windows"]["not_before"] <= created <= now,
            "owner_event_not_edited_or_future")
    require(created < plan["windows"]["publish_until"], "owner_event_publish_scope")
    require(now < plan["windows"]["recover_until" if recovery else "publish_until"], "owner_window")


def trusted(path, *, directory=False):
    """Check full ancestors, no symlinks, Root uid/gid, no writable group/world."""
    path = Path(path).absolute()
    for parent in reversed((path, *path.parents)):
        s = parent.lstat()
        require(not stat.S_ISLNK(s.st_mode) and s.st_uid == s.st_gid == 0
                and not (s.st_mode & 0o022), "root_trusted_path")
        if parent != path or directory:
            require(stat.S_ISDIR(s.st_mode), "trusted_directory")
    if not directory:
        require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, "regular_single_link")
    return path


def read_nofollow(path, limit=128 * 1024 * 1024):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_size <= limit, "input_regular_size")
        return f.read(limit + 1)


def durable_new(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if os.name == "posix":
        fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class Journal:
    """Exclusive append-only chain; never overwrite UNKNOWN or create another op."""
    def __init__(self, root):
        self.root = Path(root)

    def read(self):
        rows, previous = [], "0" * 64
        for n, path in enumerate(sorted(self.root.glob("[0-9]*.json"))):
            require(path.name == f"{n:04}.json", "journal_sequence")
            raw = read_nofollow(path, 1024 * 1024)
            row = json.loads(raw)
            require(row["previous_sha256"] == previous, "journal_hash_chain")
            previous = sha(raw)
            rows.append(row)
        return rows

    def add(self, phase, **facts):
        rows = self.read()
        previous = sha(read_nofollow(self.root / f"{len(rows)-1:04}.json")) if rows else "0" * 64
        row = {"phase": phase, "at": int(time.time()), "previous_sha256": previous, **facts}
        durable_new(self.root / f"{len(rows):04}.json", canonical(row))
        return row


class Controller:
    """Adapter owns trusted tools; every side effect is surrounded by durable intent."""
    def __init__(self, adapter):
        self.a = adapter

    def execute(self, plan, grant, journal):
        verify_plan(plan, grant, self.a.now())
        require(not journal.read(), "attempt_consumed_query_only")
        self.a.authorize(plan, grant)
        journal.add("ATTEMPT_STARTED", plan_sha256=sha(canonical(plan)), operation_id=plan["operation_id"])
        switched = False
        try:
            self.a.install(plan)
            journal.add("INSTALLED")
            with self.a.lock():
                journal.add("LOCKED")
                self.a.authorize(plan, grant)  # fresh under the same global FD9 lock
                self.a.preflight(plan)
                snapshot = self.a.capture(plan)
                journal.add("SNAPSHOT", snapshot=snapshot)
                self.a.authorize(plan, grant)
                require(self.a.now() + plan["timeout_seconds"] < plan["windows"]["recover_until"],
                        "remaining_recovery_reserve")
                journal.add("ACTIVATING")
                # An exception can happen after a partial drop-in/pointer mutation.
                switched = True
                try:
                    self.a.activate(plan["candidate"])
                    journal.add("ACTIVATED")
                    self.a.health(plan["candidate"])
                    self.a.data_preserved(plan, snapshot)
                    self.a.authorize(plan, grant)
                    journal.add("COMMITTED", source=plan["candidate"]["source"],
                                predecessor=plan["predecessor"], health="PASS")
                except BaseException as exc:
                    journal.add("FAILURE_AFTER_ACTIVATION", reason=type(exc).__name__)
                    self._recover_locked(plan, grant, journal, snapshot)
                    raise
        except BaseException as exc:
            if not switched:
                journal.add("FAILED_BEFORE_SWITCH", reason=type(exc).__name__)
            raise

    def _recover_locked(self, plan, grant, journal, snapshot):
        try:
            self.a.authorize(plan, grant, recovery=True)
            self.a.recovery_preflight(plan, snapshot)
            journal.add("RESTORING", target=plan["predecessor"])
            self.a.activate(plan["predecessor"])
            self.a.health(plan["predecessor"])
            self.a.data_preserved(plan, snapshot)
            journal.add("RESTORED", source=plan["predecessor"]["source"], health="PASS")
        except BaseException as exc:
            journal.add("RECOVERY_BLOCKED", reason=type(exc).__name__)
            raise

    def recover(self, plan, grant, journal):
        rows = journal.read()
        require(rows and rows[0]["plan_sha256"] == sha(canonical(plan)), "recovery_plan_binding")
        require(rows[-1]["phase"] in {"ACTIVATING", "ACTIVATED", "FAILURE_AFTER_ACTIVATION", "COMMITTED"},
                "unknown_or_recovery_attempt_consumed")
        snapshot = next(r["snapshot"] for r in rows if r["phase"] == "SNAPSHOT")
        with self.a.lock():
            self._recover_locked(plan, grant, journal, snapshot)

    def status(self, journal):
        rows = journal.read()
        if not rows:
            return {"status": "NOT_STARTED"}
        phase = rows[-1]["phase"]
        return {"status": phase if phase in TERMINAL else "UNKNOWN_INCOMPLETE_QUERY_ONLY",
                "last_phase": phase, "events": len(rows)}
