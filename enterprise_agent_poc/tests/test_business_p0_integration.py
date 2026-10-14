"""Incremental contracts, not actual model/Provider or Production evidence."""
import asyncio
import json
import os
from pathlib import Path

import pytest

from app.agent_productization import AgentCatalogError
from app.image_request_contract import ImageRequestError
from app.platform_mcp.service import PlatformMCPService
from app.security import RuntimePrincipal, RuntimeTokenIssuer
from scripts.create_wechat_official_account_agent_v1 import create_draft, SLUG
from test_agent_execution import execution
from test_agent_productization import catalog, published_skill
from test_chat_reference_image import chat_image_env, picture
from test_agent_quality_p0 import stub_provider


def test_single_reference_multiple_outputs_rejected_without_paid_transport(chat_image_env, monkeypatch):
    client, store, product, settings, owner = chat_image_env
    uploaded = client.post('/api/v1/chat-images', files={'file': ('reference.png', picture(), 'image/png')}).json()
    task = client.post('/api/v1/agents/image-agent/runs', json={
        'message': '使用一张参考图，制作三折页正反面两张',
        'attachments': [{'type': 'image', 'id': uploaded['id']}],
    }).json()
    issuer = RuntimeTokenIssuer('isolated-reference-batch-fixture')
    token = issuer.issue(RuntimePrincipal('tenant-a', 'image-agent', 'p', ('image:generate',), 4102444800))
    observed = stub_provider(monkeypatch, [])
    with pytest.raises(ImageRequestError) as error:
        asyncio.run(PlatformMCPService(store, issuer, settings).image_generation(
            token, 'Tool omitted quantity', [], '1:1', reference_images=[uploaded['id']],
            execution_scope='runtime:' + issuer.issue_task_scope('tenant-a', task['id'])))
    assert error.value.code == 'reference_batch_contract_required'
    assert observed == []
    with store.connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM generations WHERE task_id=?', (task['id'],)).fetchone()[0] == 0


def test_candidate_skill_formal_binding_is_draft_until_real_runtime_gate(execution):
    # All records are fresh isolated fixtures, never a production binding.
    draft = create_draft(execution.control, execution.actor)
    revision = draft['versions'][0]
    execution.control.bind_skills(draft['id'], revision['id'],
                                 [published_skill(execution, slug=SLUG, version='1.0.1')], execution.actor)
    execution.control.validate(draft['id'], revision['id'], execution.actor)
    detail = execution.control.detail(draft['id'])
    bound = detail['versions'][0]
    assert bound['status'] == 'draft'
    with pytest.raises(AgentCatalogError, match='Runtime Test Pending'):
        execution.control.publish(draft['id'], revision['id'], execution.actor, 'production')
    assert execution.control.detail(draft['id'])['versions'][0]['status'] == 'draft'
    with execution.store.connection() as conn:
        skills = conn.execute('SELECT * FROM agent_template_version_skills WHERE agent_template_version_id=?', (revision['id'],)).fetchall()
        assert len(skills) == 1
        chosen = published_skill(execution, slug=SLUG, version='1.0.1')
        assert skills[0]['skill_version_id'] == chosen['skill_version_id']
        assert conn.execute('SELECT COUNT(*) FROM tenant_agent_instances WHERE agent_id=?', (draft['id'],)).fetchone()[0] == 0
    if os.environ.get('BUSINESS_P0_SKILL_EVIDENCE'):
        Path(os.environ['BUSINESS_P0_SKILL_EVIDENCE']).write_text(json.dumps({
            'scope': 'EPHEMERAL_ISOLATED_FIXTURE_NOT_DEPLOYABLE_IDENTITY',
            'agent_id': draft['id'], 'revision_id': bound['id'], 'revision': bound['revision'],
            'configuration_fingerprint': bound['configuration_fingerprint'],
            'skills': bound['skills'], 'runtime_test': 'NOT_EXECUTED', 'publish': 'REJECTED_RUNTIME_TEST_PENDING',
            'tenant_instances': 0, 'real_provider_calls': 0,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
