from __future__ import annotations

import re

from app.agent_catalog import get_agent
from app.product_store import ProductStore, ResultPersistenceError
from app.service import AgentRunError, AgentService


class TaskService:
    """Async product orchestration around the Gate 2 AgentService."""

    def __init__(self, store: ProductStore, agents: AgentService) -> None:
        self._store = store
        self._agents = agents

    async def execute(self, task: dict) -> None:
        task_id, tenant_id = task["id"], task["tenant_id"]
        agent = get_agent(task["agent_id"])
        current = self._store.task_for_worker(task_id) or task
        if current.get("status") == "completed":
            return
        if current.get("run_id"):
            prior_trace = self._agents._store.run_trace(current["run_id"], tenant_id)
            if self._persistence_recoverable(prior_trace):
                self._store.set_task(task_id, tenant_id, "running", "persisting_result", "正在恢复已完成运行的结果")
                try:
                    self._persist_result(current, prior_trace)
                except ResultPersistenceError as exc:
                    self._agents.mark_persistence_failed(current["run_id"], tenant_id, exc.stage)
                    self._store.set_task(
                        task_id,
                        tenant_id,
                        "failed",
                        "result_persistence_failed",
                        "任务正文已生成，但结果保存失败；可安全重试保存。",
                        error_code="result_persistence_error",
                        run_id=current["run_id"],
                        conversation_id=prior_trace["conversation_id"],
                    )
                return

        self._store.set_task(task_id, tenant_id, "running", "loading_context", "正在加载企业上下文")
        try:
            if task.get("conversation_id"):
                self._store.add_message(
                    task["conversation_id"],
                    "user",
                    task["input_text"],
                    message_id=f"task:{task_id}:user",
                )
            self._store.set_task(task_id, tenant_id, "running", "starting_runtime", f"正在启动{agent.name}")
            result = await self._agents.run(
                tenant_id,
                task["agent_id"],
                task["input_text"],
                task["conversation_id"],
                defer_result_persistence=True,
            )
            trace = self._agents._store.run_trace(result.run_id, tenant_id)
            if not trace:
                raise ResultPersistenceError("trace_lookup")
            self._store.set_task(
                task_id,
                tenant_id,
                "running",
                "persisting_result",
                "正在保存最终正文和关联结果",
                run_id=result.run_id,
                conversation_id=result.conversation_id,
            )
            self._persist_result({**task, "run_id": result.run_id}, trace)
        except Exception as exc:
            if isinstance(exc, ResultPersistenceError):
                code, message = "result_persistence_error", "任务正文已生成，但结果保存失败；可安全重试保存。"
                run_id = (self._store.task_for_worker(task_id) or {}).get("run_id")
                if run_id:
                    self._agents.mark_persistence_failed(run_id, tenant_id, exc.stage)
            elif isinstance(exc, AgentRunError):
                code = exc.error_code
                if code == "required_tool_dependency_error":
                    message = "必需企业工具在有限尝试后仍未完成，任务未被标记为成功。"
                elif code == "empty_final_response":
                    message = "运行已结束，但没有收到可保存的最终正文。"
                elif code == "runtime_terminal_error":
                    message = "运行未正常完成，部分输出不会作为成功结果保存。"
                else:
                    message = "任务运行失败，请稍后重试。"
            else:
                error = str(exc).lower()
                if "image_generation" in error or "image provider" in error or "图片生成" in error:
                    code, message = "image_provider_error", "图片生成服务暂时不可用，请稍后重试。"
                elif "mcp" in error:
                    code, message = "mcp_error", "企业上下文服务暂时不可用，请稍后重试。"
                else:
                    code, message = "runtime_error", "任务执行失败，请稍后重试。"
            self._store.set_task(
                task_id,
                tenant_id,
                "failed",
                "result_persistence_failed" if code == "result_persistence_error" else "failed",
                message,
                error_code=code,
                run_id=getattr(exc, "run_id", None),
                conversation_id=getattr(exc, "conversation_id", None),
            )

    @staticmethod
    def _persistence_recoverable(trace: dict | None) -> bool:
        if not trace:
            return False
        payload = trace["payload"]
        return bool(
            str(payload.get("runtime_status") or "").lower() in {"completed", "success"}
            and payload.get("required_tool_calls_completed")
            and payload.get("final_response_received")
            and str(payload.get("final_result") or "").strip()
            and payload.get("result_persistence_status") == "failed"
        )

    @staticmethod
    def _image_storage_key(trace: dict) -> str | None:
        completed_calls = [
            call
            for call in trace["payload"].get("mcp_calls", [])
            if call.get("tool") == "image_generation" and call.get("status", "").lower() == "completed"
        ]
        for call in completed_calls:
            artifact = call.get("artifact")
            storage_key = artifact.get("storage_key") if isinstance(artifact, dict) else None
            if isinstance(storage_key, str) and storage_key.strip():
                return storage_key.strip()
        # Existing traces only contain a redacted output summary. Keep this
        # parser as a recovery fallback while all new runs use artifact above.
        output = " ".join(call.get("output_summary") or "" for call in completed_calls)
        match = re.search(r"storage_key['\"]?\s*[:=]\s*['\"]([^'\"]+)", output)
        return match.group(1) if match else None

    def _persist_result(self, task: dict, trace: dict) -> dict:
        payload = trace["payload"]
        return self._store.complete_task_success(
            task,
            run_id=trace["run_id"],
            conversation_id=trace["conversation_id"],
            response=str(payload["final_result"]),
            trace_payload=payload,
            image_storage_key=self._image_storage_key(trace),
        )
