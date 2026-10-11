"""Isolated contract components; mock authority is NOT a Native approval."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from app import test_runtime_tooling as pair
from scripts import test_rag_native_successor as native


def approved():
    value = {'contract': pair.CONTRACT, 'environment': 'test',
        'application': {'path': '/opt/enterprise-agent-workbench-test/releases/rag-candidate',
            'source': 'a' * 40, 'tree': 'b' * 40, 'files_sha256': 'c' * 64},
        'tooling': {'path': '/opt/enterprise-agent-workbench-test/shared/source-qualifications/rag-tooling',
            'source': 'd' * 40, 'tree': 'e' * 40, 'files_sha256': 'f' * 64},
        'base': {'source': pair.BASE_SOURCE, 'tree': pair.BASE_TREE},
        'predecessor': copy.deepcopy(pair.PREDECESSOR), 'schema': copy.deepcopy(pair.SCHEMA),
        'production_authority': False, 'budget': {'provider': 0, 'wechat': 0, 'image': 0}}
    approval = {'contract': pair.CONTRACT, 'pair_sha256': '1' * 64,
        'authorization': 'RAG_NATIVE_TEST_PAIR_INDEPENDENTLY_APPROVED', 'production_authority': False,
        'application_source': 'a' * 40, 'application_tree': 'b' * 40,
        'tooling_source': 'd' * 40, 'tooling_tree': 'e' * 40}
    return value, approval


def record():
    value, _ = approved()
    return {'contract': 'RAG_EXACT_TEST_RELEASE_STATE_V1', 'pair_sha256': '1' * 64,
        'application_source': value['application']['source'], 'application_tree': value['application']['tree'],
        'predecessor': copy.deepcopy(pair.PREDECESSOR), 'baseline_knowledge_sha256': '2' * 64,
        'phase': 'ACTIVATING', 'business_writes_admitted': False}


def data():
    result = {name: [] for name in native.KNOWLEDGE_TABLES}
    result['users'] = [{'id': 'admin', 'tenant_id': 'internal-test-staging-v1'}]
    result['tasks'] = [{'id': 'untouched'}]
    return result


def with_upload(value):
    value['knowledge_bases'].append({'id': 'kb', 'tenant_id': 'internal-test-staging-v1'})
    value['knowledge_files'].append({'id': 'file', 'tenant_id': 'internal-test-staging-v1',
        'knowledge_base_id': 'kb', 'uploaded_by': 'admin'})
    value['knowledge_chunks'].append({'id': 'chunk', 'file_id': 'file',
        'tenant_id': 'internal-test-staging-v1', 'knowledge_base_id': 'kb'})
    return value


def test_pair_independent_exact_identity():
    value, approval = approved()
    pair.validate_pair(value, approval, '1' * 64)


@pytest.mark.parametrize('field,value', [
    ('contract', 'INTEGRATED_RUNTIME_RELEASE_PAIR_V1'), ('environment', 'production'),
    ('production_authority', True), ('budget', {'provider': 1, 'wechat': 0, 'image': 0}),
    ('budget', {'provider': False, 'wechat': 0, 'image': 0}),
    ('base', {'source': pair.PREDECESSOR['source'], 'tree': pair.PREDECESSOR['tree']}),
    ('predecessor', {'source': '0' * 40, 'tree': '0' * 40}),
    ('schema', dict(pair.SCHEMA, embedding_dimension=1536)),
    ('schema', dict(pair.SCHEMA, vector_version='0.6.0')),
    ('schema', dict(pair.SCHEMA, migration='016'))])
def test_pair_rejects_wrong_contract(field, value):
    body, approval = approved()
    body[field] = value
    with pytest.raises(RuntimeError, match='RAG_'):
        pair.validate_pair(body, approval, '1' * 64)


@pytest.mark.parametrize('field,value', [
    ('authorization', 'INTEGRATED_RUNTIME_RELEASE_RECOVERY_FIX_APPROVED'),
    ('application_source', '0' * 40), ('application_tree', '0' * 40),
    ('tooling_source', '0' * 40), ('tooling_tree', '0' * 40),
    ('pair_sha256', '0' * 64), ('production_authority', True)])
def test_old_or_mismatched_approval_rejected(field, value):
    body, approval = approved()
    approval[field] = value
    with pytest.raises(RuntimeError, match='RAG_INDEPENDENT_APPROVAL'):
        pair.validate_pair(body, approval, '1' * 64)


def mock_load(monkeypatch, parent=pair.BASE_SOURCE):
    body, approval = approved()
    monkeypatch.setenv('APP_ENV', 'test')
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    monkeypatch.setattr(pair.sys, 'dont_write_bytecode', True)
    monkeypatch.setattr(pair, 'APPLICATION', Path(body['application']['path']) / 'enterprise_agent_poc')
    calls = []
    def document(path):
        if path.name == 'runtime-pair.v1.json':
            return body, '1' * 64
        if path.name == 'runtime-pair.approval.v1.json':
            return approval, '2' * 64
        side = 'application' if path.name.startswith('application-') else 'tooling'
        return {'file.py': '0' * 64}, body[side]['files_sha256']
    def check(path, identity):
        calls.append(str(path))
        return {'source_commit': identity['source'], 'source_tree': identity['tree'], 'parents': [parent]}
    monkeypatch.setattr(pair, 'native_json', document)
    monkeypatch.setattr(pair, 'verify_checkout', check)
    return body, calls


def test_load_pair_verifies_both_independent_installations(monkeypatch):
    body, calls = mock_load(monkeypatch)
    assert pair.load_pair() == (body, '1' * 64)
    assert calls == [body['application']['path'], body['tooling']['path']]


def test_application_must_be_direct_8ff_successor(monkeypatch):
    mock_load(monkeypatch, parent=pair.PREDECESSOR['source'])
    with pytest.raises(RuntimeError, match='RAG_APPLICATION_EXACT_PARENT'):
        pair.load_pair()


def test_no_environment_selected_authority(monkeypatch):
    monkeypatch.setenv('RUNTIME_PAIR_AUTHORITY', '/tmp/arbitrary')
    monkeypatch.setenv('PYTHONPATH', '/tmp/tooling')
    monkeypatch.setattr(pair, 'APPLICATION', Path('/tmp/arbitrary/enterprise_agent_poc'))
    with pytest.raises(RuntimeError, match='RAG_PAIR_UNREGISTERED_INSTALLATION'):
        pair.authority_root()


def test_registered_installation_cannot_disable_guard(monkeypatch):
    body, _ = approved()
    monkeypatch.setattr(pair, 'APPLICATION', Path(body['application']['path']) / 'enterprise_agent_poc')
    monkeypatch.setenv('ENTERPRISE_POC_TEST_RAG_NATIVE_REQUIRED', 'false')
    monkeypatch.setenv('APP_ENV', 'test')
    assert pair.native_installation() is True
    monkeypatch.setenv('APP_ENV', 'production')
    with pytest.raises(RuntimeError, match='RAG_NATIVE_TEST_ONLY'):
        pair.native_installation()


def test_no_approval_or_no_guard_never_starts(monkeypatch):
    monkeypatch.setattr(pair, 'native_installation', lambda: True)
    def reject(name):
        raise RuntimeError('RAG_INDEPENDENT_APPROVAL')
    monkeypatch.setattr(pair, 'tooling_module', reject)
    for role in native.ROLES:
        with pytest.raises(RuntimeError, match='RAG_INDEPENDENT_APPROVAL'):
            pair.startup(role)


def test_legacy_non_native_entry_unchanged(monkeypatch):
    monkeypatch.setattr(pair, 'native_installation', lambda: False)
    monkeypatch.setattr(pair, 'tooling_module', lambda name: pytest.fail('Unexpected Native import'))
    assert pair.startup('api') is False


def test_isolated_policy_not_issued_fails_before_data_access(monkeypatch):
    body, _ = approved()
    monkeypatch.setattr(pair, 'load_pair', lambda: (body, '1' * 64))
    monkeypatch.setattr(pair, 'authority_root', lambda: pair.ISOLATED_AUTHORITY)
    monkeypatch.setattr(pair, 'native_json', lambda path: pytest.fail('Historical data accessed'))
    with pytest.raises(RuntimeError, match='RAG_ISOLATED_DATA_CONTRACT_NOT_ISSUED'):
        native.native_state()


def test_append_only_knowledge_preserves_all_data_and_historical_guard_view():
    baseline_data = data()
    baseline_data['knowledge_documents'].append({'id': 1, 'tenant_id': 'existing-other-tenant', 'body': 'fixed'})
    baseline = native.knowledge_baseline(baseline_data)
    actual = with_upload(copy.deepcopy(baseline_data))
    before = copy.deepcopy(actual)
    sha, historical = native.validate_knowledge(actual, 'internal-test-staging-v1', ['admin'], baseline)
    assert historical == baseline_data
    assert actual == before
    assert sha != native.knowledge_state(baseline_data)
    assert historical['tasks'] == actual['tasks']


@pytest.mark.parametrize('change,error', [
    ('tenant', 'RAG_CROSS_TENANT'), ('user', 'RAG_CROSS_USER'),
    ('null_user', 'RAG_CROSS_USER'),
    ('file', 'RAG_CHUNK_FILE_RELATION'), ('base', 'RAG_BASE_RELATION'),
    ('chunk_base', 'RAG_CHUNK_BASE_RELATION'), ('duplicate', 'RAG_DUPLICATE_KNOWLEDGE_ID'),
    ('secondary', 'RAG_SECONDARY_PROFILE_CHANGE')])
def test_knowledge_extension_rejects_unapproved_graph(change, error):
    value = data()
    baseline = native.knowledge_baseline(value)
    with_upload(value)
    if change == 'tenant': value['knowledge_files'][0]['tenant_id'] = 'foreign'
    if change == 'user': value['knowledge_files'][0]['uploaded_by'] = 'foreign'
    if change == 'null_user': value['knowledge_files'][0]['uploaded_by'] = None
    if change == 'file': value['knowledge_chunks'][0]['file_id'] = 'foreign'
    if change == 'base': value['knowledge_files'][0]['knowledge_base_id'] = 'foreign'
    if change == 'chunk_base': value['knowledge_chunks'][0]['knowledge_base_id'] = None
    if change == 'duplicate': value['knowledge_chunks'].append(copy.deepcopy(value['knowledge_chunks'][0]))
    if change == 'secondary': value['knowledge_chunk_embeddings'].append({'tenant_id': 'internal-test-staging-v1'})
    with pytest.raises(RuntimeError, match=error):
        native.validate_knowledge(value, 'internal-test-staging-v1', ['admin'], baseline)


@pytest.mark.parametrize('operation', ['delete', 'mutate'])
def test_historical_knowledge_never_silently_discarded(operation):
    value = with_upload(data())
    baseline = native.knowledge_baseline(value)
    if operation == 'delete': value['knowledge_files'].clear()
    else: value['knowledge_files'][0]['uploaded_by'] = 'new'
    with pytest.raises(RuntimeError, match='RAG_BASELINE_DATA_CHANGED'):
        native.validate_knowledge(value, 'internal-test-staging-v1', ['admin'], baseline)


@pytest.mark.parametrize('tenant,actors,error', [
    ('foreign', ['admin'], 'RAG_EXACT_TENANT_SCOPE'),
    ('internal-test-staging-v1', [], 'RAG_EXACT_TENANT_SCOPE'),
    ('internal-test-staging-v1', ['foreign'], 'RAG_APPROVED_USER_SCOPE')])
def test_tenant_and_actor_scope(tenant, actors, error):
    value = data()
    with pytest.raises(RuntimeError, match=error):
        native.validate_knowledge(value, tenant, actors, native.knowledge_baseline(value))


@pytest.mark.parametrize('changed,admitted', [(False, False), (True, False), (True, True), (False, True)])
def test_recovery_never_executes_cleanup_or_claims_old_rag_healthy(changed, admitted):
    body, _ = approved()
    state = record()
    state['business_writes_admitted'] = admitted
    decision = native.validate_recovery(state, body, '1' * 64, ('3' if changed else '2') * 64)
    forward = changed or admitted
    assert decision['preserve_data'] is True
    assert decision['schema_down'] is decision['synthetic_cleanup'] is decision['executes_recovery'] is False
    assert decision['mode'] == ('FORWARD_SUCCESSOR_KEEP_DATA' if forward else 'EXACT_D8A_RESTORE_REQUIRES_NATIVE_GATES')
    assert decision['requires_protected_operator'] is True
    assert decision['requires_predecessor_native_gates'] is (not forward)
    assert decision['d8a_rag_health'] == ('NOT_SELECTED' if forward else 'KNOWN_UNAVAILABLE_NOT_HEALTHY')


@pytest.mark.parametrize('field,value', [
    ('pair_sha256', '0' * 64), ('application_source', pair.BASE_SOURCE),
    ('application_tree', pair.BASE_TREE), ('phase', 'UNKNOWN'),
    ('predecessor', {'source': '0' * 40, 'tree': '0' * 40}),
    ('baseline_knowledge_sha256', 'not-a-hash'), ('business_writes_admitted', 1)])
def test_unknown_recovery_state_fails_closed(field, value):
    body, _ = approved()
    state = record()
    state[field] = value
    with pytest.raises(RuntimeError, match='RAG_'):
        native.validate_recovery(state, body, '1' * 64, '2' * 64)


def test_service_cannot_start_before_write_admission_sealed(monkeypatch):
    body, _ = approved()
    monkeypatch.setattr(native, 'native_state', lambda: (body, '1' * 64, record(), {}))
    monkeypatch.setattr(native, 'service_snapshot', lambda: pytest.fail('Unexpected service access'))
    with pytest.raises(RuntimeError, match='RAG_WRITE_ADMISSION_REQUIRED'):
        native.guard('worker')


def service_states():
    path = approved()[0]['application']['path']
    return {role: {'pid': 100 + i, 'active': 'active', 'cwd': path + '/enterprise_agent_poc',
        'live_cwd': path + '/enterprise_agent_poc', 'exec_start':
        {'api': 'uvicorn app.main:app', 'mcp': 'python -m app.platform_mcp.server',
         'worker': 'python -m app.worker'}[role]} for i, role in enumerate(native.ROLES)}


def test_three_service_source_consistency():
    native.verify_services(service_states(), approved()[0]['application']['path'], 'COMMITTED')


@pytest.mark.parametrize('role', native.ROLES)
def test_mixed_live_version_denied(role):
    states = service_states()
    states[role]['live_cwd'] = '/old/source/enterprise_agent_poc'
    with pytest.raises(RuntimeError, match='RAG_MIXED_LIVE_SOURCE:' + role):
        native.verify_services(states, approved()[0]['application']['path'], 'ACTIVATING')


def test_activation_is_not_committed_health():
    states = service_states()
    states['worker'].update(pid=0, active='inactive', live_cwd=None)
    native.verify_services(states, approved()[0]['application']['path'], 'ACTIVATING', 'api')
    with pytest.raises(RuntimeError, match='RAG_SERVICE_NOT_RUNNING:worker'):
        native.verify_services(states, approved()[0]['application']['path'], 'COMMITTED')


def test_wrong_service_entry_denied():
    states = service_states()
    states['worker']['exec_start'] = 'python arbitrary.py'
    with pytest.raises(RuntimeError, match='RAG_SERVICE_ENTRY:worker'):
        native.verify_services(states, approved()[0]['application']['path'], 'COMMITTED')


def test_foreign_tooling_namespace_rejected(monkeypatch):
    import scripts
    body, _ = approved()
    monkeypatch.setattr(pair, 'load_pair', lambda: (body, '1' * 64))
    # Isolate namespace rejection from the earlier cached-module identity gate.
    monkeypatch.delitem(pair.sys.modules, 'scripts.test_rag_native_successor')
    monkeypatch.setattr(scripts, '__path__', ['/tmp/untrusted-tools'])
    with pytest.raises(RuntimeError, match='RAG_FOREIGN_SCRIPTS_NAMESPACE'):
        pair.tooling_module('test_rag_native_successor')


def test_foreign_cached_tooling_rejected_before_namespace(monkeypatch):
    body, _ = approved()
    monkeypatch.setattr(pair, 'load_pair', lambda: (body, '1' * 64))
    monkeypatch.setitem(pair.sys.modules, 'scripts.test_rag_native_successor',
        SimpleNamespace(__file__='/tmp/foreign_tooling.py'))
    with pytest.raises(RuntimeError, match='RAG_FOREIGN_IMPORTED_TOOLING'):
        pair.tooling_module('test_rag_native_successor')


@pytest.mark.parametrize('mode,uid,gid', [
    (stat.S_IFREG | 0o666, 0, 0), (stat.S_IFREG | 0o444, 1000, 0),
    (stat.S_IFREG | 0o444, 0, 1000), (stat.S_IFLNK | 0o777, 0, 0)])
def test_protected_file_trust_is_not_relaxed(monkeypatch, mode, uid, gid):
    monkeypatch.setattr(Path, 'lstat', lambda self: SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid))
    with pytest.raises(RuntimeError, match='RAG_PATH_TRUST'):
        pair.trusted(Path('/registered/seal'), readonly=True)


def test_external_gitdir_rejected_before_git_execution(tmp_path, monkeypatch):
    checkout = tmp_path / 'app'
    checkout.mkdir()
    external = tmp_path / 'lucky-writable-gitdir'
    external.mkdir()
    (checkout / '.git').write_text('gitdir: ' + str(external))
    def trust(path, **kwargs):
        if path == external:
            raise RuntimeError('RAG_PATH_TRUST')
    monkeypatch.setattr(pair, 'trusted', trust)
    monkeypatch.setattr(pair.subprocess, 'run', lambda *a, **k: pytest.fail('Git ran before metadata trust'))
    with pytest.raises(RuntimeError, match='RAG_PATH_TRUST'):
        pair.checkout_identity(checkout)


def test_external_common_dir_rejected_before_git(tmp_path, monkeypatch):
    marker = tmp_path / '.git'
    marker.mkdir()
    common = tmp_path / 'user-writable-common'
    common.mkdir()
    (marker / 'commondir').write_text(str(common))
    def trust(path, **kwargs):
        if path == common:
            raise RuntimeError('RAG_PATH_TRUST')
    monkeypatch.setattr(pair, 'trusted', trust)
    monkeypatch.setattr(pair.subprocess, 'run', lambda *a, **k: pytest.fail('Unsafe Git'))
    with pytest.raises(RuntimeError, match='RAG_PATH_TRUST'):
        pair.checkout_identity(tmp_path)


@pytest.mark.parametrize('bad_blob', [False, True])
def test_installed_bytes_must_match_both_approval_and_git_tree(tmp_path, monkeypatch, bad_blob):
    payload = b'approved\n'
    (tmp_path / 'file.py').write_bytes(payload)
    monkeypatch.setattr(pair, 'trusted', lambda *a, **k: None)
    monkeypatch.setattr(pair, 'checkout_identity', lambda path: {
        'source_commit': 'a' * 40, 'source_tree': 'b' * 40, 'parents': [pair.BASE_SOURCE]})
    blob = hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()
    monkeypatch.setattr(pair, 'git_read', lambda *a: ('100644 blob ' +
        ('0' * 40 if bad_blob else blob) + '\tfile.py\0').encode())
    identity = {'path': str(tmp_path), 'source': 'a' * 40, 'tree': 'b' * 40,
        'files': {'file.py': hashlib.sha256(payload).hexdigest()}}
    if bad_blob:
        with pytest.raises(RuntimeError, match='RAG_GIT_BLOB_MAP_MISMATCH'):
            pair.verify_checkout(tmp_path, identity)
    else:
        assert pair.verify_checkout(tmp_path, identity)['parents'] == [pair.BASE_SOURCE]


def test_startup_guard_before_import_and_no_native_bootstrap_ddl():
    project = Path(__file__).resolve().parents[1]
    for name, role in [('main.py', 'api'), ('worker.py', 'worker'), ('platform_mcp/server.py', 'mcp')]:
        source = (project / 'app' / name).read_text(encoding='utf-8')
        tree = ast.parse(source)
        assignment = next(n for n in tree.body if isinstance(n, ast.Assign) and
            any(isinstance(t, ast.Name) and t.id == 'RAG_NATIVE_BOOT' for t in n.targets))
        assert isinstance(assignment.value, ast.Call)
        assert assignment.value.args[0].value == role
        assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and
            n.func.attr in {'initialize', 'seed_demo_data'} for n in tree.body)
        bootstrap = [n for n in ast.walk(tree) if isinstance(n, ast.If) and
            isinstance(n.test, ast.UnaryOp) and isinstance(n.test.operand, ast.Name) and
            n.test.operand.id == 'RAG_NATIVE_BOOT']
        assert bootstrap
        assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and
                   n.func.attr == 'initialize' for block in bootstrap for n in ast.walk(block))
        assert 'RAG_NATIVE_WORKER_IMPORT' not in source


def test_recovery_interface_has_no_mutation_commands():
    source = Path(native.__file__).read_text(encoding='utf-8')
    for forbidden in ['DELETE FROM', 'DROP TABLE', 'ALTER TABLE', 'UPDATE ', 'INSERT INTO',
                      'systemctl restart', 'os.symlink(', '.unlink(', 'shutil.rmtree(']:
        assert forbidden not in source
