from fastapi.testclient import TestClient

from app.main import app
from app.auth import verify_password


def test_login_binds_server_side_tenant_and_workspace():
    with TestClient(app) as client:
        rejected = client.get("/api/v1/workspace")
        assert rejected.status_code == 401
        response = client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"})
        assert response.status_code == 200
        me = client.get("/api/v1/me")
        assert me.status_code == 200
        assert me.json()["tenant_id"] == "tenant-a"
        workspace = client.get("/api/v1/workspace")
        assert workspace.status_code == 200
        assert workspace.json()["tenant_name"] == "教育企业 A"


def test_member_cannot_access_enterprise_admin_api():
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"})
        assert client.get("/api/v1/enterprise-config").status_code == 403
        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login", json={"account": "admin@tenant-a.test", "password": "ChangeMe!2026"})
        assert client.get("/api/v1/enterprise-config").status_code == 200


def test_user_can_update_own_profile_and_avatar_only():
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"})
        updated = client.put(
            "/api/v1/me",
            json={
                "display_name": "测试成员",
                "email": "member@tenant-a.test",
                "avatar_data_url": "data:image/png;base64,iVBORw0KGgo=",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["display_name"] == "测试成员"
        assert updated.json()["avatar_url"]
        assert client.get("/api/v1/me/avatar").content == b"\x89PNG\r\n\x1a\n"


def test_enterprise_admin_can_create_list_change_role_and_reset_member_password():
    from app.main import product_store

    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "admin@tenant-a.test", "password": "ChangeMe!2026"})
        created = client.post(
            "/api/v1/members",
            json={"display_name": "首客成员", "email": "first-customer-member@example.invalid", "role": "member"},
        )
        assert created.status_code == 201
        payload = created.json()
        assert set(payload["member"]) == {"id", "email", "display_name", "role", "created_at", "status"}
        assert "password_hash" not in created.text
        assert verify_password(payload["temporary_password"], product_store.user_by_email("first-customer-member@example.invalid")["password_hash"])

        member_id = payload["member"]["id"]
        listed = client.get("/api/v1/members")
        assert listed.status_code == 200
        assert any(item["id"] == member_id and item["status"] == "enabled" for item in listed.json())

        changed = client.put(f"/api/v1/members/{member_id}/role", json={"role": "enterprise_admin"})
        assert changed.status_code == 200
        assert changed.json()["member"]["role"] == "enterprise_admin"

        reset = client.post(f"/api/v1/members/{member_id}/reset-password")
        assert reset.status_code == 200
        assert verify_password(reset.json()["temporary_password"], product_store.user_by_email("first-customer-member@example.invalid")["password_hash"])


def test_member_management_is_tenant_scoped_and_member_is_forbidden():
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"})
        denied = client.get("/api/v1/members")
        assert denied.status_code == 403
        assert denied.json()["error_code"] == "FORBIDDEN"
        assert denied.json()["user_message"] == "仅企业管理员可操作。"

        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login", json={"account": "admin@tenant-a.test", "password": "ChangeMe!2026"})
        assert all(item["email"] != "admin@tenant-b.test" for item in client.get("/api/v1/members").json())


def test_admin_password_reset_invalidates_new_style_member_sessions():
    with TestClient(app) as member_client, TestClient(app) as admin_client:
        assert member_client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"}).status_code == 200
        assert admin_client.post("/api/v1/auth/login", json={"account": "admin@tenant-a.test", "password": "ChangeMe!2026"}).status_code == 200
        member = next(item for item in admin_client.get("/api/v1/members").json() if item["email"] == "member@tenant-a.test")
        reset = admin_client.post(f"/api/v1/members/{member['id']}/reset-password")
        assert reset.status_code == 200
        assert member_client.get("/api/v1/me").status_code == 401
        assert member_client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": reset.json()["temporary_password"]}).status_code == 200
        assert member_client.put("/api/v1/me/password", json={"current_password": reset.json()["temporary_password"], "new_password": "ChangeMe!2026"}).status_code == 200


def test_user_can_change_own_password_without_exposing_credentials():
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"})
        wrong = client.put("/api/v1/me/password", json={"current_password": "WrongPass!2026", "new_password": "NewPassword!2026"})
        assert wrong.status_code == 409
        assert wrong.json()["error_code"] == "INVALID_INPUT"
        changed = client.put("/api/v1/me/password", json={"current_password": "ChangeMe!2026", "new_password": "NewPassword!2026"})
        assert changed.status_code == 200
        assert "password" not in changed.text.lower()
        client.post("/api/v1/auth/logout")
        assert client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"}).status_code == 401
        assert client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "NewPassword!2026"}).status_code == 200
        assert client.put("/api/v1/me/password", json={"current_password": "NewPassword!2026", "new_password": "ChangeMe!2026"}).status_code == 200


