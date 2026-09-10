from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum


class SandboxPolicy(StrEnum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    id: str
    tenant_id: str
    agent_id: str
    model_provider_id: str | None
    model_id: str | None
    reasoning_effort: str
    skill_manifest: dict[str, str]
    sandbox: SandboxPolicy
    runtime_version: str

    @classmethod
    def build(
        cls,
        *,
        tenant_id: str,
        agent_id: str,
        model_provider_id: str | None,
        model_id: str | None,
        reasoning_effort: str,
        skill_manifest: dict[str, str],
        sandbox: SandboxPolicy = SandboxPolicy.READ_ONLY,
        runtime_version: str = "openai-codex==0.147.0",
    ) -> "RuntimeProfile":
        payload = {
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "model_provider_id": model_provider_id,
            "model_id": model_id,
            "reasoning_effort": reasoning_effort,
            "skill_manifest": dict(sorted(skill_manifest.items())),
            "sandbox": sandbox.value,
            "runtime_version": runtime_version,
        }
        profile_id = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
        return cls(id=profile_id, **payload)


@dataclass(frozen=True, slots=True)
class RuntimeSession:
    thread_id: str
    profile_id: str


@dataclass(frozen=True, slots=True)
class RuntimeTurn:
    thread_id: str
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    mcp_calls: tuple[dict, ...] = ()
    status: str = "completed"
    error: str | None = None
    lifecycle_events: tuple[dict, ...] = ()
