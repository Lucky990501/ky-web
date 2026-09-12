from __future__ import annotations

import json
import time

import httpx
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


def http_error(status: int, retry_after: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://embedding.example/v1/embeddings")
    response = httpx.Response(status, headers={"Retry-After": retry_after} if retry_after else {}, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("expected HTTPStatusError")


def test_retryable_knowledge_failure_uses_retry_after_and_records_each_attempt(service, monkeypatch):
    mcp, token = service
    sleeps = []

    class FlakyKnowledge:
        calls = 0

        def search(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise http_error(429, "2")
            return [{"id": "chunk"}]

    flaky = FlakyKnowledge()
    mcp._knowledge = flaky
    monkeypatch.setattr("app.platform_mcp.service.time.sleep", sleeps.append)

    assert mcp.knowledge_search(token, "秋季") == [{"id": "chunk"}]
    assert flaky.calls == 2
    assert sleeps == [2.0]
    with mcp._store.connection() as conn:
        rows = conn.execute("SELECT payload FROM execution_events WHERE event_type='mcp.tool.attempt' ORDER BY id").fetchall()
    attempts = [json.loads(row["payload"]) for row in rows]
    assert [(item["attempt"], item["status"], item["error_type"]) for item in attempts] == [
        (1, "failed", "http_429"),
        (2, "completed", None),
    ]


def test_retryable_knowledge_failure_stops_after_bounded_attempts(service, monkeypatch):
    mcp, token = service

    class AlwaysUnavailable:
        calls = 0

        def search(self, *_args, **_kwargs):
            self.calls += 1
            raise http_error(503)

    unavailable = AlwaysUnavailable()
    mcp._knowledge = unavailable
    monkeypatch.setattr("app.platform_mcp.service.time.sleep", lambda _seconds: None)

    with pytest.raises(httpx.HTTPStatusError):
        mcp.knowledge_search(token, "秋季")
    assert unavailable.calls == mcp.KNOWLEDGE_MAX_ATTEMPTS == 3
    assert mcp._store.latest_mcp_audit("tenant-a", "knowledge_search")["status"] == "failed"


def test_non_retryable_credential_error_is_not_retried(service, monkeypatch):
    mcp, token = service

    class Unauthorized:
        calls = 0

        def search(self, *_args, **_kwargs):
            self.calls += 1
            raise http_error(401)

    unauthorized = Unauthorized()
    mcp._knowledge = unauthorized
    monkeypatch.setattr("app.platform_mcp.service.time.sleep", lambda _seconds: pytest.fail("must not retry"))

    with pytest.raises(httpx.HTTPStatusError):
        mcp.knowledge_search(token, "秋季")
    assert unauthorized.calls == 1
