# M7 Embedding and Hybrid Search Completion Checkpoint

Status: complete

Date: 2026-05-13

Preconditions:

- [M6.3 PDF/OCR Hardening Gate](m6-pdf-ocr-hardening-checkpoint.md)
- [M7 Readiness Checklist](m7-readiness-checklist.md)
- [M7.1 Embedding Index Checkpoint](m7-embedding-index-checkpoint.md)
- [M7.2 Hybrid Search Checkpoint](m7-hybrid-search-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

M7 is complete for deterministic local embedding indexing and retrieval-only hybrid search.

M7 remains search infrastructure. It does not introduce answer generation, `indb ask`, real embedding providers, reranking, query expansion, classification suggestions, translation, or candidate cards.

## Delivered

M7.1 delivered:

- deterministic local embedding adapter
- `embeddings` table writes through core services
- `documents.embedding_status`
- `indb index rebuild --vectors`
- embedding failure visibility through task/error/review state
- doctor/gate checks for orphan or stale embeddings

M7.2 delivered:

- `indb search --mode fts`
- `indb search --mode vector`
- `indb search --mode hybrid`
- chunk-bound snippets for all search modes
- `doc_id`, `revision_id`, `chunk_id`, `source_path`, score, and match-source output
- hybrid result merging without answer generation
- archive/source-shell/old-revision filtering
- forced OCR current-revision behavior in vector and hybrid search

## Frozen M7 Invariants

Embedding scope:

```sql
documents.status = 'active'
documents.current_revision_id = chunks.revision_id
chunks.is_current = 1
chunks.deleted_at IS NULL
```

Embedding rules:

- source shells are not embedded
- archived documents are not embedded
- old revisions are not embedded for default search
- embedding failure does not break FTS searchability
- embedding staleness is bound to `chunks.content_hash`
- metadata-only category/tag changes do not require re-embedding

Hybrid search rules:

- default search mode remains `fts`
- vector and hybrid search return source snippets, not generated answers
- archived documents are filtered by document status
- source shells are never returned
- old revision embeddings are not returned by default
- forced OCR revisions replace prior current revisions for default hybrid results
- ordinary search citations are still not persisted by default

## Verification

Latest verification commands:

```powershell
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python -m pytest
.venv\Scripts\python scripts/m6_pdf_ingest_gate.py
.venv\Scripts\python scripts/m62_ocr_gate.py
.venv\Scripts\python scripts/m63_pdf_ocr_hardening_gate.py
.venv\Scripts\python scripts/m71_embedding_index_gate.py
.venv\Scripts\python scripts/m72_hybrid_search_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

Latest result:

```text
compileall passed
145 passed
```

Latest M7.1 gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m71-embedding-index-gate-20260513201525073390
M71_EMBEDDING_INDEX_GATE=passed
```

M7.1 hard metrics:

```json
{
  "embedded_archived_chunks": 0,
  "embedded_current_chunks": 3,
  "embedded_old_revision_chunks": 0,
  "embedded_source_shells": 0,
  "embedding_failures_blocking_fts": 0,
  "ocr_current_revision_embeddings": 1,
  "orphan_embeddings": 0,
  "stale_embeddings_after_reingest": 0
}
```

Latest M7.2 gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m72-hybrid-search-gate-20260513201525644073
M72_HYBRID_SEARCH_GATE=passed
```

M7.2 hard metrics:

```json
{
  "fts_only_results": 2,
  "vector_only_results": 3,
  "hybrid_results": 3,
  "hybrid_results_with_doc_id": 3,
  "hybrid_results_with_revision_id": 3,
  "hybrid_results_with_chunk_id": 3,
  "hybrid_results_with_snippet": 3,
  "hybrid_results_with_source_path": 3,
  "hybrid_answer_fields": 0,
  "hybrid_archived_results": 0,
  "hybrid_old_revision_results": 0,
  "hybrid_source_shell_results": 0,
  "forced_ocr_current_revision_results": 1
}
```

Latest v0.1 RC aggregate after M7:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\v01-release-candidate-gate-20260513201542121437
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

## M8 Entry

M8 may start after this checkpoint if the next task explicitly asks for classification suggestions.

Before M8 implementation begins, rerun:

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python scripts/m63_pdf_ocr_hardening_gate.py
.venv\Scripts\python scripts/m71_embedding_index_gate.py
.venv\Scripts\python scripts/m72_hybrid_search_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

Blocking conditions for M8:

- any critical doctor finding
- any index integrity error
- any searchable source shell
- any embedded archived chunk
- any embedded old revision returned by default search
- any hybrid result missing `doc_id`, `revision_id`, `chunk_id`, or snippet
- any hybrid answer-generation field

## Boundary

M7 completion does not include:

- real embedding provider
- semantic quality tuning
- rerank
- query expansion
- answer generation
- `indb ask`
- classification suggestions
- translation
- candidate cards
