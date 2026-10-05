from __future__ import annotations

import base64
import binascii
import io
import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from app.security import RuntimeTokenIssuer, RuntimePrincipal, TokenError
from app.store import POCStore
from app.tool_dependencies import (
    RECONSTRUCTION_CONTRACT,
    RetryReceiptLedger,
    ToolInputValidationError,
)
from app.product_store import ProductStore
from app.knowledge import KnowledgeRetrievalService
from app.settings import Settings
from app.storage import storage_provider
from app.store import POCStore


def _validated_image_metadata(content: bytes) -> tuple[str, str, str]:
    """Return actual format, extension and MIME after strict image validation."""
    if content.startswith(b"\xff\xd8\xff"):
        image_format, pil_format, extension, mime_type = "jpeg", "JPEG", ".jpg", "image/jpeg"
        container_complete = content.endswith(b"\xff\xd9")
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        image_format, pil_format, extension, mime_type = "png", "PNG", ".png", "image/png"
        container_complete = content.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
    elif len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        image_format, pil_format, extension, mime_type = "webp", "WEBP", ".webp", "image/webp"
        container_complete = int.from_bytes(content[4:8], "little") + 8 == len(content)
    else:
        raise RuntimeError("provider_image_format_unsupported")
    if not container_complete:
        raise RuntimeError("provider_image_payload_invalid")
    try:
        with Image.open(io.BytesIO(content)) as picture:
            if picture.format != pil_format:
                raise RuntimeError("provider_image_payload_invalid")
            picture.verify()
    except RuntimeError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise RuntimeError("provider_image_payload_invalid") from exc
    return image_format, extension, mime_type


