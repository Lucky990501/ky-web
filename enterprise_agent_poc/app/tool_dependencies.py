"""Turn-local required-tool dependencies, separate from immutable attempts.

Changed-input retries require a platform-issued pre-execution validation receipt
AND an explicit retry_of from the caller. Never infer intent from tool names,
prose, adjacency, or a successful artifact. V1.1 reconstructs retry arguments
from a server-side receipt before Provider invocation. Legacy exact-request
retries retain their existing last-attempt semantics.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

CONTRACT = "required-tool-input-retry-v1"
RECONSTRUCTION_CONTRACT = "required-tool-input-retry-v1.1"
RETRY_CATEGORIES = {"argument_validation_error", "recoverable_tool_input_error"}
AUDIT_FIELDS = {
    "attempt_id", "tool_call_id", "execution_scope", "request_fingerprint",
    "argument_fingerprints", "retry_of", "retry_token", "repairable_fields",
    "failure_category", "provider_invoked", "created_at", "completed_at",
    "validation_error", "retry_parent", "superseded_by", "supersession_status",
    "lineage_error", "logical_dependency_id", "submitted_args",
    "effective_args", "effective_argument_fingerprints",
    "ignored_retry_argument_drift", "retry_receipt_validated",
    "coupled_repairs",
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

    def __init__(self, message: str, *, repairable_fields: tuple[str, ...],
                 allowed_values: dict[str, tuple[Any, ...]] | None = None,
                 coupled_text_fields: dict[str, tuple[str, ...]] | None = None,
                 retry_receipt: dict | None = None) -> None:
        super().__init__(message)
        self.repairable_fields = repairable_fields
        self.allowed_values = allowed_values or {}
        self.coupled_text_fields = coupled_text_fields or {}
        self.retry_receipt = retry_receipt


class InvalidRetryLineage(ValueError):
    """A retry capability failed validation before any Provider side effect."""

    def __init__(self, reason: str, *, submitted_args: dict | None = None) -> None:
        super().__init__("invalid_retry_lineage")
        self.reason = reason
        self.submitted_args = deepcopy(submitted_args or {})


@dataclass(slots=True)
class _RetryReceipt:
    principal_key: tuple[str | None, ...]
    execution_scope: str
    server: str
    tool: str
    original_args: dict
    failure_category: str
    repairable_fields: tuple[str, ...]
    allowed_values: dict[str, tuple[Any, ...]]
    coupled_text_fields: dict[str, tuple[str, ...]]
    expires_at: float
    consumed: bool = False


class RetryReceiptLedger:
    """Process-local, opaque, one-use retry capabilities.

    Raw canonical arguments never leave this ledger in the retry receipt.  A
    token is authenticated by possession plus a server-side SHA-256 lookup and
    is bound to the Runtime principal, execution scope, server, and tool.
    """

    def __init__(self, *, ttl_seconds: float = 600.0) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_receipts = 4096
        self._receipts: dict[str, _RetryReceipt] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _principal_key(principal: object) -> tuple[str | None, ...]:
        return tuple(getattr(principal, field, None) for field in (
            "tenant_id", "agent_id", "runtime_profile_id",
            "execution_context_id", "instance_id",
        ))

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue(self, *, principal: object, execution_scope: str, server: str,
              tool: str, original_args: dict,
              error: ToolInputValidationError) -> dict:
        if not execution_scope:
            raise ValueError("execution_scope is required for retry receipts")
        if not error.repairable_fields:
            raise ValueError("retry receipts require at least one repairable field")
        if any(not error.allowed_values.get(field) for field in error.repairable_fields):
            raise ValueError("every repairable field requires an allowed-values contract")
        token = "rtr1_" + secrets.token_urlsafe(32)
        record = _RetryReceipt(
            principal_key=self._principal_key(principal),
            execution_scope=execution_scope, server=server, tool=tool,
            original_args=deepcopy(request_arguments(original_args)),
            failure_category="argument_validation_error",
            repairable_fields=tuple(error.repairable_fields),
            allowed_values={key: tuple(values) for key, values in error.allowed_values.items()},
            coupled_text_fields={key: tuple(values) for key, values in error.coupled_text_fields.items()},
            expires_at=time.monotonic() + self._ttl_seconds,
        )
        with self._lock:
            current = time.monotonic()
            self._receipts = {
                key: value for key, value in self._receipts.items()
                if value.expires_at > current
            }
            if len(self._receipts) >= self._max_receipts:
                oldest = min(self._receipts, key=lambda key: self._receipts[key].expires_at)
                del self._receipts[oldest]
            self._receipts[self._digest(token)] = record
        coupled_repairs = []
        for field in record.repairable_fields:
            coupled_repairs.append({
                "field": field,
                "from": deepcopy(record.original_args.get(field)),
                "allowed_values": list(record.allowed_values.get(field, ())),
                "coupled_text_fields": list(record.coupled_text_fields.get(field, ())),
            })
        return {
            "retryable": True,
            "retry_of": token,
            "failure_category": record.failure_category,
            "repairable_fields": list(record.repairable_fields),
            "coupled_repairs": coupled_repairs,
        }

    def reconstruct(self, token: str, *, principal: object,
                    execution_scope: str, server: str, tool: str,
                    submitted_args: dict) -> tuple[dict, dict]:
        """Validate/consume receipt and deterministically reconstruct args."""
        if not isinstance(token, str) or not token.startswith("rtr1_"):
            raise InvalidRetryLineage("invalid_or_forged_receipt", submitted_args=submitted_args)
        with self._lock:
            record = self._receipts.get(self._digest(token))
            if record is None:
                raise InvalidRetryLineage("invalid_or_forged_receipt", submitted_args=submitted_args)
            if record.consumed:
                raise InvalidRetryLineage("receipt_already_consumed", submitted_args=submitted_args)
            if record.expires_at <= time.monotonic():
                record.consumed = True
                raise InvalidRetryLineage("receipt_expired", submitted_args=submitted_args)
            if record.principal_key != self._principal_key(principal):
                raise InvalidRetryLineage("wrong_principal", submitted_args=submitted_args)
            if record.execution_scope != execution_scope:
                raise InvalidRetryLineage("wrong_execution_scope", submitted_args=submitted_args)
            if record.server != server:
                raise InvalidRetryLineage("wrong_server", submitted_args=submitted_args)
            if record.tool != tool:
                raise InvalidRetryLineage("wrong_tool", submitted_args=submitted_args)
            if record.failure_category not in RETRY_CATEGORIES:
                raise InvalidRetryLineage("failure_category_not_retryable", submitted_args=submitted_args)

            effective = deepcopy(record.original_args)
            coupled_audit = []
            for field in record.repairable_fields:
                if field not in submitted_args:
                    raise InvalidRetryLineage("repairable_field_missing", submitted_args=submitted_args)
                old = record.original_args.get(field)
                new = submitted_args[field]
                allowed = record.allowed_values.get(field, ())
                if allowed and new not in allowed:
                    raise InvalidRetryLineage("repair_value_not_allowed", submitted_args=submitted_args)
                if new == old:
                    raise InvalidRetryLineage("repair_value_unchanged", submitted_args=submitted_args)
                effective[field] = deepcopy(new)
                for text_field in record.coupled_text_fields.get(field, ()):
                    original_text = record.original_args.get(text_field)
                    if isinstance(original_text, str) and isinstance(old, str) and isinstance(new, str):
                        repaired = original_text.replace(old, new)
                        effective[text_field] = repaired
                        coupled_audit.append({
                            "field": field, "text_field": text_field,
                            "from": old, "to": new,
                            "occurrences": original_text.count(old),
                        })

            ignored = [key for key in sorted(set(record.original_args) | set(submitted_args))
                       if key not in record.repairable_fields
                       and submitted_args.get(key) != record.original_args.get(key)]
            record.consumed = True
        return effective, {
            "contract": RECONSTRUCTION_CONTRACT,
            "retry_receipt_validated": True,
            "submitted_args": deepcopy(request_arguments(submitted_args)),
            "effective_args": deepcopy(effective),
            "ignored_retry_argument_drift": ignored,
            "coupled_repairs": coupled_audit,
        }


def input_failure(arguments: dict, error: ToolInputValidationError,
                  receipt: dict | None = None) -> dict:
    """Content-free receipt; the caller must explicitly copy retry_of to retry."""
    timestamp = now()
    retry = receipt or {"retry_of": uuid4().hex,
                        "retryable": True,
                        "failure_category": "argument_validation_error",
                        "repairable_fields": list(error.repairable_fields),
                        "coupled_repairs": []}
    return {
        "error_code": "argument_validation_error", "message": str(error),
        "retry_instruction": "Correct only the rejected fields for this same requirement and copy retry_of into the next call. Omit retry_of for an independent requirement.",
        "retryable": retry["retryable"],
        "retry_of": retry["retry_of"],
        "failure_category": retry["failure_category"],
        "repairable_fields": retry["repairable_fields"],
        "coupled_repairs": retry.get("coupled_repairs", []),
        "_tool_dependency": {
            "contract": RECONSTRUCTION_CONTRACT if receipt else CONTRACT, "status": "failed",
            "failure_category": "argument_validation_error",
            "request_fingerprint": fingerprint(request_arguments(arguments)),
            "repairable_fields": list(error.repairable_fields),
            "retryable": True,
            "coupled_repairs": retry.get("coupled_repairs", []),
            "provider_invoked": False, "created_at": timestamp,
            "completed_at": timestamp,
        },
    }


def invalid_retry_failure(error: InvalidRetryLineage) -> dict:
    timestamp = now()
    return {
        "error_code": "invalid_retry_lineage",
        "message": "Retry lineage could not be validated.",
        "retryable": False,
        "_tool_dependency": {
            "contract": RECONSTRUCTION_CONTRACT,
            "status": "failed",
            "failure_category": "invalid_retry_lineage",
            "lineage_error": error.reason,
            "provider_invoked": False,
            "created_at": timestamp,
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
        "submitted_args": deepcopy(request) if isinstance(request, dict) else request,
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
        accepted_contract = isinstance(meta, dict) and meta.get("contract") in {CONTRACT, RECONSTRUCTION_CONTRACT}
        token_valid = (isinstance(token, str)
                       and ((len(token) == 32 and all(c in "0123456789abcdef" for c in token))
                            or (token.startswith("rtr1_") and len(token) >= 32)))
        if (accepted_contract
                and meta.get("status") == "failed"
                and meta.get("failure_category") in RETRY_CATEGORIES
                and meta.get("provider_invoked") is False
                and meta.get("request_fingerprint") == observed["request_fingerprint"]
                and isinstance(fields, list) and fields and len(fields) <= 8
                and all(isinstance(key, str) and key in observed["argument_fingerprints"] for key in fields)
                and token_valid):
            observed.update(failure_category=meta["failure_category"],
                            repairable_fields=fields, retry_token=token,
                            provider_invoked=False,
                            validation_error=str(payload.get("error_code") or "argument_validation_error"),
                            coupled_repairs=deepcopy(meta.get("coupled_repairs", [])))
        elif (accepted_contract and meta.get("status") == "failed"
              and meta.get("failure_category") == "invalid_retry_lineage"
              and meta.get("provider_invoked") is False):
            observed.update(failure_category="invalid_retry_lineage",
                            provider_invoked=False,
                            lineage_error=str(meta.get("lineage_error") or "invalid_retry_lineage"))
        elif (accepted_contract and meta.get("status") == "completed"
              and meta.get("retry_receipt_validated") is True
              and meta.get("provider_invoked") is True):
            effective = meta.get("effective_args")
            submitted = meta.get("submitted_args")
            if isinstance(effective, dict) and isinstance(submitted, dict):
                observed.pop("failure_category", None)
                observed.update(
                    retry_receipt_validated=True,
                    provider_invoked=True,
                    submitted_args=deepcopy(submitted),
                    effective_args=deepcopy(effective),
                    effective_argument_fingerprints={key: fingerprint(item) for key, item in effective.items()},
                    ignored_retry_argument_drift=list(meta.get("ignored_retry_argument_drift", [])),
                    coupled_repairs=deepcopy(meta.get("coupled_repairs", [])),
                )
        elif (accepted_contract and meta.get("status") == "completed"
              and meta.get("provider_invoked") is True):
            observed.pop("failure_category", None)
    if "effective_args" not in observed and isinstance(request, dict):
        observed["effective_args"] = deepcopy(request)
        observed["effective_argument_fingerprints"] = dict(observed["argument_fingerprints"])
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
        server_lineage_error = (call.get("lineage_error")
                                if call.get("failure_category") == "invalid_retry_lineage"
                                else None)
        for field in ("retry_parent", "superseded_by", "supersession_status", "lineage_error", "logical_dependency_id"):
            call.pop(field, None)
        if server_lineage_error:
            call["lineage_error"] = server_lineage_error
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
                server_reconstructed = call.get("retry_receipt_validated") is True
                if (same_scope and not call.get("lineage_error")
                        and (stable or server_reconstructed) and candidate.get("provider_invoked") is False
                        and candidate.get("failure_category") in RETRY_CATEGORIES
                        and not candidate.get("superseded_by") and not candidate.get("lineage_error")):
                    parent = candidate
            if parent is None:
                call.setdefault("lineage_error", "invalid_retry_lineage")
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
