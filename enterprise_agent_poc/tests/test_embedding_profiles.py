from __future__ import annotations

import pytest

from app.embedding_profiles import (
    AliyunBailianEmbeddingProvider,
    aliyun_bailian_profile_from_env,
)
from scripts.probe_qwen_embedding_provider import summarize


VALID_ENV = {
    "ALIYUN_BAILIAN_EMBEDDING_PROVIDER": "aliyun-bailian",
    "ALIYUN_BAILIAN_EMBEDDING_MODEL": "qwen3.7-text-embedding",
    "ALIYUN_BAILIAN_EMBEDDING_DIMENSION": "1536",
    "ALIYUN_BAILIAN_INDEX_VERSION": "rag-index-v3-qwen",
    "ALIYUN_BAILIAN_EMBEDDING_BASE_URL": "https://workspace-123.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
    "ALIYUN_BAILIAN_API_KEY": "test-only-secret",
}


def test_inactive_bailian_profile_requires_official_workspace_endpoint():
    profile = aliyun_bailian_profile_from_env(VALID_ENV)
    assert profile.provider == "aliyun-bailian"
    assert profile.model == "qwen3.7-text-embedding"
    assert profile.dimension == 1536
    assert profile.index_version == "rag-index-v3-qwen"
    assert profile.base_url_domain.endswith(".cn-beijing.maas.aliyuncs.com")

    invalid = {**VALID_ENV, "ALIYUN_BAILIAN_EMBEDDING_BASE_URL": "https://llm-api.net/v1"}
    with pytest.raises(ValueError, match="official_endpoint_required"):
        aliyun_bailian_profile_from_env(invalid)


def test_bailian_payload_requests_1536_dimensions_and_validates_response():
    provider = AliyunBailianEmbeddingProvider(aliyun_bailian_profile_from_env(VALID_ENV))
    payload = provider._payload(["synthetic"])
    assert payload == {
        "model": "qwen3.7-text-embedding",
        "input": ["synthetic"],
        "encoding_format": "float",
        "dimensions": 1536,
    }
    assert len(provider._vectors({"data": [{"index": 0, "embedding": [0.0] * 1536}]}, 1)[0]) == 1536
    with pytest.raises(RuntimeError, match="dimension_mismatch"):
        provider._vectors({"data": [{"index": 0, "embedding": [0.0] * 1024}]}, 1)


def test_probe_summary_requires_all_successes_at_target_dimension():
    success = {
        "status": "success",
        "http_status": 200,
        "latency_ms": 100.0,
        "returned_dimension": 1536,
        "error_type": None,
    }
    failure = {
        "status": "failed",
        "http_status": 503,
        "latency_ms": 20.0,
        "returned_dimension": None,
        "error_type": "HTTPStatusError",
    }
    passed = summarize([success] * 50, provider="aliyun-bailian", model="qwen3.7-text-embedding", dimension=1536, domain="workspace.cn-beijing.maas.aliyuncs.com")
    assert passed["gate_pass"] is True
    assert passed["success_count"] == 50
    assert passed["returned_dimension"] == 1536

    blocked = summarize([success] * 49 + [failure], provider="aliyun-bailian", model="qwen3.7-text-embedding", dimension=1536, domain="workspace.cn-beijing.maas.aliyuncs.com")
    assert blocked["gate_pass"] is False
    assert blocked["error_summary"] == {"HTTPStatusError|HTTP_503": 1}