def test_member_account_status_schema_defaults_existing_and_new_users_to_enabled():
    from app.main import product_store

    with product_store._store.connection() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    assert "account_status" in columns
    assert all(member["status"] == "enabled" for member in product_store.members("tenant-a"))


def test_admin_can_disable_and_reenable_member_with_clear_feedback():
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "admin@tenant-a.test", "password": "ChangeMe!2026"})
        member = next(item for item in client.get("/api/v1/members").json() if item["email"] == "member@tenant-a.test")
        disabled = client.put(f"/api/v1/members/{member['id']}/status", json={"status": "disabled"})
        assert disabled.status_code == 200
        assert disabled.json()["member"]["status"] == "disabled"
        assert disabled.json()["user_message"] == "成员已停用，该成员将无法继续登录或使用平台。"
        enabled = client.put(f"/api/v1/members/{member['id']}/status", json={"status": "enabled"})
        assert enabled.status_code == 200
        assert enabled.json()["member"]["status"] == "enabled"
        assert enabled.json()["user_message"] == "成员已启用，可以重新登录平台。"
        client.post("/api/v1/auth/logout")
        assert client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"}).status_code == 200


def test_disabled_member_login_fails_closed_without_internal_details():
    from app.main import product_store

    member = product_store.user_by_email("member@tenant-a.test")
    product_store.update_member_status("tenant-a", member["id"], "disabled")
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"})
            assert response.status_code == 403
            assert response.json()["error_code"] == "ACCOUNT_DISABLED"
            assert response.json()["user_message"] == "账号已停用，请联系企业管理员。"
            assert "account_status" not in response.text and "password_hash" not in response.text
    finally:
        product_store.update_member_status("tenant-a", member["id"], "enabled")


def test_disabling_member_invalidates_existing_session_on_next_request():
    from app.main import product_store

    member = product_store.user_by_email("member@tenant-a.test")
    with TestClient(app) as client:
        assert client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"}).status_code == 200
        product_store.update_member_status("tenant-a", member["id"], "disabled")
        try:
            for path in ("/api/v1/me", "/api/v1/workspace", "/api/v1/agents"):
                denied = client.get(path)
                assert denied.status_code == 401
                assert denied.json()["error_code"] == "ACCOUNT_DISABLED"
                assert denied.json()["user_message"] == "账号已停用，请联系企业管理员。"
        finally:
            product_store.update_member_status("tenant-a", member["id"], "enabled")


def test_admin_cannot_change_own_account_status():
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "admin@tenant-a.test", "password": "ChangeMe!2026"})
        me = client.get("/api/v1/me").json()
        response = client.put(f"/api/v1/members/{me['user_id']}/status", json={"status": "disabled"})
        assert response.status_code == 409


def test_last_enabled_enterprise_admin_cannot_be_disabled_or_demoted():
    from app.main import product_store

    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "admin@tenant-a.test", "password": "ChangeMe!2026"})
        me = client.get("/api/v1/me").json()
        assert client.put(f"/api/v1/members/{me['user_id']}/status", json={"status": "disabled"}).status_code == 409
        assert client.put(f"/api/v1/members/{me['user_id']}/role", json={"role": "member"}).status_code == 409
    only_admin = product_store.user_by_email("admin@tenant-b.test")
    for operation in (
        lambda: product_store.update_member_status("tenant-b", only_admin["id"], "disabled"),
        lambda: product_store.update_member_role("tenant-b", only_admin["id"], "member"),
    ):
        try:
            operation()
        except ValueError as exc:
            assert str(exc) == "last_enabled_enterprise_admin"
        else:
            raise AssertionError("last enabled enterprise admin change was not blocked")


def test_member_status_update_is_tenant_scoped():
    from app.main import product_store

    other = product_store.user_by_email("admin@tenant-b.test")
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "admin@tenant-a.test", "password": "ChangeMe!2026"})
        response = client.put(f"/api/v1/members/{other['id']}/status", json={"status": "disabled"})
        assert response.status_code == 404
    assert product_store.user_by_email("admin@tenant-b.test")["account_status"] == "enabled"


def test_member_cannot_update_account_status_and_invalid_status_is_rejected():
    from app.main import product_store

    target = product_store.user_by_email("admin@tenant-a.test")
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"})
        assert client.put(f"/api/v1/members/{target['id']}/status", json={"status": "disabled"}).status_code == 403
        client.post("/api/v1/auth/logout")
        client.post("/api/v1/auth/login", json={"account": "admin@tenant-a.test", "password": "ChangeMe!2026"})
        assert client.put(f"/api/v1/members/{target['id']}/status", json={"status": "paused"}).status_code == 422


