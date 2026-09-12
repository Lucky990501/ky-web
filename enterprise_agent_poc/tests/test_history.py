import base64
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.auth import SessionIssuer, hash_password
from app.product_store import ProductStore
from app.storage import StorageObjectNotFound, StorageUnavailable, storage_provider
from app.store import POCStore


PASSWORD = "HistoryTest!2026"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def history_env(tmp_path, monkeypatch):
    database_path = tmp_path / "history.db"
    raw_store = POCStore(database_path)
    raw_store.seed_demo_data()
    product = ProductStore(raw_store)
    product.initialize()
    password_hash = hash_password(PASSWORD, salt=b"h" * 16)
    for tenant_id, email, name, role in (
        ("tenant-a", "owner@history.test", "Owner", "member"),
        ("tenant-a", "other@history.test", "Other", "member"),
        ("tenant-a", "admin@history.test", "Admin", "enterprise_admin"),
        ("tenant-b", "foreign@history.test", "Foreign", "member"),
    ):
        product.create_user(tenant_id, email, password_hash, name, role)

    runtime_settings = replace(
        app_main.settings,
        data_dir=tmp_path / "runtime",
        database_url=f"sqlite:///{database_path.as_posix()}",
        database_path=database_path,
        object_storage_dir=tmp_path / "objects",
        object_storage_provider="local",
        token_secret="history-test-session-secret",
        secure_cookies=False,
        bootstrap_demo_data=False,
        environment="test",
    )
    monkeypatch.setattr(app_main, "store", raw_store)
    monkeypatch.setattr(app_main, "product_store", product)
    monkeypatch.setattr(app_main, "settings", runtime_settings)
    monkeypatch.setattr(app_main, "sessions", SessionIssuer(runtime_settings.token_secret))

    client = TestClient(app_main.app)
    assert client.post(
        "/api/v1/auth/login",
        json={"account": "owner@history.test", "password": PASSWORD},
    ).status_code == 200
    try:
        yield SimpleNamespace(
            client=client,
            store=raw_store,
            product=product,
            settings=runtime_settings,
            owner=product.user_by_email("owner@history.test"),
            admin=product.user_by_email("admin@history.test"),
        )
    finally:
        client.close()


def add_conversation(env, *, agent_id, prompt, title="新会话", with_image=False):
    conversation_id = str(uuid4())
    env.store.save_conversation(
        conversation_id,
        "tenant-a",
        agent_id,
        f"profile-{agent_id}",
        f"thread-{conversation_id}",
        "test-runtime",
    )
    env.product.attach_conversation(conversation_id, env.owner["id"], title)
    task = env.product.create_task("tenant-a", env.owner["id"], agent_id, prompt, conversation_id)
    env.product.add_message(
        conversation_id,
        "user",
        prompt,
        message_id=f"task:{task['id']}:user",
    )
    env.product.add_message(
        conversation_id,
        "assistant",
        "已完成。",
        message_id=f"task:{task['id']}:assistant",
    )
    env.product.set_task(
        task["id"],
        "tenant-a",
        "completed",
        "completed",
        "任务完成",
        run_id=f"run-{task['id']}",
        response="已完成。",
        conversation_id=conversation_id,
    )
    generation_id = storage_key = None
    if with_image:
        storage_key = f"generated/tenant-a/历史 海报 #{uuid4().hex}.png"
        storage_provider(env.settings).put(storage_key, PNG, "image/png")
        generation_id = env.product.create_generation(
            "tenant-a",
            env.owner["id"],
            conversation_id,
            task["id"],
            storage_key,
            "image-gateway",
            "test-image-model",
        )
    return SimpleNamespace(
        conversation_id=conversation_id,
        task=task,
        generation_id=generation_id,
        storage_key=storage_key,
    )


