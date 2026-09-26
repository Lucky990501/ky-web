"""Phase B WeChat Agent transition through the existing productization service.

Only deploy/release_switch.sh may invoke mutating commands, while holding its
global lock and rollback snapshot. No command accepts SQL, tenant or slug
overrides; the reviewed Candidate manifest supplies the exact scope.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import uuid

from app.agent_release_provenance import AgentReleaseProvenance
from app.agent_productization import AgentCatalogError
from app.bundled_skills import validate_bundle
from scripts.create_wechat_official_account_agent_v1 import (
    SLUG, NAME, DESCRIPTION, PERSONA, SKILL_VERSION,
)
from scripts.release_manifest import validate_manifest_contract
from scripts.rollback_preflight import digest
from scripts.runtime_policy_rollout import Rollout
from scripts.release_scoped_runtime_test import require_inherited_release_lock


class ProductizationBlocked(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise ProductizationBlocked(code)


def operation_id_for(manifest):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "enterprise-agent-phase-b:" + manifest["release_id"]))


class Transition:
    def __init__(self, manifest, *, control, registry, store, bundle_root: Path, rollout=None):
        validate_manifest_contract(manifest)
        require("agent_productization_transition" in manifest, "PHASE_B_DECLARATION_REQUIRED")
        self.manifest = manifest
        self.declaration = manifest["agent_productization_transition"]
        self.scope = self.declaration["runtime_test"]
        self.control, self.registry, self.store = control, registry, store
        self.release = AgentReleaseProvenance(control)
        self.bundle_root = Path(bundle_root)
        self.rollout = rollout
        self.operation_id = operation_id_for(manifest)

    def _skill(self, conn):
        entry = [item for item in validate_bundle(self.bundle_root)
                 if item["skill_slug"] == SLUG and item["version"] == SKILL_VERSION]
        require(len(entry) == 1, "PHASE_B_BUNDLE_SKILL_MISSING")
        expected = self.declaration
        require(hashlib.sha256((self.bundle_root / "manifest.json").read_bytes()).hexdigest()
                == expected["bundled_manifest_sha256"]
                and entry[0]["source_identity"]["files"] == [{
                    "path": "SKILL.md", "sha256": expected["skill_source_sha256"],
                    "git_mode": "100644",
                }]
                and entry[0]["artifact_sha256"] == self.scope["skill_package_sha256"],
                "PHASE_B_BUNDLE_IDENTITY_MISMATCH")
        row = conn.execute(
            "SELECT s.id AS skill_id,v.id AS skill_version_id,v.status,v.checksum,p.sha256 "
            "FROM skills s JOIN skill_versions v ON v.skill_id=s.id "
            "JOIN skill_packages p ON p.skill_version_id=v.id "
            "WHERE s.slug=? AND v.version=?",
            (SLUG, SKILL_VERSION),
        ).fetchall()
        require(len(row) == 1 and row[0]["status"] == "published"
                and row[0]["checksum"] == row[0]["sha256"] == self.scope["skill_package_sha256"],
                "PHASE_B_REGISTRY_SKILL_IDENTITY_MISMATCH")
        return dict(row[0])

    def preflight(self):
        actor, tenant = self.scope["actor"], self.scope["tenant"]
        require(self.store.is_postgres and self.registry.is_platform_admin(actor),
                "PHASE_B_ADMIN_AUTHORITY_MISSING")
        with self.store.connection() as conn:
            require(conn.execute("SELECT 1 FROM users WHERE id=? AND tenant_id=? AND role='enterprise_admin'",
                                 (actor, tenant)).fetchone() is not None,
                    "PHASE_B_ADMIN_TENANT_MISMATCH")
            require(conn.execute("SELECT 1 FROM tenants WHERE id=?", (tenant,)).fetchone() is not None,
                    "PHASE_B_TENANT_MISSING")
            require(conn.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1")
                    .fetchone()["version"] == "014", "PHASE_B_SCHEMA_MISMATCH")
            require(conn.execute("SELECT 1 FROM agent_templates WHERE slug=?", (SLUG,)).fetchone() is None,
                    "PHASE_B_AGENT_NOT_ABSENT")
            require(conn.execute("SELECT 1 FROM agent_release_operations WHERE operation_id=?",
                                 (self.operation_id,)).fetchone() is None,
                    "PHASE_B_OPERATION_ALREADY_EXISTS")
            skill = self._skill(conn)
        if self.rollout is not None:
            status = self.rollout.status()
            require(status["policy_enabled"] is False and status["policy_tenant_count"] == 0
                    and status["policy_slug_count"] == 0
                    and status["runtime_test_tenant_configured"] is False,
                    "PHASE_B_RUNTIME_POLICY_NOT_CLOSED")
        return {"status": "preflight_passed", "operation_id": self.operation_id,
                "tenant": tenant, "actor": actor, "agent_slug": SLUG,
                "skill_package_sha256": skill["sha256"]}

    def _owned(self):
        with self.store.connection() as conn:
            operation = self.release._operation(conn, self.operation_id)
            require((operation["release_identity"], operation["source_identity"],
                     operation["manifest_identity"], operation["agent_slug"],
                     operation["created_by"], operation["pre_state"]) == (
                         self.manifest["release_id"], self.manifest["source_commit"],
                         digest(self.manifest), SLUG, self.scope["actor"], "ABSENT"),
                    "PHASE_B_OPERATION_IDENTITY_MISMATCH")
            templates = conn.execute(
                "SELECT artifact_id FROM agent_release_artifacts WHERE operation_id=? "
                "AND artifact_type='template'", (self.operation_id,),
            ).fetchall()
            revisions = conn.execute(
                "SELECT artifact_id FROM agent_release_artifacts WHERE operation_id=? "
                "AND artifact_type='revision'", (self.operation_id,),
            ).fetchall()
            require(len(templates) == len(revisions) == 1,
                    "AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED")
            template_id, revision_id = templates[0]["artifact_id"], revisions[0]["artifact_id"]
            self.release._template_mapping(conn, self.operation_id, template_id)
            revision = self.release._revision_identity(conn, self.operation_id, template_id, revision_id)
            self.release._verify_seal(conn, self.operation_id, template_id, revision)
            require(revision["model_config_id"] == self.scope["model_config_id"]
                    and revision["credit_cost"] == self.scope["credit_cost"]
                    and revision["grounding_policy"] is None,
                    "PHASE_B_REVISION_CONTRACT_MISMATCH")
        return operation, template_id, revision

    def stage(self):
        self.preflight()
        actor = self.scope["actor"]
        self.release.begin(
            operation_id=self.operation_id, release_identity=self.manifest["release_id"],
            source_identity=self.manifest["source_commit"],
            manifest_identity=digest(self.manifest), agent_slug=SLUG, actor=actor,
        )
        template = self.release.create_template(self.operation_id, {
            "name": NAME, "slug": SLUG, "description": DESCRIPTION,
            "icon": "file-text", "category": "公众号内容",
        }, actor)
        template_id = template["id"]
        revision_id = self.release.create_revision(self.operation_id, template_id, {
            "persona": PERSONA, "credit_cost": self.scope["credit_cost"],
            "model_config_id": self.scope["model_config_id"],
            "knowledge_requirement": "optional", "enterprise_config_requirement": "optional",
            "asset_requirement": "optional", "output_policy": "text",
        }, actor)["versions"][0]["id"]
        with self.store.connection() as conn:
            skill = self._skill(conn)
        self.control.bind_skills(template_id, revision_id, [{
            "skill_id": skill["skill_id"], "skill_version_id": skill["skill_version_id"],
        }], actor)
        self.control.bind_tools(template_id, revision_id, [
            {"tool_capability_id": "config_get", "invocation_requirement": "optional"},
            {"tool_capability_id": "knowledge_search", "invocation_requirement": "optional"},
            {"tool_capability_id": "asset_search", "invocation_requirement": "optional"},
        ], actor)
        fingerprint = self.control.detail(template_id)["versions"][0]["configuration_fingerprint"]
        self.release.seal_identity(self.operation_id, template_id, revision_id, fingerprint)
        self.control.validate(template_id, revision_id, actor)
        self._owned()
        return {"status": "staged", "operation_id": self.operation_id,
                "agent_id": template_id, "revision_id": revision_id,
                "configuration_fingerprint": fingerprint}

    def identity(self):
        operation, template_id, revision = self._owned()
        return {"operation_status": operation["status"], "operation_id": self.operation_id,
                "agent_id": template_id, "revision_id": revision["id"],
                "configuration_fingerprint": revision["configuration_fingerprint"]}

    def publish(self):
        operation, template_id, revision = self._owned()
        require(operation["status"] == "staged" and revision["status"] == "draft",
                "PHASE_B_PUBLISH_PRESTATE_MISMATCH")
        if self.rollout is not None:
            status = self.rollout.status()
            require(status["policy_enabled"] is False and status["policy_tenant_count"] == 0
                    and status["policy_slug_count"] == 0,
                    "PHASE_B_RUNTIME_POLICY_RESTORE_REQUIRED")
        with self.store.connection() as conn:
            tests = conn.execute(
                "SELECT t.status FROM agent_template_tests t JOIN agent_release_artifacts a "
                "ON a.artifact_id=t.task_id AND a.artifact_type='runtime_validation' "
                "WHERE a.operation_id=? AND t.agent_template_version_id=? AND t.test_type='runtime'",
                (self.operation_id, revision["id"]),
            ).fetchall()
            require(len(tests) == 1 and tests[0]["status"] == "passed",
                    "PHASE_B_RELEASE_VALIDATION_REQUIRED")
            inventory = self.release._zero_real_use(conn, self.operation_id, template_id, revision)
            require(len(inventory["validation_tasks"]) == 1,
                    "PHASE_B_RELEASE_VALIDATION_REQUIRED")
        self.control.publish(template_id, revision["id"], self.scope["actor"],
                             "production", release_operation_id=self.operation_id)
        return {"status": "published_disabled", **self.identity()}

    def enable(self):
        operation, template_id, revision = self._owned()
        require(operation["status"] == "published" and revision["status"] == "published",
                "PHASE_B_ENABLE_PRESTATE_MISMATCH")
        self.control.set_instance_status(template_id, self.scope["tenant"], "enabled",
                                         release_operation_id=self.operation_id)
        return {"status": "enabled", **self.identity()}

    def verify(self):
        operation, template_id, revision = self._owned()
        require(operation["status"] == "enabled" and revision["status"] == "published",
                "PHASE_B_FINAL_STATE_MISMATCH")
        with self.store.connection() as conn:
            template = conn.execute(
                "SELECT current_published_version_id,lifecycle_status FROM agent_templates WHERE id=?",
                (template_id,),
            ).fetchone()
            instances = conn.execute(
                "SELECT tenant_id,status,agent_template_version_id FROM tenant_agent_instances WHERE agent_id=?",
                (template_id,),
            ).fetchall()
            self._skill(conn)
            validation = conn.execute(
                "SELECT t.id,t.status,t.conversation_id,t.run_id,x.status AS test_status,"
                "c.amount FROM agent_release_artifacts a JOIN tasks t ON t.id=a.artifact_id "
                "JOIN agent_template_tests x ON x.task_id=t.id AND x.test_type='runtime' "
                "JOIN credit_transactions c ON c.task_id=t.id "
                "WHERE a.operation_id=? AND a.artifact_type='runtime_validation'",
                (self.operation_id,),
            ).fetchall()
            require(template and template["current_published_version_id"] == revision["id"]
                    and template["lifecycle_status"] == "published"
                    and len(instances) == 1
                    and (instances[0]["tenant_id"], instances[0]["status"],
                         instances[0]["agent_template_version_id"]) == (
                             self.scope["tenant"], "enabled", revision["id"]),
                    "PHASE_B_FINAL_STATE_MISMATCH")
            require(len(validation) == 1 and validation[0]["status"] == "completed"
                    and validation[0]["test_status"] == "passed"
                    and validation[0]["conversation_id"] and validation[0]["run_id"]
                    and validation[0]["amount"] == -self.scope["credit_cost"],
                    "PHASE_B_VALIDATION_EVIDENCE_MISMATCH")
        if self.rollout is not None:
            status = self.rollout.status()
            require(status["policy_enabled"] is False and status["policy_tenant_count"] == 0
                    and status["policy_slug_count"] == 0
                    and status["runtime_test_tenant_configured"] is False,
                    "PHASE_B_RUNTIME_POLICY_RESTORE_REQUIRED")
        return {"status": "verified", **self.identity()}

    def abort(self):
        if self.rollout is not None:
            status = self.rollout.status()
            require(status["policy_enabled"] is False and status["policy_tenant_count"] == 0
                    and status["policy_slug_count"] == 0
                    and status["runtime_test_tenant_configured"] is False,
                    "AGENT_PRODUCTIZATION_MANUAL_RECOVERY_REQUIRED")
        try:
            _, template_id, revision = self._owned()
        except AgentCatalogError as exc:
            if exc.status_code == 404:
                return {"status": "nothing_to_abort"}
            raise
        return self.release.abort_resolvable_absent(
            self.operation_id, template_id, revision["id"],
            revision["configuration_fingerprint"],
        )

    def commit(self):
        self.verify()
        _, template_id, revision = self._owned()
        return self.release.commit_provision(
            self.operation_id, template_id, revision["id"],
            revision["configuration_fingerprint"],
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "stage", "identity", "publish",
                                            "enable", "verify", "abort", "commit"))
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        require_inherited_release_lock()
        manifest = json.loads(args.candidate_manifest.read_text(encoding="utf-8"))
        from app.main import agent_catalog_control, skill_registry, store
        transition = Transition(
            manifest, control=agent_catalog_control, registry=skill_registry,
            store=store, bundle_root=Path(__file__).resolve().parents[1] / "skill_packages",
            rollout=Rollout(),
        )
        result = getattr(transition, args.command)()
    except Exception as exc:
        # Never print DB rows, Provider values, environment or secrets.
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
