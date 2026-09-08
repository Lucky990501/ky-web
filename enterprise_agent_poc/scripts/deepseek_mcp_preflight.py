"""Run the explicit, synthetic-data-only DeepSeek + Platform MCP preflight."""

from __future__ import annotations

import asyncio

from openai_codex import Sandbox
from openai_codex.api import AsyncThread
from openai_codex.generated.v2_all import AskForApproval, AskForApprovalValue, SandboxMode, ThreadStartParams

from app.main import agents, manager, store
from app.service import IMAGE_AGENT_INSTRUCTIONS


async def main() -> None:
    store.seed_demo_data()
    profile = agents.profile_for("tenant-a", "image-agent")
    codex = await manager.get(profile)
    try:
        # "untrusted" asks only for untrusted execution rather than treating
        # every read-only MCP invocation as a user-denied request.
        started = await codex._client.thread_start(
            ThreadStartParams(
                approvalPolicy=AskForApproval(root=AskForApprovalValue.untrusted),
                cwd=str(manager._paths(profile)[1]),
                developerInstructions=IMAGE_AGENT_INSTRUCTIONS,
                model=profile.model_id,
                modelProvider=profile.model_provider_id,
                config={"model_reasoning_effort": profile.reasoning_effort},
                sandbox=SandboxMode.read_only,
            )
        )
        thread = AsyncThread(codex, started.thread.id)
        result = await thread.run(
            "Synthetic test data only. Call the Platform MCP tool enterprise_config_get. "
            "Then reply only with the brand_name.",
            sandbox=Sandbox.read_only,
        )
        print(
            {
                "status": getattr(result.status, "value", str(result.status)),
                "final_response_present": bool(result.final_response),
                "items": [
                    {
                        "type": type(item).__name__,
                        "server": getattr(item, "server", None),
                        "tool": getattr(item, "tool", None),
                        "name": getattr(item, "name", None),
                        "status": str(getattr(item, "status", None)),
                    }
                    for item in result.items
                ],
            }
        )
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
