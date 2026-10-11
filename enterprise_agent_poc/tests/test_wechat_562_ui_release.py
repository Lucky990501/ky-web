"""Isolated contract tests. These do not claim native PG/systemd acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from scripts import release_ui562 as ui
from scripts import release_manifest as schema
from scripts import release_runtime_recovery as recovery
from scripts import schema_015_runtime_only as runtime
from scripts import rollback_preflight as gate

ROOT = Path(__file__).resolve().parents[1]


def manifest():
    return {'release_id': 'ui562-fixture', 'source_commit': 'a' * 40,
            'archive_sha256': 'b' * 64,
            'selected_files': ['enterprise_agent_poc/pyproject.toml'],
            'selected_file_count': 1, 'build_platform': 'fixture',
            'runtime_only_release': ui.binding('a' * 40, 'c' * 40)}


def test_new_manifest_and_old_contracts_coexist():
    value = schema.validate_manifest_contract(manifest())
    assert runtime.declaration(ROOT, value)['migration_action'] == 'NONE'
    assert recovery.declared(ROOT, value)['exact_predecessor'] == ui.PREDECESSOR
    assert recovery.PREDECESSOR['source_commit'].startswith('519c649')
    assert recovery.contract(ROOT)['exact_predecessor'] == recovery.PREDECESSOR
    old = json.loads((ROOT/'deploy/schema_015_runtime_only.v1.json').read_text())
    assert old['schema_version'] == 1 and old['recovery_contract'] != ui.CONTRACT_ID
    schema.validate_runtime_only_release(old)


@pytest.mark.parametrize('field,wrong', [
    ('source_commit','d'*40), ('schema_version',True), ('pending',False),
    ('unknown',1), ('checksum_drift',1), ('current_schema','014'),
    ('target_schema','016'), ('migration_action','APPLY'),
    ('recovery_contract','migration-015-exact-predecessor-recovery-v1'),
    ('post_commit_runtime_rollback','DISABLED'), ('migration015_blob','0'*40),
    ('source_tree','not-a-tree'), ('extra','not-approved')])
def test_manifest_binding_fail_closed(field, wrong):
    value = manifest()
    value['runtime_only_release'][field] = wrong
    with pytest.raises(schema.ManifestContractError):
        schema.validate_manifest_contract(value)


@pytest.mark.parametrize('field,wrong', [
    ('release_id','20261006-519c649-reference-image-oss-v1'),
    ('source_commit','519c649bd61d3bf3b1a6708410b9b7102eccb7a2'),
    ('source_tree','0'*40), ('archive_sha256','0'*64),
    ('raw_manifest_sha256','0'*64), ('manifest_sha256','0'*64)])
def test_wrong_predecessor_archive_manifest_tree_rejected(field, wrong):
    value = manifest()
    value['runtime_only_release']['exact_predecessor'][field] = wrong
    with pytest.raises(schema.ManifestContractError, match='ui562_exact_binding'):
        schema.validate_manifest_contract(value)


@pytest.mark.parametrize('extra', ['forward_migrations','binding_transition',
                                 'deferred_skill','skill_package_staging',
                                 'agent_productization_transition'])
def test_no_ddl_registry_or_productization_combination(extra):
    value = manifest()
    value[extra] = {}
    with pytest.raises(schema.ManifestContractError):
        schema.validate_manifest_contract(value)


def test_tree_pin_is_separate_root_approval_not_business_authorization(monkeypatch):
    value = manifest()
    monkeypatch.delenv('RELEASE_EXPECTED_SOURCE_TREE',raising=False)
    with pytest.raises(schema.ManifestContractError,match='root_tree_approval'):
        ui.tree_approval(value)
    monkeypatch.setenv('RELEASE_EXPECTED_SOURCE_TREE','d'*40)
    with pytest.raises(schema.ManifestContractError,match='root_tree_approval'):
        ui.tree_approval(value)
    monkeypatch.setenv('RELEASE_EXPECTED_SOURCE_TREE','c'*40)
    ui.tree_approval(value)


def test_new_profile_must_be_regular_exact_file(tmp_path):
    (tmp_path/'deploy').mkdir()
    path=tmp_path/'deploy/wechat_562_ui_successor.v1.json'
    spec=copy.deepcopy(ui.SPEC)
    spec['exact_predecessor']['source_commit']='d'*40
    path.write_text(json.dumps(spec))
    with pytest.raises(schema.ManifestContractError,match='contract_identity'):
        ui.profile(tmp_path,manifest())


def pair(tmp_path):
    before,after=tmp_path/'before',tmp_path/'after'
    (before/'app/static').mkdir(parents=True)
    (before/'deploy').mkdir()
    (before/'app/runtime.py').write_bytes(b'unchanged runtime')
    (before/'deploy/rollback_compatibility.json').write_bytes(b'unchanged historical contract')
    shutil.copytree(before,after)
    (after/'deploy/wechat_562_ui_successor.v1.json').write_text(json.dumps(ui.SPEC))
    for name,digest in ui.STATIC_SHA.items():
        data=(ROOT/'app/static'/name).read_bytes().replace(b'\r\n',b'\n')
        assert hashlib.sha256(data).hexdigest()==digest
        if name=='index.html':
            data=data.replace(b'__JS_SHA256_V1__',ui.STATIC_SHA['workbench.js'][:16].encode())
            data=data.replace(b'__CSS_SHA256_V1_',ui.STATIC_SHA['workbench.css'][:16].encode())
        (after/'app/static'/name).write_bytes(data)
    return before,after


def test_installed_pair_static_cache_and_unchanged_business_bytes(tmp_path):
    before,after=pair(tmp_path)
    ui.verify_pair(after,before,manifest())


@pytest.mark.parametrize('fault', ['runtime','missing','extra','static','placeholder'])
def test_installed_pair_refuses_unknown_or_nonui_state(tmp_path, fault):
    before,after=pair(tmp_path)
    if fault=='runtime': (after/'app/runtime.py').write_bytes(b'changed runtime')
    if fault=='missing': (after/'app/runtime.py').unlink()
    if fault=='extra': (after/'app/foreign.py').write_bytes(b'unapproved')
    if fault=='static': (after/'app/static/wechat-article-ui.js').write_bytes(b'changed UI')
    if fault=='placeholder':
        p=after/'app/static/index.html'
        p.write_bytes(p.read_bytes().replace(ui.STATIC_SHA['workbench.js'][:16].encode(),b'__JS_SHA256_V1__'))
    with pytest.raises(schema.ManifestContractError):
        ui.verify_pair(after,before,manifest())


def test_formal_switch_lock_commit_boundary_and_writer_are_not_replaced():
    script=(ROOT/'deploy/release_switch.sh').read_text()
    assert 'flock -n -E 75 9' in script
    assert script.index('technical-smoke smoke') < script.index('# RELEASE COMMIT POINT')
    assert script.index('final-state state') < script.index('# RELEASE COMMIT POINT')
    assert 'scripts/release_runtime_recovery.py receipt' in script
    assert 'scripts/release_runtime_recovery.py capture' in script
    assert 'trap \'fail_release\' ERR' in script


def test_receipt_and_unknown_binding_guard_still_apply(monkeypatch,tmp_path):
    from scripts import release_verify
    value=manifest()
    root=tmp_path/'releases'/value['release_id']/'enterprise_agent_poc'
    root.mkdir(parents=True)
    monkeypatch.setattr(recovery,'declared',lambda *_:ui.SPEC)
    monkeypatch.setattr(recovery,'recovery_profile',lambda *_:ui.SPEC)
    monkeypatch.setattr(recovery,'approval',lambda *_: {})
    # Isolate only the receipt-input branch; native service/PG checks remain NOT_VERIFIED.
    current=tmp_path/'release-current'
    # This is a receipt-input component test, NOT a POSIX switch rehearsal.
    original_resolve=Path.resolve
    original_is_symlink=Path.is_symlink
    monkeypatch.setattr(Path,'is_symlink',lambda p: True if p==current else original_is_symlink(p))
    monkeypatch.setattr(Path,'resolve',lambda p,*a,**kw: root if p==current else original_resolve(p,*a,**kw))
    monkeypatch.setattr(gate,'read_json',lambda _: {
        'phase':'POST_COMMIT_HEALTH','release_id':value['release_id'],
        'source_commit':value['source_commit'],'manifest_sha256':gate.digest(value),
        'recovery_contract_sha256':gate.digest(ui.SPEC),'predecessor':recovery.PREDECESSOR})
    with pytest.raises(gate.RollbackBlocked,match='release_commit_receipt_identity'):
        recovery.preflight(tmp_path,root,value)


@pytest.mark.parametrize('fault', [None,'source','tree','parent','scope','missing-ui','ui-bytes'])
def test_build_source_tree_parent_and_scope_are_bound(monkeypatch,fault):
    source='a'*40
    changed=list(ui.UI_FILES)+['enterprise_agent_poc/'+p for p in ui.HELPERS]
    if fault=='scope': changed.append('enterprise_agent_poc/app/agent_execution.py')
    if fault=='missing-ui': changed.remove(ui.UI_FILES[0])
    def git(command):
        args=command[3:]
        if args[0]=='rev-parse': return (('d' if fault=='tree' else 'c')*40+'\n').encode()
        if args[0]=='diff': return ('\n'.join(changed)+'\n').encode()
        if '--format=%P' in args: return ((('d'*40) if fault=='parent' else ui.PREDECESSOR['source_commit'])+'\n').encode()
        if args[0]=='show':
            return b'tampered' if fault=='ui-bytes' and args[1].startswith(source+':') else b'exact fixture UI blob'
        raise AssertionError(args)
    monkeypatch.setattr(ui.subprocess,'check_output',git)
    value=ui.binding(source,'c'*40)
    if fault=='source': value['source_commit']='d'*40
    if fault:
        with pytest.raises(schema.ManifestContractError): ui.validate_git_source(Path('.'),source,value)
    else: ui.validate_git_source(Path('.'),source,value)


def test_wrong_original_archive_or_raw_manifest_rejected(tmp_path):
    a,m=tmp_path/'original.tar.gz',tmp_path/'original.manifest.json'
    a.write_bytes(b'not the immutable original')
    m.write_text('{}')
    with pytest.raises(schema.ManifestContractError,match='archive_identity'):
        ui.audit_predecessor_artifacts(a,m)
    # No replacement artifact is constructed or accepted to hide missing bytes.


def test_gate_only_accepts_self_or_exact_predecessor_without_unknown_target():
    source=(ROOT/'scripts/release_runtime_recovery.py').read_text()
    assert 'exact = recovery[\'exact_predecessor\']' in source
    assert 'gate.require(self_target or prior_target' in source
    assert 'UNKNOWN_BINDING_STATE' in source
    assert 'unknown_current_service_' in source
    assert 'schema_rollback": False' in source
