from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "release_switch.sh"


def test_release_switch_snapshots_dropins_before_enabling_rollback_trap():
    source = SCRIPT.read_text(encoding="utf-8")

    backup_created = source.index('backup=$(mktemp -d "$base/.release-switch.XXXXXX")')
    dropin_copied = source.index('cp "$dropin" "$backup/$service.conf"')
    trap_enabled = source.index("trap 'rollback; exit 1' ERR")
    first_dropin_write = source.index('cat > "$dropin"')
    dependency_preflight = source.index('"$runtime_venv/bin/python" -c')
    migration_preflight = source.index('scripts/migrate.py up')
    config_preflight = source.index('scripts/verify_runtime_config.py')

    assert dependency_preflight < migration_preflight < config_preflight
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
