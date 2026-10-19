from __future__ import annotations

import base64
import binascii
import io
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from app.security import RuntimeTokenIssuer, RuntimePrincipal, TokenError
from app.store import POCStore
from app.platform_mcp.image_diagnostics import image_transport_guard
from app.tool_dependencies import (
    RECONSTRUCTION_CONTRACT,
    RetryReceiptLedger,
    ToolInputValidationError,
    ImageToolExecutionFailure,
    reference_image_dependency,
    fingerprint,
)
from app.product_store import ProductStore
from app.knowledge import KnowledgeRetrievalService
from app.settings import Settings
from app.storage import storage_provider
from app.image_request_contract import image_plan, ImageRequestError
from app.image_branding import overlay_brand_logo
from app.brand_logo import BrandLogoService


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
                               reference_images: list[str] | None = None,
                               size: str | None = None,
                               outputs: list[dict] | None = None,
                               reference_role: str = 'subject',
                               include_brand_logo: bool = False) -> dict:
        pilot_gate = None
        if getattr(self._settings, "test_text_gate_enabled", False):
            from app.test_text_gate import TestTextGate, require
            require(retry_of is None and not reference_images, "pilot_image_retry_or_edit_denied")
            pilot_gate = TestTextGate(self._settings, self._tokens)
        principal = self._principal(bearer_token, "image:generate")
        submitted_args = {
            "prompt": prompt,
            "references": references,
            "aspect_ratio": aspect_ratio,
        }
        if reference_images is not None:
            submitted_args["reference_images"] = reference_images
        for key, value in (("size", size), ("outputs", outputs)):
            if value is not None:
                submitted_args[key] = value
        if include_brand_logo:
            submitted_args["include_brand_logo"] = True
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
        size = effective_args.get("size")
        outputs = effective_args.get("outputs")
        include_brand_logo = effective_args.get("include_brand_logo", False)
        self._audit(principal.tenant_id, "image_generation", "started")
        if len(reference_images) > 1:
            raise ValueError("当前最多支持一张参考图片。")
        # Only a signed current Task + its immutable attachment can attest the
        # single reference-image result requirement. Model arguments cannot.
        raw_scope = execution_scope.removeprefix("runtime:") if execution_scope.startswith("runtime:") else ""
        task_id = self._tokens.verify_task_scope(raw_scope, principal.tenant_id) if "." in raw_scope else ""
        attachment = ProductStore(self._store).task_chat_image_attachment(task_id, principal.tenant_id) if task_id else None
        if reference_images and (not attachment or reference_images != [attachment["id"]]):
            raise ValueError("参考图片与当前任务不匹配。")
        image_dependency = reference_image_dependency(principal.tenant_id, task_id, attachment["id"]) if attachment else None
        preview = os.environ.get('CUSTOMER_PREVIEW_MODE') == 'true'
        custom_pixel_ratio = None
        if preview and size:
            from app.image_request_contract import pixel_size
            _, custom_pixel_ratio = pixel_size(size)
        if aspect_ratio not in {"1:1", "9:16", "16:9", "4:5", "3:2", "2:3"} and not (custom_pixel_ratio is not None and aspect_ratio == custom_pixel_ratio):
            self._audit(principal.tenant_id, "image_generation", "failed")
            error = ToolInputValidationError(
                "请选择1:1、9:16、16:9、4:5、3:2或2:3比例。",
                repairable_fields=("aspect_ratio",),
                allowed_values={"aspect_ratio": ("1:1", "9:16", "16:9", "4:5", "3:2", "2:3")},
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
            error.image_dependency = image_dependency
            raise error
        task = ProductStore(self._store).task_for_worker(task_id) if task_id else None
        if task and (task["tenant_id"] != principal.tenant_id or task["agent_id"] != "image-agent"):
            raise PermissionError("图片任务身份不匹配。")
        user_text = task["input_text"] if task else prompt
        plan = image_plan(user_text, prompt, aspect_ratio, size, outputs)
        preview = os.environ.get('CUSTOMER_PREVIEW_MODE') == 'true'
        if reference_role not in {'subject','style','background','composition','brand','reference','front','back'}:
            raise ValueError('参考图用途无效。')
        if attachment and len(plan) != 1 and not preview:
            raise ImageRequestError("reference_batch_contract_required", "参考图编辑当前每任务支持一张结果；多面编辑请先拆分任务，未发送额外付费请求。")
        config = self._store.enterprise_config(principal.tenant_id)
        omit_logo = bool(re.search(r"(?:不要|无需|不需要|不放|不加).{0,5}(?:logo|标志|标识)", user_text, re.I))
        logo_required = not omit_logo and (include_brand_logo or bool(re.search(r"(?:企业|品牌|公司).{0,8}(?:logo|标志|标识)|(?:保留|使用|放上|加上|添加).{0,8}logo", user_text, re.I))
            or bool(config.get("brand_logo") and re.search(r"企业|品牌|招生|宣传|海报|三折页", user_text)))
        if preview and not omit_logo and re.search(r'logo', user_text, re.I):
            logo_required = True
        logo_bytes = None
        if logo_required:
            logo_bytes = BrandLogoService(storage_provider(self._settings), ProductStore(self._store)).current_bytes(principal.tenant_id, config)
        api_key = os.environ.get(self._settings.image_api_key_env)
        if not api_key:
            raise RuntimeError(f"未配置图片服务 API Key 环境变量：{self._settings.image_api_key_env}。")
        source_image = None
        if attachment:
            try:
                source_image = storage_provider(self._settings).get(attachment["storage_key"])
                _actual_format, _extension, actual_mime = _validated_image_metadata(source_image)
                if actual_mime != attachment["mime_type"]:
                    raise ValueError("Stored reference format changed")
            except Exception as exc:
                raise RuntimeError("参考图片暂时无法读取，请重新上传。") from exc
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - environment dependency
            raise RuntimeError("未安装 httpx；请重新安装 POC 依赖。") from exc
        # References are tenant-scoped design metadata. Only this task's bound,
        # authenticated upload bytes can select edits; no external URL is fetched.
        audit = {"contract": RECONSTRUCTION_CONTRACT, "submitted_args": submitted_args,
                 "effective_args": effective_args, "ignored_retry_argument_drift": [], "coupled_repairs": [],
                 **(retry_audit or {}), "request_fingerprint": fingerprint(submitted_args)}
        if image_dependency is not None:
            audit["image_dependency"] = image_dependency
        results, failures = [], []
        transport_options = ({"transport": httpx.AsyncHTTPTransport(retries=0),
                              "trust_env": False, "follow_redirects": False} if pilot_gate else {})
        # Cover client enter/exit failures as well as each request; never leak
        # transport exception details or retry a partially completed request.
        async with image_transport_guard("edits" if attachment else "generations"), httpx.AsyncClient(timeout=120, **transport_options) as client:
            for item in plan:
                if preview and task_id and ProductStore(self._store).cancellation_requested(task_id, principal.tenant_id):
                    failures.extend({'index':other['index'],'purpose':other['purpose'],'code':'not_attempted_after_cancellation'} for other in plan[item['index']:])
                    break
                item_prompt = item["prompt"]
                if preview:
                    item_prompt += '\n企业已保存配置（事实只以这些资料和用户要求为准，不编造价格、师资或承诺）：' + json.dumps({k:config[k] for k in ('brand_name','description','brand_voice','brand_colors','rules') if k in config}, ensure_ascii=False)[:3000]
                    if attachment:
                        item_prompt += '\n本请求实际附带原始图片字节，用途：' + reference_role + '。'
                        if reference_role == 'subject':
                            item_prompt += '以原图人物为主体，保留五官、发型、服饰、姿势和辨识度；不要重新创造相似人物；只改变用户明确要求的区域。保持人物完整，避免不必要的裁切。'
                        item_prompt += '模型不能保证局部编辑绝对不变，忠实执行用户保持不变的约束。'
                if logo_required:
                    item_prompt += "\n企业Logo由平台在右上角叠加原始资产；请在该处留白，不要绘制、猜测或重设计任何Logo。"
                response = None
                try:
                    async with image_transport_guard("edits" if attachment else "generations"):
                        if attachment:
                            response = await client.post(
                                "https://llm-api.net/v1/images/edits", headers={"Authorization": f"Bearer {api_key}"},
                                data={"prompt": item_prompt, "model": self._settings.image_model_id,
                                      "n": "1", "size": item["size"], "output_format": "jpeg"},
                                files={"image": (attachment["filename"], source_image, attachment["mime_type"])})
                        elif pilot_gate:
                            payload = {"prompt": item_prompt, "model": self._settings.image_model_id,
                                       "provider": {"sort": "success_rate"}, "size": item["size"], "n": 1,
                                       "output_format": "jpeg", "response_format": "b64_json"}
                            wire = json.dumps(payload, ensure_ascii=True, allow_nan=False).encode()
                            pilot_gate.reserve_image(principal.tenant_id, task_id, principal.agent_id,
                                                     wire, item=item["index"])
                            response = await client.post(
                                "https://llm-api.net/v1/images/generations", content=wire,
                                headers={"Authorization": f"Bearer {api_key}",
                                         "Content-Type": "application/json"})
                        else:
                            response = await client.post(
                                "https://llm-api.net/v1/images/generations", headers={"Authorization": f"Bearer {api_key}"},
                                json={"prompt": item_prompt, "model": self._settings.image_model_id,
                                      "provider": {"sort": "success_rate"}, "size": item["size"], "n": 1,
                                      "output_format": "jpeg", "response_format": "b64_json"})
                    if response.status_code >= 400:
                        raise ImageToolExecutionFailure(response.status_code, audit)
                    try:
                        gateway = response.json()
                    except ValueError as exc:
                        raise RuntimeError(f"图片网关返回非 JSON 响应（HTTP {response.status_code}）。") from exc
                    if not isinstance(gateway, dict):
                        raise RuntimeError("图片网关返回无效 JSON 结构。")
                    data = gateway.get("data")
                    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
                        raise RuntimeError("图片网关未返回 data[0]。")
                    # Each separately planned request is n=1; never silently
                    # ignore extra Provider results or mislabel a collaged side.
                    if len(data) != 1:
                        raise RuntimeError("图片网关返回张数与本次单图请求不一致。")
                    encoded = data[0].get("b64_json")
                    if not isinstance(encoded, str) or not encoded:
                        raise RuntimeError("图片网关未返回 b64_json。")
                    try:
                        image = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise RuntimeError("图片网关返回无效 b64_json。") from exc
                    if not image:
                        raise RuntimeError("图片网关返回空图片。")
                    actual_format, extension, mime_type = _validated_image_metadata(image)
                    with Image.open(io.BytesIO(image)) as decoded:
                        actual_width, actual_height = decoded.size
                    storage = storage_provider(self._settings)
                    provider_key = f"generated/{principal.tenant_id}/{uuid4().hex}{extension}"
                    try:
                        storage.put(provider_key, image, mime_type)
                    except Exception as exc:
                        raise RuntimeError("图片已生成但平台持久化失败。") from exc
                    storage_key = provider_key
                    if logo_bytes is not None:
                        image = overlay_brand_logo(image, logo_bytes)
                        actual_format, extension, mime_type = _validated_image_metadata(image)
                        storage_key = f"generated/{principal.tenant_id}/{uuid4().hex}{extension}"
                        try:
                            storage.put(storage_key, image, mime_type)
                        except Exception as exc:
                            raise RuntimeError("图片已生成但平台持久化失败。") from exc
                    expected = tuple(map(int, item["size"].split("x")))
                    dimension_match = (actual_width, actual_height) == expected
                    request_id = gateway.get("request_id") or response.headers.get("x-request-id")
                    results.append({
                        "asset_id": request_id or uuid4().hex, "storage_key": storage_key,
                        "url": f"/api/v1/storage/{storage_key}", "file_name": None,
                        "format": actual_format, "size": item["size"], "requested_size": item["size"],
                        "actual_size": f"{actual_width}x{actual_height}", "width": actual_width, "height": actual_height,
                        "mime_type": mime_type, "request_id": request_id,
                        "references_used": references, "reference_images_used": [attachment["id"]] if attachment else [],
                        "index": item["index"], "purpose": item["purpose"], "requested_aspect_ratio": item["aspect_ratio"],
                        "dimension_match": dimension_match, "warnings": [] if dimension_match else ["PROVIDER_DIMENSION_MISMATCH"],
                        "original_storage_key": provider_key, "brand_logo_applied": logo_bytes is not None,
                        "reference_role": reference_role if attachment else None,
                    })
                    self._store.log_event(None, "image.request", {
                        "tenant_id": principal.tenant_id, "task_id": task_id, "index": item["index"],
                        "prompt_version": item["prompt_version"], "prompt_sha256": hashlib.sha256(item_prompt.encode()).hexdigest(),
                        "requested_count": len(plan), "size": item["size"], "actual_width": actual_width, "actual_height": actual_height,
                        "reference_ids": [attachment["id"]] if attachment else [], "provider_status": response.status_code,
                    })
                except Exception as exc:
                    self._audit(principal.tenant_id, "image_generation", "failed")
                    self._store.log_event(None, "image.request_failed", {
                        "tenant_id": principal.tenant_id, "task_id": task_id, "index": item["index"],
                        "prompt_version": item["prompt_version"], "prompt_sha256": hashlib.sha256(item_prompt.encode()).hexdigest(),
                        "requested_count": len(plan), "size": item["size"],
                        "reference_ids": [attachment["id"]] if attachment else [],
                        "provider_status": response.status_code if response is not None else None,
                        "error_type": type(exc).__name__,
                    })
                    if len(plan) == 1:
                        raise
                    failures.append({"index": item["index"], "purpose": item["purpose"],
                                     "code": "image_provider_or_storage_error", "provider_status": response.status_code if response is not None else None})
                    # No automatic paid retry. A provider quota/auth failure
                    # stops the rest of the batch instead of consuming more.
                    if response is not None and response.status_code in {401, 403, 429}:
                        failures.extend({"index": other["index"], "purpose": other["purpose"], "code": "not_attempted_after_provider_rejection"}
                                        for other in plan[item["index"]+1:])
                        break
        if not results:
            raise RuntimeError("本次图片均未生成成功，未将任务标记为全部成功。")
        delivery_complete = not failures and all(item["dimension_match"] for item in results)
        result = {
            "provider": self._settings.image_provider_id,
            "model": self._settings.image_model_id,
            "tenant_id_not_exposed": True,
            "results": results, "failures": failures,
            "delivery": {"requested": len(plan), "generated": len(results), "failed": len(failures),
                         "status": "completed" if delivery_complete else "partial", "provider_attempts": len(results)+len([x for x in failures if "provider_status" in x])},
        }
        result["_tool_dependency"] = {**audit, "status": "completed", "provider_invoked": True}
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
