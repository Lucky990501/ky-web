from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "release_switch.sh"


def test_release_switch_snapshots_dropins_before_enabling_rollback_trap():
    source = SCRIPT.read_text(encoding="utf-8")

    backup_created = source.index('backup=$(mktemp -d "$base/.release-switch.XXXXXX")')
    dropin_copied = source.index('cp "$dropin" "$backup/$service.conf"')
    trap_enabled = source.index("trap 'rollback; exit 1' ERR")
    first_dropin_write = source.index('cat > "$dropin"')
    dependency_preflight = source.index('"$runtime_venv/bin/python" -c')
    migration_status = source.index('scripts/migrate.py status')
    skill_preflight = source.index('scripts/verify_bundled_skills.py')
    migration_preflight = source.index('scripts/migrate.py up')
    config_preflight = source.index('scripts/verify_runtime_config.py')

    assert dependency_preflight < skill_preflight < migration_status < migration_preflight < config_preflight
    assert config_preflight < backup_created < dropin_copied < trap_enabled
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
