from __future__ import annotations

import asyncio
import base64
import time

import httpx
import pytest

from app.platform_mcp.service import PlatformMCPService
from app.security import RuntimePrincipal, RuntimeTokenIssuer
from app.settings import Settings
from app.storage import storage_provider
from app.store import POCStore


JPEG_BYTES = b"\xff\xd8\xff\xe0isolated-jpeg-fixture\xff\xd9"


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


def test_n1n_payload_decodes_and_persists_jpeg(tmp_path, monkeypatch):
    service, settings, token = image_service(tmp_path, monkeypatch)
    observed = {}

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
                "data": [{"b64_json": base64.b64encode(JPEG_BYTES).decode("ascii")}],
            })

    monkeypatch.setattr(httpx, "AsyncClient", ClientStub)
    result = asyncio.run(service.image_generation(token, "测试图片", [], "1:1"))

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
    assert image["storage_key"].endswith(".jpg")
    assert image["format"] == "jpeg"
    assert image["size"] == "1024x1024"
    assert image["request_id"] == "request-test-1"
    assert storage_provider(settings).get(image["storage_key"]) == JPEG_BYTES


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
