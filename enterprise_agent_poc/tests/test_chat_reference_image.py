import asyncio
import base64
import io
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main
from app.auth import SessionIssuer, hash_password
from app.domain import RuntimeProfile
from app.platform_mcp.service import PlatformMCPService
from app.product_store import ProductStore
from app.runtime.codex_provider import CodexRuntimeProvider
from app.security import RuntimePrincipal, RuntimeTokenIssuer, TokenError
from app.storage import storage_provider
from app.store import POCStore


def picture(image_format="PNG", size=(16, 12)):
    output = io.BytesIO()
    Image.new("RGB", size, (20, 90, 160)).save(output, format=image_format)
    return output.getvalue()


@pytest.fixture
def chat_image_env(tmp_path, monkeypatch):
    database = tmp_path / "chat-image.db"
    store = POCStore(database)
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()
    product.create_user("tenant-a", "image-owner@test.invalid", hash_password("ImageTest!2026"), "Owner", "member")
    product.create_user("tenant-a", "image-other@test.invalid", hash_password("ImageTest!2026"), "Other", "member")
    owner = product.user_by_email("image-owner@test.invalid")
    settings = replace(main.settings, database_url=f"sqlite:///{database.as_posix()}", database_path=database,
                       object_storage_provider="local", object_storage_dir=tmp_path / "objects",
                       secure_cookies=False, bootstrap_demo_data=False, environment="test", task_queue="local")
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "product_store", product)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "sessions", SessionIssuer("chat-image-test-session-secret"))

    async def no_worker(_task):
        return None

    monkeypatch.setattr(main.task_service, "execute", no_worker)
    client = TestClient(main.app)
    assert client.post("/api/v1/auth/login", json={"account": "image-owner@test.invalid", "password": "ImageTest!2026"}).status_code == 200
    try:
        yield client, store, product, settings, owner
    finally:
        client.close()


@pytest.mark.parametrize("image_format,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")])
def test_upload_accepts_real_image_formats_and_private_proxy(chat_image_env, image_format, mime):
    client, _store, _product, _settings, _owner = chat_image_env
    content = picture(image_format)
    response = client.post("/api/v1/chat-images", files={"file": (f"reference.{image_format.lower()}", content, mime)})
    assert response.status_code == 201
    record = response.json()
    assert record["type"] == "image" and record["mime_type"] == mime
    assert (record["width"], record["height"]) == (16, 12)
    assert "storage_key" not in record
    displayed = client.get(record["content_url"])
    assert displayed.status_code == 200 and displayed.content == content
    assert displayed.headers["content-type"] == mime


@pytest.mark.parametrize("content,mime", [
    (picture("PNG"), "image/jpeg"),
    (picture("PNG")[:-12], "image/png"),
    (b"GIF89a-not-an-image", "image/gif"),
    (b"not an image", "image/png"),
    (b"x" * (10 * 1024 * 1024 + 1), "image/png"),
])
def test_upload_rejects_mismatch_corruption_unsupported_and_oversize(chat_image_env, content, mime):
    client, _store, _product, _settings, _owner = chat_image_env
    response = client.post("/api/v1/chat-images", files={"file": ("bad.png", content, mime)})
    assert response.status_code == 422
    assert "Traceback" not in response.text and "storage_key" not in response.text


