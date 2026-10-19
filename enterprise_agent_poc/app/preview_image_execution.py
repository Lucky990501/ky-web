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
import re
import sqlite3
import time
from uuid import UUID, uuid4

from app.image_request_contract import image_plan
from app.preview_budget import PreviewBudgetExceeded
from app.security import RuntimePrincipal
from app.preview_image_parameters import PreviewReferenceBindings, preview_plan, quality_value, reference_limit, MODEL, SIZES, QUALITIES, SPEC_ID


MODE = 'preview_direct_image_business_v1'
AUTHORIZATION_ID = 'owner-preview-image-capabilities-20261019-v2'


class PreviewImageExecution:
    def __init__(self, main, budget, pricing, authorization):
        self.main, self.budget, self.pricing = main, budget, pricing
        self.authorization = authorization or {}
        self.pending = set()
        self.references = PreviewReferenceBindings(main.product_store,budget)

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
                and grant.get('max_provider_calls') == 20
                and self.pricing.get('image_pricing_verified') is True
                and fee.is_finite() and authorized_fee.is_finite() and Decimal('0') < fee <= authorized_fee <= Decimal('1'))

    def owns_task(self, task_id):
        with sqlite3.connect(self.budget.path) as conn:
            return any(json.loads(row[0]).get('event') == 'preview_image_submission'
                       and json.loads(row[0]).get('task_id') == task_id
                       for row in conn.execute('select payload from observations'))

    def submission(self, task_id):
        with sqlite3.connect(self.budget.path) as conn:
            return next((p for row in conn.execute('select payload from observations')
                         if (p := json.loads(row[0])).get('event') == 'preview_image_submission'
                         and p.get('task_id') == task_id), {})

    def capability(self):
        quota = self.budget.summary()['image']
        enabled = self.authorized() and quota['requests'] < quota['max_requests']
        return {'enabled': enabled, 'execution_mode': MODE,
                'request_max_cny': str(self.pricing.get('image_request_max_cny', '0')),
                'remaining_requests': max(0, quota['max_requests'] - quota['requests']),
                'max_outputs': 5, 'max_reference_images': reference_limit() if self.main.settings.image_model_id==MODEL else 1,
                'multi_reference_status':'BLOCKED_CURRENT_WIRE_HTTP_400' if reference_limit()==1 else 'OWNER_CONFIRMED_MULTIPART_NOT_PROVIDER_VERIFIED',
                'sizes':list(SIZES), 'qualities':list(QUALITIES), 'parameter_spec':SPEC_ID,
                'native_high_resolution': 'OWNER_SPECIFIED_NOT_PROVIDER_VERIFIED',
                'user_message': ('独立体验图片生成：每任务1～5张，参考图编辑及版本保留；不自动重试。'
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
        if set(payload) - {'message', 'conversation_id', 'attachments', 'preview_image_confirmed', 'preview_image_request_id', 'edit_generation_id', 'use_enterprise_context', 'size', 'quality'}:
            raise ValueError('图片执行身份及配置只能由服务端确定。')
        if not isinstance(payload.get('use_enterprise_context',False),bool):raise ValueError('知识使用选项无效。')
        if payload.get('preview_image_confirmed') is not True:
            raise PermissionError('请先明确确认图片生成费用。')
        request_id = str(UUID(str(payload.get('preview_image_request_id', ''))))
        quality = quality_value(payload.get('quality'))
        prompt = payload.get('message')
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 4000:
            raise ValueError('请输入1～4000字的图片需求。')
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        prior = self._prior_claim(principal, request_id, fingerprint)
        if prior:
            return prior
        attachments = payload.get('attachments') or []
        if not isinstance(attachments, list) or len(attachments) > 5:
            raise ValueError('当前每任务最多支持5张参考图，不会丢弃额外图片。')
        if len(attachments)>reference_limit():
            from app.image_request_contract import ImageRequestError
            raise ImageRequestError('preview_multi_reference_pending','多参考图当前暂不可用，未提交图片请求；请仅使用1张参考图。')
        ids=[item.get('id') for item in attachments if isinstance(item,dict)]
        if len(ids)!=len(attachments) or len(set(str(x) for x in ids))!=len(ids):raise ValueError('参考图片不能重复。')
        primary_record = None
        for item in attachments:
            if not isinstance(item, dict) or set(item) - {'type', 'id', 'role'} or item.get('type') != 'image':
                raise ValueError('参考图片格式无效。')
            if item.get('role', 'subject') not in {'subject', 'style', 'background', 'composition', 'brand', 'reference', 'front', 'back'}:
                raise ValueError('参考图片用途无效。')
            record = self.main.product_store.chat_image_attachment(principal.tenant_id, principal.user_id, str(item.get('id', '')))
            if not record or record.get('task_id') or self.references.claimed(principal,record['id']):
                raise ValueError('参考图不存在、已发送或无权访问。')
            primary_record = primary_record or record
        parent_id = payload.get('edit_generation_id')
        parent = None
        if not parent_id and not attachments and payload.get('conversation_id') and re.search(
                r'(?:标题|背景|人物|排版|比例).{0,12}(?:改|换|保持|不变)|(?:刚才|上一张|这张).{0,12}(?:改|换|修改|调整)|重新生成|再生成', prompt):
            with self.main.store.connection() as conn:
                saved = conn.execute('SELECT id FROM generations WHERE conversation_id=? AND tenant_id=? AND user_id=? AND deleted_at IS NULL LIMIT 1',
                    (payload['conversation_id'], principal.tenant_id, principal.user_id)).fetchone()
            if saved:
                from app.image_request_contract import ImageRequestError
                raise ImageRequestError('preview_edit_base_required','请先点击需要修改图片的“基于此图继续修改”，或上传参考图。没有把编辑请求偷偷改为文字生图，也没有调用图片服务。')
        if parent_id:
            if attachments:
                raise ValueError('请选择已有图片或上传参考图，不要同时提交。')
            with self.main.store.connection() as conn:
                row = conn.execute('SELECT * FROM generations WHERE id=? AND tenant_id=? AND user_id=? AND deleted_at IS NULL',
                                   (parent_id, principal.tenant_id, principal.user_id)).fetchone()
            if not row:
                raise LookupError('编辑基图不存在或无权访问。')
            parent = dict(row)
            if parent.get('conversation_id') != payload.get('conversation_id'):
                raise ValueError('编辑基图必须属于当前会话。')
        from fractions import Fraction
        source_ratio = '1:1'
        source_record = parent or (primary_record if len(attachments)==1 else {})
        if source_record and source_record.get('width') and source_record.get('height'):
            fraction = Fraction(source_record['width'], source_record['height'])
            candidate = f'{fraction.numerator}:{fraction.denominator}'
            if candidate in {'1:1','9:16','16:9','4:5','3:2','2:3'}:
                source_ratio = candidate
        plan = preview_plan(prompt,source_ratio,payload.get('size'))
        if (quality or len(attachments)>1 or any(int(x)>1536 for x in plan[0]['size'].split('x'))) and self.main.settings.image_model_id!=MODEL:
            raise ValueError('新增图片参数仅适用于Owner已确认的模型。')
        with self.main.store.connection() as conn:
            if conn.execute("select count(*) from tasks where status in ('queued','running')").fetchone()[0]:
                raise PermissionError('体验服务当前正在生成，请稍后再提交。')
        self.budget.check_capacity('image', len(plan), Decimal(str(self.pricing['image_request_max_cny'])))
        if payload.get('use_enterprise_context'):
            from app.preview_image_context import check_context_capacity
            check_context_capacity(self.main,self.budget,self.pricing,principal.tenant_id)
        if not self.main.product_store.agent_enabled(principal.tenant_id, 'image-agent'):
            raise PermissionError('图片智能体尚未为当前企业启用。')
        if parent:
            # An owner-checked immutable copy through the existing upload store;
            # no arbitrary URL/path, original Generation never overwritten.
            from app.storage import storage_provider
            from app.chat_image_uploads import validate_chat_image
            source = storage_provider(self.main.settings).get(parent['storage_key'])
            ext, mime, width, height = validate_chat_image(source, parent['mime_type'])
            key = f'chat-uploads/{principal.tenant_id}/{uuid4().hex}{ext}'
            storage_provider(self.main.settings).put(key, source, mime)
            record = self.main.product_store.create_chat_image_attachment(principal.tenant_id, principal.user_id,
                key, '编辑基图' + ext, mime, len(source), width, height)
            attachments = [{'type': 'image', 'id': record['id'], 'role': 'subject'}]
        task = self.main.product_store.create_task(principal.tenant_id, principal.user_id, 'image-agent', prompt,
            payload.get('conversation_id'), attachment_ids=[attachments[0]['id']] if attachments else [])
        self.budget.observe({'event': 'preview_image_submission', 'authorization_id': AUTHORIZATION_ID,
                             'tenant_id': principal.tenant_id, 'user_id': principal.user_id,
                             'request_id': request_id, 'fingerprint': fingerprint, 'task_id': task['id'],
                             'references': attachments, 'edit_parent_generation_id': parent_id, 'requested_outputs': len(plan), 'aspect_ratio': source_ratio,
                             'use_enterprise_context':payload.get('use_enterprise_context',False),
                             'size':plan[0]['size'],'quality':quality,'parameter_spec':SPEC_ID})
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
        submission = self.submission(task['id'])
        use_context = submission.get('use_enterprise_context') is True
        scopes = ('image:generate','knowledge:search','assets:search') if use_context else ('image:generate',)
        token = issuer.issue(RuntimePrincipal(task['tenant_id'], 'image-agent', MODE,
                                             scopes, int(time.time()) + 900))
        scope = issuer.issue_task_scope(task['tenant_id'], task['id'])
        # This is the normal authenticated MCP transport, without a Codex client.
        async with streamablehttp_client(self.main.settings.platform_mcp_url,
                headers={'Authorization': 'Bearer ' + token, 'X-Runtime-Execution-Scope': scope},
                timeout=timedelta(seconds=660), sse_read_timeout=timedelta(seconds=660)) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                attachments = self.references.resolve(task)
                prompt = task['input_text']
                if use_context:
                    from app.preview_image_context import collect_context
                    self.main.product_store.add_task_activity(task['id'],task['tenant_id'],'knowledge_retrieving','started')
                    prompt, context = await collect_context(session,task,self.budget)
                    self.main.product_store.add_task_activity(task['id'],task['tenant_id'],'knowledge_retrieving','completed')
                self.main.product_store.add_task_activity(task['id'],task['tenant_id'],'generating','started')
                arguments = {
                    'prompt': prompt, 'references': [],
                    'reference_images': [item['id'] for item in attachments],
                    'reference_role': (submission.get('references') or [{}])[0].get('role', 'subject'),
                    'aspect_ratio': plan['aspect_ratio'], 'size': plan['size'],
                }
                if submission.get('quality'):arguments['quality']=submission['quality']
                result = await session.call_tool('image_generation', arguments, read_timeout_seconds=timedelta(seconds=660))
        if result.isError:
            value=result.structuredContent or {}
            dependency=value.get('_tool_dependency',{}) if isinstance(value,dict) else {}
            status=dependency.get('provider_http_status')
            if type(status) is int and 400<=status<=599:
                self.budget.observe({'event':'preview_image_provider_rejected','task_id':task['id'],
                    'provider_status':status,'provider_error':dependency.get('preview_provider_error',{})})
                error=RuntimeError('image_provider_rejected')
                error.preview_provider_status=status
                raise error
            raise RuntimeError('image_business_tool_failed')
        value = result.structuredContent
        if not isinstance(value, dict):
            value = next((json.loads(item.text) for item in result.content
                          if getattr(item, 'type', '') == 'text'), None)
        if not isinstance(value, dict) or not isinstance(value.get('results'), list) or not value['results']:
            raise RuntimeError('image_business_result_invalid')
        if use_context:value['enterprise_context']=context
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
            if products.cancellation_requested(tid, tenant):
                raise asyncio.CancelledError()
            submission = self.submission(tid)
            plan = preview_plan(task['input_text'],submission.get('aspect_ratio','1:1'),submission.get('size'))[0]
            result = await self.call_image(task, plan)
            artifacts = result['results']
            if len(artifacts) > len(preview_plan(task['input_text'],submission.get('aspect_ratio','1:1'),submission.get('size'))):
                raise ValueError('image_business_result_count_invalid')
            submission = self.submission(tid)
            trace['edit_parent_generation_id'] = submission.get('edit_parent_generation_id')
            trace['reference_images_submitted'] = submission.get('references', [])
            trace['reference_images_used'] = list(dict.fromkeys(x for item in artifacts for x in item.get('reference_images_used', [])))
            from app.platform_mcp.service import _validated_image_metadata
            from app.storage import storage_provider
            from PIL import Image
            import io
            for artifact in artifacts:
                artifact['edit_parent_generation_id'] = submission.get('edit_parent_generation_id')
                key = artifact.get('storage_key', '')
                if not key.startswith(f'generated/{tenant}/') or any(x in {'', '.', '..'} for x in key.split('/')) or '\\' in key:
                    raise ValueError('image_business_storage_invalid')
                content = storage_provider(main.settings).get(key)
                _validated_image_metadata(content)
                with Image.open(io.BytesIO(content)) as picture:
                    artifact['width'], artifact['height'] = picture.size
            response = f'已生成{len(artifacts)}张图片，请使用下方图片卡片查看大图或下载。'
            if result.get('failures'):
                response += f"\n部分图片失败：{len(result['failures'])}张；已保留成功结果，没有自动重试。"
            if result.get('delivery', {}).get('status') == 'partial':
                response += '\n提示：供应商实际尺寸与请求不一致，请查看卡片中的实际像素。'
            if result.get('enterprise_context'):
                trace['enterprise_context'] = result['enterprise_context']
                response += '\n企业知识来源：'+'、'.join(dict.fromkeys(item['filename'] for item in result['enterprise_context']['sources']))
            trace.update(mcp_calls=[{'server': 'platform', 'tool': 'image_generation', 'status': 'completed',
                                    'artifact': artifacts[0], 'artifacts': artifacts, 'execution_mode': MODE}],
                         image_delivery=result.get('delivery'), runtime_completed=True, runtime_status='completed',
                         required_tool_calls_completed=True, final_response_received=True,
                         final_result=response, final_response_length=len(response), artifact_available=True)
            main.store.finish_run_trace(run_id, 'runtime_completed', trace)
            if products.cancellation_requested(tid, tenant):
                raise asyncio.CancelledError()
            products.complete_task_success(task, run_id=run_id, conversation_id=conversation_id,
                response=response, trace_payload=trace, image_artifacts=artifacts)
            products.add_task_activity(tid, tenant, 'generating', 'completed')
            products.add_task_activity(tid, tenant, 'completed', 'completed')
        except asyncio.CancelledError:
            trace.update(status='cancelled', runtime_status='cancelled')
            main.store.finish_run_trace(run_id, 'cancelled', trace)
            products.finalize_task_cancellation(tid, tenant, run_id=run_id, conversation_id=conversation_id)
        except Exception as error:
            code = ('PREVIEW_MODEL_BUDGET_EXHAUSTED' if isinstance(error, PreviewBudgetExceeded)
                    else 'image_knowledge_unavailable' if str(error).startswith(('preview_context_','preview_image_knowledge_'))
                    else 'image_business_error')
            provider_status=getattr(error,'preview_provider_status',None)
            if provider_status:code='preview_image_provider_rejected'
            trace.update(status='failed', error_code=code, error_type=type(error).__name__,
                         runtime_status='failed',provider_http_status=provider_status,
                         result_persistence_status='failed' if trace.get('runtime_completed') else 'pending')
            main.store.finish_run_trace(run_id, 'failed', trace)
            message = ('企业知识未能返回可核验的文件依据，未继续生图；检索可能已消耗额度，没有自动重试。'
                       if code=='image_knowledge_unavailable' else '图片生成或保存未完成；没有自动重试，请保留诊断ID。')
            if provider_status:message=f'图片供应商未接受或完成本次请求（HTTP {provider_status}）；未自动重试，请保留诊断ID。'
            products.set_task(tid, tenant, 'failed', 'failed', message, error_code=code,
                              run_id=run_id, conversation_id=conversation_id)
            products.add_task_activity(tid, tenant, 'failed', 'completed')
