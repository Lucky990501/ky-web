"""Private DB/ASGI, storage and multipart Stubs; never paid Provider proof."""
import asyncio
import base64
import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_chat_reference_image import chat_image_env, picture
from app.image_request_contract import ImageRequestError
from app.platform_mcp.service import PlatformMCPService
from app.product_store import ProductStore
from app.security import RuntimeTokenIssuer, RuntimePrincipal
from app.storage import storage_provider
from app.store import POCStore
from app import task_reference_images as refs
from app.tool_dependencies import reference_images_dependency, attempt_observation, resolve_dependencies, fingerprint


def uploaded(env, count=2, *, distinct=False):
    client, store, product, settings, owner = env
    raw = picture()
    source_bytes = [picture(size=(16+i, 12+i)) if distinct else raw for i in range(count)]
    ids = [client.post('/api/v1/chat-images', files={'file': (f'input-{i}.png', source_bytes[i], 'image/png')}).json()['id'] for i in range(count)]
    return ids, {identifier: hashlib.sha256(content).hexdigest() for identifier, content in zip(ids, source_bytes)}, source_bytes if distinct else raw


def task_for(env, count=2, *, distinct=False):
    ids, hashes, raw = uploaded(env, count, distinct=distinct)
    _, _, product, _, owner = env
    task = product.create_task('tenant-a', owner['id'], 'image-agent', 'combine current references', None,
        attachment_ids=ids, attachment_purposes=[f'用途{i+1}' for i in range(count)], _attachment_hashes=hashes)
    return task, ids, raw


def fixture_capability(monkeypatch, settings, field='image'):
    # Test double explicitly NOT an adopted capability or real endpoint proof.
    monkeypatch.setattr(refs, 'VERIFIED_MULTI_REFERENCE_CONTRACTS', ({
        'provider': settings.image_provider_id, 'model': settings.image_model_id,
        'endpoint': refs.EDIT_ENDPOINT, 'status': 'VERIFIED', 'file_field': field,
        'max_inputs': 5, 'evidence_sha256': 'a'*64},))


@pytest.mark.parametrize('count', [2, 3, 4, 5])
def test_ordered_owner_binding_private_task_api_history_and_restart(chat_image_env, count):
    client, store, product, _, owner = chat_image_env
    task, ids, _ = task_for(chat_image_env, count)
    bindings = product.task_chat_image_attachments(task['id'], 'tenant-a')
    assert [r['id'] for r in bindings] == ids
    assert [r['position'] for r in bindings] == list(range(count))
    with pytest.raises(ValueError, match='scalar_consumer'):
        product.task_chat_image_attachment(task['id'], 'tenant-a')
    with store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM chat_image_attachments WHERE task_id IS NOT NULL').fetchone()['n'] == 0
    task_view = client.get('/api/v1/tasks/'+task['id']).json()
    assert [a['id'] for a in task_view['attachments']] == ids and task_view['generations'] == []
    assert task_view['reference_input_contract'] == refs.CONTRACT
    assert 'storage_key' not in json.dumps(task_view['attachments'])
    conversation = str(uuid4())
    store.save_conversation(conversation, 'tenant-a', 'image-agent', 'profile-image', 'thread-fixture', 'test-runtime')
    product.attach_conversation(conversation, owner['id'], 'input history')
    product.set_task(task['id'], 'tenant-a', 'running', 'starting_runtime', 'fixture', conversation_id=conversation)
    product.add_message(conversation, 'user', task['input_text'], message_id=f"task:{task['id']}:user")
    detail = product.conversation_detail('tenant-a', owner['id'], conversation)
    assert [a['id'] for a in detail['messages'][0]['attachments']] == ids
    fresh = ProductStore(POCStore(store.database_path))  # no initialize or migration
    assert [a['id'] for a in fresh.task(task['id'], 'tenant-a', owner['id'])['attachments']] == ids
    assert product.task(task['id'], 'tenant-a', 'other-user') is None
    assert product.task(task['id'], 'tenant-b', owner['id']) is None


def test_default_api_blocks_before_task_queue_or_any_paid_execution(chat_image_env):
    client, store, _, _, _ = chat_image_env
    ids, _, _ = uploaded(chat_image_env)
    with store.connection() as conn:
        before = conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n']
    response = client.post('/api/v1/agents/image-agent/runs', json={
        'message': 'use both inputs', 'attachments': [{'type': 'image', 'id': i, 'purpose': 'input'} for i in ids]})
    assert response.status_code == 409
    assert 'multi_reference_provider_not_verified' in response.text
    with store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n'] == before


