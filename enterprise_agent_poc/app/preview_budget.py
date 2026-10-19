"""Small persistent Preview-only request counter, not a Production service."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sqlite3
import time
import json
import re

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
            item = dict(item)
            if item.get('type', 'message') == 'message':
                # SDK output decorations are not Responses input fields. Only
                # these two observed SDK fields are removed; all other unknown
                # fields still fail the unchanged formal request validator.
                if 'phase' in item and item['phase'] not in {None, 'commentary', 'final_answer'}:
                    raise ValueError('未支持的消息阶段')
                item.pop('phase', None)
                item.pop('internal_chat_message_metadata_passthrough', None)
            safe.append(item)
        body["input"] = safe
    return body, dropped


class PreviewBudgetExceeded(PermissionError):
    def __init__(self, message, code="PREVIEW_MODEL_BUDGET_EXHAUSTED"):
        super().__init__(message)
        self.code = code


PREVIEW_ERRORS = {
    "PREVIEW_MODEL_BUDGET_EXHAUSTED": "模型体验额度不足，请联系Owner追加授权；不会自动重试。",
    "PREVIEW_EMBEDDING_BUDGET_EXHAUSTED": "知识处理额度不足，原文件已保留；追加授权后可重新处理。",
    "PREVIEW_INPUT_REJECTED": "模型上下文未通过安全或费用校验，并非一定是文字过长；请保留诊断ID。",
    "PREVIEW_CONTEXT_COST_LIMIT": "本轮会话上下文超过单请求费用上限，未调用模型；请新建会话或联系Owner。",
    "KNOWLEDGE_EMBEDDING_BATCH_TOO_LARGE": "资料向量请求超过大小限制，原文件已保留，请重新处理。",
    "KNOWLEDGE_EMBEDDING_VALIDATION_FAILED": "资料向量请求未通过校验，原文件已保留，请重新处理；仍失败时请联系Owner。",
    "KNOWLEDGE_PROCESSING_FAILED": "资料处理失败，原文件已保留，可重新处理，无需重复上传。",
    "MODEL_TOOL_UNAVAILABLE": "模型或工具暂不可用，请稍后再试。",
    "CONTENT_SAVE_FAILED": "内容保存失败，请保留诊断ID联系Owner，不要重复生成。",
}


def bounded_embedding_batches(texts, model):
    """Use the installed HTTPX serializer, preserving order and every chunk."""
    def size(batch):
        return len(httpx.Request("POST", "https://llm-api.net/v1/embeddings", json={
            "model": model, "input": batch, "encoding_format": "float"}).content)
    batches, current = [], []
    for text in texts:
        if size([text]) > 8192:
            raise PreviewBudgetExceeded(PREVIEW_ERRORS["KNOWLEDGE_EMBEDDING_BATCH_TOO_LARGE"], "KNOWLEDGE_EMBEDDING_BATCH_TOO_LARGE")
        if current and (len(current) >= 32 or size(current + [text]) > 8192):
            batches.append(current); current = []
        current.append(text)
    if current: batches.append(current)
    return batches


def preview_task_failure(task, trace, denied):
    if denied:
        return denied.get("code") or ("PREVIEW_MODEL_BUDGET_EXHAUSTED" if denied.get("reason") == "PreviewBudgetExceeded" else "PREVIEW_INPUT_REJECTED")
    if trace.get("result_persistence_error_stage"):
        return "CONTENT_SAVE_FAILED"
    return "MODEL_TOOL_UNAVAILABLE"


class PreviewBudget:
    LIMITS = {"text": (154, Decimal("84")), "image": (20, Decimal("20")), "embedding": (71, Decimal("5"))}

    def __init__(self, root: Path):
        self.path = root / "preview-budget.sqlite"
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS requests(kind TEXT NOT NULL, ceiling TEXT NOT NULL, created REAL NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS observations(created REAL NOT NULL, payload TEXT NOT NULL)")

    def observe(self, payload):
        import json
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO observations VALUES(?,?)", (time.time(), json.dumps(payload)))

    def reserve(self, kind: str, ceiling: Decimal, *, controlled=None):
        if kind not in self.LIMITS or not ceiling.is_finite() or ceiling <= 0:
            raise PreviewBudgetExceeded("调用费用上限尚未核实；没有发起付费请求。")
        if kind == 'text' and ceiling > Decimal('0.60'):
            raise PreviewBudgetExceeded("本次请求预留费用超过单次授权上限；请缩短输入，未调用模型。", "PREVIEW_INPUT_REJECTED")
        with sqlite3.connect(self.path, timeout=10) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if controlled is not None:
                if (kind!='text' or not isinstance(controlled,dict)
                    or not re.fullmatch(r'[a-zA-Z0-9_-]{1,120}',str(controlled.get('authorization_id','')))
                    or not re.fullmatch(r'[a-zA-Z0-9_-]{1,120}',str(controlled.get('task_id','')))
                    or type(controlled.get('max_requests')) is not int or not 1<=controlled['max_requests']<=4):
                    raise PreviewBudgetExceeded('受控验收授权无效；未调用模型。')
                markers=[json.loads(row[0]) for row in conn.execute('SELECT payload FROM observations')]
                markers=[m for m in markers if m.get('event')=='controlled_text_reservation' and m.get('authorization_id')==controlled['authorization_id']]
                if len(markers)>=controlled['max_requests'] or any(m.get('task_id')!=controlled['task_id'] for m in markers):
                    raise PreviewBudgetExceeded('本轮受控文章请求上限已达到；没有自动重试。')
            rows = conn.execute("SELECT kind,ceiling FROM requests").fetchall()
            count, limit = self.LIMITS[kind]
            if (sum(k == kind for k, _ in rows) >= count
                or sum((Decimal(v) for k, v in rows if k == kind), Decimal(0)) + ceiling > limit
                or sum((Decimal(v) for _, v in rows), Decimal(0)) + ceiling > Decimal("109")):
                raise PreviewBudgetExceeded("本次体验调用额度已用完，请联系Owner；不会自动重试。")
            # Reserve the verified worst-case amount before network dispatch.
            # Unknown outcomes remain reserved; restart never resets this file.
            conn.execute("INSERT INTO requests VALUES(?,?,?)", (kind, str(ceiling), time.time()))
            if controlled is not None:
                conn.execute('INSERT INTO observations VALUES(?,?)',(time.time(),json.dumps({'event':'controlled_text_reservation',**controlled})))

    def check_capacity(self, kind, count, ceiling):
        """Preflight the whole file without charging; each dispatch still reserves."""
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT kind,ceiling FROM requests").fetchall()
        maximum, money = self.LIMITS[kind]
        cost = ceiling * count
        if (sum(t == kind for t,v in rows) + count > maximum
                or sum((Decimal(v) for t,v in rows if t == kind), Decimal(0)) + cost > money
                or sum((Decimal(v) for t,v in rows), Decimal(0)) + cost > Decimal("109")):
            code = "PREVIEW_EMBEDDING_BUDGET_EXHAUSTED" if kind == "embedding" else "PREVIEW_MODEL_BUDGET_EXHAUSTED"
            raise PreviewBudgetExceeded(PREVIEW_ERRORS[code], code)

    def task_denial(self, task):
        from datetime import datetime, timezone
        def stamp(value):
            parsed=datetime.fromisoformat(str(value))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
        if not task.get("started_at") or not task.get("completed_at"): return None
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT payload FROM observations WHERE created BETWEEN ? AND ? ORDER BY created DESC", (stamp(task['started_at']), stamp(task['completed_at']) + 1)).fetchall()
        for (payload,) in rows:
            value = json.loads(payload)
            if value.get('event') == 'text_denied' and (value.get('task_id') == task['id'] or not value.get('task_id')):
                return value
        return None

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
        reserve_embedding(request, self.budget, self.pricing)
        if request.url.host == "llm-api.net" and request.url.path.startswith("/v1/images/"):
            if not self.pricing.get("image_pricing_verified"):
                raise PreviewBudgetExceeded("图片供应商费用尚未核实；未调用图片Provider。")
            self.budget.reserve("image", Decimal(str(self.pricing["image_request_max_cny"])))
        return await super().handle_async_request(request)


def reserve_embedding(request, budget, pricing):
    if request.url.host != 'llm-api.net' or request.url.path != '/v1/embeddings': return
    import json
    body = json.loads(request.content)
    if (not pricing.get('embedding_pricing_verified') or body.get('model') != 'text-embedding-3-small'
        or len(request.content) > 8192):
        raise PreviewBudgetExceeded('Embedding身份或请求费用边界未满足；没有调用。')
    budget.reserve('embedding', Decimal(str(pricing['embedding_request_max_cny'])))
    budget.observe({'event':'embedding_dispatch','batch_items':len(body.get('input',[])), 'body_bytes':len(request.content)})


class PreviewSyncTransport(httpx.HTTPTransport):
    def __init__(self, budget, pricing):
        super().__init__(retries=0)
        self.budget, self.pricing = budget, pricing

    def handle_request(self, request):
        reserve_embedding(request, self.budget, self.pricing)
        return super().handle_request(request)
