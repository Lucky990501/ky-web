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
