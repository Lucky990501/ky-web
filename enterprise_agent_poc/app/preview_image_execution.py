"""Explicit Owner-authorized Preview business executor, not a Codex turn.

The existing authenticated Platform MCP service retains Provider credentials,
input/Logo checks, transport metering and object storage. No approval policy is
changed and no previously rejected Task is replayed through this executor.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
import sqlite3
import time
from uuid import UUID, uuid4

from app.image_request_contract import image_plan
from app.preview_budget import PreviewBudgetExceeded
from app.security import RuntimePrincipal


MODE = 'preview_direct_image_business_v1'
AUTHORIZATION_ID = 'owner-preview-direct-image-20261019-v1'


class PreviewImageExecution:
    def __init__(self, main, budget, pricing, authorization):
        self.main, self.budget, self.pricing = main, budget, pricing
        self.authorization = authorization or {}
        self.pending = set()

    def authorized(self):
        grant = self.authorization
        try:
            fee = Decimal(str(self.pricing.get('image_request_max_cny', 'Infinity')))
            authorized_fee = Decimal(str(grant.get('request_max_cny', '0')))
        except Exception:
            return False
        return (grant.get('authorization_id') == AUTHORIZATION_ID
                and grant.get('approved') is True
                and grant.get('execution_mode') == MODE
                and grant.get('model') == self.main.settings.image_model_id
                and grant.get('max_provider_calls') == 2
                and self.pricing.get('image_pricing_verified') is True
                and fee.is_finite() and authorized_fee.is_finite() and Decimal('0') < fee <= authorized_fee <= Decimal('1'))

    def owns_task(self, task_id):
        with sqlite3.connect(self.budget.path) as conn:
            return any(json.loads(row[0]).get('event') == 'preview_image_submission'
                       and json.loads(row[0]).get('task_id') == task_id
                       for row in conn.execute('select payload from observations'))

    def capability(self):
        quota = self.budget.summary()['image']
        enabled = self.authorized() and quota['requests'] < quota['max_requests']
        return {'enabled': enabled, 'execution_mode': MODE,
                'request_max_cny': str(self.pricing.get('image_request_max_cny', '0')),
                'remaining_requests': max(0, quota['max_requests'] - quota['requests']),
                'user_message': ('独立体验图片生成：每次仅生成1张，不自动重试。'
                                 if enabled else '图片生成暂不可用：未取得独立执行授权或体验额度已用完。')}

    def _prior_claim(self, principal, request_id, fingerprint):
        with sqlite3.connect(self.budget.path) as conn:
            rows = conn.execute('select payload from observations').fetchall()
        for (raw,) in rows:
            item = json.loads(raw)
            if (item.get('event') == 'preview_image_submission'
                    and item.get('tenant_id') == principal.tenant_id
                    and item.get('user_id') == principal.user_id
                    and item.get('request_id') == request_id):
                if item.get('fingerprint') != fingerprint:
                    raise ValueError('同一提交标识不能用于不同图片需求。')
                return self.main.product_store.task(item['task_id'], principal.tenant_id, principal.user_id)
        return None

    def submit(self, principal, payload):
        if not self.authorized() or not principal.tenant_id.startswith('preview-'):
            raise PermissionError('尚未取得Preview独立图片业务授权。')
        if set(payload) - {'message', 'conversation_id', 'attachments', 'preview_image_confirmed', 'preview_image_request_id'}:
            raise ValueError('图片执行身份及配置只能由服务端确定。')
        if payload.get('preview_image_confirmed') is not True:
            raise PermissionError('请先明确确认图片生成费用。')
        request_id = str(UUID(str(payload.get('preview_image_request_id', ''))))
        prompt = payload.get('message')
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 4000:
            raise ValueError('请输入1～4000字的图片需求。')
        if payload.get('attachments'):
            raise ValueError('本次独立图片入口仅开放文字生图，参考图编辑尚未开放。')
        plan = image_plan(prompt, prompt, '1:1', None)
        if len(plan) != 1:
            raise ValueError('本次体验每个任务只支持1张图片，请拆分需求。')
        fingerprint = hashlib.sha256(json.dumps({'message': prompt, 'conversation_id': payload.get('conversation_id')}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        prior = self._prior_claim(principal, request_id, fingerprint)
        if prior:
            return prior
        with self.main.store.connection() as conn:
            if conn.execute("select count(*) from tasks where status in ('queued','running')").fetchone()[0]:
                raise PermissionError('体验服务当前正在生成，请稍后再提交。')
        self.budget.check_capacity('image', 1, Decimal(str(self.pricing['image_request_max_cny'])))
        if not self.main.product_store.agent_enabled(principal.tenant_id, 'image-agent'):
            raise PermissionError('图片智能体尚未为当前企业启用。')
        task = self.main.product_store.create_task(principal.tenant_id, principal.user_id, 'image-agent', prompt, payload.get('conversation_id'))
        self.budget.observe({'event': 'preview_image_submission', 'authorization_id': AUTHORIZATION_ID,
                             'tenant_id': principal.tenant_id, 'user_id': principal.user_id,
                             'request_id': request_id, 'fingerprint': fingerprint, 'task_id': task['id']})
        return task

    def schedule(self, task):
        if task['status'] != 'queued' or task.get('run_id') or task['id'] in self.pending:
            return
        self.pending.add(task['id'])
        job = asyncio.create_task(self.execute(task))
        job.add_done_callback(lambda completed: self.pending.discard(task['id']))

    async def call_image(self, task, plan):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        issuer = self.main.token_issuer
        token = issuer.issue(RuntimePrincipal(task['tenant_id'], 'image-agent', MODE,
                                             ('image:generate',), int(time.time()) + 240))
        scope = issuer.issue_task_scope(task['tenant_id'], task['id'])
        # This is the normal authenticated MCP transport, without a Codex client.
        async with streamablehttp_client(self.main.settings.platform_mcp_url,
                headers={'Authorization': 'Bearer ' + token, 'X-Runtime-Execution-Scope': scope},
                timeout=timedelta(seconds=180), sse_read_timeout=timedelta(seconds=180)) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool('image_generation', {
                    'prompt': task['input_text'], 'references': [],
                    'aspect_ratio': plan['aspect_ratio'], 'size': plan['size'],
                }, read_timeout_seconds=timedelta(seconds=180))
        if result.isError:
            raise RuntimeError('image_business_tool_failed')
        value = result.structuredContent
        if not isinstance(value, dict):
            value = next((json.loads(item.text) for item in result.content
                          if getattr(item, 'type', '') == 'text'), None)
        if not isinstance(value, dict) or not isinstance(value.get('results'), list) or len(value['results']) != 1:
            raise RuntimeError('image_business_result_invalid')
        return value

    async def execute(self, task):
        main, products = self.main, self.main.product_store
        tid, tenant = task['id'], task['tenant_id']
        # Claim once before any side effect. Restart/duplicate delivery never
        # repeats a request whose Provider outcome could be unknown.
        with sqlite3.connect(self.budget.path, timeout=10) as conn:
            conn.execute('BEGIN IMMEDIATE')
            observations = [json.loads(row[0]) for row in conn.execute('select payload from observations')]
            if any(x.get('event') == 'preview_image_execution_claimed' and x.get('task_id') == tid for x in observations):
                current = products.task_for_worker(tid)
                if current and current['status'] in {'queued', 'running'}:
                    products.set_task(tid, tenant, 'failed', 'failed', '上次图片请求结果尚未确认，没有自动重试。', error_code='image_outcome_unknown')
                return
            conn.execute('insert into observations values(?,?)', (time.time(), json.dumps({'event': 'preview_image_execution_claimed', 'task_id': tid, 'authorization_id': AUTHORIZATION_ID})))
        current = products.task_for_worker(tid)
        if not current or current['status'] != 'queued':
            return
        run_id, conversation_id = str(uuid4()), task.get('conversation_id') or str(uuid4())
        trace = {'run_id': run_id, 'tenant_id': tenant, 'agent_id': 'image-agent',
                 'conversation_id': conversation_id, 'execution_mode': MODE,
                 'runtime_version': MODE, 'codex_runtime_executed': False, 'codex_thread_id': None,
                 'authorization_id': AUTHORIZATION_ID, 'model': main.settings.image_model_id,
                 'mcp_calls': [], 'runtime_completed': False, 'runtime_status': 'running',
                 'required_tool_calls_completed': False, 'final_response_received': False,
                 'result_persistence_status': 'pending'}
        try:
            if not task.get('conversation_id'):
                main.store.save_conversation(conversation_id, tenant, 'image-agent', MODE,
                                             'preview-business:' + conversation_id, MODE)
            else:
                products.add_message(conversation_id, 'user', task['input_text'], message_id=f'task:{tid}:user')
            main.store.create_run_trace(run_id, conversation_id, tenant, 'image-agent', None, trace)
            products.attach_task_run(tid, tenant, run_id, conversation_id, user_id=task['user_id'])
            products.set_task(tid, tenant, 'running', 'generating', '正在生成图片，不会自动重试', run_id=run_id, conversation_id=conversation_id)
            products.add_task_activity(tid, tenant, 'generating', 'started')
            if products.cancellation_requested(tid, tenant):
                raise asyncio.CancelledError()
            plan = image_plan(task['input_text'], task['input_text'], '1:1', None)[0]
            result = await self.call_image(task, plan)
            artifact = result['results'][0]
            from app.platform_mcp.service import _validated_image_metadata
            from app.storage import storage_provider
            from PIL import Image
            import io
            key = artifact.get('storage_key', '')
            if not key.startswith(f'generated/{tenant}/') or any(x in {'', '.', '..'} for x in key.split('/')) or '\\' in key:
                raise ValueError('image_business_storage_invalid')
            content = storage_provider(main.settings).get(key)
            _validated_image_metadata(content)
            with Image.open(io.BytesIO(content)) as picture:
                artifact['width'], artifact['height'] = picture.size
            response = '已生成1张图片，请使用下方图片卡片查看大图或下载。'
            if result.get('delivery', {}).get('status') == 'partial':
                response += '\n提示：供应商实际尺寸与请求不一致，请查看卡片中的实际像素。'
            trace.update(mcp_calls=[{'server': 'platform', 'tool': 'image_generation', 'status': 'completed',
                                    'artifact': artifact, 'artifacts': [artifact], 'execution_mode': MODE}],
                         image_delivery=result.get('delivery'), runtime_completed=True, runtime_status='completed',
                         required_tool_calls_completed=True, final_response_received=True,
                         final_result=response, final_response_length=len(response), artifact_available=True)
            main.store.finish_run_trace(run_id, 'runtime_completed', trace)
            if products.cancellation_requested(tid, tenant):
                raise asyncio.CancelledError()
            products.complete_task_success(task, run_id=run_id, conversation_id=conversation_id,
                response=response, trace_payload=trace, image_artifacts=[artifact])
            products.add_task_activity(tid, tenant, 'generating', 'completed')
            products.add_task_activity(tid, tenant, 'completed', 'completed')
        except asyncio.CancelledError:
            trace.update(status='cancelled', runtime_status='cancelled')
            main.store.finish_run_trace(run_id, 'cancelled', trace)
            products.finalize_task_cancellation(tid, tenant, run_id=run_id, conversation_id=conversation_id)
        except Exception as error:
            code = ('PREVIEW_MODEL_BUDGET_EXHAUSTED' if isinstance(error, PreviewBudgetExceeded)
                    else 'image_business_error')
            trace.update(status='failed', error_code=code, error_type=type(error).__name__,
                         result_persistence_status='failed' if trace.get('runtime_completed') else 'pending')
            main.store.finish_run_trace(run_id, 'failed', trace)
            products.set_task(tid, tenant, 'failed', 'failed', '图片生成或保存未完成；没有自动重试，请保留诊断ID。', error_code=code,
                              run_id=run_id, conversation_id=conversation_id)
            products.add_task_activity(tid, tenant, 'failed', 'completed')
