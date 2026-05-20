-- v0.3.1 Taxonomy Foundation: typed tags, provenance, and governance tables.

ALTER TABLE tags ADD COLUMN type TEXT NOT NULL DEFAULT 'topic';
ALTER TABLE tags ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE tags ADD COLUMN created_by TEXT;

ALTER TABLE tag_aliases ADD COLUMN status TEXT NOT NULL DEFAULT 'active';

ALTER TABLE documents ADD COLUMN category_source TEXT;
ALTER TABLE documents ADD COLUMN category_suggestion_id TEXT;
ALTER TABLE documents ADD COLUMN category_updated_by TEXT;
ALTER TABLE documents ADD COLUMN category_updated_at TEXT;

ALTER TABLE document_tags ADD COLUMN revision_id TEXT;
ALTER TABLE document_tags ADD COLUMN evidence_chunk_ids_json TEXT;
ALTER TABLE document_tags ADD COLUMN suggestion_id TEXT;
ALTER TABLE document_tags ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE document_tags ADD COLUMN created_by TEXT;

UPDATE tags
SET type = 'method', created_by = COALESCE(created_by, 'legacy_migration')
WHERE normalized_name IN ('rag', 'embedding', 'ocr')
  AND deleted_at IS NULL;

UPDATE tags
SET type = 'tool', created_by = COALESCE(created_by, 'legacy_migration')
WHERE normalized_name IN ('sqlite', 'python')
  AND deleted_at IS NULL;

UPDATE tags
SET type = 'topic', created_by = COALESCE(created_by, 'legacy_migration')
WHERE normalized_name = 'ai'
  AND deleted_at IS NULL;

UPDATE tags
SET type = 'topic', created_by = COALESCE(created_by, 'legacy_migration')
WHERE created_by IS NULL
  AND deleted_at IS NULL;

UPDATE tags
SET status = 'archived'
WHERE deleted_at IS NOT NULL
  AND status = 'active';

UPDATE documents
SET category_source = 'system_seed',
    category_updated_by = 'legacy_migration',
    category_updated_at = COALESCE(updated_at, created_at)
WHERE category_id IS NULL
   OR category_id = ''
   OR category_id = 'cat_uncategorized';

UPDATE documents
SET category_source = 'manual_legacy',
    category_updated_by = 'legacy_migration',
    category_updated_at = COALESCE(updated_at, created_at)
WHERE category_source IS NULL
  AND category_id IS NOT NULL
  AND category_id != ''
  AND category_id != 'cat_uncategorized';

CREATE TABLE IF NOT EXISTS document_profiles (
  profile_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  profile_version TEXT NOT NULL,
  summary_for_classification TEXT,
  features_json TEXT,
  evidence_chunk_ids_json TEXT,
  status TEXT NOT NULL CHECK (status IN ('active', 'stale')),
  created_by TEXT,
  model TEXT,
  prompt_version TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_document_profiles_doc_revision
  ON document_profiles(doc_id, revision_id);

CREATE INDEX IF NOT EXISTS idx_document_profiles_doc_status
  ON document_profiles(doc_id, status);

CREATE TABLE IF NOT EXISTS feature_atoms (
  feature_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  chunk_id TEXT NOT NULL,
  text TEXT NOT NULL,
  normalized_text TEXT,
  type TEXT NOT NULL CHECK (
    type IN ('topic', 'method', 'tool', 'entity', 'workflow', 'format', 'language', 'project')
  ),
  confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
  quote TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('deterministic', 'llm')),
  status TEXT NOT NULL CHECK (
    status IN ('active', 'stale', 'promoted', 'ignored', 'local_keyword')
  ),
  created_at TEXT NOT NULL,
  updated_at TEXT,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id),
  FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_feature_atoms_doc_revision
  ON feature_atoms(doc_id, revision_id);

CREATE INDEX IF NOT EXISTS idx_feature_atoms_chunk
  ON feature_atoms(chunk_id);

CREATE TABLE IF NOT EXISTS tag_candidates (
  candidate_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  type TEXT NOT NULL CHECK (
    type IN ('topic', 'method', 'tool', 'entity', 'workflow', 'format', 'language', 'project')
  ),
  description TEXT,
  evidence_doc_ids_json TEXT NOT NULL,
  evidence_chunk_ids_json TEXT NOT NULL,
  similar_existing_tag_ids_json TEXT,
  occurrence_count INTEGER,
  distinct_doc_count INTEGER,
  confidence REAL,
  status TEXT NOT NULL CHECK (
    status IN ('pending', 'accepted', 'rejected', 'merged', 'archived', 'blocked')
  ),
  promoted_tag_id TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  FOREIGN KEY(promoted_tag_id) REFERENCES tags(tag_id)
);

CREATE INDEX IF NOT EXISTS idx_tag_candidates_normalized_type
  ON tag_candidates(normalized_name, type);

CREATE INDEX IF NOT EXISTS idx_tag_candidates_status
  ON tag_candidates(status);

CREATE TABLE IF NOT EXISTS taxonomy_suggestions (
  suggestion_id TEXT PRIMARY KEY,
  type TEXT NOT NULL CHECK (
    type IN (
      'category_assign',
      'tag_assign',
      'alias_add',
      'tag_candidate',
      'tag_merge',
      'tag_deprecate',
      'tag_archive',
      'tag_candidate_promote'
    )
  ),
  doc_id TEXT,
  revision_id TEXT,
  target_id TEXT,
  payload_json TEXT NOT NULL,
  confidence REAL,
  status TEXT NOT NULL CHECK (
    status IN ('pending', 'accepted', 'rejected', 'stale', 'superseded')
  ),
  source TEXT NOT NULL CHECK (source IN ('deterministic', 'llm', 'janitor', 'manual')),
  created_at TEXT NOT NULL,
  updated_at TEXT,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_taxonomy_suggestions_doc_revision
  ON taxonomy_suggestions(doc_id, revision_id);

CREATE INDEX IF NOT EXISTS idx_taxonomy_suggestions_status
  ON taxonomy_suggestions(status);

CREATE TABLE IF NOT EXISTS tag_lifecycle_events (
  event_id TEXT PRIMARY KEY,
  tag_id TEXT,
  event_type TEXT NOT NULL CHECK (
    event_type IN (
      'created',
      'alias_added',
      'merged',
      'deprecated',
      'archived',
      'restored',
      'blocked',
      'promoted_from_candidate'
    )
  ),
  payload_json TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(tag_id) REFERENCES tags(tag_id)
);

CREATE INDEX IF NOT EXISTS idx_tag_lifecycle_events_tag
  ON tag_lifecycle_events(tag_id);

CREATE TABLE IF NOT EXISTS model_calls (
  model_call_id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  model TEXT,
  prompt_name TEXT,
  prompt_version TEXT,
  status TEXT NOT NULL CHECK (
    status IN ('pending', 'completed', 'failed', 'timeout', 'rejected')
  ),
  input_json TEXT,
  output_json TEXT,
  error_json TEXT,
  tokens_input INTEGER,
  tokens_output INTEGER,
  cost_usd REAL,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_model_calls_status
  ON model_calls(status);
