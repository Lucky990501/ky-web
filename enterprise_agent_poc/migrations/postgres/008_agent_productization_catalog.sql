-- Stage 1: expand-only control plane. No legacy conversion / execution contexts.
ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'draft' CHECK (lifecycle_status IN ('draft','published','deprecated'));
ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS definition_source TEXT NOT NULL DEFAULT 'legacy' CHECK (definition_source IN ('legacy','productized'));
ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;
ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS created_by TEXT REFERENCES users(id);
ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS updated_by TEXT REFERENCES users(id);

CREATE TABLE IF NOT EXISTS agent_template_versions (
  id TEXT PRIMARY KEY,
  agent_template_id TEXT NOT NULL REFERENCES agent_templates(id),
  revision INTEGER NOT NULL CHECK (revision > 0),
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','deprecated')),
  name TEXT NOT NULL, description TEXT NOT NULL, icon TEXT NOT NULL, category TEXT NOT NULL,
  persona TEXT NOT NULL, runtime_provider TEXT NOT NULL CHECK (runtime_provider='codex'),
  model_config_id TEXT NOT NULL,
  knowledge_requirement TEXT NOT NULL CHECK (knowledge_requirement IN ('none','optional','required')),
  asset_requirement TEXT NOT NULL CHECK (asset_requirement IN ('none','optional','required')),
  enterprise_config_requirement TEXT NOT NULL CHECK (enterprise_config_requirement IN ('none','optional','required')),
  output_policy TEXT NOT NULL CHECK (output_policy IN ('text','image_required')),
  credit_cost INTEGER NOT NULL CHECK (credit_cost > 0),
  tenant_override_schema TEXT NOT NULL,
  configuration_fingerprint TEXT NOT NULL,
  publication_scope TEXT CHECK (publication_scope IN ('local_test','production')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  published_at TIMESTAMPTZ, deprecated_at TIMESTAMPTZ,
  created_by TEXT REFERENCES users(id), updated_by TEXT REFERENCES users(id),
  UNIQUE (agent_template_id,revision), UNIQUE (agent_template_id,id)
);
ALTER TABLE agent_templates ADD COLUMN IF NOT EXISTS current_published_version_id TEXT REFERENCES agent_template_versions(id);

CREATE TABLE IF NOT EXISTS agent_template_version_skills (
  agent_template_version_id TEXT NOT NULL REFERENCES agent_template_versions(id),
  skill_id TEXT NOT NULL REFERENCES skills(id),
  skill_version_id TEXT NOT NULL REFERENCES skill_versions(id),
  PRIMARY KEY (agent_template_version_id,skill_id),
  FOREIGN KEY (skill_id,skill_version_id) REFERENCES skill_versions(skill_id,id)
);
CREATE TABLE IF NOT EXISTS tool_capabilities (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, server_id TEXT NOT NULL,
  required_scope TEXT NOT NULL, implemented BOOLEAN NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('enabled','disabled')),
  input_schema_revision INTEGER NOT NULL CHECK (input_schema_revision > 0),
  output_kind TEXT NOT NULL CHECK (output_kind IN ('text','image'))
);
CREATE TABLE IF NOT EXISTS agent_template_version_tools (
  agent_template_version_id TEXT NOT NULL REFERENCES agent_template_versions(id),
  tool_capability_id TEXT NOT NULL REFERENCES tool_capabilities(id),
  invocation_requirement TEXT NOT NULL CHECK (invocation_requirement IN ('optional','required')),
  PRIMARY KEY (agent_template_version_id,tool_capability_id)
);
CREATE TABLE IF NOT EXISTS agent_template_tests (
  id TEXT PRIMARY KEY,
  agent_template_version_id TEXT NOT NULL REFERENCES agent_template_versions(id),
  configuration_fingerprint TEXT NOT NULL,
  test_type TEXT NOT NULL CHECK (test_type IN ('validation','runtime')),
  task_id TEXT REFERENCES tasks(id),
  status TEXT NOT NULL CHECK (status IN ('passed','failed','invalidated')),
  result_json TEXT NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE tenant_agent_instances ADD COLUMN IF NOT EXISTS instance_id TEXT;
ALTER TABLE tenant_agent_instances ADD COLUMN IF NOT EXISTS agent_template_version_id TEXT REFERENCES agent_template_versions(id);
ALTER TABLE tenant_agent_instances ADD COLUMN IF NOT EXISTS overrides_json TEXT;
ALTER TABLE tenant_agent_instances ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE tenant_agent_instances ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_instance_identity ON tenant_agent_instances(instance_id);
CREATE INDEX IF NOT EXISTS idx_agent_template_tests_fingerprint ON agent_template_tests(agent_template_version_id,configuration_fingerprint);

-- Match the stable Template identity as well as the Revision foreign key.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_agent_instance_revision_identity') THEN
    ALTER TABLE tenant_agent_instances ADD CONSTRAINT fk_agent_instance_revision_identity FOREIGN KEY (agent_id,agent_template_version_id) REFERENCES agent_template_versions(agent_template_id,id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_agent_current_revision_identity') THEN
    ALTER TABLE agent_templates ADD CONSTRAINT fk_agent_current_revision_identity FOREIGN KEY (id,current_published_version_id) REFERENCES agent_template_versions(agent_template_id,id);
  END IF;
END $$;

-- Database-level published configuration / binding immutability.
CREATE OR REPLACE FUNCTION guard_agent_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='DELETE' AND OLD.status <> 'draft' THEN
    RAISE EXCEPTION 'Published revision is immutable';
  END IF;
  IF TG_OP='UPDATE' AND OLD.status <> 'draft' THEN
    IF NOT (OLD.status='published' AND NEW.status='deprecated'
      AND (to_jsonb(NEW) - 'status' - 'deprecated_at') = (to_jsonb(OLD) - 'status' - 'deprecated_at')) THEN
      RAISE EXCEPTION 'Published revision is immutable';
    END IF;
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION guard_agent_revision_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP <> 'INSERT' AND EXISTS (SELECT 1 FROM agent_template_versions WHERE id=OLD.agent_template_version_id AND status <> 'draft') THEN
    RAISE EXCEPTION 'Published revision binding is immutable';
  END IF;
  IF TG_OP <> 'DELETE' AND EXISTS (SELECT 1 FROM agent_template_versions WHERE id=NEW.agent_template_version_id AND status <> 'draft') THEN
    RAISE EXCEPTION 'Published revision binding is immutable';
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
-- Idempotent migration retry without dropping existing objects.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='guard_agent_revision_mutation') THEN
    CREATE TRIGGER guard_agent_revision_mutation BEFORE UPDATE OR DELETE ON agent_template_versions FOR EACH ROW EXECUTE FUNCTION guard_agent_revision();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='guard_agent_skill_binding') THEN
    CREATE TRIGGER guard_agent_skill_binding BEFORE INSERT OR UPDATE OR DELETE ON agent_template_version_skills FOR EACH ROW EXECUTE FUNCTION guard_agent_revision_binding();
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='guard_agent_tool_binding') THEN
    CREATE TRIGGER guard_agent_tool_binding BEFORE INSERT OR UPDATE OR DELETE ON agent_template_version_tools FOR EACH ROW EXECUTE FUNCTION guard_agent_revision_binding();
  END IF;
END $$;
