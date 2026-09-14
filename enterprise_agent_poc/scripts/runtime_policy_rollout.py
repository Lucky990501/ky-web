"""Dedicated four-key, local-host policy transaction. Never print env values.

The CLI has a fixed production root, no path override and no shell evaluation.
Tests inject an explicitly marked private fixture and a fake service boundary.
This module imports no application store, Settings initializer or queue client.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

try:
    from scripts.verify_runtime_config import compare, safe_runtime_config_snapshot
    from scripts.rollback_preflight import BASE, RollbackBlocked, digest, read_json, release_identity
except ModuleNotFoundError:
    from verify_runtime_config import compare, safe_runtime_config_snapshot
    from rollback_preflight import BASE, RollbackBlocked, digest, read_json, release_identity

PREFIX = "ENTERPRISE_POC_AGENT_RUNTIME_TEST_"
KEYS = tuple(PREFIX + suffix for suffix in (
    "PRODUCTION_ENABLED", "ALLOWED_TENANT_IDS", "ALLOWED_TEMPLATE_SLUGS", "TENANT_ID"))
SERVICES = ("enterprise-agent-api.service", "enterprise-agent-mcp.service", "enterprise-agent-worker.service")
TERMINAL = {"verified", "rolled_back"}
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
READINESS_MAX_ATTEMPTS = 31
READINESS_INTERVAL_SECONDS = 1
READINESS_TIMEOUT_SECONDS = 30
JOURNAL_STATES = TERMINAL | {"prepared", "config_replaced", "services_restarting", "rollback_failed"}
FAILURE_STAGES = {
    "config_replace", "config_cas", "journal_persist", "restart", "service_state", "main_pid",
    "application_identity", "process_fingerprint", "policy_semantics", "local_health",
    "public_health", "mcp_ready", "worker_ready", "unknown",
}
LEGACY_JOURNAL_FIELDS = {
    "operation_id", "timestamp", "config_path", "backup_path", "pre_sha256", "post_sha256",
    "size", "mode", "uid", "gid", "application_identity", "target_tenant", "target_slug", "state",
}
JOURNAL_V2_FIELDS = {
    "journal_version", "transition_timestamps", "original_failure_stage", "original_failure_code",
    "original_failure_at", "rollback_failure_stage", "rollback_failure_code", "rollback_failure_at",
}


class Blocked(Exception):
    """Only constant, non-sensitive error codes may cross the public boundary."""


class VerificationFailure(Blocked):
    """A fixed-code verification failure with an auditable, secret-free stage."""
    def __init__(self, stage: str, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.stage = stage
        self.code = code
        self.retryable = retryable


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error echoes caller-supplied values. Never do so.
        self.exit(2, "POLICY_CONTROL_ARGUMENTS_INVALID\n")


def require(ok: bool, code: str) -> None:
    if not ok:
        raise Blocked(code)


def verification_failure(stage: str, code: str, *, retryable: bool = False):
    require(stage in FAILURE_STAGES and re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", code) is not None,
            "INTERNAL_FAILURE_CODE_INVALID")
    raise VerificationFailure(stage, code, retryable=retryable)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def valid_timestamp(value) -> bool:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None


def failure_details(exc: Exception, default_stage: str) -> tuple[str, str]:
    if isinstance(exc, VerificationFailure):
        return exc.stage, exc.code
    if isinstance(exc, Blocked) and re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", str(exc)):
        return default_stage, str(exc)
    return default_stage, "UNEXPECTED_FAILURE"


def policy_values(tenant: str, slug: str) -> dict[str, str]:
    require(bool(NAME.fullmatch(tenant)), "INVALID_TENANT")
    require(len(slug) <= 64 and bool(SLUG.fullmatch(slug)), "INVALID_SLUG")
    return dict(zip(KEYS, ("true", tenant, slug, tenant)))


def parse(data: bytes) -> dict[str, str]:
    """A deliberately restricted shell-assignment subset, never evaluated.

    Non-target bytes are retained, not reserialized. Unsupported syntax blocks
    rather than guessing how a shell would interpret secrets or continuations.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise Blocked("UNSUPPORTED_ENV_ENCODING") from None
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)", line)
        require(match is not None, "UNSUPPORTED_ENV_SYNTAX")
        key, value = match.groups()
        require(key not in values, "DUPLICATE_ENV_KEY")
        require(not any(c in value for c in ("\\", "$", "`", "\x00")), "UNSUPPORTED_ENV_SYNTAX")
        if value.startswith(("'", '"')):
            quote = value[0]
            require(len(value) >= 2 and value[-1] == quote and quote not in value[1:-1],
                    "UNSUPPORTED_ENV_QUOTING")
            value = value[1:-1]
        else:
            require(not re.search(r"[\s'\";|&<>#()]", value), "UNSUPPORTED_ENV_SYNTAX")
        values[key] = value
    enabled = values.get(KEYS[0], "false")
    require(enabled in {"true", "false"}, "INVALID_POLICY_BOOLEAN")
    for key, pattern in ((KEYS[1], NAME), (KEYS[2], SLUG)):
        value = values.get(key, "")
        parts = value.split(",") if value else []
        require(all(len(p) <= 64 and pattern.fullmatch(p) for p in parts) and len(set(parts)) == len(parts),
                "INVALID_POLICY_LIST")
    tenant = values.get(KEYS[3], "")
    require(not tenant or bool(NAME.fullmatch(tenant)), "INVALID_POLICY_TENANT")
    return values


