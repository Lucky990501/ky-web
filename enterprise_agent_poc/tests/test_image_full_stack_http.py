"""Isolated TCP HTTP -> actual Task/Tool/PG015 -> browser acceptance.

Only the model boundary and external image Gateway response are Fixtures.
Routes, login/session, image tool, SDK extraction, storage, finalization, SSE,
history, authorization and browser rendering are the application implementations.
Not Native/Release validation and not real model acceptance.
"""
import asyncio
import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
import json
import os
from pathlib import Path
import re
import socket
from types import SimpleNamespace
from threading import Thread
import time
from uuid import uuid4

import httpx
from openai_codex.generated.v2_all import McpToolCallResult
from PIL import Image, ImageDraw, ImageFont
import pytest
import uvicorn

from app import main
from app.auth import hash_password
from app.domain import RuntimeSession, RuntimeStreamEvent
from app.platform_mcp.service import PlatformMCPService
from app.product_service import TaskService
from app.runtime.codex_provider import CodexRuntimeProvider
from app.security import RuntimePrincipal, RuntimeTokenIssuer
from app.service import AgentService
from app.skill_registry import SkillRegistry
from app.storage import storage_provider
from scripts import migrate
from test_image_multi_artifact_postgres import pg

pytestmark = pytest.mark.skipif(
    not os.environ.get('IMAGE_FULL_STACK_EVIDENCE_DIR'),
    reason='explicit independent HTTP/browser acceptance directory required',
)
FIXTURE_PASSWORD = 'FixtureOnly!2026'  # Synthetic login only; never an external credential.