def test_upload_is_single_use_task_bound_and_history_keeps_prompt_and_image(chat_image_env):
    client, store, product, _settings, owner = chat_image_env
    uploaded = client.post("/api/v1/chat-images", files={"file": ("reference.png", picture(), "image/png")}).json()
    request = {"message": "把背景换成未来都市", "attachments": [{"type": "image", "id": uploaded["id"]}]}
    created = client.post("/api/v1/agents/image-agent/runs", json=request)
    assert created.status_code == 202
    task_id = created.json()["id"]
    assert product.task_chat_image_attachment(task_id, "tenant-a")["id"] == uploaded["id"]
    assert client.post("/api/v1/agents/image-agent/runs", json=request).status_code == 409
    assert client.post("/api/v1/agents/image-agent/runs", json={**request, "attachments": request["attachments"] * 2}).status_code == 422
    assert client.post("/api/v1/agents/copywriting-agent/runs", json=request).status_code == 422
    assert client.delete(uploaded["content_url"]).status_code == 404

    conversation_id = str(uuid4())
    store.save_conversation(conversation_id, "tenant-a", "image-agent", "profile-image", "thread-image", "test-runtime")
    product.attach_conversation(conversation_id, owner["id"], "参考图片项目")
    product.set_task(task_id, "tenant-a", "running", "starting_runtime", "正在处理", conversation_id=conversation_id)
    product.add_message(conversation_id, "user", request["message"], message_id=f"task:{task_id}:user")
    detail = product.conversation_detail("tenant-a", owner["id"], conversation_id)
    user_message = next(item for item in detail["messages"] if item["role"] == "user")
    assert user_message["content"] == request["message"]
    assert user_message["attachments"][0]["content_url"] == uploaded["content_url"]
    assert "storage_key" not in user_message["attachments"][0]


def test_uploaded_image_is_invisible_to_another_member(chat_image_env):
    client, _store, _product, _settings, _owner = chat_image_env
    uploaded = client.post("/api/v1/chat-images", files={"file": ("reference.png", picture(), "image/png")}).json()
    assert client.post("/api/v1/auth/login", json={"account": "image-other@test.invalid", "password": "ImageTest!2026"}).status_code == 200
    assert client.get(uploaded["content_url"]).status_code == 404
    assert client.delete(uploaded["content_url"]).status_code == 404
    denied = client.post("/api/v1/agents/image-agent/runs", json={
        "message": "修改图片", "attachments": [{"type": "image", "id": uploaded["id"]}],
    })
    assert denied.status_code == 409


def test_plain_text_task_remains_unattached(chat_image_env):
    client, _store, product, _settings, _owner = chat_image_env
    response = client.post("/api/v1/agents/image-agent/runs", json={"message": "画一张海报"})
    assert response.status_code == 202
    assert product.task_chat_image_attachment(response.json()["id"], "tenant-a") is None


def test_delete_unclaimed_image_removes_preview_and_prevents_edits(chat_image_env):
    client, _store, _product, _settings, _owner = chat_image_env
    uploaded = client.post("/api/v1/chat-images", files={"file": ("reference.png", picture(), "image/png")}).json()
    assert client.delete(uploaded["content_url"]).status_code == 200
    assert client.get(uploaded["content_url"]).status_code == 404
    response = client.post("/api/v1/agents/image-agent/runs", json={"message": "生成一张海报"})
    assert response.status_code == 202


def test_signed_task_scope_is_tenant_bound(chat_image_env):
    _client, _store, _product, _settings, _owner = chat_image_env
    issuer = RuntimeTokenIssuer("chat-image-task-scope-test-secret")
    scope = issuer.issue_task_scope("tenant-a", "task-1")
    assert issuer.verify_task_scope(scope, "tenant-a") == "task-1"
    with pytest.raises(TokenError):
        issuer.verify_task_scope(scope, "tenant-b")


