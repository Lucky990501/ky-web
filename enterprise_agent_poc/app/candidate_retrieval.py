"""Candidate-index retrieval that leaves the active RAG path untouched."""

from __future__ import annotations

from app.embedding_profiles import AliyunBailianEmbeddingProvider, EmbeddingProfile
from app.knowledge import (
    QueryAnswerabilityPolicy,
    RetrievalConfidencePolicy,
    RetrievalOutcome,
    calculate_metadata_score,
    detect_query_intent,
    metadata_dict,
    query_embedding_input,
    structured_keyword_score,
)
from app.product_store import ProductStore
from app.settings import Settings


class CandidateKnowledgeRetrievalService:
    """Evaluate one explicit profile without changing the production selector."""

    def __init__(self, product: ProductStore, settings: Settings, profile: EmbeddingProfile) -> None:
        if not product._store.is_postgres:
            raise ValueError("candidate_retrieval_requires_postgres")
        self.product = product
        self.settings = settings
        self.profile = profile
        self.embedding = AliyunBailianEmbeddingProvider(profile)
        self.policy = RetrievalConfidencePolicy.from_settings(settings)
        self.answerability_policy = QueryAnswerabilityPolicy(settings.knowledge_query_guard_enabled)

    def search_outcome(self, tenant_id: str, query: str, top_k: int = 5) -> RetrievalOutcome:
        guard_reason = self.answerability_policy.pre_retrieval_rejection_reason(query)
        if guard_reason:
            return RetrievalOutcome([], False, "query_guard", guard_reason)

        query_intent = detect_query_intent(query)
        vector = self.embedding.embed_query_sync(query_embedding_input(query, query_intent))
        if len(vector) != self.profile.dimension:
            raise RuntimeError("candidate_query_embedding_dimension_mismatch")
        literal = "[" + ",".join(f"{value:.10g}" for value in vector) + "]"
        with self.product._store.connection() as conn:
            rows = conn.execute(
                "SELECT c.id,c.file_id,c.content,c.title,c.section,c.page_number,c.metadata,"
                "e.index_version,(1 - (e.embedding <=> ?::vector)) AS vector_score,f.name filename "
                "FROM knowledge_chunk_embeddings e "
                "JOIN knowledge_chunks c ON c.id=e.chunk_id AND c.tenant_id=e.tenant_id "
                "JOIN knowledge_files f ON f.id=c.file_id AND f.tenant_id=e.tenant_id "
                "WHERE e.tenant_id=? AND e.index_version=? AND e.provider=? AND e.model=? "
                "AND e.dimension=? AND f.status='ready' "
                "ORDER BY e.embedding <=> ?::vector LIMIT ?",
                (
                    literal,
                    tenant_id,
                    self.profile.index_version,
                    self.profile.provider,
                    self.profile.model,
                    self.profile.dimension,
                    literal,
                    max(top_k * 4, 20),
                ),
            ).fetchall()

        results: list[dict] = []
        confidence_reasons: list[str] = []
        for source_row in rows:
            row = dict(source_row)
            semantic = float(row["vector_score"] or 0)
            metadata = metadata_dict(row.get("metadata"))
            keyword = structured_keyword_score(
                query,
                title=row["title"] or "",
                section=row["section"] or "",
                content=row["content"],
                metadata=metadata,
            )
            metadata_relevance = calculate_metadata_score(query_intent, metadata)
            score = 0.65 * semantic + 0.35 * keyword + (
                0.12 * metadata_relevance if metadata_relevance >= 0 else 0.03 * metadata_relevance
            )
            accepted, rejection_reason = self.policy.decision(
                vector_score=semantic,
                keyword_score=keyword,
                final_score=score,
            )
            if accepted:
                results.append(
                    {
                        "chunk_id": row["id"],
                        "file_id": row["file_id"],
                        "filename": row["filename"],
                        "title": row["title"],
                        "content": row["content"][:1200],
                        "section": row["section"],
                        "page": row["page_number"],
                        "query_intent": query_intent.value,
                        "canonical_section": metadata.get("canonical_section"),
                        "metadata_score": round(metadata_relevance, 4),
                        "index_version": row["index_version"],
                        "metadata_schema_version": metadata.get("metadata_schema_version"),
                        "vector_score": round(semantic, 4),
                        "keyword_score": round(keyword, 4),
                        "score": round(score, 4),
                        "accepted": True,
                        "rejection_reason": rejection_reason,
                    }
                )
            elif rejection_reason:
                confidence_reasons.append(rejection_reason)
        guard_reason = self.answerability_policy.rejection_reason(query, results)
        if guard_reason:
            return RetrievalOutcome([], False, "query_guard", guard_reason)
        accepted_results = sorted(results, key=lambda item: item["score"], reverse=True)[:top_k]
        if (
            len(accepted_results) >= 2
            and accepted_results[0]["keyword_score"] <= 0
            and accepted_results[0]["score"] - accepted_results[1]["score"] < self.policy.result_margin
        ):
            return RetrievalOutcome([], False, "within_top_score_margin")
        if not accepted_results and confidence_reasons:
            return RetrievalOutcome([], False, confidence_reasons[0])
        return RetrievalOutcome(accepted_results, bool(accepted_results))
