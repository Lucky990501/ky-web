"""M1-M8: Stage 2 private PostgreSQL follows the formal release migrations."""
from pathlib import Path
import subprocess
import sys

import psycopg
import pytest

from scripts.migrate import migration_files
from scripts.release_test_environment import IsolatedServices, PROJECT


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Private Linux PostgreSQL fixture only")


def _connect(services):
    services.validate_manifest()
    return psycopg.connect(services.config["environment"]["ENTERPRISE_POC_DATABASE_URL"])


def _history(services):
    with _connect(services) as conn:
        return conn.execute("SELECT version,name,checksum FROM schema_migrations ORDER BY version").fetchall()


def test_m1_fresh_private_database_starts_empty():
    with IsolatedServices() as services:
        with _connect(services) as conn:
            assert conn.execute("SELECT to_regclass('public.users')").fetchone()[0] is None


@pytest.fixture(scope="module")
def migrated():
    with IsolatedServices() as services:
        services.migrate()
        yield services


def test_m2_formal_migrations_001_through_014_applied(migrated):
    assert [row[0] for row in _history(migrated)] == [path.name[:3] for path in migration_files()]


def test_m3_migration_count_is_current(migrated):
    assert len(_history(migrated)) == len(migration_files()) == 14


def test_m4_latest_migration_is_agent_release_provenance(migrated):
    assert migration_files()[-1].name == "014_agent_release_provenance.sql"
    assert _history(migrated)[-1][:2] == ("014", "agent_release_provenance.sql")


def test_m5_user_account_status_column_exists(migrated):
    with _connect(migrated) as conn:
        columns = conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users'").fetchall()
    assert ("account_status",) in columns


def test_m6_provisioned_synthetic_user_has_valid_status(migrated):
    subprocess.run([sys.executable, str(PROJECT / "scripts/stage2_preview.py"), "provision",
                    "--config", str(migrated.manifest)], cwd=PROJECT, env=migrated._child_env(),
                   check=True, stdout=subprocess.DEVNULL)
    with _connect(migrated) as conn:
        row = conn.execute("SELECT account_status FROM users WHERE email=%s", ("runtime@stage2.test",)).fetchone()
    assert row is not None and row[0] == "enabled"


def test_m7_repeat_formal_migration_is_idempotent(migrated):
    before = _history(migrated)
    migrated.migrate()
    assert _history(migrated) == before


def test_m8_fixture_cleanup_after_migration():
    with IsolatedServices() as services:
        services.migrate()
        root: Path = services.root
        assert root.exists()
    assert not root.exists()
