#!/opt/enterprise-agent-workbench/venv/bin/python
"""Fixed-host Linux adapter / bounded broker for the proposed ordinary contract.

Only a separately adopted, Root-owned grant can activate this entry. The
broker has no shell/command/path/host input API. The SSH client never runs the
candidate's bootstrap or qualifies itself. Existing V3 remains untouched.
"""
from __future__ import annotations

if __name__ == "__main__":
    # Direct script invocation is not the privileged entry. Reject BEFORE any
    # repository import (including unchecked-hash bytecode and cache creation).
    print('{"status":"BLOCKED","check":"real_root_host_verified_source_entry_required"}')
    raise SystemExit(2)

import argparse
import base64
import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

from lightweight_release_v1 import (
    BASE, CONTROL, HOST, LOCK, Blocked, Controller, Journal, canonical,
    durable_new, inspect_package, ordinary_diff, operation, read_nofollow,
    require, sha, trusted, verify_evidence, verify_owner, verify_plan, owner_body, HASH, sync_directory, verify_backup_binding,
)

SERVICES = tuple(f"enterprise-agent-{role}.service" for role in ("api", "mcp", "worker"))
ENTRY = CONTROL / "tooling/enterprise_agent_poc/scripts/lightweight_release_linux_v1.py"
PYTHON = BASE / "venv/bin/python"


def dropin_path(service):
    require(service in SERVICES, "fixed_service_only")
    return Path("/etc/systemd/system") / f"{service}.d" / "release.conf"


def runtime_fingerprint():
    executable = PYTHON.resolve(strict=True)
    trusted(executable)
    metadata = sorted((BASE / "venv/lib").glob("python*/site-packages/*.dist-info/METADATA"))
    require(metadata, "runtime_dependency_inventory")
    identities = {}
    for path in metadata:
        trusted(path)
        identities[str(path.relative_to(BASE / "venv"))] = sha(read_nofollow(path))
    env = BASE / "shared/enterprise-agent.env"
    trusted(env)
    return {"python_sha256": sha(read_nofollow(executable)),
            "dependency_metadata_sha256": sha(canonical(identities)),
            "environment_sha256": sha(read_nofollow(env))}


def qualified_reports(plan, reports):
    """A JSON environment label is NOT evidence provenance or authority.

    Reuse existing protected qualifications; the client cannot write this store.
    Binding its approved Test/report producer is an explicit pilot prerequisite,
    not a new issuer in this controller or a default synthetic PASS.
    """
    store = BASE / "shared/release-qualifications/ordinary-v1/reports"
    trusted(store, directory=True)
    for item in plan["evidence"].values():
        digest = item["sha256"]
        require(HASH.fullmatch(digest), "qualified_report_digest")
        path = store / f"{digest}.json"
        trusted(path)
        require(path.stat().st_mode & 0o777 == 0o400
                and read_nofollow(path) == reports[digest], "independent_qualified_report_required")
    digest = plan["backup"]["restore_report_sha256"]
    require(HASH.fullmatch(digest), "qualified_restore_digest")
    path = store / f"{digest}.json"
    trusted(path)
    require(path.stat().st_mode & 0o777 == 0o400 and read_nofollow(path) == reports[digest],
            "independent_restore_proof_required")


def run(argv, *, env=None, timeout=60):
    # Never log stdout/stderr/environment or arbitrary exception contents.
    result = subprocess.run(argv, env=env, capture_output=True, timeout=timeout)
    require(result.returncode == 0, "trusted_child_exit")
    return result.stdout


def owner_event(plan, grant):
    # GET-only existing GitHub API; no token printing, confirmation POST or issuer.
    issue = grant["owner_issue_url"]
    prefix = "https://api.github.com/repos/Lucky990501/ky-web/issues/"
    require(issue.startswith(prefix) and issue[len(prefix):].isdigit(), "owner_issue")
    headers = {"User-Agent": "Workbench-Release-V1", "Accept": "application/vnd.github+json"}
    # Private repo credentials must be a separately installed READ-ONLY GitHub
    # App token file, not Owner signing credentials. No token in argv or audit.
    token_path = CONTROL / "github-readonly-token"
    if token_path.exists():
        trusted(token_path)
        require(token_path.stat().st_mode & 0o777 == 0o600, "github_token_permissions")
        token = read_nofollow(token_path, 4096).decode().strip()
        headers["Authorization"] = f"Bearer {token}"
    matches = []
    deadline = time.monotonic() + 30
    for page in range(1, 11):
        require(time.monotonic() < deadline, "owner_event_lookup_timeout")
        request = urllib.request.Request(f"{issue}/comments?per_page=100&page={page}", headers=headers)
        with urllib.request.urlopen(request, timeout=10) as response:
            require(response.status == 200, "owner_event_http")
            rows = json.loads(response.read(2 * 1024 * 1024))
            require(isinstance(rows, list), "owner_event_list")
            matches.extend(r for r in rows if r.get("body") == owner_body(plan))
            if len(rows) < 100:
                break
    else:
        raise Blocked("owner_event_page_limit")
    require(len(matches) == 1, "unique_external_owner_event_required")
    return matches[0]


