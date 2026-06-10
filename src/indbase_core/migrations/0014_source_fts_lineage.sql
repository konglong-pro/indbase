CREATE TABLE IF NOT EXISTS index_builds (
  index_build_id TEXT PRIMARY KEY,
  index_kind TEXT NOT NULL,
  scope TEXT NOT NULL,
  doc_id TEXT,
  trigger TEXT,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  metadata_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS index_build_entries (
  index_build_entry_id TEXT PRIMARY KEY,
  index_build_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  chunk_id TEXT,
  status TEXT NOT NULL,
  reason TEXT,
  indexed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  FOREIGN KEY(index_build_id) REFERENCES index_builds(index_build_id),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id),
  FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_index_builds_kind_status
  ON index_builds(index_kind, status, created_at);

CREATE INDEX IF NOT EXISTS idx_index_builds_doc
  ON index_builds(doc_id, index_kind, created_at);

CREATE INDEX IF NOT EXISTS idx_index_build_entries_build
  ON index_build_entries(index_build_id);

CREATE INDEX IF NOT EXISTS idx_index_build_entries_chunk
  ON index_build_entries(chunk_id, index_build_id);

CREATE INDEX IF NOT EXISTS idx_index_build_entries_doc_revision
  ON index_build_entries(doc_id, revision_id, status);
