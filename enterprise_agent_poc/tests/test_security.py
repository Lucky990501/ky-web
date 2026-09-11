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


def test_failed_knowledge_search_is_audited_as_failed(service):
    mcp, token = service

    class FailingKnowledge:
        def search(self, *_args, **_kwargs):
            raise RuntimeError("embedding unavailable")

    mcp._knowledge = FailingKnowledge()
    with pytest.raises(RuntimeError, match="embedding unavailable"):
        mcp.knowledge_search(token, "秋季")
    assert mcp._store.latest_mcp_audit("tenant-a", "knowledge_search")["status"] == "failed"


def test_runtime_token_is_rejected_after_tenant_is_deleted(tmp_path):
    store = POCStore(tmp_path / "poc.db")
    store.initialize()
    with store.connection() as conn:
        conn.execute("INSERT INTO tenants(id,name,poc_api_key) VALUES (?,?,?)", ("temporary", "Temporary", "temporary-key"))
    issuer = RuntimeTokenIssuer("this-is-a-long-enough-test-token-secret")
    token = issuer.issue(RuntimePrincipal("temporary", "image-agent", "profile", ("knowledge:search",), int(time.time()) + 60))
    from app.settings import Settings
    mcp = PlatformMCPService(store, issuer, Settings.from_env())
    with store.connection() as conn:
        conn.execute("DELETE FROM tenants WHERE id=?", ("temporary",))
    with pytest.raises(TokenError, match="Tenant 已不存在"):
        mcp.knowledge_search(token, "测试")
