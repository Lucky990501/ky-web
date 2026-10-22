"""Offline orchestration/negative tests, NOT PostgreSQL or Native acceptance."""
import ast
import asyncio
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))
sys.path.insert(0, str(PROJECT))
import test_delivery_native as n
import test_delivery_fixture as f
import test_delivery_pipeline as p


def test_isolated_cli_help_does_not_import_application_or_start_services():
    for name in ('test_delivery_native.py', 'test_delivery_fixture.py'):
        result = subprocess.run([sys.executable, '-I', '-B', str(PROJECT / 'scripts' / name), '--help'],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0 and 'usage:' in result.stdout


def test_nonlinux_native_entry_fails_before_root_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(n.sys, 'platform', 'win32')
    with pytest.raises(RuntimeError, match='NATIVE_NONROOT_ONLY'):
        n.sandbox_preflight(tmp_path)
    assert not list(tmp_path.iterdir())


def test_child_environment_drops_all_credentials_and_endpoints(monkeypatch):
    for key in ('DATABASE_URL', 'REDIS_URL', 'DEEPSEEK_API_KEY', 'HTTP_PROXY', 'PYTHONPATH',
                'ENTERPRISE_POC_DATABASE_URL', 'STAGE2_DEEPSEEK_TEST_API_KEY'):
        monkeypatch.setenv(key, 'synthetic-not-a-secret')
    env = f.child_environment()
    assert not any(k in env for k in ('DATABASE_URL', 'REDIS_URL', 'DEEPSEEK_API_KEY',
                   'HTTP_PROXY', 'PYTHONPATH', 'ENTERPRISE_POC_DATABASE_URL', 'STAGE2_DEEPSEEK_TEST_API_KEY'))


def archive_fixture(tmp_path, monkeypatch, member_name='enterprise_agent_poc/app/a.py', member_type=None):
    from scripts import release_manifest
    monkeypatch.setattr(release_manifest, 'validate_manifest_contract', lambda value: value)
    archive = tmp_path / 'a.tar.gz'; data = b'synthetic exact bytes\n'
    with tarfile.open(archive, 'w:gz', format=tarfile.PAX_FORMAT, pax_headers={'comment': 'a' * 40}) as tf:
        m = tarfile.TarInfo(member_name); m.size = len(data)
        if member_type:
            m.type = member_type; m.linkname = '/outside'; m.size = 0
        tf.addfile(m, io.BytesIO(data) if not member_type else None)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'source_commit': 'a' * 40, 'archive_sha256': n.sha(archive),
                                   'selected_files': [member_name]}))
    return archive, manifest, {'source': 'a' * 40}, {'archive_sha256': n.sha(archive), 'manifest_sha256': n.sha(manifest)}


def test_extract_exact_bytes_and_no_overwrite(tmp_path, monkeypatch):
    args = archive_fixture(tmp_path, monkeypatch)
    out = tmp_path / 'extracted'
    project = n.safe_extract(*args, out)
    assert (project / 'app/a.py').read_bytes() == b'synthetic exact bytes\n'
    with pytest.raises(RuntimeError, match='EXTRACT_MUST_BE_NEW'):
        n.safe_extract(*args, out)


@pytest.mark.parametrize('name,kind', [('enterprise_agent_poc/../../escape', None),
    ('/absolute', None), ('enterprise_agent_poc/link', tarfile.SYMTYPE),
    ('enterprise_agent_poc/hardlink', tarfile.LNKTYPE), ('enterprise_agent_poc/fifo', tarfile.FIFOTYPE)])
def test_unsafe_extract_rejected_before_writes(tmp_path, monkeypatch, name, kind):
    args = archive_fixture(tmp_path, monkeypatch, name, kind)
    out = tmp_path / 'extracted'
    with pytest.raises(RuntimeError): n.safe_extract(*args, out)
    assert not out.exists()


def test_wrong_source_rejected(tmp_path, monkeypatch):
    a, m, ident, bound = archive_fixture(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match='TRANSFER_IDENTITY'):
        n.safe_extract(a, m, {'source': 'b' * 40}, bound, tmp_path / 'out')


