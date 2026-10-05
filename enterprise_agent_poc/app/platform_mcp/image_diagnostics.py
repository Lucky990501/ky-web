"""Allowlisted image transport diagnostics; never serialize exception messages."""
from __future__ import annotations

from collections import deque
from contextlib import asynccontextmanager
import json
import logging
import socket
import ssl

import httpcore
import httpx

logger = logging.getLogger(__name__)


class ImageProviderUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("image_provider_unavailable")


# Class labels are constants, not attacker-controlled class names/str/repr/args.
_TYPES = (
    (socket.gaierror, "socket.gaierror", "dns_error"),
    (ssl.SSLCertVerificationError, "ssl.SSLCertVerificationError", "tls_error"),
    (ssl.SSLError, "ssl.SSLError", "tls_error"),
    (httpx.ConnectTimeout, "httpx.ConnectTimeout", "connect_timeout"),
    (httpcore.ConnectTimeout, "httpcore.ConnectTimeout", "connect_timeout"),
    (httpx.ReadTimeout, "httpx.ReadTimeout", "read_timeout"),
    (httpcore.ReadTimeout, "httpcore.ReadTimeout", "read_timeout"),
    (httpx.WriteTimeout, "httpx.WriteTimeout", "write_timeout"),
    (httpcore.WriteTimeout, "httpcore.WriteTimeout", "write_timeout"),
    (httpx.PoolTimeout, "httpx.PoolTimeout", "pool_timeout"),
    (httpcore.PoolTimeout, "httpcore.PoolTimeout", "pool_timeout"),
    (httpx.ConnectError, "httpx.ConnectError", "connection_error"),
    (httpcore.ConnectError, "httpcore.ConnectError", "connection_error"),
    (httpx.ReadError, "httpx.ReadError", "read_error"),
    (httpcore.ReadError, "httpcore.ReadError", "read_error"),
    (httpx.WriteError, "httpx.WriteError", "write_error"),
    (httpcore.WriteError, "httpcore.WriteError", "write_error"),
    (httpx.ProxyError, "httpx.ProxyError", "proxy_error"),
    (httpcore.ProxyError, "httpcore.ProxyError", "proxy_error"),
    (httpx.TimeoutException, "httpx.TimeoutException", "timeout"),
    (httpcore.TimeoutException, "httpcore.TimeoutException", "timeout"),
    (httpx.NetworkError, "httpx.NetworkError", "transport_error"),
    (httpx.RequestError, "httpx.RequestError", "transport_error"),
    (httpcore.NetworkError, "httpcore.NetworkError", "transport_error"),
    (TimeoutError, "builtins.TimeoutError", "timeout"),
    (PermissionError, "builtins.PermissionError", "os_error"),
    (ConnectionRefusedError, "builtins.ConnectionRefusedError", "os_error"),
    (ConnectionResetError, "builtins.ConnectionResetError", "os_error"),
    (ConnectionAbortedError, "builtins.ConnectionAbortedError", "os_error"),
    (BrokenPipeError, "builtins.BrokenPipeError", "os_error"),
    (OSError, "builtins.OSError", "os_error"),
    (ExceptionGroup, "builtins.ExceptionGroup", "wrapper"),
    (BaseExceptionGroup, "builtins.BaseExceptionGroup", "wrapper"),
    (RuntimeError, "builtins.RuntimeError", "wrapper"),
)


def transport_diagnostics(error: BaseException, operation: str) -> dict:
    """Bounded graph of causes, contexts and group members, including hidden context.

    Unknown/custom types use a fixed label. Even class attributes, messages,
    requests, response bodies, traceback locals and URLs cannot enter this record.
    """
    pending = deque([(error, None, "root", 0)])
    seen: set[int] = set()
    nodes = []
    truncated = False
    while pending and len(nodes) < 32:
        current, parent, relation, depth = pending.popleft()
        if id(current) in seen:
            continue
        seen.add(id(current))
        label, category = "other_exception", "unknown"
        for kind, name, failure in _TYPES:
            if isinstance(current, kind):
                label, category = name, failure
                break
        errno = getattr(current, "errno", None) if isinstance(current, OSError) else None
        node_id = len(nodes)
        nodes.append({"class": label, "category": category,
                      "errno": errno if type(errno) is int else None,
                      "parent": parent, "relation": relation})
        children = [(current.__cause__, "cause"), (current.__context__, "context")]
        if isinstance(current, BaseExceptionGroup):
            children.extend((child, "group_member") for child in current.exceptions[:32])
            truncated |= len(current.exceptions) > 32
        for child, edge in children:
            if child is not None and id(child) not in seen:
                if depth < 8:
                    pending.append((child, node_id, edge, depth + 1))
                else:
                    truncated = True
    categories = {node["category"] for node in nodes}
    priority = ("dns_error", "tls_error", "connect_timeout", "read_timeout",
                "write_timeout", "pool_timeout", "connection_error", "read_error",
                "write_error", "proxy_error", "timeout", "os_error", "transport_error")
    category = next((item for item in priority if item in categories), "unknown")
    phase = {"dns_error": "dns", "tls_error": "tls", "connection_error": "connect",
             "connect_timeout": "connect", "read_error": "read", "read_timeout": "read",
             "write_error": "write", "write_timeout": "write", "pool_timeout": "pool",
             "proxy_error": "connect"}.get(category, "unknown")
    return {"event": "image_provider_transport_failure", "endpoint_hostname": "llm-api.net",
            "request_method": "POST", "operation": operation if operation in ("edits", "generations") else "unknown",
            "request_scope": "client_lifecycle_and_post", "failure_phase": phase,
            "category": category, "top_level_exception_class": nodes[0]["class"],
            "exceptions": nodes, "truncated": truncated or bool(pending)}


@asynccontextmanager
async def image_transport_guard(operation: str):
    try:
        yield
    except Exception as error:
        # Cancellation / mixed BaseExceptionGroups deliberately propagate.
        record = transport_diagnostics(error, operation)
        if record["category"] == "unknown":
            raise
        logger.warning("image_provider_transport_failure %s", json.dumps(record, sort_keys=True))
        # Do not let FastMCP log the original exception chain or its request.
        raise ImageProviderUnavailable() from None
