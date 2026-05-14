# M4 Practical TUI Lite Checkpoint

Status: complete

Date: 2026-05-13

Canonical scope: [mvp-v0.1-spec.md](mvp-v0.1-spec.md)

## Decision

M4 is complete for the automated v0.1 Practical TUI-lite checkpoint.

This means v0.1 now has a guided CLI surface over the Foundation data chain:

- `indb tui --action dashboard`
- `indb tui --action init`
- `indb tui --action ingest` / `ingest-wizard`
- `indb tui --action search` / `search-panel`
- `indb tui --action tasks` / `task-queue`
- `indb tui --action review` / `review-list`
- `indb tui --action errors` / `error-viewer`
- `indb tui --action settings` / `settings-summary`
- `indb tag list/add`
- `indb doc set-category`
- `indb doc add-tag/remove-tag/tags`
- `indb doc revisions`

The TUI-lite implementation stays within the v0.1 boundary: Typer, Rich, and InquirerPy guided CLI. It does not introduce Textual, background queue backends, OCR, embeddings, ask, translation, candidate cards, cloud sync, or physical delete.

## Verification

Automated checks:

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python scripts\m3_dogfood_gate.py
.venv\Scripts\python scripts\m4_tui_lite_gate.py
```

Latest result:

```text
105 passed
compileall passed
```

Latest M3 dogfood gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m3-dogfood-gate-20260513101618049324
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
DOGFOOD_ROOT=E:\indbase\.tmp\m4-tui-lite-gate-20260513101618049288
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

The non-zero review/error counts are expected in the M4 gate corpus:

- `unsupported.pdf` creates an `unsupported_source` review item.
- `empty.txt` fails conversion as `no_extractable_content`, creates visible error/review state, and does not create a current revision, chunks, or FTS rows.
- `doctor --json` has no error or critical findings.

## Boundary

M4 completes v0.1's automated TUI-lite checkpoint. It does not start M5, v0.2, or any intelligence-layer work.

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