def grant_read(*, readonly=False):
    require(os.getuid() == os.getgid() == 0 and socket.gethostname() == HOST, "real_root_host")
    trusted(CONTROL, directory=True)
    trusted(CONTROL / "machine-grant.json")
    grant = json.loads(read_nofollow(CONTROL / "machine-grant.json", 128 * 1024))
    require((readonly or grant["revoked"] is False)
            and grant["tooling_root"] == str(CONTROL / "tooling"), "machine_grant")
    # All installed Tooling bytes pinned before import, not merely top-level entry.
    root = Path(grant["tooling_root"])
    trusted(root, directory=True)
    require({str(p.relative_to(root)) for p in root.rglob("*") if not p.is_dir()}
            == set(grant["tooling"]["files"]), "complete_tooling_inventory")
    for name, digest in grant["tooling"]["files"].items():
        path = root / name
        require(path.is_relative_to(root) and ".." not in Path(name).parts, "tooling_path")
        trusted(path)
        require(sha(read_nofollow(path)) == digest, "tooling_bytes")
    require(str(ENTRY.relative_to(root)) in grant["tooling"]["files"]
            and "enterprise_agent_poc/scripts/lightweight_release_v1.py" in grant["tooling"]["files"],
            "controller_pin_required")
    require(grant["startup_probe_budget_authorized"] is True, "startup_embedding_budget_required")
    require(grant["trusted_git"] == str(CONTROL / "source.git"), "trusted_git_path")
    trusted(CONTROL / "source.git", directory=True)
    if not readonly:
        require(runtime_fingerprint() == grant["runtime_fingerprint"], "runtime_config_dependency_drift")
    if not readonly and grant["mode"] == "ENABLED":
        prior = None
        for item in grant["acceptance_cycles"]:
            path = CONTROL / "acceptance" / f"{operation(item['operation_id'])}.json"
            trusted(path)
            raw = read_nofollow(path, 1024 * 1024)
            require(sha(raw) == item["receipt_sha256"], "real_cycle_receipt_hash")
            record = json.loads(raw)
            require(record["environment"] == "PRODUCTION" and record["status"] == "COMMITTED"
                    and record["operation_id"] == item["operation_id"] and record["source"] == item["source"]
                    and record["owner_confirmations"] == 1 and record["manual_deployment_steps"] == 0
                    and 0 < record["seconds_after_owner_confirmation"] <= 900, "real_cycle_acceptance")
            if prior is not None:
                require(record["predecessor_source"] == prior, "consecutive_real_cycles")
            prior = record["source"]
            if item["exact_restore_rehearsal"]:
                proof = CONTROL / "acceptance" / f"{item['operation_id']}.restore.json"
                trusted(proof)
                require(sha(read_nofollow(proof)) == record["restore_receipt_sha256"], "real_restore_receipt_hash")
    return grant


