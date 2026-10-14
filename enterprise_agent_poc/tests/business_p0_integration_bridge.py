"""Real private ASGI/TaskService/MCP with an explicit image/model double.

JSON-line bridge, not a deployed server. No lifespan, PG, Redis, native model,
release qualification or paid Provider evidence. Reuses the network-denied,
marked document fixture and never opens shared configuration or credentials.
"""
import asyncio
import base64
import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
import time

saved_stdin = sys.stdin
with contextlib.redirect_stdout(io.StringIO()):
    try:
        sys.stdin = io.StringIO('{"operation":"stop"}\n')
        fixture = runpy.run_path(str(Path(__file__).with_name('activity_word_api_bridge.py')))
    finally:
        sys.stdin = saved_stdin
sys.path.insert(0, str(Path(__file__).parent))

import httpx
from PIL import Image
from fastapi.testclient import TestClient
from app.brand_logo import BrandLogoService
from app.domain import RuntimeSession, RuntimeTurn
from app.platform_mcp.service import PlatformMCPService
from app.product_service import TaskService
from app.security import RuntimePrincipal, RuntimeTokenIssuer
from app.service import AgentService
from app.runtime.codex_provider import CodexRuntimeProvider
from app.storage import storage_provider
from app.wechat_article_format import render_wechat_article
from openai_codex.generated.v2_all import McpToolCallResult
from test_activity_plan_runtime import ScriptedProvider, plan
from test_task_finalization import call
from test_brand_logo_upload import png as logo_png

main = fixture['main']
client = TestClient(main.app)
tenant, email, _ = fixture['USERS']['member_a']
user = main.product_store.user_by_email(email)
product = main.product_store
settings = main.settings
observed = []
checks = []


def check(name, condition):
    assert condition, name
    checks.append(name)


def png(size, color=(15, 70, 120, 255)):
    output = io.BytesIO()
    Image.new('RGBA', size, color).save(output, 'PNG')
    return output.getvalue()


issuer = RuntimeTokenIssuer('isolated-integration-scope-not-a-credential')
token = issuer.issue(RuntimePrincipal(tenant, 'image-agent', 'isolated', ('image:generate',), 4102444800))
mcp = PlatformMCPService(main.store, issuer, settings)
# Dummy non-secret value only; never read any host credential.
import os
os.environ[settings.image_api_key_env] = 'isolated-no-real-provider'
logo = logo_png()
BrandLogoService(storage_provider(settings), product).upload(tenant, 'synthetic-logo.png', logo)


class Images:
    def __init__(self, sizes, statuses=None):
        self.sizes = sizes
        self.statuses = statuses or [200] * len(sizes)
        self.calls = 0

    async def create_session(self, profile, instructions, **kwargs):
        self.task_id = kwargs['task_id']
        return RuntimeSession('fixture-thread-' + self.task_id, profile.id)

    async def resume_session(self, profile, thread_id, **kwargs):
        self.task_id = kwargs['task_id']
        return RuntimeSession(thread_id, profile.id)

    async def run_turn(self, session, message):
        runtime = self
        class StubClient:
            def __init__(self, **_): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass
            async def post(self, url, **kwargs):
                index = runtime.calls
                runtime.calls += 1
                # Never record headers, bearer tokens or base64 bodies.
                observed.append({'task_id': runtime.task_id, 'url': url,
                                 'size': kwargs.get('json', kwargs.get('data', {})).get('size')})
                return httpx.Response(runtime.statuses[index], json={'data': [
                    {'b64_json': base64.b64encode(png(runtime.sizes[index])).decode()}]})
        original = httpx.AsyncClient
        httpx.AsyncClient = StubClient
        try:
            result = await mcp.image_generation(token, 'Tool deliberately omits quantity and ratio', [], '1:1',
                include_brand_logo='Logo' in message,
                execution_scope='runtime:' + issuer.issue_task_scope(tenant, self.task_id))
        finally:
            httpx.AsyncClient = original
        artifacts = CodexRuntimeProvider._image_artifacts(McpToolCallResult(content=[], structured_content=result))
        check('real SDK adapter retains every MCP artifact', len(artifacts) == len(result['results']))
        return RuntimeTurn(session.thread_id, '图片结果', mcp_calls=({
            **call('completed', tool='image_generation'), 'artifact': artifacts[0],
            'artifacts': artifacts, 'image_delivery': result['delivery']},))


def execute(agent, text, runtime, conversation=None):
    task = product.create_task(tenant, user['id'], agent, text, conversation)
    service = TaskService(product, AgentService(main.store, runtime, settings))
    asyncio.run(service.execute(task))
    saved = product.task(task['id'], tenant, user['id'])
    check('actual Task completed: ' + task['id'], saved['status'] == 'completed')
    return saved


