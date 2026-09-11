"""Run 100 redacted requests against the isolated APIYI candidate."""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.apiyi_embedding import ApiYiCandidateEmbeddingProvider, apiyi_profile_from_env


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * probability
    low, high = math.floor(rank), math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def maximum_consecutive_infrastructure_failures(attempts: list[dict]) -> int:
    longest = current = 0
    for item in attempts:
        infrastructure_failure = item["error_type"] == "timeout" or (
            isinstance(item["http_status"], int) and 500 <= item["http_status"] <= 599
        )
        current = current + 1 if infrastructure_failure else 0
        longest = max(longest, current)
    return longest


def summarize(attempts: list[dict], domain: str) -> dict:
    successes = [item for item in attempts if item["status"] == "success"]
    failures = [item for item in attempts if item["status"] == "failed"]
    latencies = [float(item["latency_ms"]) for item in successes]
    dimensions = sorted({item["returned_dimension"] for item in successes})
    status_distribution = Counter(str(item["http_status"] or "none") for item in attempts)
    errors = Counter(f"{item['error_type']}|HTTP_{item['http_status'] or 'none'}" for item in failures)
    total = len(attempts)
    success_rate = len(successes) / total * 100 if total else 0.0
    sustained = maximum_consecutive_infrastructure_failures(attempts)
    return {
        "test_status": "COMPLETED",
        "provider_id": "apiyi-candidate",
        "base_url_domain": domain,
        "model": "text-embedding-3-small",
        "requested_dimension": 1536,
        "total_requests": total,
        "success_count": len(successes),
        "failure_count": len(failures),
        "success_rate": round(success_rate, 2),
        "http_status_distribution": dict(sorted(status_distribution.items())),
        "429_count": sum(item["http_status"] == 429 for item in attempts),
        "500_count": sum(item["http_status"] == 500 for item in attempts),
        "502_count": sum(item["http_status"] == 502 for item in attempts),
        "503_count": sum(item["http_status"] == 503 for item in attempts),
        "timeout_count": sum(item["error_type"] == "timeout" for item in attempts),
        "latency_scope": "successful_requests_only",
        "latency_p50_ms": round(percentile(latencies, 0.50), 2) if latencies else None,
        "latency_p95_ms": round(percentile(latencies, 0.95), 2) if latencies else None,
        "latency_max_ms": round(max(latencies), 2) if latencies else None,
        "returned_dimension": dimensions[0] if len(dimensions) == 1 else dimensions,
        "dimension_consistent": bool(successes) and dimensions == [1536],
        "max_consecutive_5xx_or_timeout": sustained,
        "sustained_failure_definition": "at_least_3_consecutive_5xx_or_timeout",
        "error_summary": dict(sorted(errors.items())),
        "gate_pass": total == 100 and success_rate >= 99 and dimensions == [1536] and sustained < 3,
        "attempts": attempts,
        "safety": {
            "api_key_logged": False,
            "authorization_header_logged": False,
            "response_vectors_logged": False,
            "production_config_modified": False,
            "reindex_performed": False,
            "rag_modified": False,
        },
    }


def run_probe(total: int) -> dict:
    profile = apiyi_profile_from_env()
    provider = ApiYiCandidateEmbeddingProvider(profile)
    attempts: list[dict] = []
    started = datetime.now(timezone.utc).isoformat()
    for attempt in range(1, total + 1):
        started_at = time.perf_counter()
        http_status = None
        returned_dimension = None
        error_type = None
        status = "failed"
        try:
            vector = provider.embed_query_sync(f"synthetic APIYI embedding stability probe {attempt}; no enterprise data")
            returned_dimension = len(vector)
            http_status = 200
            status = "success"
        except Exception as exc:
            response = getattr(exc, "response", None)
            http_status = getattr(response, "status_code", None)
            error_type = "timeout" if type(exc).__name__ in {"ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout"} else type(exc).__name__
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
    result = summarize(attempts, profile.base_url_domain)
    result["started_at_utc"] = started
    result["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=100, choices=range(1, 501))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = run_probe(args.requests)
    except Exception as exc:
        result = {
            "test_status": "TEST_INVALID",
            "error_type": type(exc).__name__,
            "reason": str(exc) if str(exc).startswith("apiyi_") else type(exc).__name__,
            "total_requests": args.requests,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".part")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"test_status": result["test_status"], "gate_pass": result.get("gate_pass"), "output": str(args.output)}))
    return 0 if result["test_status"] == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
