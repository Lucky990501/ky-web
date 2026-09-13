from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.main import app, STATIC_DIR


def test_workbench_route_and_async_view_regressions():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required for native frontend route regression tests')
    suite = Path(__file__).with_name('workbench_routes.test.cjs')
    result = subprocess.run([node, '--test', str(suite)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('path', ['/profile', '/platform/skills', '/workspace', '/conversations', '/generations', '/knowledge', '/assets'])
def test_static_routes_reuse_the_same_workbench_entry_without_redirect(path):
    # These entry routes don't require startup or a database connection.
    client = TestClient(app)
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 200
    assert response.content == (STATIC_DIR / 'index.html').read_bytes()
    assert 'location' not in response.headers
    assert response.headers['content-type'].startswith('text/html')


def test_profile_route_does_not_capture_api_or_static_assets():
    client = TestClient(app)
    assert client.get('/api/v1/me').status_code == 401
    javascript = client.get('/static/workbench.js')
    assert javascript.status_code == 200
    assert javascript.content == (STATIC_DIR / 'workbench.js').read_bytes()
    assert client.get('/api/not-a-route').status_code == 404
    assert client.get('/mcp').status_code == 404