def fixture_png(index):
    size = (640, 960) if index % 2 else (1080, 720)
    picture = Image.new('RGB', size, '#eaf1ff' if index % 2 else '#eef8f4')
    draw = ImageDraw.Draw(picture)
    draw.ellipse((size[0]//2, -size[0]//8, size[0]+size[0]//4, size[0]//2), fill='#ccdafa')
    draw.rounded_rectangle((40, size[1]//2, size[0]-40, size[1]-60), radius=20, fill='white')
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 24)
    draw.text((65, size[1]//2+40), f'ISOLATED FIXTURE {index}', font=font, fill='#284d8a')
    draw.text((65, size[1]//2+95), 'Actual HTTP / PostgreSQL', font=font, fill='#52657f')
    draw.text((65, size[1]//2+150), 'Not real model output', font=font, fill='#52657f')
    output = BytesIO()
    picture.save(output, 'PNG')
    return output.getvalue()


class ToolFixtureRuntime:
    def __init__(self, store, cfg, issuer):
        self.profiles, self.turns, self.tool_calls, self.gateway_fixtures = {}, 0, 0, 0
        self.settings, self.store, self.issuer = cfg, store, issuer
        self.tools = PlatformMCPService(store, issuer, cfg)

    async def create_session(self, profile, developer_instructions, **_):
        thread = 'fixture-http-'+uuid4().hex
        self.profiles[thread] = profile
        return RuntimeSession(thread, profile.id)

    async def resume_session(self, profile, thread_id, **_):
        self.profiles[thread_id] = profile
        return RuntimeSession(thread_id, profile.id)

    async def close(self):
        pass

    async def stream_turn(self, session, message):
        match = re.search(r'FIXTURE_IMAGES=([123])', message)
        assert match, 'ONLY_EXPLICIT_IMAGE_FIXTURE_TASKS_ALLOWED'
        count = int(match[1])
        self.turns += 1
        profile = self.profiles[session.thread_id]
        token = self.issuer.issue(RuntimePrincipal(profile.tenant_id, profile.agent_id,
                                  profile.id, ('image:generate',), int(time.time())+300))
        items, keys = [], []
        for index in range(count):
            args = {'prompt': f'HTTP Fixture image {index+1}', 'references': [],
                    'aspect_ratio': '4:5' if index % 2 == 0 else '16:9'}
            yield RuntimeStreamEvent.activity('tool_running', 'started')
            raw = await self.tools.image_generation(token, **args,
                    execution_scope='fixture-http:'+session.thread_id)
            self.tool_calls += 1
            keys.append(raw['results'][0]['storage_key'])
            items.append(SimpleNamespace(id=f'call-{index+1}', server='platform',
                tool='image_generation', arguments=args, status='completed', error=None,
                result=McpToolCallResult(content=[], structured_content=raw)))
            yield RuntimeStreamEvent.activity('tool_running', 'completed')
        # Explicit persistence fault, confined to the fixture's private object root.
        if 'FIXTURE_CORRUPT' in message:
            storage_provider(self.settings).put(keys[0], b'not a valid PNG', 'image/png')
        descriptions = ['正面（封面+封底）', '反面（内页三折）', '补充展示图']
        text = '\n\n'.join(f'{descriptions[index]}\n/api/v1/storage/{key}' for index, key in enumerate(keys))
        text += '\n\n源文件 /opt/fixture/runtime-data/run/workspace/source.png'
        text += '\n参考：[正常业务链接](https://example.invalid/workspace/guide)'
        result = SimpleNamespace(items=items, usage=None, final_response=text,
            status='failed' if 'FIXTURE_RUNTIME_FAILED' in message else 'completed',
            error='Fixture runtime terminal failure' if 'FIXTURE_RUNTIME_FAILED' in message else None,
            duration_ms=1, turn_id='fixture-turn-'+uuid4().hex)
        turn = CodexRuntimeProvider(None)._runtime_turn_from_result(session, SimpleNamespace(), result)
        # Force address boundaries to cross actual persisted delta/SSE events.
        chunks = re.split(r'(?=/api/v1/sto)|(?<=/api/v1/sto)|(?=/opt/)', text)
        for chunk in chunks:
            if chunk:
                yield RuntimeStreamEvent.visible_delta(chunk)
                await asyncio.sleep(.09)
        yield RuntimeStreamEvent.completed(turn)


def test_real_tcp_http_browser_pg015_chain(pg, tmp_path, monkeypatch):
    evidence = Path(os.environ['IMAGE_FULL_STACK_EVIDENCE_DIR']).resolve()
    assert (evidence/'isolated-http.marker').read_text().strip() == 'IMAGE_FULL_STACK_INTEGRATION_V1'
    assert len(migrate.migration_files()) == 15
    cfg = replace(pg.cfg, data_dir=tmp_path/'runtime', image_api_key_env='IMAGE_FULL_STACK_FIXTURE_AUTH')
    monkeypatch.setenv('IMAGE_FULL_STACK_FIXTURE_AUTH', 'synthetic-gateway-boundary-only')
    issuer = RuntimeTokenIssuer('isolated-image-http-fixture-token-secret')
    runtime = ToolFixtureRuntime(pg.store, cfg, issuer)
    monkeypatch.setattr(main, 'settings', cfg)
    monkeypatch.setattr(main, 'runtime', runtime)
    service = TaskService(pg.product, AgentService(pg.store, runtime, cfg))
    monkeypatch.setattr(main, 'task_service', service)
    monkeypatch.setattr(main, 'skill_registry', SkillRegistry(pg.store,
        cfg.data_dir/'skills', Path(__file__).resolve().parents[1]/'skill_packages'))
    for email in ('owner@fixture.test', 'other@fixture.test', 'foreign@fixture.test'):
        actor = pg.product.user_by_email(email)
        assert pg.product.update_password_hash(actor['tenant_id'], actor['id'], hash_password(FIXTURE_PASSWORD))

    class GatewayFixture:
        def __init__(self, **_):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def post(self, url, *, headers, json):
            assert url == 'https://llm-api.net/v1/images/generations'
            assert json['n'] == 1 and json['response_format'] == 'b64_json'
            index = int(json['prompt'].rsplit(' ', 1)[1])
            runtime.gateway_fixtures += 1
            return httpx.Response(200, json={'request_id': 'fixture-'+uuid4().hex,
                'data': [{'b64_json': base64.b64encode(fixture_png(index)).decode('ascii')}]})

    def deny_external_http(*_, **__):
        raise AssertionError('REAL_PROVIDER_HTTP_FORBIDDEN')
    async def async_deny_external_http(*_, **__):
        deny_external_http()
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', deny_external_http)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', async_deny_external_http)
    monkeypatch.setattr(httpx, 'AsyncClient', GatewayFixture)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(main.app, log_level='error', access_log=False,
                                          lifespan='on', timeout_graceful_shutdown=5))
    thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+20
        while not server.started and thread.is_alive() and time.monotonic()<deadline:
            time.sleep(.05)
        assert server.started, 'ISOLATED_APPLICATION_STARTUP_FAILED'
        (evidence/'http-ready.json').write_text(json.dumps({'origin': origin,
            'database': pg.db, 'schema': '001-015', 'fixture': True,
            'assets_sha256': {name: hashlib.sha256((main.STATIC_DIR/name).read_bytes()).hexdigest()
                             for name in ('workbench.js', 'workbench.css')},
            'source_tree': os.environ.get('IMAGE_FULL_STACK_SOURCE_TREE')}))
        print('ISOLATED_HTTP_READY '+origin, flush=True)
        deadline = time.monotonic()+240
        while not (evidence/'http-browser-results.json').exists() and time.monotonic()<deadline:
            time.sleep(.1)
        assert (evidence/'http-browser-results.json').exists(), 'REAL_HTTP_BROWSER_DRIVER_NOT_COMPLETED'
        browser = json.loads((evidence/'http-browser-results.json').read_text())
        assert browser['status'] == 'PASS_ISOLATED_HTTP_BROWSER', browser.get('error')
        task_evidence = []
        for case in browser['tasks']:
            saved = pg.product.task_for_worker(case['task_id'])
            assert saved and saved['user_id'] == pg.actor['id'] and saved['tenant_id'] == 'tenant-a'
            with pg.store.connection() as conn:
                records = [dict(row) for row in conn.execute('SELECT * FROM generations WHERE task_id=? ORDER BY id', (saved['id'],))]
                charges = [dict(row) for row in conn.execute('SELECT id,amount,reason FROM credit_transactions WHERE task_id=?', (saved['id'],))]
                results = [dict(row) for row in conn.execute('SELECT result_json FROM task_results WHERE task_id=?', (saved['id'],))]
            before = (len(records), len(charges))
            if case['expected_status'] == 'completed':
                assert saved['status'] == 'completed' and len(records) == case['expected_images']
                assert len(charges) == 1 and len(results) == 1
                metadata = json.loads(results[0]['result_json'])
                assert metadata['generation_ids'] == case['generation_ids']
                trace = pg.store.run_trace(saved['run_id'], 'tenant-a')
                active_calls = [item for item in trace['payload']['mcp_calls'] if item.get('tool') == 'image_generation' and item.get('status') == 'completed']
                assert len(active_calls) == case['expected_images']
                # Actual queue redelivery and concurrent finalizer replay, not a second POST.
                turns_before = runtime.turns
                with ThreadPoolExecutor(max_workers=2) as pool:
                    replay = list(pool.map(lambda _:service._persist_result(saved, trace), range(2)))
                assert all(item['replayed'] for item in replay)
                asyncio.run(service.execute(saved))
                assert runtime.turns == turns_before
            else:
                assert saved['status'] == 'failed' and not records and not charges and not results
                metadata = None
            with pg.store.connection() as conn:
                after = (conn.execute('SELECT count(*) n FROM generations WHERE task_id=?', (saved['id'],)).fetchone()['n'],
                         conn.execute('SELECT count(*) n FROM credit_transactions WHERE task_id=?', (saved['id'],)).fetchone()['n'])
            assert before == after
            task_evidence.append({**case, 'run_id': saved['run_id'], 'actual_status': saved['status'],
                'generation_rows': records, 'charge_rows': charges, 'result_metadata': metadata,
                'replay_counts_before_after': [before, after]})
        with pg.store.connection() as conn:
            migrations = [dict(row) for row in conn.execute('SELECT version,name,checksum FROM schema_migrations ORDER BY version')]
            pg_version = conn.execute('SHOW server_version').fetchone()['server_version']
            tool_audits = conn.execute("SELECT count(*) n FROM execution_events WHERE event_type='mcp.tool'").fetchone()['n']
        assert len(migrations) == 15 and runtime.tool_calls == runtime.gateway_fixtures
        (evidence/'http-postgres-results.json').write_text(json.dumps({
            'status': 'PASS_ISOLATED_HTTP_POSTGRES_E2E', 'database': pg.db, 'postgres': pg_version,
            'source_tree': os.environ.get('IMAGE_FULL_STACK_SOURCE_TREE'), 'migrations': migrations,
            'tasks': task_evidence, 'actual_image_tool_calls': runtime.tool_calls,
            'mock_gateway_responses': runtime.gateway_fixtures, 'tool_audit_events': tool_audits,
            'model_boundary': 'Explicit ToolFixtureRuntime, no real model execution',
            'real_provider_calls': 0, 'real_image_calls': 0, 'primary_changes': 0, 'production_changes': 0,
        }, ensure_ascii=False, indent=2, default=str)+'\n')
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        assert not thread.is_alive(), 'ISOLATED_HTTP_SERVER_NOT_STOPPED'
