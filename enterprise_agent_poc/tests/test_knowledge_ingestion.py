import asyncio
from dataclasses import replace

import pytest

from app.knowledge import KnowledgeProcessingService, KnowledgeRetrievalService, OpenAICompatibleEmbeddingProvider, require_semantic_runtime, runtime_diagnostic
from app.product_store import ProductStore
from app.settings import settings
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
