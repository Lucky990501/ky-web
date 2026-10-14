"""Incremental ASGI/TaskService/MCP/SQLite bridge; Provider/SDK response stubs.

Reuse the existing marked, network-denied UI fixture setup. No lifespan,
Native/PG qualification, real Runtime Test PASS, Production or paid execution.
"""
from __future__ import annotations
import asyncio
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

original_stdin = sys.stdin
try:
    sys.stdin = io.StringIO('{"operation":"stop"}\n')
    with contextlib.redirect_stdout(io.StringIO()):
        setup = runpy.run_path(str(Path(__file__).with_name('activity_word_api_bridge.py')))
finally:
    sys.stdin = original_stdin
sys.path.insert(0, str(Path(__file__).parent))

import httpx
from fastapi.testclient import TestClient
from PIL import Image
from openai_codex.generated.v2_all import McpToolCallResult
from app.domain import RuntimeSession, RuntimeTurn
from app.platform_mcp.service import PlatformMCPService
from app.product_service import TaskService
from app.runtime.codex_provider import CodexRuntimeProvider
from app.security import RuntimePrincipal, RuntimeTokenIssuer
from app.service import AgentService
from app.storage import storage_provider
from app.bundled_skills import validate_bundle
from app.wechat_article_format import render_wechat_article
from test_activity_plan_runtime import ScriptedProvider, plan

main, root = setup['main'], setup['ROOT']
# Initialize the existing SQLite control-plane adapter only in the marked
# private fixture. This creates no published Revision or Runtime Test PASS.
main.agent_catalog_control.ensure_initialized()
client = TestClient(main.app)
scenario = 'dual'
current_task = None
http_observations, task_records, checks, tool_rejections = [], [], [], []
runtime_turns = 0
queue_accepts = 0
issuer = RuntimeTokenIssuer('incremental-local-stub-scope-only')
mcp = PlatformMCPService(main.store, issuer, main.settings)
original_logo = None
logo_record = None

def png(size, color=(35, 90, 140, 255)):
    output = io.BytesIO()
    Image.new('RGBA', size, color).save(output, 'PNG')
    return output.getvalue()

class ImageProviderHTTPStub:
    def __init__(self, **_kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): pass
    async def post(self, url, *, headers, json=None, data=None, files=None):
        payload = json or data
        task_calls = [entry for entry in http_observations if entry['task_id'] == current_task['id']]
        status = 500 if scenario == 'partial' and task_calls else 200
        size = (1254, 1254) if scenario == 'mismatch' else tuple(map(int, payload['size'].split('x')))
        http_observations.append({'task_id': current_task['id'], 'scenario': scenario, 'url': url,
                                  'model': payload['model'], 'size': payload['size'], 'n': payload['n'],
                                  'status': status, 'stub': True})
        if status != 200: return httpx.Response(status, json={'error': {'message': 'isolated Provider stub failure'}})
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(png(size)).decode()}]})

httpx.AsyncClient = ImageProviderHTTPStub
os.environ[main.settings.image_api_key_env] = 'isolated-placeholder-no-external-provider'

class ImageRuntimeStub:
    async def create_session(self, profile, developer_instructions, **kwargs):
        return RuntimeSession('isolated-thread-' + str(uuid4()), profile.id)
    async def resume_session(self, profile, thread_id, **kwargs):
        return RuntimeSession(thread_id, profile.id)
    async def run_turn(self, session, message):
        global runtime_turns
        runtime_turns += 1
        token = issuer.issue(RuntimePrincipal(current_task['tenant_id'], 'image-agent', session.profile_id,
                                               ('image:generate',), int(time.time()) + 300))
        bound = main.product_store.task_chat_image_attachment(current_task['id'], current_task['tenant_id'])
        arguments = {'prompt': current_task['input_text'], 'references': [], 'aspect_ratio': '1:1'}
        if bound: arguments['reference_images'] = [bound['id']]
        try:
            result = await mcp.image_generation(token, **arguments, execution_scope='runtime:' + issuer.issue_task_scope(current_task['tenant_id'], current_task['id']))
            status, error = 'completed', None
        except Exception as exc:
            result = {'error_code': getattr(exc, 'code', 'isolated_tool_error'), 'message': str(exc)}
            tool_rejections.append({'task_id': current_task['id'], 'code': result['error_code'], 'message': str(exc)})
            status, error = 'failed', SimpleNamespace(message=str(exc))
        sdk_result = SimpleNamespace(items=[SimpleNamespace(id='isolated-tool-' + str(uuid4()), server='platform',
            tool='image_generation', status=status, arguments=arguments, error=error,
            result=McpToolCallResult(content=[], structured_content=result))], usage=None,
            final_response='Fixture模型回复：已生成全部图片。', status='completed', error=None, duration_ms=1, turn_id='fixture')
        return CodexRuntimeProvider(None)._runtime_turn_from_result(session, SimpleNamespace(), sdk_result)

