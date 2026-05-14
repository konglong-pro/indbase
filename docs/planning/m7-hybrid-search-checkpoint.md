# M7.2 Hybrid Search Checkpoint

Status: complete

Date: 2026-05-13

Precondition: [M7.1 Embedding Index Checkpoint](m7-embedding-index-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](mvp-v0.1-spec.md)

## Decision

M7.2 is complete for retrieval-only hybrid search.

Hybrid search merges FTS/CJK current-source retrieval with deterministic local vector retrieval. It still returns source snippets tied to chunks. It does not generate answers.

## Behavior

Search modes:

```powershell
indb search <query> --mode fts
indb search <query> --mode vector
indb search <query> --mode hybrid
```

Default mode remains `fts`.

All modes return:

- `doc_id`
- `revision_id`
- `chunk_id`
- `source_path`
- `snippet`
- `score`
- `match_source`

Vector search:

- uses indexed rows from `embeddings`
- compares query vectors with stored deterministic vectors
- returns active current chunks only
- skips archived documents
- skips source shells
- skips old revisions
- skips stale or invalid embedding rows

Hybrid search:

- combines FTS/CJK and vector candidates
- uses reciprocal-rank style scoring for combined matches
- marks contribution in `match_source`, such as `fts+vector`
- preserves source-snippet output
- does not persist ordinary citations
- does not create answers or generated artifacts

## Scope Rules

Hybrid search uses the same source scope as FTS and vector indexing:

```sql
documents.status = 'active'
documents.current_revision_id = chunks.revision_id
chunks.is_current = 1
chunks.deleted_at IS NULL
```

Archive/restore rules:

- archived documents are not returned
- restoring a document makes it searchable again after normal index refresh
- FTS rows and embeddings do not override document status filtering

Forced OCR rules:

- forced OCR creates a new current revision
- hybrid search returns the OCR current revision after vector rebuild
- old text-PDF revision embeddings are not returned by default

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
.venv\Scripts\python scripts/m72_hybrid_search_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

Latest result:

```text
145 passed
compileall passed
```

Latest M7.2 hybrid search gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m72-hybrid-search-gate-20260513200604093927
M72_HYBRID_SEARCH_GATE=passed
```

M7.2 gate summary:

```json
{
  "archive_restore_archived_doc_results_after_archive": 0,
  "archive_restore_archived_doc_results_after_restore": 1,
  "archive_restore_hybrid_results_after_archive": 3,
  "archive_restore_hybrid_results_after_restore": 4,
  "cli_hybrid_has_source_path": 4,
  "cli_hybrid_results": 4,
  "forced_ocr_current_revision_results": 1,
  "forced_ocr_hybrid_results": 3,
  "fts_only_results": 2,
  "hybrid_answer_fields": 0,
  "hybrid_archived_results": 0,
  "hybrid_old_revision_results": 0,
  "hybrid_results": 3,
  "hybrid_results_with_chunk_id": 3,
  "hybrid_results_with_doc_id": 3,
  "hybrid_results_with_revision_id": 3,
  "hybrid_results_with_snippet": 3,
  "hybrid_results_with_source_path": 3,
  "hybrid_source_shell_results": 0,
  "vector_only_results": 3
}
```

Latest v0.1 RC aggregate after M7.2:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\v01-release-candidate-gate-20260513200602905413
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

M7.2 enables retrieval-only hybrid search.

Still not included:

- real embedding provider
- semantic quality tuning
- rerank
- query expansion
- answer generation
- `indb ask`
- classification suggestions
- translation
- candidate cards
