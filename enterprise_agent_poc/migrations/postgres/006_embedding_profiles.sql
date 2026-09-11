CREATE TABLE IF NOT EXISTS embedding_index_profiles (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  dimension INTEGER NOT NULL CHECK (dimension > 0 AND dimension <= 4096),
  index_version TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL CHECK (state IN ('active', 'candidate', 'rollback')),
  base_url_domain TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE knowledge_chunks
  ADD CONSTRAINT knowledge_chunks_id_tenant_unique UNIQUE (id, tenant_id);

CREATE TABLE IF NOT EXISTS knowledge_chunk_embeddings (
  tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  chunk_id TEXT NOT NULL,
  profile_id TEXT NOT NULL REFERENCES embedding_index_profiles(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  dimension INTEGER NOT NULL CHECK (dimension = 1536),
  index_version TEXT NOT NULL,
  embedding vector(1536) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (chunk_id, index_version),
  FOREIGN KEY (chunk_id, tenant_id) REFERENCES knowledge_chunks(id, tenant_id) ON DELETE CASCADE,
  FOREIGN KEY (index_version) REFERENCES embedding_index_profiles(index_version) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_tenant_profile
  ON knowledge_chunk_embeddings(tenant_id, index_version, provider, model, dimension);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_vector_hnsw
  ON knowledge_chunk_embeddings USING hnsw (embedding vector_cosine_ops);
