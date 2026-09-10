"""Create a rollback snapshot and rebuild one tenant on rag-index-v2."""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if sys.path[0] != str(ROOT):
    sys.path[:] = [str(ROOT), *[item for item in sys.path if item != str(ROOT)]]

from app.knowledge import KnowledgeProcessingService
from app.knowledge_metadata import METADATA_SCHEMA_VERSION, RAG_INDEX_VERSION, metadata_dict
from app.product_store import ProductStore
from app.settings import Settings
from app.store import POCStore


def _safe_table_name(value: str) -> str:
    if not re.fullmatch(r"rag_index_backup_[a-z0-9_]+", value):
        raise ValueError("invalid_backup_table_name")
    return value


def create_snapshot(product: ProductStore, tenant_id: str, table_name: str) -> int:
    """Create an immutable tenant-scoped table before replacing any chunks."""
    table = _safe_table_name(table_name)
    with product._store.connection() as conn:
        exists = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=?) AS present",
            (table,),
        ).fetchone()["present"]
        if exists:
            raise RuntimeError("backup_table_already_exists")
        conn.execute(f"CREATE TABLE {table} AS SELECT * FROM knowledge_chunks WHERE tenant_id=?", (tenant_id,))
        count = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
    return int(count)


async def reindex(tenant_id: str, backup_table: str) -> dict:
    settings = Settings.from_env()
    if settings.environment != "production" or not settings.database_url.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("production_postgres_required")
    product = ProductStore(POCStore(settings.database_url))
    snapshot_count = create_snapshot(product, tenant_id, backup_table)
    with product._store.connection() as conn:
        files = conn.execute(
            "SELECT id FROM knowledge_files WHERE tenant_id=? AND status='ready' ORDER BY id",
            (tenant_id,),
        ).fetchall()
    if not files:
        raise RuntimeError("no_ready_knowledge_files")
    service = KnowledgeProcessingService(product, settings)
    for row in files:
        await service.process(tenant_id, row["id"], force=True, raise_errors=True)
    with product._store.connection() as conn:
        chunks = conn.execute(
            "SELECT embedding_version,metadata FROM knowledge_chunks WHERE tenant_id=?",
            (tenant_id,),
        ).fetchall()
    valid = 0
    for row in chunks:
        metadata = metadata_dict(row["metadata"])
        if row["embedding_version"] == RAG_INDEX_VERSION and metadata.get("metadata_schema_version") == METADATA_SCHEMA_VERSION:
            valid += 1
    if valid != len(chunks) or not chunks:
        raise RuntimeError("reindex_validation_failed")
    return {
        "status": "completed",
        "tenant_id": tenant_id,
        "files_reindexed": len(files),
        "chunks_before": snapshot_count,
        "chunks_after": len(chunks),
        "index_version": RAG_INDEX_VERSION,
        "metadata_schema_version": METADATA_SCHEMA_VERSION,
        "backup_table": backup_table,
    }


def main() -> int:
    args = sys.argv[1:]
    if len(args) != 2:
        print(json.dumps({"status": "failed", "message": "usage: tenant_id backup_table"}))
        return 2
    try:
        result = asyncio.run(reindex(args[0], args[1]))
    except Exception as exc:
        known_reason = str(exc) if str(exc) in {
            "backup_table_already_exists",
            "invalid_backup_table_name",
            "no_ready_knowledge_files",
            "production_postgres_required",
            "reindex_validation_failed",
        } else type(exc).__name__
        print(json.dumps({"status": "failed", "message": known_reason}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
