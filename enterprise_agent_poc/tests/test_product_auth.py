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
