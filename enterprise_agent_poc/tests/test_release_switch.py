import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from app.product_store import ProductStore
from app.skill_registry import SkillRegistry
from app.store import POCStore


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "release_switch.sh"


def test_release_switch_snapshots_dropins_before_enabling_rollback_trap():
    source = SCRIPT.read_text(encoding="utf-8")

    backup_created = source.index('backup=$(mktemp -d "$base/.release-switch.XXXXXX")')
    dropin_copied = source.index('cp "$dropin" "$backup/$service.conf"')
    trap_enabled = source.index("trap 'rollback; exit 1' ERR")
    first_dropin_write = source.index('cat > "$dropin"')
    dependency_preflight = source.index('"$runtime_venv/bin/python" -c')
    migration_status = source.index('migration_result=$(')
    skill_preflight = source.index('scripts/verify_bundled_skills.py')
    migration_preflight = source.index('scripts/migrate.py up')
    config_preflight = source.index('scripts/verify_runtime_config.py')

    dry_run_exit = source.index('if [[ "$mode" == "--preflight-only" ]]; then')
    assert dependency_preflight < skill_preflight < migration_status < config_preflight < dry_run_exit < migration_preflight
    assert migration_preflight < backup_created < dropin_copied < trap_enabled
    assert trap_enabled < first_dropin_write
    assert source[trap_enabled:].count('cp "$dropin" "$backup/$service.conf"') == 0
    assert "trap - ERR" in source[source.index("rollback() {"):trap_enabled]
    assert "set +e" in source[source.index("rollback() {"):trap_enabled]


def test_release_switch_health_gate_requires_healthy_production_json():
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'data.get("matches") is True' in source
    assert "grep -q" not in source
    assert 'data.get("status") == "ok"' in source
    assert 'data.get("knowledge") == "ok"' in source
    assert 'data.get("environment") == "production"' in source


def test_release_switch_health_timeout_rolls_back_before_exiting():
    source = SCRIPT.read_text(encoding="utf-8")

    timeout_start = source.index('if [[ "$attempt" == 30 ]]; then')
    timeout_end = source.index("  sleep 1", timeout_start)
    timeout_block = source[timeout_start:timeout_end]

    assert timeout_block.index("rollback") < timeout_block.index("exit 1")


