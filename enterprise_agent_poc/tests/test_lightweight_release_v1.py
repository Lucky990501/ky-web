"""Isolated execution tests only. No Production/native/evidence qualification."""
from __future__ import annotations

import copy
import datetime as dt
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import threading
import time
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import lightweight_release_v1 as m
import lightweight_release_client_v1 as client

NOW = 1791960000


def ident(letter, release):
    return {"release_id": release, "source": letter * 40, "tree": letter * 40,
            "archive_sha256": letter * 64, "raw_manifest_sha256": letter * 64,
            "canonical_manifest_sha256": letter * 64}


def plan_grant():
    plan = {"contract": m.CONTRACT, "host": m.HOST, "operation_id": str(uuid.uuid4()),
            "class": "STATIC_UI_COMPATIBLE", "schema": "015", "migration_action": "NONE",
            "automatic_retry": False, "provider_task_budget": 0, "database_write_authorized": False,
            "candidate": ident("b", "new-v1"), "predecessor": ident("a", "old-v1"),
            "tooling": {"source": "c" * 40, "files": {"pinned.py": "c" * 64}},
            "windows": {"not_before": NOW - 10, "publish_until": NOW + 600, "recover_until": NOW + 1800},
            "timeout_seconds": 600, "recovery_authorized": True, "evidence": {},
            "changed_files": ["enterprise_agent_poc/app/static/workbench.js"]}
    grant = {"contract": m.CONTRACT, "host": m.HOST, "tooling": plan["tooling"],
             "adoption_status": "APPROVED_AND_INSTALLED", "revoked": False,
             "issued_at": NOW - 20, "expires_at": NOW + 86400, "mode": "PILOT",
             "pilot_plan_hashes": [m.sha(m.canonical(plan))], "owner_github_id": 123,
             "owner_github_login": "fixture-owner", "owner_issue_url": "https://api.github.com/repos/Lucky990501/ky-web/issues/123"}
    return plan, grant


def event(plan, grant):
    timestamp = dt.datetime.fromtimestamp(NOW, dt.timezone.utc).isoformat()
    return {"id": 1234, "user": {"id": grant["owner_github_id"], "login": grant["owner_github_login"]},
            "body": m.owner_body(plan), "issue_url": grant["owner_issue_url"],
            "created_at": timestamp, "updated_at": timestamp}


def package(files=None, mode=0o644, link=False):
    files = files or {"enterprise_agent_poc/app/static/workbench.js": b"safe content"}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            if link:
                info.type, info.linkname, info.size = tarfile.SYMTYPE, "/etc/passwd", 0
            tar.addfile(info, io.BytesIO(data) if not link else None)
    archive = stream.getvalue()
    manifest = {"release_id": "new-v1", "source_commit": "b" * 40,
                "archive_sha256": m.sha(archive), "selected_files": list(files),
                "selected_file_count": len(files), "build_platform": "isolated-test"}
    raw = m.canonical(manifest)
    identity = ident("b", "new-v1")
    identity.update(archive_sha256=m.sha(archive), raw_manifest_sha256=m.sha(raw),
                    canonical_manifest_sha256=m.sha(m.canonical(manifest)))
    return archive, raw, identity


