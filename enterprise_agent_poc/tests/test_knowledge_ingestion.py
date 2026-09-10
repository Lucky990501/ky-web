import asyncio
from dataclasses import replace

import pytest

from app.knowledge import KnowledgeProcessingService, KnowledgeRetrievalService, OpenAICompatibleEmbeddingProvider, QueryAnswerabilityPolicy, RetrievalConfidencePolicy, require_semantic_runtime, runtime_diagnostic, set_embedding_probe
from app.product_store import ProductStore
from app.settings import safe_runtime_config_snapshot, settings
from scripts.verify_runtime_config import compare, parse_env_file
from app.storage import storage_provider
from app.store import POCStore


def test_text_file_is_parsed_chunked_indexed_and_retrieved_per_tenant(tmp_path):
    runtime_settings = replace(
        settings,
        database_url=f"sqlite:///{tmp_path / 'knowledge.db'}",
        database_path=tmp_path / "knowledge.db",
        object_storage_provider="local",
        object_storage_dir=tmp_path / "objects",
        bootstrap_demo_data=False,
    )
    store = POCStore(runtime_settings.database_url)
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()

    storage = storage_provider(runtime_settings)
    storage_key = "knowledge/tenant-a/autumn.txt"
    storage.put(storage_key, "# 秋季课程\n秋季数学冲刺课程包含每周诊断和分层练习。".encode(), "text/plain")
    record = product.create_knowledge_file(
        "tenant-a", "test-user", "秋季课程.txt", "text/plain", 80, storage_key
    )
    product.set_knowledge_file_status("tenant-a", record["file_id"], "queued")

    asyncio.run(KnowledgeProcessingService(product, runtime_settings).process("tenant-a", record["file_id"]))

    detail = product.knowledge_file("tenant-a", record["file_id"])
    assert detail["status"] == "ready"
    assert detail["knowledge_base_id"]
    assert detail["chunk_count"] >= 1
    assert product.knowledge_chunks("tenant-a", record["file_id"])

    retrieval = KnowledgeRetrievalService(product, runtime_settings)
    assert any("每周诊断" in item["content"] for item in retrieval.search("tenant-a", "秋季数学诊断"))
    assert all("每周诊断" not in item.get("content", "") for item in retrieval.search("tenant-b", "秋季数学诊断"))


def test_production_rejects_local_hash_when_fallback_is_disabled(tmp_path):
    runtime_settings = replace(
        settings,
        database_url=f"sqlite:///{tmp_path / 'strict.db'}",
        database_path=tmp_path / "strict.db",
        environment="production",
        knowledge_allow_fallback=False,
        embedding_provider="local-hash",
        embedding_api_key="",
    )
    product = ProductStore(POCStore(runtime_settings.database_url))
    product.initialize()
    diagnostic = runtime_diagnostic(product, runtime_settings)
    assert diagnostic["status"] == "degraded"
    assert diagnostic["strict"] is True
    with pytest.raises(RuntimeError, match="生产语义检索未就绪"):
        require_semantic_runtime(product, runtime_settings)


def test_openai_compatible_embedding_response_requires_configured_dimension():
    provider = OpenAICompatibleEmbeddingProvider(
        replace(settings, embedding_base_url="https://n1.ai/v1", embedding_api_key="test-key", embedding_model="text-embedding-3-small", embedding_dimension=3)
    )
    assert provider._vectors({"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}, 1) == [[0.1, 0.2, 0.3]]
    with pytest.raises(RuntimeError, match="维度"):
        provider._vectors({"data": [{"index": 0, "embedding": [0.1]}]}, 1)


def test_failed_embedding_probe_degrades_strict_production_health(tmp_path):
    runtime_settings = replace(
        settings,
        database_url=f"sqlite:///{tmp_path / 'probe.db'}",
        database_path=tmp_path / "probe.db",
        environment="production",
        knowledge_allow_fallback=False,
        embedding_provider="openai-compatible",
        embedding_base_url="https://n1.ai/v1",
        embedding_api_key="test-key",
    )
    product = ProductStore(POCStore(runtime_settings.database_url))
    product.initialize()
    set_embedding_probe("unavailable")
    assert "正式 Embedding Provider 连通性验证失败。" in runtime_diagnostic(product, runtime_settings)["reasons"]
    set_embedding_probe(None)


def test_retrieval_confidence_policy_rejects_weak_unrelated_candidates():
    policy = RetrievalConfidencePolicy(0.20, 0.30, False, 0.02)
    assert policy.decision(vector_score=0.526, keyword_score=0.125, final_score=0.3856) == (True, None)
    assert policy.decision(vector_score=0.2542, keyword_score=0, final_score=0.1652) == (False, "below_minimum_final_score")


def test_query_answerability_policy_requires_evidence_for_sensitive_requests():
    policy = QueryAnswerabilityPolicy()
    assert policy.rejection_reason("这场活动保证能提升多少分？", []) == "unsupported_absolute_promise"
    assert policy.rejection_reason("请给出不存在课程的授课老师和名额。", []) == "explicitly_nonexistent_entity"
    assert policy.rejection_reason("名师面对面课程价格是9999元吗？", [{"title": "活动介绍", "content": "活动安排"}]) == "price_without_grounding"
    assert policy.rejection_reason("课程价格是多少？", [{"title": "课程费用", "content": "报名费用为 999 元"}]) is None


def test_safe_runtime_config_snapshot_never_contains_raw_configuration_values():
    source = {"APP_ENV": "production", "ENTERPRISE_POC_DATABASE_URL": "postgresql://user:super-secret@db/app"}
    snapshot = safe_runtime_config_snapshot(source)
    assert snapshot["fields"]["APP_ENV"]["configured"] is True
    assert snapshot["fields"]["ENTERPRISE_POC_DATABASE_URL"]["configured"] is True
    assert "super-secret" not in str(snapshot)


def test_runtime_config_verifier_parses_and_compares_without_exposing_values(tmp_path):
    path = tmp_path / ".env.production"
    path.write_text("APP_ENV=production\nEMBEDDING_MODEL=embedding-v1\n", encoding="utf-8")
    expected = parse_env_file(path)
    same = compare(expected, {"APP_ENV": "production", "EMBEDDING_MODEL": "embedding-v1"})
    changed = compare(expected, {"APP_ENV": "production", "EMBEDDING_MODEL": "embedding-v2"})

    assert same["fields"][0]["expected_configured"] is True
    assert same["matches"] is True
    assert changed["matches"] is False
    assert "embedding-v1" not in str(changed)
