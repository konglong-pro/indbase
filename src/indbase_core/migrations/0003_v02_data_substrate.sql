CREATE TABLE IF NOT EXISTS ocr_pages (
  ocr_page_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  page_number INTEGER NOT NULL,
  text TEXT,
  confidence REAL,
  quality_status TEXT,
  quality_signals_json TEXT,
  needs_review INTEGER,
  engine TEXT,
  engine_version TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  UNIQUE(doc_id, revision_id, page_number),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_ocr_pages_doc_revision
  ON ocr_pages(doc_id, revision_id);

CREATE TABLE IF NOT EXISTS embeddings (
  embedding_id TEXT PRIMARY KEY,
  chunk_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  provider TEXT,
  model TEXT,
  dimension INTEGER,
  vector_ref TEXT,
  content_hash TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_chunk_id
  ON embeddings(chunk_id);

CREATE INDEX IF NOT EXISTS idx_embeddings_doc_revision
  ON embeddings(doc_id, revision_id);

CREATE TABLE IF NOT EXISTS classification_suggestions (
  suggestion_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  suggested_category_id TEXT,
  confidence REAL,
  reason TEXT,
  alternative_category_ids_json TEXT,
  suggested_tags_json TEXT,
  needs_user_confirmation INTEGER,
  model TEXT,
  prompt_version TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id),
  FOREIGN KEY(suggested_category_id) REFERENCES categories(category_id)
);

CREATE INDEX IF NOT EXISTS idx_classification_suggestions_doc_revision
  ON classification_suggestions(doc_id, revision_id);

CREATE TABLE IF NOT EXISTS classification_feedback (
  feedback_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  old_category_id TEXT,
  new_category_id TEXT,
  old_tags_json TEXT,
  new_tags_json TEXT,
  reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(old_category_id) REFERENCES categories(category_id),
  FOREIGN KEY(new_category_id) REFERENCES categories(category_id)
);

CREATE INDEX IF NOT EXISTS idx_classification_feedback_doc_id
  ON classification_feedback(doc_id);

CREATE TABLE IF NOT EXISTS executions (
  execution_id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  source_doc_id TEXT,
  source_revision_id TEXT,
  input_json TEXT,
  output_path TEXT,
  output_json TEXT,
  model TEXT,
  prompt_version TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  finished_at TEXT,
  deleted_at TEXT,
  FOREIGN KEY(source_doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(source_revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_executions_source_doc_id
  ON executions(source_doc_id);

CREATE TABLE IF NOT EXISTS translations (
  translation_id TEXT PRIMARY KEY,
  execution_id TEXT,
  source_doc_id TEXT NOT NULL,
  source_revision_id TEXT NOT NULL,
  source_language TEXT,
  target_language TEXT,
  translation_mode TEXT,
  source_chunk_ids_json TEXT,
  output_path TEXT,
  glossary_id TEXT,
  model TEXT,
  prompt_version TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  FOREIGN KEY(execution_id) REFERENCES executions(execution_id),
  FOREIGN KEY(source_doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(source_revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_translations_source_doc_revision
  ON translations(source_doc_id, source_revision_id);
