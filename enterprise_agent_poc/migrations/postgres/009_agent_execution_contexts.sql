-- Stage 2: expand-only, immutable executable snapshots. No secret material.
CREATE TABLE IF NOT EXISTS agent_execution_contexts (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(id),
  agent_id TEXT NOT NULL REFERENCES agent_templates(id),
  instance_id TEXT NOT NULL REFERENCES tenant_agent_instances(instance_id),
  agent_template_version_id TEXT NOT NULL REFERENCES agent_template_versions(id),
  definition_source TEXT NOT NULL CHECK (definition_source='productized'),
  configuration_fingerprint TEXT NOT NULL,
  persona_snapshot TEXT NOT NULL,
  runtime_provider TEXT NOT NULL CHECK (runtime_provider='codex'),
  model_config_id TEXT NOT NULL,
  model_provider_id_snapshot TEXT NOT NULL,
  model_id_snapshot TEXT NOT NULL,
  reasoning_level_snapshot TEXT NOT NULL,
  skill_manifest_snapshot TEXT NOT NULL,
  tool_policy_snapshot TEXT NOT NULL,
  knowledge_requirement TEXT NOT NULL,
  asset_requirement TEXT NOT NULL,
  enterprise_config_requirement TEXT NOT NULL,
  output_policy TEXT NOT NULL CHECK (output_policy IN ('text','image_required')),
  credit_cost INTEGER NOT NULL CHECK (credit_cost > 0),
  profile_hash_version TEXT NOT NULL CHECK (profile_hash_version='v2'),
  runtime_profile_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (agent_id,agent_template_version_id) REFERENCES agent_template_versions(agent_template_id,id)
);
CREATE TABLE IF NOT EXISTS conversation_agent_contexts (
  conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
  context_id TEXT NOT NULL REFERENCES agent_execution_contexts(id)
);
CREATE TABLE IF NOT EXISTS task_agent_contexts (
  task_id TEXT PRIMARY KEY REFERENCES tasks(id),
  context_id TEXT NOT NULL REFERENCES agent_execution_contexts(id)
);
CREATE INDEX IF NOT EXISTS idx_execution_context_instance ON agent_execution_contexts(instance_id);
CREATE INDEX IF NOT EXISTS idx_execution_context_revision ON agent_execution_contexts(agent_template_version_id);
-- PostgreSQL guards (SQLite adapter installs equivalent guards locally).
CREATE OR REPLACE FUNCTION guard_execution_context() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Execution context association is immutable';
END $$;
-- S2 / historical snapshots pin package identity, including Draft test snapshots.
CREATE OR REPLACE FUNCTION guard_context_skill_delete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE version_id TEXT;
BEGIN
  IF TG_TABLE_NAME='skill_packages' THEN version_id := OLD.skill_version_id;
  ELSE version_id := OLD.id; END IF;
  IF EXISTS (SELECT 1 FROM agent_template_version_skills b JOIN agent_template_versions v ON v.id=b.agent_template_version_id WHERE b.skill_version_id=version_id AND v.status <> 'draft')
    OR EXISTS (SELECT 1 FROM agent_execution_contexts c, jsonb_array_elements(c.tool_policy_snapshot::jsonb->'skill_refs') r WHERE r->>'id'=version_id) THEN
    RAISE EXCEPTION 'Skill package referenced by published revision or historical context';
  END IF;
  RETURN OLD;
END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='context_guard_skill_delete') THEN
    CREATE TRIGGER context_guard_skill_delete BEFORE DELETE ON skill_versions FOR EACH ROW EXECUTE FUNCTION guard_context_skill_delete();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='context_guard_package_delete') THEN
    CREATE TRIGGER context_guard_package_delete BEFORE DELETE ON skill_packages FOR EACH ROW EXECUTE FUNCTION guard_context_skill_delete();
  END IF;
END $$;
CREATE OR REPLACE FUNCTION guard_execution_context_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME='agent_execution_contexts' THEN
    IF NOT EXISTS (SELECT 1 FROM tenant_agent_instances WHERE instance_id=NEW.instance_id AND tenant_id=NEW.tenant_id AND agent_id=NEW.agent_id) THEN
      RAISE EXCEPTION 'Execution context instance identity mismatch';
    END IF;
  ELSIF TG_TABLE_NAME='task_agent_contexts' THEN
    IF NOT EXISTS (SELECT 1 FROM tasks t JOIN agent_execution_contexts c ON c.id=NEW.context_id AND c.tenant_id=t.tenant_id AND c.agent_id=t.agent_id WHERE t.id=NEW.task_id) THEN
      RAISE EXCEPTION 'Task context identity mismatch';
    END IF;
  ELSE
    IF NOT EXISTS (SELECT 1 FROM conversations v JOIN agent_execution_contexts c ON c.id=NEW.context_id AND c.tenant_id=v.tenant_id AND c.agent_id=v.agent_id AND c.runtime_profile_id=v.runtime_profile_id WHERE v.id=NEW.conversation_id) THEN
      RAISE EXCEPTION 'Conversation context identity mismatch';
    END IF;
  END IF;
  RETURN NEW;
END $$;
DO $$ DECLARE item TEXT; BEGIN
  FOREACH item IN ARRAY ARRAY['agent_execution_contexts','conversation_agent_contexts','task_agent_contexts'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='immutable_' || item) THEN
      EXECUTE 'CREATE TRIGGER ' || quote_ident('immutable_' || item) || ' BEFORE UPDATE OR DELETE ON ' || quote_ident(item) || ' FOR EACH ROW EXECUTE FUNCTION guard_execution_context()';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='identity_' || item) THEN
      EXECUTE 'CREATE TRIGGER ' || quote_ident('identity_' || item) || ' BEFORE INSERT ON ' || quote_ident(item) || ' FOR EACH ROW EXECUTE FUNCTION guard_execution_context_identity()';
    END IF;
  END LOOP;
END $$;