def non_policy_bytes(data: bytes) -> bytes:
    result = []
    for line in data.splitlines(keepends=True):
        content = line.removeprefix(b"\xef\xbb\xbf")
        match = re.match(rb"\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=", content)
        key = match[1].decode() if match else None
        if key not in KEYS:
            result.append(line)
    return b"".join(result)


def replace_policy(data: bytes, values: dict[str, str]) -> bytes:
    parse(data)
    lines = data.splitlines(keepends=True)
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    found = set()
    result = []
    for line in lines:
        content = line.removeprefix(b"\xef\xbb\xbf")
        match = re.match(rb"\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=", content)
        key = match[1].decode() if match else None
        if key in KEYS:
            end = b"\r\n" if line.endswith(b"\r\n") else b"\n" if line.endswith(b"\n") else b""
            bom = b"\xef\xbb\xbf" if line.startswith(b"\xef\xbb\xbf") else b""
            result.append(bom + (key + "=" + values[key]).encode() + end)
            found.add(key)
        else:
            result.append(line)
    for key in KEYS:
        if key not in found:
            if result and not result[-1].endswith(b"\n"):
                result.append(newline)
            result.append((key + "=" + values[key]).encode() + newline)
    output = b"".join(result)
    parsed = parse(output)
    require(all(parsed[k] == values[k] for k in KEYS), "POLICY_RENDER_FAILED")
    require({k: v for k, v in parsed.items() if k not in KEYS} ==
            {k: v for k, v in parse(data).items() if k not in KEYS}, "NON_POLICY_CHANGE")
    require(non_policy_bytes(output) == non_policy_bytes(data), "NON_POLICY_CHANGE")
    return output


@dataclass(frozen=True)
class Image:
    data: bytes
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    directory_identity: tuple = ()

    @property
    def digest(self) -> str:
        return sha(self.data)


