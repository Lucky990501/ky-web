"""Small persistent Preview-only request counter, not a Production service."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sqlite3
import time
import json
import re

import httpx

ORDINARY_TEXT_AGENTS=frozenset({'copywriting-agent','preview-wechat-agent','campaign-agent'})
TEXT_REQUEST_CEILING_CNY=Decimal('5.00')
CONTROLLED_TEXT_REQUEST_CEILING_CNY=Decimal('0.60')
COPYWRITING_MAX_OUTPUT_TOKENS=8192


def text_request_ceiling(agent_id=None,*,controlled=False):
    # Only authenticated ordinary text-Agent tasks receive the new allowance.
    # Controlled, unknown and image-orchestration requests retain their boundary.
    return (TEXT_REQUEST_CEILING_CNY if not controlled and agent_id in ORDINARY_TEXT_AGENTS
            else CONTROLLED_TEXT_REQUEST_CEILING_CNY)


class PreviewInputRejected(PermissionError):
    code='PREVIEW_INPUT_REJECTED'
    def __init__(self,branch,payload=None):
        from app.test_text_gate import input_shape
        super().__init__('Preview request rejected before Provider dispatch')
        self.branch_id=branch
        self.validation_stage='preview_text_preflight'
        self.safe_structure=input_shape(payload)


def preview_text_wire(body):
    from app.test_text_gate import MAX_REQUEST_BYTES
    wire=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf-8')
    if len(wire)>MAX_REQUEST_BYTES:
        exc=PreviewInputRejected('preview_wire_too_large',body)
        exc.safe_structure.update(encoded_bytes=len(wire),wire_limit_bytes=MAX_REQUEST_BYTES)
        raise exc
    return wire


def preview_input_message(branch):
    messages={
        'preview_wire_too_large':'模型请求超过已验证的接口大小上限，本次请求未调用模型；请保留诊断ID联系Owner。',
        'test_text_gate_request_too_large':'模型请求的安全编码大小超过接口上限，本次请求未调用模型；请保留诊断ID。',
        'message_unknown_field':'模型消息包含尚未兼容的字段，本次请求未调用模型；请保留诊断ID。',
        'preview_business_tools_missing':'当前请求缺少已授权的业务工具定义，本次请求未调用模型。',
        'preview_model_mismatch':'当前请求的模型身份不匹配，本次请求未调用模型。',
        'preview_provider_config_unverified':'文本供应商配置或费率未核验，本次请求未调用模型。',
        'preview_dispatch_paused':'Preview模型请求当前已暂停，本次请求未调用模型。',
    }
    return messages.get(branch,PREVIEW_ERRORS['PREVIEW_INPUT_REJECTED'])


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
    "PREVIEW_CONTEXT_COST_LIMIT": "当前完整方案预计超过单次费用上限，本次请求尚未调用模型；请联系Owner核对费用额度，不要反复重试。",
    "KNOWLEDGE_EMBEDDING_BATCH_TOO_LARGE": "资料向量请求超过大小限制，原文件已保留，请重新处理。",
    "KNOWLEDGE_EMBEDDING_VALIDATION_FAILED": "资料向量请求未通过校验，原文件已保留，请重新处理；仍失败时请联系Owner。",
    "KNOWLEDGE_PROCESSING_FAILED": "资料处理失败，原文件已保留，可重新处理，无需重复上传。",
    "MODEL_TOOL_UNAVAILABLE": "模型或工具暂不可用，请稍后再试。",
    "CONTENT_SAVE_FAILED": "内容保存失败，请保留诊断ID联系Owner，不要重复生成。",
    "PREVIEW_CONTROLLED_ACCEPTANCE_DENIED": "本轮受控验收授权缺失、失效或已关闭，未发起额外模型请求。",
    "MODEL_OUTPUT_INCOMPLETE": "模型输出达到本轮长度上限，文章尚未完整保存；不会自动收费重试。",
    "ACTIVITY_PLAN_VALIDATION_FAILED": "活动方案未通过结构或事实校验，未保存为正式方案；请保留诊断ID，不会自动收费重试。",
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
    if any(x.get('event')=='preview_start_timeout' for x in trace.get('lifecycle_events',[]) if isinstance(x,dict)):
        return 'PREVIEW_RUNTIME_START_TIMEOUT'
    if task.get('agent_id')=='campaign-agent' and 'failed' in str(trace.get('structured_result_status','')):
        return 'ACTIVITY_PLAN_VALIDATION_FAILED'
    if denied:
        return denied.get("code") or ("PREVIEW_MODEL_BUDGET_EXHAUSTED" if denied.get("reason") == "PreviewBudgetExceeded" else "PREVIEW_INPUT_REJECTED")
    if trace.get("result_persistence_error_stage"):
        return "CONTENT_SAVE_FAILED"
    if 'Incomplete response returned, reason:' in str(trace.get('error','')):
        return 'MODEL_OUTPUT_INCOMPLETE'
    return "MODEL_TOOL_UNAVAILABLE"


def bounded_text_output(body,prices,agent_id,*,controlled=False):
    """One estimated request; ordinary text and controlled ceilings stay distinct."""
    from app.test_text_gate import TestTextGate
    body=dict(body)
    article=agent_id=='preview-wechat-agent'
    cap=(COPYWRITING_MAX_OUTPUT_TOKENS if agent_id=='copywriting-agent' and not controlled
         else 8192 if article or agent_id=='campaign-agent' else 4096)
    if article and controlled:
        body['instructions']=(body.get('instructions') or '')+'\n本轮受控文章约600中文字，6至8个短段落、2个小标题和一处引用即可。遵循已绑定1.0.1 Skill输出简洁Markdown正文；平台会确定性生成安全内联HTML，不重复逐段CSS或输出第二份HTML，不附制作过程。不要检索知识或调用图片工具；信息充分后直接完成。'
    requested=body.get('max_output_tokens')
    if requested is not None and (type(requested) is not int or requested<=0):
        raise PreviewBudgetExceeded(PREVIEW_ERRORS['PREVIEW_INPUT_REJECTED'],'PREVIEW_INPUT_REJECTED')
    from app.preview_long_output import apply_policy, LONG_OUTPUT_TOKENS
    body,policy=apply_policy(body,agent_id,controlled=controlled)
    if policy['phase']!='unchanged':
        cap=LONG_OUTPUT_TOKENS
        requested=body['max_output_tokens']
    minimum=min(requested or (4096 if article else 2048),4096 if article else 2048)
    body['max_output_tokens']=min(requested or cap,cap)
    desired_output=body['max_output_tokens']
    ceiling=text_request_ceiling(agent_id,controlled=controlled)
    rate_in=Decimal(str(prices['text_input_cny_per_million']));rate_out=Decimal(str(prices['text_output_cny_per_million']))
    if not rate_in.is_finite() or not rate_out.is_finite() or rate_in<0 or rate_out<=0:raise PreviewBudgetExceeded('费率尚未核实。')
    def cost_refusal():
        # Measure the unchanged complete request, not the rejected/clamped
        # output value. Log only numeric structure, never prompt/tool contents.
        probe={**body,'max_output_tokens':desired_output}
        bound=TestTextGate.request_bound(probe)['input_tokens_upper']
        size=lambda value:len(json.dumps(value,ensure_ascii=True,allow_nan=False).encode('ascii'))
        exc=PreviewBudgetExceeded(PREVIEW_ERRORS['PREVIEW_CONTEXT_COST_LIMIT'],'PREVIEW_CONTEXT_COST_LIMIT')
        exc.cost_diagnostics={
            'encoded_bytes':size(probe),
            'utf8_bytes':len(json.dumps(probe,ensure_ascii=False,allow_nan=False).encode('utf-8')),
            'instructions_encoded_bytes':size(probe.get('instructions','')),
            'tools_encoded_bytes':size(probe.get('tools',[])),
            'input_encoded_bytes':size(probe.get('input',[])),
            'input_tokens_upper':bound,'minimum_output_tokens':minimum,
            'desired_output_tokens':desired_output,
            'input_reserved_cny':str(Decimal(bound)*rate_in/1_000_000),
            'minimum_output_reserved_cny':str(Decimal(minimum)*rate_out/1_000_000),
            'complete_output_reserved_cny':str(Decimal(desired_output)*rate_out/1_000_000),
            'minimum_total_cny':str((Decimal(bound)*rate_in+Decimal(minimum)*rate_out)/1_000_000),
            'complete_total_cny':str((Decimal(bound)*rate_in+Decimal(desired_output)*rate_out)/1_000_000),
            'request_ceiling_cny':str(ceiling),
        }
        return exc
    if not controlled and agent_id in ORDINARY_TEXT_AGENTS:
        bounds=TestTextGate.request_bound(body)
        cost=(Decimal(bounds['input_tokens_upper'])*rate_in+Decimal(desired_output)*rate_out)/1_000_000
        if cost>ceiling:raise cost_refusal()
        # The new ordinary allowance must not be made to fit by cutting the
        # complete article/script/structured-plan output reservation.
        return body,cost
    for _ in range(2):
        bounds=TestTextGate.request_bound(body)
        available=ceiling*1_000_000-Decimal(bounds['input_tokens_upper'])*rate_in
        body['max_output_tokens']=min(body['max_output_tokens'],max(0,int(available/rate_out)))
        if body['max_output_tokens']<minimum:
            raise cost_refusal()
    bounds=TestTextGate.request_bound(body)
    cost=(Decimal(bounds['input_tokens_upper'])*rate_in+Decimal(body['max_output_tokens'])*rate_out)/1_000_000
    if body['max_output_tokens']<minimum or cost>ceiling:
        raise cost_refusal()
    return body,cost


def observe_text_terminal(budget,task_id,block):
    """Safe HTTP response evidence; no article, tool arguments or reasoning text."""
    for line in block.splitlines():
        if not line.startswith(b'data:'):continue
        try:event=json.loads(line[5:].strip())
        except (ValueError,UnicodeError):continue
        if event.get('type') not in {'response.completed','response.incomplete','response.failed'}:continue
        response=event.get('response') or {};usage=response.get('usage') or {}
        details=usage.get('output_tokens_details') or {}
        reasoning=details.get('reasoning_tokens')
        total=usage.get('output_tokens')
        text_chars=sum(len(part.get('text','')) for item in response.get('output',[]) if isinstance(item,dict) and item.get('type')=='message'
                       for part in item.get('content',[]) if isinstance(part,dict) and part.get('type')=='output_text' and isinstance(part.get('text'),str))
        budget.observe({'event':'text_http_terminal','task_id':task_id,'type':event['type'],
                        'status':response.get('status') if response.get('status') in {'completed','incomplete','failed'} else 'UNKNOWN',
                        'incomplete_reason':(response.get('incomplete_details') or {}).get('reason') if (response.get('incomplete_details') or {}).get('reason') in {'max_output_tokens','content_filter'} else None,
                        'reasoning_tokens':reasoning if type(reasoning) is int and reasoning>=0 else None,
                        # Not an exact visible-token count: tools/framing may
                        # also consume non-reasoning output tokens.
                        'non_reasoning_output_tokens':total-reasoning if type(total) is int and type(reasoning) is int and 0<=reasoning<=total else None,
                        'visible_output_chars':text_chars,
                        'incomplete_message_count':sum(isinstance(i,dict) and i.get('type')=='message' and i.get('status')=='incomplete' for i in response.get('output',[])),
                        'usage':{k:v for k,v in usage.items() if k in {'input_tokens','output_tokens','total_tokens'} and type(v) is int}})


class PreviewBudget:
    # Owner-approved cumulative ceilings, NOT additions to prior limits.
    # Image/Embedding grants and every historical reservation remain unchanged.
    LIMITS = {"text": (1000, Decimal("600")), "image": (70, Decimal("70")), "embedding": (271, Decimal("15"))}
    TOTAL_LIMIT_CNY = Decimal("685")

    def __init__(self, root: Path):
        self.path = root / "preview-budget.sqlite"
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS requests(kind TEXT NOT NULL, ceiling TEXT NOT NULL, created REAL NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS observations(created REAL NOT NULL, payload TEXT NOT NULL)")

    def observe(self, payload):
        import json
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO observations VALUES(?,?)", (time.time(), json.dumps(payload)))

    def reserve(self, kind: str, ceiling: Decimal, *, controlled=None, agent_id=None):
        if kind not in self.LIMITS or not ceiling.is_finite() or ceiling <= 0:
            raise PreviewBudgetExceeded("调用费用上限尚未核实；没有发起付费请求。")
        if kind == 'text' and ceiling > text_request_ceiling(agent_id,controlled=controlled is not None):
            raise PreviewBudgetExceeded(PREVIEW_ERRORS['PREVIEW_CONTEXT_COST_LIMIT'], 'PREVIEW_CONTEXT_COST_LIMIT')
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
                or sum((Decimal(v) for _, v in rows), Decimal(0)) + ceiling > self.TOTAL_LIMIT_CNY):
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
                or sum((Decimal(v) for t,v in rows), Decimal(0)) + cost > self.TOTAL_LIMIT_CNY):
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

    def task_output_incomplete(self, task_id):
        # Exact Task binding; the latest terminal event is authoritative even
        # when the SDK wraps an incomplete response as a generic runtime error.
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT payload FROM observations WHERE payload LIKE ? ORDER BY rowid DESC", ('%' + task_id + '%',)).fetchall()
        for (payload,) in rows:
            value = json.loads(payload)
            if value.get('task_id') == task_id and value.get('event') == 'text_http_terminal':
                return value.get('status') == 'incomplete'
        return False

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
