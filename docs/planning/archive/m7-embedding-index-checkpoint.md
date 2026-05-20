# M7.1 Embedding Index Checkpoint

Status: complete

Date: 2026-05-13

Precondition: [M7 Readiness Checklist](m7-readiness-checklist.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

M7.1 is complete for deterministic local embedding indexing.

This checkpoint adds vector indexing substrate only. It does not add hybrid search, `ask`, answer generation, classification suggestions, translation, or external model calls.

## Behavior

Embedding rebuild:

```powershell
indb index rebuild --vectors
```

Rules:

- embeds active current chunks only
- skips archived documents
- skips old revisions
- skips source shells and failed documents
- writes rows to `embeddings`
- stores deterministic local vectors in `embeddings.vector_ref`
- binds `embeddings.content_hash` to `chunks.content_hash`
- updates `documents.embedding_status`
- records a task for vector rebuild
- records errors/reviews for embedding failures
- does not mutate document revisions
- does not break FTS searchability

Embedding status values used:

- `indexing`
- `indexed`
- `failed`
- existing `not_applicable` remains for skipped/non-indexed documents
- `stale` is reserved and detected when stored embeddings no longer match current chunk content

Default adapter:

- provider: `local`
- model: `hash-v1`
- dimension: `8`

The adapter is deterministic and local. It exists to validate indexing contracts before any real embedding provider is introduced.

## Scope Rules

Default embedding scope:

```sql
documents.status = 'active'
documents.ingest_status = 'revisioned'
documents.current_revision_id = chunks.revision_id
chunks.is_current = 1
chunks.deleted_at IS NULL
```

OCR current revisions are treated the same as MarkItDown/text revisions. If forced OCR creates a new current revision, vector rebuild indexes the OCR chunks and does not default-return old text-PDF revision embeddings.

## Doctor Behavior

Doctor now detects:

- orphan embeddings
- embeddings whose chunk/doc/revision identity does not match
- embeddings marked `indexed` when their chunk is no longer active current content

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
.venv\Scripts\python scripts/m71_embedding_index_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

Latest result:

```text
141 passed
compileall passed
```

Latest M7.1 embedding gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m71-embedding-index-gate-20260513164551383564
M71_EMBEDDING_INDEX_GATE=passed
```

M7.1 gate summary:

```json
{
  "embedded_archived_chunks": 0,
  "embedded_current_chunks": 3,
  "embedded_old_revision_chunks": 0,
  "embedded_source_shells": 0,
  "embedding_failure_errors": 1,
  "embedding_failure_reviews": 1,
  "embedding_failures_blocking_fts": 0,
  "ocr_current_revision_embeddings": 1,
  "orphan_embedding_doctor_detected": 1,
  "orphan_embeddings": 0,
  "stale_embeddings_after_reingest": 0
}
```

Latest v0.1 RC aggregate after M7.1:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\v01-release-candidate-gate-20260513164734492939
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

M7.1 enables embedding index rebuild only.

Still not included:

- hybrid search result merging
- vector-only user search command
- real embedding provider
- remote model calls
- `indb ask`
- generated answers
- classification suggestions
- translation
- candidate cards