with contextlib.redirect_stdout(io.StringIO()):
    normal = execute('image-agent', '制作三折页正反面两张，使用企业Logo', Images([(1536, 1024)] * 2))
    partial = execute('image-agent', '生成两张图片', Images([(1024, 1024)] * 2, [200, 500]))
    mismatch = execute('image-agent', '生成9:16长图', Images([(1024, 1024)]))
    source = plan()
    source.update(activity_time='本周六上午9:00', activity_location='社区活动室', target_audience='家长与孩子')
    activity_runtime = ScriptedProvider([json.dumps(source, ensure_ascii=False)])
    first = execute('campaign-agent', '帮我做一个亲子阅读活动方案，面向家长与孩子', activity_runtime)
    check('missing facts clarify without model call', not activity_runtime.options and first['structured_result'] is None)
    time.sleep(1.05)
    second = execute('campaign-agent', '地点在社区活动室', activity_runtime, first['conversation_id'])
    check('second turn asks only time', '活动时间' in second['final_response'] and '活动地点' not in second['final_response'])
    time.sleep(1.05)
    campaign = execute('campaign-agent', '时间定在本周六上午9:00', activity_runtime, first['conversation_id'])
    check('full plan and three briefs after sufficient facts', campaign['structured_result']['data'] == source and len(campaign['illustration_briefs']) == 3)
    check('one structured model double call across three turns', len(activity_runtime.options) == 1)

login = client.post('/api/v1/auth/login', json={'account': email, 'password': 'IsolatedFixture2026!'})
check('real normal-member login', login.status_code == 200 and login.json()['user']['role'] == 'member')
check('both images persisted separately', len(normal['generations']) == 2)
check('original logo programmatically applied', all(item['brand_logo_applied'] for item in normal['generations']))
check('partial success honestly preserved', len(partial['generations']) == 1 and partial['image_delivery']['status'] == 'partial')
check('actual pixels distinct from request', mismatch['generations'][0]['requested_size'] == '864x1536'
      and mismatch['generations'][0]['width'] == 1024 and mismatch['generations'][0]['dimension_match'] is False)
for saved in [normal, partial, mismatch, campaign]:
    task = client.get('/api/v1/tasks/' + saved['id'])
    check('actual authorized Task API ' + saved['id'], task.status_code == 200)
    stream = client.get('/api/v1/tasks/' + saved['id'] + '/events')
    check('actual SSE terminal ' + saved['id'], 'event: complete' in stream.text)
    detail = client.get('/api/v1/conversations/' + saved['conversation_id']).json()
    assistant = next(item for item in detail['messages'] if item.get('task_id') == saved['id'] and item['role'] == 'assistant')
    if saved is campaign:
        check('real history omits inline structured_result', 'structured_result' not in assistant)
    else:
        check('real history preserves all images ' + saved['id'], len(assistant['generations']) == len(saved['generations']))
        for image in saved['generations']:
            response = client.get(image['content_url'])
            with Image.open(io.BytesIO(response.content)) as actual:
                check('authorized original download and pixels ' + image['id'], response.status_code == 200 and actual.size == (image['width'], image['height']))

def counts():
    with main.store.connection() as conn:
        return {table: conn.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                for table in ('tasks', 'run_traces', 'generations', 'credit_transactions')}


article = render_wechat_article('# 样式衔接验证\n\n完整导语 **强调**\n\n## 正文章节\n\n正文内容\n\n> 真实引用\n\n![原始授权图片](' + normal['generations'][0]['content_url'] + ')')
print(json.dumps({'ready': True, 'mode': 'ACTUAL_PRIVATE_ASGI_SQLITE_TASK_MCP_PROVIDER_DOUBLE',
                  'normal': normal, 'partial': partial, 'mismatch': mismatch, 'campaign': campaign,
                  'rendered_article': article, 'counts': counts(),
                  'checks': checks, 'stub_http_requests': observed, 'real_provider_calls': 0}, ensure_ascii=False), flush=True)
allowed = ('/api/v1/me', '/api/v1/workspace', '/api/v1/agents/', '/api/v1/conversations',
           '/api/v1/tasks/', '/api/v1/storage/', '/api/v1/documents/')
try:
    for line in sys.stdin:
        command = json.loads(line)
        if command.get('operation') == 'stop': break
        if command.get('operation') == 'counts':
            print(json.dumps(counts()), flush=True)
            continue
        method, path = command.get('method', 'GET'), command.get('path', '')
        # No Task POST, uploads, cancellation or arbitrary filesystem/network API.
        if method != 'GET' or not path.startswith(allowed):
            answer = {'status': 403, 'body': base64.b64encode(b'fixture write denied').decode(), 'headers': {}}
        else:
            response = client.get(path)
            answer = {'status': response.status_code, 'body': base64.b64encode(response.content).decode(),
                      'headers': {'content-type': response.headers.get('content-type', 'application/octet-stream')}}
        print(json.dumps(answer), flush=True)
finally:
    client.close()
