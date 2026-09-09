import asyncio
from dataclasses import replace

from app.knowledge import KnowledgeProcessingService, KnowledgeRetrievalService
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