class SystemdBoundary:
    """Fixed read/restart boundary; command output stays private."""
    def __init__(self, base: Path, *, proc_root: Path = Path("/proc")):
        self.base = base
        self.proc_root = proc_root
        self.identity_summary = {}
        self.service_states = {}
        self.service_pids = {}

    def _show(self, service: str) -> dict[str, str]:
        result = subprocess.run(["systemctl", "show", service,
                                 "--property=MainPID,ActiveState,WorkingDirectory"],
                                capture_output=True, text=True, timeout=3)
        require(result.returncode == 0, "SERVICE_QUERY_FAILED")
        return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    def inspect(self, *, require_active: bool = True) -> tuple[str, list[dict[str, str]]]:
        link = self.base / "release-current"
        require(link.is_symlink(), "APPLICATION_IDENTITY_INVALID")
        try:
            source = link.resolve(strict=True)
            release_dir = source.parent
            releases = self.base / "releases"
            require(releases.resolve() == releases and source.name == "enterprise_agent_poc" and
                    release_dir.parent == releases and
                    bool(re.fullmatch(r"[A-Za-z0-9._-]+", release_dir.name)),
                    "APPLICATION_IDENTITY_INVALID")
            manifest_path = release_dir / (release_dir.name + ".manifest.json")
            manifest = read_json(manifest_path)
            source_commit = manifest.get("source_commit")
            verified_source, verified_manifest = release_identity(
                self.base, release_dir.name, source_commit)
            require(verified_source == source and verified_manifest == manifest and
                    isinstance(manifest.get("build_platform"), str) and
                    bool(re.fullmatch(r"[A-Za-z0-9._-]{1,64}", manifest["build_platform"])),
                    "APPLICATION_IDENTITY_INVALID")
        except (Blocked, RollbackBlocked, OSError, ValueError, TypeError, AttributeError):
            raise Blocked("APPLICATION_IDENTITY_INVALID") from None
        self.identity_summary = {
            "release_id": release_dir.name,
            "source_directory": source.name,
            "source_commit": manifest["source_commit"],
            "archive_sha256": manifest["archive_sha256"],
            "manifest_sha256": digest(manifest),
            "selected_file_count": manifest["selected_file_count"],
            "build_platform": manifest["build_platform"],
        }
        identity = sha(json.dumps(self.identity_summary, sort_keys=True,
                                  separators=(",", ":")).encode())
        environments = []
        self.service_states = {}
        self.service_pids = {}
        for service in SERVICES:
            props = self._show(service)
            state = props.get("ActiveState", "unknown")
            self.service_states[service] = state
            if state == "activating" and require_active:
                verification_failure("service_state", "SERVICE_ACTIVATING", retryable=True)
            active = state == "active"
            require(active or not require_active, "SERVICE_INACTIVE")
            if not active:
                environments.append({})
                continue
            pid = props.get("MainPID", "0")
            if not pid.isdigit() or int(pid) <= 0:
                if require_active:
                    verification_failure("main_pid", "SERVICE_PID_UNAVAILABLE", retryable=True)
                environments.append({})
                continue
            self.service_pids[service] = pid
            require(props.get("WorkingDirectory") == str(source), "SERVICE_APPLICATION_MISMATCH")
            proc = self.proc_root / pid
            try:
                cwd = (proc / "cwd").resolve(strict=True)
            except FileNotFoundError:
                verification_failure("main_pid", "PROCESS_STATE_UNAVAILABLE", retryable=True)
            require(cwd == source, "SERVICE_APPLICATION_MISMATCH")
            try:
                raw = (proc / "environ").read_bytes()
            except FileNotFoundError:
                verification_failure("process_fingerprint", "PROCESS_ENVIRONMENT_UNAVAILABLE", retryable=True)
            environments.append(dict(part.decode().split("=", 1) for part in raw.split(b"\0") if b"=" in part))
        return identity, environments

    def restart(self, service: str) -> None:
        result = subprocess.run(["systemctl", "restart", service], capture_output=True, timeout=45)
        require(result.returncode == 0, "SERVICE_RESTART_FAILED")

    @staticmethod
    def _health(url: str, invalid_code: str, http_code: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                payload = json.loads(response.read(65536))
        except urllib.error.HTTPError as exc:
            if exc.code in {502, 503, 504}:
                return False
            raise Blocked(http_code) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            return False
        except OSError as exc:
            if exc.errno in {errno.ECONNREFUSED, errno.ETIMEDOUT, errno.EHOSTUNREACH, errno.ENETUNREACH}:
                return False
            raise Blocked(http_code) from None
        except (json.JSONDecodeError, UnicodeError, TypeError, ValueError):
            raise Blocked(invalid_code) from None
        require(isinstance(payload, dict), invalid_code)
        require(payload.get("environment") == "production", "HEALTH_ENVIRONMENT_MISMATCH")
        return payload.get("status") == "ok" and payload.get("knowledge") == "ok"

    def local_health(self) -> bool:
        return self._health("http://127.0.0.1:18090/api/health",
                            "LOCAL_HEALTH_RESPONSE_INVALID", "LOCAL_HEALTH_HTTP_FAILED")

    def public_health(self) -> bool:
        return self._health("https://workbench.luckio.cn/api/health",
                            "PUBLIC_HEALTH_RESPONSE_INVALID", "PUBLIC_HEALTH_HTTP_FAILED")

    def mcp_ready(self, port: int = 8091) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return True
        except (ConnectionRefusedError, TimeoutError):
            return False
        except OSError as exc:
            if exc.errno in {errno.ECONNREFUSED, errno.ETIMEDOUT, errno.EHOSTUNREACH, errno.ENETUNREACH}:
                return False
            raise Blocked("MCP_READINESS_QUERY_FAILED") from None

    def worker_ready(self) -> bool:
        pid = self.service_pids.get("enterprise-agent-worker.service")
        if not pid:
            return False
        result = subprocess.run([
            "journalctl", "--no-pager", "--quiet", "--output=cat",
            "_SYSTEMD_UNIT=enterprise-agent-worker.service", "_PID=" + pid,
            "--grep=worker ready",
        ], capture_output=True, timeout=3)
        require(result.returncode in {0, 1}, "WORKER_READINESS_QUERY_FAILED")
        return result.returncode == 0 and b"worker ready" in result.stdout

    def health(self) -> bool:
        return self.local_health() and self.public_health()


class Rollout:
    def __init__(self, base: Path = BASE, boundary=None, *, isolated: bool = False):
        self.base = Path(base).absolute()
        if isolated:
            require(boundary is not None and not isinstance(boundary, SystemdBoundary), "INVALID_TEST_BOUNDARY")
            marker = self.base / "runtime-policy-isolated.marker"
            require(any(self.base.is_relative_to(root) for root in
                        (Path(tempfile.gettempdir()).resolve(), Path(tempfile.gettempdir()),
                         Path("/private/tmp"), Path("/tmp").resolve())), "UNCONTROLLED_TEST_ROOT")
            require(marker.is_file() and not marker.is_symlink() and
                    marker.read_bytes() == b"runtime-policy-local-only", "INVALID_TEST_MARKER")
        else:
            require(self.base == BASE and os.geteuid() == 0, "PRODUCTION_ROOT_REQUIRED")
            require(boundary is None, "INVALID_PRODUCTION_BOUNDARY")
        self.uid = os.geteuid()
        self.env = self.base / "shared/enterprise-agent.env"
        self.operations = self.base / "shared/runtime-policy-operations"
        self.boundary = boundary or SystemdBoundary(self.base)

    def _directories(self, path: Path) -> tuple:
        identity = []
        for parent in (path, *path.parents):
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), "UNCONTROLLED_DIRECTORY")
            # Ancestors such as /tmp may be sticky world-writable; the controlled
            # root and everything below it must be owned and non-writable by peers.
            if parent == self.base or parent.is_relative_to(self.base):
                require(info.st_uid == self.uid and not info.st_mode & 0o022, "UNSAFE_DIRECTORY_PERMISSIONS")
                identity.append((str(parent), info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), info.st_uid))
        return tuple(identity)

    def _read(self, path: Path) -> Image:
        directory_identity = self._directories(path.parent)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "UNSAFE_FILE_TYPE")
            require(info.st_uid == self.uid and stat.S_IMODE(info.st_mode) in {0o400, 0o600}, "UNSAFE_FILE_PERMISSIONS")
            require(info.st_size <= 2 * 1024 * 1024, "CONFIG_TOO_LARGE")
            data = b""
            while chunk := os.read(fd, 65536):
                data += chunk
                require(len(data) <= 2 * 1024 * 1024, "CONFIG_TOO_LARGE")
            after = os.fstat(fd)
            current = path.lstat()
            require((info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size) ==
                    (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size) and
                    (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), "CONFIG_CONCURRENT_MODIFICATION")
            require(self._directories(path.parent) == directory_identity, "CONFIG_CONCURRENT_MODIFICATION")
            return Image(data, info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid,
                         directory_identity)
        finally:
            os.close(fd)

    def _atomic(self, path: Path, data: bytes, metadata: Image, expected: Image | None = None,
                code: str = "CONFIG_CONCURRENT_MODIFICATION") -> None:
        self._directories(path.parent)
        parent = path.parent.lstat()
        fd, name = tempfile.mkstemp(prefix=".runtime-policy-", dir=path.parent)
        try:
            os.fchmod(fd, metadata.mode)
            os.fchown(fd, metadata.uid, metadata.gid)
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            if expected is not None:
                current = self._read(path)
                require(current == expected, code)
            now = path.parent.lstat()
            require((parent.st_dev, parent.st_ino) == (now.st_dev, now.st_ino), code)
            os.replace(name, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _journals(self) -> list[dict]:
        if not self.operations.exists() and not self.operations.is_symlink():
            return []
        self._directories(self.operations)
        require(stat.S_IMODE(self.operations.stat().st_mode) == 0o700, "UNSAFE_JOURNAL_DIRECTORY")
        result = []
        for path in self.operations.glob("*.json"):
            journal = json.loads(self._read(path).data)
            require(re.fullmatch(r"[0-9a-f]{32}", journal.get("operation_id", "")) is not None and
                    path.name == journal["operation_id"] + ".json" and
                    journal.get("state") in JOURNAL_STATES,
                    "INVALID_JOURNAL")
            fields = set(journal)
            require((fields == LEGACY_JOURNAL_FIELDS or
                     fields == LEGACY_JOURNAL_FIELDS | JOURNAL_V2_FIELDS) and
                    all(isinstance(journal[k], str) and re.fullmatch(r"[0-9a-f]{64}", journal[k])
                        for k in ("pre_sha256", "post_sha256")) and
                    journal["mode"] in {0o400, 0o600} and journal["uid"] == self.uid and
                    type(journal["gid"]) is int and journal["gid"] >= 0 and
                    type(journal["size"]) is int and 0 <= journal["size"] <= 2 * 1024 * 1024 and
                    valid_timestamp(journal.get("timestamp")),
                    "INVALID_JOURNAL")
            if fields != LEGACY_JOURNAL_FIELDS:
                transitions = journal["transition_timestamps"]
                require(journal["journal_version"] == 2 and isinstance(transitions, list) and
                        1 <= len(transitions) <= 32 and
                        all(isinstance(item, dict) and set(item) == {"state", "timestamp"} and
                            item["state"] in JOURNAL_STATES and valid_timestamp(item["timestamp"])
                            for item in transitions) and transitions[-1]["state"] == journal["state"],
                        "INVALID_JOURNAL")
                for prefix in ("original", "rollback"):
                    stage = journal[prefix + "_failure_stage"]
                    code = journal[prefix + "_failure_code"]
                    at = journal[prefix + "_failure_at"]
                    require((stage is None and code is None and at is None) or
                            (stage in FAILURE_STAGES and isinstance(code, str) and
                             re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", code) is not None and
                             valid_timestamp(at)), "INVALID_JOURNAL")
            policy_values(journal["target_tenant"], journal["target_slug"])
            result.append(journal)
        return result

    def _unresolved(self, except_id: str | None = None) -> None:
        require(not any(j["state"] not in TERMINAL and j["operation_id"] != except_id for j in self._journals()),
                "UNFINISHED_OPERATION")

    @contextmanager
    def _lock(self):
        self._directories(self.operations.parent)
        if not self.operations.exists():
            self.operations.mkdir(mode=0o700)
        self._directories(self.operations)
        require(stat.S_IMODE(self.operations.stat().st_mode) == 0o700, "UNSAFE_JOURNAL_DIRECTORY")
        path = self.operations / "transaction.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == self.uid and
                    stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1, "UNSAFE_LOCK")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def _write_journal(self, journal: dict, state: str) -> None:
        if "journal_version" not in journal:
            journal.update(journal_version=2, transition_timestamps=[],
                           original_failure_stage=None, original_failure_code=None, original_failure_at=None,
                           rollback_failure_stage=None, rollback_failure_code=None, rollback_failure_at=None)
        journal["state"] = state
        journal["transition_timestamps"].append({"state": state, "timestamp": utc_now()})
        require(len(journal["transition_timestamps"]) <= 32, "JOURNAL_TRANSITION_LIMIT")
        data = json.dumps(journal, sort_keys=True, separators=(",", ":")).encode()
        self._atomic(self.operations / (journal["operation_id"] + ".json"), data,
                     Image(b"", 0, 0, 0o600, self.uid, os.getegid()))

    def _record_failure(self, journal: dict, kind: str, exc: Exception, default_stage: str) -> None:
        require(kind in {"original", "rollback"}, "INTERNAL_FAILURE_KIND_INVALID")
        stage, code = failure_details(exc, default_stage)
        journal[kind + "_failure_stage"] = stage
        journal[kind + "_failure_code"] = code
        journal[kind + "_failure_at"] = utc_now()

    def _transition(self, journal: dict, state: str) -> None:
        try:
            self._write_journal(journal, state)
        except Exception:
            verification_failure("journal_persist", "JOURNAL_PERSIST_FAILED")

    def _verify(self, data: bytes, identity: str | None = None, *, readiness: bool = False) -> str:
        begin = getattr(self.boundary, "begin_verification", None)
        if begin:
            begin()
        try:
            current_identity, environments = self.boundary.inspect()
        except VerificationFailure:
            raise
        except Blocked as exc:
            mapping = {
                "APPLICATION_IDENTITY_INVALID": "application_identity",
                "APPLICATION_IDENTITY_CHANGED": "application_identity",
                "SERVICE_APPLICATION_MISMATCH": "application_identity",
                "SERVICE_INACTIVE": "service_state",
                "SERVICE_QUERY_FAILED": "service_state",
            }
            verification_failure(mapping.get(str(exc), "unknown"), str(exc))
        if identity is not None and identity != current_identity:
            verification_failure("application_identity", "APPLICATION_IDENTITY_CHANGED")
        if len(environments) != len(SERVICES):
            verification_failure("service_state", "SERVICE_QUERY_FAILED")
        expected = parse(data)
        require(expected.get("APP_ENV") == "production", "ENVIRONMENT_NOT_PRODUCTION")
        comparisons = [compare(expected, current) for current in environments]
        if not all(all(current.get(k, "") == expected.get(k, "") for k in KEYS)
                   for current in environments):
            verification_failure("policy_semantics", "POLICY_SEMANTICS_MISMATCH")
        if not all(result["matches"] for result in comparisons):
            verification_failure("process_fingerprint", "CONFIG_FINGERPRINT_MISMATCH")

        mcp_port = expected.get("ENTERPRISE_POC_MCP_PORT", "8091")
        if not mcp_port.isdigit() or not 1 <= int(mcp_port) <= 65535:
            verification_failure("mcp_ready", "MCP_CONFIGURATION_INVALID")
        checks = (
            ("local_health", "LOCAL_HEALTH_NOT_READY", self.boundary.local_health),
            ("public_health", "PUBLIC_HEALTH_NOT_READY", self.boundary.public_health),
            ("mcp_ready", "MCP_NOT_READY", lambda: self.boundary.mcp_ready(int(mcp_port))),
            ("worker_ready", "WORKER_NOT_READY", self.boundary.worker_ready),
        )
        for stage, code, check in checks:
            try:
                ready = check()
            except VerificationFailure:
                raise
            except Blocked as exc:
                verification_failure(stage, str(exc))
            except Exception:
                verification_failure(stage, code, retryable=readiness)
            if not ready:
                verification_failure(stage, code, retryable=readiness)
        self.last_gate = {"shared_fingerprint": comparisons[0]["expected_fingerprint"],
                          "process_fingerprints": {service: result["runtime_fingerprint"]
                                                   for service, result in zip(SERVICES, comparisons)},
                          "config_matches": True, "policy_matches": True, "health_verified": True,
                          "local_health": True, "public_health": True,
                          "mcp_ready": True, "worker_ready": True,
                          "application": getattr(self.boundary, "identity_summary",
                                                 {"identity_sha256": current_identity}),
                          "service_states": getattr(self.boundary, "service_states",
                                                    dict.fromkeys(SERVICES, "active"))}
        return current_identity

    def _readiness_verify(self, data: bytes, identity: str) -> str:
        last = None
        deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
        for attempt in range(READINESS_MAX_ATTEMPTS):
            try:
                return self._verify(data, identity, readiness=True)
            except VerificationFailure as exc:
                if not exc.retryable:
                    raise
                last = exc
            remaining = deadline - time.monotonic()
            if attempt + 1 >= READINESS_MAX_ATTEMPTS or remaining <= 0:
                break
            time.sleep(min(READINESS_INTERVAL_SECONDS, remaining))
        verification_failure(last.stage if last else "service_state", "SERVICE_READINESS_TIMEOUT")

    def status(self) -> dict:
        self._unresolved()
        image = self._read(self.env)
        values = parse(image.data)
        identity = self._verify(image.data)
        return {"status": "verified", "config_sha256": image.digest,
                "config_fingerprint": safe_runtime_config_snapshot(values)["fingerprint"],
                "application_identity": identity, "application": self.last_gate["application"],
                "policy_enabled": values.get(KEYS[0], "false") == "true",
                "runtime_test_tenant_configured": bool(values.get(KEYS[3], "")), "gates": self.last_gate,
                "policy_tenant_count": len(values.get(KEYS[1], "").split(",")) if values.get(KEYS[1]) else 0,
                "policy_slug_count": len(values.get(KEYS[2], "").split(",")) if values.get(KEYS[2]) else 0}

    def plan(self, tenant: str, slug: str) -> dict:
        proposed = policy_values(tenant, slug)
        result = self.status()
        image = self._read(self.env)
        require(image.digest == result["config_sha256"], "CONFIG_CONCURRENT_MODIFICATION")
        output = replace_policy(image.data, proposed)
        current = parse(image.data)
        current_semantics = {
            "enabled": current.get(KEYS[0], "false") == "true",
            "allowed_tenant_count": len(current.get(KEYS[1], "").split(",")) if current.get(KEYS[1]) else 0,
            "allowed_slug_count": len(current.get(KEYS[2], "").split(",")) if current.get(KEYS[2]) else 0,
            "runtime_test_tenant_configured": bool(current.get(KEYS[3], "")),
        }
        planned_semantics = {"enabled": True, "allowed_tenant_count": 1,
                             "allowed_slug_count": 1, "runtime_test_tenant_configured": True}
        result.update(status="planned", original_sha256=image.digest,
                      predicted_sha256=sha(output),
                      predicted_fingerprint=safe_runtime_config_snapshot(parse(output))["fingerprint"],
                      current_policy=current_semantics, planned_policy=planned_semantics,
                      four_field_diff=[{"key": key, "changed": current.get(key, "") != proposed[key]}
                                       for key in KEYS],
                      non_target_bytes_preserved=True,
                      policy_scope="one_tenant_one_template")
        return result

    def _restart_verify(self, data: bytes, identity: str) -> None:
        failed = False
        for service in SERVICES:
            try:
                self.boundary.restart(service)
            except Exception:
                failed = True
        if failed:
            verification_failure("restart", "SERVICE_RESTART_FAILED")
        self._readiness_verify(data, identity)

    def apply(self, tenant: str, slug: str, *, expected_config_sha256: str) -> dict:
        proposed = policy_values(tenant, slug)
        require(bool(re.fullmatch(r"[0-9a-f]{64}", expected_config_sha256)), "INVALID_EXPECTED_CONFIG_SHA")
        # Reject invalid fixture/config before creating any operation directory.
        first = self._read(self.env)
        require(first.digest == expected_config_sha256, "CONFIG_CONCURRENT_MODIFICATION")
        parse(first.data)
        with self._lock():
            self._unresolved()
            original = self._read(self.env)
            require(original.digest == expected_config_sha256, "CONFIG_CONCURRENT_MODIFICATION")
            values = parse(original.data)
            identity = self._verify(original.data)
            if all(values.get(k, "") == proposed[k] for k in KEYS):
                return {"status": "already_applied", "config_sha256": original.digest,
                        "services_verified": True, "health_verified": True, "gates": self.last_gate}
            output = replace_policy(original.data, proposed)
            operation_id = uuid.uuid4().hex
            backup = self.operations / (operation_id + ".backup")
            # Exclusive, no-follow backup; fsync and readback before prepared.
            fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(original.data)
                stream.flush()
                os.fsync(stream.fileno())
            require(self._read(backup).digest == original.digest, "BACKUP_VERIFY_FAILED")
            journal = {"operation_id": operation_id, "timestamp": utc_now(),
                       "config_path": str(self.env), "backup_path": str(backup),
                       "pre_sha256": original.digest, "post_sha256": sha(output), "size": len(original.data),
                       "mode": original.mode, "uid": original.uid, "gid": original.gid,
                       "application_identity": identity, "target_tenant": tenant, "target_slug": slug}
            self._transition(journal, "prepared")
            replaced = False
            try:
                try:
                    self._atomic(self.env, output, original, original)
                except Exception as exc:
                    stage, code = failure_details(exc, "config_replace")
                    verification_failure(stage, code)
                replaced = True
                self._transition(journal, "config_replaced")
                self._transition(journal, "services_restarting")
                self._restart_verify(output, identity)
                try:
                    require(self._read(self.env).digest == journal["post_sha256"],
                            "CONFIG_CONCURRENT_MODIFICATION")
                except Exception as exc:
                    stage, code = failure_details(exc, "config_cas")
                    verification_failure(stage, code)
                self._transition(journal, "verified")
                return {"status": "verified", "operation_id": operation_id,
                        "pre_sha256": original.digest, "post_sha256": sha(output),
                        "config_fingerprint": safe_runtime_config_snapshot(parse(output))["fingerprint"],
                        "services_verified": True, "health_verified": True, "gates": self.last_gate}
            except Exception as original_error:
                try:
                    current_digest = self._read(self.env).digest
                except Exception:
                    current_digest = None
                if not replaced and current_digest != journal["post_sha256"]:
                    # CAS conflict: leave prepared for explicit investigation,
                    # never overwrite an independently modified file.
                    raise Blocked("CONFIG_CONCURRENT_MODIFICATION") from None
                self._record_failure(journal, "original", original_error, "unknown")
                try:
                    self._transition(journal, journal["state"])
                except Exception:
                    pass
                try:
                    self._restore(journal)
                except Exception as rollback_error:
                    self._record_failure(journal, "rollback", rollback_error, "unknown")
                    try:
                        self._transition(journal, "rollback_failed")
                    except Exception:
                        pass  # An IO failure cannot turn critical rollback into success.
                    raise Blocked("CRITICAL_CONFIG_ROLLBACK_FAILED") from None
                raise Blocked("POLICY_ROLLOUT_FAILED_ROLLED_BACK") from None

    def _restore(self, journal: dict) -> dict:
        require(journal["config_path"] == str(self.env) and
                journal["backup_path"] == str(self.operations / (journal["operation_id"] + ".backup")),
                "INVALID_JOURNAL_PATH")
        backup = self._read(Path(journal["backup_path"]))
        require(backup.digest == journal["pre_sha256"] and len(backup.data) == journal["size"], "BACKUP_IDENTITY_MISMATCH")
        parse(backup.data)
        current = self._read(self.env)
        # Verify release/CWD, not old fingerprint while replacement is active.
        try:
            identity, _ = self.boundary.inspect(require_active=False)
        except VerificationFailure:
            raise
        except Blocked as exc:
            stage = "application_identity" if str(exc) in {
                "APPLICATION_IDENTITY_INVALID", "APPLICATION_IDENTITY_CHANGED",
                "SERVICE_APPLICATION_MISMATCH",
            } else "service_state"
            verification_failure(stage, str(exc))
        if identity != journal["application_identity"]:
            verification_failure("application_identity", "APPLICATION_IDENTITY_CHANGED")
        if current.digest == journal["pre_sha256"]:
            require(journal["state"] in {"prepared", "rolled_back", "rollback_failed", "services_restarting", "config_replaced"},
                    "RESTORE_CONCURRENT_MODIFICATION")
            if journal["state"] == "rolled_back":
                self._verify(backup.data, identity)
                return {"status": "already_restored", "operation_id": journal["operation_id"]}
        else:
            require(current.digest == journal["post_sha256"], "RESTORE_CONCURRENT_MODIFICATION")
        # Persist recovery intent before either replacing bytes or restarting.
        # A current==pre rollback_failed recovery does not rewrite shared env.
        self._transition(journal, "services_restarting")
        if current.digest != journal["pre_sha256"]:
            metadata = Image(b"", 0, 0, journal["mode"], journal["uid"], journal["gid"])
            try:
                self._atomic(self.env, backup.data, metadata, current, "RESTORE_CONCURRENT_MODIFICATION")
            except Exception as exc:
                stage, code = failure_details(exc, "config_replace")
                verification_failure(stage, code)
        self._restart_verify(backup.data, identity)
        try:
            require(self._read(self.env).digest == journal["pre_sha256"], "RESTORE_CONCURRENT_MODIFICATION")
        except Exception as exc:
            stage, code = failure_details(exc, "config_cas")
            verification_failure(stage, code)
        self._transition(journal, "rolled_back")
        return {"status": "rolled_back", "operation_id": journal["operation_id"],
                "exact_bytes_restored": True, "config_rewritten": current.digest != journal["pre_sha256"]}

    def restore(self, operation_id: str) -> dict:
        require(bool(re.fullmatch(r"[0-9a-f]{32}", operation_id)), "INVALID_OPERATION_ID")
        with self._lock():
            self._unresolved(operation_id)
            journal = next((j for j in self._journals() if j["operation_id"] == operation_id), None)
            require(journal is not None, "OPERATION_NOT_FOUND")
            try:
                return self._restore(journal)
            except Blocked as exc:
                if str(exc) in {"RESTORE_CONCURRENT_MODIFICATION", "APPLICATION_IDENTITY_CHANGED",
                                "BACKUP_IDENTITY_MISMATCH", "INVALID_JOURNAL_PATH"}:
                    raise
                self._record_failure(journal, "rollback", exc, "unknown")
                try:
                    self._transition(journal, "rollback_failed")
                except Exception:
                    pass
                raise Blocked("CRITICAL_CONFIG_ROLLBACK_FAILED") from None
            except Exception as exc:
                self._record_failure(journal, "rollback", exc, "unknown")
                try:
                    self._transition(journal, "rollback_failed")
                except Exception:
                    pass
                raise Blocked("CRITICAL_CONFIG_ROLLBACK_FAILED") from None


def main(argv=None, *, factory=Rollout) -> int:
    parser = SafeParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    for command in ("plan", "apply"):
        sub = commands.add_parser(command)
        sub.add_argument("--tenant", required=True)
        sub.add_argument("--slug", required=True)
        if command == "apply":
            sub.add_argument("--expected-config-sha256", required=True,
                             help="Original config SHA from the read-only plan; fail closed on drift")
    commands.add_parser("restore").add_argument("--operation-id", required=True)
    args = parser.parse_args(argv)
    try:
        rollout = factory()
        if args.command == "restore":
            result = rollout.restore(args.operation_id)
        elif args.command == "apply":
            result = rollout.apply(args.tenant, args.slug, expected_config_sha256=args.expected_config_sha256)
        elif args.command == "plan":
            result = rollout.plan(args.tenant, args.slug)
        else:
            result = rollout.status()
        print(json.dumps(result, sort_keys=True))
        return 0
    except Blocked as exc:
        print(json.dumps({"status": "blocked", "code": str(exc)}))
        return 3 if str(exc) == "CRITICAL_CONFIG_ROLLBACK_FAILED" else 2
    except Exception:
        print(json.dumps({"status": "blocked", "code": "POLICY_CONTROL_IO_FAILED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