class Adapter:
    """Real temporary files, symlink and process; NOT real systemd/DB."""
    def __init__(self, root, failure=None):
        self.root, self.failure, self.calls = root, failure, []
        self.clock = NOW
        self.active = "old-v1"
        self.guard = threading.Lock()
        self.owner_changed = False

    def now(self):
        return self.clock

    def authorize(self, plan, grant, recovery=False):
        self.calls.append("recovery_authority" if recovery else "authority")
        if recovery:
            m.require(self.clock < plan["windows"]["recover_until"] and self.failure != "recovery_revoked", "recovery_expired")
        else:
            m.verify_plan(plan, grant, self.clock)
            m.require(not self.owner_changed, "owner_revoked")
        if self.failure == "missing_authority":
            raise m.Blocked("no_authority")

    def install(self, plan):
        self.calls.append("install")
        if self.failure == "install":
            raise OSError()
        m.durable_new(self.root / "candidate.bytes", b"fixture")

    def lock(self):
        adapter = self
        class Lock:
            def __enter__(self):
                m.require(adapter.guard.acquire(blocking=False), "concurrent_release")
            def __exit__(self, *_):
                adapter.guard.release()
        return Lock()

    def preflight(self, plan):
        self.calls.append("preflight")
        if self.failure in {"tasks", "backup", "schema", "predecessor"}:
            raise m.Blocked(self.failure)
        m.require(self.active == plan["predecessor"]["release_id"], "live_predecessor_drift")

    def capture(self, plan):
        self.calls.append("capture")
        return {"predecessor": plan["predecessor"], "data": "unchanged"}

    def activate(self, release):
        self.calls.append(f"activate:{release['release_id']}")
        self.active = release["release_id"]
        path = self.root / "actual-pointer.txt"
        path.write_text(self.active)
        if self.failure == "partial_switch" and self.active == "new-v1":
            raise OSError("partial side effect")
        if self.failure == "kill" and self.active == "new-v1":
            raise KeyboardInterrupt()

    def health(self, release):
        self.calls.append(f"health:{release['release_id']}")
        # Execute an actual child, not a static 'PASS' constant.
        code = "import sys; sys.exit(1)" if self.failure == "health" and release["release_id"] == "new-v1" else "import sys; sys.exit(0)"
        subprocess.run([sys.executable, "-c", code], check=True, timeout=5)
        if self.failure == "publish_expired" and release["release_id"] == "new-v1":
            self.clock += 700
        if self.failure == "recovery_expired" and release["release_id"] == "new-v1":
            self.clock += 2000
            raise m.Blocked("health")

    def data_preserved(self, plan, snapshot):
        self.calls.append("data_guard")
        m.require(snapshot["data"] == "unchanged", "data")

    def recovery_preflight(self, plan, snapshot):
        self.calls.append("exact_recovery_guard")
        m.require(self.failure != "unsafe_recovery" and snapshot["predecessor"] == plan["predecessor"], "unsafe_recovery")


def execution(tmp_path, failure=None):
    root = tmp_path / "job"
    root.mkdir()
    log = root / "journal"
    log.mkdir()
    a = Adapter(root, failure)
    return a, m.Controller(a), m.Journal(log), *plan_grant()


def test_complete_real_child_and_durable_chain(tmp_path):
    a, c, j, plan, grant = execution(tmp_path)
    c.execute(plan, grant, j)
    assert c.status(j)["status"] == "COMMITTED"
    assert a.active == "new-v1" and j.read()[-1]["health"] == "PASS"
    assert a.calls.count("activate:new-v1") == 1


@pytest.mark.parametrize("failure", ["missing_authority", "install", "tasks", "backup", "schema", "predecessor"])
def test_precommit_failure_no_pointer_mutation(tmp_path, failure):
    a, c, j, plan, grant = execution(tmp_path, failure)
    with pytest.raises(Exception):
        c.execute(plan, grant, j)
    assert a.active == "old-v1" and not any(call.startswith("activate:") for call in a.calls)


@pytest.mark.parametrize("failure", ["health", "partial_switch", "kill", "publish_expired"])
def test_failure_or_interrupt_restores_exact_once(tmp_path, failure):
    a, c, j, plan, grant = execution(tmp_path, failure)
    with pytest.raises(BaseException):
        c.execute(plan, grant, j)
    assert a.active == "old-v1" and c.status(j)["status"] == "RESTORED"
    assert a.calls.count("activate:new-v1") == a.calls.count("activate:old-v1") == 1


@pytest.mark.parametrize("failure", ["recovery_expired", "unsafe_recovery", "recovery_revoked"])
def test_no_emergency_expiry_or_unsafe_recovery_bypass(tmp_path, failure):
    a, c, j, plan, grant = execution(tmp_path, failure)
    if failure != "recovery_expired":
        original = a.health
        a.health = lambda release: (_ for _ in ()).throw(m.Blocked("health")) if release["release_id"] == "new-v1" else original(release)
    with pytest.raises(BaseException):
        c.execute(plan, grant, j)
    assert c.status(j)["status"] == "RECOVERY_BLOCKED"
    assert "activate:old-v1" not in a.calls


def test_unknown_does_not_restart_or_create_new_operation(tmp_path):
    a, c, j, plan, grant = execution(tmp_path)
    j.add("ATTEMPT_STARTED", plan_sha256=m.sha(m.canonical(plan)))
    j.add("SNAPSHOT", snapshot={"predecessor": plan["predecessor"], "data": "unchanged"})
    j.add("ACTIVATING")
    a.active = "new-v1"
    assert c.status(j)["status"] == "UNKNOWN_INCOMPLETE_QUERY_ONLY"
    with pytest.raises(m.Blocked, match="attempt_consumed"):
        c.execute(plan, grant, j)
    c.recover(plan, grant, j)
    assert c.status(j)["status"] == "RESTORED" and "activate:new-v1" not in a.calls
    with pytest.raises(m.Blocked):
        c.recover(plan, grant, j)


