from __future__ import annotations

import asyncio
import base64
import inspect
import io
import time

import httpx
import pytest
from PIL import Image

from app.platform_mcp.service import PlatformMCPService, _validated_image_metadata
from app.product_store import _image_mime_type
from app.security import RuntimePrincipal, RuntimeTokenIssuer
from app.settings import Settings
from app.storage import storage_provider
from app.store import POCStore


FORMATS = {
    "JPEG": ("jpeg", ".jpg", "image/jpeg"),
    "PNG": ("png", ".png", "image/png"),
    "WEBP": ("webp", ".webp", "image/webp"),
}


def image_bytes(image_format: str) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3, 2), (20, 90, 160)).save(output, format=image_format)
    return output.getvalue()


def test_default_image_model_is_sunburst(monkeypatch):
    monkeypatch.delenv("ENTERPRISE_POC_IMAGE_MODEL_ID", raising=False)
    assert Settings.from_env().image_model_id == "gpt-image-2.5-sunburst-c"


def image_service(tmp_path, monkeypatch):
    monkeypatch.setenv("ENTERPRISE_POC_OBJECT_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("ENTERPRISE_POC_OBJECT_STORAGE_DIR", str(tmp_path / "objects"))
    monkeypatch.setenv("ENTERPRISE_POC_IMAGE_MODEL_ID", "gpt-image-2.5-sunburst-c")
    monkeypatch.setenv("ENTERPRISE_POC_IMAGE_API_KEY_ENV", "TEST_IMAGE_API_TOKEN")
    monkeypatch.setenv("TEST_IMAGE_API_TOKEN", "isolated-test-placeholder")
    settings = Settings.from_env()
    store = POCStore(tmp_path / "poc.db")
    store.seed_demo_data()
    issuer = RuntimeTokenIssuer("isolated-image-provider-test-secret")
    token = issuer.issue(RuntimePrincipal(
        "tenant-a", "image-agent", "profile-a", ("image:generate",), int(time.time()) + 60,
    ))
    return PlatformMCPService(store, issuer, settings), settings, token


async def generated(service, token, monkeypatch, content: bytes, observed: dict | None = None):
    observed = observed if observed is not None else {}

    class ClientStub:
        def __init__(self, **kwargs):
            observed["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, url, *, headers, json):
            observed.update(url=url, headers=headers, payload=json)
            return httpx.Response(200, headers={"x-request-id": "request-test-1"}, json={
                "data": [{"b64_json": base64.b64encode(content).decode("ascii")}],
            })

    monkeypatch.setattr(httpx, "AsyncClient", ClientStub)
    return await service.image_generation(token, "测试图片", [], "1:1")


@pytest.mark.parametrize("provider_format", ["JPEG", "PNG", "WEBP"])
def test_actual_format_controls_storage_extension_and_mime(tmp_path, monkeypatch, provider_format):
    service, settings, token = image_service(tmp_path, monkeypatch)
    content = image_bytes(provider_format)
    expected_format, extension, mime_type = FORMATS[provider_format]
    observed = {}

    result = asyncio.run(generated(service, token, monkeypatch, content, observed))

    assert observed["url"] == "https://llm-api.net/v1/images/generations"
    assert observed["headers"] == {"Authorization": "Bearer isolated-test-placeholder"}
    assert observed["payload"] == {
        "prompt": "测试图片",
        "model": "gpt-image-2.5-sunburst-c",
        "provider": {"sort": "success_rate"},
        "size": "1024x1024",
        "n": 1,
        "output_format": "jpeg",
        "response_format": "b64_json",
    }
    image = result["results"][0]
    assert result["model"] == "gpt-image-2.5-sunburst-c"
    assert image["storage_key"].endswith(extension)
    assert image["format"] == expected_format
    assert image["size"] == "1024x1024"
    assert image["request_id"] == "request-test-1"
    assert storage_provider(settings).get(image["storage_key"]) == content
    assert _image_mime_type(image["storage_key"]) == mime_type


@pytest.mark.parametrize("provider_format", ["JPEG", "PNG", "WEBP"])
def test_magic_detection_and_mapping(provider_format):
    assert _validated_image_metadata(image_bytes(provider_format)) == FORMATS[provider_format]


@pytest.mark.parametrize("payload, message", [
    ({"data": []}, r"data\[0\]"),
    ({"data": [{}]}, "b64_json"),
    ({"data": [{"b64_json": "not-valid-base64"}]}, "无效 b64_json"),
])
def test_n1n_invalid_image_result_fails_closed(tmp_path, monkeypatch, payload, message):
    service, _, token = image_service(tmp_path, monkeypatch)

    class ClientStub:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def post(self, *_args, **_kwargs):
            return httpx.Response(200, json=payload)

    monkeypatch.setattr(httpx, "AsyncClient", ClientStub)
    with pytest.raises(RuntimeError, match=message):
        asyncio.run(service.image_generation(token, "测试图片", [], "1:1"))


@pytest.mark.parametrize("content, error", [
    (b"valid base64 but not an image", "provider_image_format_unsupported"),
    (image_bytes("JPEG")[:-2], "provider_image_payload_invalid"),
    (image_bytes("PNG")[:-12], "provider_image_payload_invalid"),
    (b"GIF89a" + b"unsupported", "provider_image_format_unsupported"),
])
def test_invalid_or_unsupported_decoded_payload_fails_closed(content, error):
    with pytest.raises(RuntimeError, match=error):
        _validated_image_metadata(content)


def test_old_image_url_and_legacy_gateway_paths_are_absent():
    source = inspect.getsource(PlatformMCPService.image_generation)
    assert "image_url" not in source
    assert "image-api.luckio.cn" not in source
