"""Build an isolated Qwen candidate index without replacing rag-index-v2."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if sys.path[0] != str(ROOT):
    sys.path[:] = [str(ROOT), *[item for item in sys.path if item != str(ROOT)]]

from app.embedding_profiles import AliyunBailianEmbeddingProvider, aliyun_bailian_profile_from_env
from app.knowledge_metadata import embedding_input, metadata_dict
from app.settings import Settings
from app.store import POCStore


PROFILE_ID = "qwen3-7-text-embedding-1536"
OLD_PROFILE_ID = "production-rag-index-v2"
OLD_INDEX_VERSION = "rag-index-v2"


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.10g}" for value in vector) + "]"


def inspect_state(store: POCStore, tenant_id: str, expected_chunks: int) -> dict:
    if not store.is_postgres:
        raise RuntimeError("production_postgres_required")
    with store.connection() as conn:
        tables = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='embedding_index_profiles') AS profiles,"
            "EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='knowledge_chunk_embeddings') AS embeddings"
        ).fetchone()
        if not tables["profiles"] or not tables["embeddings"]:
            raise RuntimeError("migration_006_required")
        old = conn.execute(
            "SELECT COUNT(*) AS total,COUNT(*) FILTER (WHERE embedding_version=?) AS versioned,"
            "COUNT(*) FILTER (WHERE embedding IS NOT NULL) AS vectors FROM knowledge_chunks WHERE tenant_id=?",
            (OLD_INDEX_VERSION, tenant_id),
        ).fetchone()
        candidate = conn.execute(
            "SELECT COUNT(*) AS total,COUNT(*) FILTER (WHERE provider='aliyun-bailian' AND model='qwen3.7-text-embedding' "
            "AND dimension=1536 AND index_version='rag-index-v3-qwen') AS valid "
            "FROM knowledge_chunk_embeddings WHERE tenant_id=? AND index_version='rag-index-v3-qwen'",
            (tenant_id,),
        ).fetchone()
    state = {
        "expected_chunks": expected_chunks,
        "old_total": int(old["total"]),
        "old_versioned": int(old["versioned"]),
        "old_vectors": int(old["vectors"]),
        "candidate_total": int(candidate["total"]),
        "candidate_valid": int(candidate["valid"]),
    }
    if state["old_total"] != expected_chunks or state["old_versioned"] != expected_chunks or state["old_vectors"] != expected_chunks:
        raise RuntimeError("rag_index_v2_integrity_failed")
    if state["candidate_total"] not in {0, expected_chunks} or state["candidate_total"] != state["candidate_valid"]:
        raise RuntimeError("candidate_index_partial_or_invalid")
    return state


async def build_candidate(tenant_id: str, expected_chunks: int, execute: bool) -> dict:
    settings = Settings.from_env()
    if settings.environment != "production":
        raise RuntimeError("production_environment_required")
    profile = aliyun_bailian_profile_from_env()
    store = POCStore(settings.database_url)
    before = inspect_state(store, tenant_id, expected_chunks)
    if not execute:
        return {"status": "dry_run", "profile": profile.index_version, **before, "database_writes": 0, "provider_requests": 0}
    if before["candidate_total"] == expected_chunks:
        return {"status": "already_complete", "profile": profile.index_version, **before}

    with store.connection() as conn:
        rows = conn.execute(
            "SELECT c.id,c.content,c.metadata FROM knowledge_chunks c "
            "JOIN knowledge_files f ON f.id=c.file_id AND f.tenant_id=c.tenant_id "
            "WHERE c.tenant_id=? AND c.embedding_version=? AND f.status='ready' ORDER BY c.id",
            (tenant_id, OLD_INDEX_VERSION),
        ).fetchall()
    if len(rows) != expected_chunks:
        raise RuntimeError("ready_chunk_count_mismatch")

    provider = AliyunBailianEmbeddingProvider(profile)
    generated: list[tuple[str, list[float]]] = []
    for offset in range(0, len(rows), 20):
        batch = rows[offset : offset + 20]
        texts = [embedding_input(row["content"], metadata_dict(row["metadata"])) for row in batch]
        vectors = await provider.embed_documents(texts)
        if len(vectors) != len(batch):
            raise RuntimeError("candidate_embedding_count_mismatch")
        generated.extend((row["id"], vector) for row, vector in zip(batch, vectors, strict=True))
    if len(generated) != expected_chunks or any(len(vector) != profile.dimension for _, vector in generated):
        raise RuntimeError("candidate_embedding_validation_failed")

    with store.connection() as conn:
        old_domain = urlparse(settings.embedding_base_url).hostname or None
        conn.execute(
            "INSERT INTO embedding_index_profiles(id,provider,model,dimension,index_version,state,base_url_domain) "
            "VALUES (?,?,?,?,?,'active',?) ON CONFLICT (index_version) DO NOTHING",
            (
                OLD_PROFILE_ID,
                settings.embedding_provider,
                settings.embedding_model,
                settings.embedding_dimension,
                OLD_INDEX_VERSION,
                old_domain,
            ),
        )
        conn.execute(
            "INSERT INTO embedding_index_profiles(id,provider,model,dimension,index_version,state,base_url_domain) "
            "VALUES (?,?,?,?,?,'candidate',?) ON CONFLICT (index_version) DO NOTHING",
            (PROFILE_ID, profile.provider, profile.model, profile.dimension, profile.index_version, profile.base_url_domain),
        )
        for chunk_id, vector in generated:
            conn.execute(
                "INSERT INTO knowledge_chunk_embeddings(tenant_id,chunk_id,profile_id,provider,model,dimension,index_version,embedding) "
                "VALUES (?,?,?,?,?,?,?,?::vector)",
                (tenant_id, chunk_id, PROFILE_ID, profile.provider, profile.model, profile.dimension, profile.index_version, _vector_literal(vector)),
            )
    after = inspect_state(store, tenant_id, expected_chunks)
    if after["candidate_valid"] != expected_chunks:
        raise RuntimeError("candidate_index_validation_failed")
    return {"status": "completed", "profile": profile.index_version, **after, "old_index_preserved": True}


def safe_failure(exc: Exception) -> dict:
    known = {
        "production_postgres_required",
        "production_environment_required",
        "migration_006_required",
        "rag_index_v2_integrity_failed",
        "candidate_index_partial_or_invalid",
        "ready_chunk_count_mismatch",
        "candidate_embedding_count_mismatch",
        "candidate_embedding_validation_failed",
        "candidate_index_validation_failed",
    }
    message = str(exc) if str(exc) in known else type(exc).__name__
    return {"status": "failed", "message": message}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tenant_id")
    parser.add_argument("--expected-chunks", type=int, default=287)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        result = asyncio.run(build_candidate(args.tenant_id, args.expected_chunks, args.execute))
    except Exception as exc:
        print(json.dumps(safe_failure(exc), ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
