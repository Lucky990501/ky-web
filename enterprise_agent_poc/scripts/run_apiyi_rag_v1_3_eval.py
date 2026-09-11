"""Read-only fixed-40 eval using APIYI query vectors against rag-index-v2."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if sys.path[0] != str(ROOT):
    sys.path[:] = [str(ROOT), *[item for item in sys.path if item != str(ROOT)]]

from app.apiyi_embedding import ApiYiCandidateEmbeddingProvider, apiyi_candidate_settings, apiyi_profile_from_env
from app.knowledge import KnowledgeRetrievalService
from app.product_store import ProductStore
from app.settings import Settings
from app.store import POCStore
from scripts.run_rag_v1_3_eval import apply_section_aliases, evaluate_case, metrics_for, preflight, safe_failure, validate_dataset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tenant_id")
    parser.add_argument("--expected-chunks", type=int, default=288)
    parser.add_argument("--stability-summary", required=True, type=Path)
    args = parser.parse_args()

    stability = json.loads(args.stability_summary.read_text(encoding="utf-8"))
    if stability.get("total_requests") != 100 or stability.get("gate_pass") is not True:
        raise RuntimeError("apiyi_stability_gate_not_passed")
    dataset = validate_dataset(json.loads((ROOT / "evals" / "rag_v1_3_dataset.json").read_text(encoding="utf-8")))
    aliases = json.loads((ROOT / "evals" / "rag-section-aliases-v1.json").read_text(encoding="utf-8"))["aliases"]
    dataset = apply_section_aliases(dataset, aliases)

    production = Settings.from_env()
    profile = apiyi_profile_from_env()
    candidate = apiyi_candidate_settings(production, profile)
    product = ProductStore(POCStore(production.database_url))
    with product._store.connection() as conn:
        indexed = conn.execute(
            "SELECT COUNT(*) AS count FROM knowledge_chunks WHERE tenant_id=? AND embedding_version='rag-index-v2' "
            "AND embedding_provider='openai-compatible' AND embedding_model='text-embedding-3-small' "
            "AND embedding_dimension=1536 AND embedding IS NOT NULL",
            (args.tenant_id,),
        ).fetchone()
    if not indexed or int(indexed["count"]) != args.expected_chunks:
        raise RuntimeError("rag_index_v2_not_ready")

    service = KnowledgeRetrievalService(product, candidate)
    service.embedding = ApiYiCandidateEmbeddingProvider(profile)
    dataset = preflight(service, product, candidate, args.tenant_id, dataset)
    rows = []
    for case in dataset:
        started = time.perf_counter()
        outcome = service.search_outcome(args.tenant_id, case["query"], 5)
        rows.append(
            evaluate_case(
                case,
                outcome.results,
                round((time.perf_counter() - started) * 1000, 1),
                outcome.rejection_reason,
                outcome.rejection_detail,
            )
        )
    print(
        json.dumps(
            {
                "status": "completed",
                "provider_id": profile.provider_id,
                "base_url_domain": profile.base_url_domain,
                "model": profile.model,
                "dimension": profile.dimension,
                "index_version": "rag-index-v2",
                "eval_dataset_version": "rag-v1.3",
                "section_alias_version": "rag-section-aliases-v1",
                "metrics": metrics_for(rows),
                "cases": rows,
                "reindex_executed": False,
                "production_config_modified": False,
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