def login(client, account):
    client.post("/api/v1/auth/logout")
    response = client.post(
        "/api/v1/auth/login",
        json={"account": account, "password": PASSWORD},
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "path",
    ("/conversations", "/agents/image", "/agents/copywriting", "/agents/campaign"),
)
def test_history_and_agent_deep_links_serve_the_current_shell(history_env, path):
    response = history_env.client.get(path)

    assert response.status_code == 200
    assert "workbench.js?v=history-v1" in response.text


def test_history_classifies_projects_and_keeps_task_and_image_scope(history_env):
    image_record = add_conversation(
        history_env,
        agent_id="image-agent",
        prompt="制作秋季课程招生海报",
        with_image=True,
    )
    copy_record = add_conversation(
        history_env,
        agent_id="copywriting-agent",
        prompt="撰写秋季课程公众号推文",
        title="秋季公众号项目",
    )

    response = history_env.client.get("/api/v1/conversations")
    assert response.status_code == 200
    projects = {item["id"]: item for item in response.json()}

    image_project = projects[image_record.conversation_id]
    assert image_project["agent"]["id"] == "image-agent"
    assert image_project["agent"]["name"] == "图片生成智能体"
    assert image_project["project"] == {
        "id": image_record.conversation_id,
        "name": "制作秋季课程招生海报",
        "type": "图片生成项目",
    }
    assert image_project["stored_title"] == "新会话"
    assert image_project["latest_task"]["id"] == image_record.task["id"]
    assert image_project["latest_status"] == "completed"
    assert image_project["task_count"] == 1
    assert image_project["image_count"] == 1

    copy_project = projects[copy_record.conversation_id]
    assert copy_project["project"]["type"] == "文案创作项目"
    assert copy_project["project"]["name"] == "秋季公众号项目"
    assert copy_project["task_count"] == 1
    assert copy_project["image_count"] == 0
    assert copy_project["latest_generation"] is None

    first_page = history_env.client.get("/api/v1/conversations?limit=1&offset=0").json()
    second_page = history_env.client.get("/api/v1/conversations?limit=1&offset=1").json()
    assert len(first_page) == len(second_page) == 1
    assert first_page[0]["id"] != second_page[0]["id"]
    assert {first_page[0]["id"], second_page[0]["id"]} == {
        image_record.conversation_id,
        copy_record.conversation_id,
    }

    image_detail = history_env.client.get(
        f"/api/v1/conversations/{image_record.conversation_id}"
    ).json()
    copy_detail = history_env.client.get(
        f"/api/v1/conversations/{copy_record.conversation_id}"
    ).json()
    assert [item["id"] for item in image_detail["tasks"]] == [image_record.task["id"]]
    assert [item["id"] for item in copy_detail["tasks"]] == [copy_record.task["id"]]
    assistant = next(
        item for item in image_detail["messages"] if item["role"] == "assistant"
    )
    assert assistant["task_id"] == image_record.task["id"]
    assert assistant["generation"]["task_id"] == image_record.task["id"]


def test_image_url_is_consistent_and_returns_real_image_bytes(history_env):
    record = add_conversation(
        history_env,
        agent_id="image-agent",
        prompt="制作一张可回看的历史海报",
        with_image=True,
    )
    expected = f"/api/v1/storage/{quote(record.storage_key, safe='/')}"
    assert "%20" in expected and "%23" in expected

    conversation = next(
        item
        for item in history_env.client.get("/api/v1/conversations").json()
        if item["id"] == record.conversation_id
    )
    detail = history_env.client.get(
        f"/api/v1/conversations/{record.conversation_id}"
    ).json()
    task = history_env.client.get(f"/api/v1/tasks/{record.task['id']}").json()
    generation = next(
        item
        for item in history_env.client.get("/api/v1/generations").json()
        if item["id"] == record.generation_id
    )
    assistant = next(
        item
        for item in detail["messages"]
        if item["id"] == f"task:{record.task['id']}:assistant"
    )
    surfaces = {
        "conversation_list": conversation["latest_generation"],
        "conversation_detail": detail["generations"][0],
        "assistant_message": assistant["generation"],
        "task": task["generation"],
        "generation_list": generation,
    }
    for surface_name, surface in surfaces.items():
        assert surface["content_url"] == expected, surface_name
        assert surface["image_url"] == expected, surface_name
        response = history_env.client.get(surface["content_url"])
        assert response.status_code == 200, surface_name
        assert response.content == PNG, surface_name
        assert response.headers["content-type"].split(";", 1)[0] == "image/png"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"


