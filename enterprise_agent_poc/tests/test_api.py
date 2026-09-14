from fastapi.testclient import TestClient

from app.main import app


def test_health_and_missing_principal_are_handled_without_starting_a_runtime():
    with TestClient(app) as client:
        health = client.get("/api/v1/poc/health")
        assert health.status_code == 200
        assert health.json()["runtime"] == "openai-codex==0.147.0"

        missing_principal = client.post(
            "/api/v1/poc/runs",
            json={"agent_id": "image-agent", "message": "帮我做海报"},
        )
        assert missing_principal.status_code == 401
        assert missing_principal.json()["error_code"] == "AUTH_REQUIRED"
        assert missing_principal.json()["user_message"] == "缺少 POC API Key。"
        assert missing_principal.json()["request_id"]


def test_product_errors_use_customer_safe_protocol():
    with TestClient(app) as client:
        unauthorized = client.get("/api/v1/workspace")
        assert unauthorized.status_code == 401
        assert unauthorized.json()["error_code"] == "AUTH_REQUIRED"
        assert unauthorized.json()["user_message"]
        assert unauthorized.headers["x-request-id"] == unauthorized.json()["request_id"]

        invalid = client.post("/api/v1/auth/login", json={"account": "x", "password": "short"})
        assert invalid.status_code == 422
        assert invalid.json()["error_code"] == "INVALID_INPUT"
        assert "loc" not in invalid.json()
        assert "type" not in invalid.json()


def test_failed_task_response_hides_internal_error_and_exposes_admin_diagnostic():
    from app.main import _public_knowledge_file, _public_task

    public = _public_task(
        {"id": "task-1", "status": "failed", "error_code": "mcp_error", "user_message": "raw MCP exception /Users/private.py", "run_id": "run-1"},
        include_diagnostic=True,
    )
    assert public["error_code"] == "SERVICE_TEMPORARILY_UNAVAILABLE"
    assert public["user_message"] == "服务暂时不可用，请稍后重试。"
    assert public["diagnostic_id"] == "run-1"
    assert "run_id" not in public
    assert "MCP" not in str(public)

    knowledge = _public_knowledge_file({"id": "file-1", "status": "failed", "error_message": "sqlite exception /Users/private.py"})
    assert knowledge["error_code"] == "KNOWLEDGE_PROCESSING_FAILED"
    assert knowledge["user_message"] == "资料处理失败，请重新上传。"
    assert knowledge["diagnostic_id"] == "file-1"
    assert "error_message" not in knowledge
