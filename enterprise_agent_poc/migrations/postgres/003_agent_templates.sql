CREATE TABLE IF NOT EXISTS agent_templates (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL, icon TEXT NOT NULL, status TEXT NOT NULL,
  default_runtime_profile TEXT NOT NULL, credit_cost INTEGER NOT NULL,
  skill_manifest TEXT NOT NULL, allows_image_generation BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS tenant_agent_instances (
  tenant_id TEXT NOT NULL REFERENCES tenants(id), agent_id TEXT NOT NULL REFERENCES agent_templates(id),
  status TEXT NOT NULL, PRIMARY KEY(tenant_id, agent_id)
);
