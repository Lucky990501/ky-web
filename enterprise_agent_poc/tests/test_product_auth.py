from fastapi.testclient import TestClient

from app.main import app


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
