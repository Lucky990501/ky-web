"""Small persistent Preview-only request counter, not a Production service."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sqlite3
import time

import httpx


def plaintext_history(body):
    """Preview compatibility only: omit opaque reasoning, retain real tool IO.

    Never decrypt/relabel opaque bytes or invent missing tool results. A
    reference-only provider history cannot be recovered by this adapter.
    """
    body = dict(body)
    body.pop("include", None)
    if isinstance(body.get("reasoning"), dict):
        body["reasoning"] = {k: v for k, v in body["reasoning"].items() if k != "encrypted_content"}
    items, dropped = body.get("input"), 0
    if isinstance(items, list):
        safe = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("不支持的模型历史格式")
            if item.get("type") == "reasoning":
                dropped += 1
                continue
            if item.get("type") == "item_reference":
                raise ValueError("模型历史只有引用，不能伪造明文上下文")
            if item.get("type", "message") not in {"message", "function_call", "function_call_output"}:
                raise ValueError("未支持的工具历史类型")
            if item.get("encrypted_content") is not None:
                raise ValueError("不向供应商转发不透明加密字段")
            safe.append(item)
        body["input"] = safe
    return body, dropped


class PreviewBudgetExceeded(PermissionError):
    pass


class PreviewBudget:
    LIMITS = {"text": (12, Decimal("10")), "image": (2, Decimal("20")), "embedding": (8, Decimal("5"))}

    def __init__(self, root: Path):
        self.path = root / "preview-budget.sqlite"
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS requests(kind TEXT NOT NULL, ceiling TEXT NOT NULL, created REAL NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS observations(created REAL NOT NULL, payload TEXT NOT NULL)")

    def observe(self, payload):
        import json
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO observations VALUES(?,?)", (time.time(), json.dumps(payload)))

    def reserve(self, kind: str, ceiling: Decimal):
        if kind not in self.LIMITS or not ceiling.is_finite() or ceiling <= 0:
            raise PreviewBudgetExceeded("调用费用上限尚未核实；没有发起付费请求。")
        with sqlite3.connect(self.path, timeout=10) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT kind,ceiling FROM requests").fetchall()
            count, limit = self.LIMITS[kind]
            if (sum(k == kind for k, _ in rows) >= count
                or sum((Decimal(v) for k, v in rows if k == kind), Decimal(0)) + ceiling > limit
                or sum((Decimal(v) for _, v in rows), Decimal(0)) + ceiling > Decimal("35")):
                raise PreviewBudgetExceeded("本次体验调用额度已用完，请联系Owner；不会自动重试。")
            # Reserve the verified worst-case amount before network dispatch.
            # Unknown outcomes remain reserved; restart never resets this file.
            conn.execute("INSERT INTO requests VALUES(?,?,?)", (kind, str(ceiling), time.time()))

    def summary(self):
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT kind,ceiling FROM requests").fetchall()
        return {kind: {"requests": sum(k == kind for k, _ in rows),
                       "reserved_ceiling_cny": str(sum((Decimal(v) for k, v in rows if k == kind), Decimal(0))),
                       "max_requests": count, "max_cny": str(limit)}
                for kind, (count, limit) in self.LIMITS.items()}


class PreviewImageTransport(httpx.AsyncHTTPTransport):
    def __init__(self, budget, pricing):
        super().__init__(retries=0)
        self.budget, self.pricing = budget, pricing

    async def handle_async_request(self, request):
        if request.url.host == "llm-api.net" and request.url.path.startswith("/v1/images/"):
            if not self.pricing.get("image_pricing_verified"):
                raise PreviewBudgetExceeded("图片供应商费用尚未核实；未调用图片Provider。")
            self.budget.reserve("image", Decimal(str(self.pricing["image_request_max_cny"])))
        return await super().handle_async_request(request)
