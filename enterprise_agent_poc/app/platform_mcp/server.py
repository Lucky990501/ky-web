from __future__ import annotations

"""Streamable HTTP Platform MCP adapter.

The business service is separately testable. This adapter expects FastMCP's
request context to expose HTTP request headers; production should additionally
place this endpoint behind TLS and a service-only network policy.
"""

import os
import asyncio
import hashlib
import json
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations, CallToolResult, TextContent

from app.platform_mcp.service import PlatformMCPService
from app.security import RuntimeTokenIssuer
from app.settings import settings
from app.store import POCStore
from app.tool_dependencies import (
    InvalidRetryLineage,
    ToolInputValidationError,
    input_failure,
    invalid_retry_failure,
)


store = POCStore(settings.database_url)
tokens = RuntimeTokenIssuer(settings.token_secret)
service = PlatformMCPService(store, tokens, settings)


def _bearer_from_context(ctx: Any) -> str:
    try:
        request_context = getattr(ctx, "request_context", None)
    except (LookupError, RuntimeError):
        request_context = None
    request = getattr(request_context, "request", None)
    headers = getattr(request, "headers", {})
    header = headers.get("authorization", "") if headers else ""
    if not header.startswith("Bearer "):
        raise PermissionError("Platform MCP requires a Runtime bearer token.")
    return header.removeprefix("Bearer ")


def _execution_scope_from_context(ctx: Any, bearer_token: str) -> str:
    """Bind retry capabilities to the authenticated MCP transport session.

    Runtime installs a fresh X-Runtime-Execution-Scope for each product Task.
    Mcp-Session-Id is the compatibility fallback for older HTTP clients. The
    deterministic bearer fallback keeps in-process adapters functional while
    principal binding, receipt TTL, and one-use consumption remain enforced.
    """
    session_id = None
    try:
        request_context = getattr(ctx, "request_context", None)
        request = getattr(request_context, "request", None)
        headers = getattr(request, "headers", {})
        session_id = headers.get("x-runtime-execution-scope") if headers else None
        if session_id:
            return "runtime:" + str(session_id)
        session_id = headers.get("mcp-session-id") if headers else None
        if not session_id:
            session_id = getattr(request_context, "session_id", None)
    except Exception:
        # FastMCP's in-process test client constructs Context without binding
        # the request contextvar. Production HTTP calls take the branch above.
        session_id = None
    if session_id:
        return "mcp:" + str(session_id)
    return "legacy:" + hashlib.sha256(bearer_token.encode("utf-8")).hexdigest()


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
    async def image_generation(prompt: str, references: list[str], aspect_ratio: str,
                               ctx: Context = None, retry_of: str | None = None) -> dict[str, Any]:
        """Generate an image via the platform Tool Gateway.

        For the SAME requirement's input correction, copy retry_of from the
        validation error. For a new independent image, omit retry_of.
        """
        arguments = {"prompt": prompt, "references": references, "aspect_ratio": aspect_ratio}
        bearer = _bearer_from_context(ctx)
        execution_scope = _execution_scope_from_context(ctx, bearer)
        try:
            return await service.image_generation(
                bearer, prompt, references, aspect_ratio,
                retry_of=retry_of, execution_scope=execution_scope,
            )
        except ToolInputValidationError as error:
            # The generic contract is emitted only for typed, pre-side-effect
            # input failures. Provider/auth/storage errors receive no receipt.
            if error.retry_receipt is None:
                raise RuntimeError("Validated server retry receipt was not issued.") from error
            failure = input_failure(arguments, error, error.retry_receipt)
            return CallToolResult(isError=True, structuredContent=failure,
                                  content=[TextContent(type="text", text=json.dumps(failure, ensure_ascii=False))])
        except InvalidRetryLineage as error:
            failure = invalid_retry_failure(error)
            return CallToolResult(isError=True, structuredContent=failure,
                                  content=[TextContent(type="text", text=json.dumps(failure, ensure_ascii=False))])

    return mcp


if __name__ == "__main__":  # pragma: no cover
    store.initialize()
    create_mcp().run(transport="streamable-http")
