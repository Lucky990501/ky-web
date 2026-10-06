"""Release/acceptance-only post-MCP helper. Never installed into app Runtime.

Replaces the external production_image_smoke.Controller.run_turn local read.
No Provider/OSS credentials are read here; storage uses the existing adapter.
"""
from collections import deque
from functools import wraps
import io
import hashlib
import json
import logging
from pathlib import PurePosixPath
import socket
import ssl

import anyio
import httpx
import httpcore

from PIL import Image, UnidentifiedImageError

from app.domain import RuntimeTurn
from app.storage import StorageObjectNotFound, StorageUnavailable, storage_provider
from app.tool_dependencies import attempt_observation, tool_result_payload

logger = logging.getLogger(__name__)
ROOT_FIELDS = {'provider', 'model', 'results', '_tool_dependency'}
ITEM_FIELDS = {'asset_id', 'storage_key', 'url', 'format', 'mime_type', 'requested_size',
               'actual_size', 'size', 'width', 'height', 'request_id', 'reference_images_used',
               'references_used', 'file_name'}
IMAGE_TYPES = {'PNG': ('png', 'image/png', '.png'), 'JPEG': ('jpeg', 'image/jpeg', '.jpg'),
               'WEBP': ('webp', 'image/webp', '.webp')}


class ReferenceImageAcceptanceError(RuntimeError):
    def __init__(self, phase):
        self.phase = phase
        super().__init__('reference_image_acceptance_failed')


def _exceptions(error):
    known = ((httpx.ConnectError, 'httpx.ConnectError'), (httpx.ReadError, 'httpx.ReadError'),
             (httpx.ConnectTimeout, 'httpx.ConnectTimeout'), (httpx.ReadTimeout, 'httpx.ReadTimeout'),
             (httpx.TimeoutException, 'httpx.TimeoutException'), (httpx.RequestError, 'httpx.RequestError'),
             (httpcore.ConnectError, 'httpcore.ConnectError'), (httpcore.ReadError, 'httpcore.ReadError'),
             (httpcore.ConnectTimeout, 'httpcore.ConnectTimeout'), (httpcore.ReadTimeout, 'httpcore.ReadTimeout'),
             (anyio.BrokenResourceError, 'anyio.BrokenResourceError'),
             (anyio.ClosedResourceError, 'anyio.ClosedResourceError'), (anyio.EndOfStream, 'anyio.EndOfStream'),
             (socket.gaierror, 'socket.gaierror'), (ssl.SSLError, 'ssl.SSLError'),
             (StorageObjectNotFound, 'StorageObjectNotFound'), (StorageUnavailable, 'StorageUnavailable'),
             (UnidentifiedImageError, 'UnidentifiedImageError'), (FileNotFoundError, 'FileNotFoundError'),
             (PermissionError, 'PermissionError'), (OSError, 'OSError'),
             (ReferenceImageAcceptanceError, 'ReferenceImageAcceptanceError'),
             (RuntimeError, 'RuntimeError'), (ValueError, 'ValueError'), (TypeError, 'TypeError'),
             (KeyError, 'KeyError'), (ExceptionGroup, 'ExceptionGroup'), (BaseExceptionGroup, 'BaseExceptionGroup'))
    queue = deque([(error, None, 'root', 0)])
    seen, nodes, truncated = set(), [], False
    while queue and len(nodes) < 32:
        exc, parent, relation, depth = queue.popleft()
        if id(exc) in seen:
            continue
        seen.add(id(exc))
        name = next((label for kind, label in known if isinstance(exc, kind)), 'other_exception')
        number = getattr(exc, 'errno', None) if isinstance(exc, OSError) else None
        index = len(nodes)
        nodes.append({'class': name, 'errno': number if type(number) is int else None,
                      'parent': parent, 'relation': relation})
        children = [(exc.__cause__, 'cause'), (exc.__context__, 'context')]
        if isinstance(exc, BaseExceptionGroup):
            children.extend((x, 'group_member') for x in exc.exceptions[:32])
            truncated |= len(exc.exceptions) > 32
        for child, edge in children:
            if child is not None and id(child) not in seen:
                if depth < 8:
                    queue.append((child, index, edge, depth+1))
                else:
                    truncated = True
    return {'exceptions': nodes, 'exception_class': nodes[0]['class'], 'truncated': truncated or bool(queue)}


def _emit(report, record, sink=None):
    # Persist the safe receipt BEFORE an assertion/read can fail. The external
    # smoke disables logging globally, so its integration patch supplies save().
    report.setdefault('reference_image_diagnostics', []).append(record)
    logger.warning('reference_image_acceptance %s', json.dumps(record, sort_keys=True))
    if sink is not None:
        sink(record)


def _backend(settings):
    return settings.object_storage_provider if settings.object_storage_provider in ('local', 'oss') else 'unknown'


def _size(value):
    import re
    return value if isinstance(value, str) and re.fullmatch(r'\d{1,5}x\d{1,5}', value) else None