def test_history_and_storage_enforce_owner_and_tenant_boundaries(history_env):
    record = add_conversation(
        history_env,
        agent_id="image-agent",
        prompt="验证历史图片权限",
        with_image=True,
    )
    image_url = f"/api/v1/storage/{quote(record.storage_key, safe='/')}"

    history_env.client.post("/api/v1/auth/logout")
    assert history_env.client.get(
        f"/api/v1/conversations/{record.conversation_id}"
    ).status_code == 401
    assert history_env.client.get(image_url).status_code == 401

    login(history_env.client, "other@history.test")
    assert history_env.client.get(
        f"/api/v1/conversations/{record.conversation_id}"
    ).status_code == 404
    assert history_env.client.get(image_url).status_code == 404
    assert history_env.client.get(f"/api/v1/tasks/{record.task['id']}").status_code == 404
    assert record.conversation_id not in {
        item["id"] for item in history_env.client.get("/api/v1/conversations").json()
    }
    assert record.generation_id not in {
        item["id"] for item in history_env.client.get("/api/v1/generations").json()
    }

    login(history_env.client, "foreign@history.test")
    assert history_env.client.get(
        f"/api/v1/conversations/{record.conversation_id}"
    ).status_code == 404
    assert history_env.client.get(image_url).status_code == 404
    assert history_env.client.get(f"/api/v1/tasks/{record.task['id']}").status_code == 404
    assert record.conversation_id not in {
        item["id"] for item in history_env.client.get("/api/v1/conversations").json()
    }
    assert record.generation_id not in {
        item["id"] for item in history_env.client.get("/api/v1/generations").json()
    }


def test_enterprise_asset_keeps_image_readable_without_exposing_other_tenants(history_env):
    record = add_conversation(
        history_env,
        agent_id="image-agent",
        prompt="保存为企业素材",
        with_image=True,
    )
    image_url = f"/api/v1/storage/{quote(record.storage_key, safe='/')}"
    saved_response = history_env.client.post(
        f"/api/v1/generations/{record.generation_id}/save-to-assets",
        json={"name": "历史海报素材"},
    )
    assert saved_response.status_code == 200
    asset_id = saved_response.json()["id"]
    assert history_env.client.delete(
        f"/api/v1/generations/{record.generation_id}"
    ).status_code == 200

    login(history_env.client, "other@history.test")
    assert history_env.client.get(image_url).status_code == 200

    login(history_env.client, "foreign@history.test")
    assert history_env.client.get(image_url).status_code == 404

    login(history_env.client, "admin@history.test")
    assert history_env.client.delete(f"/api/v1/assets/{asset_id}").status_code == 200

    login(history_env.client, "other@history.test")
    assert history_env.client.get(image_url).status_code == 404


def test_deleted_personal_generation_without_asset_remains_private(history_env):
    record = add_conversation(
        history_env,
        agent_id="image-agent",
        prompt="未保存为企业素材的私有海报",
        with_image=True,
    )
    image_url = f"/api/v1/storage/{quote(record.storage_key, safe='/')}"
    assert history_env.client.delete(
        f"/api/v1/generations/{record.generation_id}"
    ).status_code == 200

    login(history_env.client, "other@history.test")
    assert history_env.client.get(image_url).status_code == 404


