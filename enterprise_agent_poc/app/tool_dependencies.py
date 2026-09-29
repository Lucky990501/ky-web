"""Turn-local required-tool dependencies, separate from immutable attempts.

Changed-input retries require a platform-issued pre-execution validation receipt
AND an explicit retry_of from the caller. Never infer intent from tool names,
prose, adjacency, or a successful artifact. Legacy exact-request retries retain
their existing last-attempt semantics.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

CONTRACT = "required-tool-input-retry-v1"
RETRY_CATEGORIES = {"argument_validation_error", "recoverable_tool_input_error"}
AUDIT_FIELDS = {
    "attempt_id", "tool_call_id", "execution_scope", "request_fingerprint",
    "argument_fingerprints", "retry_of", "retry_token", "repairable_fields",
    "failure_category", "provider_invoked", "created_at", "completed_at",
    "validation_error", "retry_parent", "superseded_by", "supersession_status",
    "lineage_error", "logical_dependency_id",
}


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     default=str).encode()).hexdigest()


def request_arguments(arguments: dict) -> dict:
    return {key: value for key, value in arguments.items() if key != "retry_of"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ToolInputValidationError(ValueError):
    """Only raise before side effects/Provider invocation, with repair fields."""

    def __init__(self, message: str, *, repairable_fields: tuple[str, ...]) -> None:
        super().__init__(message)
        self.repairable_fields = repairable_fields


def input_failure(arguments: dict, error: ToolInputValidationError) -> dict:
    """Content-free receipt; the caller must explicitly copy retry_of to retry."""
    timestamp = now()
    return {
        "error_code": "argument_validation_error", "message": str(error),
        "retry_instruction": "Correct only the rejected fields for this same requirement and copy retry_of into the next call. Omit retry_of for an independent requirement.",
        "retry_of": uuid4().hex,
        "_tool_dependency": {
            "contract": CONTRACT, "status": "failed",
            "failure_category": "argument_validation_error",
            "request_fingerprint": fingerprint(request_arguments(arguments)),
            "repairable_fields": list(error.repairable_fields),
            "provider_invoked": False, "created_at": timestamp,
            "completed_at": timestamp,
        },
    }


def tool_result_payload(result: object) -> dict:
    """SDKs sometimes drop isError; read the structured platform receipt."""
    if isinstance(result, dict):
        structured = result.get("structured_content", result.get("structuredContent"))
    else:
        structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    content = result.get("content", []) if isinstance(result, dict) else getattr(result, "content", [])
    for block in content or []:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if isinstance(text, str):
            try:
                value = json.loads(text)
            except ValueError:
                continue
            if isinstance(value, dict) and "_tool_dependency" in value:
                return value
    return {}


def attempt_observation(arguments: object, result: object, *, scope: str,
                        tool_call_id: str, created_at: str | None = None,
                        completed_at: str | None = None) -> dict:
    """Hash complete arguments; do not add enterprise text to durable traces."""
    value = arguments if isinstance(arguments, dict) else None
    request = request_arguments(value) if value is not None else arguments
    observed = {
        "attempt_id": fingerprint([scope, tool_call_id]),
        "tool_call_id": tool_call_id, "execution_scope": scope,
        "request_fingerprint": fingerprint(request),
        "argument_fingerprints": {key: fingerprint(item) for key, item in request.items()} if isinstance(request, dict) else {},
        "created_at": created_at, "completed_at": completed_at,
    }
    if value is not None and "retry_of" in value:
        observed["retry_of"] = value["retry_of"]
    payload = tool_result_payload(result)
    meta = payload.get("_tool_dependency")
    if meta is not None:
        # An invalid receipt must never be accepted as successful tool output.
        observed["failure_category"] = "unverified_retry_receipt"
        fields = meta.get("repairable_fields") if isinstance(meta, dict) else None
        token = payload.get("retry_of")
        if (isinstance(meta, dict) and meta.get("contract") == CONTRACT
                and meta.get("status") == "failed"
                and meta.get("failure_category") in RETRY_CATEGORIES
                and meta.get("provider_invoked") is False
                and meta.get("request_fingerprint") == observed["request_fingerprint"]
                and isinstance(fields, list) and fields and len(fields) <= 8
                and all(isinstance(key, str) and key in observed["argument_fingerprints"] for key in fields)
                and isinstance(token, str) and len(token) == 32
                and all(c in "0123456789abcdef" for c in token)):
            observed.update(failure_category=meta["failure_category"],
                            repairable_fields=fields, retry_token=token,
                            provider_invoked=False,
                            validation_error=str(payload.get("error_code") or "argument_validation_error"))
    return observed


def call_completed(call: dict) -> bool:
    status = str(getattr(call.get("status"), "value", call.get("status")) or "unknown").rsplit(".", 1)[-1].lower()
    return (status == "completed" and not call.get("error")
            and not call.get("result_is_error") and not call.get("failure_category")
            and not call.get("lineage_error"))


def resolve_dependencies(observations: list[dict]) -> tuple[list[dict], list[dict], dict]:
    """Resolve only backward, unique, scoped, explicit input-repair lineage."""
    calls = [dict(item) for item in observations]
    tokens: dict[str, list[dict]] = {}
    groups: dict[tuple, dict] = {}
    seen_attempts: set[str] = set()
    for index, call in enumerate(calls):
        # Remove computed state when re-evaluating (grounding and finalization
        # share this resolver). The original observations remain unchanged.
        for field in ("retry_parent", "superseded_by", "supersession_status", "lineage_error", "logical_dependency_id"):
            call.pop(field, None)
        scope = call.get("execution_scope", "legacy")
        identity = call.get("request_fingerprint") or call.get("dependency_id", "legacy-tool")
        key = (scope, call.get("server"), call.get("tool"), identity)
        attempt_id = call.setdefault("attempt_id", fingerprint([key, index]))
        # Modern observations have a distinct initial requirement slot for
        # every call, even identical arguments. Only explicit lineage merges
        # them. Legacy observations retain the old exact-request fallback.
        if call.get("execution_scope"):
            key = (*key, attempt_id)
        if attempt_id in seen_attempts:
            call["lineage_error"] = "duplicate_attempt_identity"
        seen_attempts.add(attempt_id)
        parent = None
        retry = call.get("retry_of")
        if retry is not None:
            candidates = tokens.get(retry, []) if isinstance(retry, str) else []
            if len(candidates) == 1:
                candidate = candidates[0]
                fields = set(candidate.get("repairable_fields", []))
                before = candidate.get("argument_fingerprints", {})
                after = call.get("argument_fingerprints", {})
                same_scope = bool(call.get("execution_scope")) and all(
                    call.get(field) == candidate.get(field) for field in ("execution_scope", "server", "tool"))
                stable = bool(fields) and before.keys() == after.keys() and all(
                    before[field] == after[field] for field in before if field not in fields)
                if (same_scope and stable and candidate.get("provider_invoked") is False
                        and candidate.get("failure_category") in RETRY_CATEGORIES
                        and not candidate.get("superseded_by") and not candidate.get("lineage_error")):
                    parent = candidate
            if parent is None:
                call["lineage_error"] = "invalid_retry_lineage"
            else:
                key = parent["_dependency_key"]
                call["retry_parent"] = parent["attempt_id"]
                parent["superseded_by"] = attempt_id
                parent["supersession_status"] = "superseded"
        group = groups.setdefault(key, {
            "dependency_id": fingerprint(key), "tool_name": call.get("tool"),
            "server": call.get("server"), "execution_scope": scope,
            "requirement_identity": {"initial_attempt_id": attempt_id,
                                     "request_fingerprint": identity}, "attempt_ids": [],
            "status": "unsatisfied", "satisfied": False,
        })
        call["_dependency_key"] = key
        call["logical_dependency_id"] = group["dependency_id"]
        group["attempt_ids"].append(attempt_id)
        group["active_attempt_id"] = attempt_id
        group["lineage_valid"] = group.get("lineage_valid", True) and not call.get("lineage_error")
        group["satisfied"] = call_completed(call) and group["lineage_valid"]
        group["status"] = "satisfied" if group["satisfied"] else "unsatisfied"
        token = call.get("retry_token")
        if isinstance(token, str):
            tokens.setdefault(token, []).append(call)
    for call in calls:
        call.pop("_dependency_key", None)
    required = {}
    for tool in dict.fromkeys(call.get("tool") for call in calls):
        attempts = [call for call in calls if call.get("tool") == tool]
        dependencies = [group for group in groups.values() if group["tool_name"] == tool]
        required[tool] = {
            "attempts": len(attempts),
            "completed_attempts": sum(call_completed(call) for call in attempts),
            "failed_attempts": sum(not call_completed(call) for call in attempts),
            "satisfied": bool(dependencies) and all(group["satisfied"] for group in dependencies),
        }
    return calls, list(groups.values()), required