def test_parallel_second_operation_cannot_activate(tmp_path):
    a, c, j, plan, grant = execution(tmp_path)
    a.guard.acquire()
    with pytest.raises(m.Blocked, match="concurrent"):
        c.execute(plan, grant, j)
    a.guard.release()
    assert a.active == "old-v1" and c.status(j)["status"] == "FAILED_BEFORE_SWITCH"


@pytest.mark.parametrize("field,value", [("host", "other-host"), ("migration_action", "APPLY"),
    ("automatic_retry", True), ("provider_task_budget", 1), ("database_write_authorized", True),
    ("class", "AUTH_CHANGE"), ("schema", "016"), ("recovery_authorized", False)])
def test_plan_scope_rejected(field, value):
    plan, grant = plan_grant()
    plan[field] = value
    with pytest.raises(m.Blocked):
        m.verify_plan(plan, grant, NOW)


@pytest.mark.parametrize("field,value", [("revoked", True), ("adoption_status", "CANDIDATE_READY"),
    ("expires_at", NOW), ("mode", "UNLIMITED_ROOT"), ("issued_at", NOW+1)])
def test_machine_authority_rejected(field, value):
    plan, grant = plan_grant()
    grant[field] = value
    with pytest.raises(m.Blocked):
        m.verify_plan(plan, grant, NOW)


def test_no_enablement_from_fixture_cycles():
    plan, grant = plan_grant()
    grant["mode"], grant["acceptance_cycles"] = "ENABLED", []
    with pytest.raises(m.Blocked, match="three_real_cycles"):
        m.verify_plan(plan, grant, NOW)


@pytest.mark.parametrize("path,value", [("body", "APPROVE"), ("updated_at", "2026-10-15T00:00:00Z"),
    ("issue_url", "https://api.github.com/repos/other/repo/issues/1"), ("id", 0)])
def test_owner_confirmation_cannot_be_simulated_by_label(path, value):
    plan, grant = plan_grant()
    e = event(plan, grant)
    e[path] = value
    with pytest.raises(m.Blocked):
        m.verify_owner(plan, grant, e, NOW)


def test_real_owner_exact_plan_and_recovery_window_separate():
    plan, grant = plan_grant()
    e = event(plan, grant)
    m.verify_owner(plan, grant, e, NOW)
    with pytest.raises(m.Blocked):
        m.verify_owner(plan, grant, e, NOW+700)
    m.verify_owner(plan, grant, e, NOW+700, recovery=True)
    e["user"]["id"] = 999
    with pytest.raises(m.Blocked):
        m.verify_owner(plan, grant, e, NOW)


def evidence(plan, mode="CURRENT"):
    reports = {}
    for name in m.EVIDENCE:
        reuse = mode == "REUSE" and name in m.REUSABLE
        origin = plan["predecessor"]["source"] if reuse else plan["candidate"]["source"]
        raw = m.canonical({"check": name, "status": "PASS", "source": origin,
                           "created_at": NOW-10, "environment": "REAL_TEST_PG"})
        digest = m.sha(raw)
        reports[digest] = raw
        plan["evidence"][name] = {"sha256": digest, "origin_source": origin,
                                  "mode": "REUSE" if reuse else "CURRENT", "valid_until": NOW+1000,
                                  "applicability": {"unchanged_nonstatic_bytes": True,
                                     "dependency_change": False, "schema_change": False,
                                     "exact_predecessor": plan["predecessor"]}}
    return reports


def test_all18_and_origin_preserved():
    plan, _ = plan_grant()
    reports = evidence(plan, "REUSE")
    m.verify_evidence(plan, reports, NOW)
    assert plan["evidence"]["pg_transactions"]["origin_source"] == plan["predecessor"]["source"]


@pytest.mark.parametrize("error", ["hash", "fixture", "expired", "wrong_source", "missing", "unsafe_reuse"])
def test_evidence_no_fake_pass(error):
    plan, _ = plan_grant()
    reports = evidence(plan)
    name = "candidate_regression"
    item = plan["evidence"][name]
    if error == "missing":
        del plan["evidence"][name]
    elif error == "unsafe_reuse":
        item["mode"] = "REUSE"
    elif error == "expired":
        item["valid_until"] = NOW-1
    elif error == "hash":
        reports[item["sha256"]] = b"altered"
    else:
        r = json.loads(reports[item["sha256"]])
        r["environment" if error == "fixture" else "source"] = "FIXTURE" if error == "fixture" else "a"*40
        raw = m.canonical(r)
        item["sha256"] = m.sha(raw)
        reports[item["sha256"]] = raw
    with pytest.raises(m.Blocked):
        m.verify_evidence(plan, reports, NOW)


