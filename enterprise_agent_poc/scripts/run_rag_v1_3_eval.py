"""Run the fixed V1.3 RAG acceptance dataset against one tenant.

The output contains retrieval identifiers and scores, never chunk content or
credentials.  It is intentionally a production-read-only evaluator.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from app.knowledge import KnowledgeRetrievalService
from app.product_store import ProductStore
from app.settings import Settings
from app.store import POCStore


def apply_section_aliases(cases: list[dict[str, Any]], aliases: dict[str, list[str]]) -> list[dict[str, Any]]:
    """Attach only reviewer-approved concrete section labels to positive cases."""
    resolved = []
    for original in cases:
        case = dict(original)
        expected = case.get("expected_section")
        if case.get("expected_answerable"):
            labels = aliases.get(expected, [])
            if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels):
                raise ValueError(f"章节 {expected!r} 的 aliases 必须是非空字符串列表。")
            case["accepted_sections"] = [expected, *labels]
        resolved.append(case)
    return resolved


def evaluate_case(case: dict[str, Any], results: list[dict[str, Any]], latency_ms: float) -> dict[str, Any]:
    """Evaluate one retrieval without emitting enterprise document content."""
    expected_answerable = bool(case["expected_answerable"])
    expected_section = case.get("expected_section")
    if expected_answerable and not isinstance(expected_section, str):
        raise ValueError(f"正例 {case.get('id', '<unknown>')} 必须定义 expected_section。")
    if not expected_answerable and expected_section is not None:
        raise ValueError(f"反例 {case.get('id', '<unknown>')} 不应定义 expected_section。")

    accepted_sections = case.get("accepted_sections", [expected_section] if expected_answerable else [])
    if expected_answerable and (
        not isinstance(accepted_sections, list)
        or any(not isinstance(section, str) or not section.strip() for section in accepted_sections)
    ):
        raise ValueError(f"正例 {case.get('id', '<unknown>')} 的 accepted_sections 必须是非空字符串列表。")
    retrieved_sections = [str(item.get("section") or "") for item in results]
    section_match_at_k = bool(expected_answerable and set(accepted_sections).intersection(retrieved_sections))
    top_1_section_match = bool(expected_answerable and retrieved_sections and retrieved_sections[0] in accepted_sections)
    accepted = bool(results)
    return {
        "case_id": case["id"],
        "expected_answerable": expected_answerable,
        "expected_section": expected_section,
        "accepted_sections": accepted_sections,
        "retrieved_chunk_ids": [item.get("chunk_id", item.get("id")) for item in results],
        "file_ids": sorted({item["file_id"] for item in results if item.get("file_id")}),
        "retrieved_sections": retrieved_sections,
        "accepted": accepted,
        "section_match_at_k": section_match_at_k,
        "top_1_section_match": top_1_section_match,
        "passed": section_match_at_k if expected_answerable else not accepted,
        "retrieval_latency_ms": latency_ms,
        "scores": [
            {
                "chunk_id": item.get("chunk_id", item.get("id")),
                "vector_score": item.get("vector_score"),
                "keyword_score": item.get("keyword_score"),
                "final_score": item.get("score"),
                "accepted": item.get("accepted", True),
                "rejection_reason": item.get("rejection_reason"),
            }
            for item in results
        ],
    }


def metrics_for(rows: list[dict[str, Any]]) -> dict[str, float]:
    positive = [row for row in rows if row["expected_answerable"]]
    negative = [row for row in rows if not row["expected_answerable"]]
    accepted = [row for row in rows if row["accepted"]]
    return {
        "answerable_acceptance_recall": sum(row["accepted"] for row in positive) / max(1, len(positive)),
        "section_recall_at_k": sum(row["section_match_at_k"] for row in positive) / max(1, len(positive)),
        "top_1_section_accuracy": sum(row["top_1_section_match"] for row in positive) / max(1, len(positive)),
        "grounded_precision": sum(row["section_match_at_k"] for row in positive) / max(1, len(accepted)),
        "no_answer_rejection_rate": sum(not row["accepted"] for row in negative) / max(1, len(negative)),
        "case_pass_rate": sum(row["passed"] for row in rows) / max(1, len(rows)),
    }


def main() -> int:
    args = sys.argv[1:]
    tenant_id = args[0] if args and not args[0].startswith("--") else "zhiy-e-intelligence"
    alias_path = None
    if "--section-aliases" in args:
        position = args.index("--section-aliases")
        if position + 1 >= len(args):
            raise ValueError("--section-aliases 需要一个 JSON 文件路径。")
        alias_path = Path(args[position + 1])
    root = Path(__file__).resolve().parents[1]
    dataset = json.loads((root / "evals" / "rag_v1_3_dataset.json").read_text(encoding="utf-8"))
    if alias_path:
        payload = json.loads(alias_path.read_text(encoding="utf-8"))
        aliases = payload.get("aliases") if isinstance(payload, dict) else None
        if not isinstance(aliases, dict):
            raise ValueError("章节别名文件必须包含 aliases 对象。")
        dataset = apply_section_aliases(dataset, aliases)
    settings = Settings.from_env()
    service = KnowledgeRetrievalService(ProductStore(POCStore(settings.database_url)), settings)
    rows = []
    for case in dataset:
        started = time.perf_counter()
        results = service.search(tenant_id, case["query"], 5)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        rows.append(evaluate_case(case, results, latency_ms))
    metrics = metrics_for(rows)
    print(json.dumps({"tenant_id": tenant_id, "metrics": metrics, "cases": rows}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
