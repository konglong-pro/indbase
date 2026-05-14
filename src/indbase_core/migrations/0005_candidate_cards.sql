CREATE TABLE IF NOT EXISTS candidate_cards (
  candidate_card_id TEXT PRIMARY KEY,
  source_doc_id TEXT NOT NULL,
  source_revision_id TEXT NOT NULL,
  title TEXT,
  claims_json TEXT NOT NULL,
  model TEXT,
  prompt_version TEXT,
  status TEXT NOT NULL,
  accepted_note_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  FOREIGN KEY(source_doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(source_revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_candidate_cards_source_doc_revision
  ON candidate_cards(source_doc_id, source_revision_id);

CREATE INDEX IF NOT EXISTS idx_candidate_cards_status
  ON candidate_cards(status);

CREATE TABLE IF NOT EXISTS candidate_card_sources (
  candidate_card_source_id TEXT PRIMARY KEY,
  candidate_card_id TEXT NOT NULL,
  source_doc_id TEXT NOT NULL,
  source_revision_id TEXT NOT NULL,
  source_chunk_id TEXT NOT NULL,
  claim_id TEXT,
  quote TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  deleted_at TEXT,
  FOREIGN KEY(candidate_card_id) REFERENCES candidate_cards(candidate_card_id),
  FOREIGN KEY(source_doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(source_revision_id) REFERENCES document_revisions(revision_id),
  FOREIGN KEY(source_chunk_id) REFERENCES chunks(chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_candidate_card_sources_card
  ON candidate_card_sources(candidate_card_id);

CREATE INDEX IF NOT EXISTS idx_candidate_card_sources_chunk
  ON candidate_card_sources(source_chunk_id);
