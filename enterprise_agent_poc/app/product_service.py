from __future__ import annotations

from app.product_store import ProductStore
from app.service import AgentService, AgentRunError
from app.agent_catalog import get_agent
import re


class TaskService:
    """Async product orchestration around the unchanged Gate 2 AgentService."""

    def __init__(self, store: ProductStore, agents: AgentService) -> None:
        self._store = store
        self._agents = agents

    async def execute(self, task: dict) -> None:
        task_id, tenant_id = task["id"], task["tenant_id"]
        agent = get_agent(task["agent_id"])
        self._store.set_task(task_id, tenant_id, "running", "loading_context", "正在加载企业上下文")
        try:
            if task.get("conversation_id"):
                self._store.add_message(task["conversation_id"], "user", task["input_text"])
            self._store.set_task(task_id, tenant_id, "running", "starting_runtime", f"正在启动{agent.name}")
            result = await self._agents.run(tenant_id, task["agent_id"], task["input_text"], task["conversation_id"])
            self._store.attach_conversation(result.conversation_id, task["user_id"])
            if not task.get("conversation_id"):
                self._store.add_message(result.conversation_id, "user", task["input_text"])
            self._store.add_message(result.conversation_id, "assistant", result.text)
            trace = self._agents._store.run_trace(result.run_id, tenant_id)
            output = " ".join(call.get("output_summary") or "" for call in trace["payload"]["mcp_calls"] if call["tool"] == "image_generation") if trace else ""
            match = re.search(r"storage_key['\"]?\s*[:=]\s*['\"]([^'\"]+)", output)
            if agent.allows_image_generation and match:
                self._store.create_generation(tenant_id, task["user_id"], result.conversation_id, task_id, match.group(1), "image-gateway", "gateway-managed-gpt-image-2")
            self._store.charge_success(tenant_id, task["user_id"], task_id)
            self._store.set_task(task_id, tenant_id, "completed", "completed", "任务已完成，正文已返回", run_id=result.run_id, response=result.text, conversation_id=result.conversation_id)
        except Exception as exc:
            error = str(exc).lower()
            if "image_generation" in error or "image provider" in error or "图片生成" in error:
                code, message = "image_provider_error", "图片生成服务暂时不可用，请稍后重试。"
            elif "mcp" in error:
                code, message = "mcp_error", "企业上下文服务暂时不可用，请稍后重试。"
            else:
                code, message = "runtime_error", "任务执行失败，请稍后重试。"
            self._store.set_task(task_id, tenant_id, "failed", "failed", message, error_code=code, run_id=getattr(exc, "run_id", None), conversation_id=getattr(exc, "conversation_id", None))
