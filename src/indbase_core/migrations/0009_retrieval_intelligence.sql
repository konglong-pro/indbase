-- v0.3.2 Retrieval Intelligence Foundation: auditable retrieval packages.

CREATE TABLE IF NOT EXISTS retrieval_runs (
  retrieval_run_id TEXT PRIMARY KEY,
  query_text TEXT NOT NULL,
  normalized_query_text TEXT NOT NULL,
  planner_version TEXT NOT NULL,
  base_mode TEXT NOT NULL CHECK (base_mode IN ('fts', 'vector', 'hybrid')),
  linked_search_query_id TEXT,
  filters_json TEXT NOT NULL,
  planner_json TEXT NOT NULL,
  warnings_json TEXT NOT NULL,
  top_k INTEGER NOT NULL,
  candidate_k INTEGER NOT NULL,
  per_doc_limit INTEGER NOT NULL,
  result_count INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'partial')),
  created_at TEXT NOT NULL,
  finished_at TEXT,
  FOREIGN KEY(linked_search_query_id) REFERENCES search_queries(query_id)
);

CREATE INDEX IF NOT EXISTS idx_retrieval_runs_created_at
  ON retrieval_runs(created_at);

CREATE TABLE IF NOT EXISTS retrieval_items (
  retrieval_item_id TEXT PRIMARY KEY,
  retrieval_run_id TEXT NOT NULL,
  rank INTEGER NOT NULL,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  chunk_id TEXT NOT NULL,
  title TEXT,
  source_path TEXT,
  quote TEXT NOT NULL,
  snippet TEXT NOT NULL,
  base_score REAL NOT NULL,
  taxonomy_score REAL NOT NULL,
  final_score REAL NOT NULL,
  match_source TEXT NOT NULL,
  reasons_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(retrieval_run_id) REFERENCES retrieval_runs(retrieval_run_id),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id),
  FOREIGN KEY(chunk_id) REFERENCES chunks(chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_retrieval_items_run_rank
  ON retrieval_items(retrieval_run_id, rank);
