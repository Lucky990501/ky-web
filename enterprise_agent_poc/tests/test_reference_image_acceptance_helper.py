import asyncio
import errno
import io
import json
import logging
import httpx
from dataclasses import replace
from types import SimpleNamespace

import anyio
import pytest
from mcp.types import CallToolResult, TextContent
from PIL import Image

from app.domain import RuntimeSession
from app.storage import OSSStorage, storage_provider
from app.tool_dependencies import RECONSTRUCTION_CONTRACT
from reference_image_legacy_fixture import legacy_post_mcp
from test_run_trace import product_task_fixture
from scripts.reference_image_acceptance import (
    ReferenceImageAcceptanceError, reference_image_runtime_turn, observe_reference_image_runtime,
)


def scenario(tmp_path, monkeypatch, builder=legacy_post_mcp, backend='oss', image_format='PNG', mutate=None):
    data = io.BytesIO()
    Image.new('RGB', (12, 10), (20, 90, 160)).save(data, format=image_format)
    image = data.getvalue()
    suffix = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp', 'GIF': '.gif'}[image_format]
    mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp', 'GIF': 'image/gif'}[image_format]
    key = 'generated/tenant-a/offline-reference' + suffix
    artifact = {'asset_id': 'offline-asset', 'storage_key': key, 'url': '/api/v1/storage/' + key,
                'mime_type': mime, 'format': image_format.lower(), 'requested_size': '1024x1024',
                'actual_size': '12x10', 'width': 12, 'height': 10, 'reference_images_used': ['reference-fixture']}
    payload = {'model': 'gpt-image-2.5-sunburst-c', 'results': [artifact]}
    # Actual MCP envelope, with text fallback contract expected by tool_result_payload.
    native_payload = {**payload, '_tool_dependency': {'contract': RECONSTRUCTION_CONTRACT,
                      'status': 'completed', 'provider_invoked': True}}
    if mutate:
        mutate(native_payload)
    result = CallToolResult(isError=False, structuredContent=native_payload,
                           content=[TextContent(type='text', text=json.dumps(native_payload))])
    observations = {'read_calls': [], 'report': {}, 'turn_returned': False, 'inner': None}
    class FixtureRuntime:
        async def create_session(self, profile, *_args, **_kwargs):
            return RuntimeSession('fixture-thread', profile.id)
        async def resume_session(self, profile, thread_id, **_kwargs):
            return RuntimeSession(thread_id, profile.id)
        async def run_turn(self, session, _message):
            # Real AnyIO TaskGroup provides the original opaque outer-error shape.
            async with anyio.create_task_group():
                try:
                    turn = builder(result, settings=settings, session=session, tenant='tenant-a',
                                   reference={'id': 'reference-fixture'},
                                   args={'prompt': 'offline fixture', 'references': [], 'aspect_ratio': '1:1'},
                                   report=observations['report'])
                except Exception as error:
                    observations['inner'] = error
                    raise
                observations['turn_returned'] = True
                return turn
    runtime = FixtureRuntime()
    store, product, task, _, service = product_task_fixture(tmp_path, monkeypatch, 'image-agent', runtime)
    settings = replace(service._agents._settings, object_storage_provider=backend,
                       oss_access_key_id='FAKE-OSS-ID', oss_access_key_secret='FAKE-OSS-SECRET',
                       oss_endpoint='https://oss.invalid', oss_bucket_name='fixture-only')
    service._agents._settings = settings
    if builder is not legacy_post_mcp:
        runtime.run_turn = observe_reference_image_runtime(
            settings=lambda: settings, report=lambda: observations['report'])(runtime.run_turn)
    class Missing:
        status = 404
        code = 'NoSuchKey'
    class Bucket:
        def get_object(self, requested):
            observations['read_calls'].append(requested)
            if requested != key or observations.get('missing'):
                error = RuntimeError('PRIVATE-OSS-ERROR')
                error.status = 404
                error.code = 'NoSuchKey'
                raise error
            return io.BytesIO(image)
    monkeypatch.setattr(OSSStorage, '_bucket', lambda _self: Bucket())
    if backend == 'local':
        storage_provider(settings).put(key, image, mime)
    observations.update(settings=settings, key=key, result=result, image=image,
                        run=lambda: asyncio.run(service.execute(task)))
    return observations, store, product, task, service


