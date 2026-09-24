"""Reproducible Linux-only release-test tools; never a Production entry point.

The Redis E2E owns a private PostgreSQL cluster, Redis Unix socket, API, MCP,
and Worker. Nothing is connected to a Production endpoint or release-current.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import urlopen


PROJECT = Path(__file__).resolve().parents[1]
CONTRACT = PROJECT / "deploy/release_test_environment.json"
RUNTIME_CACHE = Path.home() / ".cache/enterprise-agent-test-runtime"
ISOLATED_PARENT = Path("/private/tmp")
ROOT_PREFIX = "ky-web-stage2-readiness."
MARKER = "ky-web-stage2-isolated-v1"
PG_PORT = 54330  # Fixed by scripts/stage2_isolation.py, Unix socket only.


def contract() -> dict:
    data = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if not re.fullmatch(r"\d+\.\d+\.\d+", data["node"]["version"]):
        raise RuntimeError("Invalid pinned Node version")
    if not re.fullmatch(r"\d+\.\d+\.\d+", data["redis"]["version"]):
        raise RuntimeError("Invalid pinned Redis version")
    for section, key in (("node", "linux_x64_sha256"), ("redis", "source_sha256")):
        if not re.fullmatch(r"[0-9a-f]{64}", data[section][key]):
            raise RuntimeError("Invalid pinned archive checksum")
    return data


def linux_only() -> None:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise RuntimeError("Release test toolchain requires native Linux x64")


def _download_verified(url: str, expected: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
        return
    temporary = destination.with_name(destination.name + ".partial")
    try:
        subprocess.run(["curl", "--fail", "--location", "--silent", "--show-error", "--max-time", "180",
                        "--proto", "=https", "--tlsv1.2", "--output", str(temporary), url], check=True)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Official archive SHA-256 mismatch: {destination.name}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _extract_verified(archive: Path, destination: Path, top_directory: str) -> None:
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != top_directory:
                raise RuntimeError("Unexpected path in verified toolchain archive")
        source.extractall(destination, filter="data")


def prepare_node(data: dict | None = None) -> Path:
    linux_only()
    data = data or contract()
    version = data["node"]["version"]
    name = f"node-v{version}-linux-x64"
    binary = RUNTIME_CACHE / name / "bin/node"
    archive = RUNTIME_CACHE / "downloads" / f"{name}.tar.xz"
    _download_verified(f"https://nodejs.org/dist/v{version}/{archive.name}", data["node"]["linux_x64_sha256"], archive)
    if binary.is_file():
        verify_node(binary, version)
        with tarfile.open(archive) as source:
            member = source.extractfile(f"{name}/bin/node")
            if member is None:
                raise RuntimeError("Verified Node archive has no executable")
            digest = hashlib.sha256()
            while chunk := member.read(1024 * 1024):
                digest.update(chunk)
        if hashlib.sha256(binary.read_bytes()).hexdigest() != digest.hexdigest():
            raise RuntimeError("Cached Node executable identity mismatch")
        return binary
    RUNTIME_CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="node-verified-", dir=RUNTIME_CACHE) as directory:
        _extract_verified(archive, Path(directory), name)
        staged = Path(directory) / name
        verify_node(staged / "bin/node", version)
        staged.rename(RUNTIME_CACHE / name)
    return binary


def verify_node(binary: Path, expected: str) -> None:
    actual = subprocess.check_output([str(binary), "--version"], text=True).strip()
    if actual != f"v{expected}":
        raise RuntimeError(f"Node version mismatch: expected v{expected}, got {actual}")


def prepare_redis(data: dict | None = None) -> tuple[Path, Path]:
    linux_only()
    data = data or contract()
    version = data["redis"]["version"]
    target = RUNTIME_CACHE / f"redis-{version}/bin"
    server, cli = target / "redis-server", target / "redis-cli"
    archive = RUNTIME_CACHE / "downloads" / f"redis-{version}.tar.gz"
    _download_verified(f"https://download.redis.io/releases/{archive.name}", data["redis"]["source_sha256"], archive)
    if server.is_file() and cli.is_file():
        verify_redis(server, version)
        return server, cli
    RUNTIME_CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="redis-verified-", dir=RUNTIME_CACHE) as directory:
        work = Path(directory)
        _extract_verified(archive, work, f"redis-{version}")
        source = work / f"redis-{version}"
        subprocess.run(["make", "-C", str(source), "-j2", "BUILD_TLS=no"], check=True,
                       stdout=subprocess.DEVNULL)
        staged = work / "bin"
        staged.mkdir()
        shutil.copy2(source / "src/redis-server", staged / "redis-server")
        shutil.copy2(source / "src/redis-cli", staged / "redis-cli")
        verify_redis(staged / "redis-server", version)
        target.parent.mkdir(parents=True, exist_ok=True)
        staged.rename(target)
    return server, cli


def verify_redis(binary: Path, expected: str) -> None:
    result = subprocess.check_output([str(binary), "--version"], text=True)
    if f"v={expected} " not in result:
        raise RuntimeError(f"Redis version mismatch: expected {expected}")


def _available_port() -> int:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        return candidate.getsockname()[1]


class IsolatedServices:
    """Own exactly one marked temporary Stage 2 cluster and all its children."""

    def __init__(self, credential_file: Path | None = None):
        self.root: Path | None = None
        self.credential_file = credential_file
        self.children: list[tuple[str, subprocess.Popen]] = []
        self.logs = []
        self.pg_started = False
        self.redis_server: Path | None = None
        self.redis_cli: Path | None = None
        self.manifest: Path | None = None
        self.api_port = 0

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    def _safe_root(self) -> Path:
        root = self.root
        if root is None or root.is_symlink() or root.parent != ISOLATED_PARENT or not root.name.startswith(ROOT_PREFIX):
            raise RuntimeError("Unsafe isolated release-test root")
        if root.resolve() != root or root.stat().st_uid != os.getuid() or (root / "stage2-isolated.marker").read_text().strip() != MARKER:
            raise RuntimeError("Isolated release-test root identity mismatch")
        return root

    def start(self) -> None:
        linux_only()
        self.redis_server, self.redis_cli = prepare_redis()
        pg_bin = RUNTIME_CACHE / "postgresql-16.6/bin"
        for name in ("initdb", "pg_ctl", "createdb"):
            if not (pg_bin / name).is_file():
                raise RuntimeError(f"Isolated PostgreSQL 16.6 prerequisite missing: {name}")
        ISOLATED_PARENT.mkdir(mode=0o700, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix=ROOT_PREFIX, dir=ISOLATED_PARENT))
        self.root.chmod(0o700)
        (self.root / "stage2-isolated.marker").write_text(MARKER, encoding="utf-8")
        try:
            self._start_pg(pg_bin)
            self._start_redis()
            self._manifest()
            self.validate_manifest()
        except BaseException:
            self.stop()
            raise

    def _start_pg(self, pg_bin: Path) -> None:
        root = self._safe_root()
        (root / "pg/socket").mkdir(parents=True, mode=0o700)
        (root / "pg/socket").chmod(0o700)
        cluster = root / "pg/cluster"
        subprocess.run([str(pg_bin / "initdb"), "-D", str(cluster), "-U", "stage1_fixture",
                        "-A", "trust", "--encoding=UTF8", "--locale=C", "--no-instructions"],
                       check=True, stdout=subprocess.DEVNULL)
        with (cluster / "postgresql.conf").open("a", encoding="utf-8") as config:
            config.write(f"\nlisten_addresses = ''\nport = {PG_PORT}\nunix_socket_directories = '{root / 'pg/socket'}'\nunix_socket_permissions = 0700\n")
        subprocess.run([str(pg_bin / "pg_ctl"), "-D", str(cluster), "-l", str(root / "postgres.log"),
                        "-w", "-t", "30", "start"], check=True, stdout=subprocess.DEVNULL)
        self.pg_started = True
        database = "stage25_" + root.name.rsplit(".", 1)[1].lower().replace("-", "_")
        self.database = database
        subprocess.run([str(pg_bin / "createdb"), "-h", str(root / "pg/socket"), "-p", str(PG_PORT),
                        "-U", "stage1_fixture", database], check=True)

    def _start_redis(self) -> None:
        root = self._safe_root()
        (root / "redis-data").mkdir(mode=0o700)
        output = (root / "redis.log").open("w", encoding="utf-8")
        self.logs.append(output)
        process = subprocess.Popen([str(self.redis_server), "--port", "0", "--unixsocket", str(root / "redis.sock"),
                                    "--unixsocketperm", "700", "--save", "", "--appendonly", "no",
                                    "--dir", str(root / "redis-data"), "--daemonize", "no"],
                                   stdout=output, stderr=subprocess.STDOUT)
        self.children.append(("redis", process))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Isolated Redis exited during startup")
            if (root / "redis.sock").exists():
                pong = subprocess.run([str(self.redis_cli), "-s", str(root / "redis.sock"), "PING"],
                                      capture_output=True, text=True)
                if pong.returncode == 0 and pong.stdout.strip() == "PONG":
                    info = subprocess.check_output([str(self.redis_cli), "-s", str(root / "redis.sock"), "INFO", "server"], text=True)
                    expected = contract()["redis"]["version"]
                    if f"redis_version:{expected}" not in info:
                        raise RuntimeError("Isolated Redis runtime version mismatch")
                    return
            time.sleep(0.1)
        raise RuntimeError("Isolated Redis health timeout")

    def _manifest(self) -> None:
        root = self._safe_root()
        template = PROJECT / contract()["manifest_template"]
        config = json.loads(template.read_text(encoding="utf-8"))
        self.api_port, mcp_port = _available_port(), _available_port()
        tenant = "stage25-test-tenant"
        db_url = f"postgresql:///{self.database}?" + urlencode({"host": str(root / "pg/socket"), "port": PG_PORT, "user": "stage1_fixture"})
        values = {
            "__RUNTIME_ROOT__": str(root), "__API_PORT__": self.api_port,
            "__TEST_CREDENTIAL_FILE__": str(self.credential_file or root / "unused-credential"),
            "__DATABASE_URL__": db_url, "__DATA_DIR__": str(root / "data"),
            "__OBJECT_DIR__": str(root / "objects"), "__MCP_URL__": f"http://127.0.0.1:{mcp_port}/mcp",
            "__TEST_TENANT__": tenant, "__REDIS_URL__": f"unix://{root / 'redis.sock'}?db=0",
            "__NAMESPACE__": root.name,
        }
        def render(value):
            if isinstance(value, dict):
                return {key: render(item) for key, item in value.items()}
            return values.get(value, value) if isinstance(value, str) else value
        config = render(config)
        self.manifest = root / "redis-e2e.manifest.json"
        self.manifest.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        self.config = config

    def validate_manifest(self) -> None:
        self._safe_root()
        result = subprocess.run([sys.executable, str(PROJECT / "scripts/stage2_preview.py"),
                                 "check", "--config", str(self.manifest)],
                                cwd=PROJECT, env=self._child_env(), capture_output=True, text=True)
        if result.returncode != 0 or '"database_is_isolated": true' not in result.stdout:
            raise RuntimeError(f"Stage 2 isolation gate failed: {result.stdout}{result.stderr}")

    def _child_env(self) -> dict:
        keys = ("PATH", "HOME", "LANG", "TMPDIR", "PYTHONDONTWRITEBYTECODE")
        result = {key: os.environ[key] for key in keys if key in os.environ}
        result["PATH"] = str(prepare_node().parent) + os.pathsep + result.get("PATH", "")
        return result

    def start_app(self) -> None:
        if (self.credential_file is None or not self.credential_file.is_file()
                or self.credential_file.stat().st_uid != os.getuid()
                or self.credential_file.stat().st_mode & 0o077):
            raise RuntimeError("Non-Production Stage 2 credential file with mode 0600 required")
        root = self._safe_root()
        self.migrate()
        for role in ("provision", "mcp", "api", "worker"):
            if role == "provision":
                subprocess.run([sys.executable, str(PROJECT / "scripts/stage2_preview.py"), role,
                                "--config", str(self.manifest)], cwd=PROJECT, env=self._child_env(), check=True)
                continue
            output = (root / f"{role}.log").open("w", encoding="utf-8")
            self.logs.append(output)
            process = subprocess.Popen([sys.executable, str(PROJECT / "scripts/stage2_preview.py"), role,
                                        "--config", str(self.manifest)], cwd=PROJECT, env=self._child_env(),
                                       stdout=output, stderr=subprocess.STDOUT)
            self.children.append((role, process))
        deadline = time.monotonic() + 40
        from urllib.error import URLError
        while time.monotonic() < deadline:
            for name, process in self.children:
                if process.poll() is not None:
                    raise RuntimeError(f"Isolated {name} exited before readiness; see private test log")
            try:
                with urlopen(f"http://127.0.0.1:{self.api_port}/api/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except (URLError, TimeoutError):
                time.sleep(0.25)
        raise RuntimeError("Isolated API health timeout")

    def migrate(self) -> None:
        """Apply the project's formal ordered migration runner to this private DB."""
        self.validate_manifest()
        subprocess.run([sys.executable, str(PROJECT / "scripts/stage2_preview.py"), "migrate",
                        "--config", str(self.manifest)], cwd=PROJECT, env=self._child_env(),
                       check=True, stdout=subprocess.DEVNULL)

    def run_pytest(self, arguments: list[str]) -> int:
        self.start_app()
        environment = self._child_env()
        environment["STAGE25_REDIS_E2E_CONFIG"] = str(self.manifest)
        environment["STAGE25_API_PID"] = str(next(process.pid for name, process in self.children if name == "api"))
        environment["PYTHONUTF8"] = "1"
        # Stage 1 and historical-artifact fixture locations are caller-supplied and not in the Stage 2 manifest.
        for name in ("STAGE1_POSTGRES_ROOT", "ROLLBACK_OLD_ARTIFACT_ROOT"):
            if name in os.environ:
                environment[name] = os.environ[name]
        return subprocess.run([sys.executable, "-m", "pytest", *arguments], cwd=PROJECT, env=environment).returncode

    def stop(self) -> None:
        if self.root is None:
            return
        root = self._safe_root()
        for _, process in reversed(self.children):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        self.children.clear()
        if self.pg_started:
            pg_ctl = RUNTIME_CACHE / "postgresql-16.6/bin/pg_ctl"
            subprocess.run([str(pg_ctl), "-D", str(root / "pg/cluster"), "-m", "immediate", "-w", "-t", "30", "stop"],
                           check=True, stdout=subprocess.DEVNULL)
            self.pg_started = False
        for output in self.logs:
            output.close()
        self.logs.clear()
        # Only a root created by this instance, still marked and owned, is removable.
        shutil.rmtree(root)
        self.root = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    run = sub.add_parser("run")
    run.add_argument("--credential-file", type=Path, required=True)
    run.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == "prepare":
        node = prepare_node()
        redis, _ = prepare_redis()
        print(json.dumps({"node": str(node), "node_version": contract()["node"]["version"],
                          "redis": str(redis), "redis_version": contract()["redis"]["version"]}))
        return 0
    arguments = args.pytest_args
    if arguments and arguments[0] == "--":
        arguments = arguments[1:]
    with IsolatedServices(args.credential_file) as isolated:
        return isolated.run_pytest(arguments or ["-q", "-ra"])


if __name__ == "__main__":
    raise SystemExit(main())
