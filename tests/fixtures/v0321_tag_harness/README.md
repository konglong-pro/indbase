# v0.3.2.1 Tag Harness Fixtures

Synthetic and sanitized cases for the deterministic tag harness. Each row in `cases.jsonl` is one explicit expected-outcome case.

Rules:

- Expected outcomes are explicit; the harness does not infer behavior from `case_id` or prose.
- `source` must be `synthetic` or `feedback_derived_sanitized`.
- Do not commit raw production vault exports or unsanitized feedback dumps.