class LinuxAdapter:
    def __init__(self, grant, op_root):
        self.grant, self.op_root = grant, Path(op_root)
        self.tooling = Path(grant["tooling_root"]) / "enterprise_agent_poc"

    def now(self):
        return int(time.time())

    @contextlib.contextmanager
    def mutation_guard(self, callback):
        prior = getattr(self, "_mutation_check", None)
        self._mutation_check = callback
        try:
            yield
        finally:
            self._mutation_check = prior

    def before_mutation(self):
        check = getattr(self, "_mutation_check", None)
        if check is not None:
            check()

    def _fresh_authority(self, plan, grant, recovery):
        # Read the protected grant before the final local time/marker checks:
        # grant_read may itself do slow Tooling/runtime integrity I/O.
        fresh = grant_read()
        require(fresh == grant, "machine_revoked_or_changed")
        if recovery:
            require(plan["recovery_authorized"] is True and self.now() < plan["windows"]["recover_until"]
                    <= grant["expires_at"], "recovery_authority_expired")
        else:
            verify_plan(plan, grant, self.now())
        require(not (CONTROL / "REVOKED").exists(), "machine_revoked_or_changed")
        require(not (self.op_root / ("RECOVERY_REVOKED" if recovery else "PUBLISH_REVOKED")).exists(),
                "operation_revoked")

    def authorize(self, plan, grant, recovery=False):
        self._fresh_authority(plan, grant, recovery)
        event = owner_event(plan, grant)
        verify_owner(plan, grant, event, self.now(), recovery=recovery)
        # Never return cached pre-GET authority to the per-mutation guard. No
        # network lookup or other slow work follows this fresh local check.
        self._fresh_authority(plan, grant, recovery)

    def git(self, *args):
        return run(["/usr/bin/git", "--no-pager", "-c", "core.hooksPath=/dev/null",
                    "-c", "core.fsmonitor=false", "-C", self.grant["trusted_git"], *args],
                   env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent",
                        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"})

    def package(self, release, root):
        archive = read_nofollow(root / "archive.tar.gz")
        raw = read_nofollow(root / "manifest.json", 2 * 1024 * 1024)
        manifest, files, dirs = inspect_package(archive, raw, release)
        require(self.git("rev-parse", f"{release['source']}^{{tree}}").decode().strip()
                == release["tree"], "source_tree")
        # Same builder allowlist; all archive bytes/modes map to exact Git blobs.
        sys.path.insert(0, str(self.tooling))
        from scripts.build_release import ALLOWED, STATIC_VERSION_PLACEHOLDERS
        from app.bundled_skills import forbidden_path, secret_content
        records = self.git("ls-tree", "-r", "-z", release["source"]).split(b"\0")
        selected = {}
        for record in records:
            if record:
                header, name = record.split(b"\t", 1)
                name = name.decode()
                if any(name == p or name.startswith(p) for p in ALLOWED):
                    mode, kind, oid = header.decode().split()
                    require(kind == "blob" and mode in {"100644", "100755"}, "git_member_type")
                    selected[name] = (mode, oid)
        require(set(selected) == set(files), "git_selected_inventory")
        for name, (mode, oid) in selected.items():
            data = self.git("cat-file", "blob", oid)
            require(not forbidden_path(name) and not secret_content(data), "forbidden_or_secret_source_file")
            if name == "enterprise_agent_poc/app/static/index.html":
                for kind, placeholder in STATIC_VERSION_PLACEHOLDERS.items():
                    asset = "workbench.js" if kind == "javascript" else "workbench.css"
                    version = files[f"enterprise_agent_poc/app/static/{asset}"]["sha256"][:16].encode()
                    require(data.count(placeholder) == 1, "static_placeholder")
                    data = data.replace(placeholder, version)
            require(sha(data) == files[name]["sha256"] and int(mode[-3:], 8) == files[name]["mode"],
                    "exact_git_byte_mode")
        return manifest, files, dirs

    def nonstatic_tree(self, source):
        require(__import__("re").fullmatch("[0-9a-f]{40}", source), "evidence_origin_source")
        sys.path.insert(0, str(self.tooling))
        from scripts.build_release import ALLOWED
        selected = []
        for record in self.git("ls-tree", "-r", "-z", source).split(b"\0"):
            if record:
                _, name = record.split(b"\t", 1)
                name = name.decode()
                if any(name == p or name.startswith(p) for p in ALLOWED) and not name.startswith("enterprise_agent_poc/app/static/"):
                    selected.append(record)
        require(selected, "evidence_origin_inventory")
        return sha(b"\0".join(selected))

    def installed(self, release):
        core = BASE / "releases" / release["release_id"]
        trusted(core, directory=True)
        archive = core / f"{release['release_id']}.tar.gz"
        manifest = core / f"{release['release_id']}.manifest.json"
        trusted(archive)
        trusted(manifest)
        _, files, _ = inspect_package(read_nofollow(archive), read_nofollow(manifest), release)
        actual = {str(p.relative_to(core)): p for p in (core / "enterprise_agent_poc").rglob("*") if not p.is_dir()}
        require(set(actual) == set(files), "installed_inventory")
        for name, item in files.items():
            path = actual[name]
            trusted(path)
            require(path.stat().st_mode & 0o777 == (0o555 if item["mode"] == 0o755 else 0o444)
                    and sha(read_nofollow(path)) == item["sha256"], "installed_bytes_mode")
        return files

    def install(self, plan):
        _, files, dirs = self.package(plan["candidate"], self.op_root / "inputs")
        old = self.installed(plan["predecessor"])
        ordinary_diff(old, files, plan["changed_files"])
        baseline = self.nonstatic_tree(plan["candidate"]["source"])
        require(self.nonstatic_tree(plan["predecessor"]["source"]) == baseline, "nonstatic_git_equivalence")
        for item in plan["evidence"].values():
            if item["mode"] == "REUSE":
                require(self.nonstatic_tree(item["origin_source"]) == baseline, "reuse_origin_git_equivalence")
        core = BASE / "releases" / plan["candidate"]["release_id"]
        trusted(core.parent, directory=True)
        require(not core.exists() and not core.is_symlink(), "candidate_target_exists")
        # Intent and all checks before first core write; preserve any partial install.
        durable_new(self.op_root / "INSTALL_INTENT.json", canonical({"candidate": plan["candidate"]}))
        core.mkdir(mode=0o755)
        for name in sorted(dirs, key=lambda n: (n.count("/"), n)):
            (core / name).mkdir(mode=0o755)
        for name, item in files.items():
            durable_new(core / name, item["data"], 0o555 if item["mode"] == 0o755 else 0o444)
        rid = plan["candidate"]["release_id"]
        for source, target in (("archive.tar.gz", f"{rid}.tar.gz"), ("manifest.json", f"{rid}.manifest.json")):
            durable_new(core / target, read_nofollow(self.op_root / "inputs" / source), 0o444)
        self.installed(plan["candidate"])

    @contextlib.contextmanager
    def lock(self):
        trusted(LOCK)
        fd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW)
        try:
            # Same file as legacy FD9; no new lock namespace, handoff or wait/retry.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(os.fstat(fd).st_ino == LOCK.lstat().st_ino, "lock_inode_drift")
            yield
        finally:
            os.close(fd)

    def snapshot(self, *, require_idle=False):
        sys.path.insert(0, str(self.tooling))
        from scripts.schema_015_runtime_only import ledger_snapshot
        from app.settings import settings
        from psycopg import connect
        ledger = ledger_snapshot(self.tooling, settings.database_url)
        with connect(settings.database_url, options="-c default_transaction_read_only=on -c statement_timeout=5000") as conn:
            require(conn.execute("SHOW transaction_read_only").fetchone()[0] == "on", "readonly_database")
            # Reads only; never imports write/service provisioning entry points.
            active = conn.execute("SELECT count(*) FROM tasks WHERE status IN ('pending','queued','running')").fetchone()[0]
        if require_idle:
            require(active == 0, "active_tasks")
        return {"ledger": ledger, "database_writes": 0}

    def preflight(self, plan):
        require((BASE / "release-current").resolve(strict=True) == self.target(plan["predecessor"]),
                "live_exact_predecessor")
        self.installed(plan["candidate"])
        self.installed(plan["predecessor"])
        self.health(plan["predecessor"])
        self.snapshot(require_idle=True)
        self.backup_preflight(plan)

    def backup_preflight(self, plan):
        store = BASE / "shared/release-qualifications/ordinary-v1/reports"
        item = plan["evidence"]["production_backup_restore"]
        reports = {digest: read_nofollow(trusted(store / f"{digest}.json")) for digest in
                   (item["sha256"], plan["backup"]["restore_report_sha256"])}
        backup = verify_backup_binding(plan, reports, self.now())
        path = BASE / "backups" / backup["relative_path"]
        require(".." not in Path(backup["relative_path"]).parts and path.is_relative_to(BASE / "backups"), "backup_path")
        trusted(path)
        require(sha(read_nofollow(path)) == backup["ciphertext_sha256"]
                and backup["captured_at"] <= self.now() <= backup["valid_until"], "fresh_encrypted_backup")

    def capture(self, plan):
        snapshot = self.snapshot()
        snapshot["predecessor"] = plan["predecessor"]
        durable_new(self.op_root / "snapshot.json", canonical(snapshot))
        return snapshot

    def target(self, release):
        return BASE / "releases" / release["release_id"] / "enterprise_agent_poc"

    def activate(self, release):
        target = self.target(release)
        self.installed(release)
        # Existing release_switch.sh configure_services layout / fixed argv.
        for service in SERVICES:
            dropin = dropin_path(service)
            trusted(dropin.parent, directory=True)
            executable = {SERVICES[0]: f"{BASE}/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 18090",
                          SERVICES[1]: f"{PYTHON} -m app.platform_mcp.server",
                          SERVICES[2]: f"{PYTHON} -m app.worker"}[service]
            data = (f"[Service]\nWorkingDirectory={target}\nEnvironmentFile=\n"
                    f"EnvironmentFile={BASE}/shared/enterprise-agent.env\nEnvironment=PYTHONPATH={target}\n"
                    f"Environment=ENTERPRISE_POC_DATA_DIR={BASE}/shared/runtime-data\nExecStart=\n"
                    f"ExecStart={executable}\n").encode()
            temp = dropin.parent / "release.lightweight-v1.pending"
            self.before_mutation()
            durable_new(temp, data, 0o644)
            self.before_mutation()
            os.replace(temp, dropin)
            sync_directory(dropin.parent)
        temp_link = BASE / "release-current.lightweight-v1.pending"
        require(not temp_link.exists() and not temp_link.is_symlink(), "pointer_temp_exists")
        self.before_mutation()
        temp_link.symlink_to(target)
        self.before_mutation()
        os.replace(temp_link, BASE / "release-current")
        sync_directory(BASE)
        self.before_mutation()
        run(["/usr/bin/systemctl", "daemon-reload"])
        self.before_mutation()
        run(["/usr/bin/systemctl", "restart", SERVICES[1], SERVICES[0], SERVICES[2]], timeout=120)

    def health(self, release):
        # Reuse actual production health functions; not fixed two paid Tasks.
        sys.path.insert(0, str(self.tooling))
        from scripts.release_verify import service_state, health
        require((BASE / "release-current").resolve(strict=True) == self.target(release), "health_pointer")
        deadline = time.monotonic() + 90
        while True:
            try:
                for row in service_state(BASE).values():
                    require(row.get("active") == "active" and row.get("pid", 0) > 0
                            and row.get("cwd") == str(self.target(release)) and row.get("module_ok")
                            and row.get("exe") == str(PYTHON.resolve(strict=True)), "three_service_identity")
                health()
                return
            except Exception:
                require(time.monotonic() < deadline, "three_service_readiness_timeout")
                time.sleep(1)  # readiness poll, never a second service activation

    def data_preserved(self, plan, snapshot):
        require(self.snapshot()["ledger"] == snapshot["ledger"], "live_ledger_preserved")
        # Ordinary lane proves ALL non-static application/migration/Skill/config
        # code bytes unchanged; no writes/migrations/Registry compensation run.
        ordinary_diff(self.installed(plan["predecessor"]), self.installed(plan["candidate"]), plan["changed_files"])

    def recovery_preflight(self, plan, snapshot):
        require(snapshot["predecessor"] == plan["predecessor"], "recovery_exact_target")
        require((BASE / "release-current").resolve(strict=True)
                in {self.target(plan["candidate"]), self.target(plan["predecessor"])}, "recovery_unknown_pointer")
        self.data_preserved(plan, snapshot)
        self.backup_preflight(plan)


