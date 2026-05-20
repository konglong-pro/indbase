CREATE TABLE IF NOT EXISTS output_runs (
  output_run_id TEXT PRIMARY KEY,
  task_id TEXT,
  mode TEXT NOT NULL,
  input_kind TEXT NOT NULL,
  input_id TEXT,
  input_path TEXT,
  source_doc_id TEXT,
  source_revision_id TEXT,
  created_revision_id TEXT,
  contract_version TEXT,
  transition_version TEXT,
  transition_commit TEXT,
  config_hash TEXT,
  input_hash TEXT,
  normalized_hash TEXT,
  evidence_manifest_path TEXT,
  evidence_trace_path TEXT,
  input_stale INTEGER NOT NULL DEFAULT 0,
  input_archived INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  finished_at TEXT,
  deleted_at TEXT,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(source_doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(source_revision_id) REFERENCES document_revisions(revision_id),
  FOREIGN KEY(created_revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_output_runs_source_doc_id
  ON output_runs(source_doc_id);

CREATE INDEX IF NOT EXISTS idx_output_runs_status
  ON output_runs(status);

CREATE TABLE IF NOT EXISTS output_artifacts (
  output_artifact_id TEXT PRIMARY KEY,
  output_run_id TEXT NOT NULL,
  format TEXT NOT NULL,
  path TEXT,
  sha256 TEXT,
  status TEXT NOT NULL,
  error_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  FOREIGN KEY(output_run_id) REFERENCES output_runs(output_run_id)
);

CREATE INDEX IF NOT EXISTS idx_output_artifacts_output_run_id
  ON output_artifacts(output_run_id);

CREATE TABLE IF NOT EXISTS output_sources (
  output_source_id TEXT PRIMARY KEY,
  output_run_id TEXT NOT NULL,
  source_doc_id TEXT,
  source_revision_id TEXT,
  source_chunk_id TEXT,
  source_path TEXT,
  source_locator_json TEXT,
  mapping_confidence TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  FOREIGN KEY(output_run_id) REFERENCES output_runs(output_run_id),
  FOREIGN KEY(source_doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(source_revision_id) REFERENCES document_revisions(revision_id),
  FOREIGN KEY(source_chunk_id) REFERENCES chunks(chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_output_sources_output_run_id
  ON output_sources(output_run_id);

ALTER TABLE document_revisions ADD COLUMN parent_revision_id TEXT;
ALTER TABLE document_revisions ADD COLUMN derived_from_output_run_id TEXT;
ALTER TABLE document_revisions ADD COLUMN derivation_kind TEXT;
ALTER TABLE document_revisions ADD COLUMN derivation_tool TEXT;
ALTER TABLE document_revisions ADD COLUMN promotion_status TEXT;

UPDATE document_revisions
SET promotion_status = 'promoted'
WHERE revision_id IN (
  SELECT current_revision_id
  FROM documents
  WHERE current_revision_id IS NOT NULL
);

UPDATE document_revisions
SET promotion_status = 'superseded'
WHERE promotion_status IS NULL;
