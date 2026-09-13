"""Stage 1 control plane only. Never resolves or executes a production Agent."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.store import POCStore


class AgentCatalogError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


# Public metadata only; credentials / endpoint remain in existing settings.
MODEL_CONFIGS = {
    "codex-deepseek-v4-pro-high": {
        "id": "codex-deepseek-v4-pro-high", "runtime_provider": "codex",
        "provider": "deepseek", "model": "deepseek-v4-pro", "reasoning_effort": "high",
        "label": "Codex / DeepSeek V4 Pro / high",
    },
}
TOOL_CAPABILITIES = {
    name: {"id": name, "name": name, "server_id": "platform",
           "required_scope": scope, "implemented": implemented, "status": "enabled",
           "input_schema_revision": 1, "output_kind": output}
    for name, scope, output, implemented in [
        ("config_get", "enterprise_config:read", "text", True),
        ("knowledge_search", "knowledge:search", "text", True),
        ("asset_search", "assets:search", "text", True),
        ("image_generation", "image:generate", "image", True),
        ("asset_get", "assets:search", "text", False),
    ]
}
OVERRIDE_SCHEMA = {"display_name": {"type": "string", "maxLength": 80},
                   "business_context_note": {"type": "string", "maxLength": 2000}}
DEFAULTS = {
    "name": "New Agent", "description": "", "icon": "bot", "category": "general",
    "persona": "", "runtime_provider": "codex", "model_config_id": "codex-deepseek-v4-pro-high",
    "knowledge_requirement": "optional", "asset_requirement": "optional",
    "enterprise_config_requirement": "optional", "output_policy": "text", "credit_cost": 1,
    "tenant_override_schema": OVERRIDE_SCHEMA,
}


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class AgentProductization:
    def __init__(self, store: POCStore, environment: str = "development"):
        self.store = store
        self.environment = environment

    def ensure_initialized(self):
        # Stage 1 local schema belongs to control-plane use, not legacy API
        # startup. This preserves the existing Registry restart/no-write gate.
        if not self.store.is_postgres:
            with self.store.connection() as conn:
                tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                columns = {r["name"] for r in conn.execute("PRAGMA table_info(agent_templates)")}
            required = {"agent_template_versions", "agent_template_version_skills", "tool_capabilities", "agent_template_version_tools", "agent_template_tests"}
            if not required <= tables or "current_published_version_id" not in columns:
                self.initialize()

    def initialize(self) -> None:
        """SQLite local adapter only. PostgreSQL 008 must be applied externally."""
        if self.store.is_postgres:
            return
        migration = Path(__file__).resolve().parents[1] / "migrations/postgres/008_agent_productization_catalog.sql"
        sql = migration.read_text(encoding="utf-8").split("-- Match the stable")[0]
        with self.store.connection() as conn:
            for statement in sql.split(";"):
                statement = re.sub(r"--[^\n]*", "", statement).strip()
                if not statement:
                    continue
                alter = re.match(r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+) (.*)", statement, re.S)
                if alter:
                    table, column, ddl = alter.groups()
                    if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                else:
                    conn.execute(statement)
            fields = list(DEFAULTS) + ["id", "agent_template_id", "revision", "configuration_fingerprint",
                                       "publication_scope", "created_at", "updated_at", "published_at", "created_by", "updated_by"]
            unchanged = " AND ".join(f"NEW.{f} IS OLD.{f}" for f in fields)
            conn.executescript(f"""
                CREATE TRIGGER IF NOT EXISTS guard_agent_revision_update BEFORE UPDATE ON agent_template_versions
                WHEN OLD.status <> 'draft' AND NOT (OLD.status='published' AND NEW.status='deprecated' AND {unchanged})
                BEGIN SELECT RAISE(ABORT,'Published revision is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS guard_agent_revision_delete BEFORE DELETE ON agent_template_versions
                WHEN OLD.status <> 'draft' BEGIN SELECT RAISE(ABORT,'Published revision is immutable'); END;
            """)
            for table in ["agent_template_version_skills", "agent_template_version_tools"]:
                for operation in ["INSERT", "UPDATE", "DELETE"]:
                    rows = ["NEW"] if operation == "INSERT" else ["OLD"] if operation == "DELETE" else ["OLD", "NEW"]
                    guard = " OR ".join(f"EXISTS(SELECT 1 FROM agent_template_versions WHERE id={row}.agent_template_version_id AND status <> 'draft')" for row in rows)
                    conn.execute(f"CREATE TRIGGER IF NOT EXISTS guard_{table}_{operation.lower()} BEFORE {operation} ON {table} WHEN {guard} BEGIN SELECT RAISE(ABORT,'Published revision binding is immutable'); END")
            for table, identity, column in [("tenant_agent_instances", "agent_id", "agent_template_version_id"),
                                            ("agent_templates", "id", "current_published_version_id")]:
                for operation in ["INSERT", "UPDATE"]:
                    conn.execute(f"CREATE TRIGGER IF NOT EXISTS guard_{table}_revision_{operation.lower()} BEFORE {operation} ON {table} WHEN NEW.{column} IS NOT NULL AND NOT EXISTS(SELECT 1 FROM agent_template_versions WHERE id=NEW.{column} AND agent_template_id=NEW.{identity}) BEGIN SELECT RAISE(ABORT,'Revision / Template identity mismatch'); END")
            self._seed_capabilities(conn)

    def _seed_capabilities(self, conn):
        # Never accept capability metadata from an admin request.
        for capability in TOOL_CAPABILITIES.values():
            conn.execute("INSERT INTO tool_capabilities(id,name,server_id,required_scope,implemented,status,input_schema_revision,output_kind) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING", tuple(capability.values()))
            actual = dict(conn.execute("SELECT * FROM tool_capabilities WHERE id=?", (capability["id"],)).fetchone())
            if actual != capability:
                raise AgentCatalogError("Tool capability metadata drift; controlled review required", 409)

    def _ready(self, conn):
        if self.store.is_postgres:
            exists = conn.execute("SELECT to_regclass('public.agent_template_versions') AS name").fetchone()
            if not exists["name"]:
                raise AgentCatalogError("Stage 1 migration 008 pending; existing Agents unchanged.", 503)

    def options(self):
        return {"model_configs": list(MODEL_CONFIGS.values()), "tool_capabilities": list(TOOL_CAPABILITIES.values()),
                "tenant_override_schema": OVERRIDE_SCHEMA, "runtime_test_status": "Runtime Test Pending",
                "execution_enabled": False, "local_test_publish_allowed": self.environment != "production"}

    def _template(self, conn, template_id, *, lock=False):
        self._ready(conn)
        suffix = " FOR UPDATE" if lock and self.store.is_postgres else ""
        row = conn.execute("SELECT * FROM agent_templates WHERE id=?" + suffix, (template_id,)).fetchone()
        if not row:
            raise AgentCatalogError("Agent Template not found", 404)
        return dict(row)

    def _version(self, conn, template_id, version_id, *, draft=False):
        suffix = " FOR UPDATE" if self.store.is_postgres else ""
        row = conn.execute("SELECT * FROM agent_template_versions WHERE id=? AND agent_template_id=?" + suffix, (version_id, template_id)).fetchone()
        if not row:
            raise AgentCatalogError("Revision not found", 404)
        value = dict(row)
        if draft and value["status"] != "draft":
            raise AgentCatalogError("Published / Deprecated revision is immutable; create a new Draft", 409)
        return value

    def _productized(self, template):
        if template["definition_source"] != "productized":
            raise AgentCatalogError("Legacy Agent is read-only in Stage 1; controlled import deferred", 409)

    def _fields(self, payload, base=None):
        if not isinstance(payload, dict) or set(payload) - set(DEFAULTS):
            raise AgentCatalogError("Unknown configuration field; model/secret/tool metadata not editable")
        fields = {**(base or DEFAULTS), **payload}
        for name, limit in {"name": 120, "description": 2000, "icon": 40, "category": 80, "persona": 16000}.items():
            if not isinstance(fields[name], str) or len(fields[name]) > limit:
                raise AgentCatalogError(f"Invalid {name}")
        if not fields["name"].strip() or not re.fullmatch(r"[a-z0-9-]+", fields["icon"]) or not fields["category"].strip():
            raise AgentCatalogError("Invalid name / icon / category")
        if fields["runtime_provider"] != "codex" or not isinstance(fields["model_config_id"], str) or fields["model_config_id"] not in MODEL_CONFIGS:
            raise AgentCatalogError("Unapproved Model Config / Runtime")
        for name in ["knowledge_requirement", "asset_requirement", "enterprise_config_requirement"]:
            if not isinstance(fields[name], str) or fields[name] not in {"none", "optional", "required"}:
                raise AgentCatalogError(f"Invalid {name}")
        if not isinstance(fields["output_policy"], str) or fields["output_policy"] not in {"text", "image_required"}:
            raise AgentCatalogError("Invalid output policy")
        if type(fields["credit_cost"]) is not int or not 0 < fields["credit_cost"] <= 1_000_000:
            raise AgentCatalogError("Credit cost must be a positive integer")
        if fields["tenant_override_schema"] != OVERRIDE_SCHEMA:
            raise AgentCatalogError("V1 Tenant Override schema is fixed")
        return fields

    def list_templates(self):
        with self.store.connection() as conn:
            self._ready(conn)
            return [dict(r) for r in conn.execute("SELECT * FROM agent_templates ORDER BY id")]

    def detail(self, template_id):
        with self.store.connection() as conn:
            template = self._template(conn, template_id)
            template["versions"] = [self._view(conn, dict(r)) for r in conn.execute("SELECT * FROM agent_template_versions WHERE agent_template_id=? ORDER BY revision DESC", (template_id,))]
            return template

    def _view(self, conn, version):
        version["tenant_override_schema"] = json.loads(version["tenant_override_schema"])
        version["skills"] = [dict(r) for r in conn.execute("SELECT b.skill_id,b.skill_version_id,s.slug,v.version,v.status,v.checksum FROM agent_template_version_skills b JOIN skills s ON s.id=b.skill_id JOIN skill_versions v ON v.id=b.skill_version_id WHERE b.agent_template_version_id=? ORDER BY b.skill_id", (version["id"],))]
        version["tools"] = [dict(r) for r in conn.execute("SELECT tool_capability_id,invocation_requirement FROM agent_template_version_tools WHERE agent_template_version_id=? ORDER BY tool_capability_id", (version["id"],))]
        version["tests"] = [dict(r) for r in conn.execute("SELECT * FROM agent_template_tests WHERE agent_template_version_id=? ORDER BY created_at DESC,id", (version["id"],))]
        version["runtime_test_status"] = "Runtime Test Pending"
        version["production_ready"] = False  # No execution resolver / real Runtime Test in Stage 1.
        return version

    def create_template(self, payload, actor):
        allowed = {"name", "slug", "description", "icon", "category"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise AgentCatalogError("Invalid Template fields")
        slug = payload.get("slug", "")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or len(slug) > 100:
            raise AgentCatalogError("Invalid slug")
        fields = self._fields({k: v for k, v in payload.items() if k != "slug"})
        template_id = str(uuid.uuid4())
        with self.store.connection() as conn:
            self._ready(conn)
            if conn.execute("SELECT id FROM agent_templates WHERE slug=?", (slug,)).fetchone():
                raise AgentCatalogError("Slug already exists", 409)
            conn.execute("INSERT INTO agent_templates(id,name,slug,description,icon,status,default_runtime_profile,credit_cost,skill_manifest,allows_image_generation,category,definition_source,lifecycle_status,created_at,updated_at,created_by,updated_by) VALUES (?,?,?,?,?,'disabled','default',?,'{}',FALSE,?,'productized','draft',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?)", (template_id, fields["name"], slug, fields["description"], fields["icon"], fields["credit_cost"], fields["category"], actor, actor))
        return self.detail(template_id)

    def create_version(self, template_id, payload, actor):
        if not isinstance(payload, dict):
            raise AgentCatalogError("Invalid Draft fields")
        payload = dict(payload)
        source_id = payload.pop("from_version_id", None)
        if source_id is not None and not isinstance(source_id, str):
            raise AgentCatalogError("Invalid source Revision ID")
        with self.store.connection() as conn:
            if not self.store.is_postgres:
                conn.execute("BEGIN IMMEDIATE")
            template = self._template(conn, template_id, lock=True)
            self._productized(template)
            base = {**DEFAULTS, **{f: template[f] for f in ["name", "description", "icon", "category"]}}
            if source_id:
                source = self._version(conn, template_id, source_id)
                invalid = conn.execute("SELECT 1 FROM agent_template_version_skills b JOIN skill_versions v ON v.id=b.skill_version_id WHERE b.agent_template_version_id=? AND v.status <> 'published'", (source_id,)).fetchone()
                if invalid:
                    raise AgentCatalogError("Cannot clone non-Published Skill references", 409)
                base = {k: source[k] for k in DEFAULTS}
                base["tenant_override_schema"] = json.loads(base["tenant_override_schema"])
            fields = self._fields(payload, base)
            revision = conn.execute("SELECT COALESCE(MAX(revision),0)+1 AS next FROM agent_template_versions WHERE agent_template_id=?", (template_id,)).fetchone()["next"]
            version_id = str(uuid.uuid4())
            columns = list(DEFAULTS)
            values = [canonical(fields[k]) if k == "tenant_override_schema" else fields[k] for k in columns]
            conn.execute(f"INSERT INTO agent_template_versions(id,agent_template_id,revision,{','.join(columns)},configuration_fingerprint,created_by,updated_by) VALUES ({','.join('?' for _ in range(len(columns)+6))})", (version_id, template_id, revision, *values, "pending", actor, actor))
            if source_id:
                for table, columns in [("agent_template_version_skills", "skill_id,skill_version_id"), ("agent_template_version_tools", "tool_capability_id,invocation_requirement")]:
                    conn.execute(f"INSERT INTO {table}(agent_template_version_id,{columns}) SELECT ?,{columns} FROM {table} WHERE agent_template_version_id=?", (version_id, source_id))
            self._refresh_fingerprint(conn, version_id, actor)
        return self.detail(template_id)

    def edit_version(self, template_id, version_id, payload, actor):
        with self.store.connection() as conn:
            self._productized(self._template(conn, template_id, lock=True))
            version = self._version(conn, template_id, version_id, draft=True)
            base = {k: version[k] for k in DEFAULTS}
            base["tenant_override_schema"] = json.loads(base["tenant_override_schema"])
            fields = self._fields(payload, base)
            conn.execute(f"UPDATE agent_template_versions SET {','.join(k+'=?' for k in DEFAULTS)} WHERE id=?", (*[canonical(fields[k]) if k == "tenant_override_schema" else fields[k] for k in DEFAULTS], version_id))
            self._refresh_fingerprint(conn, version_id, actor)
        return self.detail(template_id)

    def bind_skills(self, template_id, version_id, bindings, actor):
        with self.store.connection() as conn:
            self._productized(self._template(conn, template_id, lock=True))
            self._version(conn, template_id, version_id, draft=True)
            if not isinstance(bindings, list) or len(bindings) > 30:
                raise AgentCatalogError("Invalid Skill bindings")
            seen = set()
            for binding in bindings:
                if not isinstance(binding, dict) or set(binding) != {"skill_id", "skill_version_id"}:
                    raise AgentCatalogError("Skill binding requires exact Skill / Version IDs")
                skill_id, skill_version_id = binding["skill_id"], binding["skill_version_id"]
                if not isinstance(skill_id, str) or not isinstance(skill_version_id, str):
                    raise AgentCatalogError("Invalid Skill / Version IDs")
                row = conn.execute("SELECT skill_id,status FROM skill_versions WHERE id=?", (skill_version_id,)).fetchone()
                if not row or row["skill_id"] != skill_id or row["status"] != "published" or skill_id in seen:
                    raise AgentCatalogError("Only matching Published Skill Versions may be bound")
                seen.add(skill_id)
            conn.execute("DELETE FROM agent_template_version_skills WHERE agent_template_version_id=?", (version_id,))
            for binding in bindings:
                conn.execute("INSERT INTO agent_template_version_skills(agent_template_version_id,skill_id,skill_version_id) VALUES (?,?,?)", (version_id, binding["skill_id"], binding["skill_version_id"]))
            self._refresh_fingerprint(conn, version_id, actor)
        return self.detail(template_id)

    def bind_tools(self, template_id, version_id, bindings, actor):
        with self.store.connection() as conn:
            self._productized(self._template(conn, template_id, lock=True))
            self._version(conn, template_id, version_id, draft=True)
            if not isinstance(bindings, list) or len(bindings) > len(TOOL_CAPABILITIES):
                raise AgentCatalogError("Invalid Tool bindings")
            seen = set()
            for binding in bindings:
                if not isinstance(binding, dict) or set(binding) != {"tool_capability_id", "invocation_requirement"}:
                    raise AgentCatalogError("Invalid Tool binding; presence is allowed, absence is denied")
                tool_id = binding["tool_capability_id"]
                if not isinstance(tool_id, str):
                    raise AgentCatalogError("Invalid Tool ID")
                capability = TOOL_CAPABILITIES.get(tool_id)
                if not capability or not capability["implemented"] or capability["status"] != "enabled" or tool_id in seen:
                    raise AgentCatalogError("Tool not allowlisted / implemented")
                if not isinstance(binding["invocation_requirement"], str) or binding["invocation_requirement"] not in {"optional", "required"}:
                    raise AgentCatalogError("Invalid invocation requirement")
                seen.add(tool_id)
            self._seed_capabilities(conn)
            conn.execute("DELETE FROM agent_template_version_tools WHERE agent_template_version_id=?", (version_id,))
            for binding in bindings:
                conn.execute("INSERT INTO agent_template_version_tools(agent_template_version_id,tool_capability_id,invocation_requirement) VALUES (?,?,?)", (version_id, binding["tool_capability_id"], binding["invocation_requirement"]))
            self._refresh_fingerprint(conn, version_id, actor)
        return self.detail(template_id)

    def _fingerprint(self, conn, version):
        config = {k: version[k] for k in DEFAULTS}
        config["tenant_override_schema"] = json.loads(config["tenant_override_schema"])
        config["model_config"] = MODEL_CONFIGS.get(config["model_config_id"])
        config["skills"] = [dict(r) for r in conn.execute("SELECT b.skill_id,b.skill_version_id,v.checksum FROM agent_template_version_skills b JOIN skill_versions v ON v.id=b.skill_version_id WHERE b.agent_template_version_id=? ORDER BY b.skill_id", (version["id"],))]
        config["tools"] = [{**dict(r), "capability": TOOL_CAPABILITIES.get(r["tool_capability_id"])} for r in conn.execute("SELECT tool_capability_id,invocation_requirement FROM agent_template_version_tools WHERE agent_template_version_id=? ORDER BY tool_capability_id", (version["id"],))]
        return hashlib.sha256(canonical(config).encode()).hexdigest()

    def _refresh_fingerprint(self, conn, version_id, actor):
        version = dict(conn.execute("SELECT * FROM agent_template_versions WHERE id=?", (version_id,)).fetchone())
        fingerprint = self._fingerprint(conn, version)
        if fingerprint != version["configuration_fingerprint"]:
            conn.execute("UPDATE agent_template_tests SET status='invalidated' WHERE agent_template_version_id=? AND status <> 'invalidated'", (version_id,))
        conn.execute("UPDATE agent_template_versions SET configuration_fingerprint=?,updated_at=CURRENT_TIMESTAMP,updated_by=? WHERE id=?", (fingerprint, actor, version_id))

    def _validation_errors(self, conn, version):
        errors = []
        try:
            fields = {k: version[k] for k in DEFAULTS}
            fields["tenant_override_schema"] = json.loads(fields["tenant_override_schema"])
            self._fields(fields)
        except (AgentCatalogError, ValueError, TypeError):
            errors.append("Invalid revision configuration")
        if not version["persona"].strip():
            errors.append("Persona required")
        tools = {r["tool_capability_id"]: r["invocation_requirement"] for r in conn.execute("SELECT * FROM agent_template_version_tools WHERE agent_template_version_id=?", (version["id"],))}
        for tool_id in tools:
            tool = TOOL_CAPABILITIES.get(tool_id)
            if not tool or not tool["implemented"] or tool["status"] != "enabled":
                errors.append("Unimplemented / unapproved Tool")
            actual = conn.execute("SELECT * FROM tool_capabilities WHERE id=?", (tool_id,)).fetchone()
            if not actual or dict(actual) != tool:
                errors.append("Tool capability metadata drift")
        for field, tool in [("enterprise_config_requirement", "config_get"), ("knowledge_requirement", "knowledge_search"), ("asset_requirement", "asset_search")]:
            if version[field] == "required" and tools.get(tool) != "required":
                errors.append(f"{field} requires required {tool} binding")
            if version[field] == "none" and tool in tools:
                errors.append(f"{field}=none forbids {tool}")
        if version["output_policy"] == "image_required" and tools.get("image_generation") != "required":
            errors.append("image_required requires required image_generation binding")
        if version["output_policy"] == "text" and "image_generation" in tools:
            errors.append("text output forbids image_generation in V1")
        invalid = conn.execute("SELECT 1 FROM agent_template_version_skills b JOIN skill_versions v ON v.id=b.skill_version_id WHERE b.agent_template_version_id=? AND (v.status <> 'published' OR v.skill_id <> b.skill_id)", (version["id"],)).fetchone()
        if invalid:
            errors.append("Bound Skill Version no longer Published / matching")
        if self._fingerprint(conn, version) != version["configuration_fingerprint"]:
            errors.append("Configuration fingerprint drift")
        return errors

    def validate(self, template_id, version_id, actor):
        with self.store.connection() as conn:
            self._productized(self._template(conn, template_id, lock=True))
            version = self._version(conn, template_id, version_id, draft=True)
            errors = self._validation_errors(conn, version)
            result = {"errors": errors, "runtime_test_status": "Runtime Test Pending", "production_ready": False}
            conn.execute("INSERT INTO agent_template_tests(id,agent_template_version_id,configuration_fingerprint,test_type,task_id,status,result_json,created_at) VALUES (?,?,?,'validation',NULL,?,?,?)", (str(uuid.uuid4()), version_id, version["configuration_fingerprint"], "failed" if errors else "passed", canonical(result), datetime.now(timezone.utc).isoformat()))
        return self.detail(template_id)

    def publish(self, template_id, version_id, actor, mode="production"):
        with self.store.connection() as conn:
            self._productized(self._template(conn, template_id, lock=True))
            version = self._version(conn, template_id, version_id, draft=True)
            errors = self._validation_errors(conn, version)
            if errors:
                raise AgentCatalogError("; ".join(errors), 409)
            test = conn.execute("SELECT id FROM agent_template_tests WHERE agent_template_version_id=? AND configuration_fingerprint=? AND test_type='validation' AND status='passed'", (version_id, version["configuration_fingerprint"])).fetchone()
            if not test:
                raise AgentCatalogError("Current configuration validation required", 409)
            if mode != "local_test" or self.environment == "production":
                raise AgentCatalogError("Runtime Test Pending: Stage 1 cannot publish production-ready revisions", 409)
            conn.execute("UPDATE agent_template_versions SET status='published',publication_scope='local_test',published_at=CURRENT_TIMESTAMP,updated_by=? WHERE id=?", (actor, version_id))
            conn.execute("UPDATE agent_templates SET current_published_version_id=?,lifecycle_status='published',published_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP,updated_by=? WHERE id=?", (version_id, actor, template_id))
        return self.detail(template_id)

    def deprecate(self, template_id, version_id, actor):
        with self.store.connection() as conn:
            self._productized(self._template(conn, template_id, lock=True))
            version = self._version(conn, template_id, version_id)
            if version["status"] != "published":
                raise AgentCatalogError("Only Published revisions may be deprecated", 409)
            conn.execute("UPDATE agent_template_versions SET status='deprecated',deprecated_at=CURRENT_TIMESTAMP WHERE id=?", (version_id,))
            conn.execute("UPDATE agent_templates SET current_published_version_id=NULL,lifecycle_status='deprecated',updated_at=CURRENT_TIMESTAMP,updated_by=? WHERE id=? AND current_published_version_id=?", (actor, template_id, version_id))
        return self.detail(template_id)

    def configure_instance(self, template_id, tenant_id, version_id, overrides):
        if not isinstance(overrides, dict) or set(overrides) - set(OVERRIDE_SCHEMA):
            raise AgentCatalogError("Tenant Override outside V1 allowlist")
        for key, value in overrides.items():
            if not isinstance(value, str) or len(value) > OVERRIDE_SCHEMA[key]["maxLength"]:
                raise AgentCatalogError("Invalid Tenant Override")
        with self.store.connection() as conn:
            self._productized(self._template(conn, template_id, lock=True))
            version = self._version(conn, template_id, version_id)
            if version["status"] != "published":
                raise AgentCatalogError("Only Published revisions may configure a new Instance", 409)
            if not conn.execute("SELECT id FROM tenants WHERE id=?", (tenant_id,)).fetchone():
                raise AgentCatalogError("Tenant not found", 404)
            instance_id = str(uuid.uuid4())
            conn.execute("INSERT INTO tenant_agent_instances(tenant_id,agent_id,status,instance_id,agent_template_version_id,overrides_json,created_at,updated_at) VALUES (?,?,'configured',?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP) ON CONFLICT(tenant_id,agent_id) DO UPDATE SET agent_template_version_id=excluded.agent_template_version_id,overrides_json=excluded.overrides_json,updated_at=CURRENT_TIMESTAMP", (tenant_id, template_id, instance_id, version_id, canonical(overrides)))
            row = dict(conn.execute("SELECT * FROM tenant_agent_instances WHERE tenant_id=? AND agent_id=?", (tenant_id, template_id)).fetchone())
        return {**row, "execution_enabled": False, "runtime_test_status": "Runtime Test Pending"}
