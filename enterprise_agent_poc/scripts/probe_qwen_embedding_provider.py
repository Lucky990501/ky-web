"""Run a redacted stability probe against the inactive Bailian Qwen profile."""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.embedding_profiles import AliyunBailianEmbeddingProvider, aliyun_bailian_profile_from_env


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * probability
    low, high = math.floor(rank), math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def summarize(attempts: list[dict], *, provider: str, model: str, dimension: int, domain: str) -> dict:
    successes = [item for item in attempts if item["status"] == "success"]
    failures = [item for item in attempts if item["status"] == "failed"]
    success_latencies = [float(item["latency_ms"]) for item in successes]
    returned_dimensions = sorted({item["returned_dimension"] for item in successes})
    http_statuses = Counter(str(item["http_status"] or "none") for item in attempts)
    errors = Counter(
        f"{item['error_type']}|HTTP_{item['http_status'] or 'none'}" for item in failures
    )
    total = len(attempts)
    return {
        "test_status": "COMPLETED",
        "provider": provider,
        "base_url_domain": domain,
        "model": model,
        "requested_dimension": dimension,
        "total_requests": total,
        "success_count": len(successes),
        "failure_count": len(failures),
        "success_rate": round(len(successes) / total * 100, 2) if total else 0,
        "http_status_distribution": dict(sorted(http_statuses.items())),
        "latency_scope": "successful_requests_only",
        "latency_p50_ms": round(_percentile(success_latencies, 0.50), 2) if successes else None,
        "latency_p95_ms": round(_percentile(success_latencies, 0.95), 2) if successes else None,
        "min_latency_ms": round(min(success_latencies), 2) if successes else None,
        "max_latency_ms": round(max(success_latencies), 2) if successes else None,
        "returned_dimension": returned_dimensions[0] if len(returned_dimensions) == 1 else returned_dimensions,
        "dimension_consistent": bool(successes) and returned_dimensions == [dimension],
        "error_summary": dict(sorted(errors.items())),
        "gate_pass": len(successes) == total and returned_dimensions == [dimension],
        "attempts": attempts,
        "safety": {
            "enterprise_data_used": False,
            "api_key_logged": False,
            "authorization_header_logged": False,
            "response_vectors_logged": False,
            "production_config_modified": False,
            "reindex_performed": False,
            "pgvector_modified": False,
            "rag_modified": False,
        },
    }


def run_probe(total: int) -> dict:
    profile = aliyun_bailian_profile_from_env()
    provider = AliyunBailianEmbeddingProvider(profile)
    attempts: list[dict] = []
    started = datetime.now(timezone.utc).isoformat()
    for attempt in range(1, total + 1):
        started_at = time.perf_counter()
        http_status = None
        returned_dimension = None
        error_type = None
        status = "failed"
        try:
            vector = provider.embed_query_sync(
                f"synthetic Alibaba Cloud Model Studio embedding probe {attempt}; no enterprise data"
            )
            returned_dimension = len(vector)
            http_status = 200
            status = "success"
        except Exception as exc:
            error_type = type(exc).__name__
            http_status = getattr(getattr(exc, "response", None), "status_code", None)
        attempts.append(
            {
                "attempt": attempt,
                "status": status,
                "http_status": http_status,
                "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
                "returned_dimension": returned_dimension,
                "error_type": error_type,
            }
        )
    result = summarize(
        attempts,
        provider=profile.provider,
        model=profile.model,
        dimension=profile.dimension,
        domain=profile.base_url_domain,
    )
    result["started_at_utc"] = started
    result["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=50, choices=range(1, 501))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run_probe(args.requests)
    except Exception as exc:
        result = {
            "test_status": "TEST_INVALID",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "total_requests": args.requests,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".part")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"test_status": result["test_status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["test_status"] == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