def test_asgi_multi_contract_preserves_all_inputs_under_explicit_fixture_capability(chat_image_env, monkeypatch):
    client, _, product, settings, _ = chat_image_env
    fixture_capability(monkeypatch, settings)
    ids, _, _ = uploaded(chat_image_env, 5)
    payload = {'message': 'combine inputs', 'attachments': [{'type': 'image', 'id': identifier, 'purpose': str(i)} for i, identifier in enumerate(ids)]}
    response = client.post('/api/v1/agents/image-agent/runs', json=payload)
    assert response.status_code == 202
    task_id = response.json()['id']
    assert [r['id'] for r in product.task_chat_image_attachments(task_id, 'tenant-a')] == ids
    repeated = client.post('/api/v1/agents/image-agent/runs', json=payload)
    assert repeated.status_code == 202 and repeated.json()['id'] == task_id


@pytest.mark.parametrize('damage', ['duplicate', 'too_many', 'foreign', 'missing_hash', 'wrong_agent'])
def test_atomic_binding_rejects_invalid_selection_without_partial_task_or_credit(chat_image_env, damage):
    _, store, product, _, owner = chat_image_env
    ids, hashes, _ = uploaded(chat_image_env, 2)
    agent = 'image-agent'
    if damage == 'duplicate': ids = [ids[0], ids[0]]
    if damage == 'too_many': ids = [str(uuid4()) for _ in range(6)]
    if damage == 'foreign': ids[-1] = str(uuid4())
    if damage == 'missing_hash': hashes = {}
    if damage == 'wrong_agent': agent = 'copywriting-agent'
    with store.connection() as conn:
        before = conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n']
        credit = conn.execute("SELECT balance FROM credit_accounts WHERE tenant_id='tenant-a'").fetchone()['balance']
    with pytest.raises(ValueError):
        product.create_task('tenant-a', owner['id'], agent, 'input', None, attachment_ids=ids, _attachment_hashes=hashes)
    with store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n'] == before
        assert conn.execute('SELECT count(*) AS n FROM task_reference_images').fetchone()['n'] == 0
        assert conn.execute("SELECT balance FROM credit_accounts WHERE tenant_id='tenant-a'").fetchone()['balance'] == credit


def test_parallel_claim_is_single_use_and_cancel_retains_all_bindings(chat_image_env):
    _, store, product, _, owner = chat_image_env
    ids, hashes, _ = uploaded(chat_image_env, 3)
    def claim(_):
        try:
            return product.create_task('tenant-a', owner['id'], 'image-agent', 'input', None,
                attachment_ids=ids, _attachment_hashes=hashes)['id']
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    task_id = next(i for i in results if i)
    assert len(set(results)) == 1
    with store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n'] == 1
    product.cancel_task(task_id, 'tenant-a', owner['id'])
    assert product.task(task_id, 'tenant-a', owner['id'])['status'] == 'cancelled'
    assert len(product.task_chat_image_attachments(task_id, 'tenant-a')) == 3
    assert product.remove_chat_image_attachment('tenant-a', owner['id'], ids[0]) is None
    with store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM credit_transactions').fetchone()['n'] == 0


def test_identity_trigger_rejects_mutation_and_delete(chat_image_env):
    _, store, _, _, _ = chat_image_env
    task, _, _ = task_for(chat_image_env)
    for sql in ('UPDATE task_reference_images SET purpose=\'changed\' WHERE task_id=?',
                'UPDATE task_reference_images SET tenant_id=\'tenant-b\' WHERE task_id=?',
                'DELETE FROM task_reference_images WHERE task_id=?'):
        with pytest.raises(sqlite3.IntegrityError):
            with store.connection() as conn:
                conn.execute(sql, (task['id'],))


def test_null_dispatch_identity_cannot_bypass_state_trigger(chat_image_env):
    _, store, product, _, _ = chat_image_env
    task, _, _ = task_for(chat_image_env)
    product.set_task(task['id'], 'tenant-a', 'running', 'generating', 'fixture')
    refs.claim_dispatch(store, task['id'], 'tenant-a', 'a'*64)
    with pytest.raises(sqlite3.IntegrityError):
        with store.connection() as conn:
            conn.execute("UPDATE task_reference_images SET dispatch_state='completed',dispatch_fingerprint=NULL,result_json='{}' WHERE task_id=? AND position=0", (task['id'],))
    assert product.task(task['id'], 'tenant-a', task['user_id'])['reference_dispatch_state'] == 'submitted'