@pytest.mark.parametrize('free,expected', [(20.2, False), (23, True)])
def test_job_capacity_reserves_host_and_counts_tmpfs(monkeypatch, free, expected):
    job = object.__new__(n.Job); job.root_parent = Path('/private/tmp')
    job.input_size = 12 * 1024**2; job.peak = 0; job.free_min = 1
    def stat(path):
        if str(path) == '/':
            return SimpleNamespace(f_bavail=int(free / 100 * 10_240_000), f_blocks=10_240_000, f_frsize=4096)
        return SimpleNamespace(f_bfree=170000, f_blocks=180000, f_frsize=4096)
    monkeypatch.setattr(os, 'statvfs', stat, raising=False)
    if expected:
        job.space()
        assert job.peak == 10000 * 4096 + job.input_size
    else:
        with pytest.raises(RuntimeError, match='CAPACITY_STOP'): job.space()


def test_orchestration_uses_original_lifecycle_without_downloading():
    source = (PROJECT / 'scripts/test_delivery_native.py').read_text(encoding='utf-8')
    assert 'self.s._start_pg(self.pg)' in source and 'self.s._start_redis()' in source
    assert 'self.s.stop()' not in source  # cleanup uses job.s.stop in finally
    assert 'job.s.stop()' in source and 'prepare_node(' not in source and 'prepare_redis(' not in source
    assert 'pg_dump' in source and 'pg_restore' in source


def test_real_worker_and_mcp_not_replaced_by_fake_queue():
    source = (PROJECT / 'scripts/test_delivery_fixture.py').read_text(encoding='utf-8')
    assert 'from app.worker import run' in source and 'asyncio.run(run())' in source
    assert 'create_mcp().run' in source and 'ClientSession' in source
    assert 'main.agents._runtime = provider' in source
    assert 'RedisTaskQueue.from_settings =' not in source


def test_readback_has_no_initialize_or_migration():
    tree = ast.parse((PROJECT / 'scripts/test_delivery_fixture.py').read_text(encoding='utf-8'))
    block = next(x for x in ast.walk(tree) if isinstance(x, ast.If) and ast.unparse(x.test) == "role == 'readback'")
    text = ast.unparse(block)
    assert 'initialize(' not in text and 'up(' not in text
    assert 'product.task(' in text and 'product.conversation_detail(' in text


def test_provider_fixture_runs_real_mcp_call_boundary_and_never_paid(tmp_path, monkeypatch):
    from app.domain import RuntimeProfile
    calls = []
    async def fake_transport(url, token, tool, arguments=None):
        calls.append(tool); return SimpleNamespace(isError=False)
    monkeypatch.setattr(f, 'mcp_call', fake_transport)
    provider = f.fixture_provider(SimpleNamespace(token_secret='synthetic-delivery-only-secret',
        platform_mcp_url='http://127.0.0.1:12345/mcp'), tmp_path)
    profile = RuntimeProfile.build(tenant_id='tenant-a', agent_id='copywriting-agent',
        model_provider_id='zero-cost-delivery-fixture', model_id='synthetic', reasoning_effort='high', skill_manifest={})
    async def run():
        session = await provider.create_session(profile, 'synthetic context')
        return [event async for event in provider._stream_provider_turn(session, profile, 'synthetic prompt')]
    events = asyncio.run(run())
    assert events[-1].turn.text == f.OUTPUTS[0] and calls == ['enterprise_config_get']
    assert len(f.read_calls(tmp_path)) == 1 and f.read_calls(tmp_path)[0]['paid_calls'] == 0


