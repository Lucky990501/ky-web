"""Local UI test bridge to the unchanged ASGI document API.

Synthetic tenants/users; fresh SQLite and local objects only. No lifespan,
Native/Stage2 claim, task execution, Provider, or network access. Requests are
allowlisted and use the real login/session and document handlers, not mocks.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
if os.environ.get("UI_FIXTURE_SITE_PACKAGES"):
    # Optional installed pure-Python test dependencies, behind this interpreter.
    sys.path.append(os.environ["UI_FIXTURE_SITE_PACKAGES"])

ROOT = Path(tempfile.mkdtemp(prefix="customer-word-ui-fixture-"))
(ROOT / "isolated-ui-fixture.marker").write_text("synthetic-local-ui-only", encoding="utf-8")
os.environ.update(
    APP_ENV="test", ENTERPRISE_POC_DATABASE_URL=f"sqlite:///{ROOT / 'fixture.db'}",
    ENTERPRISE_POC_DATA_DIR=str(ROOT / "data"),
    ENTERPRISE_POC_OBJECT_STORAGE_DIR=str(ROOT / "objects"),
    ENTERPRISE_POC_OBJECT_STORAGE_PROVIDER="local", ENTERPRISE_POC_TASK_QUEUE="local",
    ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA="false", ENTERPRISE_POC_SECURE_COOKIES="false",
    ENTERPRISE_POC_TOKEN_SECRET="isolated-ui-fixture-only-not-a-credential",
    ENTERPRISE_POC_MCP_URL="http://127.0.0.1:1/disabled",
    ENTERPRISE_POC_MODEL_BASE_URL="http://127.0.0.1:1/disabled",
    ENTERPRISE_POC_AGENT_RUNTIME_TEST_PRODUCTION_ENABLED="false",
    DEEPSEEK_API_KEY="isolated-fixture-no-provider", GATEWAY_API_TOKEN="isolated-fixture-no-provider",
    EMBEDDING_PROVIDER="local-hash",
)
os.environ.pop("REDIS_URL", None)

original_connect = socket.socket.connect

def deny_network(*_args, **_kwargs):
    raise RuntimeError("Network access is forbidden in the document UI fixture")

def guarded_connect(sock, address):
    # Windows asyncio implements its internal self-pipe as a loopback pair.
    # Allow only the stdlib socketpair caller, never an application HTTP client.
    caller = sys._getframe(1).f_code
    pair = getattr(socket, "_fallback_socketpair", None)
    if pair and caller is pair.__code__ and address[0] in {"127.0.0.1", "::1"}:
        return original_connect(sock, address)
    return deny_network()

socket.socket.connect = guarded_connect
socket.create_connection = deny_network

from app.settings import settings
from app.store import POCStore

assert settings.environment == "test" and settings.task_queue == "local"
assert settings.object_storage_provider == "local"
assert settings.database_path and settings.database_path.resolve().is_relative_to(ROOT.resolve())
assert settings.data_dir.resolve().is_relative_to(ROOT.resolve())
assert settings.object_storage_dir.resolve().is_relative_to(ROOT.resolve())
original_connection = POCStore.connection

def local_connection(store):
    assert store.database_path and store.database_path.resolve().is_relative_to(ROOT.resolve())
    return original_connection(store)

POCStore.connection = local_connection

from fastapi.testclient import TestClient
from app import main
from app.document_generator import DOCX_CONTENT_TYPE

main.store.seed_demo_data()
main.product_store.initialize()
main.skill_registry.initialize()
USERS = {
    "member_a": ("tenant-a", "ui-member@tenant-a.test", "member"),
    "peer_a": ("tenant-a", "ui-peer@tenant-a.test", "member"),
    "admin_b": ("tenant-b", "ui-admin@tenant-b.test", "enterprise_admin"),
}
for tenant, email, role in USERS.values():
    main.product_store.create_user(tenant, email, main.hash_password("IsolatedFixture2026!"), "隔离 UI Fixture", role)

client = TestClient(main.app)
created = {}
checks = []
requests = []
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

def login(alias):
    tenant, email, role = USERS[alias]
    client.cookies.clear()
    response = client.post("/api/v1/auth/login", json={"account": email, "password": "IsolatedFixture2026!"})
    assert response.status_code == 200
    user = response.json()["user"]
    assert user["tenant_id"] == tenant and user["role"] == role
    return user

def verify_document(content, body):
    assert len(body) > 30000 and body.startswith(b"PK")
    with ZipFile(BytesIO(body)) as archive:
        assert {"[Content_Types].xml", "word/document.xml", "word/styles.xml"} <= set(archive.namelist())
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    text = "".join(node.text or "" for node in root.findall(".//w:t", NS))
    for key in ["document_title", "activity_theme", "activity_time", "activity_location", "target_audience", "invitation_copy"]:
        assert content[key] in text, key
    for value in content["promotion_channels"] + content["pending_items"]:
        assert value in text
    rows = [["".join(node.text or "" for node in cell.findall(".//w:t", NS)) for cell in row.findall("w:tc", NS)] for row in root.findall(".//w:tbl/w:tr", NS)]
    assert rows[0] == ["环节", "名称", "说明", "示意图需求"]
    assert rows[1:] == [[item[key] for key in ["phase", "name", "description", "image_requirement"]] for item in content["activity_items"]]
    return {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "zip_xml": "PASS", "source_content": "PASS", "table_and_invitation": "PASS", "word_desktop_open": "NOT_VERIFIED"}

def request(command):
    method, path = command["method"], command["path"]
    assert method in {"GET", "POST"}
    assert path == "/api/v1/me" and method == "GET" or path == "/api/v1/documents/activity-plan" and method == "POST" or path.startswith("/api/v1/documents/activity-plan/") and method == "GET"
    response = client.request(method, path, json=command.get("body") if method == "POST" else None)
    requests.append({"method": method, "path": path, "status": response.status_code})
    headers = {key: value for key, value in response.headers.items() if key in {"content-type", "content-disposition", "cache-control", "x-content-type-options"}}
    if method == "POST" and response.status_code == 201:
        metadata = response.json()
        assert set(metadata) == {"document_id", "filename", "content_type", "download_url"}
        assert metadata["content_type"] == DOCX_CONTENT_TYPE
        assert metadata["download_url"] == "/api/v1/documents/activity-plan/" + metadata["document_id"]
        principal = client.get("/api/v1/me").json()
        stored = main.document_service.get(principal["tenant_id"], principal["user_id"], metadata["document_id"])
        verification = verify_document(command["body"]["content"], stored.content)
        created[metadata["download_url"]] = {"content": command["body"]["content"], "metadata": metadata, "verification": verification}
        checks.append({"name": "real_document_persistence_" + metadata["document_id"], "status": "PASS_ISOLATED_REAL_API", **verification})
    if response.status_code == 200 and path in created:
        assert headers["content-type"] == DOCX_CONTENT_TYPE
        assert "filename*=UTF-8''" in headers["content-disposition"]
        assert headers["cache-control"] == "private, no-store"
        assert headers["x-content-type-options"] == "nosniff"
        verify_document(created[path]["content"], response.content)
    result = {"status": response.status_code, "headers": headers}
    if "application/json" in headers.get("content-type", ""):
        result["json"] = response.json()
    else:
        result["base64"] = base64.b64encode(response.content).decode("ascii")
    return result

def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)

emit({"ready": True, "user": login("member_a"), "mode": "ISOLATED_ASGI_REAL_AUTH_AND_DOCX_ONLY"})
for line in sys.stdin:
    try:
        command = json.loads(line)
        operation = command.get("operation", "request")
        if operation == "login":
            result = {"user": login(command["alias"])}
        elif operation == "logout":
            response = client.post("/api/v1/auth/logout")
            assert response.status_code == 200
            result = {"status": 200}
        elif operation == "summary":
            result = {"mode": "LOCAL_SQLITE_AND_OBJECTS_FIXTURE", "checks": checks, "requests": requests, "document_count": len(created), "provider_calls": 0, "production_writes": 0, "native_startup": "NOT_EXECUTED", "source_tasks": "FIXTURE_ONLY"}
        elif operation == "stop":
            emit({"stopped": True})
            break
        else:
            assert operation == "request"
            result = request(command)
        emit(result)
    except Exception as exc:
        emit({"bridge_error": type(exc).__name__, "message": str(exc)[:300]})
        break
client.close()
