"""D1-D10: synthetic Agent and public history on a private Linux cluster."""
import os
import subprocess
import sys
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row
import pytest

from scripts import compatibility_epoch as epoch
from scripts.release_test_environment import IsolatedServices, PROJECT


pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Private Linux PostgreSQL fixture only')


def _run(services, role):
    return subprocess.run([sys.executable, str(PROJECT / 'scripts/stage2_preview.py'), role,
                           '--config', str(services.manifest)], cwd=PROJECT,
                          env=services._child_env(), check=True, capture_output=True, text=True)


def _db(services):
    services.validate_manifest()
    return psycopg.connect(services.config['environment']['ENTERPRISE_POC_DATABASE_URL'], row_factory=dict_row)


def _bootstrap_compatibility(services):
    before = dict(os.environ)
    try:
        for name in services.config['environment']:
            os.environ.pop(name, None)
        with patch.object(epoch, '_current_source_identity', return_value='f'*40):
            assert epoch.bootstrap_current(services.manifest)['status'] == 'bootstrapped'
    finally:
        os.environ.clear()
        os.environ.update(before)


@pytest.fixture(scope='module')
def domain():
    with IsolatedServices() as services:
        services.migrate()
        _run(services, 'system-provision')
        with _db(services) as conn:
            initial = epoch.read_state(conn)['epoch']
        _bootstrap_compatibility(services)
        _run(services, 'provision')
        first = _run(services, 'domain-provision')
        yield services, initial, first.stdout


def _agent(services):
    with _db(services) as conn:
        rows = conn.execute("SELECT * FROM agent_templates WHERE slug='social-content-agent'").fetchall()
    assert len(rows) == 1
    return dict(rows[0])


def _public(services, agent_id):
    with _db(services) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT t.id,t.status,t.tenant_id,t.user_id,t.conversation_id,m.context_id,c.credit_cost "
            "FROM tasks t JOIN task_agent_contexts m ON m.task_id=t.id "
            "JOIN agent_execution_contexts c ON c.id=m.context_id "
            "WHERE t.agent_id=%s AND t.id NOT IN "
            "(SELECT task_id FROM agent_template_tests WHERE task_id IS NOT NULL)", (agent_id,))]


def test_d1_fresh_private_database_bootstraps(domain):
    services, initial, output = domain
    assert initial == 'legacy_v1'
    assert "'status': 'bootstrapped'" in output
    with _db(services) as conn:
        assert epoch.read_state(conn)['epoch'] == 'productized_v1'


def test_d2_social_content_agent_present(domain):
    assert _agent(domain[0])['definition_source'] == 'productized'


def test_d3_published_and_usable(domain):
    services = domain[0]
    agent = _agent(services)
    with _db(services) as conn:
        version = conn.execute('SELECT * FROM agent_template_versions WHERE agent_template_id=%s', (agent['id'],)).fetchone()
        instance = conn.execute('SELECT * FROM tenant_agent_instances WHERE agent_id=%s AND tenant_id=%s',
                                (agent['id'], 'tenant-a')).fetchone()
        test = conn.execute("SELECT status FROM agent_template_tests WHERE agent_template_version_id=%s AND test_type='runtime'",
                            (version['id'],)).fetchone()
    assert version['status'] == 'published' and version['credit_cost'] == 3
    assert instance['status'] == 'enabled' and test['status'] == 'passed'


def test_d4_tenant_and_user_ownership(domain):
    services = domain[0]
    rows = _public(services, _agent(services)['id'])
    with _db(services) as conn:
        user = conn.execute("SELECT id,tenant_id FROM users WHERE email='stage2-public@tenant-a.test'").fetchone()
        other = conn.execute("SELECT count(*) AS n FROM tenant_agent_instances WHERE tenant_id='tenant-b' AND agent_id=%s",
                             (_agent(services)['id'],)).fetchone()
    assert user['tenant_id'] == 'tenant-a' and all(row['tenant_id'] == 'tenant-a' and row['user_id'] == user['id'] for row in rows)
    assert other['n'] == 0


def test_d5_only_required_published_skill_binding(domain):
    services = domain[0]
    with _db(services) as conn:
        rows = conn.execute("SELECT s.slug,v.status FROM agent_template_version_skills b "
                            "JOIN skill_versions v ON v.id=b.skill_version_id "
                            "JOIN skills s ON s.id=b.skill_id "
                            "JOIN agent_template_versions a ON a.id=b.agent_template_version_id "
                            "WHERE a.agent_template_id=%s", (_agent(services)['id'],)).fetchall()
    assert [(row['slug'],row['status']) for row in rows] == [('social-copywriting','published')]


def test_d6_exactly_two_public_tasks(domain):
    services = domain[0]
    assert len(_public(services, _agent(services)['id'])) == 2


def test_d7_public_tasks_are_completed(domain):
    services = domain[0]
    assert all(row['status'] == 'completed' for row in _public(services, _agent(services)['id']))


def test_d8_public_visibility_and_shared_context(domain):
    services = domain[0]
    rows = _public(services, _agent(services)['id'])
    assert len({row['conversation_id'] for row in rows}) == 1
    assert len({row['context_id'] for row in rows}) == 1
    assert all(row['credit_cost'] == 3 for row in rows)


def test_d9_repeat_domain_bootstrap_is_idempotent(domain):
    services = domain[0]
    before = {row['id'] for row in _public(services, _agent(services)['id'])}
    result = _run(services, 'domain-provision')
    assert "'status': 'already_bootstrapped'" in result.stdout
    assert before == {row['id'] for row in _public(services, _agent(services)['id'])}


def test_d10_private_cluster_cleanup(domain):
    services = domain[0]
    root = services.root
    assert root.exists()
    services.stop()
    assert not root.exists()
