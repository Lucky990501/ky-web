from __future__ import annotations

import json
import base64
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.document_generator import (
    ActivityPlanContent,
    ActivityPlanDocumentService,
    DOCX_CONTENT_TYPE,
    DocumentGenerationError,
    DocumentPresentationOptions,
    sanitize_filename_component,
)
from app import main
from app.main import app
from app.storage import LocalStorage


FIXTURES = Path(__file__).parent / "fixtures"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": WORD_NS}


def fixture(name: str) -> ActivityPlanContent:
    return ActivityPlanContent.model_validate_json((FIXTURES / f"activity_plan_{name}.json").read_text(encoding="utf-8"))


def service(tmp_path: Path) -> ActivityPlanDocumentService:
    fixed = lambda: datetime(2026, 9, 23, 8, 30, tzinfo=timezone.utc)
    return ActivityPlanDocumentService(LocalStorage(tmp_path / "objects"), clock=fixed)


def initialize_api_state() -> None:
    main.store.seed_demo_data()
    main.product_store.initialize()
    for tenant_id, email, display_name, role in (
        ("tenant-a", "admin@tenant-a.test", "Tenant A 管理员", "enterprise_admin"),
        ("tenant-a", "member@tenant-a.test", "Tenant A 成员", "member"),
        ("tenant-b", "admin@tenant-b.test", "Tenant B 管理员", "enterprise_admin"),
    ):
        main.product_store.create_user(
            tenant_id,
            email,
            main.hash_password("ChangeMe!2026"),
            display_name,
            role,
        )


def document_xml(content: bytes) -> tuple[ElementTree.Element, dict[str, bytes]]:
    from io import BytesIO

    with ZipFile(BytesIO(content)) as archive:
        payload = {name: archive.read(name) for name in archive.namelist()}
    return ElementTree.fromstring(payload["word/document.xml"]), payload


def all_text(root: ElementTree.Element) -> str:
    return "".join(node.text or "" for node in root.findall(".//w:t", NS))


def table_rows(root: ElementTree.Element) -> list[list[str]]:
    table = root.find(".//w:tbl", NS)
    assert table is not None
    return [
        ["".join(node.text or "" for node in cell.findall(".//w:t", NS)) for cell in row.findall("w:tc", NS)]
        for row in table.findall("w:tr", NS)
    ]


@pytest.mark.parametrize("case", ["d1", "d2", "d3"])
def test_d1_d2_d3_generate_structurally_valid_docx(case: str, tmp_path: Path):
    content = fixture(case)
    generated = service(tmp_path).create(
        "tenant-a",
        "user-a",
        content,
        DocumentPresentationOptions(
            brand_name="启明企业服务",
            primary_color="#173B65",
            secondary_color="#EAF1F8",
            footer_text="企业活动方案",
        ),
    )
    stored = (tmp_path / "objects" / generated.storage_key).read_bytes()
    root, payload = document_xml(stored)
    text = all_text(root)

    assert generated.content_type == DOCX_CONTENT_TYPE
    assert generated.filename.startswith("活动方案_") and generated.filename.endswith("_20260923-083000.docx")
    assert len(stored) > 30_000
    assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml"} <= set(payload)
    assert any(name.startswith("word/header") for name in payload)
    assert any(name.startswith("word/footer") for name in payload)
    for heading in ("活动主题", "活动时间", "活动地点", "活动对象", "活动宣发途径", "活动执行表", "邀约文案"):
        assert heading in text
    for value in (
        content.document_title,
        content.activity_theme,
        content.activity_time,
        content.activity_location,
        content.target_audience,
        content.invitation_copy,
    ):
        assert value in text
    rows = table_rows(root)
    assert rows[0] == ["环节", "名称", "说明", "示意图需求"]
    assert len(rows) == len(content.activity_items) + 1
    for item, row in zip(content.activity_items, rows[1:]):
        assert row == [item.phase, item.name, item.description, item.image_requirement]
    if content.pending_items:
        assert "待确认事项" in text
        assert all(item in text for item in content.pending_items)
    else:
        assert "待确认事项" not in text

    page_size = root.find(".//w:sectPr/w:pgSz", NS)
    assert page_size is not None
    assert page_size.get(f"{{{WORD_NS}}}w") == "11906"
    assert page_size.get(f"{{{WORD_NS}}}h") == "16838"
    assert root.find(".//w:tbl/w:tr/w:trPr/w:tblHeader", NS) is not None
    footer_xml = b"".join(value for name, value in payload.items() if name.startswith("word/footer"))
    assert b"PAGE" in footer_xml