def test_original_helper_oss_bug_reproduces_production_failed_task_and_run(tmp_path, monkeypatch):
    obs, store, product, task, _service = scenario(tmp_path, monkeypatch)
    assert not (obs['settings'].object_storage_dir / obs['key']).exists()
    assert storage_provider(obs['settings']).get(obs['key']) == obs['image']
    obs['read_calls'].clear()
    obs['run']()
    saved = product.task_for_worker(task['id'])
    trace = store.run_trace(saved['run_id'], 'tenant-a')
    assert isinstance(obs['inner'], FileNotFoundError)
    assert obs['inner'].errno == errno.ENOENT
    assert obs['read_calls'] == [] and obs['turn_returned'] is False
    assert saved['status'] == trace['status'] == 'failed'
    assert saved['error_code'] == 'runtime_error'
    assert trace['payload']['error'] == 'unhandled errors in a TaskGroup (1 sub-exception)'
    assert trace['payload']['result_persistence_status'] == 'pending'
    assert trace['payload']['final_response_received'] is False
    with store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM task_results WHERE task_id=?', (task['id'],)).fetchone()['n'] == 0
        assert conn.execute("SELECT count(*) AS n FROM messages WHERE id=?", ('task:'+task['id']+':assistant',)).fetchone()['n'] == 0


def test_original_helper_local_backend_control_completes(tmp_path, monkeypatch):
    obs, store, product, task, _service = scenario(tmp_path, monkeypatch, backend='local')
    obs['run']()
    saved = product.task_for_worker(task['id'])
    assert saved['status'] == store.run_trace(saved['run_id'], 'tenant-a')['status'] == 'completed'


@pytest.mark.parametrize('backend', ['local','oss'])
@pytest.mark.parametrize('image_format', ['PNG','JPEG','WEBP'])
def test_fixed_helper_full_task_run_assistant_and_history_chain(tmp_path, monkeypatch, backend, image_format):
    obs, store, product, task, _service = scenario(tmp_path, monkeypatch,
        builder=reference_image_runtime_turn, backend=backend, image_format=image_format)
    local_path = obs['settings'].object_storage_dir/obs['key']
    assert local_path.exists() is (backend == 'local')
    obs['run']()
    saved = product.task_for_worker(task['id'])
    trace = store.run_trace(saved['run_id'], 'tenant-a')
    assert obs['turn_returned'] and obs['inner'] is None
    assert saved['status'] == trace['status'] == 'completed'
    assert trace['payload']['final_response_persisted'] and trace['payload']['assistant_message_saved']
    assert trace['payload']['result_persistence_status'] == 'completed'
    assert trace['payload']['runtime_completed'] and trace['payload']['required_tool_calls_completed']
    if backend == 'oss':
        assert obs['read_calls'] == [obs['key'], obs['key']]  # helper + unchanged TaskService
        assert not local_path.exists()  # No OSS copy downloaded to satisfy local path.
    user = product.user_by_email('member@tenant-a.test')
    detail = product.conversation_detail('tenant-a', user['id'], saved['conversation_id'])
    assistant = next(x for x in detail['messages'] if x['id']=='task:'+task['id']+':assistant')
    assert assistant['generation']['storage_key'] == obs['key']
    assert len(detail['generations']) == 1
    generation = detail['generations'][0]
    assert generation['mime_type'] == obs['report']['provider_result']['mime']
    assert (generation['width'],generation['height']) == (12,10)
    assert generation['requested_size'] == '1024x1024'
    summary = obs['report']['reference_image_diagnostics'][-1]
    assert summary['event']=='runtime_turn_built'
    assert summary['local_path_exists'] is (backend=='local')
    assert summary['storage_abstraction_invoked'] and summary['object_key_exists']
    assert summary['has_storage_id'] and summary['has_object_key'] and summary['has_url']


@pytest.mark.parametrize('failure,phase,leaf', [
    ('missing','storage_backend_read','StorageObjectNotFound'),
    ('invalid_reference','storage_reference_validation','ValueError'),
    ('unsupported_mime','image_metadata_validation','ValueError'),
    ('metadata_mismatch','image_metadata_validation','ValueError'),
    ('model_mismatch','result_contract','ValueError'),
])
def test_fixed_failure_keeps_inner_evidence_and_no_assistant(tmp_path, monkeypatch, caplog, failure, phase, leaf):
    def mutate(value):
        item = value['results'][0]
        if failure=='invalid_reference':
            item['storage_key']='generated/tenant-a/../secret.png'
        elif failure=='metadata_mismatch':
            item['width']=999
        elif failure=='model_mismatch':
            value['model']='wrong-fixture-model'
    obs, store, product, task, _service = scenario(tmp_path,monkeypatch,
        builder=reference_image_runtime_turn, image_format='GIF' if failure=='unsupported_mime' else 'PNG', mutate=mutate)
    if failure=='missing':
        obs['missing']=True
    with caplog.at_level(logging.WARNING):
        obs['run']()
    saved=product.task_for_worker(task['id'])
    assert saved['status']==store.run_trace(saved['run_id'],'tenant-a')['status']=='failed'
    records=obs['report']['reference_image_diagnostics']
    failed=next(x for x in records if x['event']=='helper_failed')
    assert failed['failure_phase']==phase
    assert leaf in [x['class'] for x in failed['exceptions']]
    assert records[-1]['event']=='controller_failed'
    assert 'ExceptionGroup' in [x['class'] for x in records[-1]['exceptions']]
    if failure=='missing':
        assert failed['storage_abstraction_invoked'] and failed['object_key_exists'] is False
    if failure in ('model_mismatch','invalid_reference'):
        assert obs['read_calls']==[]
    with store.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM messages WHERE id=?',('task:'+task['id']+':assistant',)).fetchone()['n']==0
        assert conn.execute('SELECT count(*) AS n FROM task_results WHERE task_id=?',(task['id'],)).fetchone()['n']==0
    assert 'PRIVATE-OSS-ERROR' not in caplog.text


