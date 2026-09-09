CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS filename TEXT;
ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS mime_type TEXT;
ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS size_bytes BIGINT;
ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS uploaded_by TEXT;
ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS parsed_text TEXT;
ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS chunk_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS embedding_provider TEXT;
ALTER TABLE knowledge_files ADD COLUMN IF NOT EXISTS embedding_model TEXT;
CREATE TABLE IF NOT EXISTS knowledge_chunks (
  id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  knowledge_base_id TEXT, file_id TEXT NOT NULL REFERENCES knowledge_files(id) ON DELETE CASCADE,
  content TEXT NOT NULL, title TEXT, section TEXT, page_number INTEGER, chunk_index INTEGER NOT NULL,
  embedding vector(128), embedding_provider TEXT, embedding_model TEXT, embedding_version TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(file_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_tenant_file ON knowledge_chunks(tenant_id,file_id);
