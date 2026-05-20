# M10 Readiness Checkpoint

Status: complete

Date: 2026-05-14

Precondition: [M9.3 Translation Hardening Checkpoint](m9-translation-hardening-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

M10.1 Candidate Extraction can start after this checkpoint.

This checkpoint does not implement candidate cards. It verifies that generated artifact lifecycle checks, source binding preconditions, review queue visibility, and default search boundaries are strong enough to start claim-level candidate extraction.

## Behavior

The M10 readiness gate is:

```powershell
.venv\Scripts\python scripts/m10_candidate_card_preflight_gate.py
```

It verifies:

- duplicate translation outputs do not overwrite each other
- duplicate translation outputs use unique output paths
- multiple target languages create distinct translation records
- selected-chunk and full-document translation outputs coexist
- partial translation failure creates no successful translation record
- missing translation output is detected by doctor as generated artifact drift
- orphan translation source bindings are detected by doctor
- active current documents and current chunks exist for candidate extraction
- archived documents do not appear in default search
- source shells are not searchable
- old revisions do not appear in default search
- translation workflows do not mutate source revisions, chunks, or Markdown
- review queue records are visible before card review work begins

## M10.1 Boundary

M10.1 should implement candidate extraction only:

- create candidate card records
- create candidate card source bindings
- require claim-level source chunks
- keep candidate cards bound to source revision and chunks
- reject source shells, archived documents, old revisions, and no-chunk current revisions
- write no `notes/atomic/` files
- provide no card accept/reject workflow yet

M10.2 may add review actions and accepted note writing after M10.1 source binding passes.

## Verification

Latest verification commands:

```powershell
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts/m93_translation_hardening_gate.py
.venv\Scripts\python scripts/m10_candidate_card_preflight_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

Latest result:

```text
compileall passed
173 tests collected
pytest passed
```

Latest M10 readiness gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m10-candidate-card-preflight-gate-20260514093500799818
M10_CANDIDATE_CARD_PREFLIGHT_GATE=passed
```

Hard metrics:

```json
{
  "duplicate_translation_overwrites": 0,
  "duplicate_translation_output_collisions": 0,
  "multiple_target_output_collisions": 0,
  "selected_full_output_collisions": 0,
  "partial_translation_success_records": 0,
  "partial_translation_output_files": 0,
  "partial_failed_errors_missing": 0,
  "missing_translation_output_source_corruption": 0,
  "orphan_translation_records_undetected": 0,
  "archived_docs_in_default_search": 0,
  "source_shells_searchable": 0,
  "old_revision_default_results": 0,
  "translations_mutating_source": 0
}
```

Positive proof metrics:

```json
{
  "multiple_target_language_records": 3,
  "selected_and_full_outputs_coexist": 1,
  "missing_translation_output_detected": 1,
  "orphan_translation_records_detected": 4,
  "active_current_documents": 2,
  "current_chunks": 2,
  "review_queue_works": 1,
  "candidate_preflight_passed": 1
}
```

Latest v0.1 RC aggregate after M10 readiness:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\v01-release-candidate-gate-20260514093512862935
V01_RELEASE_CANDIDATE_GATE=passed
```

RC aggregate:

```json
{
  "critical_doctor_findings": 0,
  "fts_desync": 0,
  "index_integrity_errors": 0,
  "orphan_files": 0,
  "orphan_records": 0,
  "unexpected_errors": 0,
  "unsupported_documents_created": 0,
  "zero_chunk_current_revisions": 0
}
```

## Boundary

This checkpoint does not include:

- candidate card schema or records
- candidate extraction
- card review UI
- accept/reject workflow
- accepted atomic note writing
- model-backed extraction
- answer generation
- `indb ask`
