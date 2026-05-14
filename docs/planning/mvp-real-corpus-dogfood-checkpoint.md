# MVP Real Corpus Dogfood Checkpoint

Status: complete

Date: 2026-05-14

Canonical baseline: [mvp-v0.1-spec.md](mvp-v0.1-spec.md)

## Decision

The MVP real corpus dogfood pass is complete for the repo-local release close-out corpus.

The default corpus uses real project documentation and source content:

- Markdown files are copied directly from `README.md`, `AGENTS.md`, and `docs/planning/`.
- Python/config source files are mirrored as `.txt` files so their real content can exercise the supported text ingest path.

For private or larger user material, rerun the same gate with:

```powershell
$env:INDB_REAL_CORPUS="E:\path\to\real\folder"
.venv\Scripts\python scripts\mvp_real_corpus_dogfood.py
```

## Gate

Run:

```powershell
.venv\Scripts\python scripts\mvp_real_corpus_dogfood.py
```

The gate validates:

- init
- recursive ingest of 50-100 real-content files
- FTS rebuild
- vector rebuild
- FTS, vector, and hybrid search
- manual category/tag mutation
- classification suggest/accept/reject
- selected-chunk and full-document translation
- candidate card generate/accept/reject
- `doc open`, `doc open --original`, and `translate open`
- accepted atomic note output
- final doctor

## Latest Result

```text
DOGFOOD_ROOT=E:\indbase\.tmp\mvp-real-corpus-dogfood-20260514145143400157
MVP_REAL_CORPUS_DOGFOOD=passed
```

Latest summary:

```json
{
  "accepted_atomic_notes": 1,
  "accepted_candidate_cards": 1,
  "active_documents": 85,
  "candidate_cards": 2,
  "chunks": 504,
  "classification_scanned_documents": 20,
  "classification_suggested_documents": 17,
  "critical_doctor_findings": 0,
  "current_chunks": 504,
  "direct_files": 32,
  "documents": 85,
  "embeddings": 504,
  "errors": 0,
  "fts_rows": 504,
  "input_files": 85,
  "mirrored_text_files": 53,
  "rejected_candidate_cards": 1,
  "reviews": 17,
  "search_failures": 0,
  "search_fts_missing_queries": 0,
  "search_fts_results": 65,
  "search_hybrid_missing_queries": 0,
  "search_hybrid_results": 100,
  "search_vector_missing_queries": 0,
  "search_vector_results": 100,
  "source_shells": 0,
  "source_shells_searchable": 0,
  "tasks": 10,
  "translations": 2,
  "zero_chunk_current_revisions": 0
}
```

Doctor exit code was `1` because expected review items remain visible. There were no critical doctor findings.

## Boundary

This is a repo-local real-content dogfood pass. It does not claim to cover the user's private knowledge corpus unless `INDB_REAL_CORPUS` is set and the gate is rerun against that folder.