image_runtime = ImageRuntimeStub()
activity_plan = plan()
activity_plan.update(activity_time='本周六上午9:00', activity_location='社区活动室', target_audience='家长与孩子')
activity_runtime = ScriptedProvider([json.dumps(activity_plan, ensure_ascii=False)] * 4)

async def queue_boundary_only(task):
    global queue_accepts
    queue_accepts += 1
    # Scheduling boundary is explicitly isolated; execute() below invokes the
    # unchanged TaskService/AgentService, not a fake completed API response.

main.task_service.execute = queue_boundary_only

def login(alias):
    if alias == 'admin_a':
        email, password = 'incremental-admin@tenant-a.test', 'IsolatedFixture2026!'
    else:
        _, email, _ = setup['USERS'][alias]
        password = 'IsolatedFixture2026!'
    client.cookies.clear()
    response = client.post('/api/v1/auth/login', json={'account': email, 'password': password})
    assert response.status_code == 200
    return response.json()['user']

main.product_store.create_user('tenant-a', 'incremental-admin@tenant-a.test', main.hash_password('IsolatedFixture2026!'), 'Fixture administrator', 'enterprise_admin')
principal = login('member_a')

def execute(task_id):
    global current_task
    current_task = main.product_store.task_for_worker(task_id)
    assert current_task and current_task['tenant_id'] == principal['tenant_id'] and current_task['user_id'] == principal['id']
    runtime = activity_runtime if current_task['agent_id'] == 'campaign-agent' else image_runtime
    service = TaskService(main.product_store, AgentService(main.store, runtime, main.settings, main.skill_registry.manifest_for_agent))
    before = len(http_observations)
    asyncio.run(service.execute(current_task))
    saved = main.product_store.task(task_id, principal['tenant_id'], principal['id'])
    task_records.append({'id': task_id, 'run_id': saved.get('run_id'), 'scenario': scenario, 'status': saved['status'],
                         'agent_id': saved['agent_id'], 'generation_ids': [item['id'] for item in saved['generations']],
                         'stub_image_attempts': len(http_observations) - before})
    return saved

def receipt(response):
    result = {'status': response.status_code, 'headers': {key: value for key, value in response.headers.items()
              if key in {'content-type', 'content-disposition', 'cache-control', 'x-content-type-options'}}}
    if 'json' in response.headers.get('content-type', ''): result['json'] = response.json()
    elif 'text/event-stream' in response.headers.get('content-type', ''): result['text'] = response.text
    else: result['base64'] = base64.b64encode(response.content).decode()
    return result

def request(command):
    method, path = command['method'], command['path']
    assert method in {'GET', 'POST'} and path.startswith('/api/v1/')
    assert path == '/api/v1/me' or path == '/api/v1/workspace' or path.startswith('/api/v1/agents/') or path.startswith('/api/v1/tasks/') or path.startswith('/api/v1/conversations') or path.startswith('/api/v1/chat-images') or path.startswith('/api/v1/storage/') or path.startswith('/api/v1/enterprise-config')
    files = None
    if command.get('file'): files = {'file': (command['file']['name'], base64.b64decode(command['file']['base64']), command['file']['mime'])}
    response = client.request(method, path, json=command.get('body'), files=files)
    return receipt(response)