def test_runtime_start_and_resume_install_current_task_scope(monkeypatch):
    issuer = RuntimeTokenIssuer("chat-image-task-scope-test-secret")
    profile = RuntimeProfile.build(tenant_id="tenant-a", agent_id="image-agent",
                                   model_provider_id="provider-a", model_id="model-a",
                                   reasoning_effort="medium", skill_manifest={})
    observed = []

    class FakeCodex:
        async def thread_start(self, **kwargs):
            observed.append(kwargs["config"]["mcp_servers"]["platform"]["http_headers"]["X-Runtime-Execution-Scope"])
            return SimpleNamespace(id="thread-a")

        async def thread_resume(self, _thread_id, **kwargs):
            observed.append(kwargs["config"]["mcp_servers"]["platform"]["http_headers"]["X-Runtime-Execution-Scope"])
            return SimpleNamespace(id="thread-a")

    async def get(_profile):
        return FakeCodex()

    manager = SimpleNamespace(get=get, _token_issuer=issuer, _start_event=lambda *_a, **_k: None,
                              _paths=lambda _profile: (Path("."), Path(".")))
    provider = CodexRuntimeProvider(manager)
    monkeypatch.setattr(provider, "_sandbox", lambda _policy: "isolated-test-sandbox")
    asyncio.run(provider.create_session(profile, "instructions", task_id="task-new"))
    asyncio.run(provider.resume_session(profile, "thread-a", task_id="task-resumed"))
    assert [issuer.verify_task_scope(scope, "tenant-a") for scope in observed] == ["task-new", "task-resumed"]


def test_attached_task_uses_multipart_edits_and_actual_png_metadata(chat_image_env, monkeypatch):
    client, store, product, settings, _owner = chat_image_env
    source_bytes = picture("JPEG", (20, 16))
    uploaded = client.post("/api/v1/chat-images", files={"file": ("reference.jpg", source_bytes, "image/jpeg")}).json()
    task_id = client.post("/api/v1/agents/image-agent/runs", json={
        "message": "把这张图改成敦煌风", "attachments": [{"type": "image", "id": uploaded["id"]}],
    }).json()["id"]
    issuer = RuntimeTokenIssuer("chat-image-provider-test-secret")
    token = issuer.issue(RuntimePrincipal("tenant-a", "image-agent", "profile-a", ("image:generate",), int(time.time()) + 60))
    scope = "runtime:" + issuer.issue_task_scope("tenant-a", task_id)
    monkeypatch.setenv(settings.image_api_key_env, "isolated-test-placeholder")
    observed = {}
    returned_png = picture("PNG", (1254, 1254))

    class ClientStub:
        def __init__(self, **kwargs):
            observed["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, **kwargs):
            observed.update(url=url, **kwargs)
            return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(returned_png).decode()}]})

    monkeypatch.setattr(httpx, "AsyncClient", ClientStub)
    service = PlatformMCPService(store, issuer, settings)
    result = asyncio.run(service.image_generation(token, "把这张图改成敦煌风", [], "1:1", execution_scope=scope))
    assert observed["url"] == "https://llm-api.net/v1/images/edits"
    assert observed["headers"] == {"Authorization": "Bearer isolated-test-placeholder"}
    assert "content-type" not in observed["headers"]
    assert observed["files"]["image"] == ("reference.jpg", source_bytes, "image/jpeg")
    assert observed["data"] == {"prompt": "把这张图改成敦煌风", "model": "gpt-image-2.5-sunburst-c",
                                 "n": "1", "size": "1024x1024", "output_format": "jpeg"}
    item = result["results"][0]
    assert item["requested_size"] == "1024x1024"
    assert item["actual_size"] == "1254x1254"
    assert (item["width"], item["height"], item["format"], item["mime_type"]) == (1254, 1254, "png", "image/png")
    assert item["storage_key"].endswith(".png")
    assert storage_provider(settings).get(item["storage_key"]) == returned_png
    assert item["reference_images_used"] == [uploaded["id"]]
    with pytest.raises(ValueError, match="最多"):
        asyncio.run(service.image_generation(token, "prompt", [], "1:1", execution_scope=scope,
                                             reference_images=[uploaded["id"], uploaded["id"]]))


