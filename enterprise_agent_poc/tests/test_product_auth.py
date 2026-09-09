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