def test_provider_rejects_wrong_tenant_before_transport(tmp_path, monkeypatch):
    from app.domain import RuntimeProfile, RuntimeSession
    monkeypatch.setattr(f, 'mcp_call', lambda *a: pytest.fail('transport must not be called'))
    provider = f.fixture_provider(SimpleNamespace(), tmp_path)
    profile = RuntimeProfile.build(tenant_id='other', agent_id='copywriting-agent', model_provider_id='fixture',
                                  model_id='synthetic', reasoning_effort='high', skill_manifest={})
    async def run():
        return [x async for x in provider._stream_provider_turn(RuntimeSession('fixture', profile.id), profile, 'x')]
    with pytest.raises(RuntimeError, match='FIXTURE_PROFILE_SCOPE'): asyncio.run(run())


def test_backup_restore_uses_new_database_and_old_reader(tmp_path):
    job = object.__new__(n.Job); job.s = SimpleNamespace(root=tmp_path, database='stage25_synthetic',
        manifest=tmp_path / 'config.json')
    job.s.manifest.write_text(json.dumps({'environment': {}, 'snapshot': {}}))
    job.pg = Path('/runtime/postgresql-16.6/bin')
    job.request = {'observed_predecessor': {'source': 'b' * 40}}
    commands = []
    job.database_fingerprint = lambda db: {'synthetic_table': {'rows': 5, 'sha256': 'a' * 64}}
    def command(args):
        commands.append(args)
        if 'pg_dump' in args[0]: (tmp_path / 'fixture-backup.dump').write_bytes(b'synthetic dump')
    job.command = command
    job.role_args = lambda role, **kw: ['readback', str(kw['project']), str(kw['config'])]
    result = job.backup_restore(tmp_path / 'exact-prior')
    assert commands[1][-1] == 'stage25_synthetic_restore'
    assert '--single-transaction' in commands[2] and '--exit-on-error' in commands[2]
    assert result['primary_restore'] == 'NOT_EXECUTED'
    config = json.loads((tmp_path / 'restore-readonly.json').read_text())
    assert 'default_transaction_read_only%3Don' in config['snapshot']['database_url']
    assert '+' not in config['snapshot']['database_url']


def test_failed_restore_never_runs_old_reader(tmp_path):
    job = object.__new__(n.Job); job.s = SimpleNamespace(root=tmp_path, database='stage25_one')
    job.pg = Path('/runtime/pg/bin'); job.database_fingerprint = lambda db: {}
    calls = []
    def command(args):
        calls.append(args)
        if 'pg_restore' in args[0]: raise RuntimeError('synthetic restore failure')
    job.command = command
    with pytest.raises(RuntimeError, match='synthetic restore failure'): job.backup_restore(tmp_path / 'prior')
    assert len(calls) == 3


def test_fail_closed_no_predecessor_inputs(scope=None):
    with pytest.raises(p.Block, match='PREDECESSOR_ARTIFACT_REQUIRED'):
        p.sandbox_command({'operation_id': 'unused'}, {'runtime_root': '/runtime', 'tooling_root': '/tooling',
            'python': '/runtime/python', 'qualification_driver': '/tooling/driver'}, Path('/op'), Path('/a'), Path('/m'))


def test_no_source_literal_in_new_driver_or_fixture():
    for name in ('test_delivery_native.py', 'test_delivery_fixture.py', 'test_delivery_checks.py'):
        content = (PROJECT / 'scripts' / name).read_text(encoding='utf-8')
        assert '801fd27' not in content and '77e5eb' not in content and '894bdd' not in content


