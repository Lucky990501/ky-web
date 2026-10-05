import asyncio
import errno
import json
import logging
import socket
import ssl
from types import SimpleNamespace

import httpcore
import httpx
import pytest

from app.platform_mcp.image_diagnostics import (
    ImageProviderUnavailable, image_transport_guard, transport_diagnostics,
)
from test_image_generation_provider import image_service


PRIVATE = 'secret-canary Authorization: Bearer fake-key prompt-canary signed-url?token=private base64-canary'


def chain(*errors):
    for parent, child in zip(errors, errors[1:]):
        parent.__cause__ = child
    return errors[0]


@pytest.mark.parametrize('error,category,classes', [
    (httpx.ConnectError(PRIVATE), 'connection_error', ['httpx.ConnectError']),
    (chain(httpx.ConnectError(PRIVATE), httpcore.ConnectError(PRIVATE)), 'connection_error',
     ['httpx.ConnectError', 'httpcore.ConnectError']),
    (httpx.ConnectTimeout(PRIVATE), 'connect_timeout', ['httpx.ConnectTimeout']),
    (socket.gaierror(socket.EAI_AGAIN, PRIVATE), 'dns_error', ['socket.gaierror']),
    (OSError(errno.EACCES, PRIVATE), 'os_error', ['builtins.PermissionError']),
    (OSError(errno.EIO, PRIVATE), 'os_error', ['builtins.OSError']),
    (ExceptionGroup(PRIVATE, [httpx.ConnectError(PRIVATE)]), 'connection_error',
     ['builtins.ExceptionGroup', 'httpx.ConnectError']),
    (chain(httpx.ConnectError(PRIVATE), httpcore.ConnectError(PRIVATE),
           OSError(PRIVATE), ExceptionGroup(PRIVATE, [OSError(errno.ENETUNREACH, PRIVATE)])),
     'connection_error', ['httpx.ConnectError', 'httpcore.ConnectError', 'builtins.OSError',
                          'builtins.ExceptionGroup', 'builtins.OSError']),
    (chain(httpx.ConnectError(PRIVATE), ssl.SSLError(PRIVATE)), 'tls_error',
     ['httpx.ConnectError', 'ssl.SSLError']),
    (httpx.ReadTimeout(PRIVATE), 'read_timeout', ['httpx.ReadTimeout']),
    (httpx.WriteTimeout(PRIVATE), 'write_timeout', ['httpx.WriteTimeout']),
    (httpx.PoolTimeout(PRIVATE), 'pool_timeout', ['httpx.PoolTimeout']),
])
def test_safe_nested_exception_metadata(error, category, classes, caplog):
    record = transport_diagnostics(error, 'edits')
    assert [x['class'] for x in record['exceptions']] == classes
    assert record['category'] == category
    assert record['endpoint_hostname'] == 'llm-api.net'
    assert record['request_method'] == 'POST'
    assert record['truncated'] is False
    async def fail():
        async with image_transport_guard('edits'):
            raise error
    with caplog.at_level(logging.WARNING), pytest.raises(ImageProviderUnavailable) as caught:
        asyncio.run(fail())
    assert str(caught.value) == 'image_provider_unavailable'
    assert caught.value.__suppress_context__ is True
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None
    for private in ('secret-canary', 'Authorization', 'fake-key', 'prompt-canary', 'token=private', 'base64-canary'):
        assert private not in caplog.text
    logged = json.loads(caplog.records[0].getMessage().split(' ', 1)[1])
    assert logged == record