def summary():
    with main.store.connection() as conn:
        generations = [dict(row) for row in conn.execute('SELECT id,task_id,tenant_id,user_id,width,height,requested_size FROM generations ORDER BY created_at,id').fetchall()]
        credits = [dict(row) for row in conn.execute('SELECT task_id,amount FROM credit_transactions').fetchall()]
    return {'mode': 'ACTUAL_ASGI_TASKSERVICE_MCP_SQLITE_WITH_PROVIDER_STUBS', 'tasks': task_records,
            'generation_records': generations, 'credit_transactions': credits,
            'stub_image_requests': http_observations,
            'image_runtime_stub_turns': runtime_turns,
            'activity_runtime_stub_turns': len(activity_runtime.options),
            'runtime_stub_turns': runtime_turns + len(activity_runtime.options),
            'tool_rejections': tool_rejections,
            'queue_boundary_accepts': queue_accepts, 'real_provider_calls': 0, 'production_changes': 0,
            'formal_runtime_test': 'NOT_EXECUTED', 'native_or_pg': 'NOT_EXECUTED'}

def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)

emit({'ready': True, 'principal': principal, 'mode': 'NETWORK_DENIED_SYNTHETIC_LOCAL_FIXTURE'})
try:
    for line in sys.stdin:
        command = json.loads(line)
        try:
            operation = command.get('operation', 'request')
            if operation == 'stop': emit({'stopped': True}); break
            if operation == 'login': principal = login(command['alias']); result = principal
            elif operation == 'scenario': scenario = command['name']; result = {'scenario': scenario}
            elif operation == 'execute': result = execute(command['task_id'])
            elif operation == 'duplicate':
                before = len(http_observations); execute(command['task_id']); result = {'extra_stub_calls': len(http_observations)-before}
            elif operation == 'logo':
                principal = login('admin_a')
                image = Image.new('RGBA', (48, 32), (0, 0, 0, 0))
                for x in range(4, 44):
                    for y in range(4, 28): image.putpixel((x, y), (230, 20, 30, 255))
                output = io.BytesIO(); image.save(output, 'PNG'); original_logo = output.getvalue()
                response = client.post('/api/v1/enterprise-config/brand-logo', files={'file': ('Fixture-original-brand.png', original_logo, 'image/png')})
                assert response.status_code in {200, 201}, response.text
                config = client.get('/api/v1/enterprise-config').json(); logo_record = config['brand_logo']
                readback = client.get('/api/v1/enterprise-config/brand-logo')
                assert readback.content == original_logo
                result = {'original_sha256': hashlib.sha256(original_logo).hexdigest(), 'readback_sha256': hashlib.sha256(readback.content).hexdigest(), 'brand_logo': logo_record}
                principal = login('member_a')
            elif operation == 'logo_pixels':
                saved = main.product_store.task(command['task_id'], principal['tenant_id'], principal['id'])
                item = saved['generations'][0]
                body = client.get(item['content_url']).content
                with Image.open(io.BytesIO(body)) as image:
                    margin = max(1, min(image.size)//40)
                    pixel = image.convert('RGBA').getpixel((image.width-48-margin+8, margin+8))
                    result = {'actual_size': image.size, 'original_red_pixel_preserved': pixel == (230,20,30,255)}
            elif operation == 'skill':
                packages = Path(__file__).resolve().parents[1]/'skill_packages'
                entry = next(item for item in validate_bundle(packages) if item['skill_slug']=='wechat-official-account-writing' and item['version']=='1.0.1')
                registered = next(item for item in main.skill_registry.list_skills() if item['slug']=='wechat-official-account-writing')
                result = {'bundle': entry, 'registry': registered, 'live_binding': 'NOT_VERIFIED', 'formal_runtime_test': 'NOT_EXECUTED',
                          'fixture_html': render_wechat_article('# Fixture文章\n\n导语与**暖黄重点**\n\n## 章节\n\n> 引用内容\n\n![原始图片](/api/v1/storage/generated/tenant-a/article.png)')}
            elif operation == 'summary': result = summary()
            else: result = request(command)
            emit(result)
        except Exception as exc: emit({'bridge_error': type(exc).__name__, 'message': str(exc)[:600]})
finally:
    client.close()
