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
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
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


class Blocked(Exception):
    """Only constant, non-sensitive error codes may cross the public boundary."""


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default error echoes caller-supplied values. Never do so.
        self.exit(2, "POLICY_CONTROL_ARGUMENTS_INVALID\n")


def require(ok: bool, code: str) -> None:
    if not ok:
        raise Blocked(code)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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

    def _show(self, service: str) -> dict[str, str]:
        result = subprocess.run(["systemctl", "show", service,
                                 "--property=MainPID,ActiveState,WorkingDirectory"],
                                capture_output=True, text=True, timeout=15)
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
        for service in SERVICES:
            props = self._show(service)
            active = props.get("ActiveState") == "active" and props.get("MainPID", "0").isdigit() and int(props["MainPID"]) > 0
            self.service_states[service] = props.get("ActiveState", "unknown")
            require(active or not require_active, "SERVICE_INACTIVE")
            if not active:
                environments.append({})
                continue
            require(props.get("WorkingDirectory") == str(source), "SERVICE_APPLICATION_MISMATCH")
            proc = self.proc_root / props["MainPID"]
            require((proc / "cwd").resolve(strict=True) == source,
                    "SERVICE_APPLICATION_MISMATCH")
            raw = (proc / "environ").read_bytes()
            environments.append(dict(part.decode().split("=", 1) for part in raw.split(b"\0") if b"=" in part))
        return identity, environments

    def restart(self, service: str) -> None:
        result = subprocess.run(["systemctl", "restart", service], capture_output=True, timeout=45)
        require(result.returncode == 0, "SERVICE_RESTART_FAILED")

    def health(self) -> bool:
        for url in ("http://127.0.0.1:18090/api/health", "https://workbench.luckio.cn/api/health"):
            with urllib.request.urlopen(url, timeout=15) as response:
                payload = json.loads(response.read(65536))
            if not all(payload.get(k) == v for k, v in (
                    ("status", "ok"), ("knowledge", "ok"), ("environment", "production"))):
                return False
        return True


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
                    journal.get("state") in TERMINAL | {"prepared", "config_replaced", "services_restarting", "rollback_failed"},
                    "INVALID_JOURNAL")
            require(set(journal) == {"operation_id", "timestamp", "config_path", "backup_path", "pre_sha256",
                                    "post_sha256", "size", "mode", "uid", "gid", "application_identity",
                                    "target_tenant", "target_slug", "state"} and
                    all(isinstance(journal[k], str) and re.fullmatch(r"[0-9a-f]{64}", journal[k])
                        for k in ("pre_sha256", "post_sha256")) and
                    journal["mode"] in {0o400, 0o600} and journal["uid"] == self.uid and
                    type(journal["gid"]) is int and journal["gid"] >= 0 and
                    type(journal["size"]) is int and 0 <= journal["size"] <= 2 * 1024 * 1024,
                    "INVALID_JOURNAL")
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
        journal["state"] = state
        data = json.dumps(journal, sort_keys=True, separators=(",", ":")).encode()
        self._atomic(self.operations / (journal["operation_id"] + ".json"), data,
                     Image(b"", 0, 0, 0o600, self.uid, os.getegid()))

    def _verify(self, data: bytes, identity: str | None = None) -> str:
        current_identity, environments = self.boundary.inspect()
        require(identity is None or identity == current_identity, "APPLICATION_IDENTITY_CHANGED")
        require(len(environments) == len(SERVICES), "SERVICE_QUERY_FAILED")
        expected = parse(data)
        require(expected.get("APP_ENV") == "production", "ENVIRONMENT_NOT_PRODUCTION")
        comparisons = [compare(expected, current) for current in environments]
        require(all(result["matches"] and
                    all(current.get(k, "") == expected.get(k, "") for k in KEYS)
                    for result, current in zip(comparisons, environments)), "CONFIG_FINGERPRINT_MISMATCH")
        require(self.boundary.health(), "HEALTH_GATE_FAILED")
        self.last_gate = {"shared_fingerprint": comparisons[0]["expected_fingerprint"],
                          "process_fingerprints": {service: result["runtime_fingerprint"]
                                                   for service, result in zip(SERVICES, comparisons)},
                          "config_matches": True, "policy_matches": True, "health_verified": True,
                          "application": getattr(self.boundary, "identity_summary",
                                                 {"identity_sha256": current_identity}),
                          "service_states": getattr(self.boundary, "service_states",
                                                    dict.fromkeys(SERVICES, "active"))}
        return current_identity

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
        require(not failed, "SERVICE_RESTART_FAILED")
        self._verify(data, identity)

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
            journal = {"operation_id": operation_id, "timestamp": datetime.now(timezone.utc).isoformat(),
                       "config_path": str(self.env), "backup_path": str(backup),
                       "pre_sha256": original.digest, "post_sha256": sha(output), "size": len(original.data),
                       "mode": original.mode, "uid": original.uid, "gid": original.gid,
                       "application_identity": identity, "target_tenant": tenant, "target_slug": slug}
            self._write_journal(journal, "prepared")
            replaced = False
            try:
                self._atomic(self.env, output, original, original)
                replaced = True
                self._write_journal(journal, "config_replaced")
                self._write_journal(journal, "services_restarting")
                self._restart_verify(output, identity)
                require(self._read(self.env).digest == journal["post_sha256"], "CONFIG_CONCURRENT_MODIFICATION")
                self._write_journal(journal, "verified")
                return {"status": "verified", "operation_id": operation_id,
                        "pre_sha256": original.digest, "post_sha256": sha(output),
                        "config_fingerprint": safe_runtime_config_snapshot(parse(output))["fingerprint"],
                        "services_verified": True, "health_verified": True, "gates": self.last_gate}
            except Exception:
                try:
                    current_digest = self._read(self.env).digest
                except Exception:
                    current_digest = None
                if not replaced and current_digest != journal["post_sha256"]:
                    # CAS conflict: leave prepared for explicit investigation,
                    # never overwrite an independently modified file.
                    raise Blocked("CONFIG_CONCURRENT_MODIFICATION") from None
                try:
                    self._restore(journal)
                except Exception:
                    try:
                        self._write_journal(journal, "rollback_failed")
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
        identity, _ = self.boundary.inspect(require_active=False)
        require(identity == journal["application_identity"], "APPLICATION_IDENTITY_CHANGED")
        if current.digest == journal["pre_sha256"]:
            require(journal["state"] in {"prepared", "rolled_back", "rollback_failed", "services_restarting", "config_replaced"},
                    "RESTORE_CONCURRENT_MODIFICATION")
            if journal["state"] == "rolled_back":
                self._verify(backup.data, identity)
                return {"status": "already_restored", "operation_id": journal["operation_id"]}
        else:
            require(current.digest == journal["post_sha256"], "RESTORE_CONCURRENT_MODIFICATION")
            # Persist recovery intent before restoring bytes. A crash between
            # replacement and restart must not leave a 'verified' journal which
            # could be mistaken for an unrelated manual pre-SHA modification.
            self._write_journal(journal, "services_restarting")
            metadata = Image(b"", 0, 0, journal["mode"], journal["uid"], journal["gid"])
            self._atomic(self.env, backup.data, metadata, current, "RESTORE_CONCURRENT_MODIFICATION")
        self._restart_verify(backup.data, identity)
        require(self._read(self.env).digest == journal["pre_sha256"], "RESTORE_CONCURRENT_MODIFICATION")
        self._write_journal(journal, "rolled_back")
        return {"status": "rolled_back", "operation_id": journal["operation_id"], "exact_bytes_restored": True}

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
                try:
                    self._write_journal(journal, "rollback_failed")
                except Exception:
                    pass
                raise Blocked("CRITICAL_CONFIG_ROLLBACK_FAILED") from None
            except Exception:
                try:
                    self._write_journal(journal, "rollback_failed")
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
