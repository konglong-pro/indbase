CREATE TABLE IF NOT EXISTS provider_runs (
  provider_run_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL,
  action_id TEXT,
  task_id TEXT,
  ingest_run_id TEXT,
  converter_run_id TEXT,
  output_run_id TEXT,
  provider_id TEXT NOT NULL,
  provider_package TEXT,
  provider_version TEXT NOT NULL,
  capability_id TEXT NOT NULL,
  capability_contract_version TEXT NOT NULL,
  transport_profile TEXT NOT NULL,
  provider_job_id TEXT,
  provider_status TEXT,
  evidence_status TEXT NOT NULL DEFAULT 'pending',
  started_at TEXT NOT NULL,
  finished_at TEXT,
  input_sha256 TEXT,
  manifest_artifact_ref_json TEXT,
  trace_artifact_ref_json TEXT,
  evidence_root TEXT NOT NULL,
  warning_count INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0,
  primary_error_code TEXT,
  provider_error_code TEXT,
  provider_error_json TEXT,
  metadata_json TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(ingest_run_id) REFERENCES ingest_runs(ingest_id),
  FOREIGN KEY(converter_run_id) REFERENCES converter_runs(converter_run_id),
  FOREIGN KEY(output_run_id) REFERENCES output_runs(output_run_id)
);

CREATE INDEX IF NOT EXISTS idx_provider_runs_operation_id
  ON provider_runs(operation_id);

CREATE INDEX IF NOT EXISTS idx_provider_runs_task_id
  ON provider_runs(task_id);

CREATE INDEX IF NOT EXISTS idx_provider_runs_ingest_run_id
  ON provider_runs(ingest_run_id);

CREATE INDEX IF NOT EXISTS idx_provider_runs_output_run_id
  ON provider_runs(output_run_id);

CREATE INDEX IF NOT EXISTS idx_provider_runs_provider_job_id
  ON provider_runs(provider_id, provider_job_id);

ALTER TABLE ingest_runs ADD COLUMN adopted_provider_run_id TEXT;

ALTER TABLE converter_runs ADD COLUMN adopted_provider_run_id TEXT;

ALTER TABLE output_runs ADD COLUMN adopted_provider_run_id TEXT;

ALTER TABLE output_artifacts ADD COLUMN artifact_role TEXT;

ALTER TABLE output_artifacts ADD COLUMN trust_level TEXT;

ALTER TABLE errors ADD COLUMN provider_run_id TEXT;

ALTER TABLE review_items ADD COLUMN ingest_run_id TEXT;

ALTER TABLE review_items ADD COLUMN provider_run_id TEXT;

CREATE INDEX IF NOT EXISTS idx_ingest_runs_adopted_provider_run_id
  ON ingest_runs(adopted_provider_run_id);

CREATE INDEX IF NOT EXISTS idx_converter_runs_adopted_provider_run_id
  ON converter_runs(adopted_provider_run_id);

CREATE INDEX IF NOT EXISTS idx_output_runs_adopted_provider_run_id
  ON output_runs(adopted_provider_run_id);

CREATE INDEX IF NOT EXISTS idx_errors_provider_run_id
  ON errors(provider_run_id);

CREATE INDEX IF NOT EXISTS idx_review_items_provider_run_id
  ON review_items(provider_run_id);
