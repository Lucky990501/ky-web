"""Execute one synthetic Tenant A poster-design Gate 2 turn."""

from __future__ import annotations

import asyncio
import json
import sys

from app.main import agents, store


async def main() -> None:
    tenant_id = sys.argv[1] if len(sys.argv) > 1 else "tenant-a"
    if tenant_id not in {"tenant-a", "tenant-b"}:
        raise ValueError("tenant must be tenant-a or tenant-b")
    store.seed_demo_data()
    result = await agents.run(tenant_id, "image-agent", "帮我做一张秋季招生海报")
    trace = store.run_trace(result.run_id, tenant_id)
    assert trace is not None
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "tenant_id": tenant_id,
                "thread_id": result.thread_id,
                "reply_present": bool(result.text),
                "status": trace["status"],
                "mcp_tools": [call["tool"] for call in trace["payload"]["mcp_calls"]],
                "mcp_statuses": [(call["tool"], call["status"], call["error"]) for call in trace["payload"]["mcp_calls"]],
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