def test_password_reset_does_not_reenable_disabled_member():
    from app.main import product_store

    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "admin@tenant-a.test", "password": "ChangeMe!2026"})
        created = client.post("/api/v1/members", json={"display_name": "停用成员", "email": "disabled-reset@example.invalid", "role": "member"}).json()
        member_id = created["member"]["id"]
        client.put(f"/api/v1/members/{member_id}/status", json={"status": "disabled"})
        reset = client.post(f"/api/v1/members/{member_id}/reset-password")
        assert reset.status_code == 200
        assert product_store.user_by_email("disabled-reset@example.invalid")["account_status"] == "disabled"


def test_role_and_account_status_are_independent():
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "admin@tenant-a.test", "password": "ChangeMe!2026"})
        created = client.post("/api/v1/members", json={"display_name": "状态角色独立", "email": "role-status@example.invalid", "role": "member"}).json()["member"]
        client.put(f"/api/v1/members/{created['id']}/status", json={"status": "disabled"})
        changed = client.put(f"/api/v1/members/{created['id']}/role", json={"role": "enterprise_admin"})
        assert changed.status_code == 200
        assert changed.json()["member"]["role"] == "enterprise_admin"
        assert changed.json()["member"]["status"] == "disabled"


def test_agent_catalog_exposes_enabled_tenant_instances_and_separate_agent_threads():
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"})
        agents = client.get("/api/v1/agents")
        assert agents.status_code == 200
        templates = {item["id"]: item for item in agents.json()}
        assert templates["copywriting-agent"]["enabled"] is True
        assert templates["copywriting-agent"]["credit_cost"] == 3
        assert templates["campaign-agent"]["credit_cost"] == 8
        assert templates["copywriting-agent"]["allows_image_generation"] in (False, 0)


def test_copywriting_task_uses_its_own_credit_cost_and_refuses_cross_agent_conversation():
    from app.main import product_store

    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"})
        principal = product_store.user_by_email("member@tenant-a.test")
        before = client.get("/api/v1/workspace").json()["credit_balance"]
        task = product_store.create_task("tenant-a", principal["id"], "copywriting-agent", "课程介绍", None)
        assert task["agent_id"] == "copywriting-agent"
        assert client.get("/api/v1/workspace").json()["credit_balance"] == before
    product_store.set_task(task["id"], "tenant-a", "cancelled", "cancelled", "测试清理")


def test_cross_agent_conversation_is_a_conflict_not_server_error():
    import uuid
    from app.main import product_store, store

    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"})
        user = product_store.user_by_email("member@tenant-a.test")
        conversation_id = str(uuid.uuid4())
        store.save_conversation(conversation_id, "tenant-a", "copywriting-agent", "profile", "thread-test", "test")
        product_store.attach_conversation(conversation_id, user["id"])
        response = client.post("/api/v1/agents/image-agent/runs", json={"message": "跨 Agent 恢复", "conversation_id": conversation_id})
    assert response.status_code == 409


def test_conversation_detail_returns_only_its_owner_visible_messages():
    from app.main import product_store, store
    import uuid

    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"})
        user = product_store.user_by_email("member@tenant-a.test")
        conversation_id = str(uuid.uuid4())
        store.save_conversation(conversation_id, "tenant-a", "copywriting-agent", "profile", "thread-test", "test")
        product_store.attach_conversation(conversation_id, user["id"], "历史文案")
        product_store.add_message(conversation_id, "user", "写招生文案")
        product_store.add_message(conversation_id, "assistant", "这是生成的正文")
        detail = client.get(f"/api/v1/conversations/{conversation_id}")
        assert detail.status_code == 200
        assert [item["content"] for item in detail.json()["messages"]] == ["写招生文案", "这是生成的正文"]


def test_conversation_detail_recovers_legacy_task_context_when_messages_are_absent():
    from app.main import product_store, store
    import uuid

    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"})
        user = product_store.user_by_email("member@tenant-a.test")
        conversation_id = str(uuid.uuid4())
        store.save_conversation(conversation_id, "tenant-a", "copywriting-agent", "profile", "legacy-thread", "test")
        product_store.attach_conversation(conversation_id, user["id"], "旧会话")
        task = product_store.create_task("tenant-a", user["id"], "copywriting-agent", "旧任务内容", conversation_id)
        product_store.set_task(task["id"], "tenant-a", "completed", "completed", "任务完成", response="旧任务正文", conversation_id=conversation_id)
        detail = client.get(f"/api/v1/conversations/{conversation_id}")
        assert detail.status_code == 200
        assert [item["content"] for item in detail.json()["messages"]] == ["旧任务内容", "旧任务正文"]
