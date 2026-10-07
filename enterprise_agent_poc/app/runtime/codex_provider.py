from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.domain import RuntimeProfile, RuntimeSession, RuntimeStreamEvent, RuntimeTurn, SandboxPolicy
from app.activity_plan_runtime import (
    PLAN_SCHEMA, SAFE_FALLBACK, STRUCTURED_INSTRUCTION, chunk_validated_markdown, correction_prompt,
    is_full_activity_plan, merge_targeted_correction, parse_plan, render_markdown,
    result_envelope, safe_violations, semantic_guard,
)
from app.grounded_writing_runtime import (
    AUDIT_INSTRUCTION, MAX_PRIVATE_TEXT, SAFE_RESPONSE, apply_targeted_correction,
    audit_prompt, correction_prompt as grounding_correction_prompt, generation_prompt,
    parse_audit, parse_draft,
)
from app.security import RuntimePrincipal, RuntimeTokenIssuer
from app.skills import SkillDeployment
from app.settings import Settings
from app.tool_dependencies import attempt_observation, now, resolve_dependencies


MAX_STRUCTURED_MODEL_ATTEMPTS = 2
from app.runtime.base import RuntimeProvider, RuntimeStartError


@dataclass(slots=True)
class _Instance:
    codex: object
    profile: RuntimeProfile
    last_active_at: float
    token_expires_at: int


