# M6.3 PDF/OCR Hardening Checkpoint

Status: complete

Date: 2026-05-13

Precondition:

- [M6.1 PDF Text Ingest Checkpoint](m6-pdf-ingest-checkpoint.md)
- [M6.2 OCR v0 Checkpoint](m6-ocr-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

M6.3 is complete for PDF/OCR state-machine hardening.

M6.1 and M6.2 had the main happy paths and failure paths working. M6.3 fixes the remaining boundary risk: PDF conversion failure can preserve a non-searchable document shell for later OCR, and OCR can create revisions only under explicit rules.

## Source Shell Invariant

A source shell is a document row created for a source whose original was archived but whose content has not produced a searchable source revision.

Allowed:

- `documents` row
- `source_files` row
- preserved archived original
- failed converter run
- visible error and review item

Required:

- `current_revision_id = null`
- `canonical_path = null`
- `ingest_status = failed`
- `fts_status = not_indexed`
- `quality_status = failed`
- `needs_review = true`
- no `document_revisions`
- no `chunks`
- no `chunks_fts`
- default search does not return it
- `doc show` exposes the failed state
- `doc open --original` works
- `doc open` without `--original` fails instead of pretending Markdown exists
- `index rebuild --fts` skips it without critical failure
- `doctor` treats it as review state, not corruption

This is intentionally different from unsupported sources. Unsupported sources still must not create document rows.

## OCR Rules

Default OCR:

- allowed for PDF source shells with no current revision
- blocked for documents that already have a current revision
- blocked for archived documents
- blocked for non-PDF documents in this checkpoint

Forced OCR:

- `indb ocr run <doc_id> --force` is required to create an OCR revision over an existing current revision
- successful forced OCR writes a new immutable revision and makes it current
- old revisions and old chunks remain preserved
- FTS default search returns only the current revision

OCR failure:

- records OCR error/review
- does not create a new revision
- does not create chunks or FTS rows
- does not clear or replace an existing `current_revision_id`
- does not delete old chunks or FTS rows
- keeps existing text-PDF search results working

OCR rerun:

- same OCR content does not create a new revision
- changed OCR content creates a new immutable revision

## Sidecar Rules

`.ocr.json`:

- must be a JSON list of page objects
- page objects may contain `page_number`, `text`, `confidence`, and extra ignored fields
- missing `page_number` is assigned from list order
- provided `page_number` must be an integer >= 1
- duplicate page numbers fail
- `confidence` must be a number from 0 to 1
- pages are stored sorted by page number

`.ocr.txt`:

- form-feed (`\f`) separates pages
- empty pages are skipped
- page numbers remain stable after skipped empty pages
- CRLF and UTF-8 BOM are normalized
- CJK OCR text remains searchable

## Doctor Hardening

Doctor now detects M6-specific corruption:

- source shell marked indexed
- source shell with canonical Markdown
- source shell with chunks
- source shell with FTS rows
- source shell missing review/error
- orphan OCR page rows
- OCR page revision/doc mismatch
- OCR current revision without OCR pages
- OCR converter success without a valid revision
- low-confidence OCR page without OCR review
- failed MarkItDown conversion without review/error

Valid OCR sidecars next to referenced archived originals are allowed. Invalid sidecars or unrelated files remain orphan findings.

## Verification

Automated checks:

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python scripts/m3_dogfood_gate.py
.venv\Scripts\python scripts/m4_tui_lite_gate.py
.venv\Scripts\python scripts/m5_catalog_review_gate.py
.venv\Scripts\python scripts/m6_pdf_ingest_gate.py
.venv\Scripts\python scripts/m62_ocr_gate.py
.venv\Scripts\python scripts/m63_pdf_ocr_hardening_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

Latest result:

```text
134 passed
compileall passed
```

Latest M6.3 hardening gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m63-pdf-ocr-hardening-gate-20260513160531966821
M63_PDF_OCR_HARDENING_GATE=passed
```

M6.3 gate summary:

```json
{
  "critical_doctor_findings": 0,
  "fts_desync": 0,
  "index_integrity_errors": 0,
  "negative_doctor_cases_detected": 6,
  "negative_doctor_cases_total": 6,
  "ocr_changed_cjk_search_results": 1,
  "ocr_changed_content_new_revision": 1,
  "ocr_default_blocked_existing_revision": 1,
  "ocr_failed_mutated_current_revisions": 0,
  "ocr_force_cjk_search_results": 1,
  "ocr_force_old_revision_current_chunks": 0,
  "ocr_force_search_results": 1,
  "ocr_force_success_new_revision": 1,
  "ocr_low_confidence_without_review": 0,
  "ocr_malformed_sidecars_failed": 6,
  "ocr_same_content_new_revisions": 0,
  "ocr_txt_page_numbers_stable": 1,
  "orphan_ocr_pages": 0,
  "pdf_failed_searchable_documents": 0,
  "pdf_shell_created": 1,
  "pdf_shell_doc_show_ok": 1,
  "pdf_shell_index_rebuild_failures": 0,
  "pdf_shell_ocr_cjk_search_results": 1,
  "pdf_shell_ocr_converter_runs": 2,
  "pdf_shell_ocr_pages": 1,
  "pdf_shell_ocr_search_results": 1,
  "pdf_shell_ocr_success": 1,
  "pdf_shell_open_markdown_blocked": 1,
  "pdf_shell_open_original_ok": 1,
  "unexpected_errors": 0,
  "zero_chunk_current_revisions": 0
}
```

Latest v0.1 RC aggregate after M6.3:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\v01-release-candidate-gate-20260513160530824669
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

M6.3 is a hardening checkpoint only.

Still not included:

- automatic PDF OCR fallback
- direct Tesseract page rendering
- image ingest as a supported OCR pipeline
- embeddings or hybrid search
- `indb ask`
- translation
- candidate cards
