"""Password and session primitives for the product-facing API.

Runtime MCP credentials are deliberately separate from user sessions.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass


class AuthenticationError(PermissionError):
    pass


def hash_password(password: str, salt: bytes | None = None) -> str:
    if len(password) < 8:
        raise ValueError("密码至少需要 8 个字符。")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return "pbkdf2_sha256$310000$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, raw_salt, raw_digest = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.urlsafe_b64decode(raw_salt), int(rounds))
        return hmac.compare_digest(derived, base64.urlsafe_b64decode(raw_digest))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True, slots=True)
class UserPrincipal:
    user_id: str
    tenant_id: str
    role: str


class SessionIssuer:
    def __init__(self, secret: str) -> None:
        self._secret = secret.encode("utf-8")

    def issue(self, principal: UserPrincipal, lifetime_seconds: int = 60 * 60 * 12) -> str:
        payload = json.dumps({"sub": principal.user_id, "tenant_id": principal.tenant_id, "role": principal.role, "exp": int(time.time()) + lifetime_seconds}, separators=(",", ":"), sort_keys=True).encode()
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
        signature = hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
        return f"workbench1.{encoded}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"

    def verify(self, token: str) -> UserPrincipal:
        try:
            prefix, encoded, signature = token.split(".")
            if prefix != "workbench1":
                raise AuthenticationError("登录状态无效。")
            expected = hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
            actual = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
            if not hmac.compare_digest(expected, actual):
                raise AuthenticationError("登录状态无效。")
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if int(payload["exp"]) <= int(time.time()):
                raise AuthenticationError("登录已过期。")
            return UserPrincipal(str(payload["sub"]), str(payload["tenant_id"]), str(payload["role"]))
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            raise AuthenticationError("登录状态无效。") from exc
