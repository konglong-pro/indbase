# M6.1 PDF Text Ingest Checkpoint

Status: complete

Date: 2026-05-13

Precondition: [v0.2 Entry Plan](v0.2-entry-plan.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

M6.1 is complete for PDF text ingest v1.

PDF is now a Tier 2 best-effort source type. The implementation uses MarkItDown for text extraction and does not perform OCR. Image-only PDFs, broken PDFs, unavailable conversion, and empty conversion output must fail visibly and must not create searchable zero-chunk current revisions.

## Behavior

Successful PDF text ingest:

- preserves the original PDF
- writes `source_files`
- writes a `converter_runs` row with `converter_name = markitdown`
- writes source Markdown
- creates an immutable revision
- chunks current content
- indexes current chunks in SQLite FTS
- returns source snippets through `indb search`

Failed PDF text ingest:

- preserves the original PDF
- records `converter_runs.status = failed`
- records an error
- creates a `conversion_low_quality` review item
- does not create `current_revision_id`
- leaves `fts_status = not_indexed`
- does not create chunks
- does not write FTS rows

M6.3 defines this failed state as a non-searchable source shell. See [M6.3 PDF/OCR Hardening Checkpoint](m6-pdf-ocr-hardening-checkpoint.md).

Still not included:

- OCR fallback
- image-only PDF extraction
- page-level OCR text
- PDF layout reconstruction
- embeddings or hybrid search
- `indb ask`

## Verification

Automated checks:

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python scripts/m3_dogfood_gate.py
.venv\Scripts\python scripts/m4_tui_lite_gate.py
.venv\Scripts\python scripts/m5_catalog_review_gate.py
.venv\Scripts\python scripts/doctor_negative_gate.py
.venv\Scripts\python scripts/metadata_consistency_gate.py
.venv\Scripts\python scripts/m6_pdf_ingest_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

Latest result:

```text
112 passed
compileall passed
```

Latest M6 PDF ingest gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m6-pdf-ingest-gate-20260513151115454916
M6_PDF_INGEST_GATE=passed
```

M6 gate summary:

```json
{
  "pdf_failure_chunks": 0,
  "pdf_failure_documents": 1,
  "pdf_failure_errors": 1,
  "pdf_failure_fts": 0,
  "pdf_failure_reviews": 1,
  "pdf_failure_revisions": 0,
  "pdf_success_chunks": 1,
  "pdf_success_documents": 1,
  "pdf_success_fts": 1,
  "pdf_success_revisions": 1,
  "pdf_success_search_results": 1,
  "unsupported_documents_created": 0,
  "unsupported_items": 1,
  "unsupported_reviews": 1,
  "zero_chunk_current_revisions": 0
}
```

Latest v0.1 RC aggregate after PDF support:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\v01-release-candidate-gate-20260513151251129594
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

M6.1 enables PDF text ingest only. OCR remains a separate M6.2 checkpoint.
