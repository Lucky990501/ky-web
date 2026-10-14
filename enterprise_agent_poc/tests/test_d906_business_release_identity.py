"""Exact identity continuation only; no Root/DB/Authority/Provider execution."""
import json
from pathlib import Path

import pytest
from scripts import schema015_successor_binding as binding
from scripts import v3_bootstrap_verified_entry as entry
from scripts.release_manifest import ManifestContractError, validate_skill_package_staging
from test_schema015_successor_binding import offline

D11 = 'd11c2fb5ddb6203eb5cee23832fafec47a2781f0'
ROOT = Path(__file__).resolve().parents[1]

def state_declaration():
    value = offline()
    value.update(schema_version=4, contract_id=binding.STATE_CONTRACT,
                 recovery_contract=binding.STATE_CONTRACT, qualification_profile=binding.STATE_CONTRACT,
                 state_contract=binding.STATE_CONTRACT, maximum_approval_lifetime_seconds=86400,
                 business_base_source=binding.P0_SOURCE, business_base_tree=binding.P0_TREE,
                 security_base_source=binding.STATE_BASE_SOURCE, security_base_tree=binding.STATE_BASE_TREE)
    value['successor']['parent_source_commit'] = D11
    return value

def test_runtime_predecessor_and_git_parent_are_distinct_exact_identities():
    assert binding.PREDECESSOR == {
        'release_id':'20261014-d9063ee-release-tty-pg1614-v1',
        'source_commit':'d9063ee43bc65e821457221ad824f5dde84fe408',
        'source_tree':'3a612584d278925afbcd1e2004fd47016fc9b6ce',
        'archive_sha256':'9d8f56ba6748ed44de6aa994f5972c86021ed02d1afd88de2fccfb9c2a54ee86',
        'raw_manifest_sha256':'30fb8c512253ce62cfce7c5bee7ef072c0d16dab5b436c47e94c12a7f7ab36fc',
        'manifest_sha256':'f54752cfd149526f98adad416a6626fdb02e347c356c52dc5ccc6b6ed50dd166'}
    assert entry.PARENT == binding.STATE_BASE_SOURCE == binding.P0_SOURCE == D11
    assert binding.STATE_BASE_TREE == binding.P0_TREE == 'ce8922c95be85c8edfdb117172107aaa72fb03e9'
    assert binding.PREDECESSOR['source_commit'] != entry.PARENT
    assert binding.validate(state_declaration())['exact_predecessor'] == binding.PREDECESSOR

@pytest.mark.parametrize('field,bad',[('source_commit','562f201e362f0b10ae4fe3a8dae9a8c4d9ef91d3'),
                                   ('archive_sha256','0'*64),('raw_manifest_sha256','0'*64),('manifest_sha256','0'*64)])
def test_wrong_runtime_predecessor_or_artifact_rejected(field,bad):
    value = state_declaration()
    value['exact_predecessor'][field] = bad
    with pytest.raises(binding.BindingBlocked, match='successor_binding_fixed_contract'):
        binding.validate(value)

def test_wrong_git_parent_rejected_without_rewriting_history():
    value = state_declaration()
    value['successor']['parent_source_commit'] = '7ca9be5ecf912f95d363b93ca11a9b0b82ea4fb2'
    with pytest.raises(binding.BindingBlocked, match='p0_direct_successor_required'):
        binding.validate(value)

def test_changed_schema_rejected():
    value = state_declaration()
    value['target_schema'] = '017'
    with pytest.raises(binding.BindingBlocked, match='successor_binding_fixed_contract'):
        binding.validate(value)

def test_unpinned_tooling_inventory_rejected():
    value = state_declaration()
    value['successor']['tooling_pins'].pop(binding.TOOLING_FILES[0])
    with pytest.raises(binding.BindingBlocked, match='successor_tooling_pins'):
        binding.validate(value)

def test_no_implicit_new_skill_authority_in_identity_proposal():
    proposal = json.loads((ROOT/'deploy/p0_controlled_release.v3.proposal.json').read_bytes())
    assert proposal['status'] == 'PROPOSED_CODE_ONLY_NOT_APPROVED_NOT_RELEASE_READY'
    assert proposal['predecessor_source'] == binding.PREDECESSOR['source_commit']
    assert proposal['business_base_source'] == D11
    assert 'does not authorize Registry staging' in proposal['scope']
    assert 'exact_d906_compatibility' in proposal['required_evidence']
    assert binding.P0_CHECKS['exact_d906_compatibility'] == 'REAL_D906_ON_SCHEMA015_WITH_PG_DATA'

