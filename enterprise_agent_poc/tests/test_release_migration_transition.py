"""Exact forward Release migration contract on an isolated PostgreSQL 16 DB."""
from pathlib import Path
from urllib.parse import urlencode
import json
import os
import shutil
import uuid

import psycopg
from psycopg import sql
import pytest

from app.store import POCStore
from scripts import migrate, release_migration_transition as transition


pytestmark = pytest.mark.skipif(not os.environ.get("STAGE1_POSTGRES_ROOT"),
                                reason="isolated PostgreSQL cluster not provided")


@pytest.fixture
def schema_012_store(tmp_path, monkeypatch):
    root = Path(os.environ["STAGE1_POSTGRES_ROOT"]).resolve()
    assert root.parent == Path("/private/tmp") and root.name.startswith("ky-web-stage1-postgres.")
    assert (root / "stage1-isolated.marker").read_text().strip() == "ky-web-stage1-local-only"
    socket = root / "socket"
    port = int(os.environ.get("STAGE1_POSTGRES_PORT", "54329"))
    name = "phase_a_" + uuid.uuid4().hex
    with psycopg.connect(dbname="postgres", host=str(socket), port=port,
                         user="stage1_fixture", autocommit=True) as admin:
        assert admin.execute("SHOW listen_addresses").fetchone()[0] == ""
        assert 160000 <= int(admin.execute("SHOW server_version_num").fetchone()[0]) < 170000
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
    store = POCStore(f"postgresql:///{name}?" + urlencode({
        "host": str(socket), "port": port, "user": "stage1_fixture"
    }))
    baseline = tmp_path / "schema-012"
    baseline.mkdir()
    for path in migrate.migration_files()[:12]:
        shutil.copy2(path, baseline / path.name)
    with monkeypatch.context() as patch:
        patch.setattr(migrate, "MIGRATIONS", baseline)
        assert migrate.up(store) == 0
    store.seed_demo_data()
    # Historical V1 contract still owns exactly 001-014. New source also ships
    # 015; don't silently turn V1 regression into an undeclared three-step plan.
    legacy = tmp_path / 'legacy-014'
    legacy.mkdir()
    for path in migrate.migration_files()[:14]:
        shutil.copy2(path, legacy / path.name)
    monkeypatch.setattr(migrate, 'MIGRATIONS', legacy)
    return store


def declared():
    return json.loads(transition.DECLARATION_PATH.read_text(encoding="utf-8"))


def test_m1_m5_exact_read_only_plan_and_unknown_history_block(schema_012_store):
    store = schema_012_store
    with store.connection() as conn:
        before = transition._history(conn)
    plan = transition.read_only_plan(store, declared())
    assert plan["schema"] == "012" and plan["pending_versions"] == ["013", "014"]
    with store.connection() as conn:
        assert transition._history(conn) == before
        conn.execute("INSERT INTO schema_migrations(version,name,checksum) VALUES ('015','unknown.sql',?)", ("0" * 64,))
    with pytest.raises(transition.MigrationTransitionBlocked, match="unexpected_pending_or_unknown_migration"):
        transition.read_only_plan(store, declared())


def test_m6_m10_atomic_forward_apply_preserves_fingerprints_and_agent_absence(schema_012_store):
    store = schema_012_store
    before = transition.read_only_plan(store, declared())
    result = transition.apply_declared(store, declared())
    assert result == {"status": "declared_forward_migrations_applied", "schema": "014",
                      "applied": ["013", "014"], "schema_rollback": False,
                      "historical_revision_fingerprints_unchanged": True}
    after = transition.read_only_verify(store, declared())
    assert after["applied_versions"] == [f"{v:03d}" for v in range(1, 15)]
    assert after["pending_versions"] == [] and after["fingerprints"] == before["fingerprints"]
    with store.connection() as conn:
        assert conn.execute("SELECT to_regclass('public.agent_release_operations') AS name").fetchone()["name"]
        assert conn.execute("SELECT COUNT(*) AS n FROM agent_templates WHERE slug=?",
                            (transition.WECHAT_SLUG,)).fetchone()["n"] == 0
    with pytest.raises(transition.MigrationTransitionBlocked, match="unexpected_pending_or_unknown_migration"):
        transition.apply_declared(store, declared())


def test_m4_declared_checksum_mismatch_blocks_before_ddl(schema_012_store):
    changed = declared()
    changed["migrations"][1]["canonical_sha256"] = "0" * 64
    with pytest.raises(transition.MigrationTransitionBlocked, match="declared_migration_identity"):
        transition.read_only_plan(schema_012_store, changed)
    with schema_012_store.connection() as conn:
        assert [row["version"] for row in transition._history(conn)][-1] == "012"
