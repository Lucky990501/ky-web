import asyncio
from dataclasses import replace

import pytest

from app.knowledge import KnowledgeProcessingService, KnowledgeRetrievalService, OpenAICompatibleEmbeddingProvider, QueryAnswerabilityPolicy, RetrievalConfidencePolicy, require_semantic_runtime, retrieval_policy_diagnostic, runtime_diagnostic, sectionize, set_embedding_probe
from app.knowledge_metadata import METADATA_SCHEMA_VERSION, RAG_INDEX_VERSION, KnowledgeQueryIntent, build_chunk_metadata, detect_query_intent, embedding_input, metadata_dict, metadata_score
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
    chunks = product.knowledge_chunks("tenant-a", record["file_id"])
    assert chunks
    chunk_metadata = metadata_dict(chunks[0]["metadata"])
    assert chunk_metadata["source_file_id"] == record["file_id"]
    assert chunk_metadata["source_filename"] == "秋季课程.txt"
    assert chunk_metadata["index_version"] == RAG_INDEX_VERSION
    assert chunk_metadata["metadata_schema_version"] == METADATA_SCHEMA_VERSION

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
    assert policy.decision(vector_score=0.35, keyword_score=0, final_score=0.215) == (False, "within_confidence_margin")


def test_markdown_hierarchy_preserves_sheet_name_for_canonical_metadata():
    sections = sectionize("# 嘉宾档案\n## 黎明简介\n姓名：黎明\n## 前期准备：确定嘉宾\n确认邀请名单")
    assert sections[0]["sheet_name"] == "嘉宾档案"
    assert sections[0]["section"] == "黎明简介"
    profile = build_chunk_metadata(
        source_file_id="file-1",
        source_filename="knowledge.md",
        sheet_name=sections[0]["sheet_name"],
        section=sections[0]["section"],
        title="企业知识",
        content=sections[0]["text"],
    )
    assert profile["canonical_section"] == "嘉宾档案"
    assert profile["record_type"] == "profile"
    assert profile["entity_type"] == "person"
    assert profile["person_name"] == "黎明"


def test_structured_source_category_recovers_canonical_metadata_from_weak_title():
    profile = build_chunk_metadata(
        source_file_id="file-2",
        source_filename="history.md",
        sheet_name=None,
        section="记录 87",
        title="企业知识",
        content="- 一级分类：历史日期索引\n- 原始日期：2020年10月",
    )
    assert profile["canonical_section"] == "历史日期索引"
    assert profile["record_type"] == "date_index"
    assert profile["year"] == 2020


def test_structured_date_record_recovers_historical_index_without_category_label():
    profile = build_chunk_metadata(
        source_file_id="file-3",
        source_filename="legacy.md",
        sheet_name=None,
        section="记录 42",
        title="企业知识",
        content="- 原始日期：2019/10/11\n- 标准日期：2019-10-11\n- 年份：2019\n- 序号：42",
    )
    assert profile["canonical_section"] == "历史日期索引"
    assert profile["record_type"] == "date_index"


def test_lightweight_query_intent_prefers_stronger_category_phrases():
    assert detect_query_intent("如何安排一场教师交流会的排期？") is KnowledgeQueryIntent.EVENT_SESSION
    assert detect_query_intent("项目有哪些资料说明？") is KnowledgeQueryIntent.AI_KNOWLEDGE
    assert detect_query_intent("请推荐一些延伸阅读") is KnowledgeQueryIntent.RECOMMENDED_READING
    assert detect_query_intent("这段自由文本没有分类信号") is KnowledgeQueryIntent.UNKNOWN


def test_metadata_boost_is_explainable_and_never_a_hard_filter():
    metadata = {"canonical_section": "嘉宾档案"}
    assert metadata_score(KnowledgeQueryIntent.GUEST_PROFILE, metadata) == 1.0
    assert metadata_score(KnowledgeQueryIntent.EVENT_WORKFLOW, metadata) == -0.25
    assert metadata_score(KnowledgeQueryIntent.UNKNOWN, metadata) == 0.0


def test_v2_embedding_input_adds_metadata_without_changing_source_content():
    content = "这是未经修改的企业原文。"
    metadata = {"canonical_section": "推荐阅读", "record_type": "reading", "section": "导读资料"}
    vector_text = embedding_input(content, metadata)
    assert vector_text.endswith(content)
    assert "知识分类：推荐阅读" in vector_text
    assert content == "这是未经修改的企业原文。"


def test_query_answerability_policy_requires_evidence_for_sensitive_requests():
    policy = QueryAnswerabilityPolicy()
    assert policy.rejection_reason("这场活动保证能提升多少分？", []) == "unsupported_absolute_promise"
    assert policy.rejection_reason("请给出不存在课程的授课老师和名额。", []) == "explicitly_nonexistent_entity"
    assert policy.rejection_reason("名师面对面课程价格是9999元吗？", [{"title": "活动介绍", "content": "活动安排"}]) == "price_without_grounding"
    assert policy.rejection_reason("课程价格是多少？", [{"title": "课程费用", "content": "报名费用为 999 元"}]) is None


def test_query_guard_covers_categories_without_query_specific_blacklists():
    policy = QueryAnswerabilityPolicy()
    assert policy.pre_retrieval_rejection_reason("请给我某位客户的手机联系方式") == "private_or_credential_data"
    assert policy.pre_retrieval_rejection_reason("未公开的客户名单和融资金额是什么？") == "non_public_enterprise_data"
    assert policy.pre_retrieval_rejection_reason("可以帮我预订下周的航班吗？") == "obvious_out_of_scope"
    assert policy.pre_retrieval_rejection_reason("我们的课程承诺一定录取吗？") == "unsupported_absolute_promise"


def test_policy_diagnostic_exposes_only_p0_approved_state():
    diagnostic = retrieval_policy_diagnostic(settings)
    assert diagnostic["query_guard_enabled"] is settings.knowledge_query_guard_enabled
    assert diagnostic["guard_policy_version"] == "query-guard-v2"
    assert diagnostic["min_final_score"] == settings.knowledge_min_final_score
    assert "runtime_config_fingerprint" in diagnostic


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
