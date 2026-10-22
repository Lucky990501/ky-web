"""Isolated fixture process adapter. No business modules are imported at load time.

Calls candidate API/Worker/MCP unchanged. Only the model Provider is substituted;
Redis, stores, auth, TaskService and MCP transport are never mocked here.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))

PASSWORD = 'SyntheticDeliveryOnly!2026'
EMAILS = ('owner@delivery.invalid', 'other@delivery.invalid',
          'admin@delivery.invalid', 'foreign@delivery.invalid')
PROMPTS = ('写一篇招生文案。', '改成短视频脚本。', '只要口播台词。',
           '第二段自然一些。', '换开头，其余保持不变。')
OUTPUTS = ('完整文案第一版\n' + '这是隔离合成素材，不承诺提分。\n' * 1300,
           '镜头：复习桌前。\n台词：先找到自己的节奏。',
           '先找到自己的节奏。\n我们一起梳理复习安排。\n欢迎了解。',
           '先找到自己的节奏。\n先看看哪里卡住，再一步一步整理。\n欢迎了解。',
           '越临近考试，越要稳住节奏。\n先看看哪里卡住，再一步一步整理。\n欢迎了解。')


def need(ok, label):
    if not ok:
        raise RuntimeError(label)


def child_environment():
    """Never inherit credentials, application settings, PYTHONPATH or proxies."""
    return {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'TMPDIR',
                                      'PYTHONDONTWRITEBYTECODE') if k in os.environ}


def append_event(root, value):
    path = root / 'fixture-calls.jsonl'
    raw = (json.dumps(value, ensure_ascii=False) + '\n').encode()
    need(len(raw) < 256 * 1024 and (not path.exists() or path.stat().st_size < 2 * 1024**2),
         'FIXTURE_EVIDENCE_BOUND')
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'ab') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())


async def mcp_call(url, token, tool, arguments=None):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(url, headers={'Authorization': 'Bearer ' + token},
                                    timeout=10, sse_read_timeout=15) as (reader, writer, _):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            return await session.call_tool(tool, arguments or {})


def fixture_provider(settings, root):
    from app.domain import RuntimeSession, RuntimeTurn, RuntimeStreamEvent
    from app.runtime.codex_provider import CodexRuntimeProvider
    from app.security import RuntimePrincipal, RuntimeTokenIssuer

    class ZeroCostProvider(CodexRuntimeProvider):
        def __init__(self):
            super().__init__(None)
            self.contexts = {}

        async def create_session(self, profile, developer_instructions):
            self._profiles[profile.id] = profile
            thread = 'delivery-fixture-' + str(uuid4())
            self.contexts[thread] = developer_instructions
            return RuntimeSession(thread, profile.id)

        async def resume_session(self, profile, thread_id, **kwargs):
            self._profiles[profile.id] = profile
            return RuntimeSession(thread_id, profile.id)

        def startup_events(self, profile):
            return ()

        async def close(self):
            return None

        async def _stream_provider_turn(self, session, profile, message, *, output_schema=None, visible_deltas=True):
            need(profile.tenant_id == 'tenant-a' and profile.agent_id == 'copywriting-agent',
                 'FIXTURE_PROFILE_SCOPE')
            token = RuntimeTokenIssuer(settings.token_secret).issue(RuntimePrincipal(
                profile.tenant_id, profile.agent_id, profile.id,
                ('enterprise_config:read',), int(time.time()) + 120))
            result = await mcp_call(settings.platform_mcp_url, token, 'enterprise_config_get')
            need(not result.isError, 'MCP_AUTHORIZED_CALL_FAILED')
            context = self.contexts.get(session.thread_id, '')
            rows = read_calls(root)
            append_event(root, {'kind': 'provider_fixture_entered', 'index': len(rows),
                               'thread': session.thread_id, 'context': context,
                               'mcp': 'enterprise_config_get', 'paid_calls': 0})
            if 'INTERRUPT_AFTER_FIXTURE_ENTRY' in message:
                await asyncio.Event().wait()  # killed by orchestrator, never a paid request
            need(len(rows) < len(OUTPUTS), 'UNEXPECTED_FIXTURE_PROVIDER_INVOCATION')
            text = OUTPUTS[len(rows)]
            yield RuntimeStreamEvent.activity('tool_running', 'completed')
            if visible_deltas:
                for offset in range(0, len(text), 2048):
                    yield RuntimeStreamEvent.visible_delta(text[offset:offset + 2048])
            yield RuntimeStreamEvent.completed(RuntimeTurn(session.thread_id, text))

    return ZeroCostProvider()


def read_calls(root):
    p = Path(root) / 'fixture-calls.jsonl'
    if not p.exists():
        return []
    need(p.stat().st_size <= 3 * 1024**2, 'FIXTURE_EVIDENCE_BOUND')
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()]


def configure(project, config_path):
    project = Path(project).resolve(strict=True)
    config = json.loads(Path(config_path).read_text())
    root = Path(config['root'])
    need(sys.platform == 'linux' and os.getuid() != 0, 'NATIVE_NONROOT_REQUIRED')
    need(project.is_relative_to(root) and project.name == 'enterprise_agent_poc', 'ARTIFACT_ROOT_REQUIRED')
    need(not (project / '.env').exists() and not (project.parent / '.env').exists(), 'NO_ENV_FILE')
    # Candidate Python is imported only after the external pinned driver verified
    # and extracted its immutable artifact inside the isolated namespace.
    sys.path.insert(0, str(project))
    from scripts.stage2_isolation import bootstrap
    with redirect_stdout(io.StringIO()):
        config, settings = bootstrap(config_path)
    need(settings.model_provider_id == 'zero-cost-delivery-fixture', 'FIXTURE_PROVIDER_REQUIRED')
    return config, settings


def role_main(role, project, config_path, expected=None, phase=None):
    config, settings = configure(project, config_path)
    root = Path(config['root'])
    from scripts.stage2_isolation import assert_worker_isolated
    def gate():
        assert_worker_isolated(settings, root, config['snapshot'])
    gate()
    from app.store import POCStore
    from app.product_store import ProductStore
    store = POCStore(settings.database_url)
    product = ProductStore(store)
    if role == 'checks':
        from test_delivery_checks import run_phase
        print(json.dumps(run_phase(phase, config, settings)))
        return
    if role == 'provision':
        from scripts.migrate import up, state, migration_records
        from app.auth import hash_password
        with redirect_stdout(io.StringIO()):
            need(up(store) == 0, 'MIGRATION_FAILED')
        files, applied = state(store)
        need(len(files) == len(applied) and all(x['compatibility_status'] != 'CHECKSUM_MISMATCH'
             for x in migration_records(files, applied)), 'MIGRATION_LEDGER_MISMATCH')
        store.seed_demo_data()  # existing explicitly synthetic tenant-a / tenant-b fixture
        product.initialize()
        for email, tenant, role_name in zip(EMAILS, ('tenant-a', 'tenant-a', 'tenant-a', 'tenant-b'),
                                           ('member', 'member', 'enterprise_admin', 'member')):
            product.create_user(tenant, email, hash_password(PASSWORD), 'Synthetic delivery fixture', role_name)
        print(json.dumps({'provisioned': True, 'migration_versions': [x['version'] for x in files]}))
        return
    if role == 'readback':
        # No initialize, DDL, bootstrap or writes in the old-reader process.
        expected_data = json.loads(Path(expected).read_text())
        for item in expected_data:
            task = product.task(item['task_id'], 'tenant-a', item['user_id'])
            need(task and hashlib.sha256(task['final_response'].encode()).hexdigest() == item['sha256'],
                 'PREDECESSOR_FULL_TEXT_READBACK_FAILED')
            detail = product.conversation_detail('tenant-a', item['user_id'], item['conversation_id'])
            need(any(m['id'] == item['assistant_message_id'] and hashlib.sha256(m['content'].encode()).hexdigest()
                     == item['sha256'] for m in detail['messages']), 'PREDECESSOR_HISTORY_READBACK_FAILED')
        print(json.dumps({'old_reader': 'PASS', 'records': len(expected_data)}))
        return
    if role == 'mcp':
        from app.platform_mcp.server import create_mcp
        create_mcp().run(transport='streamable-http')
        return
    from app import main
    main.task_service.pre_execute_guard = gate
    if role == 'worker':
        # Preserve the actual TaskService, AgentService, resolver and lifecycle;
        # replace only the Provider used by that AgentService.
        provider = fixture_provider(settings, root)
        main.agents._runtime = provider
        # RuntimeProvider attribute must be checked against the frozen code.
        need(main.agents._runtime is provider, 'PROVIDER_ADAPTER_NOT_CONNECTED')
        main.runtime = provider
        from app.worker import run
        asyncio.run(run())
    elif role == 'api':
        import uvicorn
        uvicorn.run(main.app, host='127.0.0.1', port=config['api_port'], log_level='warning')
    else:
        raise RuntimeError('UNKNOWN_FIXTURE_ROLE')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=('provision', 'api', 'mcp', 'worker', 'readback', 'checks'), required=True)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--expected', type=Path)
    parser.add_argument('--phase', choices=('five', 'cancel', 'cancel-check', 'interrupt', 'unknown-check'))
    args = parser.parse_args()
    try:
        role_main(args.role, args.project, args.config, args.expected, args.phase)
    except Exception as exc:
        print(json.dumps({'status': 'FAILED', 'exception_class': type(exc).__name__}), flush=True)
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
