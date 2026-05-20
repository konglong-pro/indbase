# M6.2 OCR v0 Checkpoint

Status: complete

Date: 2026-05-13

Precondition: [M6.1 PDF Text Ingest Checkpoint](m6-pdf-ingest-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

M6.2 is complete for explicit OCR v0.

OCR is available as an explicit workflow through `indb ocr run <doc_id>`. It is not an automatic fallback during PDF ingest. The current adapter is a local sidecar OCR adapter: it reads OCR text placed next to the archived original as either `original.<ext>.ocr.txt` or `original.<ext>.ocr.json`.

Tesseract availability is reported by `indb doctor`, but page rendering plus direct Tesseract execution is not enabled in this checkpoint.

M6.3 hardens the OCR state rules: OCR defaults to refusing documents that already have a current revision, and `--force` is required to create a replacement OCR revision. See [M6.3 PDF/OCR Hardening Checkpoint](m6-pdf-ocr-hardening-checkpoint.md).

## Behavior

Successful OCR:

- reads sidecar OCR text for the archived original
- writes an OCR source Markdown revision
- writes `converter_runs.converter_name = ocr_sidecar`
- writes `ocr_pages`
- chunks OCR text
- indexes current chunks in SQLite FTS
- returns OCR snippets through `indb search`
- records task and task event state

OCR sidecar formats:

- `.ocr.txt`: form-feed (`\f`) separates pages
- `.ocr.json`: list of page objects with `page_number`, `text`, and optional `confidence`

Low-confidence OCR:

- keeps OCR content searchable
- marks affected OCR pages as needing review
- creates `review_items.type = ocr_low_quality`
- marks document quality as warning

Failed OCR:

- records `errors.component = ocr`
- creates an `ocr_low_quality` review item
- does not create a current revision
- does not create chunks
- does not write FTS rows

Doctor behavior:

- reports OCR engine availability as an info finding
- allows `.ocr.txt` and `.ocr.json` sidecars next to referenced archived originals
- still reports unrelated orphan original files as errors

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
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

Latest result:

```text
117 passed
compileall passed
```

Latest M6.2 OCR gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m62-ocr-gate-20260513153045401880
M62_OCR_GATE=passed
```

M6.2 gate summary:

```json
{
  "doctor_ocr_availability_findings": 1,
  "ocr_failure_chunks": 0,
  "ocr_failure_errors": 1,
  "ocr_failure_fts": 0,
  "ocr_failure_reviews": 1,
  "ocr_failure_revisions": 0,
  "ocr_low_confidence_pages_needing_review": 1,
  "ocr_low_confidence_review_items": 1,
  "ocr_success_chunks": 2,
  "ocr_success_fts": 2,
  "ocr_success_pages": 2,
  "ocr_success_revisions": 1,
  "ocr_success_search_results": 1,
  "zero_chunk_current_revisions": 0
}
```

Latest v0.1 RC aggregate after OCR support:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\v01-release-candidate-gate-20260513153045109314
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

M6.2 enables explicit OCR v0 only.

Still not included:

- automatic PDF OCR fallback
- direct Tesseract page rendering
- image ingest as a supported OCR pipeline
- PDF layout reconstruction
- embeddings or hybrid search
- `indb ask`
- translation
- candidate cards
