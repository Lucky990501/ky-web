"""Internal, PostgreSQL-only Agent release ownership and compensation.

No public endpoint exposes these methods. Release integration must bind the
operation to its candidate and commit point; this module never edits release
scripts or performs deployment.
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

    def provisional_owner(self, conn, template_id):
        """Return an uncommitted release owner, never adopt an ordinary Agent."""
        if not self.store.is_postgres or not conn.execute(
            "SELECT to_regclass('public.agent_release_operations') AS name"
        ).fetchone()["name"]:
            return None
        rows = conn.execute(
            "SELECT o.operation_id,o.status FROM agent_release_operations o "
            "JOIN agent_release_artifacts a ON a.operation_id=o.operation_id "
            "WHERE a.artifact_type='template' AND a.artifact_id=? "
            "AND o.status IN ('provisioning','staged','published','enabled','aborting')",
            (template_id,),
        ).fetchall()
        if len(rows) > 1:
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        return dict(rows[0]) if rows else None

    def require_provisional_owner(self, conn, template_id, operation_id, allowed_statuses):
        owner = self.provisional_owner(conn, template_id)
        if owner and (not operation_id or owner["operation_id"] != operation_id or owner["status"] not in allowed_statuses):
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        if operation_id and not owner:
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        return owner

    def require_unsealed_draft(self, conn, template_id):
        owner = self.provisional_owner(conn, template_id)
        if owner and (owner["status"] != "provisioning" or conn.execute(
            "SELECT 1 FROM agent_release_artifacts WHERE operation_id=? AND artifact_type='identity_seal'",
            (owner["operation_id"],),
        ).fetchone()):
            raise AgentCatalogError("Release Draft identity is sealed", 409)

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

    def _template_mapping(self, conn, operation_id, template_id):
        rows = conn.execute(
            "SELECT artifact_id,identity FROM agent_release_artifacts "
            "WHERE operation_id=? AND artifact_type='template'", (operation_id,)
        ).fetchall()
        if len(rows) != 1 or rows[0]["artifact_id"] != template_id or rows[0]["identity"] != {
            "template_id": template_id, "slug": self._operation(conn, operation_id)["agent_slug"]
        }:
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)

    def _revision_identity(self, conn, operation_id, template_id, revision_id):
        mappings = conn.execute(
            "SELECT artifact_id,identity FROM agent_release_artifacts "
            "WHERE operation_id=? AND artifact_type='revision'", (operation_id,)
        ).fetchall()
        actual = conn.execute(
            "SELECT * FROM agent_template_versions WHERE agent_template_id=? ORDER BY revision", (template_id,)
        ).fetchall()
        if revision_id is None:
            if mappings or actual:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            return None
        if len(mappings) != 1 or len(actual) != 1 or mappings[0]["artifact_id"] != revision_id or actual[0]["id"] != revision_id:
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        version = dict(actual[0])
        identity = mappings[0]["identity"]
        if (identity.get("template_id"), identity.get("revision_id"), identity.get("revision"), identity.get("model_config_id")) != (
            template_id, revision_id, version["revision"], version["model_config_id"]
        ) or self.catalog._fingerprint(conn, version) != version["configuration_fingerprint"]:
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        return version

    def _binding_identity(self, conn, revision_id):
        skills = [dict(row) for row in conn.execute(
            "SELECT b.skill_id,b.skill_version_id,s.slug,v.version,v.checksum,p.id AS package_id,p.sha256 AS package_sha256 "
            "FROM agent_template_version_skills b JOIN skills s ON s.id=b.skill_id "
            "JOIN skill_versions v ON v.id=b.skill_version_id AND v.skill_id=b.skill_id "
            "LEFT JOIN skill_packages p ON p.skill_version_id=v.id "
            "WHERE b.agent_template_version_id=? ORDER BY b.skill_id", (revision_id,)
        ).fetchall()]
        if any(not row["package_id"] or not row["package_sha256"] for row in skills):
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        tools = [dict(row) for row in conn.execute(
            "SELECT tool_capability_id,invocation_requirement FROM agent_template_version_tools "
            "WHERE agent_template_version_id=? ORDER BY tool_capability_id", (revision_id,)
        ).fetchall()]
        return {"skills": skills, "tools": tools}

    def seal_identity(self, operation_id, template_id, revision_id, fingerprint):
        """Append a final immutable snapshot after Draft binding, before Publish."""
        with self.store.connection() as conn:
            operation = self._active(conn, operation_id)
            self._template_mapping(conn, operation_id, template_id)
            template = self.catalog._template(conn, template_id, lock=True)
            version = self._revision_identity(conn, operation_id, template_id, revision_id)
            if not version or version["status"] != "draft" or template["slug"] != operation["agent_slug"]:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            if version["configuration_fingerprint"] != fingerprint:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            if conn.execute("SELECT 1 FROM agent_release_artifacts WHERE operation_id=? AND artifact_type='identity_seal'", (operation_id,)).fetchone():
                raise AgentCatalogError("Release identity already sealed", 409)
            identity = {"template_id": template_id, "revision_id": revision_id,
                        "revision": version["revision"], "fingerprint": fingerprint,
                        "model_config_id": version["model_config_id"],
                        **self._binding_identity(conn, revision_id)}
            self._artifact(conn, operation_id, "identity_seal", revision_id, identity)
            self._event(conn, operation_id, "identity_sealed", canonical(identity))
            conn.execute("UPDATE agent_release_operations SET status='staged' WHERE operation_id=?", (operation_id,))
        return identity

    def _verify_seal(self, conn, operation_id, template_id, revision):
        seals = conn.execute(
            "SELECT identity FROM agent_release_artifacts WHERE operation_id=? AND artifact_type='identity_seal'",
            (operation_id,),
        ).fetchall()
        if not revision:
            if seals:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            return None
        operation_status = self._operation(conn, operation_id)["status"]
        if not seals and (revision["status"] == "draft" or
                          (revision["status"] == "deprecated" and operation_status == "aborted")):
            mapped = conn.execute(
                "SELECT identity FROM agent_release_artifacts WHERE operation_id=? AND artifact_type='revision'",
                (operation_id,),
            ).fetchone()
            if mapped and mapped["identity"].get("initial_fingerprint") == revision["configuration_fingerprint"] and self._binding_identity(conn, revision["id"]) == {"skills": [], "tools": []}:
                return {"initial_fingerprint": revision["configuration_fingerprint"]}
        if len(seals) != 1:
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        expected = seals[0]["identity"]
        actual = {"template_id": template_id, "revision_id": revision["id"],
                  "revision": revision["revision"], "fingerprint": revision["configuration_fingerprint"],
                  "model_config_id": revision["model_config_id"],
                  **self._binding_identity(conn, revision["id"])}
        if actual != expected:
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        return actual

    def _validation_tasks(self, conn, operation_id, template_id, revision):
        """Classify only atomically mapped, terminal Runtime Tests as validation."""
        rows = conn.execute(
            "SELECT artifact_id,identity FROM agent_release_artifacts "
            "WHERE operation_id=? AND artifact_type='runtime_validation'", (operation_id,)
        ).fetchall()
        result = {}
        for mapping in rows:
            identity = mapping["identity"]
            task_id = mapping["artifact_id"]
            if not revision or identity.get("task_id") != task_id or identity.get("template_id") != template_id or identity.get("revision_id") != revision["id"] or identity.get("fingerprint") != revision["configuration_fingerprint"]:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            row = conn.execute(
                "SELECT t.*,x.id AS test_id,x.status AS test_status,x.configuration_fingerprint AS test_fingerprint,"
                "c.id AS context_id,c.instance_id,c.tool_policy_snapshot,c.agent_template_version_id AS context_revision,"
                "c.configuration_fingerprint AS context_fingerprint "
                "FROM tasks t JOIN agent_template_tests x ON x.task_id=t.id AND x.test_type='runtime' "
                "JOIN task_agent_contexts m ON m.task_id=t.id "
                "JOIN agent_execution_contexts c ON c.id=m.context_id WHERE t.id=?",
                (task_id,),
            ).fetchone()
            if not row or (row["id"], row["test_id"], row["context_id"], row["instance_id"], row["tenant_id"], row["agent_id"], row["context_revision"], row["context_fingerprint"], row["test_fingerprint"]) != (
                task_id, identity.get("test_id"), identity.get("context_id"), identity.get("instance_id"), identity.get("tenant_id"), template_id, revision["id"], revision["configuration_fingerprint"], revision["configuration_fingerprint"]
            ) or row["status"] not in {"completed", "failed", "cancelled"} or row["test_status"] not in {"passed", "failed", "invalidated"}:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            if conn.execute("SELECT COUNT(*) AS n FROM agent_template_tests WHERE task_id=? AND test_type='runtime'", (task_id,)).fetchone()["n"] != 1:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            import json
            try:
                policy = json.loads(row["tool_policy_snapshot"])
            except (TypeError, ValueError):
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409) from None
            if not isinstance(policy, dict) or policy.get("runtime_test") is not True:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            if conn.execute("SELECT 1 FROM generations WHERE task_id=?", (task_id,)).fetchone():
                # Generated files/storage keys have no formal test cleanup path.
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            result_row = conn.execute("SELECT result_json FROM task_results WHERE task_id=?", (task_id,)).fetchone()
            if result_row:
                try:
                    result_payload = json.loads(result_row["result_json"] or "{}")
                except (TypeError, ValueError):
                    raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409) from None
                if not isinstance(result_payload, dict) or result_payload.get("structured_result"):
                    # Structured outputs can own document artifacts outside DB.
                    raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            result[task_id] = dict(row)
        return result

    def _zero_real_use(self, conn, operation_id, template_id, revision):
        allowed_kinds = {"template", "revision", "identity_seal", "tenant_instance", "runtime_validation"}
        if any(row["artifact_type"] not in allowed_kinds for row in conn.execute(
            "SELECT artifact_type FROM agent_release_artifacts WHERE operation_id=?", (operation_id,)
        )):
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        tests = self._validation_tasks(conn, operation_id, template_id, revision)
        task_rows = conn.execute("SELECT id,conversation_id,run_id FROM tasks WHERE agent_id=?", (template_id,)).fetchall()
        if {row["id"] for row in task_rows} != set(tests):
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        if {row["task_id"] for row in conn.execute(
            "SELECT task_id FROM agent_template_tests WHERE agent_template_version_id=? AND test_type='runtime'",
            (revision["id"] if revision else "",),
        )} != set(tests):
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        test_conversations = {row["conversation_id"] for row in task_rows if row["conversation_id"]}
        test_runs = {row["run_id"] for row in task_rows if row["run_id"]}
        test_contexts = {row["context_id"] for row in tests.values()}
        if {row["id"] for row in conn.execute("SELECT id FROM conversations WHERE agent_id=?", (template_id,))} != test_conversations:
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        if {row["run_id"] for row in conn.execute("SELECT run_id FROM run_traces WHERE agent_id=?", (template_id,))} != test_runs:
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        for task in task_rows:
            if task["run_id"]:
                trace = conn.execute("SELECT conversation_id,payload FROM run_traces WHERE run_id=?", (task["run_id"],)).fetchone()
                if not trace or trace["conversation_id"] != task["conversation_id"]:
                    raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
                import json
                try:
                    payload = json.loads(trace["payload"] or "{}")
                except (TypeError, ValueError):
                    raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409) from None
                if not isinstance(payload, dict) or payload.get("artifact_completed") is True or payload.get("structured_result"):
                    raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        if {row["id"] for row in conn.execute("SELECT id FROM agent_execution_contexts WHERE agent_id=?", (template_id,))} != test_contexts:
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        for conversation_id in test_conversations:
            if conn.execute("SELECT 1 FROM tasks WHERE conversation_id=? AND id NOT IN (SELECT artifact_id FROM agent_release_artifacts WHERE operation_id=? AND artifact_type='runtime_validation')", (conversation_id, operation_id)).fetchone():
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            context = conn.execute("SELECT context_id FROM conversation_agent_contexts WHERE conversation_id=?", (conversation_id,)).fetchone()
            if not context or context["context_id"] not in test_contexts:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            owner = conn.execute("SELECT user_id,deleted_at FROM conversation_owners WHERE conversation_id=?", (conversation_id,)).fetchone()
            expected_users = {task["user_id"] for task in tests.values() if task["conversation_id"] == conversation_id}
            if not owner or owner["user_id"] not in expected_users or len(expected_users) != 1:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            allowed_messages = {f"task:{task_id}:{role}" for task_id, task in tests.items()
                                if task["conversation_id"] == conversation_id for role in ("user", "assistant")}
            if any(message["id"] not in allowed_messages or
                   message["role"] != message["id"].rsplit(":", 1)[-1]
                   for message in conn.execute(
                       "SELECT id,role FROM messages WHERE conversation_id=?", (conversation_id,)
                   )):
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        if conn.execute("SELECT 1 FROM agent_skill_bindings WHERE agent_id=?", (template_id,)).fetchone():
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        instances = [dict(row) for row in conn.execute("SELECT * FROM tenant_agent_instances WHERE agent_id=? FOR UPDATE", (template_id,))]
        mappings = {row["artifact_id"]: row["identity"] for row in conn.execute(
            "SELECT artifact_id,identity FROM agent_release_artifacts WHERE operation_id=? AND artifact_type='tenant_instance'",
            (operation_id,),
        )}
        if {row["instance_id"] for row in instances} != set(mappings):
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        for instance in instances:
            if not revision or mappings[instance["instance_id"]] != {
                "tenant_id": instance["tenant_id"], "template_id": template_id,
                "revision_id": revision["id"], "instance_id": instance["instance_id"]
            } or instance["agent_template_version_id"] != revision["id"]:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        return {"validation_tasks": tests, "instances": instances}

    def abort_resolvable_absent(self, operation_id, template_id, revision_id=None, fingerprint=None):
        """Compensate only an exact pre-commit release with zero real use.

        Published configuration and historical validation Tasks remain intact.
        Runtime Test records are logically retired (invalidated), never
        physically deleted. Every compensation and audit write is one PG tx.
        """
        with self.store.connection() as conn:
            operation = self._operation(conn, operation_id)
            if operation["status"] == "committed" or operation["commit_point_at"] is not None:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            if operation["pre_state"] != "ABSENT" or operation["operation_type"] != "agent_provision":
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            self._template_mapping(conn, operation_id, template_id)
            template = self.catalog._template(conn, template_id, lock=True)
            if template["slug"] != operation["agent_slug"] or template["definition_source"] != "productized":
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            revision = self._revision_identity(conn, operation_id, template_id, revision_id)
            if revision and (not fingerprint or revision["configuration_fingerprint"] != fingerprint):
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            self._verify_seal(conn, operation_id, template_id, revision)
            if operation["status"] == "aborted":
                self._assert_resolvably_absent(conn, template_id, operation["agent_slug"])
                return {"status": "already_aborted", "resolution": "RESOLVABLE_ABSENT"}
            if operation["status"] not in {"provisioning", "staged", "published", "enabled"}:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            if template["current_published_version_id"] not in {None, revision_id}:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            if revision and revision["status"] not in {"draft", "published"}:
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            published_state = bool(revision and revision["status"] == "published")
            expected_lifecycle = "published" if published_state else "draft"
            expected_pointer = revision_id if published_state else None
            if (template["lifecycle_status"] != expected_lifecycle or
                    template["current_published_version_id"] != expected_pointer):
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            inventory = self._zero_real_use(conn, operation_id, template_id, revision)
            self._event(conn, operation_id, "abort_started", canonical({"template_id": template_id, "revision_id": revision_id, "validation_task_count": len(inventory["validation_tasks"])}))
            conn.execute("UPDATE agent_release_operations SET status='aborting' WHERE operation_id=?", (operation_id,))
            for task_id, test in inventory["validation_tasks"].items():
                conn.execute("UPDATE agent_template_tests SET status='invalidated' WHERE id=? AND test_type='runtime'", (test["test_id"],))
                self._event(conn, operation_id, "runtime_validation_retired", canonical({"task_id": task_id, "test_id": test["test_id"]}))
            # Re-run the real-use gate after validation retirement.
            self._zero_real_use(conn, operation_id, template_id, revision)
            for instance in inventory["instances"]:
                if instance["status"] != "disabled":
                    conn.execute("UPDATE tenant_agent_instances SET status='disabled',updated_at=CURRENT_TIMESTAMP WHERE instance_id=?", (instance["instance_id"],))
                    self._event(conn, operation_id, "instance_disabled", canonical({"instance_id": instance["instance_id"]}))
            if revision:
                conn.execute("UPDATE agent_template_versions SET status='deprecated',deprecated_at=CURRENT_TIMESTAMP WHERE id=?", (revision_id,))
                self._event(conn, operation_id, "revision_deprecated", canonical({"revision_id": revision_id}))
            conn.execute("UPDATE agent_templates SET current_published_version_id=NULL,lifecycle_status='deprecated',updated_at=CURRENT_TIMESTAMP WHERE id=?", (template_id,))
            self._event(conn, operation_id, "template_deprecated", canonical({"template_id": template_id}))
            self._assert_resolvably_absent(conn, template_id, operation["agent_slug"])
            conn.execute("UPDATE agent_release_operations SET status='aborted',aborted_at=CURRENT_TIMESTAMP,completed_at=CURRENT_TIMESTAMP WHERE operation_id=?", (operation_id,))
        return {"status": "aborted", "resolution": "RESOLVABLE_ABSENT"}

    def commit_provision(self, operation_id, template_id, revision_id, fingerprint):
        """Record the irreversible Release Commit Point, never a deployment."""
        with self.store.connection() as conn:
            operation = self._operation(conn, operation_id)
            if operation["status"] == "committed":
                return {"status": "already_committed"}
            if operation["status"] not in {"published", "enabled"} or operation["pre_state"] != "ABSENT":
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            self._template_mapping(conn, operation_id, template_id)
            template = self.catalog._template(conn, template_id, lock=True)
            revision = self._revision_identity(conn, operation_id, template_id, revision_id)
            if (not revision or revision["status"] != "published" or
                    revision["configuration_fingerprint"] != fingerprint or
                    template["lifecycle_status"] != "published" or
                    template["current_published_version_id"] != revision_id):
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
            self._verify_seal(conn, operation_id, template_id, revision)
            conn.execute(
                "UPDATE agent_release_operations SET status='committed',"
                "commit_point_at=CURRENT_TIMESTAMP,completed_at=CURRENT_TIMESTAMP WHERE operation_id=?",
                (operation_id,),
            )
        return {"status": "committed"}

    def _assert_resolvably_absent(self, conn, template_id, slug):
        from app.agent_availability import current_published_revision, tenant_available
        from app.agent_reference import resolve_agent_reference
        if current_published_revision(conn, template_id, postgres=True):
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        if conn.execute("SELECT 1 FROM tenant_agent_instances WHERE agent_id=? AND status='enabled'", (template_id,)).fetchone():
            raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        for tenant in conn.execute("SELECT tenant_id FROM tenant_agent_instances WHERE agent_id=?", (template_id,)):
            if tenant_available(conn, tenant["tenant_id"], template_id, postgres=True):
                raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
        try:
            resolve_agent_reference(conn, slug, postgres=True)
        except LookupError:
            return
        raise AgentCatalogError("AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED", 409)
