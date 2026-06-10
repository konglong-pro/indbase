ALTER TABLE provider_runs ADD COLUMN failure_class TEXT;

CREATE INDEX IF NOT EXISTS idx_provider_runs_failure_class
  ON provider_runs(failure_class);
