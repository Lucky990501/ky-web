"""Locked, post-staging fresh-start probe of the exact predecessor.

The only Redis instance used by the transient Worker is private and empty.
No transient service replaces a systemd unit or touches release-current.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time


class ProbeBlocked(RuntimeError):
    """A constant, non-sensitive release gate category."""


def require(value: bool, category: str) -> None:
    if not value:
        raise ProbeBlocked(category)


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_port(port: int, process: subprocess.Popen, category: str, seconds: float = 75) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        require(process.poll() is None, category)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise ProbeBlocked(category)


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if process.stdout is not None:
        process.stdout.close()


def _environment(base: Path, predecessor_root: Path, redis_socket: Path, mcp_port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": str(predecessor_root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "ENTERPRISE_POC_DATA_DIR": str(base / "shared/runtime-data"),
        "ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA": "false",
        "ENTERPRISE_POC_TASK_QUEUE": "redis",
        "ENTERPRISE_POC_TASK_QUEUE_NAMESPACE": "release-predecessor-probe",
        "REDIS_URL": "unix://" + str(redis_socket) + "?db=0",
        "ENTERPRISE_POC_MCP_HOST": "127.0.0.1",
        "ENTERPRISE_POC_MCP_PORT": str(mcp_port),
        "ENTERPRISE_POC_MCP_URL": f"http://127.0.0.1:{mcp_port}/mcp",
    })
    require(env.get("ENTERPRISE_POC_DATABASE_URL", "").startswith("postgresql://"), "PREDECESSOR_DATABASE_CONFIG")
    return env


def _registry_and_agents(python: Path, root: Path, env: dict[str, str], tenant_id: str) -> None:
    # Import and initialize the installed predecessor in a NEW interpreter.
    # Its Registry consumer must tolerate the extra dormant immutable package.
    code = '''
import json, os
from pathlib import Path
from app.store import POCStore
from app.skill_registry import SkillRegistry
from app.product_store import ProductStore
from app.agent_catalog import CATALOG
root = Path.cwd()
store = POCStore(os.environ["ENTERPRISE_POC_DATABASE_URL"])
registry = SkillRegistry(store, Path(os.environ["ENTERPRISE_POC_DATA_DIR"]) / "skill-registry", root / "skill_packages")
registry.initialize()
registry.verify_bootstrap()
print("PREDECESSOR_REGISTRY_READY", flush=True)
product = ProductStore(store)
tenant = os.environ["RELEASE_PROBE_TENANT_ID"]
visible = {item["id"] for item in product.agents(tenant)}
if not set(CATALOG) <= visible:
    raise RuntimeError("existing_agents_missing")
for item in CATALOG.values():
    if product.resolve_agent_reference(item.id) != item.id:
        raise RuntimeError("existing_agent_resolution")
try:
    product.resolve_agent_reference("wechat-official-account-writing")
except LookupError:
    pass
else:
    raise AssertionError("wechat_agent_resolvable")
if not all(item["slug"] != "wechat-official-account-writing" for item in product.agents(tenant)):
    raise RuntimeError("wechat_agent_visible")
with registry._read_connection() as conn:
    rows = conn.execute("SELECT s.slug,v.version FROM agent_skill_bindings b JOIN skills s ON s.id=b.skill_id JOIN skill_versions v ON v.id=b.skill_version_id").fetchall()
if not rows or not all((registry.published_root / row["slug"] / row["version"] / "SKILL.md").is_file() for row in rows):
    raise RuntimeError("existing_skill_resolution")
print(json.dumps({"registry":"PASS","agents":"PASS","bindings":"PASS","skills":"PASS","wechat":"ABSENT"}))
'''
    child_env = dict(env, RELEASE_PROBE_TENANT_ID=tenant_id)
    try:
        result = subprocess.run([str(python), "-c", code], cwd=root, env=child_env,
                                capture_output=True, text=True, timeout=45)
        if result.returncode != 0:
            raise ProbeBlocked("PREDECESSOR_EXISTING_AGENT_RESOLUTION" if "PREDECESSOR_REGISTRY_READY" in result.stdout
                               else "PREDECESSOR_REGISTRY_INIT")
        report = json.loads(result.stdout.strip().splitlines()[-1])
        require(report == {"registry": "PASS", "agents": "PASS", "bindings": "PASS",
                           "skills": "PASS", "wechat": "ABSENT"}, "PREDECESSOR_EXISTING_AGENT_RESOLUTION")
    except (IndexError, ValueError, subprocess.TimeoutExpired) as exc:
        raise ProbeBlocked("PREDECESSOR_REGISTRY_OR_AGENTS") from exc


def _api(python: Path, root: Path, env: dict[str, str]) -> None:
    port = _port()
    process = subprocess.Popen([str(python), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                "--port", str(port)], cwd=root, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_port(port, process, "PREDECESSOR_API_FRESH_START")
        import httpx
        response = httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=8, trust_env=False)
        require(response.status_code == 200 and response.json().get("status") == "ok",
                "PREDECESSOR_API_FRESH_START")
    except (OSError, ValueError) as exc:
        raise ProbeBlocked("PREDECESSOR_API_FRESH_START") from exc
    finally:
        _stop(process)


def _mcp(python: Path, root: Path, env: dict[str, str], port: int) -> None:
    process = subprocess.Popen([str(python), "-m", "app.platform_mcp.server"], cwd=root, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_port(port, process, "PREDECESSOR_MCP_FRESH_START", seconds=25)
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def check():
            async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    await session.send_ping()
                    tools = await session.list_tools()
                    require({"enterprise_config_get", "knowledge_search", "asset_search"}
                            <= {tool.name for tool in tools.tools}, "PREDECESSOR_MCP_FRESH_START")

        asyncio.run(asyncio.wait_for(check(), timeout=20))
    except Exception as exc:
        raise ProbeBlocked("PREDECESSOR_MCP_FRESH_START") from exc
    finally:
        _stop(process)


def _worker(python: Path, root: Path, env: dict[str, str], redis_socket: Path) -> None:
    process = subprocess.Popen([str(python), "-m", "app.worker"], cwd=root, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.monotonic() + 20
        ready = False
        while time.monotonic() < deadline:
            require(process.poll() is None, "PREDECESSOR_WORKER_FRESH_START")
            selected, _, _ = select.select([process.stdout], [], [], 0.5)
            if selected and "worker ready" in process.stdout.readline():
                ready = True
                break
        require(ready and process.poll() is None, "PREDECESSOR_WORKER_FRESH_START")
        from redis import Redis
        queue = Redis.from_url("unix://" + str(redis_socket) + "?db=0")
        try:
            require(queue.llen("release-predecessor-probe:tasks:pending") == 0
                    and queue.llen("release-predecessor-probe:tasks:processing") == 0
                    and queue.llen("release-predecessor-probe:knowledge:pending") == 0
                    and queue.llen("release-predecessor-probe:knowledge:processing") == 0,
                    "PREDECESSOR_WORKER_QUEUE_NOT_ISOLATED")
        finally:
            queue.close()
    finally:
        _stop(process)


def _redis_server(directory: Path) -> tuple[subprocess.Popen, Path]:
    binary = shutil.which("redis-server")
    require(bool(binary), "PREDECESSOR_PROBE_REDIS_UNAVAILABLE")
    socket_path = directory / "redis.sock"
    process = subprocess.Popen([binary, "--port", "0", "--unixsocket", str(socket_path),
                                "--unixsocketperm", "700", "--save", "", "--appendonly", "no",
                                "--dir", str(directory), "--daemonize", "no"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 12
    try:
        while time.monotonic() < deadline:
            require(process.poll() is None, "PREDECESSOR_PROBE_REDIS_START")
            if socket_path.is_socket():
                from redis import Redis
                queue = Redis.from_url("unix://" + str(socket_path) + "?db=0")
                try:
                    require(queue.ping(), "PREDECESSOR_PROBE_REDIS_START")
                finally:
                    queue.close()
                return process, socket_path
            time.sleep(0.1)
        raise ProbeBlocked("PREDECESSOR_PROBE_REDIS_START")
    except BaseException:
        _stop(process)
        raise


def probe(base: Path, candidate: Path, predecessor: Path, snapshot: Path) -> dict:
    from scripts import release_verify
    from scripts.release_binding_transition import package_only_state

    manifest = json.loads(candidate.read_text())
    require("skill_package_staging" in manifest and "binding_transition" not in manifest,
            "POST_STAGING_DECLARATION")
    prior = json.loads(predecessor.read_text())
    root = base / "releases" / prior["release_id"] / "enterprise_agent_poc"
    require((base / "release-current").resolve() == root, "POST_STAGING_PREDECESSOR_CHANGED")
    python = base / "venv/bin/python"
    require(python.is_file() and os.access(python, os.X_OK), "PREDECESSOR_PROBE_RUNTIME")
    require(shutil.which("redis-server") is not None, "PREDECESSOR_PROBE_REDIS_UNAVAILABLE")
    # Formal pre-switch state verifier checks live services, schema, bindings,
    # package identity, Agent absence and exact predecessor archive identity.
    before = release_verify.verify_state(base, candidate, predecessor, snapshot, rollback=True)
    registry = release_verify.registry_for(base, root / "skill_packages")
    require(package_only_state(registry, manifest["skill_package_staging"]["package"], required=True)
            == "EXACT", "POST_STAGING_PACKAGE_IDENTITY")
    release_verify.phase_a_absence(registry, manifest["deferred_skill"])
    tenant_id = release_verify.smoke_config(base)["tenant_id"]
    with tempfile.TemporaryDirectory(prefix="predecessor-probe-", dir=snapshot.parent) as name:
        directory = Path(name)
        directory.chmod(0o700)
        redis, redis_socket = _redis_server(directory)
        try:
            mcp_port = _port()
            env = _environment(base, root, redis_socket, mcp_port)
            _registry_and_agents(python, root, env, tenant_id)
            _api(python, root, env)
            _mcp(python, root, env, mcp_port)
            _worker(python, root, env, redis_socket)
        finally:
            _stop(redis)
    after = release_verify.verify_state(base, candidate, predecessor, snapshot, rollback=True)
    require(before["campaign_binding"] == after["campaign_binding"], "POST_STAGING_BINDING_DRIFT")
    return {"status": "post_staging_exact_predecessor_passed", "release_id": prior["release_id"],
            "schema": "014", "package": "EXACT", "registry": "PASS", "api": "PASS",
            "mcp": "PASS", "worker": "PASS", "existing_agents": "PASS",
            "bindings": "UNCHANGED", "wechat_agent": "ABSENT", "live_health": "PASS"}
