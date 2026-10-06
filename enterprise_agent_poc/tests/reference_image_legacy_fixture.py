"""Frozen post-MCP block of the external acceptance Controller, not app Runtime.

Source: .stage2-pg-stability-v1/production_image_smoke.py lines73-87;
SHA256 db730e22a90406510aa106e0e55aa53479a4d883d44f87d738ce25b76ae30f93.
Only function arguments/timing/report plumbing are adapted for offline invocation.
The defective local read and original contract checks remain unchanged.
"""
import io
from pathlib import Path
from PIL import Image
from app.domain import RuntimeTurn
from app.tool_dependencies import attempt_observation, tool_result_payload


def legacy_post_mcp(result, *, settings, session, tenant, reference, args, report):
    def need(ok, reason):
        if not ok:
            raise RuntimeError(reason)
    payload = tool_result_payload(result)
    if 'results' not in payload and isinstance(payload.get('result'), dict):
        payload = payload['result']
    need(payload['model'] == 'gpt-image-2.5-sunburst-c' and len(payload['results']) == 1, 'N1N_RESULT_MODEL')
    artifact = payload['results'][0]
    need(artifact['reference_images_used'] == [reference['id']], 'REAL_EDITS_REFERENCE')
    key = artifact['storage_key']
    need(key.startswith('generated/' + tenant + '/') and '..' not in Path(key).parts, 'RESULT_SCOPE')
    blob = (settings.object_storage_dir / key).read_bytes()
    with Image.open(io.BytesIO(blob)) as im:
        fmt = im.format
        size = im.size
        im.verify()
    mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}[fmt]
    need(artifact['format'] == fmt.lower() and artifact['mime_type'] == mime
         and (artifact['width'], artifact['height']) == size, 'ACTUAL_FORMAT')
    need(Path(key).suffix == {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}[fmt], 'EXTENSION')
    report['provider_result'] = {'storage_key': key}
    observation = attempt_observation(args, result, scope='fixture', tool_call_id='single-reference-edit')
    call = {'server': 'platform', 'tool': 'image_generation', 'status': 'completed',
            'artifact': artifact, 'result_is_error': False, **observation}
    return RuntimeTurn(session.thread_id, '参考图片编辑结果已生成。', mcp_calls=(call,))
