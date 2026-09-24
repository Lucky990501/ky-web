"""Build synthetic branded and legacy DOCX fixtures for local render QA."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

from PIL import Image

from app.document_generator import ActivityPlanContent, ActivityPlanDocumentService, DocumentPresentationOptions
from app.storage import LocalStorage


def _png(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGBA", size, (*color, 255))
    image.putpixel((0, 0), (*color, 0))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def main(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    storage = LocalStorage(output_dir / "objects")
    content = ActivityPlanContent.model_validate_json(
        (Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "activity_plan_d1.json")
        .read_text(encoding="utf-8")
    )
    logo_key = "brand-assets/qa-branded/logos/logo.png"
    storage.put(logo_key, _png((500, 110), (38, 106, 125)), "image/png")
    image_key = "brand-assets/qa-branded/activity-plan-images/example.png"
    storage.put(image_key, _png((480, 270), (168, 93, 59)), "image/png")
    config = {
        "qa-branded": {"brand_name": "示例企业", "brand_logo": logo_key},
        "qa-legacy": {"brand_name": "示例企业"},
    }
    service = ActivityPlanDocumentService(storage, brand_provider=lambda tenant: config[tenant])
    result = {}
    for tenant, name, options in (
        ("qa-branded", "branded.docx", DocumentPresentationOptions(image_paths={0: image_key})),
        ("qa-legacy", "legacy.docx", DocumentPresentationOptions()),
    ):
        generated = service.create(tenant, "qa-user", content, options)
        target = output_dir / name
        target.write_bytes(storage.get(generated.storage_key))
        result[name] = str(target)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