def test_legacy_single_upload_still_reads_without_backfill(chat_image_env):
    _, store, product, _, owner = chat_image_env
    ids, _, _ = uploaded(chat_image_env, 1)
    task = product.create_task('tenant-a', owner['id'], 'image-agent', 'single', None, attachment_ids=ids)
    assert product.task_chat_image_attachment(task['id'], 'tenant-a')['id'] == ids[0]
    with store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM task_reference_images').fetchone()['n'] == 0


@pytest.mark.parametrize('foreign_tenant', [False, True])
def test_other_real_owner_cannot_claim_any_multi_upload(chat_image_env, foreign_tenant):
    _, store, product, _, _ = chat_image_env
    ids, hashes, _ = uploaded(chat_image_env)
    tenant = 'tenant-b' if foreign_tenant else 'tenant-a'
    if foreign_tenant:
        product.create_user(tenant, 'multi-other@test.invalid', 'fixture-hash-only', 'Fixture', 'member')
        other = product.user_by_email('multi-other@test.invalid')
    else:
        other = product.user_by_email('image-other@test.invalid')
    with pytest.raises(ValueError):
        product.create_task(tenant, other['id'], 'image-agent', 'input', None, attachment_ids=ids, _attachment_hashes=hashes)
    with store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM task_reference_images').fetchone()['n'] == 0


def test_unmigrated_pg_fails_closed_without_runtime_ddl():
    calls = []
    class Conn:
        def execute(self, sql, *_):
            calls.append(sql)
            return SimpleNamespace(fetchone=lambda: {'name': None})
    with pytest.raises(ValueError, match='SCHEMA_NOT_INSTALLED'):
        refs.bind(Conn(), True, 'task', 'tenant', 'user', [('a', 0, 'a'), ('b', 1, 'b')], {})
    assert calls == ["SELECT to_regclass('task_reference_images') AS name"]


def gateway(env, monkeypatch, *, supported=True, timeout=False, count=3, distinct=False):
    _, store, product, settings, _ = env
    task, ids, raw = task_for(env, count, distinct=distinct)
    product.set_task(task['id'], 'tenant-a', 'running', 'generating', 'fixture')
    if supported:
        fixture_capability(monkeypatch, settings)
    issuer = RuntimeTokenIssuer('isolated-multi-reference-scope-secret')
    token = issuer.issue(RuntimePrincipal('tenant-a', 'image-agent', 'fixture-profile', ('image:generate',), int(time.time())+120))
    scope = 'runtime:' + issuer.issue_task_scope('tenant-a', task['id'])
    calls = []
    class Client:
        def __init__(self, **_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            if timeout: raise TimeoutError('isolated response lost')
            return SimpleNamespace(status_code=200, headers={}, json=lambda: {'data': [{'b64_json': base64.b64encode(raw[0] if distinct else raw).decode()}]})
    monkeypatch.setenv(settings.image_api_key_env, 'isolated-placeholder-not-a-real-key')
    monkeypatch.setattr('httpx.AsyncClient', Client)
    args = (token, 'combine current inputs', [], '1:1')
    return PlatformMCPService(store, issuer, settings), args, scope, calls, task, ids, raw


@pytest.mark.parametrize('count', [2, 3, 4, 5])
def test_every_reference_is_forwarded_in_order_and_duplicate_survives_mcp_restart(chat_image_env, monkeypatch, count):
    service, args, scope, calls, task, ids, raw = gateway(chat_image_env, monkeypatch, count=count)
    first = asyncio.run(service.image_generation(*args, execution_scope=scope))
    assert len(calls) == 1
    url, request = calls[0]
    assert url == refs.EDIT_ENDPOINT and request['data']['n'] == '1'
    assert [field for field, _ in request['files']] == ['image']*count
    assert [file[1] for _, file in request['files']] == [raw]*count
    assert [file[0] for _, file in request['files']] == [f'input-{i}.png' for i in range(count)]
    assert all(f'用途{i+1}' in request['data']['prompt'] for i in range(count))
    import httpx
    from email.parser import BytesParser
    from email.policy import default
    encoded = httpx.Request('POST', url, **request)
    body = encoded.read()
    multipart = BytesParser(policy=default).parsebytes(('Content-Type: '+encoded.headers['content-type']+'\r\n\r\n').encode()+body)
    parts = [p for p in multipart.iter_parts() if p.get_param('name', header='content-disposition') == 'image']
    assert [p.get_filename() for p in parts] == [f'input-{i}.png' for i in range(count)]
    assert [p.get_payload(decode=True) for p in parts] == [raw]*count
    assert first['results'][0]['reference_images_used'] == ids and len(first['results']) == 1
    fresh = PlatformMCPService(service._store, service._tokens, service._settings)
    again = asyncio.run(fresh.image_generation(*args, execution_scope=scope))
    assert again['results'] == first['results'] and len(calls) == 1
    assert again['_tool_dependency']['replayed_from_durable_result'] is True
    assert chat_image_env[2].task(task['id'], 'tenant-a', task['user_id'])['reference_dispatch_state'] == 'completed'


@pytest.mark.parametrize('damage', ['unsupported', 'wrong_scope', 'missing_task', 'foreign_tenant', 'subset', 'reordered', 'changed_bytes', 'cancelled'])
def test_provider_never_called_when_reference_contract_invalid(chat_image_env, monkeypatch, damage):
    service, args, scope, calls, task, ids, _ = gateway(chat_image_env, monkeypatch, supported=damage != 'unsupported')
    kwargs = {'execution_scope': scope}
    if damage == 'wrong_scope': kwargs['execution_scope'] = 'runtime:invalid.signature'
    if damage == 'missing_task': kwargs['execution_scope'] = 'runtime:'+service._tokens.issue_task_scope('tenant-a', str(uuid4()))
    if damage == 'foreign_tenant': kwargs['execution_scope'] = 'runtime:'+service._tokens.issue_task_scope('tenant-b', task['id'])
    if damage == 'subset': kwargs['reference_images'] = ids[:1]
    if damage == 'reordered': kwargs['reference_images'] = ids[::-1]
    if damage == 'changed_bytes':
        record = chat_image_env[2].task_chat_image_attachments(task['id'], 'tenant-a')[0]
        storage_provider(service._settings).put(record['storage_key'], picture('PNG', (14, 10)), 'image/png')
    if damage == 'cancelled': chat_image_env[2].cancel_task(task['id'], 'tenant-a', task['user_id'])
    with pytest.raises(Exception):
        asyncio.run(service.image_generation(*args, **kwargs))
    assert calls == []
    with service._store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM generations').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM credit_transactions').fetchone()['n'] == 0


