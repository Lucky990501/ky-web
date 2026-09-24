"""E1-E10: Linux release-test toolchain and private Redis lifecycle contract."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.release_test_environment import (
    IsolatedServices, PROJECT, contract, prepare_node, verify_node,
)


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux release-test environment only")


def test_e1_node_single_source():
    assert contract()["node"]["version"] == "24.21.0"


def test_e2_node_executable_version():
    node = prepare_node()
    assert subprocess.check_output([str(node), "--version"], text=True).strip() == "v24.21.0"


def test_e3_wrong_node_version_blocks(tmp_path):
    wrong = tmp_path / "node"
    wrong.write_text("#!/bin/sh\necho v24.20.0\n", encoding="utf-8")
    wrong.chmod(0o700)
    with pytest.raises(RuntimeError, match="Node version mismatch"):
        verify_node(wrong, contract()["node"]["version"])


def test_e4_frontend_suite_uses_native_node():
    suite = PROJECT / "tests/workbench_routes.test.cjs"
    result = subprocess.run([str(prepare_node()), "--test", str(suite)], cwd=PROJECT,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="module")
def isolated():
    with IsolatedServices() as services:
        yield services


def test_e5_manifest_passes_stage2_gate(isolated):
    isolated.validate_manifest()
    manifest = json.loads(isolated.manifest.read_text(encoding="utf-8"))
    assert manifest["mode"] == "redis-postgres"
    assert manifest["environment"]["ENTERPRISE_POC_TASK_QUEUE_NAMESPACE"] == isolated.root.name


def test_e6_redis_runtime_matches_production_pin(isolated):
    assert contract()["redis"]["version"] == "7.0.15"
    info = subprocess.check_output([str(isolated.redis_cli), "-s", str(isolated.root / "redis.sock"),
                                    "INFO", "server"], text=True)
    assert "redis_version:7.0.15" in info


def test_e7_production_redis_endpoint_cannot_pass_gate(isolated):
    config = json.loads(isolated.manifest.read_text(encoding="utf-8"))
    config["environment"]["REDIS_URL"] = "redis://127.0.0.1:6379/0"
    unsafe = isolated.root / "unsafe-manifest.json"
    unsafe.write_text(json.dumps(config), encoding="utf-8")
    result = subprocess.run([sys.executable, str(PROJECT / "scripts/stage2_preview.py"),
                             "check", "--config", str(unsafe)], cwd=PROJECT,
                            env=isolated._child_env(), capture_output=True, text=True)
    assert result.returncode != 0
    assert "Private PG / Redis queue configuration required" in result.stdout + result.stderr


def test_e8_private_redis_health(isolated):
    root = isolated.root
    assert (root / "redis.sock").is_socket()
    result = subprocess.check_output([str(isolated.redis_cli), "-s", str(root / "redis.sock"), "PING"], text=True)
    assert result.strip() == "PONG"


def test_e9_normal_cleanup():
    with IsolatedServices() as services:
        root = services.root
        redis = next(process for name, process in services.children if name == "redis")
        assert root.exists() and redis.poll() is None
    assert redis.poll() is not None
    assert not root.exists()


def test_e10_failure_cleanup():
    root: Path | None = None
    redis = None
    with pytest.raises(RuntimeError, match="synthetic test failure"):
        with IsolatedServices() as services:
            root = services.root
            redis = next(process for name, process in services.children if name == "redis")
            raise RuntimeError("synthetic test failure")
    assert redis is not None and redis.poll() is not None
    assert root is not None and not root.exists()
