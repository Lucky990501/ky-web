from __future__ import annotations

import pytest
from pathlib import Path

from app.embedding_profiles import aliyun_bailian_profile_from_env
from scripts.reindex_qwen_embedding_v3 import _vector_literal, safe_failure


def test_qwen_candidate_profile_is_separate_from_active_index():
    profile = aliyun_bailian_profile_from_env(
        {
            "ALIYUN_BAILIAN_EMBEDDING_BASE_URL": "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            "ALIYUN_BAILIAN_API_KEY": "test-only-secret",
        }
    )
    assert profile.index_version == "rag-index-v3-qwen"
    assert profile.index_version != "rag-index-v2"
    assert profile.provider == "aliyun-bailian"


def test_vector_literal_does_not_use_json_or_sql_fragments():
    assert _vector_literal([0.1, -0.2, 0.0]) == "[0.1,-0.2,0]"


@pytest.mark.parametrize(
    "reason",
    [
        "migration_006_required",
        "rag_index_v2_integrity_failed",
        "candidate_index_partial_or_invalid",
        "candidate_index_validation_failed",
    ],
)
def test_candidate_reindex_safe_failures(reason):
    assert safe_failure(RuntimeError(reason)) == {"status": "failed", "message": reason}


def test_candidate_migration_enforces_tenant_chunk_binding_and_preserves_legacy_column():
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "postgres" / "006_embedding_profiles.sql").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(sql.lower().split())
    assert "foreign key (chunk_id, tenant_id) references knowledge_chunks(id, tenant_id)" in normalized
    assert "knowledge_chunk_embeddings" in normalized
    assert "alter column embedding" not in normalized
    assert "drop column embedding" not in normalized
