"""Thin private-job orchestrator. Requires the adopted external Test entry.

Reuses IsolatedServices PG/Redis lifecycle, candidate migration/store/API/Worker/
MCP, and PostgreSQL pg_dump/pg_restore. Does not install, sign or switch PRIMARY.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlencode, quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_delivery_fixture import child_environment, need

SCRIPTS = Path(__file__).resolve().parent
SCRATCH = 704 * 1024**2
INPUTS = 63 * 1024**2
CAP = 768 * 1024**2
RESERVE = 256 * 1024**2
CHECKS = ('pg', 'redis', 'worker', 'mcp', 'authentication', 'portals', 'task',
          'sse_history', 'five_turns', 'full_text', 'idempotency', 'cancel',
          'unknown_recovery', 'tenant_user_isolation', 'old_reader_compatibility')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda: f.read(1024**2), b''):
            h.update(data)
    return h.hexdigest()


def load_lifecycle():
    # Load the reviewed lifecycle file, not an arbitrary candidate hook.
    spec = importlib.util.spec_from_file_location('delivery_existing_lifecycle', SCRIPTS / 'release_test_environment.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.RUNTIME_CACHE = Path('/runtime')
    return module


def safe_extract(archive, manifest_path, identity, bound, destination):
    from scripts.release_manifest import validate_manifest_contract
    manifest = validate_manifest_contract(json.loads(Path(manifest_path).read_text()))
    need(manifest['source_commit'] == identity['source'] and sha(archive) == bound['archive_sha256']
         == manifest['archive_sha256'] and sha(manifest_path) == bound['manifest_sha256'], 'TRANSFER_IDENTITY')
    need(not destination.exists(), 'EXTRACT_MUST_BE_NEW')
    # Preflight all members before any extraction; do not tar.extractall.
    with tarfile.open(archive) as tf:
        need(tf.pax_headers.get('comment') == identity['source'], 'ARCHIVE_SOURCE')
        members = tf.getmembers(); names = set(); files = set(); size = 0
        for m in members:
            p = PurePosixPath(m.name)
            need(m.name not in names and not p.is_absolute() and '..' not in p.parts and '\\' not in m.name
                 and p.parts and p.parts[0] == 'enterprise_agent_poc' and (m.isdir() or m.isfile()), 'UNSAFE_MEMBER')
            names.add(m.name)
            if m.isfile():
                files.add(m.name); size += m.size
        need(files == set(manifest['selected_files']) and size <= INPUTS, 'ARCHIVE_FILE_SET_OR_SIZE')
        destination.mkdir(mode=0o700)
        for m in members:
            target = destination / m.name
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with target.open('xb') as out, tf.extractfile(m) as incoming:
                    for chunk in iter(lambda: incoming.read(65536), b''):
                        out.write(chunk)
                target.chmod(0o500 if m.mode & 0o111 else 0o400)
    return destination / 'enterprise_agent_poc'


def sandbox_preflight(parent):
    need(sys.platform == 'linux' and os.geteuid() != 0, 'NATIVE_NONROOT_ONLY')
    need(parent == Path('/private/tmp') and parent.resolve() == parent, 'PRIVATE_ROOT_REQUIRED')
    need(os.environ.get('TEST_DELIVERY_SANDBOX') == 'v1', 'ADOPTED_LAUNCHER_REQUIRED')
    # This is defense-in-depth, not a replacement for protected Root adoption.
    need({name for _, name in socket.if_nameindex()} <= {'lo'}, 'PRIVATE_NETWORK_REQUIRED')
    matches = [x.split() for x in Path('/proc/self/mountinfo').read_text().splitlines()
               if x.split()[4] == str(parent)]
    need(len(matches) == 1 and matches[0][matches[0].index('-') + 1] == 'tmpfs', 'BOUNDED_TMPFS_REQUIRED')
    stat = os.statvfs(parent)
    need(stat.f_blocks * stat.f_frsize <= SCRATCH and stat.f_bavail > 0, 'TMPFS_CAP_MISMATCH')
    for k in os.environ:
        need(not any(x in k for x in ('API_KEY', 'ACCESS_KEY', 'PASSWORD', 'DATABASE_URL', 'REDIS_URL')),
             'INHERITED_SECRET_OR_ENDPOINT')


class Job:
    def __init__(self, request, root_parent, *, dev_runtime=None):
        self.request = request; self.root_parent = root_parent
        self.lifecycle = load_lifecycle(); self.s = self.lifecycle.IsolatedServices()
        self.dev_runtime = dev_runtime
        if dev_runtime:
            # DEV uses existing binaries; it neither installs nor adopts them.
            # Keep the existing lifecycle's exact PG stop/marker contract.
            self.lifecycle.RUNTIME_CACHE = Path(dev_runtime['pg']).parent
        self.stage = 'PRECHECK'; self.evidence = []; self.project = None
        self.peak = 0; self.free_min = 1.; self.initial_used = 0
        self.process_identities = []

    def space(self):
        scratch = os.statvfs(self.root_parent)
        if getattr(self, 'dev_runtime', None):
            # DEV has no bounded tmpfs. Do not count the host's unrelated disk
            # usage as this job's usage, or claim cgroup/tmpfs enforcement.
            used = 0
            if self.s.root:
                for path in self.s._safe_root().rglob('*'):
                    try:
                        used += path.lstat().st_blocks * 512
                    except FileNotFoundError:  # PG atomic temporary-file rename
                        pass
        else:
            used = (scratch.f_blocks - scratch.f_bfree) * scratch.f_frsize
        self.peak = max(self.peak, used + self.input_size)
        host = os.statvfs('/')
        fraction = host.f_bavail / host.f_blocks
        self.free_min = min(self.free_min, fraction)
        need(self.peak <= CAP and fraction > .2 and
             host.f_bavail * host.f_frsize > .2 * host.f_blocks * host.f_frsize + RESERVE, 'CAPACITY_STOP')

    def command(self, argv, seconds=90, output=None):
        """All child output goes to bounded tmpfs, never an unbounded host log."""
        log = output or self.s.root / ('command-' + str(len(self.evidence)) + '.log')
        with log.open('xb') as stream:
            proc = subprocess.Popen(argv, cwd=self.project or self.s.root,
                                    env=child_environment(), stdout=stream, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + seconds
            try:
                while proc.poll() is None:
                    self.space()
                    need(time.monotonic() < deadline and log.stat().st_size <= 4 * 1024**2, 'COMMAND_BOUND')
                    time.sleep(.1)
                if proc.returncode != 0:
                    # Fixture assertions are fixed labels, not arbitrary child
                    # logs. Keep failure evidence without emitting secrets.
                    for line in log.read_bytes()[-4096:].decode(errors='replace').splitlines():
                        try:
                            value = json.loads(line)
                            code = value.get('failure_code', '')
                            if re.fullmatch('[A-Z0-9_:-]{1,100}', code):
                                self.evidence.append({'stage': self.stage, 'failure_code': code,
                                                      'exception_class': value.get('exception_class', 'UNKNOWN')})
                        except (ValueError, AttributeError, TypeError):
                            pass
                need(proc.returncode == 0, 'CHILD_FAILED:' + self.stage)
            finally:
                if proc.poll() is None:
                    proc.kill(); proc.wait(timeout=10)
        self.evidence.append({'stage': self.stage, 'log_sha256': sha(log), 'exit_code': proc.returncode})

    def role_args(self, role, project=None, config=None):
        return [sys.executable, '-I', '-B', str(SCRIPTS / 'test_delivery_fixture.py'),
                '--role', role, '--project', str(project or self.project),
                '--config', str(config or self.s.manifest)]

    def start_role(self, role):
        self.space(); self.stage = 'START_' + role.upper()
        log = (self.s.root / (role + '-' + str(time.monotonic_ns()) + '.log')).open('x')
        self.s.logs.append(log)
        proc = subprocess.Popen(self.role_args(role), cwd=self.project, env=child_environment(),
                                stdout=log, stderr=subprocess.STDOUT)
        self.s.children.append((role, proc)); return proc

    def stop_role(self, role, kill=False):
        for name, proc in self.s.children:
            if name == role and proc.poll() is None:
                (proc.kill if kill else proc.terminate)()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill(); proc.wait(timeout=5)

    def phase(self, name):
        self.stage = name.upper()
        self.command(self.role_args('checks') + ['--phase', name], seconds=180)

    def health(self):
        from urllib.request import urlopen
        from urllib.error import URLError
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.space()
            need(all(p.poll() is None for n, p in self.s.children if n in ('redis', 'api', 'mcp', 'worker')),
                 'CHILD_STARTUP_FAILED')
            try:
                with urlopen(f'http://127.0.0.1:{self.s.api_port}/api/health', timeout=1) as response:
                    value = json.load(response)
                    need(value['status'] == 'ok' and value['environment'] == 'test', 'API_HEALTH_FAILED')
                    mcp_port = int(self.s.config['environment']['ENTERPRISE_POC_MCP_PORT'])
                    with socket.create_connection(('127.0.0.1', mcp_port), timeout=1):
                        pass
                    self.health_value = value
                    for role, proc in self.s.children:
                        if role in ('api', 'mcp', 'worker'):
                            cwd = Path(f'/proc/{proc.pid}/cwd').resolve(strict=True)
                            need(cwd == self.project, 'RUNTIME_CWD_MISMATCH')
                            command = Path(f'/proc/{proc.pid}/cmdline').read_bytes()
                            self.process_identities.append({'role': role, 'pid': proc.pid,
                                'cwd': str(cwd), 'cmdline_sha256': hashlib.sha256(command).hexdigest(),
                                'source': self.request['candidate']['source']})
                    return
            except (URLError, TimeoutError, ConnectionRefusedError):
                time.sleep(.2)
        raise RuntimeError('API_HEALTH_TIMEOUT')

    def start(self):
        self.s.root = Path(tempfile.mkdtemp(prefix=self.lifecycle.ROOT_PREFIX, dir=self.root_parent))
        self.s.root.chmod(0o700)
        (self.s.root / 'stage2-isolated.marker').write_text(self.lifecycle.MARKER)
        redis = Path(self.dev_runtime['redis']) if self.dev_runtime else Path('/runtime/redis-7.0.15')
        pg = Path(self.dev_runtime['pg']) if self.dev_runtime else Path('/runtime/postgresql-16.6')
        self.s.redis_server = redis / 'bin/redis-server'
        self.s.redis_cli = redis / 'bin/redis-cli'
        self.pg = pg / 'bin'
        for binary in (self.s.redis_server, self.s.redis_cli, *(self.pg / n for n in
                       ('initdb', 'pg_ctl', 'createdb', 'pg_dump', 'pg_restore'))):
            need(binary.is_file() and os.access(binary, os.X_OK), 'TRUSTED_RUNTIME_INPUT_MISSING')
        self.s._prepare_evidence()
        # Reuse lifecycle implementations, including private sockets and markers.
        self.s._start_pg(self.pg); self.s._start_redis(); self.s._manifest()
        self.s.config['environment'].update(ENTERPRISE_POC_MODEL_PROVIDER_ID='zero-cost-delivery-fixture',
                                          ENTERPRISE_POC_MODEL_ID='synthetic-no-model')
        self.s.manifest.write_text(json.dumps(self.s.config))

    def database_fingerprint(self, database):
        import psycopg
        from psycopg import sql
        result = {}
        with psycopg.connect(host=str(self.s.root / 'pg/socket'), port=54330, user='stage1_fixture',
                             dbname=database, options='-c default_transaction_read_only=on') as conn:
            version = conn.execute('SHOW server_version').fetchone()[0].split()[0]
            vector = conn.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
            need(version == '16.6', 'PG_VERSION')
            need(vector == ('0.8.6',), 'VECTOR_VERSION')
            self.runtime_versions = {'postgresql': version, 'pgvector': vector[0],
                                     'python': sys.version.split()[0], 'redis': '7.0.15'}
            tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
            for (table,) in tables:
                h = hashlib.sha256(); count = 0
                # Synthetic data only; no content is emitted, sorted canonical rows.
                with conn.cursor(name='delivery_hash') as cursor:
                    cursor.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text').format(sql.Identifier(table)))
                    for row in cursor:
                        h.update(row[0].encode() + b'\n'); count += 1
                result[table] = {'rows': count, 'sha256': h.hexdigest()}
        return result

    def backup_restore(self, predecessor):
        """Restore only into a NEW private DB; never overwrite the candidate DB."""
        self.stage = 'ISOLATED_PG_BACKUP'
        before = self.database_fingerprint(self.s.database)
        common = ['-h', str(self.s.root / 'pg/socket'), '-p', '54330', '-U', 'stage1_fixture']
        dump = self.s.root / 'fixture-backup.dump'
        self.command([str(self.pg / 'pg_dump'), *common, '-Fc', '--no-owner', '--no-acl',
                      '-f', str(dump), self.s.database])
        restored = self.s.database + '_restore'
        self.stage = 'ISOLATED_PG_RESTORE'
        self.command([str(self.pg / 'createdb'), *common, restored])
        self.command([str(self.pg / 'pg_restore'), *common, '--exit-on-error', '--single-transaction',
                      '--no-owner', '--no-acl', '-d', restored, str(dump)])
        need(self.database_fingerprint(restored) == before, 'RESTORED_DATA_MISMATCH')
        config = json.loads(self.s.manifest.read_text())
        dsn = 'postgresql:///' + restored + '?' + urlencode({'host': str(self.s.root / 'pg/socket'),
               'port': '54330', 'user': 'stage1_fixture', 'options': '-c default_transaction_read_only=on'}, quote_via=quote)
        config['environment']['ENTERPRISE_POC_DATABASE_URL'] = dsn
        config['snapshot']['database_url'] = dsn
        path = self.s.root / 'restore-readonly.json'; path.write_text(json.dumps(config))
        self.stage = 'EXACT_PREDECESSOR_READBACK'
        self.command(self.role_args('readback', project=predecessor, config=path) +
                     ['--expected', str(self.s.root / 'expected-results.json')])
        need(self.database_fingerprint(restored) == before, 'OLD_READER_MUTATED_DATA')
        return {'backup_sha256': sha(dump), 'table_fingerprints': before,
                'restored_database_distinct': True, 'old_reader_source': self.request['observed_predecessor']['source'],
                'primary_restore': 'NOT_EXECUTED', 'isolated_restore': 'PASS'}

    def run(self, archive, manifest, prior_archive, prior_manifest):
        self.input_size = sum(Path(x).stat().st_size for x in (archive, manifest, prior_archive, prior_manifest))
        need(self.input_size <= INPUTS, 'COMBINED_INPUT_BUDGET')
        self.space(); self.start()
        self.stage = 'VERIFY_EXTRACT'
        self.project = safe_extract(archive, manifest, self.request['candidate'], self.request['artifact'], self.s.root / 'candidate')
        prior = safe_extract(prior_archive, prior_manifest, self.request['observed_predecessor'],
                             self.request['predecessor_artifact'], self.s.root / 'predecessor')
        # This bounded driver does not silently introduce a migration/rollback policy.
        migration_hashes = lambda p: {f.name: sha(f) for f in (p / 'migrations/postgres').glob('*.sql')}
        need(migration_hashes(self.project) == migration_hashes(prior), 'MIGRATION_TRANSITION_NOT_ADOPTED')
        self.stage = 'PROVISION_SYNTHETIC'
        self.command(self.role_args('provision'))
        self.start_role('mcp'); self.start_role('api'); self.start_role('worker'); self.health()
        self.phase('five')
        self.stop_role('worker'); self.phase('cancel')
        self.start_role('worker'); self.phase('cancel-check')
        self.phase('interrupt'); self.stop_role('worker', kill=True)
        self.start_role('worker'); self.phase('unknown-check')
        for role in ('worker', 'api', 'mcp'):
            self.stop_role(role)
        recovery = self.backup_restore(prior)
        self.space()
        return {'operation_id': self.request['operation_id'], 'source': self.request['candidate']['source'],
                'tree': self.request['candidate']['tree'], 'backend': 'postgresql-redis-worker-mcp',
                'provider_calls': 0, 'production_changes': 0, 'preview_changes': 0, 'primary_changes': 0,
                'checks': {x: 'PASS' for x in CHECKS},
                'disk_peak_bytes': self.peak, 'min_root_free_fraction': self.free_min,
                'stages': self.evidence, 'recovery': recovery,
                'process_identities': self.process_identities,
                'knowledge': self.health_value.get('knowledge', 'NOT_REPORTED'),
                'runtime_versions': getattr(self, 'runtime_versions', {}),
                'knowledge_semantic_acceptance': 'NOT_IN_SCOPE_LOCAL_HASH',
                'primary_activation': 'NOT_EXECUTED', 'native_systemd_per_service_parity': 'NOT_PROVEN'}


def dev_preflight(args):
    """Explicit non-Root development only; NEVER a protected Native receipt.

    No technical approval is fabricated. The user's DEV authorization permits
    only new local synthetic resources. Candidate/predecessor archives are
    rechecked against Git before any candidate code or database is started.
    """
    need(sys.platform == 'linux' and os.geteuid() != 0, 'DEV_NONROOT_ONLY')
    need(args.root == Path('/private/tmp') and args.root.resolve() == args.root,
         'DEV_PRIVATE_PARENT')
    need(args.repo and args.pg_root and args.redis_root, 'DEV_RUNTIME_AND_REPO_REQUIRED')
    need(not any(k in os.environ for k in ('DATABASE_URL', 'REDIS_URL', 'PYTHONPATH',
         'ENTERPRISE_POC_DATABASE_URL', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY')),
         'DEV_CLEAN_ENVIRONMENT_REQUIRED')
    from test_delivery_pipeline import read, artifact, identity
    request = read(args.request)
    for ident, archive, manifest, bound in (
        (request['candidate'], args.archive, args.manifest, request['artifact']),
        (request['observed_predecessor'], args.predecessor_archive,
         args.predecessor_manifest, request['predecessor_artifact'])):
        need(identity(args.repo, ident['source']) == ident, 'DEV_GIT_IDENTITY')
        need(artifact(args.repo, ident['source'], archive, manifest) == bound, 'DEV_GIT_ARTIFACT')
    for path in (args.pg_root, args.redis_root):
        need(path.is_absolute() and path.resolve(strict=True) == path, 'DEV_RUNTIME_PATH')
    free = os.statvfs(args.root)
    need(free.f_bavail * free.f_frsize > 2 * 1024**3 and free.f_bavail / free.f_blocks > .2,
         'DEV_CAPACITY')
    return request


def main():
    parser = argparse.ArgumentParser()
    for name in ('request', 'archive', 'manifest', 'predecessor-archive', 'predecessor-manifest', 'root'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--dev', action='store_true', help='Authorized independent DEV only; not TEST_NATIVE')
    for name in ('repo', 'pg-root', 'redis-root'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args(); job = None; result = None
    try:
        if args.dev:
            request = dev_preflight(args)
        else:
            need(not any((args.repo, args.pg_root, args.redis_root)), 'NATIVE_RUNTIME_FIXED')
            sandbox_preflight(args.root)
            request = json.loads(args.request.read_text())
        need(request['action'] == 'isolated_qualify' and request['environment'] == 'test'
             and request['calls'] == {'text': 0, 'image': 0, 'wechat': 0}
             and request['checks'] == list(CHECKS), 'EXACT_ZERO_COST_SCOPE')
        job = Job(request, args.root, dev_runtime={'pg': str(args.pg_root),
                  'redis': str(args.redis_root)} if args.dev else None)
        result = job.run(args.archive, args.manifest, args.predecessor_archive, args.predecessor_manifest)
        result.update(validation_level='DEV_INTEGRATION_PASS' if args.dev else 'PROTECTED_ISOLATED_QUALIFICATION',
                      test_native='NOT_EXECUTED' if args.dev else 'PENDING_EXTERNAL_ACCEPTANCE')
    except Exception as exc:
        result = {'status': 'FAILED', 'failed_stage': job.stage if job else 'PRECHECK',
                  'exception_class': type(exc).__name__, 'stages': job.evidence if job else [],
                  'failure_code': str(exc) if isinstance(exc, RuntimeError) and
                      re.fullmatch('[A-Z0-9_:-]{1,100}', str(exc)) else 'REDACTED'}
    finally:
        if job and job.s.root:
            try:
                # Existing lifecycle stops only its own recorded processes and
                # marked PG cluster. It deletes only its own synthetic root.
                job.s.stop()
            except Exception as exc:
                result = {'status': 'FAILED', 'failed_stage': 'CLEANUP', 'exception_class': type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if result.get('status') == 'FAILED':
        raise SystemExit(2)


if __name__ == '__main__':
    # -I removes the script directory. Add only this reviewed Tooling directory.
    sys.path.insert(0, str(SCRIPTS))
    sys.path.insert(0, str(SCRIPTS.parent))
    main()