def test_tar_all_bytes_and_modes():
    archive, raw, identity = package()
    manifest, files, dirs = m.inspect_package(archive, raw, identity)
    assert len(files) == 1 and files[next(iter(files))]["data"] == b"safe content"
    assert "enterprise_agent_poc/app/static" in dirs


@pytest.mark.parametrize("name", ["../escape", "/etc/passwd", "enterprise_agent_poc/../escape",
    "enterprise_agent_poc\\escape", "enterprise_agent_poc/./escape"])
def test_archive_escape_rejected(name):
    a, r, i = package({name: b"x"})
    with pytest.raises(m.Blocked):
        m.inspect_package(a, r, i)


@pytest.mark.parametrize("bad", ["link", "exec_suid", "archive", "manifest", "canonical"])
def test_package_drift_rejected(bad):
    a, r, i = package(link=bad == "link", mode=0o4755 if bad == "exec_suid" else 0o644)
    if bad in {"archive", "manifest", "canonical"}:
        i[{"archive": "archive_sha256", "manifest": "raw_manifest_sha256",
           "canonical": "canonical_manifest_sha256"}[bad]] = "f"*64
    with pytest.raises(m.Blocked):
        m.inspect_package(a, r, i)


def test_nonstatic_or_wrong_diff_never_ordinary():
    _, _, _ = package()
    before = {"enterprise_agent_poc/app/main.py": {"sha256": "a", "mode": 0o644}}
    after = {"enterprise_agent_poc/app/main.py": {"sha256": "b", "mode": 0o644}}
    with pytest.raises(m.Blocked, match="high_risk"):
        m.ordinary_diff(before, after, list(before))
    with pytest.raises(m.Blocked, match="declared_diff"):
        m.ordinary_diff(before, after, [])


def test_append_only_journal_and_hash_tampering(tmp_path):
    j = m.Journal(tmp_path)
    j.add("A")
    j.add("B")
    with pytest.raises(FileExistsError):
        m.durable_new(tmp_path / "0000.json", b"overwrite")
    (tmp_path / "0000.json").write_text('{}')
    with pytest.raises((m.Blocked, KeyError)):
        j.read()


@pytest.mark.parametrize("rc,stdout", [(255, b""), (0, b"not json"), (0, b'{"status":"BLOCKED"}'),
                                      (0, b'{"status":"fake","operation_id":"wrong"}')])
def test_ssh_failure_no_printed_success_or_retry(monkeypatch, rc, stdout):
    calls = []
    def invoke(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, rc, stdout, b"")
    monkeypatch.setattr(client.subprocess, "run", invoke)
    op = str(uuid.uuid4())
    result = client.call_ssh("approved-host", "submit", b"{}", op)
    assert "QUERY" in result["status"] and len(calls) == 1
    assert "StrictHostKeyChecking=yes" in calls[0]
    assert calls[0][-1] == "lightweight-release-v1" and "sudo" not in calls[0]


def test_transport_complete_receipt_only(monkeypatch):
    op = str(uuid.uuid4())
    monkeypatch.setattr(client.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(
        args, 0, m.canonical({"status": "SUBMITTED_QUERY_SAME_OPERATION_ONLY", "operation_id": op}), b""))
    assert client.call_ssh("approved-host", "submit", b"{}", op)["status"].startswith("SUBMITTED")


def test_three_fixture_cycles_are_not_production_acceptance(tmp_path):
    results = []
    for n in range(3):
        root = tmp_path / str(n)
        root.mkdir()
        a, c, j, p, g = execution(root)
        c.execute(p, g, j)
        results.append(c.status(j)["status"])
    assert results == ["COMMITTED"] * 3  # labelled Fixture in report, NOT the enablement gate


def test_unpacked_budget_rejects_before_reading_next_member(monkeypatch):
    monkeypatch.setattr(m, "MAX_UNPACKED_BYTES", 9)
    a, r, i = package({"enterprise_agent_poc/app/static/a.js": b"12345",
                       "enterprise_agent_poc/app/static/b.js": b"12345"})
    with pytest.raises(m.Blocked, match="unpacked_limit"):
        m.inspect_package(a, r, i)
