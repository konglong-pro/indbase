# M5 Catalog Review Checkpoint

Status: complete

Date: 2026-05-13

Canonical scope: [mvp-v0.1-spec.md](mvp-v0.1-spec.md)

## Decision

M5 is complete for the v0.1 catalog, review, and archive/restore polish checkpoint.

This means v0.1 now has the durable metadata operations needed before broader dogfood:

- manual category update/archive/restore without mutating revisions
- manual tag update/archive/restore without mutating revisions
- document category assignment
- document tag add/remove/list
- document listing by `active`, `archived`, or `all`
- document listing filters for category and active tag
- review list filtering by type and target type
- review resolve and resolve-many with resolver metadata and notes
- archive/restore visibility through default search and document lists

M5 stays inside the v0.1 boundary. It does not add Textual, OCR, PDF ingest, embeddings, ask, translation, candidate cards, cloud sync, physical delete, or a background queue backend.

## Verification

Automated checks:

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python scripts\m3_dogfood_gate.py
.venv\Scripts\python scripts\m4_tui_lite_gate.py
.venv\Scripts\python scripts\m5_catalog_review_gate.py
```

Latest result:

```text
109 passed
compileall passed
```

Latest M3 dogfood gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m3-dogfood-gate-20260513124515909522
INDEX_REBUILD_EXIT=0
DOCTOR_EXIT=1
```

M3 dogfood summary:

```json
{
  "citations": 0,
  "critical_doctor_findings": 0,
  "current_chunks": 19,
  "documents": 20,
  "duplicate_items": 1,
  "errors": 1,
  "expected_failures": 1,
  "expected_reviews": 2,
  "failed_items": 1,
  "fts": 19,
  "index_integrity_errors": 0,
  "reviews": 3,
  "revisions": 20,
  "search_queries": 8,
  "tasks": 4,
  "unexpected_errors": 0,
  "unsupported_items": 2
}
```

Latest M4 TUI-lite gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m4-tui-lite-gate-20260513124515890782
M4_TUI_LITE_GATE=passed
```

M4 gate summary:

```json
{
  "critical_doctor_findings": 0,
  "current_chunks": 2,
  "documents": 3,
  "errors": 1,
  "fts": 2,
  "manual_tags": 1,
  "pending_reviews": 2,
  "tasks": 2
}
```

Latest M5 catalog/review gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m5-catalog-review-gate-20260513124515880477
M5_CATALOG_REVIEW_GATE=passed
```

M5 gate summary:

```json
{
  "active_documents": 3,
  "archived_documents": 0,
  "critical_doctor_findings": 0,
  "current_chunks": 2,
  "documents": 3,
  "errors": 1,
  "fts": 2,
  "manual_tags": 1,
  "pending_reviews": 0,
  "resolved_reviews": 2
}
```

The M5 gate corpus intentionally includes `empty.txt` and `unsupported.pdf`. `empty.txt` creates visible `no_extractable_content` error/review state without creating a searchable zero-chunk current revision. `unsupported.pdf` creates an `unsupported_source` review item without creating a document. The gate resolves expected review items and confirms `doctor --json` has no error or critical findings.

## Boundary

M5 completes the v0.1 metadata and review polish checkpoint. It does not start v0.2 or the intelligence-layer work.

Still not included:

- Textual full TUI
- OCR or PDF ingest
- embeddings or hybrid search
- `indb ask`
- translation
- candidate cards
- background queue backend
- cloud sync
- physical delete
