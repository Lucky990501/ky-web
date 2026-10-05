from __future__ import annotations

import uuid
from dataclasses import dataclass
from collections.abc import Awaitable
from typing import Callable
import asyncio

from app.domain import RuntimeProfile, RuntimeSession
from app.runtime.base import RuntimeProvider, RuntimeStartError
from app.settings import Settings
from app.store import POCStore
from app.agent_catalog import get_agent
from app.tool_dependencies import AUDIT_FIELDS, call_completed, resolve_dependencies


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    conversation_id: str
    thread_id: str
    text: str


class AgentRunError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        run_id: str,
        conversation_id: str,
        error_code: str = "runtime_error",
        failure_stage: str = "runtime",
    ) -> None:
        super().__init__(message)
        self.run_id, self.conversation_id = run_id, conversation_id
        self.error_code, self.failure_stage = error_code, failure_stage


class GenerationCancelled(RuntimeError):
    """The product task requested that this locally-running turn stop."""


class AgentService:
    def __init__(self, store: POCStore, runtime: RuntimeProvider, settings: Settings, skill_manifest_resolver: Callable[[str], dict[str, str]] | None = None) -> None:
        self._store = store
        self._runtime = runtime
        self._settings = settings
        self._skill_manifest_resolver = skill_manifest_resolver

    def profile_for(self, tenant_id: str, agent_id: str) -> RuntimeProfile:
        agent = get_agent(agent_id)
        skill_manifest = self._skill_manifest_resolver(agent_id) if self._skill_manifest_resolver else agent.skill_manifest
        return RuntimeProfile.build(
            tenant_id=tenant_id,
            agent_id=agent_id,
            model_provider_id=self._settings.model_provider_id,
            model_id=self._settings.model_id,
            reasoning_effort=self._settings.reasoning_effort,
            skill_manifest=skill_manifest,
        )

    async def run(
        self,
        tenant_id: str,
        agent_id: str,
        message: str,
        conversation_id: str | None = None,
        *,
        defer_result_persistence: bool = False,
        execution_context: dict | None = None,
        on_visible_delta: Callable[[str], Awaitable[None]] | None = None,
        on_execution_activity: Callable[[str, str], None] | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        on_run_started: Callable[[str, str], None] | None = None,
        task_id: str | None = None,
        reference_image_attached: bool = False,
    ) -> RunResult:
        if execution_context is None:
            profile = self.profile_for(tenant_id, agent_id)
            agent = get_agent(agent_id)
        else:
            from app.agent_execution import definition, profile as context_profile
            if execution_context["tenant_id"] != tenant_id or execution_context["agent_id"] != agent_id:
                raise PermissionError("Execution context identity mismatch")
            profile = context_profile(execution_context)
            agent = definition(execution_context)
        grounding_enabled = bool(profile.grounding_policy and profile.grounding_policy.get("enabled") is True)
        if reference_image_attached and (agent_id != "image-agent" or not task_id):
            raise ValueError("参考图片必须绑定图片任务。")
        is_resume = bool(conversation_id)
        if conversation_id:
            existing = self._store.conversation(conversation_id, tenant_id)
            if not existing:
                raise LookupError("会话不存在或不属于当前 Tenant。")
            if existing["agent_id"] != agent_id or existing["runtime_profile_id"] != profile.id:
                raise ValueError("会话与当前 Agent 或 Runtime Profile 不匹配。")
        else:
            conversation_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        baseline = {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "conversation_id": conversation_id,
            "codex_thread_id": None,
            "runtime_version": profile.runtime_version,
            "model_provider": profile.model_provider_id,
            "model": profile.model_id,
            "reasoning_effort": profile.reasoning_effort,
            "skills": [{"name": name, "version": version} for name, version in profile.skill_manifest.items()],
            "skill_name": next(iter(profile.skill_manifest), None),
            "skill_version": next(iter(profile.skill_manifest.values()), None),
            "mcp_calls": [],
            "knowledge_calls": [],
            "knowledge_retrievals": [],
            "asset_calls": [],
            "tool_calls": [],
            "lifecycle_events": [],
            "tool_calls_completed": False,
            "required_tool_calls": {},
            "logical_tool_dependencies": [],
            "required_tool_calls_completed": False,
            "runtime_status": None,
            "runtime_completed": False,
            "final_response_received": False,
            "final_response_persisted": False,
            "final_response_length": 0,
            "artifact_required": agent.allows_image_generation,
            "artifact_completed": False if agent.allows_image_generation else None,
            "assistant_message_saved": False,
            "assistant_message_id": None,
            "result_persistence_status": "pending" if defer_result_persistence else "not_applicable",
            "result_persistence_error_stage": None,
            "partial_output": False,
            "token_usage": {"input_tokens": None, "output_tokens": None},
            "latency_ms": None,
            "estimated_cost": None,
            "estimated_cost_note": "未配置价格表；不对未知价格进行估算。",
            "status": "running",
            "error": None,
            "final_result": None,
            "artifacts": {
                "design_brief": None if grounding_enabled else message,
                "image_prompt": None,
                "reference_assets": [],
                "enterprise_context_used": None,
                "knowledge_context_used": [],
            },
        }
        if execution_context:
            baseline.update(execution_context_id=execution_context["id"], profile_hash_version="v2", configuration_fingerprint=execution_context["configuration_fingerprint"])
        # A startup may fail before Codex returns a thread.  Persist a minimal
        # trace first so the task keeps an auditable run_id without inventing a
        # thread id or storing provider exceptions/secrets.
        self._store.create_run_trace(run_id, conversation_id, tenant_id, agent_id, "pending", baseline)
        if on_run_started:
            on_run_started(run_id, conversation_id)
        trace = baseline
        try:
            if cancellation_requested and cancellation_requested():
                raise GenerationCancelled()
            if is_resume:
                recovery_context = self._recovery_context(conversation_id, tenant_id, message)
                session = await self._runtime.resume_session(
                    profile,
                    existing["runtime_thread_id"],
                    developer_instructions=agent.instructions,
                    recovery_context=recovery_context,
                    **({"task_id": task_id} if reference_image_attached else {}),
                )
                if session.thread_id != existing["runtime_thread_id"]:
                    if not self._store.replace_conversation_thread(
                        conversation_id, tenant_id, existing["runtime_thread_id"], session.thread_id
                    ):
                        raise RuntimeError("会话 Thread 绑定并发更新失败。")
            else:
                session = await self._runtime.create_session(
                    profile, agent.instructions,
                    **({"task_id": task_id} if reference_image_attached else {}),
                )
                if execution_context:
                    with self._store.connection() as conn:
                        conn.execute("INSERT INTO conversations(id,tenant_id,agent_id,runtime_profile_id,runtime_thread_id,runtime_version) VALUES (?,?,?,?,?,?)", (conversation_id, tenant_id, agent_id, profile.id, session.thread_id, profile.runtime_version))
                        conn.execute("INSERT INTO conversation_agent_contexts VALUES (?,?)", (conversation_id, execution_context["id"]))
                else:
                    self._store.save_conversation(
                        conversation_id, tenant_id, agent_id, profile.id, session.thread_id, profile.runtime_version
                    )
            trace = {**baseline, "codex_thread_id": session.thread_id, "lifecycle_events": self._startup_events(profile)}
            self._store.log_event(conversation_id, "turn.started", {"run_id": run_id, "profile_id": profile.id, "thread_id": session.thread_id})
            runtime_message = (message + "\n\n本轮消息显式附有一张参考图片。请按用户文字要求调用 image_generation；平台会安全读取本任务绑定的参考图并执行图片编辑。不要沿用历史图片。") if reference_image_attached else message
            turn = await self._run_turn(
                session, runtime_message, on_visible_delta, cancellation_requested,
                on_execution_activity,
                grounding_enabled=grounding_enabled,
            )
            trace = self._completed_trace(trace, turn, agent.allows_image_generation)
            if execution_context:
                for tool in profile.required_tools:
                    trace["required_tool_calls"].setdefault(tool, {"attempts": 0, "completed_attempts": 0, "failed_attempts": 0, "satisfied": False})
                trace["required_tool_calls_completed"] = all(item["satisfied"] for item in trace["required_tool_calls"].values())
            trace["lifecycle_events"] = self._startup_events(profile) + trace["lifecycle_events"]
            has_final_response = bool(turn.text.strip())
            runtime_completed = trace["runtime_completed"]
            if not runtime_completed:
                raise AgentRunError(
                    self._safe_error(turn.error) if turn.error else f"Codex Turn 终态为 {turn.status}。",
                    run_id=run_id,
                    conversation_id=conversation_id,
                    error_code="runtime_terminal_error",
                    failure_stage="runtime_terminal",
                )
            if (not trace["required_tool_calls_completed"]
                    and not (turn.grounding_telemetry and not turn.grounding_telemetry["final_pass"])):
                missing = ", ".join(name for name, item in trace["required_tool_calls"].items() if not item["satisfied"])
                raise AgentRunError(
                    f"必需工具依赖未完成：{missing}。",
                    run_id=run_id,
                    conversation_id=conversation_id,
                    error_code="required_tool_dependency_error",
                    failure_stage="required_tools",
                )
            if not has_final_response:
                raise AgentRunError(
                    "Codex Turn 已结束，但未返回非空最终正文。",
                    run_id=run_id,
                    conversation_id=conversation_id,
                    error_code="empty_final_response",
                    failure_stage="final_response",
                )
            status = "runtime_completed" if defer_result_persistence else "completed"
            trace["status"] = status
            self._store.log_event(conversation_id, "turn.completed", {"run_id": run_id, "token_usage": trace["token_usage"], "latency_ms": turn.latency_ms})
            self._store.finish_run_trace(run_id, status, trace, turn.thread_id)
            return RunResult(run_id=run_id, conversation_id=conversation_id, thread_id=turn.thread_id, text=turn.text)
        except GenerationCancelled:
            trace.update(
                status="cancelled",
                partial_output=bool(trace.get("final_response_received")),
                final_response_persisted=False,
                assistant_message_saved=False,
                result_persistence_status="not_applicable",
                failure_stage="cancelled",
                error=None,
            )
            self._store.finish_run_trace(run_id, "cancelled", trace, trace.get("codex_thread_id"))
            if trace.get("codex_thread_id"):
                self._store.log_event(conversation_id, "turn.cancelled", {"run_id": run_id})
            raise
        except Exception as exc:
            trace["status"] = "failed"
            trace["partial_output"] = bool(trace.get("final_response_received"))
            trace["failure_stage"] = getattr(exc, "failure_stage", "runtime_start" if isinstance(exc, RuntimeStartError) else "runtime")
            startup_events = self._startup_events(profile)
            lifecycle_events = list(trace.get("lifecycle_events", []))
            if lifecycle_events[: len(startup_events)] != startup_events:
                lifecycle_events = startup_events + lifecycle_events
            trace["lifecycle_events"] = lifecycle_events
            trace["error"] = (
                "Grounded Runtime 未能安全完成。" if grounding_enabled else
                "Codex Runtime 未能启动；请查看安全运行时 Trace。" if isinstance(exc, RuntimeStartError) else
                self._safe_error(str(exc))
            )
            thread_id = trace.get("codex_thread_id") or "pending"
            self._store.finish_run_trace(run_id, "failed", trace, thread_id)
            if trace.get("codex_thread_id"):
                self._store.log_event(conversation_id, "turn.failed", {"run_id": run_id, "error": trace["error"]})
            if isinstance(exc, AgentRunError):
                raise
            raise AgentRunError(
                trace["error"],
                run_id=run_id,
                conversation_id=conversation_id,
                error_code="runtime_start_error" if isinstance(exc, RuntimeStartError) else "runtime_error",
                failure_stage=trace["failure_stage"],
            ) from exc

    async def _run_turn(
        self,
        session: RuntimeSession,
        message: str,
        on_visible_delta: Callable[[str], Awaitable[None]] | None,
        cancellation_requested: Callable[[], bool] | None = None,
        on_execution_activity: Callable[[str, str], None] | None = None,
        *, grounding_enabled: bool = False,
    ):
        stream_turn = getattr(self._runtime, "stream_turn", None)
        if not callable(stream_turn):
            return await self._runtime.run_turn(session, message)

        async def consume():
            completed = None
            generating_started = False
            explicit_full_plan_generation = False
            async for event in stream_turn(session, message):
                if event.kind == "delta":
                    if grounding_enabled and cancellation_requested and cancellation_requested():
                        raise GenerationCancelled()
                    if not generating_started and not explicit_full_plan_generation and on_execution_activity:
                        on_execution_activity("generating", "started")
                        generating_started = True
                    if on_visible_delta and event.text:
                        await on_visible_delta(event.text)
                elif event.kind == "activity" and on_execution_activity and event.text:
                    stage, separator, status = event.text.partition(":")
                    if separator:
                        if stage == "full_plan_generating" and status == "started":
                            explicit_full_plan_generation = True
                        on_execution_activity(stage, status)
                elif event.kind == "completed":
                    completed = event.turn
            if completed is None:
                raise RuntimeError("Runtime stream ended without a final turn.")
            if generating_started and on_execution_activity:
                on_execution_activity("generating", "completed")
            if grounding_enabled and cancellation_requested and cancellation_requested():
                raise GenerationCancelled()
            return completed

        consumer = asyncio.create_task(consume())
        try:
            while not consumer.done():
                if cancellation_requested and cancellation_requested():
                    cancel_turn = getattr(self._runtime, "cancel_turn", None)
                    if callable(cancel_turn):
                        try:
                            await cancel_turn(session)
                        except Exception:
                            # Local cancellation and the durable task state still
                            # prevent a false successful result if the provider
                            # rejects or races the best-effort interrupt request.
                            pass
                    consumer.cancel()
                    await asyncio.gather(consumer, return_exceptions=True)
                    raise GenerationCancelled()
                await asyncio.wait({consumer}, timeout=0.1)
            return await consumer
        finally:
            if not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)

    def _startup_events(self, profile: RuntimeProfile) -> list[dict]:
        getter = getattr(self._runtime, "startup_events", None)
        if not callable(getter):
            return []
        return [dict(event) for event in getter(profile)]

    def _recovery_context(self, conversation_id: str, tenant_id: str, current_message: str) -> str:
        messages = self._store.conversation_messages(conversation_id, tenant_id)
        if messages and messages[-1]["role"] == "user" and messages[-1]["content"] == current_message:
            messages = messages[:-1]
        lines = [f"{item['role']}: {item['content']}" for item in messages]
        return "\n".join(lines)[-12000:]

    @staticmethod
    def _completed_trace(trace: dict, turn, requires_image_generation: bool = False) -> dict:
        trace = {**trace}
        calls = list(turn.mcp_calls)
        if turn.grounding_telemetry:
            # Do not persist private source material or tool argument summaries.
            # These fields suffice for dependency checks and artifact ownership.
            safe_keys = {"server", "tool", "status", "duration_ms", "dependency_id",
                         "result_is_error", "artifact", "retrieval_observation"} | AUDIT_FIELDS
            calls = [{key: value for key, value in call.items() if key in safe_keys} for call in calls]
        calls, logical_dependencies, required_status = resolve_dependencies(calls)
        trace["mcp_calls"] = calls
        trace["tool_calls"] = [{"server": call["server"], "tool": call["tool"], "status": call["status"]} for call in calls]
        trace["lifecycle_events"] = list(turn.lifecycle_events)
        trace["tool_calls_completed"] = bool(calls) and all(call.get("status", "").lower() == "completed" for call in calls)
        trace["logical_tool_dependencies"] = logical_dependencies
        trace["required_tool_calls"] = required_status
        trace["required_tool_calls_completed"] = all(item["satisfied"] for item in required_status.values())
        trace["required_tool_dependency_source"] = "observed_mcp_requests_with_explicit_input_retry_lineage"
        trace["artifact_required"] = requires_image_generation
        trace["runtime_status"] = str(getattr(turn.status, "value", turn.status) or "unknown").rsplit(".", 1)[-1].lower()
        trace["runtime_completed"] = trace["runtime_status"] in {"completed", "success"} and not turn.error
        trace["final_response_received"] = bool(turn.text.strip())
        trace["final_response_length"] = len(turn.text.strip())
        trace["knowledge_calls"] = [call for call in calls if call["tool"] == "knowledge_search"]
        trace["knowledge_retrievals"] = [call["retrieval_observation"] for call in trace["knowledge_calls"] if call.get("retrieval_observation")]
        trace["asset_calls"] = [call for call in calls if call["tool"] == "asset_search"]
        trace["token_usage"] = {"input_tokens": turn.input_tokens, "output_tokens": turn.output_tokens}
        trace["latency_ms"] = turn.latency_ms
        trace["error"] = AgentService._safe_error(turn.error) if turn.error else None
        trace["final_result"] = turn.text
        trace["structured_result"] = turn.structured_result
        trace["structured_result_diagnostic"] = turn.structured_diagnostic
        trace["structured_attempt_trace"] = list(turn.structured_attempt_trace)
        trace["structured_result_status"] = turn.structured_result_status
        trace["total_model_calls_for_structured_result"] = turn.structured_model_calls
        if turn.grounding_telemetry:
            # Bounded metadata only; no article, ledger, evidence, or audit text.
            trace["grounding"] = dict(turn.grounding_telemetry)
        artifacts = dict(trace["artifacts"])
        for call in calls:
            if call["tool"] == "enterprise_config_get":
                artifacts["enterprise_context_used"] = call.get("output_summary")
            elif call["tool"] == "knowledge_search":
                if call.get("output_summary"):
                    artifacts["knowledge_context_used"].append(call["output_summary"])
            elif call["tool"] == "asset_search":
                if call.get("output_summary"):
                    artifacts["reference_assets"].append(call["output_summary"])
            elif call["tool"] == "image_generation":
                artifacts["image_prompt"] = call.get("input_summary")
        trace["artifacts"] = artifacts
        return trace

    @staticmethod
    def _call_completed(call: dict) -> bool:
        return call_completed(call)

    def mark_persistence_failed(self, run_id: str, tenant_id: str, stage: str) -> None:
        trace = self._store.run_trace(run_id, tenant_id)
        if not trace or trace["status"] == "completed":
            return
        payload = trace["payload"]
        payload.update(
            {
                "status": "failed",
                "partial_output": bool(payload.get("final_response_received")),
                "assistant_message_saved": False,
                "final_response_persisted": False,
                "result_persistence_status": "failed",
                "result_persistence_error_stage": stage,
                "failure_stage": stage,
                "error": "运行结果持久化失败；未将部分输出标记为成功。",
            }
        )
        self._store.finish_run_trace(run_id, "failed", payload, trace.get("codex_thread_id"))
        self._store.log_event(trace["conversation_id"], "result.persistence_failed", {"run_id": run_id, "stage": stage})

    def mark_cancelled(self, run_id: str | None, tenant_id: str) -> None:
        if not run_id:
            return
        trace = self._store.run_trace(run_id, tenant_id)
        if not trace or trace["status"] == "completed":
            return
        payload = trace["payload"]
        payload.update(
            {
                "status": "cancelled",
                "partial_output": bool(payload.get("final_response_received")),
                "assistant_message_saved": False,
                "final_response_persisted": False,
                "result_persistence_status": "not_applicable",
                "failure_stage": "cancelled",
                "error": None,
            }
        )
        self._store.finish_run_trace(run_id, "cancelled", payload, trace.get("codex_thread_id"))
        self._store.log_event(trace["conversation_id"], "turn.cancelled", {"run_id": run_id})

    @staticmethod
    def _safe_error(error: str | None) -> str:
        if not error:
            return "Codex Turn 未完成。"
        if "Incorrect API key" in error or "401 Unauthorized" in error:
            return "OpenAI API Key 被拒绝（401）；请配置可用于 Responses API 的有效密钥。"
        return error[:600]
