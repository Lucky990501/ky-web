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