@pytest.mark.parametrize('source', ['a' * 40, 'b' * 40])
def test_full_driver_phase_order_is_source_independent(tmp_path, monkeypatch, source):
    """Only orchestration proof: every process/DB hook is replaced locally."""
    request = {'candidate': {'source': source, 'tree': 'c' * 40},
               'observed_predecessor': {'source': 'd' * 40}, 'artifact': {}, 'predecessor_artifact': {},
               'operation_id': 'synthetic-order', 'checks': list(n.CHECKS)}
    job = object.__new__(n.Job); job.request = request; job.s = SimpleNamespace(root=tmp_path)
    job.peak = 12; job.free_min = .23; job.evidence = []; job.process_identities = []
    job.health_value = {'knowledge': 'degraded'}; order = []
    job.space = lambda: None; job.start = lambda: order.append('PG_REDIS_START')
    job.role_args = lambda role: [role]
    job.command = lambda argv, **kwargs: order.append(argv[0])
    job.start_role = lambda role: order.append('start:' + role)
    job.stop_role = lambda role, kill=False: order.append(('kill:' if kill else 'stop:') + role)
    job.health = lambda: order.append('health')
    job.phase = lambda name: order.append(name)
    job.backup_restore = lambda prior: order.append('restore-readback') or {'primary_restore': 'NOT_EXECUTED'}
    def extract(a, m, identity, bound, dest):
        (dest / 'enterprise_agent_poc/migrations/postgres').mkdir(parents=True)
        (dest / 'enterprise_agent_poc/migrations/postgres/001_synthetic.sql').write_text('synthetic')
        return dest / 'enterprise_agent_poc'
    monkeypatch.setattr(n, 'safe_extract', extract)
    inputs = []
    for name in ('archive', 'manifest', 'prior', 'prior-manifest'):
        pth = tmp_path / name; pth.write_bytes(b'synthetic'); inputs.append(pth)
    result = job.run(*inputs)
    assert result['source'] == source and result['knowledge_semantic_acceptance'].startswith('NOT_IN_SCOPE')
    assert order == ['PG_REDIS_START', 'provision', 'start:mcp', 'start:api', 'start:worker', 'health',
        'five', 'stop:worker', 'cancel', 'start:worker', 'cancel-check', 'interrupt', 'kill:worker',
        'start:worker', 'unknown-check', 'stop:worker', 'stop:api', 'stop:mcp', 'restore-readback']
    assert result['primary_activation'] == 'NOT_EXECUTED'


def test_qualification_checks_refuse_unimplemented_claims():
    assert n.CHECKS == p.CHECKS
    # Result acceptance is exact; a "PASS" for an unrelated/extra feature cannot qualify.
    import inspect
    assert "request['checks'] == list(CHECKS)" in inspect.getsource(n.main)


def test_archive_pair_required_in_execute_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'identity', lambda repo, source: {'source': source, 'tree': 'b' * 40, 'git_parents': []})
    monkeypatch.setattr(p, 'artifact', lambda *args: {})
    monkeypatch.setattr(p, 'schema_identity', lambda *args: {})
    obs = {'environment': 'test', 'hostname': 'synthetic-host', 'primary_source': 'c' * 40,
           'primary_tree': 'b' * 40, 'primary_path': '/synthetic', 'observed_at': 'synthetic'}
    with pytest.raises(p.Block, match='PREDECESSOR_ARTIFACT_PAIR_REQUIRED'):
        p.plan(tmp_path, 'a' * 40, 'b' * 40, tmp_path / 'a', tmp_path / 'm', obs, tmp_path / 'prior', None)


@pytest.mark.parametrize('code,stage,expected', [(0, 'FIVE', None),
    (2, 'EXACT_PREDECESSOR_READBACK', 'DRIVER_STAGE_FAILED:EXACT_PREDECESSOR_READBACK'),
    (2, 'untrusted secret-shaped detail', 'DRIVER_FAILED')])
def test_bounded_runner_preserves_only_sanitized_failure_stage(monkeypatch, code, stage, expected):
    process = SimpleNamespace(stdout=io.BytesIO(json.dumps({'failed_stage': stage}).encode()),
                              returncode=code, poll=lambda: code, wait=lambda **kw: code)
    monkeypatch.setattr(p.subprocess, 'Popen', lambda *a, **kw: process)
    stopped = []
    monkeypatch.setattr(p.subprocess, 'run', lambda argv, **kw: stopped.append(argv))
    if expected:
        with pytest.raises(p.Block, match=expected):
            p.bounded_run(['synthetic-command'], 'test-delivery-synthetic.service', 0, lambda: None)
    else:
        assert p.bounded_run(['synthetic-command'], 'test-delivery-synthetic.service', 0, lambda: None)['failed_stage'] == stage
    assert stopped == [['/usr/bin/systemctl', 'stop', 'test-delivery-synthetic.service']]
