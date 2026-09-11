"""Isolated embedding profiles used by controlled migration evaluations.

This module is intentionally not wired into the active knowledge services.  A
profile must pass its production probe and migration gates before a separate
release may select it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

import httpx

from app.knowledge import EmbeddingProvider


ALIYUN_BAILIAN_PROVIDER = "aliyun-bailian"
QWEN_EMBEDDING_MODEL = "qwen3.7-text-embedding"
QWEN_EMBEDDING_DIMENSION = 1536
QWEN_INDEX_VERSION = "rag-index-v3-qwen"
_BEIJING_WORKSPACE_HOST = re.compile(
    r"^[a-z0-9][a-z0-9-]*\.cn-beijing\.maas\.aliyuncs\.com$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    provider: str
    model: str
    dimension: int
    index_version: str
    base_url: str
    api_key: str

    @property
    def base_url_domain(self) -> str:
        return urlparse(self.base_url).hostname or ""


def aliyun_bailian_profile_from_env(env: Mapping[str, str] | None = None) -> EmbeddingProfile:
    """Load the inactive Qwen evaluation profile from dedicated variables."""

    values = os.environ if env is None else env
    profile = EmbeddingProfile(
        provider=values.get("ALIYUN_BAILIAN_EMBEDDING_PROVIDER", ALIYUN_BAILIAN_PROVIDER),
        model=values.get("ALIYUN_BAILIAN_EMBEDDING_MODEL", QWEN_EMBEDDING_MODEL),
        dimension=int(values.get("ALIYUN_BAILIAN_EMBEDDING_DIMENSION", str(QWEN_EMBEDDING_DIMENSION))),
        index_version=values.get("ALIYUN_BAILIAN_INDEX_VERSION", QWEN_INDEX_VERSION),
        base_url=values.get("ALIYUN_BAILIAN_EMBEDDING_BASE_URL", "").rstrip("/"),
        api_key=values.get("ALIYUN_BAILIAN_API_KEY", ""),
    )
    validate_aliyun_bailian_profile(profile)
    return profile


def validate_aliyun_bailian_profile(profile: EmbeddingProfile) -> None:
    if profile.provider != ALIYUN_BAILIAN_PROVIDER:
        raise ValueError("aliyun_bailian_provider_mismatch")
    if profile.model != QWEN_EMBEDDING_MODEL:
        raise ValueError("aliyun_bailian_model_mismatch")
    if profile.dimension != QWEN_EMBEDDING_DIMENSION:
        raise ValueError("aliyun_bailian_dimension_mismatch")
    if profile.index_version != QWEN_INDEX_VERSION:
        raise ValueError("aliyun_bailian_index_version_mismatch")
    if not profile.api_key:
        raise ValueError("aliyun_bailian_api_key_missing")

    parsed = urlparse(profile.base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not _BEIJING_WORKSPACE_HOST.fullmatch(parsed.hostname)
        or parsed.path.rstrip("/") != "/compatible-mode/v1"
    ):
        raise ValueError("aliyun_bailian_official_endpoint_required")


class AliyunBailianEmbeddingProvider(EmbeddingProvider):
    """OpenAI-compatible adapter scoped to the inactive Qwen profile."""

    def __init__(self, profile: EmbeddingProfile, *, timeout_seconds: float = 45) -> None:
        validate_aliyun_bailian_profile(profile)
        self.profile = profile
        self.timeout_seconds = timeout_seconds

    def _payload(self, texts: list[str]) -> dict:
        if not texts or len(texts) > 20:
            raise ValueError("aliyun_bailian_batch_size_out_of_range")
        return {
            "model": self.profile.model,
            "input": texts,
            "encoding_format": "float",
            "dimensions": self.profile.dimension,
        }

    def _vectors(self, body: dict, expected: int) -> list[list[float]]:
        data = sorted(body.get("data") or [], key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") for item in data]
        if len(vectors) != expected or any(
            not isinstance(vector, list) or len(vector) != self.profile.dimension for vector in vectors
        ):
            raise RuntimeError("aliyun_bailian_vector_count_or_dimension_mismatch")
        return [[float(value) for value in vector] for vector in vectors]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.profile.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.profile.api_key}"},
                json=self._payload(texts),
            )
            response.raise_for_status()
            return self._vectors(response.json(), len(texts))

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

    def embed_query_sync(self, text: str) -> list[float]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(
                f"{self.profile.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.profile.api_key}"},
                json=self._payload([text]),
            )
            response.raise_for_status()
            return self._vectors(response.json(), 1)[0]
