-- v0.3.2 Tag Governance Foundation: lifecycle fields, tagger runs, feedback, blocklist, audit.

ALTER TABLE tags ADD COLUMN scope TEXT NOT NULL DEFAULT 'global';
ALTER TABLE tags ADD COLUMN scope_category_id TEXT;
ALTER TABLE tags ADD COLUMN merged_into_tag_id TEXT;
ALTER TABLE tags ADD COLUMN policy_warning_json TEXT;

ALTER TABLE tag_aliases ADD COLUMN locale TEXT;
ALTER TABLE tag_aliases ADD COLUMN source TEXT;
ALTER TABLE tag_aliases ADD COLUMN created_by TEXT;

ALTER TABLE document_tags ADD COLUMN candidate_id TEXT;

ALTER TABLE tag_candidates ADD COLUMN candidate_type TEXT;
ALTER TABLE tag_candidates ADD COLUMN raw_name TEXT;
ALTER TABLE tag_candidates ADD COLUMN target_tag_id TEXT;
ALTER TABLE tag_candidates ADD COLUMN proposed_name TEXT;
ALTER TABLE tag_candidates ADD COLUMN doc_id TEXT;
ALTER TABLE tag_candidates ADD COLUMN revision_id TEXT;
ALTER TABLE tag_candidates ADD COLUMN tagger_run_id TEXT;
ALTER TABLE tag_candidates ADD COLUMN tagger_result_id TEXT;
ALTER TABLE tag_candidates ADD COLUMN resolution_status TEXT;
ALTER TABLE tag_candidates ADD COLUMN admission_status TEXT;
ALTER TABLE tag_candidates ADD COLUMN budget_status TEXT;
ALTER TABLE tag_candidates ADD COLUMN policy_decision_json TEXT;
ALTER TABLE tag_candidates ADD COLUMN budget_decision_json TEXT;
ALTER TABLE tag_candidates ADD COLUMN review_item_id TEXT;

UPDATE tag_candidates
SET raw_name = name
WHERE raw_name IS NULL;

CREATE TABLE IF NOT EXISTS tagger_runs (
  tagger_run_id TEXT PRIMARY KEY,
  trigger TEXT NOT NULL CHECK (
    trigger IN ('manual', 'post_ingest', 'feedback_eval', 'fixture_gate', 'propagation_review')
  ),
  tagger_version TEXT NOT NULL,
  harness_version TEXT,
  policy_version TEXT NOT NULL,
  auto_attach_threshold REAL NOT NULL,
  per_doc_auto_attach_limit INTEGER NOT NULL,
  per_doc_candidate_limit INTEGER NOT NULL,
  per_run_new_tag_proposal_limit INTEGER NOT NULL,
  per_run_total_candidate_limit INTEGER NOT NULL,
  scanned_documents INTEGER NOT NULL,
  auto_attached_count INTEGER NOT NULL,
  candidate_count INTEGER NOT NULL,
  new_tag_proposal_count INTEGER NOT NULL,
  blocked_candidate_count INTEGER NOT NULL,
  preserved_manual_count INTEGER NOT NULL,
  error_count INTEGER NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tagger_runs_created_at
  ON tagger_runs(created_at);

CREATE TABLE IF NOT EXISTS tagger_results (
  tagger_result_id TEXT PRIMARY KEY,
  tagger_run_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (
    outcome IN (
      'auto_attached',
      'candidates_created',
      'no_candidates',
      'preserved_manual',
      'completed_with_warnings',
      'error'
    )
  ),
  auto_attached_tag_ids_json TEXT NOT NULL,
  candidate_ids_json TEXT NOT NULL,
  blocked_candidates_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  warnings_json TEXT NOT NULL,
  error_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(tagger_run_id) REFERENCES tagger_runs(tagger_run_id),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id)
);

CREATE INDEX IF NOT EXISTS idx_tagger_results_run
  ON tagger_results(tagger_run_id);

CREATE INDEX IF NOT EXISTS idx_tagger_results_doc_revision
  ON tagger_results(doc_id, revision_id);

CREATE TABLE IF NOT EXISTS tag_feedback (
  tag_feedback_id TEXT PRIMARY KEY,
  doc_id TEXT,
  revision_id TEXT,
  tag_id TEXT,
  candidate_id TEXT,
  action TEXT NOT NULL CHECK (
    action IN (
      'accepted',
      'rejected',
      'manual_created',
      'manual_removed',
      'merged',
      'deprecated',
      'alias_added',
      'alias_removed',
      'blocked',
      'unblocked',
      'policy_suggestion_created',
      'fixture_candidate_created'
    )
  ),
  reason TEXT,
  old_json TEXT,
  new_json TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id),
  FOREIGN KEY(tag_id) REFERENCES tags(tag_id),
  FOREIGN KEY(candidate_id) REFERENCES tag_candidates(candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_tag_feedback_candidate
  ON tag_feedback(candidate_id);

CREATE INDEX IF NOT EXISTS idx_tag_feedback_doc
  ON tag_feedback(doc_id);

CREATE TABLE IF NOT EXISTS tag_blocklist (
  blocked_id TEXT PRIMARY KEY,
  pattern TEXT NOT NULL,
  normalized_pattern TEXT NOT NULL,
  match_type TEXT NOT NULL CHECK (match_type IN ('exact', 'contains')),
  reason TEXT,
  source TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tag_blocklist_normalized_pattern
  ON tag_blocklist(normalized_pattern);

CREATE TABLE IF NOT EXISTS tag_governance_events (
  event_id TEXT PRIMARY KEY,
  tag_id TEXT,
  candidate_id TEXT,
  doc_id TEXT,
  event_type TEXT NOT NULL CHECK (
    event_type IN (
      'created',
      'promoted',
      'alias_added',
      'alias_removed',
      'merged',
      'deprecated',
      'archived',
      'restored',
      'scope_changed',
      'blocked',
      'unblocked',
      'policy_suggestion_created',
      'link_migration_started',
      'link_migration_finished'
    )
  ),
  payload_json TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(tag_id) REFERENCES tags(tag_id),
  FOREIGN KEY(candidate_id) REFERENCES tag_candidates(candidate_id),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id)
);

CREATE INDEX IF NOT EXISTS idx_tag_governance_events_tag
  ON tag_governance_events(tag_id);

CREATE INDEX IF NOT EXISTS idx_tag_governance_events_candidate
  ON tag_governance_events(candidate_id);

CREATE INDEX IF NOT EXISTS idx_tag_candidates_doc_revision
  ON tag_candidates(doc_id, revision_id);

CREATE INDEX IF NOT EXISTS idx_tag_candidates_tagger_run
  ON tag_candidates(tagger_run_id);
