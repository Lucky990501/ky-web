"""Isolated APIYI embedding candidate; never selected by production settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Mapping
from urllib.parse import urlparse

from app.knowledge import OpenAICompatibleEmbeddingProvider
from app.settings import Settings


APIYI_PROVIDER_ID = "apiyi-candidate"
APIYI_MODEL = "text-embedding-3-small"
APIYI_DIMENSION = 1536
APIYI_BASE_URL = "https://api.apiyi.com/v1"


@dataclass(frozen=True, slots=True)
class ApiYiCandidateProfile:
    provider_id: str
    model: str
    dimension: int
    base_url: str
    api_key: str

    @property
    def base_url_domain(self) -> str:
        return urlparse(self.base_url).hostname or ""


def apiyi_profile_from_env(env: Mapping[str, str] | None = None) -> ApiYiCandidateProfile:
    values = os.environ if env is None else env
    profile = ApiYiCandidateProfile(
        provider_id=values.get("APIYI_EMBEDDING_PROVIDER_ID", APIYI_PROVIDER_ID),
        model=values.get("APIYI_EMBEDDING_MODEL", APIYI_MODEL),
        dimension=int(values.get("APIYI_EMBEDDING_DIMENSION", str(APIYI_DIMENSION))),
        base_url=values.get("APIYI_EMBEDDING_BASE_URL", APIYI_BASE_URL).rstrip("/"),
        api_key=values.get("APIYI_EMBEDDING_API_KEY", ""),
    )
    validate_apiyi_profile(profile)
    return profile


def validate_apiyi_profile(profile: ApiYiCandidateProfile) -> None:
    if profile.provider_id != APIYI_PROVIDER_ID:
        raise ValueError("apiyi_provider_id_mismatch")
    if profile.model != APIYI_MODEL:
        raise ValueError("apiyi_model_mismatch")
    if profile.dimension != APIYI_DIMENSION:
        raise ValueError("apiyi_dimension_mismatch")
    if not profile.api_key:
        raise ValueError("apiyi_api_key_missing")
    parsed = urlparse(profile.base_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.apiyi.com"
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.path.rstrip("/") != "/v1"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("apiyi_official_endpoint_required")


def apiyi_candidate_settings(production: Settings, profile: ApiYiCandidateProfile) -> Settings:
    """Return an in-process copy; the production Settings singleton is untouched."""

    validate_apiyi_profile(profile)
    if (
        production.embedding_provider != "openai-compatible"
        or production.embedding_model != APIYI_MODEL
        or production.embedding_dimension != APIYI_DIMENSION
    ):
        raise ValueError("production_embedding_profile_mismatch")
    return replace(
        production,
        embedding_base_url=profile.base_url,
        embedding_api_key=profile.api_key,
    )


class ApiYiCandidateEmbeddingProvider(OpenAICompatibleEmbeddingProvider):
    """Exact 1536-dimension APIYI client compatible with the frozen RAG service."""

    def __init__(self, profile: ApiYiCandidateProfile) -> None:
        validate_apiyi_profile(profile)
        self._base_url = profile.base_url
        self._api_key = profile.api_key
        self._model = profile.model
        self._dimension = profile.dimension

    def _payload(self, texts: list[str]) -> dict:
        payload = super()._payload(texts)
        payload["dimensions"] = self._dimension
        return payload
