CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY,
  current_revision_id TEXT,
  title TEXT,
  original_title TEXT,
  filename_slug TEXT,
  status TEXT NOT NULL,
  source_type TEXT,
  source_uri TEXT,
  normalized_source_uri TEXT,
  source_hash TEXT,
  canonical_path TEXT,
  original_path TEXT,
  language TEXT,
  category_id TEXT,
  quality_status TEXT,
  quality_signals_json TEXT,
  needs_review INTEGER,
  ingest_status TEXT,
  fts_status TEXT,
  embedding_status TEXT,
  classification_status TEXT,
  archived_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_documents_status
  ON documents(status);

CREATE INDEX IF NOT EXISTS idx_documents_normalized_source_uri
  ON documents(normalized_source_uri);

CREATE TABLE IF NOT EXISTS document_revisions (
  revision_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  markdown_path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  converter_name TEXT,
  converter_version TEXT,
  chunk_strategy TEXT,
  text_length INTEGER,
  chunk_count INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  notes TEXT,
  UNIQUE(doc_id, sequence),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
);

CREATE INDEX IF NOT EXISTS idx_document_revisions_doc_id
  ON document_revisions(doc_id);

CREATE TABLE IF NOT EXISTS source_files (
  source_file_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  source_uri TEXT,
  normalized_source_uri TEXT,
  original_filename TEXT,
  original_ext TEXT,
  mime_type TEXT,
  size_bytes INTEGER,
  source_hash TEXT,
  original_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
);

CREATE INDEX IF NOT EXISTS idx_source_files_doc_id
  ON source_files(doc_id);

CREATE INDEX IF NOT EXISTS idx_source_files_source_hash
  ON source_files(source_hash);

CREATE TABLE IF NOT EXISTS ingest_runs (
  ingest_id TEXT PRIMARY KEY,
  task_id TEXT,
  source_kind TEXT NOT NULL,
  source_input TEXT,
  status TEXT NOT NULL,
  total_items INTEGER,
  succeeded_items INTEGER,
  failed_items INTEGER,
  unsupported_items INTEGER,
  duplicate_items INTEGER,
  review_items_count INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  finished_at TEXT
);

CREATE TABLE IF NOT EXISTS ingest_items (
  ingest_item_id TEXT PRIMARY KEY,
  ingest_id TEXT NOT NULL,
  doc_id TEXT,
  source_uri TEXT,
  normalized_source_uri TEXT,
  status TEXT NOT NULL,
  error_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  finished_at TEXT,
  FOREIGN KEY(ingest_id) REFERENCES ingest_runs(ingest_id),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
);

CREATE INDEX IF NOT EXISTS idx_ingest_items_ingest_id
  ON ingest_items(ingest_id);

CREATE TABLE IF NOT EXISTS converter_runs (
  converter_run_id TEXT PRIMARY KEY,
  doc_id TEXT,
  revision_id TEXT,
  converter_name TEXT,
  converter_version TEXT,
  input_hash TEXT,
  output_hash TEXT,
  warnings_json TEXT,
  quality_signals_json TEXT,
  status TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_converter_runs_doc_id
  ON converter_runs(doc_id);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  heading_path_json TEXT,
  text TEXT NOT NULL,
  start_offset INTEGER,
  end_offset INTEGER,
  source_page INTEGER,
  language TEXT,
  token_count INTEGER,
  content_hash TEXT,
  is_current INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  UNIQUE(revision_id, sequence),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc_revision
  ON chunks(doc_id, revision_id);

CREATE INDEX IF NOT EXISTS idx_chunks_current
  ON chunks(is_current);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id UNINDEXED,
  doc_id UNINDEXED,
  revision_id UNINDEXED,
  title,
  heading_path,
  text,
  tags,
  category
);

CREATE TABLE IF NOT EXISTS citations (
  citation_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  chunk_id TEXT NOT NULL,
  quote TEXT,
  quote_hash TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id),
  FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id)
);

CREATE TABLE IF NOT EXISTS categories (
  category_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  parent_id TEXT,
  sort_order INTEGER,
  is_active INTEGER,
  is_system INTEGER,
  include_rules TEXT,
  exclude_rules TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  FOREIGN KEY(parent_id) REFERENCES categories(category_id)
);

CREATE TABLE IF NOT EXISTS tags (
  tag_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  description TEXT,
  language TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  UNIQUE(normalized_name)
);

CREATE TABLE IF NOT EXISTS tag_aliases (
  alias_id TEXT PRIMARY KEY,
  tag_id TEXT NOT NULL,
  alias TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  language TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  UNIQUE(normalized_alias),
  FOREIGN KEY(tag_id) REFERENCES tags(tag_id)
);

CREATE TABLE IF NOT EXISTS document_tags (
  doc_id TEXT NOT NULL,
  tag_id TEXT NOT NULL,
  source TEXT,
  confidence REAL,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  PRIMARY KEY (doc_id, tag_id),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(tag_id) REFERENCES tags(tag_id)
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  status TEXT NOT NULL,
  input_json TEXT,
  result_json TEXT,
  error_json TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  progress_current INTEGER,
  progress_total INTEGER,
  created_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status
  ON tasks(status);

CREATE TABLE IF NOT EXISTS task_events (
  event_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  event_type TEXT,
  message TEXT,
  payload_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_task_events_task_id
  ON task_events(task_id);

CREATE TABLE IF NOT EXISTS errors (
  error_id TEXT PRIMARY KEY,
  task_id TEXT,
  trace_id TEXT,
  component TEXT,
  error_type TEXT,
  severity TEXT,
  retryable INTEGER,
  user_message TEXT,
  developer_message TEXT,
  message TEXT,
  stack TEXT,
  payload_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_errors_task_id
  ON errors(task_id);

CREATE TABLE IF NOT EXISTS review_items (
  review_id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  priority INTEGER,
  reason TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_review_items_status
  ON review_items(status);

CREATE TABLE IF NOT EXISTS search_queries (
  query_id TEXT PRIMARY KEY,
  query_text TEXT NOT NULL,
  mode TEXT NOT NULL,
  filters_json TEXT,
  top_k INTEGER,
  result_count INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_results (
  query_id TEXT NOT NULL,
  rank INTEGER NOT NULL,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  chunk_id TEXT NOT NULL,
  snippet TEXT,
  score REAL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (query_id, rank),
  FOREIGN KEY(query_id) REFERENCES search_queries(query_id),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id),
  FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id)
);

CREATE TABLE IF NOT EXISTS duplicate_candidates (
  duplicate_id TEXT PRIMARY KEY,
  doc_id_a TEXT,
  doc_id_b TEXT,
  existing_doc_id TEXT,
  source_uri TEXT,
  reason TEXT,
  confidence REAL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT
);