def test_legacy_history_without_messages_restores_assistant_generation_link(history_env):
    record = add_conversation(
        history_env,
        agent_id="image-agent",
        prompt="旧版任务生成的活动海报",
        with_image=True,
    )
    with history_env.store.connection() as conn:
        conn.execute(
            "DELETE FROM messages WHERE conversation_id=?",
            (record.conversation_id,),
        )

    detail = history_env.client.get(
        f"/api/v1/conversations/{record.conversation_id}"
    ).json()
    assistant = next(
        message for message in detail["messages"] if message["role"] == "assistant"
    )
    expected = f"/api/v1/storage/{quote(record.storage_key, safe='/')}"
    assert assistant["id"] == f"legacy-assistant-{record.task['id']}"
    assert assistant["task_id"] == record.task["id"]
    assert assistant["generation"]["task_id"] == record.task["id"]
    assert assistant["generation"]["content_url"] == expected
    assert assistant["generation"]["image_url"] == expected


def test_storage_provider_errors_are_safely_mapped(history_env, monkeypatch):
    record = add_conversation(
        history_env,
        agent_id="image-agent",
        prompt="验证图片存储异常",
        with_image=True,
    )
    image_url = f"/api/v1/storage/{quote(record.storage_key, safe='/')}"

    class MissingStorage:
        def get(self, _key):
            raise StorageObjectNotFound("missing")

    monkeypatch.setattr(app_main, "storage_provider", lambda _settings: MissingStorage())
    missing = history_env.client.get(image_url)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "图片文件不存在。"

    class UnavailableStorage:
        def get(self, _key):
            raise StorageUnavailable("provider unavailable")

    monkeypatch.setattr(app_main, "storage_provider", lambda _settings: UnavailableStorage())
    unavailable = history_env.client.get(image_url)
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "图片存储暂时不可用，请稍后重试。"


def test_sse_serializes_postgres_style_datetimes_and_generation(history_env, monkeypatch):
    now = datetime(2026, 9, 12, 8, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(
        history_env.product,
        "task_events_since",
        lambda *_args, **_kwargs: [
            {"id": 1, "stage": "completed", "message": "任务已完成", "created_at": now}
        ],
    )
    monkeypatch.setattr(
        history_env.product,
        "task",
        lambda *_args, **_kwargs: {
            "status": "completed",
            "user_message": None,
            "final_response": "已完成。",
            "conversation_id": "history-conversation",
            "generation": {
                "id": "history-generation",
                "content_url": "/api/v1/storage/generated/test.png",
                "created_at": now,
            },
        },
    )
    response = history_env.client.get("/api/v1/tasks/history-task/events")
    assert response.status_code == 200
    assert "event: progress" in response.text
    assert "event: complete" in response.text
    assert '"created_at": "2026-09-12T08:30:00+00:00"' in response.text
    assert '"content_url": "/api/v1/storage/generated/test.png"' in response.text


def test_deleted_project_has_no_broken_history_link_and_unknown_agent_does_not_break_list(history_env):
    record = add_conversation(
        history_env,
        agent_id="image-agent",
        prompt="删除历史项目后仍保留生成记录",
        with_image=True,
    )
    assert history_env.client.delete(
        f"/api/v1/conversations/{record.conversation_id}"
    ).status_code == 200
    assert record.conversation_id not in {
        item["id"] for item in history_env.client.get("/api/v1/conversations").json()
    }
    assert history_env.client.get(
        f"/api/v1/conversations/{record.conversation_id}"
    ).status_code == 404
    generation = next(
        item
        for item in history_env.client.get("/api/v1/generations").json()
        if item["id"] == record.generation_id
    )
    assert generation["project_available"] is False
    assert generation["project"] is None

    retired_conversation_id = str(uuid4())
    history_env.store.save_conversation(
        retired_conversation_id,
        "tenant-a",
        "retired-agent",
        "retired-profile",
        "retired-thread",
        "test-runtime",
    )
    history_env.product.attach_conversation(
        retired_conversation_id,
        history_env.owner["id"],
        "旧智能体历史",
    )
    projects = {item["id"]: item for item in history_env.client.get("/api/v1/conversations").json()}
    assert projects[retired_conversation_id]["agent"] == {
        "id": "retired-agent",
        "name": "历史智能体",
        "icon": "bot",
    }
    assert projects[retired_conversation_id]["project"]["type"] == "历史创作项目"
