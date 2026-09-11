CREATE TABLE IF NOT EXISTS skills (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS skill_versions (
  id TEXT PRIMARY KEY,
  skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  version TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('draft','published','deprecated')),
  checksum TEXT NOT NULL,
  created_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ,
  deprecated_at TIMESTAMPTZ,
  UNIQUE(skill_id, version),
  UNIQUE(skill_id, id)
);

CREATE TABLE IF NOT EXISTS skill_packages (
  id TEXT PRIMARY KEY,
  skill_version_id TEXT NOT NULL UNIQUE REFERENCES skill_versions(id) ON DELETE CASCADE,
  storage_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_skill_bindings (
  agent_id TEXT NOT NULL REFERENCES agent_templates(id) ON DELETE CASCADE,
  skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  skill_version_id TEXT NOT NULL REFERENCES skill_versions(id),
  updated_by TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(agent_id, skill_id),
  FOREIGN KEY(skill_id, skill_version_id) REFERENCES skill_versions(skill_id, id)
);

CREATE TABLE IF NOT EXISTS platform_admins (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  granted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_skill_versions_skill_status ON skill_versions(skill_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_skill_bindings_agent ON agent_skill_bindings(agent_id);
