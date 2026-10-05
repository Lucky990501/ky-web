-- A pending upload is private to its creator and can belong to one task only.
CREATE TABLE IF NOT EXISTS chat_image_attachments (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL REFERENCES tenants(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  storage_key TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  mime_type TEXT NOT NULL CHECK (mime_type IN ('image/jpeg','image/png','image/webp')),
  size_bytes BIGINT NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 10485760),
  width INTEGER NOT NULL CHECK (width BETWEEN 1 AND 4096),
  height INTEGER NOT NULL CHECK (height BETWEEN 1 AND 4096),
  task_id TEXT UNIQUE REFERENCES tasks(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_chat_image_attachments_owner
  ON chat_image_attachments(tenant_id,user_id,id);

ALTER TABLE generations ADD COLUMN IF NOT EXISTS requested_size TEXT;
