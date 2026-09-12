-- History/project listing and authenticated image reads introduced in V1.
-- Keep this migration append-only: existing migration checksums are immutable.
CREATE INDEX IF NOT EXISTS idx_tasks_history
    ON tasks(tenant_id, user_id, conversation_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_generations_history
    ON generations(tenant_id, user_id, conversation_id, created_at, id);

CREATE INDEX IF NOT EXISTS idx_generations_storage
    ON generations(tenant_id, storage_key);

CREATE INDEX IF NOT EXISTS idx_assets_tenant_url
    ON assets(tenant_id, url);

CREATE INDEX IF NOT EXISTS idx_conversation_owners_history
    ON conversation_owners(user_id, deleted_at, conversation_id);