def write_executable(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def switch_harness(tmp_path):
    """Execute the real Bash flow; replace only host paths and OS boundaries.

    The actual Skill CLI, approved bundle, SQLite Registry and config verifier
    run unchanged. PostgreSQL migration I/O/systemd are boundary stubs.
    """
    project = SCRIPT.parents[1]
    base = tmp_path / "workbench"
    candidate = base / "releases/candidate/enterprise_agent_poc"
    candidate.mkdir(parents=True)
    shutil.copytree(project / "app", candidate / "app", ignore=shutil.ignore_patterns("__pycache__", "static"))
    shutil.copytree(project / "skill_packages", candidate / "skill_packages")
    (candidate / "scripts").mkdir()
    for name in ("verify_bundled_skills.py", "verify_runtime_config.py"):
        shutil.copyfile(project / "scripts" / name, candidate / "scripts" / name)
    (candidate / "pyproject.toml").write_text("# fixture\n")
    data = base / "shared/runtime-data"
    data.mkdir(parents=True)
    store = POCStore(tmp_path / "registry.db")
    store.seed_demo_data()
    ProductStore(store).initialize()
    registry = SkillRegistry(store, data / "skill-registry", candidate / "skill_packages")
    registry.initialize()
    for slug, agent in [("campaign-planning", "campaign-agent"), ("poster-design", "image-agent")]:
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(f"{slug}/SKILL.md", f"# {slug} fixture 1.2.0\n")
        item = registry.import_archive(slug, "1.2.0", slug, "", stream.getvalue(), "fixture")
        registry.publish(item["id"], "fixture")
        registry.bind_agent(agent, slug, "1.2.0", "fixture")
    shared_env = base / "shared/enterprise-agent.env"
    shared_env.write_text(f"APP_ENV=production\nENTERPRISE_POC_DATABASE_URL={store.database_url}\n")
    shared_env.chmod(0o600)
    old = base / "releases/old/enterprise_agent_poc"
    old.mkdir(parents=True)
    (old.parent / 'old.manifest.json').write_text(json.dumps({'source_commit': 'a' * 40}))
    (base / "release-current").symlink_to(old, target_is_directory=True)
    executable = tmp_path / "release_switch.sh"
    source = SCRIPT.read_text().replace("base=/opt/enterprise-agent-workbench", f'base="{base}"')
    source = source.replace("/etc/systemd/system/", str(tmp_path / "systemd") + "/")
    write_executable(executable, source)
    events = tmp_path / "events"
    boundary = tmp_path / "boundary"
    write_executable(base / "venv/bin/python", f'''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["SWITCH_TEST_EVENTS"], "a") as f:
    f.write(json.dumps(args) + "\\n")
if args and args[0] == "scripts/rollback_preflight.py":
    print(json.dumps({{"status": "rollback_plan_passed" if "--check-plan" in args else "rollback_preflight_passed", "read_only": True}}))
    sys.exit(0)
if args and args[0] == "-":
    source = sys.stdin.read()
    assert "default_transaction_read_only=on" in source
    assert "SELECT version,name,checksum,applied_at" in source
    assert "ensure_history" not in source and "CREATE TABLE" not in source
    fault = os.environ.get("SWITCH_TEST_FAULT")
    print(json.dumps({{"migrations": [], "unknown_history_versions": [], "checksum_mismatch": int(fault == "migration"), "pending": 0}}))
    sys.exit(2 if fault == "migration" else 0)
if args and args[0] == "scripts/verify_bundled_skills.py":
    assert args[1] == "--data-dir"
    assert args[2] == os.environ["ENTERPRISE_POC_DATA_DIR"]
    fault = os.environ.get("SWITCH_TEST_FAULT")
    if fault == "missing":
        args = args[:1]
    elif fault in ("candidate", "wrong"):
        args[2] = str(Path.cwd() if fault == "candidate" else Path(os.environ["SWITCH_TEST_WRONG"]))
    elif fault == "skill":
        print(json.dumps({{"status": "BLOCKED", "check": "fixture_skill_fail"}}))
        sys.exit(2)
os.execv({sys.executable!r}, [{sys.executable!r}, *args])
''')
    write_executable(base / "venv/bin/pip", "#!/bin/sh\nexit 0\n")
    write_executable(base / "venv/bin/uvicorn", "#!/bin/sh\nexit 0\n")
    write_executable(boundary / "stat", f'#!/bin/sh\nexec "{sys.executable}" -c "import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[2:])" "$3"\n')
    write_executable(boundary / "systemctl", '#!/bin/sh\nprintf "systemctl:%s\\n" "$*" >> "$SWITCH_TEST_EVENTS"\ncase "$1" in is-active) echo active;; esac\nexit 0\n')
    write_executable(boundary / "curl", '#!/bin/sh\nprintf \'{"status":"ok","knowledge":"ok","environment":"production"}\\n\'\n')
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    env = os.environ.copy()
    # Host-boundary fixture gets its configuration solely from shared_env,
    # not the surrounding Stage 2 pytest process's isolated Settings.
    from app.settings import RUNTIME_CONFIG_ENV_NAMES
    for name in RUNTIME_CONFIG_ENV_NAMES:
        env.pop(name,None)
    env.pop("ENTERPRISE_POC_DATA_DIR", None)
    env.update(PATH=str(boundary) + os.pathsep + os.environ["PATH"], PYTHONDONTWRITEBYTECODE="1",
               DEEPSEEK_API_KEY="local-test-placeholder", GATEWAY_API_TOKEN="local-test-placeholder",
               SWITCH_TEST_EVENTS=str(events), SWITCH_TEST_WRONG=str(wrong))
    def snapshot():
        files = {p.relative_to(registry.data_root).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                 for p in registry.data_root.rglob("*") if p.is_file()}
        return store.database_path.read_bytes(), files
    return dict(base=base, candidate=candidate, data=data, registry=registry, shared_env=shared_env,
                old=old, script=executable, events=events, env=env, snapshot=snapshot, systemd=tmp_path / "systemd")


def run_switch(harness, *, fault="", preflight=True):
    env = {**harness["env"], "SWITCH_TEST_FAULT": fault}
    args = ["bash", str(harness["script"]), "candidate"]
    if preflight:
        args.append("--preflight-only")
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)


