# M7 Readiness Checklist

Status: ready for M7.1 implementation

Date: 2026-05-13

Precondition: [M6.3 PDF/OCR Hardening Checkpoint](m6-pdf-ocr-hardening-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](mvp-v0.1-spec.md)

## Decision

M7 may start, but only after these interfaces are treated as frozen for the first embedding and hybrid-search checkpoints.

M7 is still search infrastructure. It must not introduce `ask`, answer generation, summaries, candidate cards, translation, or LLM-backed classification.

## Frozen Design Points

### 1. Embeddings Index Current Source Chunks Only

Default embedding scope:

```sql
documents.status = 'active'
documents.current_revision_id = chunks.revision_id
chunks.is_current = 1
chunks.deleted_at IS NULL
```

Do not embed by default:

- archived documents
- old revisions
- source shells
- failed documents
- unsupported/review-only ingest items

This prevents vector and hybrid search from returning stale revisions or failed sources.

### 2. Embedding Does Not Block Ingest Or OCR

FTS remains the reliable search baseline.

Embedding is an enhancement index. Embedding failure must not make a document lose FTS searchability.

Embedding status values:

- `pending`
- `indexing`
- `indexed`
- `failed`
- `stale`

### 3. OCR Revisions Are Normal Source Revisions

Text PDF revisions and OCR revisions are both source revisions for indexing.

Rules:

- text PDF current revision: embed current chunks
- OCR current revision: embed current chunks
- forced OCR current revision replaces the default vector-search scope
- old text PDF embeddings must not be default-returned after forced OCR

### 4. Vector Index Must Be Rebuildable

M7 starts with explicit rebuild behavior:

```powershell
indb index status
indb index rebuild --vectors
```

Vector rebuild rules:

- rebuild active current source chunks only
- skip source shells
- skip archived documents
- skip old revisions
- record embedding failures in tasks/errors/reviews
- do not mutate document revisions
- do not break FTS searchability

### 5. Hybrid Search Still Returns Source Snippets

Hybrid search is not `ask`.

Hybrid search output must include:

- `doc_id`
- `revision_id`
- `chunk_id`
- `snippet`
- `score`
- source path when available
- search-mode contribution when available

Hybrid search must not generate synthetic answers.

### 6. Embedding Staleness Uses Chunk Content Hash

M7 v1 binds embeddings to chunk text identity:

```text
embeddings.content_hash = chunks.content_hash
```

Rules:

- chunk content changed -> embedding stale
- current revision changed -> old embeddings not default-returned
- metadata-only category/tag changes -> embedding not stale
- metadata changes may refresh FTS metadata, but do not require re-embedding

## M7.1 Embedding Index Checkpoint

Goal: implement the vector indexing substrate without hybrid result merging.

Required verification:

- embedding adapter exists
- embeddings table is written
- vector index storage is deterministic for tests
- `embedding_status` transitions are visible
- `indb index rebuild --vectors` works
- source shells are skipped
- archived documents are skipped
- old revisions are skipped
- OCR current revisions can be embedded
- embedding failure is visible but does not block FTS

Hard metrics:

```json
{
  "embedded_current_chunks": ">0",
  "embedded_archived_chunks": 0,
  "embedded_old_revision_chunks": 0,
  "embedded_source_shells": 0,
  "embedding_failures_blocking_fts": 0,
  "stale_embeddings_after_reingest": 0,
  "orphan_embeddings": 0
}
```

## M7.2 Hybrid Search Checkpoint

Goal: add hybrid retrieval while preserving source-snippet search semantics.

Required verification:

- FTS-only search still works
- vector-only search works
- hybrid search merges/ranks FTS and vector results
- hybrid search returns snippets from chunks
- no answer generation exists
- current revision scope is respected
- archive/restore filters apply
- forced OCR current revision is respected

Hard metrics:

```json
{
  "hybrid_results_with_doc_id": ">0",
  "hybrid_results_with_revision_id": ">0",
  "hybrid_results_with_chunk_id": ">0",
  "hybrid_results_with_snippet": ">0",
  "hybrid_old_revision_results": 0,
  "hybrid_archived_results": 0,
  "hybrid_source_shell_results": 0
}
```

## M7 Start Gate

Before implementing M7.1, run:

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python scripts/m63_pdf_ocr_hardening_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

M7.1 may start if all pass and the M6.3 hard metrics remain zero.
