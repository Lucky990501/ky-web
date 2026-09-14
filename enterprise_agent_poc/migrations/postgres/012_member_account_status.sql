ALTER TABLE users
  ADD COLUMN IF NOT EXISTS account_status TEXT NOT NULL DEFAULT 'enabled'
  CHECK (account_status IN ('enabled', 'disabled'));
