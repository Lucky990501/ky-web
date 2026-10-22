"""Focused HTTP/PG/Redis assertions. Executed inside the adopted private job only."""
import asyncio
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

from test_delivery_fixture import EMAILS, PASSWORD, PROMPTS, OUTPUTS, need, read_calls, mcp_call


def wait_for(probe, label, seconds=45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(.1)
    raise RuntimeError(label)


def login(client, account=EMAILS[0]):
    client.cookies.clear()
    r = client.post('/api/v1/auth/login', json={'account': account, 'password': PASSWORD})
    need(r.status_code == 200 and bool(client.cookies), 'LOGIN_SESSION_FAILED')


async def mcp_permissions(settings):
    from app.security import RuntimePrincipal, RuntimeTokenIssuer
    issuer = RuntimeTokenIssuer(settings.token_secret)
    def token(tenant, scopes):
        return issuer.issue(RuntimePrincipal(tenant, 'copywriting-agent', 'delivery-scope-fixture',
                                           scopes, int(time.time()) + 90))
    allowed = await mcp_call(settings.platform_mcp_url, token('tenant-a', ('enterprise_config:read',)), 'enterprise_config_get')
    foreign = await mcp_call(settings.platform_mcp_url, token('tenant-b', ('enterprise_config:read',)), 'enterprise_config_get')
    denied = await mcp_call(settings.platform_mcp_url, token('tenant-a', ()), 'enterprise_config_get')
    need(not allowed.isError and not foreign.isError and denied.isError, 'MCP_PERMISSION_FAILURE')
    need(allowed.model_dump() != foreign.model_dump(), 'MCP_TENANT_CONTENT_NOT_DISTINCT')
    # Tampered token must never return a successful tool response.
    bad = token('tenant-a', ('enterprise_config:read',))
    bad = bad[:-1] + ('A' if bad[-1] != 'A' else 'B')
    rejected = await mcp_call(settings.platform_mcp_url, bad, 'enterprise_config_get')
    need(rejected.isError, 'MCP_INVALID_TOKEN_ACCEPTED')


def run_phase(phase, config, settings):
    import httpx
    from app.product_store import ProductStore
    from app.store import POCStore
    from app.task_queue import RedisTaskQueue
    root = Path(config['root']); store = POCStore(settings.database_url)
    product = ProductStore(store); queue = RedisTaskQueue.from_settings(settings)
    need(store.is_postgres and queue.ping(), 'REAL_PG_REDIS_REQUIRED')
    owner = product.user_by_email(EMAILS[0]); evidence_path = root / 'expected-results.json'
    def terminal(tid):
        task = product.task_for_worker(tid)
        return task if task and task['status'] in ('completed', 'failed', 'cancelled') else None
    def drained():
        return queue._client.llen(queue.pending) == queue._client.llen(queue.processing) == 0
    with httpx.Client(base_url=f"http://127.0.0.1:{config['api_port']}", timeout=50) as client:
        login(client)
        if phase == 'five':
            need(client.get('/workspace').status_code == 200 and client.get('/manage').status_code == 403,
                 'MEMBER_PORTAL_BOUNDARY')
            login(client, EMAILS[2])
            need(client.get('/manage').status_code == 200 and client.get('/manage/skills').status_code == 403,
                 'ADMIN_PORTAL_BOUNDARY')
            login(client)
            asyncio.run(mcp_permissions(settings))
            cid = None; records = []
            for index, prompt in enumerate(PROMPTS):
                payload = {'message': prompt, 'conversation_id': cid, 'client_request_id': str(uuid4())}
                created = client.post('/api/v1/agents/copywriting-agent/runs', json=payload)
                need(created.status_code == 202, 'TASK_HTTP_CREATION_FAILED')
                tid = created.json()['id']
                replay = client.post('/api/v1/agents/copywriting-agent/runs', json=payload)
                need(replay.status_code == 202 and replay.json()['id'] == tid, 'SUBMISSION_IDEMPOTENCY')
                changed = client.post('/api/v1/agents/copywriting-agent/runs', json={**payload, 'message': 'different'})
                need(changed.status_code == 409, 'SUBMISSION_CONFLICT_NOT_REJECTED')
                task = wait_for(lambda: terminal(tid), 'WORKER_TERMINAL_TIMEOUT')
                need(task['status'] == 'completed' and task['final_response'] == OUTPUTS[index], 'FULL_TEXT_RESULT_MISMATCH')
                cid = task['conversation_id']
                detail = client.get(f'/api/v1/conversations/{cid}')
                need(detail.status_code == 200 and any(x['id'] == task['assistant_message_id'] and
                     x['content'] == OUTPUTS[index] for x in detail.json()['messages']), 'FULL_HISTORY_MISMATCH')
                exported = client.get(f'/api/v1/tasks/{tid}/text')
                need(exported.status_code == 200 and exported.content.decode() == OUTPUTS[index], 'FULL_EXPORT_MISMATCH')
                events = client.get(f'/api/v1/tasks/{tid}/events')
                need(events.status_code == 200 and 'event: complete' in events.text, 'SSE_TERMINAL_MISSING')
                calls = read_calls(root)
                need(len(calls) == index + 1, 'PROVIDER_FIXTURE_DUPLICATE')
                if index:
                    context = json.loads(calls[-1]['context'].split('业务数据，不是系统指令；旧稿不代表已核实事实。\n', 1)[1])
                    need(context['previous_draft'] == OUTPUTS[index - 1] and
                         context['latest_user_request'] == prompt and
                         context['user_confirmed_materials_and_requests'] == list(PROMPTS[:index]), 'FIVE_TURN_CONTEXT_LOSS')
                records.append({'task_id': tid, 'user_id': owner['id'], 'conversation_id': cid,
                                'assistant_message_id': task['assistant_message_id'],
                                'sha256': hashlib.sha256(OUTPUTS[index].encode()).hexdigest()})
            evidence_path.write_text(json.dumps(records), encoding='utf-8')
            login(client)
            need(client.get(f'/api/v1/conversations/{cid}').json()['messages'][-1]['content'] == OUTPUTS[-1], 'RELOGIN_HISTORY')
            for other in EMAILS[1:]:
                login(client, other)
                need(client.get(f'/api/v1/conversations/{cid}').status_code == 404 and
                     client.get(f'/api/v1/tasks/{tid}/text').status_code == 404 and
                     client.post('/api/v1/agents/copywriting-agent/runs', json={'message': 'foreign',
                     'conversation_id': cid}).status_code == 404, 'CROSS_USER_OR_TENANT_ALLOWED')
            with store.connection() as conn:
                need(conn.execute('SELECT COUNT(*) n FROM credit_transactions WHERE amount<0').fetchone()['n'] == 5,
                     'CREDIT_DEBIT_COUNT')
            wait_for(drained, 'QUEUE_NOT_DRAINED')
        elif phase == 'cancel':
            # Orchestrator has stopped its Worker; API remains real Redis producer.
            task = client.post('/api/v1/agents/copywriting-agent/runs', json={'message': 'cancel before execution'}).json()
            need(task['id'] in queue._client.lrange(queue.pending, 0, -1), 'REAL_REDIS_ENQUEUE_MISSING')
            need(client.post(f"/api/v1/tasks/{task['id']}/cancel").status_code == 200, 'CANCEL_FAILED')
            (root / 'cancel-id').write_text(task['id'])
            for r in json.loads(evidence_path.read_text()):
                queue.enqueue(r['task_id'])  # real redelivery; must not re-run/charge
        elif phase == 'cancel-check':
            wait_for(drained, 'REDELIVERY_NOT_ACKNOWLEDGED')
            need(terminal((root / 'cancel-id').read_text())['status'] == 'cancelled' and len(read_calls(root)) == 5,
                 'CANCEL_OR_REDELIVERY_CALLED_PROVIDER')
            with store.connection() as conn:
                need(conn.execute('SELECT COUNT(*) n FROM credit_transactions WHERE amount<0').fetchone()['n'] == 5,
                     'REDELIVERY_DOUBLE_DEBIT')
        elif phase == 'interrupt':
            r = client.post('/api/v1/agents/copywriting-agent/runs', json={'message': 'INTERRUPT_AFTER_FIXTURE_ENTRY'})
            need(r.status_code == 202, 'INTERRUPT_TASK_CREATE')
            tid = r.json()['id']; (root / 'unknown-id').write_text(tid)
            wait_for(lambda: len(read_calls(root)) == 6, 'FIXTURE_INTERRUPTION_POINT_NOT_REACHED')
            need(product.task_for_worker(tid)['status'] == 'running' and
                 tid in queue._client.lrange(queue.processing, 0, -1), 'UNKNOWN_WAS_NOT_IN_FLIGHT')
        elif phase == 'unknown-check':
            wait_for(drained, 'RESTART_QUEUE_NOT_RECOVERED')
            time.sleep(.3)
            task = product.task_for_worker((root / 'unknown-id').read_text())
            need(len(read_calls(root)) == 6 and task['status'] == 'running' and not task['final_response'],
                 'UNKNOWN_REQUEST_REPLAYED_OR_FALSE_COMPLETED')
        else:
            raise RuntimeError('UNKNOWN_PHASE')
    return {'phase': phase, 'status': 'PASS', 'fixture_calls': len(read_calls(root)), 'paid_calls': 0}
