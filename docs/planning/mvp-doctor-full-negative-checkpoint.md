# MVP Doctor Full Negative Checkpoint

Status: complete

Date: 2026-05-14

Precondition: [MVP Release Gate Checkpoint](mvp-release-gate-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](mvp-v0.1-spec.md)

## Decision

The doctor full negative gate is complete for the current MVP artifact surface.

This gate deliberately corrupts temporary vaults and verifies that `indb doctor` can identify source corruption, index corruption, generated-artifact drift, source-binding breakage, schema mismatch, and missing config. It does not mutate user vaults and does not add new product workflow.

## Gate

Run:

```powershell
.venv\Scripts\python scripts\mvp_doctor_full_negative_gate.py
```

The gate covers:

- missing original file
- missing source Markdown
- invalid `documents.current_revision_id`
- current revision with missing chunks
- cleared FTS
- missing vector index record after `embedding_status = indexed`
- orphan embedding
- source shell incorrectly marked searchable
- missing translation output
- orphan translation record
- missing accepted atomic note
- orphan atomic note
- candidate card source chunk missing
- accepted card without sources
- uncited accepted claim
- schema version mismatch
- missing config
- stale category/tag FTS metadata

## Doctor Invariants Added

This checkpoint adds two doctor checks that were not previously covered:

- `missing_vector_index_record`: an active current document marked `embedding_status = indexed` must have indexed embeddings for its current chunks.
- `fts_metadata_stale`: active current FTS rows must match the current DB title, category, and tags.

## Latest Result

```text
DOGFOOD_ROOT=E:\indbase\.tmp\mvp-doctor-full-negative-gate-20260514114639713526
MVP_DOCTOR_FULL_NEGATIVE_GATE=passed
```

Latest summary:

```json
{
  "category_tag_fts_stale_undetected": 0,
  "config_schema_corruption_detected": 2,
  "generated_artifact_corruption_detected": 11,
  "index_corruption_detected": 7,
  "missing_expected_findings": 0,
  "missing_generated_outputs_undetected": 0,
  "missing_vector_index_record_undetected": 0,
  "passed": 18,
  "scenarios": 18,
  "source_corruption_detected": 5,
  "weak_error_severity_findings": 0
}
```

## Boundary

This gate proves doctor can catch synthetic full-system corruption. The broader release close-out surface is covered by separate gates:

- [MVP Windows path and Unicode checkpoint](mvp-windows-path-unicode-checkpoint.md)
- [MVP performance smoke checkpoint](mvp-performance-smoke-checkpoint.md)
- [MVP real corpus dogfood checkpoint](mvp-real-corpus-dogfood-checkpoint.md)
- [MVP CLI/TUI acceptance checkpoint](mvp-cli-tui-acceptance-checkpoint.md)
- [MVP backup restore checkpoint](mvp-backup-restore-checkpoint.md)
