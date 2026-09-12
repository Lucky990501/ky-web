from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

from app.domain import RuntimeProfile, RuntimeSession
from app.runtime.base import RuntimeProvider, RuntimeStartError
from app.settings import Settings
from app.store import POCStore
from app.agent_catalog import get_agent


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
    ) -> RunResult:
        profile = self.profile_for(tenant_id, agent_id)
        agent = get_agent(agent_id)
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
            "skill_name": next(iter(profile.skill_manifest)),
            "skill_version": next(iter(profile.skill_manifest.values())),
            "mcp_calls": [],
            "knowledge_calls": [],
            "knowledge_retrievals": [],
            "asset_calls": [],
            "tool_calls": [],
            "lifecycle_events": [],
            "tool_calls_completed": False,
            "required_tool_calls": {},
            "required_tool_calls_completed": False,
            "runtime_status": None,
            "final_response_received": False,
            "final_response_length": 0,
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
                "design_brief": message,
                "image_prompt": None,
                "reference_assets": [],
                "enterprise_context_used": None,
                "knowledge_context_used": [],
            },
        }
        # A startup may fail before Codex returns a thread.  Persist a minimal
        # trace first so the task keeps an auditable run_id without inventing a
        # thread id or storing provider exceptions/secrets.
        self._store.create_run_trace(run_id, conversation_id, tenant_id, agent_id, "pending", baseline)
        trace = baseline
        try:
            if is_resume:
                recovery_context = self._recovery_context(conversation_id, tenant_id, message)
                session = await self._runtime.resume_session(
                    profile,
                    existing["runtime_thread_id"],
                    developer_instructions=agent.instructions,
                    recovery_context=recovery_context,
                )
                if session.thread_id != existing["runtime_thread_id"]:
                    if not self._store.replace_conversation_thread(
                        conversation_id, tenant_id, existing["runtime_thread_id"], session.thread_id
                    ):
                        raise RuntimeError("会话 Thread 绑定并发更新失败。")
            else:
                session = await self._runtime.create_session(profile, agent.instructions)
                self._store.save_conversation(
                    conversation_id, tenant_id, agent_id, profile.id, session.thread_id, profile.runtime_version
                )
            trace = {**baseline, "codex_thread_id": session.thread_id, "lifecycle_events": self._startup_events(profile)}
            self._store.log_event(conversation_id, "turn.started", {"run_id": run_id, "profile_id": profile.id, "thread_id": session.thread_id})
            turn = await self._runtime.run_turn(session, message)
            trace = self._completed_trace(trace, turn, agent.allows_image_generation)
            trace["lifecycle_events"] = self._startup_events(profile) + trace["lifecycle_events"]
            has_final_response = bool(turn.text.strip())
            runtime_completed = turn.status.lower() in {"completed", "success"} and not turn.error
            if not runtime_completed:
                raise AgentRunError(
                    self._safe_error(turn.error) if turn.error else f"Codex Turn 终态为 {turn.status}。",
                    run_id=run_id,
                    conversation_id=conversation_id,
                    error_code="runtime_terminal_error",
                    failure_stage="runtime_terminal",
                )
            if not trace["required_tool_calls_completed"]:
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
        except Exception as exc:
            trace["status"] = "failed"
            trace["partial_output"] = bool(trace.get("final_response_received"))
            trace["failure_stage"] = getattr(exc, "failure_stage", "runtime_start" if isinstance(exc, RuntimeStartError) else "runtime")
            startup_events = self._startup_events(profile)
            lifecycle_events = list(trace.get("lifecycle_events", []))
            if lifecycle_events[: len(startup_events)] != startup_events:
                lifecycle_events = startup_events + lifecycle_events
            trace["lifecycle_events"] = lifecycle_events
            trace["error"] = "Codex Runtime 未能启动；请查看安全运行时 Trace。" if isinstance(exc, RuntimeStartError) else self._safe_error(str(exc))
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
        trace["mcp_calls"] = calls
        trace["tool_calls"] = [{"server": call["server"], "tool": call["tool"], "status": call["status"]} for call in calls]
        trace["lifecycle_events"] = list(turn.lifecycle_events)
        trace["tool_calls_completed"] = bool(calls) and all(call.get("status", "").lower() == "completed" for call in calls)
        required = ["enterprise_config_get", "knowledge_search", "asset_search"]
        if requires_image_generation:
            required.append("image_generation")
        required_status = {}
        for tool in required:
            attempts = [call for call in calls if call.get("tool") == tool]
            completed_attempts = sum(call.get("status", "").lower() == "completed" for call in attempts)
            required_status[tool] = {
                "attempts": len(attempts),
                "completed_attempts": completed_attempts,
                "failed_attempts": sum(call.get("status", "").lower().endswith("failed") for call in attempts),
                "satisfied": completed_attempts > 0,
            }
        trace["required_tool_calls"] = required_status
        trace["required_tool_calls_completed"] = all(item["satisfied"] for item in required_status.values())
        trace["runtime_status"] = turn.status
        trace["final_response_received"] = bool(turn.text.strip())
        trace["final_response_length"] = len(turn.text.strip())
        trace["knowledge_calls"] = [call for call in calls if call["tool"] == "knowledge_search"]
        trace["knowledge_retrievals"] = [call["retrieval_observation"] for call in trace["knowledge_calls"] if call.get("retrieval_observation")]
        trace["asset_calls"] = [call for call in calls if call["tool"] == "asset_search"]
        trace["token_usage"] = {"input_tokens": turn.input_tokens, "output_tokens": turn.output_tokens}
        trace["latency_ms"] = turn.latency_ms
        trace["error"] = AgentService._safe_error(turn.error) if turn.error else None
        trace["final_result"] = turn.text
        artifacts = dict(trace["artifacts"])
        for call in calls:
            if call["tool"] == "enterprise_config_get":
                artifacts["enterprise_context_used"] = call["output_summary"]
            elif call["tool"] == "knowledge_search":
                artifacts["knowledge_context_used"].append(call["output_summary"])
            elif call["tool"] == "asset_search":
                artifacts["reference_assets"].append(call["output_summary"])
            elif call["tool"] == "image_generation":
                artifacts["image_prompt"] = call["input_summary"]
        trace["artifacts"] = artifacts
        return trace

    def mark_persistence_failed(self, run_id: str, tenant_id: str, stage: str) -> None:
        trace = self._store.run_trace(run_id, tenant_id)
        if not trace:
            return
        payload = trace["payload"]
        payload.update(
            {
                "status": "failed",
                "partial_output": bool(payload.get("final_response_received")),
                "assistant_message_saved": False,
                "result_persistence_status": "failed",
                "result_persistence_error_stage": stage,
                "failure_stage": stage,
                "error": "运行结果持久化失败；未将部分输出标记为成功。",
            }
        )
        self._store.finish_run_trace(run_id, "failed", payload, trace.get("codex_thread_id"))
        self._store.log_event(trace["conversation_id"], "result.persistence_failed", {"run_id": run_id, "stage": stage})

    @staticmethod
    def _safe_error(error: str | None) -> str:
        if not error:
            return "Codex Turn 未完成。"
        if "Incorrect API key" in error or "401 Unauthorized" in error:
            return "OpenAI API Key 被拒绝（401）；请配置可用于 Responses API 的有效密钥。"
        return error[:600]
