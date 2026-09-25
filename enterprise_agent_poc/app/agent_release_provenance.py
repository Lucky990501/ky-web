"""Internal, PostgreSQL-only release ownership primitives.

This is deliberately not an abort API. A release may not use these primitives
for deployment until runtime-validation cleanup and published-revision
compensation have their own verified, fail-closed contracts.
"""
from __future__ import annotations

import re
import uuid

from app.agent_productization import AgentCatalogError, AgentProductization, canonical


class AgentReleaseProvenance:
    def __init__(self, catalog: AgentProductization):
        self.catalog = catalog
        self.store = catalog.store

    def _postgres(self, conn):
        if not self.store.is_postgres or not conn.execute(
            "SELECT to_regclass('public.agent_release_operations') AS name"
        ).fetchone()["name"]:
            raise AgentCatalogError("Agent Release Provenance migration 014 required", 503)

    def _operation(self, conn, operation_id, *, lock=True):
        self._postgres(conn)
        row = conn.execute(
            "SELECT * FROM agent_release_operations WHERE operation_id=?" + (" FOR UPDATE" if lock else ""),
            (operation_id,),
        ).fetchone()
        if not row:
            raise AgentCatalogError("Release operation not found", 404)
        return dict(row)

    def _active(self, conn, operation_id):
        operation = self._operation(conn, operation_id)
        if operation["status"] != "provisioning" or operation["pre_state"] != "ABSENT":
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        return operation

    @staticmethod
    def _event(conn, operation_id, event_type, evidence="{}"):
        conn.execute(
            "INSERT INTO agent_release_events(operation_id,event_type,evidence) VALUES (?,?,?::jsonb)",
            (operation_id, event_type, evidence),
        )

    @staticmethod
    def _artifact(conn, operation_id, kind, artifact_id, identity):
        from app.agent_productization import canonical

        conn.execute(
            "INSERT INTO agent_release_artifacts(operation_id,artifact_type,artifact_id,identity) VALUES (?,?,?,?::jsonb)",
            (operation_id, kind, artifact_id, canonical(identity)),
        )

    def begin(self, *, operation_id, release_identity, source_identity, manifest_identity, agent_slug, actor):
        """Acquire an ABSENT slug lease and record its observed product pre-state.

        The partial unique index in 014 is the concurrency authority. The
        product slug's own UNIQUE constraint remains a second independent gate.
        """
        try:
            if str(uuid.UUID(operation_id)) != operation_id:
                raise ValueError
        except (TypeError, ValueError, AttributeError):
            raise AgentCatalogError("Invalid release operation ID") from None
        if not isinstance(release_identity, str) or not release_identity.strip() or len(release_identity) > 200:
            raise AgentCatalogError("Invalid release identity")
        if not isinstance(source_identity, str) or not re.fullmatch(r"[0-9a-f]{40}", source_identity):
            raise AgentCatalogError("Invalid source identity")
        if not isinstance(manifest_identity, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest_identity):
            raise AgentCatalogError("Invalid canonical manifest identity")
        if not isinstance(agent_slug, str) or len(agent_slug) > 100 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", agent_slug):
            raise AgentCatalogError("Invalid Agent slug")
        with self.store.connection() as conn:
            self._postgres(conn)
            from scripts.compatibility_epoch import EpochBlocked, require_productized_epoch

            try:
                require_productized_epoch(conn, postgres=True)
            except EpochBlocked:
                raise AgentCatalogError("compatibility_epoch_not_advanced", 409) from None
            # No revision or instance can exist without its Template by FK.
            # Query each independently so the evidence is explicit, not assumed.
            evidence = conn.execute(
                "SELECT (SELECT COUNT(*) FROM agent_templates WHERE slug=?) AS template_count, "
                "(SELECT COUNT(*) FROM tenant_agent_instances i JOIN agent_templates t ON t.id=i.agent_id WHERE t.slug=?) AS instance_count, "
                "(SELECT COUNT(*) FROM agent_template_versions v JOIN agent_templates t ON t.id=v.agent_template_id "
                "WHERE t.slug=? AND v.status='published') AS published_revision_count",
                (agent_slug, agent_slug, agent_slug),
            ).fetchone()
            if any(evidence.values()):
                raise AgentCatalogError("Agent pre-state is not ABSENT", 409)
            from app.agent_productization import canonical

            conn.execute(
                "INSERT INTO agent_release_operations(operation_id,operation_type,release_identity,source_identity,"
                "manifest_identity,agent_slug,pre_state,pre_state_evidence,created_by) "
                "VALUES (?,'agent_provision',?,?,?,?,'ABSENT',?::jsonb,?)",
                (operation_id, release_identity, source_identity, manifest_identity, agent_slug,
                 canonical(dict(evidence)), actor),
            )
            self._event(conn, operation_id, "operation_created")
            self._event(conn, operation_id, "pre_state_verified", canonical(dict(evidence)))
        return operation_id

    def create_template(self, operation_id, payload, actor):
        """Atomically create a product Template and its exact ownership row."""
        from app.agent_catalog import CATALOG
        if not isinstance(payload, dict) or set(payload) - {"name", "slug", "description", "icon", "category"}:
            raise AgentCatalogError("Invalid Template fields")
        slug = payload.get("slug", "")
        if not isinstance(slug, str) or slug in {*CATALOG, *(a.slug for a in CATALOG.values()), "image", "campaign"}:
            raise AgentCatalogError("Reserved legacy Agent reference", 409)
        try:
            uuid.UUID(slug)
        except ValueError:
            pass
        else:
            raise AgentCatalogError("Public slug cannot be an internal UUID", 409)
        fields = self.catalog._fields({k: v for k, v in payload.items() if k != "slug"})
        template_id = str(uuid.uuid4())
        with self.store.connection() as conn:
            operation = self._active(conn, operation_id)
            if operation["agent_slug"] != slug or operation["created_by"] != actor:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            if conn.execute("SELECT 1 FROM agent_release_artifacts WHERE operation_id=? AND artifact_type='template'", (operation_id,)).fetchone():
                raise AgentCatalogError("Template already owned by release operation", 409)
            if conn.execute("SELECT 1 FROM agent_templates WHERE slug=?", (slug,)).fetchone():
                raise AgentCatalogError("Agent pre-state changed", 409)
            conn.execute(
                "INSERT INTO agent_templates(id,name,slug,description,icon,status,default_runtime_profile,credit_cost,"
                "skill_manifest,allows_image_generation,category,definition_source,lifecycle_status,created_at,updated_at,created_by,updated_by) "
                "VALUES (?,?,?,?,?,'disabled','default',?,'{}',FALSE,?,'productized','draft',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?)",
                (template_id, fields["name"], slug, fields["description"], fields["icon"], fields["credit_cost"], fields["category"], actor, actor),
            )
            self._artifact(conn, operation_id, "template", template_id, {"template_id": template_id, "slug": slug})
            self._event(conn, operation_id, "template_created", canonical({"template_id": template_id, "slug": slug}))
        return self.catalog.detail(template_id)

    def create_revision(self, operation_id, template_id, payload, actor):
        """Atomically create a Draft revision and record its initial fingerprint."""
        from app.agent_productization import DEFAULTS, canonical

        if not isinstance(payload, dict) or "from_version_id" in payload:
            raise AgentCatalogError("Release Revision requires explicit fields; clone is not supported")
        with self.store.connection() as conn:
            operation = self._active(conn, operation_id)
            mapping = conn.execute(
                "SELECT artifact_id FROM agent_release_artifacts WHERE operation_id=? AND artifact_type='template'",
                (operation_id,),
            ).fetchone()
            if not mapping or mapping["artifact_id"] != template_id or operation["created_by"] != actor:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            template = self.catalog._template(conn, template_id, lock=True)
            if template["slug"] != operation["agent_slug"]:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            if conn.execute("SELECT 1 FROM agent_template_versions WHERE agent_template_id=?", (template_id,)).fetchone():
                raise AgentCatalogError("Extra Revision; manual recovery required", 409)
            base = {**DEFAULTS, **{f: template[f] for f in ("name", "description", "icon", "category")}}
            fields = self.catalog._fields(payload, base)
            version_id = str(uuid.uuid4())
            columns = list(DEFAULTS)
            values = [self.catalog._db_field(k, fields[k]) for k in columns]
            conn.execute(
                f"INSERT INTO agent_template_versions(id,agent_template_id,revision,{','.join(columns)},"
                f"configuration_fingerprint,created_by,updated_by) VALUES ({','.join('?' for _ in range(len(columns)+6))})",
                (version_id, template_id, 1, *values, "pending", actor, actor),
            )
            self.catalog._refresh_fingerprint(conn, version_id, actor)
            version = dict(conn.execute("SELECT * FROM agent_template_versions WHERE id=?", (version_id,)).fetchone())
            identity = {"template_id": template_id, "revision_id": version_id, "revision": 1,
                        "initial_fingerprint": version["configuration_fingerprint"],
                        "model_config_id": version["model_config_id"]}
            self._artifact(conn, operation_id, "revision", version_id, identity)
            self._event(conn, operation_id, "revision_created", canonical(identity))
        return self.catalog.detail(template_id)