def test_response_lost_unknown_is_durable_and_cannot_auto_retry(chat_image_env, monkeypatch):
    service, args, scope, calls, task, _, _ = gateway(chat_image_env, monkeypatch, timeout=True)
    with pytest.raises(ImageRequestError) as failure: asyncio.run(service.image_generation(*args, execution_scope=scope))
    assert failure.value.provider_invoked is True
    assert failure.value.tool_dependency['image_dependency']['task_id'] == task['id']
    assert chat_image_env[2].task(task['id'], 'tenant-a', task['user_id'])['reference_dispatch_state'] == 'unknown'
    with pytest.raises(ImageRequestError, match='禁止自动重传'):
        asyncio.run(PlatformMCPService(service._store, service._tokens, service._settings).image_generation(*args, execution_scope=scope))
    assert len(calls) == 1


def test_committed_intent_without_result_blocks_second_worker(chat_image_env):
    _, store, product, _, _ = chat_image_env
    task, _, _ = task_for(chat_image_env)
    product.set_task(task['id'], 'tenant-a', 'running', 'generating', 'fixture')
    assert refs.claim_dispatch(store, task['id'], 'tenant-a', 'a'*64) is None
    with pytest.raises(ValueError, match='unknown_or_consumed'):
        refs.claim_dispatch(POCStore(store.database_path), task['id'], 'tenant-a', 'a'*64)


def test_validation_retry_keeps_whole_v2_manifest_without_paid_retry(chat_image_env, monkeypatch):
    service, args, scope, calls, _, ids, _ = gateway(chat_image_env, monkeypatch)
    from app.tool_dependencies import ToolInputValidationError, input_failure
    initial = {'prompt': args[1], 'references': [], 'aspect_ratio': '3:4'}
    with pytest.raises(ToolInputValidationError) as rejected:
        asyncio.run(service.image_generation(args[0], args[1], [], '3:4', execution_scope=scope))
    assert calls == []
    receipt = rejected.value.retry_receipt
    repaired = {**initial, 'aspect_ratio': '1:1', 'retry_of': receipt['retry_of']}
    result = asyncio.run(service.image_generation(args[0], args[1], [], '1:1', execution_scope=scope, retry_of=receipt['retry_of']))
    failed = input_failure(initial, rejected.value, receipt)
    observations = [
        {'status': 'failed', 'server': 'platform', 'tool': 'image_generation', **attempt_observation(initial, {'structuredContent': failed},
          scope=scope, tool_call_id='initial', server='platform', tool='image_generation')},
        {'status': 'completed', 'server': 'platform', 'tool': 'image_generation', **attempt_observation(repaired, {'structuredContent': result},
          scope=scope, tool_call_id='repair', server='platform', tool='image_generation')},
    ]
    _, groups, required = resolve_dependencies(observations)
    assert len(groups) == 1 and required['image_generation']['satisfied'] is True
    assert result['results'][0]['reference_images_used'] == ids and len(calls) == 1


