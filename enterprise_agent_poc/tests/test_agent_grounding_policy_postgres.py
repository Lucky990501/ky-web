"""Real private-PostgreSQL verification of the nullable Revision policy."""
import os
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlencode

import psycopg
from psycopg import sql
import pytest

from app.agent_productization import AgentProductization, OVERRIDE_SCHEMA, canonical
from app.store import POCStore
from scripts import migrate
from test_agent_grounding_policy import ENABLED, _old_fingerprint
from test_agent_productization import new_draft, version
from test_agent_productization_postgres import pg_catalog


pytestmark = pytest.mark.skipif(not os.environ.get("STAGE1_POSTGRES_ROOT"), reason="private PostgreSQL root required")


def test_pg_jsonb_policy_db_constraint_and_published_immutability(pg_catalog):
    catalog = pg_catalog
    template_id, revision_id = new_draft(catalog, grounding_policy=ENABLED)
    with catalog.store.connection() as conn:
        row = dict(conn.execute("SELECT * FROM agent_template_versions WHERE id=?", (revision_id,)).fetchone())
        assert row["grounding_policy"] == ENABLED
        assert row["configuration_fingerprint"] == catalog.control._fingerprint(conn, row)
    with pytest.raises(psycopg.errors.CheckViolation), catalog.store.connection() as conn:
        conn.execute("UPDATE agent_template_versions SET grounding_policy=?::jsonb WHERE id=?",
                     (canonical({**ENABLED, "skip_audit": True}), revision_id))
    catalog.control.validate(template_id, revision_id, catalog.actor)
    catalog.control.publish(template_id, revision_id, catalog.actor, "local_test")
    with pytest.raises(psycopg.errors.RaiseException), catalog.store.connection() as conn:
        conn.execute("UPDATE agent_template_versions SET grounding_policy=NULL WHERE id=?", (revision_id,))
    assert version(catalog, template_id)["grounding_policy"] == ENABLED


def test_013_real_migration_preserves_old_revision_fingerprint(pg_catalog, tmp_path, monkeypatch):
    """Create an actual pre-013 row, apply 013, then verify old identity."""
    root = Path(os.environ["STAGE1_POSTGRES_ROOT"]).resolve()
    socket = root / "socket"
    port = int(os.environ.get("STAGE1_POSTGRES_PORT", "54329"))
    db_name = "grounding_" + uuid.uuid4().hex
    with psycopg.connect(dbname="postgres", host=str(socket), port=port, user="stage1_fixture", autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(db_name)))
    store = POCStore(f"postgresql:///{db_name}?" + urlencode({"host": str(socket), "port": port, "user": "stage1_fixture"}))
    try:
        baseline = tmp_path / "pre-013"
        baseline.mkdir()
        for path in migrate.migration_files()[:12]:
            shutil.copy2(path, baseline / path.name)
        with monkeypatch.context() as patch:
            patch.setattr(migrate, "MIGRATIONS", baseline)
            assert migrate.up(store) == 0
        with store.connection() as conn:
            assert conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name='agent_template_versions' AND column_name='grounding_policy'").fetchone() is None
            conn.execute("UPDATE platform_compatibility_state SET epoch='productized_v1',epoch_rank=2,advanced_at=CURRENT_TIMESTAMP,advanced_by_release_id='isolated-fixture',advanced_by_source_commit=?,advance_origin='controlled_advance' WHERE scope='agent_data_contract'", ("f" * 40,))
            conn.execute("INSERT INTO agent_templates(id,name,slug,description,icon,status,default_runtime_profile,credit_cost,skill_manifest,allows_image_generation,category,definition_source,lifecycle_status) VALUES ('historical-agent','Historical','historical-agent','','bot','disabled','default',1,'{}',FALSE,'general','productized','draft')")
            conn.execute("INSERT INTO agent_template_versions(id,agent_template_id,revision,name,description,icon,category,persona,runtime_provider,model_config_id,knowledge_requirement,asset_requirement,enterprise_config_requirement,output_policy,credit_cost,tenant_override_schema,configuration_fingerprint) VALUES ('historical-revision','historical-agent',1,'Historical','','bot','general','Synthetic','codex','codex-deepseek-v4-pro-high','optional','optional','optional','text',1,?,'pending')", (canonical(OVERRIDE_SCHEMA),))
            before = dict(conn.execute("SELECT * FROM agent_template_versions WHERE id='historical-revision'").fetchone())
            old_fingerprint = _old_fingerprint(conn, before)
            conn.execute("UPDATE agent_template_versions SET configuration_fingerprint=? WHERE id='historical-revision'", (old_fingerprint,))
        migration_013_only = tmp_path / "through-013"
        migration_013_only.mkdir()
        for path in migrate.migration_files()[:13]:
            shutil.copy2(path, migration_013_only / path.name)
        with monkeypatch.context() as patch:
            patch.setattr(migrate, "MIGRATIONS", migration_013_only)
            assert migrate.up(store) == 0
        with store.connection() as conn:
            row = dict(conn.execute("SELECT * FROM agent_template_versions WHERE id='historical-revision'").fetchone())
            assert row["grounding_policy"] is None
            assert row["configuration_fingerprint"] == old_fingerprint
            assert AgentProductization(store)._fingerprint(conn, row) == old_fingerprint
            assert conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"] == 13
        assert migrate.up(store) == 0
    finally:
        with psycopg.connect(dbname="postgres", host=str(socket), port=port, user="stage1_fixture", autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db_name)))
