from __future__ import annotations

"""Streamable HTTP Platform MCP adapter.

The business service is separately testable. This adapter expects FastMCP's
request context to expose HTTP request headers; production should additionally
place this endpoint behind TLS and a service-only network policy.
"""

import os
import asyncio
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from app.platform_mcp.service import PlatformMCPService
from app.security import RuntimeTokenIssuer
from app.settings import settings
from app.store import POCStore


store = POCStore(settings.database_url)
tokens = RuntimeTokenIssuer(settings.token_secret)
service = PlatformMCPService(store, tokens, settings)


def _bearer_from_context(ctx: Any) -> str:
    request_context = getattr(ctx, "request_context", None)
    request = getattr(request_context, "request", None)
    headers = getattr(request, "headers", {})
    header = headers.get("authorization", "") if headers else ""
    if not header.startswith("Bearer "):
        raise PermissionError("Platform MCP requires a Runtime bearer token.")
    return header.removeprefix("Bearer ")


def create_mcp():
    mcp = FastMCP(
        "Enterprise Platform MCP",
        host=os.environ.get("ENTERPRISE_POC_MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("ENTERPRISE_POC_MCP_PORT", "8091")),
        streamable_http_path="/mcp",
    )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def enterprise_config_get(ctx: Context) -> dict:
        """Read the authenticated runtime's current enterprise configuration."""
        return service.enterprise_config_get(_bearer_from_context(ctx))

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def knowledge_search(query: str, limit: int = 5, ctx: Context = None) -> list[dict]:
        """Search knowledge belonging only to the authenticated enterprise."""
        # Embedding lookup performs a synchronous HTTPS request.  Keeping it off
        # FastMCP's event loop lets the streamable HTTP transport continue to
        # send tool responses instead of being disconnected mid-call.
        return await asyncio.to_thread(service.knowledge_search, _bearer_from_context(ctx), query, limit)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def asset_search(query: str, asset_type: str | None = None, ctx: Context = None) -> list[dict]:
        """Find visual assets belonging only to the authenticated enterprise."""
        return service.asset_search(_bearer_from_context(ctx), query, asset_type)

    @mcp.tool()
    async def image_generation(prompt: str, references: list[str], aspect_ratio: str, ctx: Context = None) -> dict:
        """Generate an image via the platform Tool Gateway."""
        return await service.image_generation(_bearer_from_context(ctx), prompt, references, aspect_ratio)

    return mcp


if __name__ == "__main__":  # pragma: no cover
    store.initialize()
    create_mcp().run(transport="streamable-http")
