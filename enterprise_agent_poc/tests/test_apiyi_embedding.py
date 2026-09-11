from __future__ import annotations

from dataclasses import replace

import pytest

from app.apiyi_embedding import ApiYiCandidateEmbeddingProvider, apiyi_candidate_settings, apiyi_profile_from_env
from app.settings import Settings
from scripts.probe_apiyi_embedding_provider import maximum_consecutive_infrastructure_failures, summarize


VALID_ENV = {
    "APIYI_EMBEDDING_PROVIDER_ID": "apiyi-candidate",
    "APIYI_EMBEDDING_MODEL": "text-embedding-3-small",
    "APIYI_EMBEDDING_DIMENSION": "1536",
    "APIYI_EMBEDDING_BASE_URL": "https://api.apiyi.com/v1",
    "APIYI_EMBEDDING_API_KEY": "test-only-secret",
}


def test_apiyi_profile_requires_official_endpoint_and_exact_model_dimension():
    profile = apiyi_profile_from_env(VALID_ENV)
    assert profile.base_url_domain == "api.apiyi.com"
    assert profile.model == "text-embedding-3-small"
    assert profile.dimension == 1536
    with pytest.raises(ValueError, match="official_endpoint_required"):
        apiyi_profile_from_env({**VALID_ENV, "APIYI_EMBEDDING_BASE_URL": "https://llm-api.net/v1"})


def test_apiyi_payload_requests_1536_and_candidate_copy_does_not_mutate_production():
    profile = apiyi_profile_from_env(VALID_ENV)
    provider = ApiYiCandidateEmbeddingProvider(profile)
    assert provider._payload(["synthetic"])["dimensions"] == 1536
    production = replace(
        Settings.from_env(),
        embedding_provider="openai-compatible",
        embedding_model="text-embedding-3-small",
        embedding_dimension=1536,
        embedding_base_url="https://current.example/v1",
        embedding_api_key="current-secret",
    )
    candidate = apiyi_candidate_settings(production, profile)
    assert production.embedding_base_url == "https://current.example/v1"
    assert production.embedding_api_key == "current-secret"
    assert candidate.embedding_base_url == "https://api.apiyi.com/v1"
    assert candidate.embedding_api_key == "test-only-secret"


def test_apiyi_stability_gate_requires_99_percent_dimension_and_no_sustained_failures():
    success = {"status": "success", "http_status": 200, "latency_ms": 100.0, "returned_dimension": 1536, "error_type": None}
    failure = {"status": "failed", "http_status": 503, "latency_ms": 50.0, "returned_dimension": None, "error_type": "HTTPStatusError"}
    passed = summarize([success] * 99 + [failure], "api.apiyi.com")
    assert passed["gate_pass"] is True
    assert passed["success_rate"] == 99.0
    blocked = summarize([success] * 97 + [failure] * 3, "api.apiyi.com")
    assert blocked["gate_pass"] is False
    assert maximum_consecutive_infrastructure_failures(blocked["attempts"]) == 3
