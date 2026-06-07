-- v0.3.1 taxonomy category foundation: profiles, localizations, auditable classification.

ALTER TABLE categories ADD COLUMN template_key TEXT;
ALTER TABLE categories ADD COLUMN classification_state TEXT NOT NULL DEFAULT 'classification_ready';
ALTER TABLE categories ADD COLUMN profile_version TEXT;
ALTER TABLE categories ADD COLUMN locale_preference TEXT;

CREATE TABLE IF NOT EXISTS category_profiles (
  category_profile_id TEXT PRIMARY KEY,
  category_id TEXT NOT NULL,
  profile_version TEXT NOT NULL,
  description TEXT NOT NULL,
  positive_cues_json TEXT NOT NULL,
  negative_cues_json TEXT NOT NULL,
  example_titles_json TEXT NOT NULL,
  example_quotes_json TEXT NOT NULL,
  aliases_json TEXT NOT NULL,
  classification_ready INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  FOREIGN KEY(category_id) REFERENCES categories(category_id),
  UNIQUE(category_id, profile_version)
);

CREATE INDEX IF NOT EXISTS idx_category_profiles_category_id
  ON category_profiles(category_id);

CREATE TABLE IF NOT EXISTS category_localizations (
  category_localization_id TEXT PRIMARY KEY,
  category_id TEXT NOT NULL,
  locale TEXT NOT NULL,
  label TEXT NOT NULL,
  description TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  UNIQUE(category_id, locale),
  FOREIGN KEY(category_id) REFERENCES categories(category_id)
);

CREATE INDEX IF NOT EXISTS idx_category_localizations_category_id
  ON category_localizations(category_id);

CREATE TABLE IF NOT EXISTS category_classification_runs (
  category_run_id TEXT PRIMARY KEY,
  trigger TEXT NOT NULL,
  classifier_version TEXT NOT NULL,
  harness_version TEXT,
  threshold REAL NOT NULL,
  margin_threshold REAL NOT NULL,
  profile_snapshot_json TEXT NOT NULL,
  scanned_documents INTEGER NOT NULL,
  confident_count INTEGER NOT NULL,
  suggestion_count INTEGER NOT NULL,
  abstained_count INTEGER NOT NULL,
  preserved_count INTEGER NOT NULL,
  error_count INTEGER NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_category_classification_runs_created_at
  ON category_classification_runs(created_at);

CREATE TABLE IF NOT EXISTS category_classification_results (
  category_result_id TEXT PRIMARY KEY,
  category_run_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  previous_category_id TEXT,
  proposed_category_id TEXT,
  final_category_id TEXT,
  outcome TEXT NOT NULL,
  confidence REAL NOT NULL,
  margin REAL NOT NULL,
  evidence_json TEXT NOT NULL,
  warnings_json TEXT NOT NULL,
  error_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(category_run_id) REFERENCES category_classification_runs(category_run_id),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_category_classification_results_run
  ON category_classification_results(category_run_id);

CREATE INDEX IF NOT EXISTS idx_category_classification_results_doc
  ON category_classification_results(doc_id);

CREATE TABLE IF NOT EXISTS category_suggestions (
  category_suggestion_id TEXT PRIMARY KEY,
  category_result_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  suggested_category_id TEXT,
  alternative_category_ids_json TEXT NOT NULL,
  confidence REAL NOT NULL,
  margin REAL NOT NULL,
  reason TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  FOREIGN KEY(category_result_id) REFERENCES category_classification_results(category_result_id),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_category_suggestions_doc
  ON category_suggestions(doc_id);

CREATE INDEX IF NOT EXISTS idx_category_suggestions_status
  ON category_suggestions(status);

CREATE TABLE IF NOT EXISTS category_feedback (
  category_feedback_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  category_result_id TEXT,
  category_suggestion_id TEXT,
  previous_category_id TEXT,
  corrected_category_id TEXT,
  action TEXT NOT NULL,
  reason TEXT,
  evidence_json TEXT,
  profile_version TEXT,
  classifier_version TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_category_feedback_doc
  ON category_feedback(doc_id);
