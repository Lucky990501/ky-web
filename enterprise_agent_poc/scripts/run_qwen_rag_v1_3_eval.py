"""Run the immutable V1.3 dataset against only rag-index-v3-qwen."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if sys.path[0] != str(ROOT):
    sys.path[:] = [str(ROOT), *[item for item in sys.path if item != str(ROOT)]]

from app.candidate_retrieval import CandidateKnowledgeRetrievalService
from app.embedding_profiles import aliyun_bailian_profile_from_env
from app.product_store import ProductStore
from app.settings import Settings
from app.store import POCStore
from scripts.run_rag_v1_3_eval import (
    apply_section_aliases,
    evaluate_case,
    metrics_for,
    safe_failure,
    validate_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tenant_id", nargs="?", default="zhiy-e-intelligence")
    parser.add_argument("--expected-chunks", type=int, default=287)
    args = parser.parse_args()
    tenant_id = args.tenant_id
    dataset = validate_dataset(json.loads((ROOT / "evals" / "rag_v1_3_dataset.json").read_text(encoding="utf-8")))
    alias_path = ROOT / "evals" / "rag-section-aliases-v1.json"
    alias_payload = json.loads(alias_path.read_text(encoding="utf-8"))
    dataset = apply_section_aliases(dataset, alias_payload["aliases"])
    settings = Settings.from_env()
    profile = aliyun_bailian_profile_from_env()
    product = ProductStore(POCStore(settings.database_url))
    with product._store.connection() as conn:
        indexed = conn.execute(
            "SELECT COUNT(*) AS count FROM knowledge_chunk_embeddings WHERE tenant_id=? AND index_version=? "
            "AND provider=? AND model=? AND dimension=?",
            (tenant_id, profile.index_version, profile.provider, profile.model, profile.dimension),
        ).fetchone()
    if not indexed or int(indexed["count"]) != args.expected_chunks:
        raise RuntimeError("candidate_index_not_ready")
    service = CandidateKnowledgeRetrievalService(product, settings, profile)
    probe = service.embedding.embed_query_sync("synthetic candidate evaluation readiness probe")
    if len(probe) != profile.dimension:
        raise RuntimeError("candidate_provider_dimension_mismatch")

    rows = []
    for case in dataset:
        started = time.perf_counter()
        outcome = service.search_outcome(tenant_id, case["query"], 5)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        rows.append(evaluate_case(case, outcome.results, latency_ms, outcome.rejection_reason, outcome.rejection_detail))
    print(
        json.dumps(
            {
                "status": "completed",
                "eval_dataset_version": "rag-v1.3",
                "section_alias_version": "rag-section-aliases-v1",
                "index_version": profile.index_version,
                "provider": profile.provider,
                "model": profile.model,
                "dimension": profile.dimension,
                "metrics": metrics_for(rows),
                "cases": rows,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps(safe_failure(exc), ensure_ascii=False))
        raise SystemExit(2)