def test_unverified_provider_failure_retains_current_task_dependency_not_success(chat_image_env, monkeypatch):
    service, args, scope, calls, task, _, _ = gateway(chat_image_env, monkeypatch, supported=False)
    with pytest.raises(ImageRequestError) as rejected:
        asyncio.run(service.image_generation(*args, execution_scope=scope))
    assert rejected.value.tool_dependency['image_dependency']['task_id'] == task['id']
    assert rejected.value.tool_dependency['provider_invoked'] is False and calls == []


def test_five_distinct_original_images_are_not_replaced_by_copies_of_first(chat_image_env, monkeypatch):
    service, args, scope, calls, _, ids, original_bytes = gateway(chat_image_env, monkeypatch, count=5, distinct=True)
    assert len(set(original_bytes)) == 5
    result = asyncio.run(service.image_generation(*args, execution_scope=scope))
    assert [file[1] for _, file in calls[0][1]['files']] == original_bytes
    assert result['results'][0]['reference_images_used'] == ids and len(calls) == 1


def test_worker_rechecks_unverified_capability_before_any_model_turn(chat_image_env):
    from app.product_service import TaskService
    _, store, product, settings, owner = chat_image_env
    task, _, _ = task_for(chat_image_env)
    class Agents:
        _settings = settings
        _store = store
        calls = 0
        async def run(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError('must not start model')
    agents = Agents()
    asyncio.run(TaskService(product, agents).execute(task))
    assert agents.calls == 0
    view = product.task(task['id'], 'tenant-a', owner['id'])
    assert view['status'] == 'failed' and view['error_code'] == 'multi_reference_provider_not_verified'
    assert view['run_id'] is None and len(view['attachments']) == 2
    with store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM credit_transactions').fetchone()['n'] == 0


@pytest.mark.parametrize('damage', ['none', 'missing_input', 'order', 'manifest_hash', 'group_hash', 'wrong_output_count'])
def test_v2_dependency_proves_all_ordered_inputs_and_one_output(damage):
    inputs = [{'id': str(uuid4()), 'position': i, 'purpose': f'图{i+1}', 'sha256': str(i)*64} for i in range(3)]
    proof = reference_images_dependency('tenant-a', 'task-current', 'user-current', inputs)
    args = {'prompt': 'combine inputs', 'references': [], 'aspect_ratio': '1:1'}
    item = {'storage_key': 'generated/tenant-a/result.png', 'format': 'png', 'mime_type': 'image/png', 'width': 16, 'height': 12,
            'reference_images_used': [r['id'] for r in inputs], 'reference_manifest_sha256': proof['inputs_sha256']}
    payload = {'results': [item], '_tool_dependency': {'contract': 'required-tool-input-retry-v1.1', 'status': 'completed',
               'provider_invoked': True, 'image_dependency': proof, 'request_fingerprint': fingerprint(args)}}
    if damage == 'missing_input': item['reference_images_used'].pop()
    if damage == 'order': item['reference_images_used'].reverse()
    if damage == 'manifest_hash': item['reference_manifest_sha256'] = 'a'*64
    if damage == 'group_hash': proof['group_id'] = 'a'*64
    if damage == 'wrong_output_count': payload['results'].append(dict(item))
    observation = {'status': 'completed', 'server': 'platform', 'tool': 'image_generation', **attempt_observation(args, {'structuredContent': payload},
        scope='signed-current-task', tool_call_id='call-current', server='platform', tool='image_generation')}
    _, _, required = resolve_dependencies([observation])
    assert required['image_generation']['satisfied'] is (damage == 'none')


def test_migration_remains_a_proposal_and_015_unchanged_by_mapping():
    root = Path(__file__).resolve().parents[1]
    assert not list((root/'migrations/postgres').glob('017*'))
    proposal = (root/'deploy/migration_proposals/017_task_reference_images.sql').read_text()
    assert 'DEFERRABLE INITIALLY DEFERRED' in proposal and 'FOR UPDATE' in proposal
    assert 'ALTER TABLE chat_image_attachments' not in proposal
    assert 'task_id TEXT UNIQUE' in (root/'migrations/postgres/015_chat_image_attachments.sql').read_text()


@pytest.mark.parametrize('resume', [False, True])
def test_actual_task_service_mcp_sdk_persistence_sse_and_no_rebilling(chat_image_env, monkeypatch, resume):
    from app.domain import RuntimeSession
    from app.product_service import TaskService
    from app.service import AgentService
    from app.runtime.codex_provider import CodexRuntimeProvider
    from app.platform_mcp import server
    from openai_codex.generated.v2_all import McpToolCallResult
    client, store, product, settings, owner = chat_image_env
    service, args, scope, calls, task, ids, _ = gateway(chat_image_env, monkeypatch)
    monkeypatch.setattr(server, 'service', service)
    monkeypatch.setattr(server, '_bearer_from_context', lambda _: args[0])
    monkeypatch.setattr(server, '_execution_scope_from_context', lambda *_: scope)
    native = server.create_mcp()
    class Runtime:
        turns = 0
        task_scopes = []
        async def create_session(self, profile, *_, **options):
            self.task_scopes.append(options['task_id'])
            return RuntimeSession('thread-fixture-'+task['id'], profile.id)
        async def resume_session(self, profile, thread_id, **options):
            self.task_scopes.append(options['task_id'])
            return RuntimeSession(thread_id, profile.id)
        async def run_turn(self, session, message):
            self.turns += 1
            assert all(identifier in message for identifier in ids)
            arguments = {'prompt': args[1], 'references': [], 'aspect_ratio': '1:1'}
            content, structured = await native.call_tool('image_generation', arguments)
            result = McpToolCallResult(content=[r.model_dump() for r in content], structured_content=structured)
            item = SimpleNamespace(id='call-multi', server='platform', tool='image_generation', arguments=arguments,
                                   result=result, status='completed', error=None)
            turn = SimpleNamespace(items=[item], usage=None, final_response='fixture result only', status='completed',
                                   error=None, duration_ms=1, turn_id='fixture-turn')
            return CodexRuntimeProvider(None)._runtime_turn_from_result(session, SimpleNamespace(), turn)
    runtime = Runtime()
    agents = AgentService(store, runtime, settings)
    if resume:
        conversation = str(uuid4())
        profile = agents.profile_for('tenant-a', 'image-agent')
        store.save_conversation(conversation, 'tenant-a', 'image-agent', profile.id, 'historical-thread', profile.runtime_version)
        product.attach_conversation(conversation, owner['id'], 'resume fixture')
        product.set_task(task['id'], 'tenant-a', 'running', 'starting_runtime', 'fixture', conversation_id=conversation)
        task = product.task_for_worker(task['id'])
    worker = TaskService(product, agents)
    asyncio.run(worker.execute(task))
    saved = product.task_for_worker(task['id'])
    assert saved['status'] == 'completed', saved
    trace = store.run_trace(saved['run_id'], 'tenant-a')
    assert trace['payload']['logical_tool_dependencies'][0]['requirement_identity']['inputs'][0]['id'] == ids[0]
    view = product.task(task['id'], 'tenant-a', owner['id'])
    assert len(view['generations']) == 1 and view['generations'][0]['reference_images_used'] == ids
    assert [a['id'] for a in view['attachments']] == ids
    sse = client.get('/api/v1/tasks/'+task['id']+'/events').text
    terminal = next(json.loads(block.split('data: ', 1)[1]) for block in sse.split('\n\n') if block.startswith('event: complete'))
    assert terminal['generations'] == view['generations'] and terminal['attachments'] == view['attachments']
    detail = product.conversation_detail('tenant-a', owner['id'], saved['conversation_id'])
    assert detail['tasks'][0]['attachments'] == view['attachments']
    asyncio.run(worker.execute(saved))
    assert runtime.turns == len(calls) == 1 and runtime.task_scopes == [task['id']]
    assert worker._persist_result(saved, trace)['replayed'] is True
    with store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM credit_transactions WHERE task_id=?', (task['id'],)).fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) AS n FROM generations WHERE task_id=?', (task['id'],)).fetchone()['n'] == 1
