"""One exact UI-only Schema015 successor; consumed by the existing release entry.

No service writer, DDL, productization, caller-selected loader or authority issuer.
The final Source/tree belong in the immutable manifest and Root approval pins,
not in the Git source that would have to hash itself.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess

CONTRACT_ID = 'WECHAT_562_EXACT_UI_RUNTIME_ONLY_V1'
PREDECESSOR = {
    'release_id': '20261007-562f201-reference-dependency-retry-v1',
    'source_commit': '562f201e362f0b10ae4fe3a8dae9a8c4d9ef91d3',
    'source_tree': 'e5205965e57fce6ce2b65dd747a110d00f7f1b1a',
    'archive_sha256': '17f31cb139ef9a619e455e5999b407fc7cfd98b45c3fd3757912b16a2da39941',
    'raw_manifest_sha256': 'eb6d382b3bc0b720d54349293d4f2afe73f372c8fd3838beb3eb86d557a9811c',
    'manifest_sha256': 'f99d02e36e33e2d0df20127019d019c52f93b5fdb26ebd0105c306c2ae93968a',
}
UI_SOURCE = '63925e05021f46ed34d17be13fb0ad65a4ec5283'
STATIC_SHA = {
    'index.html': '09387cf18e2420019354d418cede7fee6c431302b8f3ef6b545b548bd8652f16',
    'workbench.js': '1bbbb2cb63773420fc15ed54d8cbc7f8d0f1fc7f640eccc53164e9d8d2a0c667',
    'workbench.css': '6f8576fba6cb6c199e2cf629f4fc136ccefc54dba8676780f04ee7de55220cc6',
    'wechat-article-ui.js': '1c4b48ed43268e2978728efa6a0d94c36867f46a1eb16770e38fe6ef37697bb5',
}
UI_FILES = tuple('enterprise_agent_poc/app/static/' + name for name in STATIC_SHA) + (
    'docs/WECHAT_MANUAL_RICHTEXT_PRODUCTION_BASE_PORT_V1_TEST_RESULT.json',
    'enterprise_agent_poc/tests/wechat_manual_export.test.cjs',
    'enterprise_agent_poc/tests/wechat_manual_export.playwright.cjs',
)
HELPERS = (
    'scripts/build_release.py', 'scripts/release_manifest.py',
    'scripts/release_runtime_recovery.py', 'scripts/release_verify.py',
    'scripts/schema_015_runtime_only.py', 'scripts/release_ui562.py',
    'deploy/wechat_562_ui_successor.v1.json',
)
SPEC = {
    'schema_version': 1, 'contract_id': CONTRACT_ID,
    'feature_source': UI_SOURCE,
    'feature_tree': '6781719c9cbdeb4cbc1215f22779a288514a28c7',
    'exact_predecessor': PREDECESSOR,
    'current_schema': '015', 'target_schema': '015', 'migration_action': 'NONE',
    'data_contract': 'member_account_status_v1',
    'recovery_mode': 'PREDECESSOR_ON_SCHEMA_015',
    'post_commit_runtime_rollback': 'ENABLED',
    'agent_transition': 'NONE', 'registry_staging': 'NONE',
}


def require(ok, check):
    from scripts.release_manifest import ManifestContractError
    if not ok:
        raise ManifestContractError(check)


def selected(manifest):
    value = manifest.get('runtime_only_release')
    return isinstance(value, dict) and value.get('schema_version') == 2


def binding(source, tree):
    value = {
        'schema_version': 2, 'contract_id': CONTRACT_ID,
        'source_commit': source, 'source_tree': tree,
        'release_mode': 'RUNTIME_ONLY', 'current_schema': '015', 'target_schema': '015',
        'migration_action': 'NONE', 'migration015_state': 'ALREADY_APPLIED',
        'pending': 0, 'unknown': 0, 'checksum_drift': 0,
        'migration015_blob': '96bba0ea64ade51670775cf6f69b806a2248342a',
        'recovery_contract': CONTRACT_ID, 'recovery_mode': 'PREDECESSOR_ON_SCHEMA_015',
        'post_commit_runtime_rollback': 'ENABLED', 'exact_predecessor': PREDECESSOR.copy(),
    }
    validate(value)
    return value


def validate(value):
    require(isinstance(value, dict) and type(value.get('schema_version')) is int,
            'ui562_versioned_binding_required')
    source, tree = value.get('source_commit'), value.get('source_tree')
    require(isinstance(source, str) and re.fullmatch('[0-9a-f]{40}', source)
            and isinstance(tree, str) and re.fullmatch('[0-9a-f]{40}', tree)
            and source != PREDECESSOR['source_commit'], 'ui562_source_tree_required')
    expected = {
        'schema_version': 2, 'contract_id': CONTRACT_ID,
        'source_commit': source, 'source_tree': tree,
        'release_mode': 'RUNTIME_ONLY', 'current_schema': '015', 'target_schema': '015',
        'migration_action': 'NONE', 'migration015_state': 'ALREADY_APPLIED',
        'pending': 0, 'unknown': 0, 'checksum_drift': 0,
        'migration015_blob': '96bba0ea64ade51670775cf6f69b806a2248342a',
        'recovery_contract': CONTRACT_ID, 'recovery_mode': 'PREDECESSOR_ON_SCHEMA_015',
        'post_commit_runtime_rollback': 'ENABLED', 'exact_predecessor': PREDECESSOR,
    }
    require(value == expected and all(type(value[k]) is int for k in
            ('schema_version', 'pending', 'unknown', 'checksum_drift')), 'ui562_exact_binding')


def profile(root, manifest):
    validate(manifest.get('runtime_only_release'))
    require(manifest['source_commit'] == manifest['runtime_only_release']['source_commit'],
            'ui562_manifest_source_binding')
    path = root / 'deploy/wechat_562_ui_successor.v1.json'
    require(path.is_file() and not path.is_symlink()
            and json.loads(path.read_text(encoding='utf-8')) == SPEC, 'ui562_contract_identity')
    return SPEC


def validate_git_source(repo, source, value):
    """Build-time proof, not a substitute for Root/native deployment authority."""
    validate(value)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args])
    require(value['source_commit'] == source
            and git('rev-parse', source + '^{tree}').decode().strip() == value['source_tree'],
            'ui562_git_source_tree')
    require(git('show', '-s', '--format=%P', source).decode().strip()
            == PREDECESSOR['source_commit'], 'ui562_direct_exact_parent')
    changed = set(git('diff', '--name-only', PREDECESSOR['source_commit'], source).decode().splitlines())
    allowed = set(UI_FILES) | {'enterprise_agent_poc/' + p for p in HELPERS} | {
        'enterprise_agent_poc/tests/test_wechat_562_ui_release.py'}
    require(set(UI_FILES) <= changed <= allowed, 'ui562_git_change_scope')
    for name in UI_FILES:
        require(git('show', source + ':' + name) == git('show', UI_SOURCE + ':' + name),
                'ui562_exact_seven_file_port')


def verify_pair(candidate, prior, manifest):
    """After both immutable archive identities passed, reject non-UI business changes."""
    profile(candidate, manifest)
    changed = set(HELPERS) | {'app/static/' + n for n in STATIC_SHA}
    def files(root):
        return {p.relative_to(root).as_posix(): p for p in root.rglob('*')
                if p.is_file() and '__pycache__' not in p.parts}
    new, old = files(candidate), files(prior)
    require(set(old) <= set(new) and set(new) - set(old) <= changed, 'ui562_installed_file_scope')
    for name, path in old.items():
        if name not in changed:
            require(path.read_bytes() == new[name].read_bytes(), 'ui562_business_bytes_unchanged')
    for name, digest in STATIC_SHA.items():
        data = (candidate / 'app/static' / name).read_bytes()
        if name == 'index.html':
            # Existing formal builder substitutes only these fixed-width tokens.
            for asset, token in (('workbench.js', b'__JS_SHA256_V1__'),
                                 ('workbench.css', b'__CSS_SHA256_V1_')):
                version = STATIC_SHA[asset][:16].encode()
                require(data.count(version) == 1, 'ui562_packaged_static_cache')
                data = data.replace(version, token)
        require(hashlib.sha256(data).hexdigest() == digest, 'ui562_static_bytes')


def tree_approval(manifest):
    import os
    validate(manifest['runtime_only_release'])
    require(os.environ.get('RELEASE_EXPECTED_SOURCE_TREE') ==
            manifest['runtime_only_release']['source_tree'], 'ui562_root_tree_approval_required')


def audit_predecessor_artifacts(archive, manifest):
    """Read retained original bytes; installed POSIX/native gates remain separate."""
    from scripts import rollback_preflight as gate
    from scripts.release_manifest import validate_manifest_contract
    require(hashlib.sha256(Path(archive).read_bytes()).hexdigest() == PREDECESSOR['archive_sha256'],
            'ui562_predecessor_archive_identity')
    raw = Path(manifest).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == PREDECESSOR['raw_manifest_sha256'],
            'ui562_predecessor_raw_manifest_identity')
    value = validate_manifest_contract(json.loads(raw))
    require((value['release_id'], value['source_commit'], value['archive_sha256'], gate.digest(value)) ==
            tuple(PREDECESSOR[k] for k in ('release_id','source_commit','archive_sha256','manifest_sha256')),
            'ui562_predecessor_canonical_identity')
    return {'archive': 'PASS', 'raw_manifest': 'PASS', 'canonical_manifest': 'PASS',
            'native_installed_identity': 'NOT_VERIFIED'}
