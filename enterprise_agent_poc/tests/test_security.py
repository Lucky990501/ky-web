from __future__ import annotations

import time

import pytest

from app.platform_mcp.service import PlatformMCPService
from app.security import RuntimePrincipal, RuntimeTokenIssuer, TokenError
from app.store import POCStore


@pytest.fixture()
def service(tmp_path):
    store = POCStore(tmp_path / "poc.db")
    store.seed_demo_data()
    issuer = RuntimeTokenIssuer("this-is-a-long-enough-test-token-secret")
    token = issuer.issue(
        RuntimePrincipal("tenant-a", "image-agent", "profile-a", ("knowledge:search",), int(time.time()) + 60)
    )
    from app.settings import Settings
    return PlatformMCPService(store, issuer, Settings.from_env()), token


def test_mcp_token_scopes_and_isolates_knowledge(service):
    mcp, token = service
    results = mcp.knowledge_search(token, "秋季")
    assert results and all("A" in item["title"] or "数学" in item["content"] for item in results)
    assert all("小学" not in item["content"] for item in results)
    with pytest.raises(TokenError):
        mcp.enterprise_config_get(token)


def test_tampered_token_is_rejected(service):
    mcp, token = service
    with pytest.raises(TokenError):
        mcp.knowledge_search(token[:-1] + "x", "秋季")