def test_summary_and_failure_logs_never_contain_secrets_or_image_bytes(tmp_path,monkeypatch,caplog):
    def mutate(value):
        item=value['results'][0]
        item['url']='https://oss.invalid/object?signature=SIGNED-CANARY&token=SECRET-CANARY'
        item['untrusted-field-SECRET-CANARY']='N1N-CANARY OSS-CANARY Authorization BASE64-CANARY'
        value['Authorization']='Bearer N1N-CANARY'
    obs, _store, _product, _task, _service=scenario(tmp_path,monkeypatch,builder=reference_image_runtime_turn,mutate=mutate)
    obs['missing']=True
    with caplog.at_level(logging.WARNING):
        obs['run']()
    safe=json.dumps(obs['report']['reference_image_diagnostics'])+caplog.text
    for word in ('N1N-CANARY','OSS-CANARY','Authorization','SIGNED-CANARY','SECRET-CANARY','BASE64-CANARY',
                 'FAKE-OSS-ID','FAKE-OSS-SECRET','PRIVATE-OSS-ERROR'):
        assert word not in safe
    for record in caplog.records:
        assert record.exc_info is None


def test_entire_controller_scope_exit_exception_is_observed_without_message():
    report={'reference_image_diagnostics':[{'event':'runtime_turn_built','failure_phase':'runtime_turn_construction',
             'storage_reference_type':'object_key','object_key_exists':True,'local_path_exists':False,
             'storage_abstraction_invoked':True}]}
    persisted=[]
    @observe_reference_image_runtime(settings=lambda:SimpleNamespace(object_storage_provider='oss'),
                                   report=lambda:report,diagnostic_sink=persisted.append)
    async def exit_failure():
        raise ExceptionGroup('PRIVATE GROUP', [OSError(errno.EIO,'SECRET-CANARY')])
    with pytest.raises(ReferenceImageAcceptanceError) as caught:
        asyncio.run(exit_failure())
    assert str(caught.value)=='reference_image_acceptance_failed'
    record=persisted[0]
    assert record['failure_phase']=='mcp_client_scope_exit'
    assert record['exception_class']=='ExceptionGroup'
    assert any(x['errno']==errno.EIO for x in record['exceptions'])
    assert 'SECRET-CANARY' not in json.dumps(report)


def test_diagnostics_survive_globally_disabled_logging(tmp_path,monkeypatch):
    obs, _store, _product, _task, _service=scenario(tmp_path,monkeypatch,builder=reference_image_runtime_turn)
    obs['missing']=True
    prior=logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        obs['run']()
    finally:
        logging.disable(prior)
    assert any(x['event']=='helper_failed' for x in obs['report']['reference_image_diagnostics'])


def test_controller_cancellation_not_converted():
    @observe_reference_image_runtime(settings=lambda:SimpleNamespace(object_storage_provider='oss'),report=lambda:{})
    async def cancel():
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancel())


def test_nested_client_exception_fields_are_preserved_not_request_or_text():
    from scripts.reference_image_acceptance import _exceptions
    request=httpx.Request('POST','https://private.invalid/?signature=SECRET-CANARY',
                          headers={'Authorization':'Bearer N1N-CANARY'})
    outer=httpx.ConnectError('PRIVATE-MESSAGE',request=request)
    outer.__cause__=OSError(errno.ECONNREFUSED,'OSS-CANARY')
    group=ExceptionGroup('SECRET-CANARY',[outer,anyio.EndOfStream()])
    record=_exceptions(group)
    assert record['exception_class']=='ExceptionGroup'
    assert 'httpx.ConnectError' in [x['class'] for x in record['exceptions']]
    assert 'anyio.EndOfStream' in [x['class'] for x in record['exceptions']]
    assert any(x['errno']==errno.ECONNREFUSED for x in record['exceptions'])
    assert not any(word in json.dumps(record) for word in ('SECRET-CANARY','N1N-CANARY','Authorization','PRIVATE-MESSAGE','OSS-CANARY'))
    outer.__context__=outer
    assert len(_exceptions(outer)['exceptions'])==2
    huge=ExceptionGroup('SECRET-CANARY',[ValueError('PRIVATE-MESSAGE') for _ in range(100)])
    assert _exceptions(huge)['truncated']