def test_d3_preserves_pending_markers_without_completion(tmp_path: Path):
    content = fixture("d3")
    generated = service(tmp_path).create("tenant-a", "user-a", content, DocumentPresentationOptions())
    root, _ = document_xml((tmp_path / "objects" / generated.storage_key).read_bytes())
    text = all_text(root)
    expected_count = json.dumps(content.model_dump(), ensure_ascii=False).count("[待确认]")
    assert text.count("[待确认]") == expected_count
    assert "已确认" not in text


def test_filename_is_windows_safe_bounded_and_storage_is_unique(tmp_path: Path):
    assert sanitize_filename_component('../CON:<教师节>|?*  ') == "CON__教师节"
    content = fixture("d2").model_copy(update={"activity_theme": '../CON:<教师节>|?*  '})
    generator = service(tmp_path)
    first = generator.create("tenant-a", "user-a", content, DocumentPresentationOptions())
    second = generator.create("tenant-a", "user-a", content, DocumentPresentationOptions())
    assert first.document_id != second.document_id
    assert len(first.filename) <= 100
    assert not any(char in first.filename for char in '<>:"/\\|?*')
    assert first.storage_key.startswith(f"generated-documents/tenant-a/user-a/{first.document_id}/")
    assert Path(first.storage_key).parts[-1] == first.filename


def test_logo_must_be_current_tenant_object_and_cannot_read_server_paths(tmp_path: Path):
    generator = service(tmp_path)
    content = fixture("d2")
    for path in ("../../etc/passwd", "C:\\Windows\\system.ini", "/etc/passwd", "brand-assets/tenant-b/logos/logo.png"):
        with pytest.raises(DocumentGenerationError):
            generator.create("tenant-a", "user-a", content, DocumentPresentationOptions(logo_path=path))


def test_logo_is_embedded_from_bounded_tenant_storage_key(tmp_path: Path):
    # 1x1 transparent PNG.
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    storage = LocalStorage(tmp_path / "objects")
    key = "brand-assets/tenant-a/logos/logo.png"
    storage.put(key, png, "image/png")
    fixed = lambda: datetime(2026, 9, 23, 8, 30, tzinfo=timezone.utc)
    generator = ActivityPlanDocumentService(storage, clock=fixed)
    result = generator.create(
        "tenant-a", "user-a", fixture("d2"), DocumentPresentationOptions(logo_path=key)
    )
    _, payload = document_xml((tmp_path / "objects" / result.storage_key).read_bytes())
    assert any(name.startswith("word/media/") for name in payload)


def test_contract_rejects_unknown_fields_and_oversized_arrays():
    data = fixture("d2").model_dump()
    data["new_content_contract"] = "not allowed"
    with pytest.raises(ValidationError):
        ActivityPlanContent.model_validate(data)
    data = fixture("d2").model_dump()
    data["promotion_channels"] = ["channel"] * 31
    with pytest.raises(ValidationError):
        ActivityPlanContent.model_validate(data)


def test_authenticated_api_creates_downloads_and_tenant_scopes_docx():
    request = {
        "content": fixture("d1").model_dump(),
        "presentation": {"brand_name": "启明教育", "primary_color": "#173B65", "footer_text": "正式活动方案"},
    }
    initialize_api_state()
    client = TestClient(app)
    try:
        assert client.post(
            "/api/v1/documents/activity-plan", json=request
        ).status_code == 401
        assert client.post(
            "/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"}
        ).status_code == 200
        response = client.post("/api/v1/documents/activity-plan", json=request)
        assert response.status_code == 201
        result = response.json()
        assert set(result) == {"document_id", "filename", "content_type", "download_url"}
        assert result["content_type"] == DOCX_CONTENT_TYPE
        download = client.get(result["download_url"])
        assert download.status_code == 200
        assert download.headers["content-type"] == DOCX_CONTENT_TYPE
        assert "attachment" in download.headers["content-disposition"]
        document_xml(download.content)

        client.post("/api/v1/auth/logout")
        assert client.post(
            "/api/v1/auth/login", json={"account": "admin@tenant-b.test", "password": "ChangeMe!2026"}
        ).status_code == 200
        assert client.get(result["download_url"]).status_code == 404
    finally:
        client.close()


def test_api_rejects_arbitrary_logo_path_without_creating_document():
    request = {
        "content": fixture("d2").model_dump(),
        "presentation": {"logo_path": "../../etc/passwd"},
    }
    initialize_api_state()
    client = TestClient(app)
    try:
        assert client.post(
            "/api/v1/auth/login", json={"account": "member@tenant-a.test", "password": "ChangeMe!2026"}
        ).status_code == 200
        response = client.post("/api/v1/documents/activity-plan", json=request)
        assert response.status_code == 400
        assert response.json()["error_code"] == "INVALID_INPUT"
        assert "etc/passwd" not in response.text
    finally:
        client.close()
