"""Successor candidate, isolated ASGI/SQLite fixtures. No real providers/services."""
import pytest
from dataclasses import replace

from app import main
from app.auth import hash_password
from app.customer_workspace import capabilities
from app.skill_registry import SkillRegistry
from test_history import history_env, login, add_conversation


@pytest.fixture
def env(history_env, monkeypatch, tmp_path):
    e = history_env
    registry = SkillRegistry(e.store, tmp_path/'registry', main.skill_registry.bundled_root)
    with e.store.connection() as conn:
        conn.executescript(registry._sqlite_schema())
    monkeypatch.setattr(main, 'skill_registry', registry)
    e.registry = registry
    return e


@pytest.mark.parametrize('email', ['owner@history.test','admin@history.test'])
def test_nonplatform_cannot_enter_manage_or_platform_api(env, email):
    login(env.client, email)
    for url in ['/manage','/manage/agents','/manage/skills','/manage/enterprise',
                '/manage/members','/manage/usage','/platform/agents','/platform/skills',
                '/api/v1/platform/tenants','/api/v1/platform/skills','/api/v1/platform/agents',
                '/api/admin/runtime/diagnostics']:
        assert env.client.get(url).status_code == 403, url
    assert env.client.post('/api/admin/runtime/test',json={'mode':'ok'}).status_code == 403


def test_platform_metadata_does_not_grant_private_history(env):
    conversation = add_conversation(env, agent_id='copywriting-agent', prompt='PRIVATE-DRAFT')
    env.registry.grant_platform_admin(env.admin['id'])
    login(env.client, 'admin@history.test')
    for url in ['/manage','/manage/members','/platform/agents','/api/v1/platform/tenants']:
        assert env.client.get(url).status_code == 200
    tenants=env.client.get('/api/v1/platform/tenants').json()
    assert {x['id'] for x in tenants} == {'tenant-a','tenant-b'}
    assert all(set(t)=={'id','name','members'} for t in tenants)
    assert env.client.get(f'/api/v1/conversations/{conversation.conversation_id}').status_code == 404


def test_settings_role_and_expired_session(env):
    for url in ['/workspace/settings','/workspace/settings/brand','/workspace/settings/members',
                '/workspace/settings/knowledge','/workspace/settings/assets','/workspace/settings/usage',
                '/workspace/settings/agents','/api/v1/workspace/settings','/api/v1/members',
                '/api/v1/enterprise-config','/api/v1/knowledge/files','/api/v1/assets']:
        assert env.client.get(url).status_code == 403, url
    login(env.client, 'admin@history.test')
    assert env.client.get('/workspace/settings').status_code == 200
    assert env.client.get('/workspace/settings/unknown').status_code == 404
    data=env.client.get('/api/v1/workspace/settings').json()
    assert data['tenant_id']=='tenant-a'
    assert data['usage']['unit']=='credits'
    assert data['usage']['personal_allocation_supported'] is False
    assert data['usage']['provider_balance_verified'] is False
    env.client.cookies.clear()
    env.client.cookies.set('workbench_session','expired-fixture')
    assert env.client.get('/workspace/settings').status_code==200  # Login shell only.
    assert env.client.get('/api/v1/workspace/settings').status_code==401
    assert env.client.get('/api/v1/platform/tenants').status_code==401


def test_member_crud_and_cross_tenant_target_rejection(env):
    login(env.client,'admin@history.test')
    foreign=env.product.user_by_email('foreign@history.test')
    assert env.client.put(f"/api/v1/members/{foreign['id']}/status",json={'status':'disabled'}).status_code==404
    response=env.client.post('/api/v1/members',json={'display_name':'Synthetic member',
        'email':'new@example.invalid','role':'member'})
    assert response.status_code==201
    member=response.json()['member']
    assert env.product.user_by_email('new@example.invalid')['tenant_id']=='tenant-a'
    assert env.client.put(f"/api/v1/members/{member['id']}/status",json={'status':'disabled'}).status_code==200
    assert env.client.put(f"/api/v1/members/{member['id']}/status",json={'status':'enabled'}).status_code==200
    assert env.client.post('/api/v1/members',json={'display_name':'Attempt','email':'p@example.invalid',
        'role':'platform_admin'}).status_code==422
    assert env.product.user_by_email('foreign@history.test')['account_status']=='enabled'


def test_brand_knowledge_assets_and_usage_are_tenant_scoped(env):
    login(env.client,'admin@history.test')
    original_b=env.store.enterprise_config('tenant-b')
    assert env.client.put('/api/v1/enterprise-config',json={'payload':{'brand_name':'Synthetic A'}}).status_code==200
    for forbidden in ['provider_api_key','credit_balance','skill_version','tenant_id']:
        assert env.client.put('/api/v1/enterprise-config',json={'payload':{forbidden:'not-allowed'}}).status_code==422
    knowledge=env.client.post('/api/v1/knowledge/text',json={'name':'Synthetic','content':'Synthetic facts'}).json()
    asset=env.client.post('/api/v1/assets',json={'name':'Synthetic','asset_type':'other',
        'url':'https://example.invalid/synthetic.png'}).json()
    env.product.create_user('tenant-b','admin-b@example.invalid',hash_password('HistoryTest!2026'),
                            'Synthetic B','enterprise_admin')
    login(env.client,'admin-b@example.invalid')
    assert env.client.get('/api/v1/workspace/settings').json()['tenant_id']=='tenant-b'
    assert env.client.get('/api/v1/enterprise-config').json()['brand_name']==original_b['brand_name']
    assert asset['id'] not in {x['id'] for x in env.client.get('/api/v1/assets').json()}
    assert env.client.delete(f"/api/v1/assets/{asset['id']}").status_code==404
    assert env.client.delete(f"/api/v1/knowledge/files/{knowledge['id']}").status_code==404
    assert env.client.get(f"/api/v1/knowledge/files/{knowledge['id']}").status_code==404
    login(env.client,'admin@history.test')
    assert env.client.delete(f"/api/v1/assets/{asset['id']}").status_code==200
    assert env.client.delete(f"/api/v1/knowledge/files/{knowledge['id']}").status_code==200
    usage=env.client.get('/api/v1/workspace/settings').json()['usage']
    assert set(usage)=={'credit_balance','credits_spent','debit_transactions','tasks','unit',
                        'personal_allocation_supported','provider_balance_verified'}


def test_legacy_enabled_does_not_prove_customer_readiness(env, monkeypatch):
    enabled=[{'id':identifier,'enabled':True} for identifier in
             ['copywriting-agent','campaign-agent','image-agent','wechat-agent']]
    rows=capabilities(enabled,runtime_configured=True)
    assert [a['id'] for a in rows if a['runnable']]==['copywriting-agent']
    assert all(a['state']=='not_migrated' for a in rows[1:])
    assert capabilities(enabled,runtime_configured=False)[0]['state']=='unavailable'
    assert capabilities([],runtime_configured=True)[0]['state']=='not_enabled'
    monkeypatch.setattr(main,'settings',replace(env.settings,model_id=''))
    actual=env.client.get('/api/v1/workspace').json()['customer_capabilities']
    assert all(not a['runnable'] for a in actual)
