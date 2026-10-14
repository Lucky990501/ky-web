"""Actual ASGI attachment/permission contract, no runtime or paid execution.

Reuse the existing document UI fixture's network-denied, marked SQLite setup.
This does not run application lifespan or provide Native/release evidence.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys

saved_stdin = sys.stdin
try:
    sys.stdin = io.StringIO('{"operation":"stop"}\n')
    with contextlib.redirect_stdout(io.StringIO()):
        fixture = runpy.run_path(str(Path(__file__).with_name('activity_word_api_bridge.py')))
finally:
    sys.stdin = saved_stdin

from fastapi.testclient import TestClient
from PIL import Image

client = TestClient(fixture['main'].app)
checks = []

def login(alias):
    tenant, email, role = fixture['USERS'][alias]
    client.cookies.clear()
    response = client.post('/api/v1/auth/login', json={'account': email, 'password': 'IsolatedFixture2026!'})
    assert response.status_code == 200
    assert response.json()['user']['tenant_id'] == tenant

def check(name, predicate):
    assert predicate, name
    checks.append({'name': name, 'status': 'PASS_ACTUAL_ASGI_ISOLATED_SQLITE'})

try:
    login('member_a')
    image = io.BytesIO()
    Image.new('RGB', (48, 32), '#ffe080').save(image, 'PNG')
    attachments = []
    for number in range(5):
        response = client.post('/api/v1/chat-images', files={'file': (f'Fixture-{number}.png', image.getvalue(), 'image/png')})
        check(f'actual upload {number+1} accepts independent multipart attachment', response.status_code == 201)
        record = response.json()
        assert record['type'] == 'image' and record['width'] == 48 and record['height'] == 32
        assert record['content_url'] == '/api/v1/chat-images/' + record['id']
        attachments.append(record)
    check('five uploads retain unique IDs', len({record['id'] for record in attachments}) == 5)
    check('authorized reference download uses actual content URL', client.get(attachments[0]['content_url']).content == image.getvalue())
    agent = client.get('/api/v1/agents/image-agent')
    check('actual image metadata provides enabled and credit_cost', agent.status_code == 200 and agent.json()['enabled'] and isinstance(agent.json()['credit_cost'], int))
    # Pydantic rejects >1 BEFORE runtime/queue/admission. No accepted Task.
    with fixture['main'].store.connection() as conn:
        tasks_before = conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
    response = client.post('/api/v1/agents/image-agent/runs', json={'message': 'Fixture MUST NOT EXECUTE', 'attachments': [{'type': 'image', 'id': record['id']} for record in attachments[:2]]})
    with fixture['main'].store.connection() as conn:
        tasks_after = conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]
    check('frozen real request rejects multireference before task creation', response.status_code == 422 and tasks_after == tasks_before)
    login('peer_a')
    check('same-Tenant other User cannot read attachment', client.get(attachments[0]['content_url']).status_code == 404)
    login('admin_b')
    check('cross-Tenant administrator cannot read attachment', client.get(attachments[0]['content_url']).status_code == 404)
    login('member_a')
    check('formal delete removes only selected owned attachment', client.delete(attachments[0]['content_url']).status_code == 200 and client.get(attachments[0]['content_url']).status_code == 404 and client.get(attachments[1]['content_url']).status_code == 200)
    client.cookies.clear()
    check('anonymous upload/download rejected', client.get(attachments[1]['content_url']).status_code == 401 and client.post('/api/v1/chat-images', files={'file': ('Fixture.png', image.getvalue(), 'image/png')}).status_code == 401)
    report = {'mode': 'ACTUAL_ASGI_LOCAL_SQLITE_FIXTURES', 'checks': checks, 'total': len(checks), 'failed': 0, 'provider_calls': 0, 'accepted_tasks': 0, 'production_changes': 0, 'native_startup': 'NOT_EXECUTED'}
    if os.environ.get('BUSINESS_P0_API_RESULT'):
        Path(os.environ['BUSINESS_P0_API_RESULT']).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
finally:
    client.close()
