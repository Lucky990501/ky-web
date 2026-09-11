from __future__ import annotations

import os
from uuid import uuid4

from app.security import RuntimeTokenIssuer, RuntimePrincipal, TokenError
from app.store import POCStore
from app.product_store import ProductStore
from app.knowledge import KnowledgeRetrievalService
from app.settings import Settings
from app.storage import storage_provider
from app.store import POCStore


class PlatformMCPService:
    """Tenant-scoped business tools. Tool arguments intentionally have no tenant_id."""

    def __init__(self, store: POCStore, token_issuer: RuntimeTokenIssuer, settings: Settings) -> None:
        self._store = store
        self._tokens = token_issuer
        self._settings = settings
        self._knowledge = KnowledgeRetrievalService(ProductStore(store), settings)

    def enterprise_config_get(self, bearer_token: str) -> dict:
        principal = self._principal(bearer_token, "enterprise_config:read")
        self._audit(principal.tenant_id, "enterprise_config_get", "completed")
        return self._store.enterprise_config(principal.tenant_id)

    def knowledge_search(self, bearer_token: str, query: str, limit: int = 5) -> list[dict]:
        principal = self._principal(bearer_token, "knowledge:search")
        try:
            results = self._knowledge.search(principal.tenant_id, query, limit)
        except Exception:
            self._audit(principal.tenant_id, "knowledge_search", "failed")
            raise
        self._audit(principal.tenant_id, "knowledge_search", "completed")
        return results

    def asset_search(self, bearer_token: str, query: str, asset_type: str | None = None) -> list[dict]:
        principal = self._principal(bearer_token, "assets:search")
        self._audit(principal.tenant_id, "asset_search", "completed")
        return self._store.asset_search(principal.tenant_id, query, asset_type)

    async def image_generation(self, bearer_token: str, prompt: str, references: list[str], aspect_ratio: str) -> dict:
        principal = self._principal(bearer_token, "image:generate")
        self._audit(principal.tenant_id, "image_generation", "started")
        api_key = os.environ.get(self._settings.image_api_key_env)
        if not api_key:
            raise RuntimeError(f"未配置图片服务 API Key 环境变量：{self._settings.image_api_key_env}。")
        if aspect_ratio not in {"1:1", "9:16", "16:9", "4:5"}:
            raise ValueError("当前图片网关只允许 1:1、9:16、16:9、4:5 比例。")
        size = {"1:1": "1024x1024", "9:16": "1024x1536", "16:9": "1536x1024", "4:5": "1024x1536"}[aspect_ratio]
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - environment dependency
            raise RuntimeError("未安装 httpx；请重新安装 POC 依赖。") from exc
        # References are tenant-scoped design metadata. External URLs are never
        # fetched by the gateway, preventing SSRF and cross-tenant retrieval.
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                "https://image-api.luckio.cn/api/v1/images/generate",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"prompt": prompt, "size": size, "quality": "medium", "output_format": "png", "n": 1},
            )
        try:
            gateway = response.json()
        except ValueError as exc:
            raise RuntimeError(f"图片网关返回非 JSON 响应（HTTP {response.status_code}）。") from exc
        if response.status_code >= 400 or gateway.get("code") != 0:
            request_id = gateway.get("request_id", "unknown")
            raise RuntimeError(f"图片网关调用失败（HTTP {response.status_code}，request_id={request_id}）。")
        data = gateway.get("data") or {}
        image_url = data.get("image_url")
        if not image_url:
            raise RuntimeError("图片网关未返回 image_url。")
        asset_id = data.get("file_name") or gateway.get("request_id") or uuid4().hex
        # Persist the provider's short-lived URL in platform-owned storage.
        try:
            async with httpx.AsyncClient(timeout=120) as download_client:
                image = await download_client.get(image_url)
            image.raise_for_status()
            storage_key = f"generated/{principal.tenant_id}/{uuid4().hex}.png"
            storage_provider(self._settings).put(storage_key, image.content, image.headers.get("content-type", "image/png"))
        except Exception as exc:
            raise RuntimeError("图片已生成但平台持久化失败。") from exc
        result = {
            "provider": self._settings.image_provider_id,
            "model": self._settings.image_model_id,
            "tenant_id_not_exposed": True,
            "results": [
                {
                    "asset_id": asset_id,
                    "storage_key": storage_key,
                    "url": f"/api/v1/storage/{storage_key}",
                    "file_name": data.get("file_name"),
                    "format": data.get("format"),
                    "size": data.get("size"),
                    "request_id": gateway.get("request_id"),
                    "references_used": references,
                }
            ],
        }
        self._audit(principal.tenant_id, "image_generation", "completed")
        return result

    def _audit(self, tenant_id: str, tool_name: str, status: str) -> None:
        # Server-side confirmation of an executed MCP tool. Deliberately omit
        # tool arguments and enterprise payloads from this cross-run audit.
        self._store.log_event(None, "mcp.tool", {"tenant_id": tenant_id, "tool": tool_name, "status": status})

    def _principal(self, bearer_token: str, required_scope: str) -> RuntimePrincipal:
        principal = self._tokens.verify(bearer_token, required_scope)
        if not self._store.tenant_exists(principal.tenant_id):
            raise TokenError("Runtime MCP token 对应的 Tenant 已不存在。")
        return principal
