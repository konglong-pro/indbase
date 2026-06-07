-- v0.3.3 Retrieval Evaluation / Answer Readiness.

CREATE TABLE IF NOT EXISTS retrieval_eval_cases (
  eval_case_id TEXT PRIMARY KEY,
  suite TEXT NOT NULL,
  name TEXT NOT NULL,
  query_text TEXT NOT NULL,
  options_json TEXT NOT NULL,
  expectations_json TEXT NOT NULL,
  notes TEXT,
  status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
  source TEXT NOT NULL CHECK (source IN ('fixture', 'dogfood', 'manual')),
  created_at TEXT NOT NULL,
  updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_retrieval_eval_cases_suite_status
  ON retrieval_eval_cases(suite, status, name);

CREATE TABLE IF NOT EXISTS retrieval_eval_runs (
  eval_run_id TEXT PRIMARY KEY,
  suite TEXT NOT NULL,
  evaluator_version TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  case_count INTEGER NOT NULL,
  passed_count INTEGER NOT NULL,
  failed_count INTEGER NOT NULL,
  error_count INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'partial')),
  error_json TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_retrieval_eval_runs_created_at
  ON retrieval_eval_runs(created_at);

CREATE TABLE IF NOT EXISTS answer_readiness_reports (
  readiness_report_id TEXT PRIMARY KEY,
  retrieval_run_id TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('ready', 'needs_more_evidence', 'not_ready')),
  score REAL NOT NULL,
  blockers_json TEXT NOT NULL,
  warnings_json TEXT NOT NULL,
  metrics_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(retrieval_run_id) REFERENCES retrieval_runs(retrieval_run_id)
);

CREATE INDEX IF NOT EXISTS idx_answer_readiness_reports_run
  ON answer_readiness_reports(retrieval_run_id, created_at);

CREATE TABLE IF NOT EXISTS retrieval_eval_results (
  eval_result_id TEXT PRIMARY KEY,
  eval_run_id TEXT NOT NULL,
  eval_case_id TEXT NOT NULL,
  retrieval_run_id TEXT,
  readiness_report_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('passed', 'failed', 'error')),
  metrics_json TEXT NOT NULL,
  failures_json TEXT NOT NULL,
  warnings_json TEXT NOT NULL,
  error_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(eval_run_id) REFERENCES retrieval_eval_runs(eval_run_id),
  FOREIGN KEY(eval_case_id) REFERENCES retrieval_eval_cases(eval_case_id),
  FOREIGN KEY(retrieval_run_id) REFERENCES retrieval_runs(retrieval_run_id)
);

CREATE INDEX IF NOT EXISTS idx_retrieval_eval_results_run
  ON retrieval_eval_results(eval_run_id, eval_case_id);