def test_errno_causes_context_cycles_and_bounded_groups():
    error = httpx.ConnectError(PRIVATE)
    error.__cause__ = OSError(errno.ECONNREFUSED, PRIVATE)
    error.__context__ = socket.gaierror(socket.EAI_AGAIN, PRIVATE)
    error.__context__.__cause__ = error
    nodes = transport_diagnostics(error, 'edits')['exceptions']
    assert nodes[1]['errno'] == errno.ECONNREFUSED
    assert nodes[2]['errno'] == socket.EAI_AGAIN
    assert nodes[2]['relation'] == 'context'
    huge = ExceptionGroup(PRIVATE, [OSError(errno.EACCES, PRIVATE) for _ in range(100)])
    record = transport_diagnostics(huge, PRIVATE)
    assert len(record['exceptions']) == 32 and record['truncated']
    assert record['operation'] == 'unknown'
    assert PRIVATE not in json.dumps(record)
    deep = chain(*[RuntimeError(PRIVATE) for _ in range(20)])
    assert transport_diagnostics(deep, 'edits')['truncated']


def test_request_headers_and_url_cannot_enter_diagnostics(caplog):
    request = httpx.Request('POST', 'https://example.invalid/private?token=canary',
                            headers={'Authorization': 'Bearer fake-key'}, content=b'private-image')
    error = httpx.ConnectError(PRIVATE, request=request)
    record = json.dumps(transport_diagnostics(error, 'edits'))
    for text in ('example.invalid', 'canary', 'Authorization', 'fake-key', 'private-image'):
        assert text not in record


def test_cancellation_and_programming_error_not_reclassified():
    async def fail(error):
        async with image_transport_guard('edits'):
            raise error
    for error in (asyncio.CancelledError(), ValueError('programming error'),
                  BaseExceptionGroup('cancel', [asyncio.CancelledError(), OSError(errno.EACCES, PRIVATE)])):
        with pytest.raises(type(error)) as caught:
            asyncio.run(fail(error))
        assert caught.value is error


@pytest.mark.parametrize('phase', ['enter', 'post', 'exit'])
def test_generations_lifecycle_transport_failure_is_safe_no_retry(tmp_path, monkeypatch, caplog, phase):
    service, settings, token = image_service(tmp_path, monkeypatch)
    calls = []
    class Client:
        def __init__(self, **options):
            assert options == {'timeout': 120}  # No speculative network tuning.
        async def __aenter__(self):
            if phase == 'enter':
                raise httpx.ConnectError(PRIVATE)
            return self
        async def __aexit__(self, *_):
            if phase == 'exit':
                raise httpx.ReadError(PRIVATE)
        async def post(self, url, **kwargs):
            calls.append(url)
            if phase == 'post':
                raise chain(httpx.ConnectError(PRIVATE), httpcore.ConnectError(PRIVATE),
                            OSError(errno.ECONNREFUSED, PRIVATE))
            return httpx.Response(200, json={})
    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    with caplog.at_level(logging.WARNING), pytest.raises(ImageProviderUnavailable):
        asyncio.run(service.image_generation(token, 'prompt-canary', [], '1:1'))
    assert len(calls) == (0 if phase == 'enter' else 1)
    assert '"operation": "generations"' in caplog.text
    assert PRIVATE not in caplog.text


def test_native_mcp_error_result_has_no_traceback_or_retry_receipt(monkeypatch, caplog):
    from app.platform_mcp import server
    async def failed(*_args, **_kwargs):
        async with image_transport_guard('edits'):
            raise chain(httpx.ConnectError(PRIVATE), OSError(errno.EACCES, PRIVATE))
    monkeypatch.setattr(server, 'service', SimpleNamespace(image_generation=failed))
    monkeypatch.setattr(server, '_bearer_from_context', lambda _: 'fake-runtime-token')
    monkeypatch.setattr(server, '_execution_scope_from_context', lambda *_: 'scope')
    mcp = server.create_mcp()
    with caplog.at_level(logging.WARNING):
        result = asyncio.run(mcp.call_tool('image_generation', {'prompt': 'prompt-canary',
                              'references': [], 'aspect_ratio': '1:1'}))
    assert result.isError
    assert result.structuredContent == {'error_code': 'image_provider_unavailable'}
    assert json.loads(result.content[0].text) == result.structuredContent
    assert 'Traceback' not in caplog.text
    assert 'Authorization' not in caplog.text and 'prompt-canary' not in caplog.text
    assert 'fake-key' not in caplog.text