def submit(payload, grant):
    plan = payload["plan"]
    verify_plan(plan, grant, int(time.time()))
    verify_owner(plan, grant, owner_event(plan, grant), int(time.time()))
    reports = {k: base64.b64decode(v, validate=True) for k, v in payload["reports"].items()}
    verify_evidence(plan, reports, int(time.time()))
    qualified_reports(plan, reports)
    archive = base64.b64decode(payload["archive"], validate=True)
    manifest = base64.b64decode(payload["manifest"], validate=True)
    bundle = base64.b64decode(payload["source_bundle"], validate=True)
    require(HASH.fullmatch(plan["source_bundle_sha256"]) and sha(bundle) == plan["source_bundle_sha256"]
            and len(bundle) <= 64 * 1024 * 1024, "source_bundle_hash_size")
    inspect_package(archive, manifest, plan["candidate"])
    trusted(CONTROL / "operations", directory=True)
    root = CONTROL / "operations" / operation(plan["operation_id"])
    # Exclusive operation, independent of client connection. No resume/overwrite.
    root.mkdir(mode=0o700)
    (root / "inputs").mkdir(mode=0o700)
    (root / "journal").mkdir(mode=0o700)
    durable_new(root / "plan.json", canonical(plan), 0o400)
    durable_new(root / "owner-event.json", canonical(owner_event(plan, grant)), 0o400)
    durable_new(root / "inputs/archive.tar.gz", archive, 0o400)
    durable_new(root / "inputs/manifest.json", manifest, 0o400)
    durable_new(root / "inputs/source.bundle", bundle, 0o400)
    for digest, raw in reports.items():
        require(HASH.fullmatch(digest) and sha(raw) == digest, "report_filename_hash")
        durable_new(root / "inputs" / f"{digest}.json", raw, 0o400)
    adapter = LinuxAdapter(grant, root)
    # Existing Git only; Root-owned imported bundle, no candidate program runs.
    adapter.git("bundle", "verify", str(root / "inputs/source.bundle"))
    adapter.git("fetch", "--no-tags", "--no-write-fetch-head", str(root / "inputs/source.bundle"),
                plan["candidate"]["source"])
    adapter.package(plan["candidate"], root / "inputs")
    durable_new(root / "INPUTS_COMMITTED.json", canonical({"plan_sha256": sha(canonical(plan))}), 0o400)
    # Only fixed unit + validated UUID, never caller-provided commands/paths.
    run(["/usr/bin/systemctl", "start", "--no-block", f"enterprise-agent-release-v1@{plan['operation_id']}.service"])
    return {"status": "SUBMITTED_QUERY_SAME_OPERATION_ONLY", "operation_id": plan["operation_id"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("rpc", "submit", "status", "run", "recover-if-needed"))
    parser.add_argument("operation_id", nargs="?")
    args = parser.parse_args()
    payload = None
    if args.action == "rpc":
        raw = sys.stdin.buffer.read(96 * 1024 * 1024 + 1)
        require(len(raw) <= 96 * 1024 * 1024, "input_limit")
        envelope = json.loads(raw)
        require(set(envelope) <= {"action", "payload", "operation_id"}
                and envelope["action"] in {"submit", "status"}, "bounded_rpc")
        args.action = envelope["action"]
        args.operation_id = envelope.get("operation_id")
        payload = envelope.get("payload")
    grant = grant_read(readonly=args.action == "status")
    if args.action == "submit":
        if payload is None:
            raw = sys.stdin.buffer.read(96 * 1024 * 1024 + 1)
            require(len(raw) <= 96 * 1024 * 1024, "input_limit")
            payload = json.loads(raw)
        print(json.dumps(submit(payload, grant)))
        return
    root = CONTROL / "operations" / operation(args.operation_id)
    trusted(root, directory=True)
    journal = Journal(root / "journal")
    controller = Controller(LinuxAdapter(grant, root))
    if args.action == "status":
        status = controller.status(journal)
        if not journal.read():
            status = {"status": "SUBMITTED_NOT_STARTED_QUERY_ONLY" if (root / "INPUTS_COMMITTED.json").exists()
                      else "UNKNOWN_INCOMPLETE_INPUTS_QUERY_ONLY"}
        print(json.dumps(status))
        return
    # Only systemd can invoke side-effect verbs, not a client RPC or SSH TTY.
    require(os.environ.get("INVOCATION_ID") and os.environ.get("LW_RELEASE_SYSTEMD") == "1", "systemd_job_required")
    plan = json.loads(read_nofollow(root / "plan.json"))
    inputs = json.loads(read_nofollow(root / "INPUTS_COMMITTED.json"))
    require(inputs["plan_sha256"] == sha(canonical(plan)), "committed_input_plan")
    if args.action == "run":
        reports = {item["sha256"]: read_nofollow(root / "inputs" / f"{item['sha256']}.json")
                   for item in plan["evidence"].values()}
        proof = plan["backup"]["restore_report_sha256"]
        require(HASH.fullmatch(proof), "restore_proof_filename")
        reports[proof] = read_nofollow(root / "inputs" / f"{proof}.json")
        verify_evidence(plan, reports, int(time.time()))
        qualified_reports(plan, reports)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError()))
    if args.action == "run":
        controller.execute(plan, grant, journal)
    elif args.action == "recover-if-needed":
        rows = journal.read()
        if rows and rows[-1]["phase"] in {"ACTIVATING", "ACTIVATED", "FAILURE_AFTER_ACTIVATION", "COMMIT_PREPARED"}:
            controller.recover(plan, grant, journal)
    print(json.dumps(controller.status(journal)))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "check": str(exc) if isinstance(exc, Blocked) else type(exc).__name__}))
        raise SystemExit(2)