class PlatformMCPService:
    """Tenant-scoped business tools. Tool arguments intentionally have no tenant_id."""

    KNOWLEDGE_MAX_ATTEMPTS = 3
    # Three underlying embedding calls can each consume the provider's 45s
    # timeout; the aggregate budget still bounds calls plus backoff.
    KNOWLEDGE_RETRY_BUDGET_SECONDS = 150.0

    def __init__(self, store: POCStore, token_issuer: RuntimeTokenIssuer, settings: Settings) -> None:
        self._store = store
        self._tokens = token_issuer
        self._settings = settings
        self._knowledge = KnowledgeRetrievalService(ProductStore(store), settings)
        self._retry_receipts = RetryReceiptLedger()

    def enterprise_config_get(self, bearer_token: str) -> dict:
        principal = self._principal(bearer_token, "enterprise_config:read")
        self._audit(principal.tenant_id, "enterprise_config_get", "completed")
        return self._store.enterprise_config(principal.tenant_id)

    def knowledge_search(self, bearer_token: str, query: str, limit: int = 5) -> list[dict]:
        principal = self._principal(bearer_token, "knowledge:search")
        deadline = time.monotonic() + self.KNOWLEDGE_RETRY_BUDGET_SECONDS
        for attempt in range(1, self.KNOWLEDGE_MAX_ATTEMPTS + 1):
            try:
                results = self._knowledge.search(principal.tenant_id, query, limit)
            except Exception as exc:
                retryable, error_type, retry_after = self._retry_policy(exc)
                self._audit_attempt(principal.tenant_id, "knowledge_search", attempt, "failed", error_type)
                if not retryable or attempt == self.KNOWLEDGE_MAX_ATTEMPTS:
                    self._audit(principal.tenant_id, "knowledge_search", "failed")
                    raise
                delay = retry_after if retry_after is not None else float(2 ** (attempt - 1))
                if delay < 0 or time.monotonic() + delay > deadline:
                    self._audit(principal.tenant_id, "knowledge_search", "failed")
                    raise
                time.sleep(delay)
            else:
                self._audit_attempt(principal.tenant_id, "knowledge_search", attempt, "completed", None)
                self._audit(principal.tenant_id, "knowledge_search", "completed")
                return results
        raise RuntimeError("knowledge_search retry loop ended unexpectedly")  # pragma: no cover

    def asset_search(self, bearer_token: str, query: str, asset_type: str | None = None) -> list[dict]:
        principal = self._principal(bearer_token, "assets:search")
        self._audit(principal.tenant_id, "asset_search", "completed")
        return self._store.asset_search(principal.tenant_id, query, asset_type)

    async def image_generation(self, bearer_token: str, prompt: str,
                               references: list[str], aspect_ratio: str,
                               *, retry_of: str | None = None,
                               execution_scope: str = "legacy-runtime",
                               reference_images: list[str] | None = None) -> dict:
        principal = self._principal(bearer_token, "image:generate")
        submitted_args = {
            "prompt": prompt,
            "references": references,
            "aspect_ratio": aspect_ratio,
        }
        if reference_images is not None:
            submitted_args["reference_images"] = reference_images
        retry_audit = None
        if retry_of is not None:
            effective_args, retry_audit = self._retry_receipts.reconstruct(
                retry_of,
                principal=principal,
                execution_scope=execution_scope,
                server="platform",
                tool="image_generation",
                submitted_args=submitted_args,
            )
        else:
            effective_args = submitted_args
        prompt = effective_args["prompt"]
        references = effective_args["references"]
        aspect_ratio = effective_args["aspect_ratio"]
        reference_images = effective_args.get("reference_images") or []
        self._audit(principal.tenant_id, "image_generation", "started")
        if len(reference_images) > 1:
            raise ValueError("当前最多支持一张参考图片。")
        if aspect_ratio not in {"1:1", "9:16", "16:9", "4:5"}:
            self._audit(principal.tenant_id, "image_generation", "failed")
            error = ToolInputValidationError(
                "当前图片网关只允许 1:1、9:16、16:9、4:5 比例。",
                repairable_fields=("aspect_ratio",),
                allowed_values={"aspect_ratio": ("1:1", "9:16", "16:9", "4:5")},
                coupled_text_fields={"aspect_ratio": ("prompt",)},
            )
            error.retry_receipt = self._retry_receipts.issue(
                principal=principal,
                execution_scope=execution_scope,
                server="platform",
                tool="image_generation",
                original_args=effective_args,
                error=error,
            )
            raise error
        api_key = os.environ.get(self._settings.image_api_key_env)
        if not api_key:
            raise RuntimeError(f"未配置图片服务 API Key 环境变量：{self._settings.image_api_key_env}。")
        size = {"1:1": "1024x1024", "9:16": "1024x1536", "16:9": "1536x1024", "4:5": "1024x1536"}[aspect_ratio]
        raw_scope = execution_scope.removeprefix("runtime:") if execution_scope.startswith("runtime:") else ""
        task_id = self._tokens.verify_task_scope(raw_scope, principal.tenant_id) if "." in raw_scope else ""
        attachment = ProductStore(self._store).task_chat_image_attachment(task_id, principal.tenant_id) if task_id else None
        if reference_images and (not attachment or reference_images != [attachment["id"]]):
            raise ValueError("参考图片与当前任务不匹配。")
        source_image = None
        if attachment:
            try:
                source_image = storage_provider(self._settings).get(attachment["storage_key"])
                _actual_format, _extension, actual_mime = _validated_image_metadata(source_image)
                if actual_mime != attachment["mime_type"]:
                    raise ValueError("Stored reference format changed")
            except Exception as exc:
                raise RuntimeError("参考图片暂时无法读取，请重新上传。") from exc
            size = "1024x1024"
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - environment dependency
            raise RuntimeError("未安装 httpx；请重新安装 POC 依赖。") from exc
        # References are tenant-scoped design metadata. Only this task's bound,
        # authenticated upload bytes can select edits; no external URL is fetched.
        async with httpx.AsyncClient(timeout=120) as client:
            if attachment:
                response = await client.post(
                    "https://llm-api.net/v1/images/edits",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data={"prompt": prompt, "model": self._settings.image_model_id,
                          "n": "1", "size": size, "output_format": "jpeg"},
                    files={"image": (attachment["filename"], source_image, attachment["mime_type"])},
                )
            else:
                response = await client.post(
                    "https://llm-api.net/v1/images/generations",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "prompt": prompt,
                        "model": self._settings.image_model_id,
                        "provider": {"sort": "success_rate"},
                        "size": size,
                        "n": 1,
                        "output_format": "jpeg",
                        "response_format": "b64_json",
                    },
                )
        try:
            gateway = response.json()
        except ValueError as exc:
            raise RuntimeError(f"图片网关返回非 JSON 响应（HTTP {response.status_code}）。") from exc
        if not isinstance(gateway, dict):
            raise RuntimeError(f"图片网关返回无效 JSON 结构（HTTP {response.status_code}）。")
        request_id = gateway.get("request_id") or response.headers.get("x-request-id") or "unknown"
        if response.status_code >= 400:
            raise RuntimeError(f"图片网关调用失败（HTTP {response.status_code}，request_id={request_id}）。")
        items = gateway.get("data")
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise RuntimeError("图片网关未返回 data[0]。")
        data = items[0]
        encoded = data.get("b64_json")
        if not isinstance(encoded, str) or not encoded:
            raise RuntimeError("图片网关未返回 b64_json。")
        try:
            image = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("图片网关返回无效 b64_json。") from exc
        if not image:
            raise RuntimeError("图片网关返回空图片。")
        actual_format, extension, mime_type = _validated_image_metadata(image)
        with Image.open(io.BytesIO(image)) as generated_image:
            actual_width, actual_height = generated_image.size
        asset_id = request_id if request_id != "unknown" else uuid4().hex
        try:
            storage_key = f"generated/{principal.tenant_id}/{uuid4().hex}{extension}"
            storage_provider(self._settings).put(storage_key, image, mime_type)
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
                    "file_name": None,
                    "format": actual_format,
                    "size": size,
                    "requested_size": size,
                    "actual_size": f"{actual_width}x{actual_height}",
                    "width": actual_width,
                    "height": actual_height,
                    "mime_type": mime_type,
                    "request_id": None if request_id == "unknown" else request_id,
                    "references_used": references,
                    "reference_images_used": [attachment["id"]] if attachment else [],
                }
            ],
        }
        if retry_audit is not None:
            result["_tool_dependency"] = {
                **retry_audit,
                "status": "completed",
                "provider_invoked": True,
            }
        else:
            result["_tool_dependency"] = {
                "contract": RECONSTRUCTION_CONTRACT,
                "status": "completed",
                "provider_invoked": True,
                "submitted_args": submitted_args,
                "effective_args": effective_args,
                "ignored_retry_argument_drift": [],
                "coupled_repairs": [],
            }
        self._audit(principal.tenant_id, "image_generation", "completed")
        return result

    def _audit(self, tenant_id: str, tool_name: str, status: str) -> None:
        # Server-side confirmation of an executed MCP tool. Deliberately omit
        # tool arguments and enterprise payloads from this cross-run audit.
        self._store.log_event(None, "mcp.tool", {"tenant_id": tenant_id, "tool": tool_name, "status": status})

    def _audit_attempt(self, tenant_id: str, tool_name: str, attempt: int, status: str, error_type: str | None) -> None:
        self._store.log_event(
            None,
            "mcp.tool.attempt",
            {"tenant_id": tenant_id, "tool": tool_name, "attempt": attempt, "status": status, "error_type": error_type},
        )

    @staticmethod
    def _retry_policy(exc: Exception) -> tuple[bool, str, float | None]:
        """Classify transient transport failures without retaining response bodies."""
        try:
            import httpx
        except ImportError:  # pragma: no cover - production dependency
            return False, type(exc).__name__, None
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
            return True, "transport", None
        if not isinstance(exc, httpx.HTTPStatusError):
            return False, type(exc).__name__, None
        status = exc.response.status_code
        # A 429 can mean exhausted quota, not temporary rate limiting. Classify
        # the machine code internally; never audit the provider response body.
        try:
            body = exc.response.json()
            error = body.get("error", {}) if isinstance(body, dict) else {}
            code = (error.get("code") or error.get("type")) if isinstance(error, dict) else None
        except ValueError:
            code = None
        if code in {"insufficient_quota", "quota_exhausted", "quota_exceeded", "billing_hard_limit_reached"}:
            return False, "quota_exhausted", None
        if status not in {429, 500, 502, 503, 504}:
            return False, f"http_{status}", None
        retry_after = PlatformMCPService._retry_after_seconds(exc.response.headers.get("Retry-After"))
        return True, f"http_{status}", retry_after

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    def _principal(self, bearer_token: str, required_scope: str) -> RuntimePrincipal:
        principal = self._tokens.verify(bearer_token, required_scope)
        if not self._store.tenant_exists(principal.tenant_id):
            raise TokenError("Runtime MCP token 对应的 Tenant 已不存在。")
        if principal.execution_context_id:
            from app.agent_execution import authorize_tool
            authorize_tool(self._store, principal, required_scope)
        return principal