def test_real_entry_preflight_only_uses_shared_data_and_six_gates_without_writes(switch_harness):
    h = switch_harness
    before = h["snapshot"]()
    result = run_switch(h)
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    skill = next(item for item in records if item.get("mode") == "reuse")
    assert len(skill["checks"]) == 6 and skill["data_dir_resolved"] is True
    assert any(b["skill_slug"] == "campaign-planning" and b["version"] == "1.2.0" for b in skill["bindings"])
    assert any(b["skill_slug"] == "poster-design" and b["version"] == "1.2.0" for b in skill["bindings"])
    assert records[-1]["status"] == "preflight_passed"
    assert h["snapshot"]() == before
    assert (h["base"] / "release-current").resolve() == h["old"]
    assert not h["systemd"].exists()
    assert "systemctl:" not in h["events"].read_text()
    assert "scripts/migrate.py" not in h["events"].read_text()


@pytest.mark.parametrize("fault", ["missing", "candidate", "wrong", "skill", "migration"])
def test_actual_entry_failure_blocks_before_any_service_switch(switch_harness, fault):
    h = switch_harness
    before = h["snapshot"]()
    result = run_switch(h, fault=fault, preflight=False)
    assert result.returncode != 0
    assert (h["base"] / "release-current").resolve() == h["old"]
    assert not h["systemd"].exists()
    assert "systemctl:" not in h["events"].read_text()
    assert h["snapshot"]() == before
    if fault != "migration":
        assert '"-"' not in h["events"].read_text()


