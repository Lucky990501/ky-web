"""Isolated PostgreSQL provenance tests; no Production database is reachable."""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.agent_productization import AgentCatalogError
from app.agent_release_provenance import AgentReleaseProvenance
from test_agent_productization_postgres import pg_catalog  # marked PG16 fixture


def begin(control, actor, slug="release-agent"):
    operation_id = str(uuid.uuid4())
    control.begin(operation_id=operation_id, release_identity="fixture-candidate",
                  source_identity="a" * 40, manifest_identity="b" * 64,
                  agent_slug=slug, actor=actor)
    return operation_id


def template_payload(slug="release-agent"):
    return {"name": "Release Agent", "slug": slug, "description": "Fixture",
            "icon": "sparkles", "category": "Fixture"}


def test_p1_p2_absent_evidence_and_unique_slug_lease(pg_catalog):
    control = AgentReleaseProvenance(pg_catalog.control)
    first = begin(control, pg_catalog.actor)
    with pg_catalog.store.connection() as conn:
        row = conn.execute("SELECT pre_state,pre_state_evidence,status FROM agent_release_operations WHERE operation_id=?", (first,)).fetchone()
        assert row["pre_state"] == "ABSENT" and row["status"] == "provisioning"
        assert row["pre_state_evidence"] == {"template_count": 0, "instance_count": 0, "published_revision_count": 0}
    with pytest.raises(Exception):
        begin(control, pg_catalog.actor)
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM agent_release_operations").fetchone()["n"] == 1


def test_p2_concurrent_same_slug_only_one_owner(pg_catalog):
    control = AgentReleaseProvenance(pg_catalog.control)

    def attempt(_):
        try:
            begin(control, pg_catalog.actor, "concurrent-release-agent")
            return "owned"
        except Exception:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(attempt, range(2))) == ["blocked", "owned"]
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM agent_release_operations WHERE agent_slug='concurrent-release-agent'").fetchone()["n"] == 1


def test_p3_p4_product_and_mapping_are_atomic(pg_catalog, monkeypatch):
    control = AgentReleaseProvenance(pg_catalog.control)
    operation = begin(control, pg_catalog.actor)
    original = control._artifact

    def reject(*args):
        raise RuntimeError("mapping write failed")

    monkeypatch.setattr(control, "_artifact", reject)
    with pytest.raises(RuntimeError):
        control.create_template(operation, template_payload(), pg_catalog.actor)
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT 1 FROM agent_templates WHERE slug='release-agent'").fetchone() is None
    monkeypatch.setattr(control, "_artifact", original)
    created = control.create_template(operation, template_payload(), pg_catalog.actor)
    template_id = created["id"]
    monkeypatch.setattr(control, "_artifact", reject)
    with pytest.raises(RuntimeError):
        control.create_revision(operation, template_id, {"persona": "Fixture persona"}, pg_catalog.actor)
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT 1 FROM agent_template_versions WHERE agent_template_id=?", (template_id,)).fetchone() is None
    monkeypatch.setattr(control, "_artifact", original)
    control.create_revision(operation, template_id, {"persona": "Fixture persona"}, pg_catalog.actor)
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM agent_release_artifacts WHERE operation_id=?", (operation,)).fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(*) AS n FROM agent_template_versions WHERE agent_template_id=?", (template_id,)).fetchone()["n"] == 1


def test_p5_p6_p7_immutable_evidence_survives_product_removal(pg_catalog):
    control = AgentReleaseProvenance(pg_catalog.control)
    operation = begin(control, pg_catalog.actor)
    template_id = control.create_template(operation, template_payload(), pg_catalog.actor)["id"]
    with pg_catalog.store.connection() as conn:
        with pytest.raises(Exception):
            conn.execute("UPDATE agent_release_artifacts SET identity='{}'::jsonb WHERE operation_id=?", (operation,))
    with pg_catalog.store.connection() as conn:
        with pytest.raises(Exception):
            conn.execute("DELETE FROM agent_release_events WHERE operation_id=?", (operation,))
    with pg_catalog.store.connection() as conn:
        conn.execute("DELETE FROM agent_templates WHERE id=?", (template_id,))
        assert conn.execute("SELECT COUNT(*) AS n FROM agent_release_artifacts WHERE operation_id=?", (operation,)).fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM agent_release_events WHERE operation_id=?", (operation,)).fetchone()["n"] >= 3


def test_p8_commit_point_cannot_transition_to_abort(pg_catalog):
    control = AgentReleaseProvenance(pg_catalog.control)
    operation = begin(control, pg_catalog.actor)
    with pg_catalog.store.connection() as conn:
        conn.execute("UPDATE agent_release_operations SET status='staged' WHERE operation_id=?", (operation,))
        conn.execute("UPDATE agent_release_operations SET status='published' WHERE operation_id=?", (operation,))
        conn.execute("UPDATE agent_release_operations SET status='committed',commit_point_at=CURRENT_TIMESTAMP,completed_at=CURRENT_TIMESTAMP WHERE operation_id=?", (operation,))
    with pg_catalog.store.connection() as conn:
        with pytest.raises(Exception):
            conn.execute("UPDATE agent_release_operations SET status='aborting' WHERE operation_id=?", (operation,))
    with pg_catalog.store.connection() as conn:
        assert conn.execute("SELECT status FROM agent_release_operations WHERE operation_id=?", (operation,)).fetchone()["status"] == "committed"
        assert conn.execute("SELECT COUNT(*) AS n FROM agent_release_events WHERE operation_id=? AND event_type='commit_point'", (operation,)).fetchone()["n"] == 1


def test_preexisting_agent_cannot_be_adopted(pg_catalog):
    control = AgentReleaseProvenance(pg_catalog.control)
    pg_catalog.control.create_template(template_payload("ordinary-agent"), pg_catalog.actor)
    with pytest.raises(AgentCatalogError, match="not ABSENT"):
        begin(control, pg_catalog.actor, "ordinary-agent")
