"""Bounded adapter components; native Linux/PG/unit proof is separate."""
import copy
from pathlib import Path

import pytest

from scripts import release_dual_source as b, release_schema016 as s
from scripts.release_manifest import validate_manifest_contract, ManifestContractError


def manifest():
    return dict(release_id='isolated-f8b-schema016',source_commit=b.APP_SOURCE,
        archive_sha256='a'*64,selected_files=['enterprise_agent_poc/app/main.py'],
        selected_file_count=1,build_platform='Linux-x86_64',forward_migrations=copy.deepcopy(s.PLAN),
        dual_source_release=s.declaration('b'*40,'c'*40))


def test_exact016_manifest():
    assert validate_manifest_contract(manifest())['forward_migrations']==s.PLAN


@pytest.mark.parametrize('change',[
    ('application_source','0'*40),('application_tree','0'*40),
    ('tooling_source','not-a-source'),('tooling_tree','not-a-tree'),
    ('recovery_contract','migration-015-exact-predecessor-recovery-v1'),('contract','old'),
])
def test_manifest_pair_mutations_blocked(change):
    value=manifest(); value['dual_source_release'][change[0]]=change[1]
    with pytest.raises(ValueError): validate_manifest_contract(value)


@pytest.mark.parametrize('key,value',[
    ('from_schema','014'),('target_schema','015'),('data_contract','legacy'),
    ('rollback_strategy','code_only_forward_safe'),('destructive_down_migration',True),
    ('target_table_set',[]),('exact_predecessor',{}),
])
def test_plan_mutations_blocked(key,value):
    data=manifest(); data['forward_migrations'][key]=value
    with pytest.raises(ValueError): validate_manifest_contract(data)


def test016_cannot_use_original_manifest_without_pair():
    data=manifest(); del data['dual_source_release']
    with pytest.raises(ManifestContractError,match='schema016_dual_source_required'):
        validate_manifest_contract(data)


def test016_without_declared_transition_stays_blocked():
    from scripts.release_migration_transition import declaration, MigrationTransitionBlocked
    data=manifest(); del data['dual_source_release']; del data['forward_migrations']
    with pytest.raises(MigrationTransitionBlocked,match='forward_migration_declaration_mismatch'):
        declaration(data)


@pytest.mark.parametrize('key,value',[
    ('version','017'),('canonical_sha256','0'*64),('git_blob','0'*40),
])
def test016_migration_identity_mutations_blocked(key,value):
    data=manifest(); data['forward_migrations']['migrations'][0][key]=value
    with pytest.raises(ManifestContractError,match='forward_migrations_016'):
        validate_manifest_contract(data)


def test_original_manifest_stays_original():
    data=manifest(); del data['dual_source_release'];del data['forward_migrations']
    assert validate_manifest_contract(data)==data


def test_development_checkout_grants_no_formal_authority():
    assert b.profile() is None
    with pytest.raises(b.BindingBlocked,match='formal_install_location'): b.bound(manifest())


@pytest.mark.parametrize('key', ['binding_transition','agent_productization_transition','runtime_only_release'])
def test016_has_no_productization_or_binding_mutation(key):
    data=manifest();data[key]={}
    with pytest.raises(ValueError): validate_manifest_contract(data)


def test_forward_target_never_returns_old_worker_on016():
    data=manifest()
    assert s.target({'schema':'015'},data)==s.PREDECESSOR['release_id']
    assert s.target({'schema':'016'},data)==data['release_id']
    with pytest.raises(b.BindingBlocked): s.target({'schema':'unknown'},data)


def test_native_catalog_includes_all016_constraints_and_journal():
    assert 'wechat_draft_operations' in s.TABLES and len(s.TABLES)==45
    assert {'columns','constraints','indexes','triggers','functions','extensions','ledger','tables','sequences'}==set(s.CATALOG)


def test_startup_default_deny_source_unchanged():
    root=Path(__file__).resolve().parents[1]
    assert "value['environment']!='test'" in (root/'app/wechat_draft_capability.py').read_text()
    assert "self.settings.environment!='test'" in (root/'app/wechat_draft_execution.py').read_text()
