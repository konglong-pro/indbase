ALTER TABLE classification_feedback
  ADD COLUMN suggestion_id TEXT;

ALTER TABLE classification_feedback
  ADD COLUMN revision_id TEXT;

ALTER TABLE classification_feedback
  ADD COLUMN action TEXT;

ALTER TABLE classification_feedback
  ADD COLUMN forced_category INTEGER;

CREATE INDEX IF NOT EXISTS idx_classification_feedback_suggestion_id
  ON classification_feedback(suggestion_id);