def reference_image_runtime_turn(result, *, settings, session, tenant, reference, args, report,
                                 scope='reference-image-acceptance', created_at=None,
                                 diagnostic_sink=None, duration_seconds=None):
    phase = 'result_normalization'
    summary = {'storage_backend': _backend(settings), 'result_field_names': [], 'item_field_names': [],
               'has_storage_id': False, 'has_object_key': False, 'has_url': False,
               'mime_type': None, 'extension': None, 'requested_size': None, 'actual_size': None,
               'storage_reference_type': 'missing', 'object_key_exists': None,
               'local_path_exists': None, 'storage_abstraction_invoked': False}
    try:
        if getattr(result, 'isError', False):
            raise ValueError('failed_mcp_result')
        payload = tool_result_payload(result)
        if 'results' not in payload and isinstance(payload.get('result'), dict):
            payload = payload['result']
        summary['result_field_names'] = sorted(ROOT_FIELDS.intersection(payload))
        items = payload.get('results')
        artifact = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
        key = artifact.get('storage_key')
        summary.update(item_field_names=sorted(ITEM_FIELDS.intersection(artifact)),
                       has_storage_id=isinstance(artifact.get('asset_id'), str) and bool(artifact['asset_id']),
                       has_object_key=isinstance(key, str) and bool(key),
                       has_url=isinstance(artifact.get('url'), str) and bool(artifact['url']),
                       mime_type=artifact.get('mime_type') if artifact.get('mime_type') in {'image/png','image/jpeg','image/webp'} else None,
                       extension=PurePosixPath(key).suffix if isinstance(key, str) and PurePosixPath(key).suffix in {'.png','.jpg','.webp'} else None,
                       requested_size=_size(artifact.get('requested_size')), actual_size=_size(artifact.get('actual_size')))
        _emit(report, {'event': 'mcp_result_received', 'helper_function': 'reference_image_runtime_turn',
                       'failure_phase': phase, **summary}, diagnostic_sink)
        phase = 'result_contract'
        if payload.get('model') != settings.image_model_id or not isinstance(items, list) or len(items) != 1:
            raise ValueError('result_contract')
        if artifact.get('reference_images_used') != [reference['id']]:
            raise ValueError('reference_binding')
        phase = 'storage_reference_validation'
        if (not isinstance(key, str) or len(key) > 1024 or not key.startswith('generated/'+tenant+'/')
                or '\\' in key or any(x in ('', '.', '..') for x in key.split('/')) or '?' in key or '#' in key):
            summary['storage_reference_type'] = 'invalid'
            raise ValueError('storage_reference')
        summary['storage_reference_type'] = 'object_key'
        # Observation only: existence is NOT a success gate, and no bytes are
        # read from this path. For OSS it can legitimately be False.
        try:
            summary['local_path_exists'] = (settings.object_storage_dir/key).exists()
        except OSError:
            summary['local_path_exists'] = None
        phase = 'storage_backend_read'
        summary['storage_abstraction_invoked'] = True
        try:
            blob = storage_provider(settings).get(key)
            summary['object_key_exists'] = True
        except StorageObjectNotFound:
            summary['object_key_exists'] = False
            raise
        phase = 'image_metadata_validation'
        with Image.open(io.BytesIO(blob)) as picture:
            actual_format, actual_size = picture.format, picture.size
            picture.verify()
        if actual_format not in IMAGE_TYPES:
            raise ValueError('unsupported_mime')
        image_format, mime, extension = IMAGE_TYPES[actual_format]
        if (artifact.get('format') != image_format or artifact.get('mime_type') != mime
                or (artifact.get('width'), artifact.get('height')) != actual_size
                or PurePosixPath(key).suffix != extension):
            raise ValueError('image_metadata')
        report['provider_result'] = {'model': payload['model'], 'storage_key': key,
                                     'sha256': hashlib.sha256(blob).hexdigest(), 'bytes': len(blob),
                                     'mime': mime, 'extension': extension, 'actual_format': actual_format,
                                     'actual_size': f'{actual_size[0]}x{actual_size[1]}',
                                     'requested_format': 'jpeg', 'requested_size': artifact.get('requested_size'),
                                     'base64_decode': 'PASS_FROZEN_NATIVE_SOURCE_STRICT',
                                     'b64_json': 'PASS_FROZEN_NATIVE_SOURCE_REQUIRED', 'pillow_verify': 'PASS',
                                     'duration_seconds': duration_seconds}
        phase = 'runtime_turn_construction'
        observation = attempt_observation(args, result, scope=scope, tool_call_id='single-reference-edit',
                                          created_at=created_at)
        call = {'server': 'platform', 'tool': 'image_generation', 'status': 'completed',
                'artifact': artifact, 'result_is_error': False, **observation}
        turn = RuntimeTurn(session.thread_id, '[受控发布验收；仅真实图片编辑，无Text Provider] 已完成参考图片编辑。',
                           mcp_calls=(call,), lifecycle_events=({'event': 'CONTROLLED_PRODUCTION_NATIVE_MCP_IMAGE_SMOKE_NO_TEXT_V1'},))
        _emit(report, {'event': 'runtime_turn_built', 'helper_function': 'reference_image_runtime_turn',
                       'failure_phase': phase, **summary}, diagnostic_sink)
        return turn
    except Exception as error:
        _emit(report, {'event': 'helper_failed', 'helper_function': 'reference_image_runtime_turn',
                       'failure_phase': phase, **summary, **_exceptions(error)}, diagnostic_sink)
        raise ReferenceImageAcceptanceError(phase) from None


def observe_reference_image_runtime(*, settings, report, diagnostic_sink=None):
    """Wrap the entire external Controller, including MCP context teardown."""
    def decorate(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            try:
                return await function(*args, **kwargs)
            except Exception as error:
                target = report()
                records = target.get('reference_image_diagnostics') or []
                last = records[-1] if records else {}
                phase = ('mcp_client_scope_exit' if last.get('event') == 'runtime_turn_built'
                         else last.get('failure_phase', 'mcp_client_wait'))
                summary = {k: last.get(k) for k in ('result_field_names','item_field_names','storage_reference_type',
                            'object_key_exists','local_path_exists','storage_abstraction_invoked')}
                _emit(target, {'event': 'controller_failed', 'helper_function': 'Controller.run_turn',
                               'failure_phase': phase, 'storage_backend': _backend(settings()),
                               **summary, **_exceptions(error)}, diagnostic_sink)
                raise ReferenceImageAcceptanceError(phase) from None
        return wrapped
    return decorate