def test_legacy_phase_a_cannot_stage_existing_wechat_1_0_1():
    candidate = {'schema_version':1,'rollback_strategy':'forward_safe_dormant',
                 'package':{'slug':'wechat-official-account-writing','version':'1.0.1',
                            'source_sha256':'1'*64,'artifact_sha256':'2'*64,'bundled_manifest_sha256':'3'*64}}
    with pytest.raises(ManifestContractError, match='skill_package_staging'):
        validate_skill_package_staging(candidate)

def test_existing_registry_package_lifecycle_keeps_agent_1_0_0_binding(tmp_path):
    """Real private Registry DB/files, not authenticated Production API authority.

    Existing platform import/test/publish delegates to these methods; package
    publication is distinct from Agent Revision Runtime Test/Publish/enable.
    """
    import shutil
    from app.store import POCStore
    from app.product_store import ProductStore
    from app.agent_productization import AgentProductization
    from app.skill_registry import SkillRegistry, SkillRegistryError
    from scripts.create_wechat_official_account_agent_v1 import create_draft

    bundle = ROOT/'skill_packages'
    old_bundle = tmp_path/'prior-bundle'
    old_bundle.mkdir()
    manifest = json.loads((bundle/'manifest.json').read_bytes())
    manifest['skills'] = [item for item in manifest['skills'] if not (
        item['skill_slug'] == 'wechat-official-account-writing' and item['version'] == '1.0.1')]
    (old_bundle/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    for item in manifest['skills']:
        destination = old_bundle/item['source_identity']['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bundle/item['source_identity']['path'], destination)
        artifact = old_bundle/item['artifact_path']
        artifact.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle/item['artifact_path'], artifact)
    store = POCStore(tmp_path/'registry.db')
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()
    product.create_user('tenant-a','identity-review@example.invalid','unused-fixture-only','Review fixture','enterprise_admin')
    control = AgentProductization(store)
    control.initialize()
    # Reproduce the observed productized_v1 pre-state in this private fixture;
    # never advance a real epoch or create a production approval.
    with store.connection() as connection:
        connection.execute("UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,advanced_at=CURRENT_TIMESTAMP,advanced_by_release_id='isolated-fixture',advanced_by_source_commit=?,advance_origin='controlled_advance' WHERE scope='agent_data_contract'", ('f'*40,))
    registry = SkillRegistry(store, tmp_path/'registry-files', old_bundle)
    registry.initialize()
    with store.connection() as connection:
        actor = connection.execute('SELECT id FROM users ORDER BY id LIMIT 1').fetchone()['id']
    registry.grant_platform_admin(actor)  # Private fixture only, never machine authority.
    create_draft(control, actor)  # Existing exact1.0.0 binding; no Runtime/Publish.
    def agent_state():
        with store.connection() as connection:
            return {name:[dict(row) for row in connection.execute(query)] for name,query in {
                'templates':"SELECT id,slug,lifecycle_status,current_published_version_id FROM agent_templates ORDER BY id",
                'revisions':"SELECT id,revision,status,configuration_fingerprint FROM agent_template_versions ORDER BY id",
                'version_bindings':"SELECT * FROM agent_template_version_skills ORDER BY agent_template_version_id,skill_id",
                'legacy_bindings':"SELECT * FROM agent_skill_bindings ORDER BY agent_id,skill_id",
                'instances':"SELECT * FROM tenant_agent_instances ORDER BY tenant_id,agent_id"}.items()}
    before = agent_state()
    candidate = SkillRegistry(store, registry.data_root, bundle)
    with pytest.raises(SkillRegistryError, match='missing bundled version'):
        candidate.verify_bootstrap()
    with store.connection() as connection:
        metadata = connection.execute("SELECT name,description FROM skills WHERE slug=?", ('wechat-official-account-writing',)).fetchone()
    raw = (bundle/'wechat-official-account-writing/1.0.1.zip').read_bytes()
    item = candidate.import_archive('wechat-official-account-writing','1.0.1',metadata['name'],metadata['description'],raw,actor)
    assert item['status'] == 'draft'
    assert candidate.test_version(item['id'])['status'] == 'passed'
    assert candidate.publish(item['id'],actor)['status'] == 'published'
    assert candidate.verify_bootstrap()['status'] == 'ok'
    assert registry.verify_bootstrap()['status'] == 'ok'  # Prior bundle accepts unbound extra package.
    assert agent_state() == before
    with store.connection() as connection:
        current = connection.execute("SELECT sv.version FROM agent_template_version_skills b JOIN skill_versions sv ON sv.id=b.skill_version_id JOIN skills s ON s.id=b.skill_id WHERE s.slug=?", ('wechat-official-account-writing',)).fetchall()
    assert [row['version'] for row in current] == ['1.0.0']
