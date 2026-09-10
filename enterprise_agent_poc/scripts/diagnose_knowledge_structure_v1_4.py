"""Emit aggregate-only knowledge structure diagnostics for V1.4 acceptance."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if sys.path[0] != str(ROOT):
    sys.path[:] = [str(ROOT), *[item for item in sys.path if item != str(ROOT)]]

from app.knowledge_metadata import CANONICAL_SECTIONS, build_chunk_metadata, metadata_dict
from app.product_store import ProductStore
from app.settings import Settings
from app.store import POCStore


METADATA_FIELDS = (
    "source_file_id",
    "source_filename",
    "sheet_name",
    "section",
    "canonical_section",
    "record_type",
    "entity_type",
    "year",
    "person_name",
    "event_name",
    "index_version",
    "metadata_schema_version",
)


def diagnose(tenant_id: str) -> dict:
    settings = Settings.from_env()
    product = ProductStore(POCStore(settings.database_url))
    with product._store.connection() as conn:
        files = conn.execute("SELECT parsed_text FROM knowledge_files WHERE tenant_id=?", (tenant_id,)).fetchall()
        rows = conn.execute(
            "SELECT c.file_id,c.section,c.title,c.content,c.embedding_version,c.metadata,f.name filename "
            "FROM knowledge_chunks c JOIN knowledge_files f ON f.id=c.file_id "
            "WHERE c.tenant_id=?",
            (tenant_id,),
        ).fetchall()
    coverage = Counter()
    versions = Counter()
    weak_titles = 0
    canonical_counts = Counter()
    projected_canonical_counts = Counter()
    projected_unknown_sections = Counter()
    projected_unknown_field_labels = Counter()
    for row in rows:
        metadata = metadata_dict(row["metadata"])
        versions[str(row["embedding_version"] or "none")] += 1
        weak_titles += bool(re.fullmatch(r"记录\s*\d+", str(row["section"] or "")))
        for field in METADATA_FIELDS:
            coverage[field] += metadata.get(field) not in (None, "")
        canonical_counts[str(metadata.get("canonical_section") or "unknown")] += 1
        projected = build_chunk_metadata(
            source_file_id=row["file_id"],
            source_filename=row["filename"],
            sheet_name=metadata.get("sheet_name"),
            section=row["section"],
            title=row["title"],
            content=row["content"],
        )
        projected_canonical = str(projected.get("canonical_section") or "unknown")
        projected_canonical_counts[projected_canonical] += 1
        if projected_canonical == "unknown":
            projected_unknown_sections[str(row["section"] or "正文")] += 1
            for line in str(row["content"] or "").splitlines():
                match = re.match(r"^\s*([^：:\n]{1,24})[：:]", line)
                if match:
                    projected_unknown_field_labels[match.group(1).strip()] += 1
    top_level = Counter()
    for file_row in files:
        for heading in re.findall(r"(?m)^#\s+(.+)$", file_row["parsed_text"] or ""):
            top_level[heading.strip()] += 1
    return {
        "tenant_id": tenant_id,
        "file_count": len(files),
        "chunk_count": len(rows),
        "embedding_versions": dict(sorted(versions.items())),
        "metadata_coverage": {field: coverage[field] for field in METADATA_FIELDS},
        "canonical_section_counts": dict(sorted(canonical_counts.items())),
        "projected_canonical_section_counts": dict(sorted(projected_canonical_counts.items())),
        "projected_unknown_chunk_count": projected_canonical_counts["unknown"],
        "projected_unknown_section_samples": [name for name, _ in projected_unknown_sections.most_common(20)],
        "projected_unknown_field_labels": dict(projected_unknown_field_labels.most_common(30)),
        "approved_top_level_headings": sorted(name for name in top_level if name in CANONICAL_SECTIONS),
        "top_level_heading_count": sum(top_level.values()),
        "weak_record_title_count": weak_titles,
    }


def main() -> int:
    tenant_id = sys.argv[1] if len(sys.argv) > 1 else "zhiy-e-intelligence"
    print(json.dumps(diagnose(tenant_id), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
