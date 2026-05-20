ALTER TABLE converter_runs ADD COLUMN external_job_id TEXT;
ALTER TABLE converter_runs ADD COLUMN external_trace_path TEXT;
ALTER TABLE converter_runs ADD COLUMN external_manifest_path TEXT;
ALTER TABLE converter_runs ADD COLUMN primary_worker TEXT;
ALTER TABLE converter_runs ADD COLUMN worker_chain_json TEXT;
ALTER TABLE converter_runs ADD COLUMN candidate_path TEXT;
ALTER TABLE converter_runs ADD COLUMN artifact_manifest_json TEXT;
ALTER TABLE converter_runs ADD COLUMN promotion_status TEXT;
ALTER TABLE converter_runs ADD COLUMN promotion_reason TEXT;

ALTER TABLE chunks ADD COLUMN source_locator_json TEXT;

ALTER TABLE source_files ADD COLUMN access_context TEXT;
ALTER TABLE source_files ADD COLUMN privacy_flags_json TEXT;
ALTER TABLE source_files ADD COLUMN source_snapshot_path TEXT;

ALTER TABLE documents ADD COLUMN access_context TEXT;
ALTER TABLE documents ADD COLUMN privacy_flags_json TEXT;
ALTER TABLE documents ADD COLUMN source_snapshot_path TEXT;

ALTER TABLE ingest_items ADD COLUMN parent_ingest_item_id TEXT;
ALTER TABLE ingest_items ADD COLUMN logical_source_id TEXT;

CREATE INDEX IF NOT EXISTS idx_converter_runs_external_job_id
  ON converter_runs(external_job_id);

CREATE INDEX IF NOT EXISTS idx_ingest_items_parent
  ON ingest_items(parent_ingest_item_id);
