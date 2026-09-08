from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


class TokenError(PermissionError):
    pass


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@dataclass(frozen=True, slots=True)
class RuntimePrincipal:
    tenant_id: str
    agent_id: str
    runtime_profile_id: str
    scopes: tuple[str, ...]
    expires_at: int


class RuntimeTokenIssuer:
    """Signs short-lived, least-privilege tokens for Platform MCP only."""

    def __init__(self, secret: str) -> None:
        if len(secret) < 16:
            raise ValueError("Runtime MCP token secret 至少需要 16 个字符。")
        self._secret = secret.encode("utf-8")

    def issue(self, principal: RuntimePrincipal) -> str:
        payload = json.dumps(
            {
                "tenant_id": principal.tenant_id,
                "agent_id": principal.agent_id,
                "runtime_profile_id": principal.runtime_profile_id,
                "scopes": list(principal.scopes),
                "exp": principal.expires_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        encoded = _encode(payload)
        signature = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest()
        return f"poc1.{encoded}.{_encode(signature)}"

    def verify(self, token: str, required_scope: str) -> RuntimePrincipal:
        try:
            prefix, encoded, signature = token.split(".")
            if prefix != "poc1":
                raise TokenError("未知的 Runtime MCP token 格式。")
            # Reject alternate Base64URL spellings that decode to identical
            # bytes. Token values are credentials and must be canonical.
            if _encode(_decode(encoded)) != encoded or _encode(_decode(signature)) != signature:
                raise TokenError("Runtime MCP token 编码无效。")
            expected = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _decode(signature)):
                raise TokenError("Runtime MCP token 签名无效。")
            payload = json.loads(_decode(encoded))
            principal = RuntimePrincipal(
                tenant_id=str(payload["tenant_id"]),
                agent_id=str(payload["agent_id"]),
                runtime_profile_id=str(payload["runtime_profile_id"]),
                scopes=tuple(payload["scopes"]),
                expires_at=int(payload["exp"]),
            )
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise TokenError("Runtime MCP token 无效。") from exc
        if principal.expires_at <= int(time.time()):
            raise TokenError("Runtime MCP token 已过期。")
        if required_scope not in principal.scopes:
            raise TokenError("Runtime MCP token 没有该工具权限。")
        return principal