class CodexRuntimeManager:
    """Owns one local Codex App Server client per tenant-agent Runtime Profile."""

    def __init__(self, settings: Settings, deployment: SkillDeployment, token_issuer: RuntimeTokenIssuer) -> None:
        self._settings = settings
        self._deployment = deployment
        self._token_issuer = token_issuer
        self._instances: dict[str, _Instance] = {}
        self._startup_events: dict[str, list[dict]] = {}
        self._lock = asyncio.Lock()

    def _start_event(self, profile: RuntimeProfile, event: str, **details: str) -> None:
        self._startup_events.setdefault(profile.id, []).append({"event": event, **details})

    def startup_events(self, profile: RuntimeProfile) -> tuple[dict, ...]:
        """Return only lifecycle labels/stages; never provider error text or secrets."""
        return tuple(dict(event) for event in self._startup_events.get(profile.id, ()))

    def _paths(self, profile: RuntimeProfile) -> tuple[Path, Path]:
        root = self._settings.data_dir / "runtime" / profile.tenant_id / profile.agent_id / profile.id
        return root / "codex-home", root / "workspace"

    def _prepare_profile(self, profile: RuntimeProfile) -> tuple[Path, Path, str, int]:
        codex_home, workspace = self._paths(profile)
        workspace.mkdir(parents=True, exist_ok=True)
        skills_dir = codex_home / "skills"
        self._deployment.deploy(profile.skill_manifest, skills_dir)
        token_expires_at = int(time.time()) + 15 * 60
        scopes = ["enterprise_config:read", "knowledge:search", "assets:search"]
        if profile.agent_id == "image-agent":
            scopes.append("image:generate")
        if profile.profile_hash_version == "v2":
            scopes = list(profile.tool_scopes)
        token = self._token_issuer.issue(
            RuntimePrincipal(
                tenant_id=profile.tenant_id,
                agent_id=profile.agent_id,
                runtime_profile_id=profile.id,
                scopes=tuple(scopes),
                expires_at=token_expires_at,
                execution_context_id=profile.execution_context_id,
                instance_id=profile.instance_id,
            )
        )
        codex_home.mkdir(parents=True, exist_ok=True)
        provider_lines = []
        if self._settings.model_provider_id == "deepseek":
            catalog_path = codex_home / "models.json"
            catalog_path.write_text(json.dumps({"models": [self._deepseek_model_catalog(profile.model_id)]}), encoding="utf-8")
            provider_lines = [
                f'model = "{profile.model_id}"',
                'model_provider = "deepseek"',
                f'model_reasoning_effort = "{profile.reasoning_effort}"',
                f'model_catalog_json = "{catalog_path.as_posix()}"',
                "",
                "[model_providers.deepseek]",
                'name = "deepseek"',
                f'base_url = "{self._settings.model_base_url}"',
                f'wire_api = "{self._settings.model_wire_api}"',
                f'env_key = "{self._settings.codex_api_key_env}"',
                "requires_openai_auth = false",
                "",
            ]
        (codex_home / "config.toml").write_text(
            "\n".join(
                provider_lines + [
                    "[mcp_servers.platform]",
                    f'url = "{self._settings.platform_mcp_url}"',
                    'bearer_token_env_var = "PLATFORM_MCP_TOKEN"',
                    "required = true",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return codex_home, workspace, token, token_expires_at

    @staticmethod
    def _deepseek_model_catalog(model_id: str | None) -> dict:
        if model_id != "deepseek-v4-pro":
            raise ValueError("DeepSeek Gate 2 仅允许模型 deepseek-v4-pro。")
        return {
            "slug": "deepseek-v4-pro",
            "display_name": "DeepSeek-V4-Pro",
            "description": "DeepSeek V4 Pro for Codex Runtime POC.",
            "context_window": 1048576,
            "max_context_window": 1048576,
            "truncation_policy": {"mode": "tokens", "limit": 10000},
            "default_reasoning_level": "high",
            "supported_reasoning_levels": [
                {"effort": "low", "description": "Lower reasoning"},
                {"effort": "high", "description": "High reasoning"},
                {"effort": "max", "description": "Maximum reasoning"},
            ],
            "input_modalities": ["text"],
            "supports_parallel_tool_calls": True,
            "apply_patch_tool_type": "freeform",
            "web_search_tool_type": "text",
            "shell_type": "shell_command",
            "visibility": "list",
            "supported_in_api": True,
            "priority": 2,
            "availability_nux": None,
            "upgrade": None,
            "model_messages": {
                "instructions_template": "You are Codex. Follow developer instructions and tool policies.",
            },
            "support_verbosity": True,
            "default_verbosity": "low",
            "experimental_supported_tools": [],
            "minimal_client_version": "0.144.0",
        }

    async def get(self, profile: RuntimeProfile):
        async with self._lock:
            self._startup_events[profile.id] = [{"event": "runtime_start_requested"}]
            current = self._instances.get(profile.id)
            if current and current.token_expires_at > int(time.time()) + 60:
                current.last_active_at = time.monotonic()
                self._start_event(profile, "app_server_ready")
                return current.codex
            if current:
                await current.codex.close()
                del self._instances[profile.id]
            stage = "runtime_profile"
            try:
                from openai_codex import AsyncCodex, CodexConfig
                codex_home, workspace, token, token_expires_at = self._prepare_profile(profile)
                stage = "provider_initialization"
                api_key = os.environ.get(self._settings.codex_api_key_env)
                if not api_key:
                    raise RuntimeError("Codex API Key 未配置。")
                env = {
                    "CODEX_HOME": str(codex_home),
                    "PLATFORM_MCP_TOKEN": token,
                    # The secret stays process-local: it is never written into
                    # config.toml, the database, trace payloads, or source files.
                    self._settings.codex_api_key_env: api_key,
                }
                codex = AsyncCodex(CodexConfig(cwd=str(workspace), env=env))
                self._start_event(profile, "runtime_process_created")
                stage = "app_server"
                await codex.__aenter__()
                if profile.profile_hash_version == "v2":
                    from openai_codex.generated.v2_all import SkillsListResponse
                    stage = "skill_discovery"
                    discovered = await codex._client.request("skills/list", {"cwds":[str(workspace)],"forceReload":True}, response_model=SkillsListResponse)
                    names = {s.name for entry in discovered.data for s in entry.skills if s.enabled}
                    for name in profile.skill_manifest:
                        if name not in names:
                            raise RuntimeError("Bound Skill not discovered by Codex")
                        self._start_event(profile,"skill_discovered",skill=name)
                if profile.model_provider_id != "deepseek":
                    await codex.login_api_key(api_key)
                self._start_event(profile, "app_server_ready")
                self._instances[profile.id] = _Instance(
                    codex=codex,
                    profile=profile,
                    last_active_at=time.monotonic(),
                    token_expires_at=token_expires_at,
                )
                return codex
            except Exception as exc:
                self._start_event(profile, "runtime_start_failed", stage=stage)
                if 'codex' in locals():
                    try:
                        await codex.close()
                    except Exception:
                        pass
                raise RuntimeStartError(stage) from exc

    async def close(self) -> None:
        async with self._lock:
            instances, self._instances = list(self._instances.values()), {}
        for instance in instances:
            await instance.codex.close()


class CodexRuntimeProvider(RuntimeProvider):
    def __init__(self, manager: CodexRuntimeManager) -> None:
        self._manager = manager
        self._profiles: dict[str, RuntimeProfile] = {}
        self._threads: dict[str, object] = {}
        self._active_turns: dict[str, object] = {}
        self._grounding_child_sessions: dict[str, RuntimeSession] = {}

    @staticmethod
    def _sandbox(policy: SandboxPolicy):
        from openai_codex import Sandbox

        return Sandbox.read_only if policy is SandboxPolicy.READ_ONLY else Sandbox.workspace_write

    @staticmethod
    def _runtime_session_config(profile: RuntimeProfile, execution_scope: str) -> dict:
        """Install a server-visible, model-inaccessible scope per product Task."""
        return {
            "model_reasoning_effort": profile.reasoning_effort,
            "mcp_servers": {
                "platform": {
                    "http_headers": {
                        "X-Runtime-Execution-Scope": execution_scope,
                    },
                },
            },
        }

    @staticmethod
    def _rollout_unavailable(error: Exception) -> bool:
        text = str(error).lower()
        return "no rollout found for thread id" in text or (
            "failed to read thread: thread-store internal error" in text
            and "belongs to thread" in text
            and "expected" in text
        )

    async def create_session(self, profile: RuntimeProfile, developer_instructions: str,
                             *, task_id: str | None = None) -> RuntimeSession:
        codex = await self._manager.get(profile)
        self._manager._start_event(profile, "thread_start_requested")
        execution_scope = self._manager._token_issuer.issue_task_scope(profile.tenant_id, task_id) if task_id else uuid4().hex
        try:
            thread = await codex.thread_start(
                cwd=str(self._manager._paths(profile)[1]),
                developer_instructions=developer_instructions,
                model=profile.model_id,
                config=self._runtime_session_config(profile, execution_scope),
                model_provider=profile.model_provider_id,
                sandbox=self._sandbox(profile.sandbox),
            )
        except Exception as exc:
            self._manager._start_event(profile, "runtime_start_failed", stage="thread_start")
            raise RuntimeStartError("thread_start") from exc
        self._manager._start_event(profile, "thread_started")
        self._profiles[profile.id] = profile
        self._threads[thread.id] = thread
        return RuntimeSession(thread_id=thread.id, profile_id=profile.id)

    def startup_events(self, profile: RuntimeProfile) -> tuple[dict, ...]:
        return self._manager.startup_events(profile)

    async def resume_session(
        self,
        profile: RuntimeProfile,
        thread_id: str,
        developer_instructions: str | None = None,
        recovery_context: str | None = None,
        *, task_id: str | None = None,
    ) -> RuntimeSession:
        codex = await self._manager.get(profile)
        self._manager._start_event(profile, "thread_resume_requested")
        execution_scope = self._manager._token_issuer.issue_task_scope(profile.tenant_id, task_id) if task_id else uuid4().hex
        try:
            thread = await codex.thread_resume(
                thread_id,
                cwd=str(self._manager._paths(profile)[1]),
                model=profile.model_id,
                config=self._runtime_session_config(profile, execution_scope),
                model_provider=profile.model_provider_id,
                sandbox=self._sandbox(profile.sandbox),
            )
            self._manager._start_event(profile, "thread_resumed")
        except Exception as exc:
            if not self._rollout_unavailable(exc) or not developer_instructions:
                raise
            self._manager._start_event(profile, "thread_resume_unavailable")
            recovered_instructions = developer_instructions
            if recovery_context:
                recovered_instructions += (
                    "\n\nThe prior runtime rollout is unavailable. Continue using only this "
                    "user-visible conversation history; it contains no hidden reasoning:\n" + recovery_context
                )
            self._manager._start_event(profile, "thread_start_requested")
            thread = await codex.thread_start(
                cwd=str(self._manager._paths(profile)[1]),
                developer_instructions=recovered_instructions,
                model=profile.model_id,
                config=self._runtime_session_config(profile, execution_scope),
                model_provider=profile.model_provider_id,
                sandbox=self._sandbox(profile.sandbox),
            )
            self._manager._start_event(profile, "thread_started")
        self._profiles[profile.id] = profile
        self._threads[thread.id] = thread
        return RuntimeSession(thread_id=thread.id, profile_id=profile.id)

    async def run_turn(self, session: RuntimeSession, message: str) -> RuntimeTurn:
        profile = self._profiles[session.profile_id]
        if (getattr(profile, "grounding_policy", None) and profile.grounding_policy.get("enabled") is True
                or is_full_activity_plan(getattr(profile, "agent_id", ""), message)):
            async for event in self.stream_turn(session, message):
                if event.kind == "completed" and event.turn is not None:
                    return event.turn
            raise RuntimeError("structured turn completed event not received")
        thread = await self._thread_for_session(session, profile)
        result = await thread.run(message, sandbox=self._sandbox(profile.sandbox))
        return self._runtime_turn_from_result(session, profile, result)

    async def _thread_for_session(self, session: RuntimeSession, profile: RuntimeProfile):
        thread = self._threads.get(session.thread_id)
        if thread is None:
            codex = await self._manager.get(profile)
            thread = await codex.thread_resume(session.thread_id, sandbox=self._sandbox(profile.sandbox))
            self._threads[session.thread_id] = thread
        return thread

    def _runtime_turn_from_result(self, session: RuntimeSession, profile: RuntimeProfile, result: object) -> RuntimeTurn:
        lifecycle_events: list[dict] = [{"event": "turn_started"}]
        usage = result.usage
        mcp_calls: list[dict] = []
        scope = str(getattr(result, "turn_id", None) or uuid4().hex)
        scope = f"{session.thread_id}:{scope}"
        timestamps = getattr(result, "attempt_timestamps", {})
        for index, wrapped_item in enumerate(result.items):
            item = getattr(wrapped_item, "root", wrapped_item)
            if getattr(profile,"profile_hash_version","v1") == "v2" and getattr(item,"exit_code",None) == 0:
                for wrapped_action in getattr(item,"command_actions",()):
                    action = getattr(wrapped_action,"root",wrapped_action)
                    if getattr(action,"type",None) == "read":
                        path = str(getattr(getattr(action,"path",None),"root",getattr(action,"path","")))
                        for name in profile.skill_manifest:
                            if path.replace('\\','/').endswith(f"/{name}/SKILL.md"):
                                lifecycle_events.append({"event":"skill_read","skill":name})
            # Only structured tool observations are retained. Reasoning items and
            # their hidden content are intentionally excluded from the trace.
            tool = getattr(item, "tool", None)
            server = getattr(item, "server", None)
            if tool and server:
                raw_tool_status = getattr(item, "status", "completed")
                # The SDK currently exposes a string such as
                # ``McpToolCallStatus.completed`` for some providers.  Store a
                # stable status vocabulary in the product trace.
                status = str(getattr(raw_tool_status, "value", raw_tool_status)).rsplit(".", 1)[-1].lower()
                arguments = getattr(item, "arguments", None)
                parsed_arguments = self._json_value(arguments)
                if tool=='skill_action_execute':
                    from app.skill_dispatch import safe_arguments
                    parsed_arguments=safe_arguments(parsed_arguments)
                    arguments=json.dumps(parsed_arguments,ensure_ascii=False)
                tool_result = getattr(item, "result", None)
                call = {
                    "server": server,
                    "tool": tool,
                    "input_summary": self._summary(arguments),
                    "output_summary": self._summary(tool_result),
                    "status": status,
                    "duration_ms": getattr(item, "duration_ms", None),
                    "error": self._summary(getattr(item, "error", None)),
                    # Compatibility fingerprint uses full arguments, not the
                    # truncated display summary. V1.1 separately retains
                    # submitted/effective args in the authorized Run Trace.
                    "dependency_id": hashlib.sha256(json.dumps(parsed_arguments if parsed_arguments is not None else arguments, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest(),
                    "result_is_error": bool(getattr(tool_result, "is_error", False) or
                                            (isinstance(tool_result, dict) and (tool_result.get("isError") or tool_result.get("is_error")))),
                }
                call_id = str(getattr(item, "id", None) or f"observed-{index}")
                times = timestamps.get(call_id, {})
                observed_at = now()
                call.update(attempt_observation(
                    parsed_arguments if parsed_arguments is not None else arguments,
                    tool_result, scope=scope, tool_call_id=call_id,
                    created_at=times.get("created_at", observed_at),
                    completed_at=times.get("completed_at", observed_at),
                ))
                if call.get("failure_category"):
                    call["status"] = "failed"
                    call["result_is_error"] = True
                if tool == "image_generation":
                    artifact = self._image_artifact(tool_result)
                    if artifact:
                        # Persist only the platform-owned object key. The full
                        # tool result may contain provider metadata or URLs and
                        # remains outside the durable product trace.
                        call["artifact"] = artifact
                if tool == "knowledge_search":
                    call["retrieval_observation"] = self._retrieval_observation(arguments, tool_result)
                mcp_calls.append(call)
                lifecycle_events.extend(({"event": "tool_started", "tool": tool}, {"event": "tool_completed", "tool": tool, "status": status}))
        text = result.final_response or ""
        if mcp_calls:
            lifecycle_events.append({"event": "model_resumed"})
        if text.strip():
            lifecycle_events.append({"event": "final_response_received", "length": len(text)})
        raw_status = getattr(result, "status", None)
        raw_status = str(getattr(raw_status, "value", raw_status) or "unknown").rsplit(".", 1)[-1].lower()
        lifecycle_events.append({"event": "turn_completed", "status": raw_status, "has_final_response": bool(text.strip())})
        return RuntimeTurn(
            thread_id=session.thread_id,
            text=text,
            input_tokens=getattr(usage, "input_tokens", None) if usage else None,
            output_tokens=getattr(usage, "output_tokens", None) if usage else None,
            latency_ms=getattr(result, "duration_ms", None),
            mcp_calls=tuple(mcp_calls),
            status=raw_status,
            error=self._summary(getattr(result, "error", None)),
            lifecycle_events=tuple(lifecycle_events),
        )

    async def stream_turn(self, session: RuntimeSession, message: str) -> AsyncIterator[RuntimeStreamEvent]:
        """Stream public text for simple turns, validated Markdown for full plans."""
        profile = self._profiles[session.profile_id]
        if getattr(profile, "grounding_policy", None) and profile.grounding_policy.get("enabled") is True:
            async for event in self._grounded_stream_turn(session, profile, message):
                yield event
            return
        if not is_full_activity_plan(getattr(profile, "agent_id", ""), message):
            async for event in self._stream_provider_turn(session, profile, message):
                yield event
            return

        first_prompt = message + "\n\n" + STRUCTURED_INSTRUCTION
        prompt = first_prompt
        original = None
        original_violations = []
        violations = []
        last_turn = None
        use_schema = True
        grounding: tuple[str, ...] = ()
        attempt_trace: list[dict] = []
        model_calls = 0

        def record(stage: str, attempt: int, *, format_valid: bool | None = None,
                   semantic_valid: bool | None = None, correction_result: str | None = None,
                   issues: list | None = None) -> None:
            safe = safe_violations(issues or [])
            attempt_trace.append({
                "structured_attempt_index": attempt, "stage": stage,
                "format_valid": format_valid, "semantic_valid": semantic_valid,
                "violation_count": len(safe), "violation_type": sorted({item["type"] for item in safe}),
                "violations": safe, "correction_attempted": attempt > 1,
                "correction_result": correction_result,
                "total_model_calls_for_structured_result": model_calls,
            })

        for attempt in range(1, MAX_STRUCTURED_MODEL_ATTEMPTS + 1):
            generation_stage = ("semantic_correcting" if attempt == 2 and original is not None
                                and original_violations else "full_plan_generating")
            yield RuntimeStreamEvent.activity(generation_stage, "started")
            model_calls += 1
            record("INITIAL" if attempt == 1 else "CORRECTION", attempt)
            try:
                async for event in self._stream_provider_turn(
                    session, profile, prompt, output_schema=PLAN_SCHEMA if use_schema else None,
                    visible_deltas=False,
                ):
                    if event.kind == "completed":
                        last_turn = event.turn
                    else:
                        yield event
            except Exception as exc:
                # A provider/adapter that rejects the schema can use one raw
                # JSON attempt. Operational failures still follow normal error handling.
                wording = str(exc).lower()
                if attempt == 1 and any(part in wording for part in ("output_schema", "output schema", "json_schema", "json schema", "response_format")):
                    yield RuntimeStreamEvent.activity(generation_stage, "completed")
                    record("FORMAT_VALIDATION", attempt, format_valid=False)
                    use_schema = False
                    prompt = first_prompt
                    continue
                raise
            if last_turn is None:
                raise RuntimeError("structured turn completed event not received")
            yield RuntimeStreamEvent.activity(generation_stage, "completed")
            grounding += tuple(str(call.get("output_summary") or "") for call in last_turn.mcp_calls
                               if call.get("tool") in {"knowledge_search", "enterprise_config_get"}
                               and call.get("status") == "completed")
            yield RuntimeStreamEvent.activity("structured_validating", "started")
            candidate = parse_plan(last_turn.text)
            record("FORMAT_VALIDATION", attempt, format_valid=candidate is not None)
            correction_merge_valid = True
            if attempt == 2 and candidate is not None and original is not None:
                merged = merge_targeted_correction(original, candidate, original_violations)
                correction_merge_valid = merged is not None
                candidate = merged
            yield RuntimeStreamEvent.activity("structured_validating", "completed")
            violations = []
            if candidate is not None:
                yield RuntimeStreamEvent.activity("semantic_validating", "started")
                violations = semantic_guard(candidate, grounding)
                yield RuntimeStreamEvent.activity("semantic_validating", "completed")
            valid = candidate is not None and correction_merge_valid and not violations
            record("SEMANTIC_VALIDATION" if attempt == 1 else "REVALIDATION", attempt,
                   format_valid=candidate is not None, semantic_valid=valid,
                   correction_result=("PASS" if valid else "FAIL") if attempt == 2 else None,
                   issues=violations)
            if valid:
                yield RuntimeStreamEvent.activity("result_rendering", "started")
                markdown = render_markdown(candidate)
                chunks = chunk_validated_markdown(markdown)
                yield RuntimeStreamEvent.activity("result_rendering", "completed")
                record("FINAL", attempt, format_valid=True, semantic_valid=True,
                       correction_result="PASS" if attempt == 2 else None)
                for chunk in chunks:
                    yield RuntimeStreamEvent.visible_delta(chunk)
                yield RuntimeStreamEvent.completed(replace(
                    last_turn, text=markdown, structured_result=result_envelope(candidate), structured_diagnostic=None,
                    structured_attempt_trace=tuple(attempt_trace),
                    structured_result_status="validated_after_retry" if attempt == 2 else "validated_initial",
                    structured_model_calls=model_calls,
                ))
                return
            if attempt == 1:
                original = candidate
                original_violations = violations
                prompt = correction_prompt(candidate, violations)

        fallback = SAFE_FALLBACK
        status = ("semantic_guard_failed_after_retry" if violations else
                  "targeted_correction_failed_after_retry" if not correction_merge_valid else
                  "format_validation_failed_after_retry")
        record("FINAL", MAX_STRUCTURED_MODEL_ATTEMPTS, format_valid=candidate is not None,
               semantic_valid=False, correction_result="FAIL", issues=violations)
        yield RuntimeStreamEvent.visible_delta(fallback)
        yield RuntimeStreamEvent.completed(replace(
            last_turn, text=fallback, structured_result=None,
            structured_diagnostic={"code": status, "violations": safe_violations(violations)},
            structured_attempt_trace=tuple(attempt_trace), structured_result_status=status,
            structured_model_calls=model_calls,
        ))

    async def _grounded_stream_turn(
        self, session: RuntimeSession, profile: RuntimeProfile, message: str,
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """No article delta leaves this boundary before independent audit PASS."""
        started = time.monotonic()
        telemetry = {
            "grounding_enabled": True, "grounding_mode": "claim_audit_v1",
            "generation_calls": 0, "audit_calls": 0, "initial_pass": False,
            "correction_invoked": False, "final_pass": False,
            "violation_categories": [], "draft_duration_ms": None,
            "audit_duration_ms": None, "correction_duration_ms": None,
            "re_audit_duration_ms": None, "duration_ms": None,
        }
        generation_turns: list[RuntimeTurn] = []
        material: list[str] = []
        final = SAFE_RESPONSE
        audit_session = None
        phase = None
        try:
            if len(message) > MAX_PRIVATE_TEXT:
                raise ValueError("Grounding input exceeds private limit")
            phase = "grounded_drafting"
            yield RuntimeStreamEvent.activity(phase, "started")
            stage_started = time.monotonic()
            telemetry["generation_calls"] = 1
            draft_turn = await self._collect_hidden_turn(
                session, profile, generation_prompt(message), material_sink=material,
            )
            telemetry["draft_duration_ms"] = round((time.monotonic() - stage_started) * 1000)
            generation_turns.append(draft_turn)
            yield RuntimeStreamEvent.activity(phase, "completed")
            if sum(len(item) for item in material) > MAX_PRIVATE_TEXT:
                raise ValueError("Grounding material exceeds private limit")
            draft = parse_draft(draft_turn.text, len(material))
            if draft is None:
                raise ValueError("Invalid private grounded draft")

            # A separate tool-less profile and developer instruction keep the
            # audit independent of the drafting thread and tenant data scopes.
            audit_profile = replace(
                profile,
                id=hashlib.sha256((profile.id + ":grounding-audit-v1").encode()).hexdigest()[:24],
                skill_manifest={}, tool_scopes=(), required_tools=(), grounding_policy=None,
            )
            audit_session = await self.create_session(audit_profile, AUDIT_INSTRUCTION)
            self._grounding_child_sessions[session.thread_id] = audit_session
            phase = "grounding_auditing"
            yield RuntimeStreamEvent.activity(phase, "started")
            stage_started = time.monotonic()
            telemetry["audit_calls"] = 1
            audit_turn = await self._collect_hidden_turn(
                audit_session, audit_profile,
                audit_prompt(message, material, draft.fact_ledger, draft.article),
            )
            telemetry["audit_duration_ms"] = round((time.monotonic() - stage_started) * 1000)
            audit = parse_audit(audit_turn.text, draft.article)
            if audit is None:
                raise ValueError("Invalid private grounding audit")
            telemetry["violation_categories"] = audit.categories
            telemetry["initial_pass"] = audit.grounded
            yield RuntimeStreamEvent.activity(phase, "completed")
            if audit.grounded:
                final = draft.article
                telemetry["final_pass"] = True
            else:
                phase = "grounding_correcting"
                yield RuntimeStreamEvent.activity(phase, "started")
                stage_started = time.monotonic()
                telemetry["generation_calls"] = 2
                telemetry["correction_invoked"] = True
                correction_turn = await self._collect_hidden_turn(
                    session, profile,
                    grounding_correction_prompt(message, material, draft.fact_ledger, draft.article, audit),
                )
                telemetry["correction_duration_ms"] = round((time.monotonic() - stage_started) * 1000)
                generation_turns.append(correction_turn)
                corrected = apply_targeted_correction(correction_turn.text, draft.article, audit)
                if corrected is None:
                    raise ValueError("Invalid private targeted correction")
                yield RuntimeStreamEvent.activity(phase, "completed")
                phase = "grounding_revalidating"
                yield RuntimeStreamEvent.activity(phase, "started")
                stage_started = time.monotonic()
                telemetry["audit_calls"] = 2
                re_audit_turn = await self._collect_hidden_turn(
                    audit_session, audit_profile,
                    audit_prompt(message, material, draft.fact_ledger, corrected),
                )
                telemetry["re_audit_duration_ms"] = round((time.monotonic() - stage_started) * 1000)
                re_audit = parse_audit(re_audit_turn.text, corrected)
                if re_audit is None:
                    raise ValueError("Invalid private grounding re-audit")
                telemetry["violation_categories"] = sorted(
                    set(telemetry["violation_categories"]) | set(re_audit.categories)
                )
                yield RuntimeStreamEvent.activity(phase, "completed")
                if re_audit.grounded:
                    final = corrected
                    telemetry["final_pass"] = True
            if telemetry["final_pass"] and not self._grounding_required_tools_satisfied(profile, generation_turns):
                final = SAFE_RESPONSE
                telemetry["final_pass"] = False
        except Exception:
            # Provider, parse, tool, and audit failures all close to the same
            # content-free public response; no draft or exception is traced.
            final = SAFE_RESPONSE
            telemetry["final_pass"] = False
        finally:
            self._grounding_child_sessions.pop(session.thread_id, None)
        phase = "grounded_rendering"
        yield RuntimeStreamEvent.activity(phase, "started")
        from app.activity_plan_runtime import chunk_validated_markdown
        for chunk in chunk_validated_markdown(final):
            yield RuntimeStreamEvent.visible_delta(chunk)
        telemetry["duration_ms"] = round((time.monotonic() - started) * 1000)
        yield RuntimeStreamEvent.activity(phase, "completed")
        turns = generation_turns
        yield RuntimeStreamEvent.completed(RuntimeTurn(
            thread_id=session.thread_id, text=final,
            input_tokens=sum(turn.input_tokens or 0 for turn in turns) or None,
            output_tokens=sum(turn.output_tokens or 0 for turn in turns) or None,
            latency_ms=telemetry["duration_ms"],
            mcp_calls=tuple(call for turn in turns for call in turn.mcp_calls),
            lifecycle_events=tuple(event for turn in turns for event in turn.lifecycle_events),
            grounding_telemetry=telemetry,
        ))

    async def _collect_hidden_turn(
        self, session: RuntimeSession, profile: RuntimeProfile, prompt: str,
        *, material_sink: list[str] | None = None,
    ) -> RuntimeTurn:
        completed = None
        async for event in self._stream_provider_turn(
            session, profile, prompt, visible_deltas=False, material_sink=material_sink,
        ):
            if event.kind == "completed":
                completed = event.turn
        if completed is None:
            raise RuntimeError("Hidden turn did not complete")
        status = str(getattr(completed.status, "value", completed.status) or "unknown").rsplit(".", 1)[-1].lower()
        if status not in {"completed", "success"} or completed.error:
            raise RuntimeError("Hidden turn failed")
        return completed

    @staticmethod
    def _grounding_required_tools_satisfied(profile: RuntimeProfile, turns: list[RuntimeTurn]) -> bool:
        calls = [call for turn in turns for call in turn.mcp_calls]
        _, dependencies, required = resolve_dependencies(calls)
        return (all(item["satisfied"] for item in dependencies)
                and all(required.get(tool, {}).get("satisfied") is True for tool in profile.required_tools))

    async def _stream_provider_turn(
        self, session: RuntimeSession, profile: RuntimeProfile, message: str,
        *, output_schema: dict | None = None, visible_deltas: bool = True,
        material_sink: list[str] | None = None,
    ) -> AsyncIterator[RuntimeStreamEvent]:
        """Collect one SDK turn, preserving public activity and tool evidence."""
        from openai_codex.generated.v2_all import (
            AgentMessageDeltaNotification,
            ItemCompletedNotification,
            ItemStartedNotification,
            ThreadTokenUsageUpdatedNotification,
            TurnCompletedNotification,
            TurnStatus,
        )

        thread = await self._thread_for_session(session, profile)
        options = {"sandbox": self._sandbox(profile.sandbox)}
        if output_schema is not None:
            options["output_schema"] = output_schema
        handle = await thread.turn(message, **options)
        self._active_turns[session.thread_id] = handle
        items: list[object] = []
        attempt_timestamps: dict[str, dict] = {}
        usage = None
        completed = None
        stream = handle.stream()
        try:
            async for notification in stream:
                payload = notification.payload
                if (
                    notification.method == "item/agentMessage/delta"
                    and isinstance(payload, AgentMessageDeltaNotification)
                    and payload.turn_id == handle.id
                    and payload.delta
                ):
                    if visible_deltas:
                        yield RuntimeStreamEvent.visible_delta(payload.delta)
                elif isinstance(payload, ItemStartedNotification) and payload.turn_id == handle.id:
                    item = getattr(payload.item, "root", payload.item)
                    if getattr(item, "tool", None) and getattr(item, "id", None):
                        attempt_timestamps.setdefault(item.id, {})["created_at"] = now()
                    activity = self._safe_tool_activity(getattr(payload, "item", None), "started")
                    if activity:
                        yield activity
                elif isinstance(payload, ItemCompletedNotification) and payload.turn_id == handle.id:
                    items.append(payload.item)
                    item = getattr(payload.item, "root", payload.item)
                    if getattr(item, "tool", None) and getattr(item, "id", None):
                        attempt_timestamps.setdefault(item.id, {})["completed_at"] = now()
                    if (material_sink is not None and getattr(item, "tool", None) in
                            {"knowledge_search", "enterprise_config_get", "asset_search"}
                            and getattr(item, "result", None) is not None):
                        material_sink.append(str(item.result))
                    activity = self._safe_tool_activity(payload.item, "completed")
                    if activity:
                        yield activity
                elif isinstance(payload, ThreadTokenUsageUpdatedNotification) and payload.turn_id == handle.id:
                    usage = payload.token_usage
                elif isinstance(payload, TurnCompletedNotification) and payload.turn.id == handle.id:
                    completed = payload.turn
        finally:
            await stream.aclose()
            self._active_turns.pop(session.thread_id, None)
        if completed is None:
            raise RuntimeError("turn completed event not received")
        if completed.status == TurnStatus.failed:
            message = getattr(getattr(completed, "error", None), "message", None)
            raise RuntimeError(message or "turn failed")
        final_response = self._final_assistant_response(items or completed.items)
        result = SimpleNamespace(
            usage=usage,
            items=items or completed.items,
            final_response=final_response,
            status=completed.status,
            error=completed.error,
            duration_ms=completed.duration_ms,
            turn_id=handle.id,
            attempt_timestamps=attempt_timestamps,
        )
        yield RuntimeStreamEvent.completed(self._runtime_turn_from_result(session, profile, result))

    @staticmethod
    def _safe_tool_activity(item: object, status: str) -> RuntimeStreamEvent | None:
        """Map actual Codex thread tool items to allowlisted product categories."""
        from openai_codex.generated.v2_all import DynamicToolCallThreadItem, McpToolCallThreadItem

        if status not in {"started", "completed"}:
            return None
        thread_item = getattr(item, "root", item)
        if isinstance(thread_item, McpToolCallThreadItem):
            tool = thread_item.tool
        elif isinstance(thread_item, DynamicToolCallThreadItem):
            tool = ""
        else:
            return None
        stage = {
            "enterprise_config_get": "enterprise_config_loading",
            "knowledge_search": "knowledge_retrieving",
            "asset_search": "asset_retrieving",
        }.get(tool, "tool_running")
        return RuntimeStreamEvent.activity(stage, status)

    async def cancel_turn(self, session: RuntimeSession) -> bool:
        """Interrupt the SDK turn when it is still active.

        AsyncTurnHandle.interrupt is the provider's remote cancellation request;
        it is deliberately kept distinct from closing this process's stream.
        """
        handle = self._active_turns.get(session.thread_id)
        if handle is None:
            child = self._grounding_child_sessions.get(session.thread_id)
            if child is not None:
                handle = self._active_turns.get(child.thread_id)
        if handle is None:
            return False
        await handle.interrupt()
        return True

    @staticmethod
    def _final_assistant_response(items: list[object]) -> str | None:
        fallback = None
        for wrapped_item in reversed(items):
            item = getattr(wrapped_item, "root", wrapped_item)
            text = getattr(item, "text", None)
            if not isinstance(text, str):
                continue
            phase = str(getattr(getattr(item, "phase", None), "value", getattr(item, "phase", None)) or "")
            if phase == "final_answer":
                return text
            if not phase and fallback is None:
                fallback = text
        return fallback

    @staticmethod
    def _summary(value: object, limit: int = 600) -> str | None:
        if value is None:
            return None
        text = str(value).replace("\n", " ")
        text = re.sub(r"Incorrect API key provided:\s*[^,.]+", "API key rejected", text, flags=re.IGNORECASE)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    @staticmethod
    def _json_value(value: object) -> object | None:
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return None

    @classmethod
    def _image_artifact(cls, result: object) -> dict | None:
        """Extract only platform-owned artifact identity and size metadata."""
        structured = getattr(result, "structured_content", None)
        if structured is None and isinstance(result, dict):
            structured = result.get("structured_content", result.get("structuredContent"))
        structured = cls._json_value(structured) or structured
        if not isinstance(structured, dict):
            return None
        records = structured.get("results")
        if not isinstance(records, list):
            return None
        for record in records:
            if not isinstance(record, dict):
                continue
            storage_key = record.get("storage_key")
            if not isinstance(storage_key, str):
                continue
            storage_key = storage_key.strip()
            parts = storage_key.split("/")
            if (
                storage_key.startswith("generated/")
                and len(storage_key) <= 1024
                and "\\" not in storage_key
                and all(part not in {"", ".", ".."} for part in parts)
            ):
                artifact = {"storage_key": storage_key}
                requested_size = record.get("requested_size")
                if isinstance(requested_size, str) and re.fullmatch(r"\d{1,5}x\d{1,5}", requested_size):
                    artifact["requested_size"] = requested_size
                return artifact
        return None

    @classmethod
    def _retrieval_observation(cls, arguments: object, result: object) -> dict:
        """Persist minimal retrieval evidence without retaining query or chunk text."""
        argument_value = cls._json_value(arguments)
        query = argument_value.get("query") if isinstance(argument_value, dict) else None
        result_value = cls._json_value(result)
        records = result_value if isinstance(result_value, list) else []
        observations = []
        for item in records[:10]:
            if not isinstance(item, dict):
                continue
            observations.append(
                {
                    "chunk_id": item.get("chunk_id", item.get("id")),
                    "file_id": item.get("file_id"),
                    "score": item.get("score"),
                    "accepted": item.get("accepted"),
                    "rejection_reason": item.get("rejection_reason"),
                }
            )
        return {
            "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest() if isinstance(query, str) else None,
            "query_length": len(query) if isinstance(query, str) else None,
            "result_count": len(records) if isinstance(result_value, list) else None,
            "results": observations,
        }

    async def close(self) -> None:
        await self._manager.close()
