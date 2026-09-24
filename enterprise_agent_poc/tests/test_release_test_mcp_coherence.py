"""T1-T8: private Stage 2 MCP endpoint and evidence fixture contract."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from urllib.parse import urlparse

import pytest

from scripts.release_test_environment import IsolatedServices, PROJECT


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux release-test environment only")


@pytest.fixture(scope="module")
def isolated():
    with IsolatedServices() as services:
        yield services


def _manifest(services):
    return json.loads(services.manifest.read_text(encoding="utf-8"))


def test_t1_random_private_mcp_port(isolated):
    port = int(_manifest(isolated)["environment"]["ENTERPRISE_POC_MCP_PORT"])
    assert 1 <= port <= 65535


def test_t2_url_and_listener_share_endpoint(isolated):
    environment = _manifest(isolated)["environment"]
    endpoint = urlparse(environment["ENTERPRISE_POC_MCP_URL"])
    assert endpoint.hostname == environment["ENTERPRISE_POC_MCP_HOST"] == "127.0.0.1"
    assert endpoint.port == int(environment["ENTERPRISE_POC_MCP_PORT"])
    assert endpoint.path == "/mcp"


def test_t3_mismatched_endpoint_blocks_before_runtime(isolated):
    config = _manifest(isolated)
    original = int(config["environment"]["ENTERPRISE_POC_MCP_PORT"])
    config["environment"]["ENTERPRISE_POC_MCP_PORT"] = str(original % 65535 + 1)
    mismatched = isolated.root / "mismatched-mcp.manifest.json"
    mismatched.write_text(json.dumps(config), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(PROJECT / "scripts/stage2_preview.py"), "check", "--config", str(mismatched)],
        cwd=PROJECT, env=isolated._child_env(), capture_output=True, text=True, timeout=20,
    )
    assert result.returncode != 0
    assert "MCP_ENDPOINT_CONFIGURATION_MISMATCH" in result.stdout + result.stderr
    assert all(name == "redis" for name, _ in isolated.children)


def test_t4_independent_environments_have_distinct_ports(isolated):
    first = int(_manifest(isolated)["environment"]["ENTERPRISE_POC_MCP_PORT"])
    with IsolatedServices() as second:
        other = int(_manifest(second)["environment"]["ENTERPRISE_POC_MCP_PORT"])
        assert second.root != isolated.root
        assert other != first


def test_t5_actual_mcp_listener_matches_manifest():
    path = os.environ.get("STAGE25_REDIS_E2E_CONFIG")
    if not path:
        pytest.skip("Requires running formal Stage 2 service fixture")
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    endpoint = urlparse(config["environment"]["ENTERPRISE_POC_MCP_URL"])
    assert endpoint.port == int(config["environment"]["ENTERPRISE_POC_MCP_PORT"])
    with socket.create_connection((endpoint.hostname, endpoint.port), timeout=3):
        pass


def test_t6_private_evidence_directory_created(isolated):
    evidence = isolated.root / "evidence"
    assert evidence.is_dir() and not evidence.is_symlink()
    assert evidence.parent == isolated.root
    assert evidence.stat().st_uid == os.getuid()
    assert evidence.stat().st_mode & 0o077 == 0


def test_t7_evidence_directory_writable(isolated):
    evidence = isolated.root / "evidence" / "writable-probe"
    evidence.write_text("test-only", encoding="utf-8")
    assert evidence.read_text(encoding="utf-8") == "test-only"
    evidence.unlink()


def test_t8_evidence_cleaned_with_isolated_root():
    with IsolatedServices() as services:
        root = services.root
        evidence = root / "evidence" / "cleanup-probe"
        evidence.write_text("test-only", encoding="utf-8")
    assert not root.exists()