def test_missing_reference_cannot_be_forced_into_edits(chat_image_env, monkeypatch):
    client, store, _product, settings, _owner = chat_image_env
    task_id = client.post("/api/v1/agents/image-agent/runs", json={"message": "画一张海报"}).json()["id"]
    issuer = RuntimeTokenIssuer("chat-image-provider-test-secret")
    token = issuer.issue(RuntimePrincipal("tenant-a", "image-agent", "profile-a", ("image:generate",), int(time.time()) + 60))
    monkeypatch.setenv(settings.image_api_key_env, "isolated-test-placeholder")
    service = PlatformMCPService(store, issuer, settings)
    with pytest.raises(ValueError, match="不匹配"):
        asyncio.run(service.image_generation(token, "prompt", [], "1:1",
                                             execution_scope="runtime:" + issuer.issue_task_scope("tenant-a", task_id),
                                             reference_images=[str(uuid4())]))


def test_bound_reference_storage_read_failure_stops_before_provider(chat_image_env, monkeypatch):
    client, store, _product, settings, _owner = chat_image_env
    uploaded = client.post("/api/v1/chat-images", files={"file": ("reference.png", picture(), "image/png")}).json()
    task_id = client.post("/api/v1/agents/image-agent/runs", json={
        "message": "修改图片", "attachments": [{"type": "image", "id": uploaded["id"]}],
    }).json()["id"]
    issuer = RuntimeTokenIssuer("chat-image-provider-test-secret")
    token = issuer.issue(RuntimePrincipal("tenant-a", "image-agent", "profile-a", ("image:generate",), int(time.time()) + 60))
    monkeypatch.setenv(settings.image_api_key_env, "isolated-test-placeholder")

    class UnavailableStorage:
        def get(self, _key):
            raise FileNotFoundError("private storage location")

    monkeypatch.setattr("app.platform_mcp.service.storage_provider", lambda _settings: UnavailableStorage())
    service = PlatformMCPService(store, issuer, settings)
    with pytest.raises(RuntimeError, match="参考图片暂时无法读取") as caught:
        asyncio.run(service.image_generation(token, "修改图片", [], "1:1",
                                             execution_scope="runtime:" + issuer.issue_task_scope("tenant-a", task_id)))
    assert "private storage location" not in str(caught.value)


@pytest.mark.parametrize("failure", ["http_400", "http_503", "timeout", "malformed_b64", "unsupported_actual"])
def test_edit_failure_is_bounded_without_retry_or_storage_write(chat_image_env, monkeypatch, failure):
    client, store, _product, settings, _owner = chat_image_env
    uploaded = client.post("/api/v1/chat-images", files={"file": ("reference.png", picture(), "image/png")}).json()
    task_id = client.post("/api/v1/agents/image-agent/runs", json={
        "message": "修改图片", "attachments": [{"type": "image", "id": uploaded["id"]}],
    }).json()["id"]
    issuer = RuntimeTokenIssuer("chat-image-provider-test-secret")
    token = issuer.issue(RuntimePrincipal("tenant-a", "image-agent", "profile-a", ("image:generate",), int(time.time()) + 60))
    scope = "runtime:" + issuer.issue_task_scope("tenant-a", task_id)
    monkeypatch.setenv(settings.image_api_key_env, "isolated-test-placeholder")
    observed = {"calls": 0}

    class ClientStub:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_args, **_kwargs):
            observed["calls"] += 1
            if failure == "timeout":
                raise httpx.TimeoutException("isolated timeout")
            if failure.startswith("http_"):
                return httpx.Response(int(failure.split("_")[1]), json={"error": {"message": "private provider detail"}})
            content = b"GIF89a-invalid" if failure == "unsupported_actual" else picture()
            encoded = "invalid*base64" if failure == "malformed_b64" else base64.b64encode(content).decode()
            return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

    monkeypatch.setattr(httpx, "AsyncClient", ClientStub)
    service = PlatformMCPService(store, issuer, settings)
    with pytest.raises((RuntimeError, httpx.TimeoutException)) as caught:
        asyncio.run(service.image_generation(token, "修改图片", [], "1:1", execution_scope=scope))
    assert "private provider detail" not in str(caught.value)
    assert observed["calls"] == 1
    assert tuple(storage_provider(settings).list_keys("generated/")) == ()
