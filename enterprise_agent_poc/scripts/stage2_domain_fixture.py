"""Synthetic, isolated Stage 2 prerequisite state; never a production entry point.

The local runtime double drives the real Runtime-Test/Task persistence services.
No Provider network request and no direct business-table write is made here.
"""
from __future__ import annotations

import asyncio
from uuid import uuid4

from app.auth import hash_password
from app.domain import RuntimeSession, RuntimeTurn


SLUG = 'social-content-agent'
PUBLIC_EMAIL = 'stage2-public@tenant-a.test'
PUBLIC_TENANT = 'tenant-a'
PUBLIC_PROMPTS = ('Synthetic historical public task A', 'Synthetic historical public task B')


class _FixtureRuntime:
    """Deterministic local completion through the normal TaskService path."""

    async def create_session(self, profile, _instructions):
        return RuntimeSession(thread_id='stage2-fixture-' + str(uuid4()), profile_id=profile.id)

    async def resume_session(self, profile, thread_id, **_kwargs):
        return RuntimeSession(thread_id=thread_id, profile_id=profile.id)

    def startup_events(self, profile):
        return tuple({'event': 'skill_discovered', 'skill': slug} for slug in profile.skill_manifest)

    async def run_turn(self, session, _message):
        return RuntimeTurn(thread_id=session.thread_id, text='隔离测试历史正文', status='completed')

    async def close(self):
        pass


def _read_state(store, template_id):
    with store.connection() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT t.id,t.status,t.conversation_id,m.context_id,c.credit_cost,t.tenant_id,t.user_id "
            "FROM tasks t JOIN task_agent_contexts m ON m.task_id=t.id "
            "JOIN agent_execution_contexts c ON c.id=m.context_id "
            "WHERE t.agent_id=? AND t.id NOT IN "
            "(SELECT task_id FROM agent_template_tests WHERE task_id IS NOT NULL)", (template_id,))]
        return rows


def bootstrap_domain(*, store, product, registry, control, tasks, test_tenant, isolation_guard):
    """Create a valid published Agent and two public completions, or verify it.

    All mutations use the project's formal service/store methods. A partial
    previous run fails closed instead of inventing or repairing rows.
    """
    isolation_guard()
    actor = product.user_by_email('runtime@stage2.test')
    if not actor or actor['tenant_id'] != test_tenant or not registry.is_platform_admin(actor['id']):
        raise RuntimeError('Stage 2 synthetic platform-admin fixture missing')
    existing = [item for item in control.list_templates() if item['slug'] == SLUG]
    if existing:
        if len(existing) != 1:
            raise RuntimeError('Stage 2 Agent identity ambiguous')
        template_id = existing[0]['id']
        detail = control.detail(template_id)
        versions = [version for version in detail['versions'] if version['status'] == 'published']
        instance = control.instance(template_id, PUBLIC_TENANT)
        rows = _read_state(store, template_id)
        if (len(versions) != 1 or not versions[0]['production_ready'] or
                instance['status'] != 'enabled' or len(rows) != 2 or
                any(row['status'] != 'completed' for row in rows)):
            raise RuntimeError('Stage 2 Domain Bootstrap partial state; BLOCK')
        return {'status': 'already_bootstrapped', 'agent_id': template_id, 'public_tasks': 2}

    public = product.user_by_email(PUBLIC_EMAIL)
    if public is None:
        product.create_user(PUBLIC_TENANT, PUBLIC_EMAIL, hash_password('Stage2Synthetic!2026'),
                            'Stage 2 Public Fixture', 'member')
        public = product.user_by_email(PUBLIC_EMAIL)
    if not public or public['tenant_id'] != PUBLIC_TENANT:
        raise RuntimeError('Stage 2 public user ownership mismatch')

    template = control.create_template({'slug': SLUG, 'name': 'Synthetic Social Content'}, actor['id'])
    template_id = template['id']
    detail = control.create_version(template_id, {
        'persona': 'Synthetic social content fixture Persona', 'credit_cost': 3,
        'knowledge_requirement': 'none', 'asset_requirement': 'none',
        'enterprise_config_requirement': 'optional',
    }, actor['id'])
    version = detail['versions'][0]
    with store.connection() as conn:
        skill = conn.execute(
            "SELECT s.id AS skill_id,v.id AS skill_version_id FROM skills s "
            "JOIN skill_versions v ON v.skill_id=s.id "
            "WHERE s.slug='social-copywriting' AND v.status='published' ORDER BY v.version DESC LIMIT 1"
        ).fetchone()
    if not skill:
        raise RuntimeError('Published synthetic social-copywriting prerequisite missing')
    control.bind_skills(template_id, version['id'], [dict(skill)], actor['id'])
    control.bind_tools(template_id, version['id'],
                       [{'tool_capability_id': 'config_get', 'invocation_requirement': 'optional'}], actor['id'])
    control.validate(template_id, version['id'], actor['id'])
    version = control.detail(template_id)['versions'][0]

    # AgentRuntimeTest.run itself enforces Codex provider type while creating
    # the isolated Runtime-Test task. Only execution is driven by a local
    # deterministic double; this seeds prerequisite evidence without calling
    # the external Provider before the actual Redis Worker E2E.
    tester = control.runtime_tester
    original_enqueue, original_runtime = tester.enqueue, tasks._agents._runtime
    tester.isolation_guard = isolation_guard
    tester.enqueue = lambda _task_id: None
    try:
        queued = asyncio.run(tester.run(template_id, version['id'], actor['id'],
                                        version['configuration_fingerprint']))
        tasks._agents._runtime = _FixtureRuntime()
        asyncio.run(tasks.execute(product.task_for_worker(queued['task_id'])))
        with store.connection() as conn:
            status = conn.execute('SELECT status FROM agent_template_tests WHERE id=?',
                                  (queued['runtime_test_id'],)).fetchone()['status']
        if status != 'passed':
            raise RuntimeError('Synthetic Stage 2 Runtime-Test persistence failed')
        control.publish(template_id, version['id'], actor['id'], 'production')
        control.configure_instance(template_id, PUBLIC_TENANT, version['id'], {})
        control.set_instance_status(template_id, PUBLIC_TENANT, 'enabled')
        conversation_id = None
        for prompt in PUBLIC_PROMPTS:
            task = product.create_task(PUBLIC_TENANT, public['id'], template_id, prompt, conversation_id)
            asyncio.run(tasks.execute(task))
            completed = product.task_for_worker(task['id'])
            if completed['status'] != 'completed':
                raise RuntimeError('Synthetic public Task persistence failed')
            conversation_id = completed['conversation_id']
    finally:
        tasks._agents._runtime = original_runtime
        tester.enqueue = original_enqueue
    isolation_guard()
    rows = _read_state(store, template_id)
    if len(rows) != 2 or len({row['conversation_id'] for row in rows}) != 1 or len({row['context_id'] for row in rows}) != 1:
        raise RuntimeError('Synthetic public history identity mismatch')
    return {'status': 'bootstrapped', 'agent_id': template_id, 'public_tasks': 2}
