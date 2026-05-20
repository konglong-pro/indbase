# M8.1 Classification Hardening Checkpoint

Status: complete

Date: 2026-05-13

Precondition: [M8 Classification Suggestions Checkpoint](m8-classification-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

M8.1 is complete for classification state-machine hardening before M9 translation.

The hardening patch keeps M8 local and deterministic. It does not add model-backed classification, automatic metadata overwrite, translation, candidate cards, answer generation, or `indb ask`.

## Behavior Locked

Revision binding:

- each suggestion binds to `doc_id` and `revision_id`
- pending suggestions for old revisions are marked `stale`
- default `classify list` shows only active current pending suggestions
- changed-content re-ingest requires a new suggestion for the new current revision

Duplicate control:

- repeated `classify suggest` for the same `doc_id`, `revision_id`, model, and prompt version does not create duplicate pending suggestions
- `--force` supersedes pending suggestions for the same current revision before creating a replacement

Archive behavior:

- archived documents are not scanned by `classify suggest`
- pending suggestions on documents archived after suggestion creation are hidden from the default list
- accepting an archived document suggestion is blocked by default

Terminal suggestion behavior:

- rejected suggestions cannot later be accepted
- accepted suggestions cannot be accepted again
- terminal actions do not create duplicate feedback records

Manual metadata behavior:

- accepting a suggestion without `--force-category` preserves a non-uncategorized manual category
- accepting with `--force-category` may overwrite the manual category and records the forced action
- accepting suggested tags reuses normalized existing tags and document tag links
- rejecting a suggestion does not refresh FTS with rejected category/tag metadata

OCR/source-shell behavior:

- source shells still cannot be classified
- a failed PDF shell that later gets a successful OCR current revision can be classified

Feedback audit:

- `classification_feedback` now records `suggestion_id`
- `classification_feedback` now records `revision_id`
- `classification_feedback` now records `action`
- `classification_feedback` now records `forced_category`

Confidence gate:

- `confidence >= threshold` creates a suggestion
- `confidence < threshold` does not create a suggestion

## Verification

Latest verification commands:

```powershell
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python -m pytest
.venv\Scripts\python scripts/m71_embedding_index_gate.py
.venv\Scripts\python scripts/m72_hybrid_search_gate.py
.venv\Scripts\python scripts/m8_classification_gate.py
.venv\Scripts\python scripts/m81_classification_hardening_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

Latest result:

```text
compileall passed
159 passed
```

Latest M8.1 gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m81-classification-hardening-gate-20260513211625052970
M81_CLASSIFICATION_HARDENING_GATE=passed
```

M8.1 hard metrics:

```json
{
  "stale_suggestions_after_reingest": 0,
  "duplicate_pending_suggestions": 0,
  "accepted_rejected_suggestions": 0,
  "rejected_accepted_suggestions": 0,
  "archived_doc_suggestions_default": 0,
  "archived_doc_accepts_default": 0,
  "source_shell_suggestions": 0,
  "tag_duplicates_created": 0,
  "below_threshold_suggestions_created": 0,
  "feedback_missing_revision_id": 0,
  "unexpected_metadata_mutations": 0
}
```

Positive proof metrics:

```json
{
  "stale_suggestions_marked": 1,
  "new_suggestions_after_reingest": 1,
  "ocr_success_classifiable": 1,
  "force_category_overwrites_recorded": 1,
  "threshold_equal_suggestions_created": 1,
  "reject_fts_pollution": 0
}
```

Latest v0.1 RC aggregate after M8.1:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\v01-release-candidate-gate-20260513211837839209
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

## M9 Entry

M9 translation may start after this checkpoint if the next task explicitly asks for translation.

M9 should start with selected-chunk translation, not full-document translation:

- selected chunks only
- `source_doc_id` required
- `source_revision_id` required
- `source_chunk_ids_json` required
- write outputs under `outputs/translations/`
- record `executions` and `translations`
- do not overwrite source Markdown
- do not generate answers or candidate cards

## Boundary

M8.1 does not include:

- model-backed classification
- automatic classification during ingest
- automatic category/tag overwrite
- translation
- answer generation
- `indb ask`
- candidate cards