def test_real_entry_pass_then_controlled_switch_reuses_same_data_and_skips_no_pending_up(switch_harness):
    h = switch_harness
    before = h["snapshot"]()
    result = run_switch(h, preflight=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["status"] == "switched"
    assert (h["base"] / "release-current").resolve() == h["candidate"]
    events = h["events"].read_text()
    assert events.index("scripts/verify_bundled_skills.py") < events.index("systemctl:daemon-reload")
    assert "systemctl:restart" in events and "scripts/migrate.py" not in events
    for service in ("enterprise-agent-api", "enterprise-agent-mcp", "enterprise-agent-worker"):
        dropin = (h["systemd"] / f"{service}.service.d/release.conf").read_text()
        assert f'Environment=ENTERPRISE_POC_DATA_DIR={h["data"]}' in dropin
    assert h["snapshot"]() == before


def test_shared_env_conflicting_data_dir_blocks_without_overriding_configuration(switch_harness):
    h = switch_harness
    before = h["snapshot"]()
    with h["shared_env"].open("a") as stream:
        stream.write(f'ENTERPRISE_POC_DATA_DIR={h["candidate"]}\n')
    result = run_switch(h)
    assert result.returncode == 2
    assert (h["base"] / "release-current").resolve() == h["old"]
    assert not h["systemd"].exists()
    assert h["snapshot"]() == before


# Real migration history + immutable historical Release. Host operations alone
# are adapted; no fake migration records are fed to the rollback helper.
@pytest.fixture
def pg_rollback_catalog(request, tmp_path, approved_bundle):
    if not os.environ.get('STAGE1_POSTGRES_ROOT') or not os.environ.get('ROLLBACK_OLD_ARTIFACT_ROOT'):
        pytest.skip('Explicit private PostgreSQL and immutable historical artifact fixtures required')
    import psycopg
    from psycopg import sql
    from types import SimpleNamespace
    from urllib.parse import urlencode
    from datetime import datetime, timedelta, timezone
    import uuid
    from scripts import migrate
    root = Path(os.environ['STAGE1_POSTGRES_ROOT']).resolve()
    assert root.parent == Path('/private/tmp') and root.name.startswith('ky-web-stage1-postgres.')
    assert root.stat().st_uid == os.getuid()
    assert (root / 'stage1-isolated.marker').read_text().strip() == 'ky-web-stage1-local-only'
    socket = root / 'socket'
    assert socket.stat().st_mode & 0o077 == 0
    args = dict(dbname='postgres', host=str(socket), port=54329, user='stage1_fixture')

    def fresh_database(count=11, fault=None):
        name = 'rollback_' + uuid.uuid4().hex
        with psycopg.connect(**args, autocommit=True) as admin:
            assert 160000 <= int(admin.execute('SHOW server_version_num').fetchone()[0]) < 170000
            assert Path(admin.execute('SHOW data_directory').fetchone()[0]).resolve() == root / 'cluster'
            assert admin.execute('SHOW listen_addresses').fetchone()[0] == ''
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(name)))
        query = urlencode({k: args[k] for k in ('host', 'port', 'user')})
        store = POCStore(f'postgresql:///{name}?{query}')
        # Build the actual schema first. Malformed cases INSERT initial history
        # into a fresh database; never UPDATE/delete approved migration history.
        paths = migrate.migration_files()[:count]
        with store.connection() as c:
            for p in paths:
                c.execute(p.read_text())
            primary_key = '' if fault == 'duplicate_history' else ' PRIMARY KEY'
            c.execute('CREATE TABLE schema_migrations (version TEXT' + primary_key +
                      ', name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            rows = []
            for i, p in enumerate(paths):
                version, filename = p.name[:3], p.name[4:]
                checksum = migrate.migration_checksums(p)['canonical_checksum']
                if fault == 'crlf' and i < 7:
                    checksum = migrate.migration_checksums(p)['legacy_crlf_checksum']
                if fault == 'checksum' + version:
                    checksum = '0' * 64
                if fault == 'name008' and version == '008':
                    filename = 'other.sql'
                if fault == 'missing' + version:
                    continue
                applied = start + timedelta(seconds=i)
                if fault == 'applied_order' and i == 0:
                    applied += timedelta(days=1)
                rows.append((version, filename, checksum, applied))
            if fault == 'unknown012':
                rows.append(('012', 'unapproved.sql', '0' * 64, start + timedelta(seconds=12)))
            if fault == 'duplicate_history':
                rows.append(rows[8])
            for row in rows:
                c.execute('INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES (?,?,?,?)', row)
        return store

    fault = getattr(request.node, 'callspec', SimpleNamespace(params={})).params.get('fault')
    if request.node.name == 'test_postgres_legacy_crlf_history_still_strictly_compatible':
        fault = 'crlf'
    store = fresh_database(fault=fault)
    store.seed_demo_data()
    product = ProductStore(store)
    product.initialize()
    registry = SkillRegistry(store, tmp_path / 'controlled/shared/runtime-data/skill-registry', approved_bundle)
    registry.initialize()
    return SimpleNamespace(store=store, registry=registry, fresh_database=fresh_database)


@pytest.fixture
def pg_rollback_harness(pg_rollback_catalog, tmp_path):
    pg_catalog = pg_rollback_catalog
    import tarfile
    from scripts import rollback_preflight as gate
    artifact_root = Path(os.environ['ROLLBACK_OLD_ARTIFACT_ROOT']).resolve()
    assert artifact_root.parent == Path('/private/tmp')
    project = SCRIPT.parents[1]
    base = tmp_path / 'controlled'
    releases = base / 'releases'
    old_id = '20260913-6abccad'
    commit = '6abccad4db3e4802810380fae2082a30473f229c'
    old_dir = releases / old_id
    old_dir.mkdir(parents=True)
    for suffix in ('tar.gz', 'manifest.json'):
        shutil.copyfile(artifact_root / f'{old_id}.{suffix}', old_dir / f'{old_id}.{suffix}')
    with tarfile.open(old_dir / f'{old_id}.tar.gz') as archive:
        archive.extractall(old_dir, filter='data')
    old = old_dir / 'enterprise_agent_poc'
    new = releases / 'fixture-new' / 'enterprise_agent_poc'
    shutil.copytree(project, new, ignore=shutil.ignore_patterns('.venv', '.runtime-data', '__pycache__', '.pytest_cache', 'tests', 'skill_sources', '*.md', '.env*'))
    # *.md ignore is inappropriate for immutable bundled inputs: copy exact bundle.
    shutil.rmtree(new / 'skill_packages')
    shutil.copytree(project / 'skill_packages', new / 'skill_packages')
    # Synthetic approval applies ONLY inside this test Artifact. The repository
    # declaration remains PENDING until independent restart/E2E evidence exists.
    declaration = new / 'deploy/rollback_compatibility.json'
    d = json.loads(declaration.read_text())
    approval = d['epoch_contract']['schema_compatibility_evidence']
    approval.update(status='PASS', report_sha256='a'*64)
    approval['checks'] = dict.fromkeys(approval['checks'], 'PASS')
    d['approved_targets'][1]['compatibility_evidence']['report_sha256'] = 'a'*64
    declaration.write_text(json.dumps(d))
    helper = new / 'scripts/rollback_preflight.py'
    helper.write_text(helper.read_text().replace("BASE = Path('/opt/enterprise-agent-workbench')", f'BASE = Path({str(base)!r})'))
    systemd = tmp_path / 'systemd'
    switch = new / 'deploy/release_switch.sh'
    switch.write_text(switch.read_text().replace('base=/opt/enterprise-agent-workbench', f'base="{base}"').replace('/etc/systemd/system/', str(systemd) + '/'))
    data = base / 'shared/runtime-data'
    assert pg_catalog.registry.data_root == data / 'skill-registry'
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'TMPDIR') if k in os.environ}
    env.update(APP_ENV='production', ENTERPRISE_POC_DATABASE_URL=pg_catalog.store.database_url,
               ENTERPRISE_POC_DATA_DIR=str(data), DEEPSEEK_API_KEY='unused-test-placeholder',
               GATEWAY_API_TOKEN='unused-test-placeholder', PYTHONDONTWRITEBYTECODE='1')
    import shlex
    shared = base / 'shared/enterprise-agent.env'
    shared.write_text('\n'.join(k + '=' + shlex.quote(v) for k, v in env.items() if k not in ('PATH', 'LANG', 'TMPDIR')) + '\n')
    shared.chmod(0o600)
    venv = base / 'venv/bin'
    write_executable(venv / 'python', f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    for name in ('pip', 'uvicorn'):
        write_executable(venv / name, f'#!/bin/sh\nexec "{Path(sys.executable).with_name(name)}" "$@"\n')
    (base / 'release-current').symlink_to(old, target_is_directory=True)
    dropins = {}
    for name in ('enterprise-agent-api', 'enterprise-agent-mcp', 'enterprise-agent-worker'):
        p = systemd / f'{name}.service.d/release.conf'
        p.parent.mkdir(parents=True)
        p.write_text('[Service]\nWorkingDirectory=' + str(old) + '\n')
        dropins[name] = p.read_bytes()
    boundary = tmp_path / 'boundary'
    events = tmp_path / 'host-events.jsonl'
    write_executable(boundary / 'stat', f'#!{sys.executable}\nimport os,sys\nprint(oct(os.stat(sys.argv[-1]).st_mode&0o777)[2:])\n')
    write_executable(boundary / 'systemctl', f'''#!{sys.executable}
import json,sys
from pathlib import Path
with Path({str(events)!r}).open('a') as f:
    f.write(json.dumps({{'args':sys.argv[1:],'target':str(Path({str(base / 'release-current')!r}).resolve())}})+'\\n')
''')
    write_executable(boundary / 'curl', f'''#!{sys.executable}
import json,os
from pathlib import Path
old=Path({str(base / 'release-current')!r}).resolve()==Path({str(old)!r})
print(json.dumps({{'status':'ok' if old and os.environ.get('FAIL_OLD_HEALTH')!='true' else 'error','knowledge':'ok','environment':'production'}}))
''')
    write_executable(boundary / 'sleep', '#!/bin/sh\nexit 0\n')
    write_executable(boundary / 'install', f'''#!{sys.executable}
import sys,shutil
from pathlib import Path
assert sys.argv[1:4]==['-D','-m','0644']
p=Path(sys.argv[-1]);assert p.is_relative_to(Path({str(systemd)!r}))
p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(sys.argv[-2],p);p.chmod(0o644)
''')
    env['PATH'] = str(boundary) + os.pathsep + env['PATH']

    def pack_trusted_fixture():
        # Synthetic test archive, not build_release or a candidate production build.
        archive = new.parent / 'fixture-new.tar.gz'
        files = sorted(p for p in new.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
        with tarfile.open(archive, 'w:gz', pax_headers={'comment': 'f' * 40}) as tar:
            for p in files:
                tar.add(p, arcname=p.relative_to(new.parent).as_posix(), recursive=False)
        manifest = {'release_id': 'fixture-new', 'source_commit': 'f' * 40,
                    'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                    'selected_files': [p.relative_to(new.parent).as_posix() for p in files],
                    'selected_file_count': len(files), 'build_platform': 'isolated-test-fixture'}
        (new.parent / 'fixture-new.manifest.json').write_text(json.dumps(manifest))

    def snapshot():
        with pg_catalog.store.connection() as conn:
            tables = [('schema_migrations', 'version'), ('skills', 'id'), ('skill_versions', 'id'), ('skill_packages', 'id'), ('agent_skill_bindings', 'agent_id,skill_id')]
            rows = {t: [dict(r) for r in conn.execute(f'SELECT * FROM {t} ORDER BY {order}')] for t, order in tables}
        files = {p.relative_to(data).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in data.rglob('*') if p.is_file()}
        return rows, files

    pack_trusted_fixture()
    return dict(base=base, new=new, old=old, old_id=old_id, commit=commit, gate=gate, env=env,
                switch=switch, helper=helper, systemd=systemd, events=events, dropins=dropins,
                store=pg_catalog.store, registry=pg_catalog.registry, snapshot=snapshot, pack=pack_trusted_fixture,
                fresh_database=pg_catalog.fresh_database)


def pg_gate(h, *, plan=False, target_id=None, commit=None):
    return h['gate'].verify(h['base'], h['new'], target_id or h['old_id'], commit or h['commit'],
                            plan=plan, database_url=h['store'].database_url)


def pg_entry(h, mode='--rollback-preflight', **changes):
    args = ['bash', str(h['switch']), 'fixture-new']
    if mode:
        args.append(mode)
    if mode == '--rollback-preflight':
        args.extend([changes.pop('target_id', h['old_id']), changes.pop('commit', h['commit'])])
    return subprocess.run(args, env={**h['env'], **changes}, capture_output=True, text=True, timeout=60)


def test_postgres_approved_old_target_complete_schema_and_real_entry_read_only(pg_rollback_harness):
    h = pg_rollback_harness
    before = h['snapshot']()
    result = pg_gate(h)
    assert result['status'] == 'rollback_preflight_passed'
    assert result['applied_versions'] == [f'{i:03}' for i in range(1, 12)]
    assert result['target_known_versions'] == [f'{i:03}' for i in range(1, 8)]
    assert result['old_runner_invoked'] is False
    assert pg_entry(h).returncode == 0
    assert before == h['snapshot']() and not h['events'].exists()
    assert (h['base'] / 'release-current').resolve() == h['old']


@pytest.mark.parametrize('fault', ['unknown012', 'checksum010', 'name008', 'missing009', 'checksum002',
                                  'applied_order', 'duplicate_history', 'commit', 'release_id', 'manifest_missing',
                                  'manifest_abnormal', 'archive', 'source_file', 'target_outside', 'evidence', 'baseline_drift', 'parent_env'])
def test_postgres_malicious_history_and_identity_block_before_any_host_write(pg_rollback_harness, fault):
    h = pg_rollback_harness
    kwargs = {}
    if fault == 'commit': kwargs['commit'] = 'a' * 40
    elif fault == 'release_id': kwargs['target_id'] = 'unapproved'
    elif fault == 'manifest_missing': (h['old'].parent / (h['old_id'] + '.manifest.json')).unlink()
    elif fault == 'manifest_abnormal': (h['old'].parent / (h['old_id'] + '.manifest.json')).write_text('{"source_commit":"wrong"}')
    elif fault == 'parent_env': (h['old'].parent / '.env').write_text('# Unapproved empty environment input; no credentials\n')
    elif fault == 'archive':
        p = h['old'].parent / (h['old_id'] + '.tar.gz');p.write_bytes(p.read_bytes() + b'tampered')
    elif fault == 'source_file':
        p = h['old'] / 'migrations/postgres/001_workbench_v1.sql';p.write_bytes(p.read_bytes() + b'-- changed\n')
    elif fault == 'target_outside':
        outside = h['base'].parent / 'outside-target';shutil.move(h['old'].parent, outside)
        h['old'].parent.symlink_to(outside, target_is_directory=True)
    elif fault in {'evidence', 'baseline_drift'}:
        p = h['new'] / 'deploy/rollback_compatibility.json';d = json.loads(p.read_text())
        if fault == 'evidence':d['approved_targets'][0]['compatibility_evidence']['checks']['api'] = 'FAIL'
        else:d['baseline']['migrations'][-1]['canonical_sha256'] = '0' * 64
        p.write_text(json.dumps(d));h['pack']()
    before = h['snapshot']()
    dropins = {str(p):p.read_bytes() for p in h['systemd'].rglob('*.conf')}
    r = pg_entry(h, **kwargs)
    assert r.returncode == 2
    assert before == h['snapshot']() and not h['events'].exists()
    assert dropins == {str(p):p.read_bytes() for p in h['systemd'].rglob('*.conf')}


def test_postgres_legacy_crlf_history_still_strictly_compatible(pg_rollback_harness):
    h = pg_rollback_harness
    before = h['snapshot']()
    assert pg_gate(h)['status'] == 'rollback_preflight_passed' and h['snapshot']() == before


def test_postgres_old_runner_stays_failed_new_normal_runner_strict_and_preflight_unchanged(pg_rollback_harness):
    h = pg_rollback_harness
    from scripts import migrate
    old = subprocess.run([sys.executable, str(h['old'] / 'scripts/migrate.py'), 'status'], env=h['env'], capture_output=True, text=True)
    assert old.returncode == 2
    assert json.loads(old.stdout)['unknown_history_versions'] == ['008', '009', '010', '011']
    assert pg_entry(h, '--preflight-only').returncode == 0
    with h['store'].connection() as c:c.execute("INSERT INTO schema_migrations(version,name,checksum) VALUES ('012','unknown.sql',?)", ('0' * 64,))
    assert migrate.status(h['store']) == 2
    with pytest.raises(RuntimeError, match='未知'):migrate.up(h['store'])
    assert not h['events'].exists()


def test_postgres_pre_switch_plan_allows_only_an_applied_prefix_not_full_rollback(pg_rollback_harness):
    h = pg_rollback_harness
    # Genuine seven-migration schema, not a ten-migration DB disguised as seven.
    h['store'] = h['fresh_database'](count=7)
    assert pg_gate(h, plan=True)['status'] == 'rollback_plan_passed'
    with pytest.raises(h['gate'].RollbackBlocked):pg_gate(h)
    h['store'] = h['fresh_database'](count=7, fault='missing006')
    with pytest.raises(h['gate'].RollbackBlocked):pg_gate(h, plan=True)


def test_postgres_switch_health_fail_runs_trusted_gate_restores_old_then_health(pg_rollback_harness):
    h = pg_rollback_harness;before = h['snapshot']()
    r = pg_entry(h, '')
    assert r.returncode == 1 and 'rollback_preflight_passed' in r.stdout and 'rolled_back' in r.stdout
    assert 'schema_rollback":false' in r.stdout
    events = [json.loads(l) for l in h['events'].read_text().splitlines()]
    restarts = [e for e in events if e['args'][0] == 'restart']
    assert len(restarts) == 2 and restarts[0]['target'] == str(h['new']) and restarts[1]['target'] == str(h['old'])
    assert (h['base'] / 'release-current').resolve() == h['old']
    for name, b in h['dropins'].items():assert (h['systemd'] / f'{name}.service.d/release.conf').read_bytes() == b
    assert h['snapshot']() == before


def test_postgres_rollback_failed_gate_after_new_health_failure_does_not_restore_or_restart(pg_rollback_harness):
    h = pg_rollback_harness
    # Trigger corruption at the OS failure boundary AFTER plan & forward switch.
    curl = Path(h['env']['PATH'].split(os.pathsep)[0]) / 'curl'
    write_executable(curl, f'''#!{sys.executable}
import json,psycopg
from pathlib import Path
if Path({str(h['base'] / 'release-current')!r}).resolve()==Path({str(h['new'])!r}):
    with psycopg.connect({h['store'].database_url!r}) as c:
        c.execute("INSERT INTO schema_migrations(version,name,checksum) VALUES ('012','unapproved.sql',%s) ON CONFLICT DO NOTHING",('0'*64,))
print(json.dumps({{'status':'error','knowledge':'ok','environment':'production'}}))
''')
    r = pg_entry(h, '')
    assert r.returncode == 1 and 'rollback_BLOCKED' in r.stderr and 'rolled_back' not in r.stdout
    events = [json.loads(l) for l in h['events'].read_text().splitlines()]
    assert len([e for e in events if e['args'][0] == 'restart']) == 1
    assert all(e['target'] == str(h['new']) for e in events)
    assert (h['base'] / 'release-current').resolve() == h['new']
    assert list(h['base'].glob('.release-switch.*'))  # Snapshot retained for humans.


def test_postgres_old_health_failure_cannot_be_reported_as_successful_rollback(pg_rollback_harness):
    h = pg_rollback_harness
    r = pg_entry(h, '', FAIL_OLD_HEALTH='true')
    assert r.returncode == 1 and 'rollback_health_BLOCKED' in r.stderr and '"status":"rolled_back"' not in r.stdout
    assert (h['base'] / 'release-current').resolve() == h['old']
    assert list(h['base'].glob('.release-switch.*'))


def test_rollback_gate_order_is_fail_closed_and_old_runner_is_not_the_authority():
    source = SCRIPT.read_text()
    plan = source.index('--check-plan')
    up = source.index('scripts/migrate.py up')
    backup = source.index('backup=$(mktemp')
    rollback = source[source.index('rollback() {'):source.index("trap 'rollback; exit 1' ERR")]
    assert plan < up < backup
    assert rollback.index('scripts/rollback_preflight.py') < rollback.index('ln -sfn') < rollback.index('systemctl restart') < rollback.index('curl --fail')
    assert 'scripts/migrate.py' not in rollback
    assert 'rollback_BLOCKED' in rollback and 'rollback_health_BLOCKED' in rollback


def test_postgres_unapproved_rollback_plan_blocks_forward_before_any_service_write(pg_rollback_harness):
    h = pg_rollback_harness
    (h['old'].parent / (h['old_id'] + '.manifest.json')).write_text('{"source_commit":"' + 'a' * 40 + '"}')
    before = h['snapshot']()
    r = pg_entry(h, '')
    assert r.returncode == 2 and not h['events'].exists()
    assert (h['base'] / 'release-current').resolve() == h['old'] and h['snapshot']() == before


def test_postgres_legacy_only_evidence_does_not_authorize_productized_pilot_data(pg_rollback_harness):
    h = pg_rollback_harness
    # Isolated synthetic control-plane row only; no runtime or production Agent.
    with h['store'].connection() as c:
        c.execute("UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,advanced_at=CURRENT_TIMESTAMP,advanced_by_release_id='isolated-fixture',advanced_by_source_commit=?,advance_origin='controlled_advance' WHERE scope='agent_data_contract'", ('f'*40,))
        c.execute("INSERT INTO agent_templates(id,name,slug,description,icon,status,default_runtime_profile,credit_cost,skill_manifest,definition_source) VALUES ('synthetic-pilot','Synthetic','synthetic-pilot','Isolated fixture','test','active','test',1,'[]','productized')")
    r = pg_entry(h)
    assert r.returncode == 2 and 'rollback_target_below_data_compatibility_floor' in r.stdout and not h['events'].exists()
