# MVP Performance Smoke Checkpoint

Status: complete

Date: 2026-05-14

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

The MVP performance smoke is complete for release close-out telemetry.

This checkpoint does not set a strict performance SLA. It records practical timing and size metrics while enforcing that no consistency invariant is broken under larger synthetic workloads.

## Gate

Run:

```powershell
.venv\Scripts\python scripts\mvp_perf_smoke.py
```

The gate runs:

- 100 small mixed Tier 1 files
- 500 small mixed Tier 1 files
- 1000 small mixed Tier 1 files
- large Markdown, CSV, and JSON payloads
- PDF text batch through deterministic MarkItDown shim
- OCR sidecar batch from failed PDF source shells

Each scenario records:

- input file count
- documents, source shells, unsupported items, failed items, errors, and reviews
- chunks, FTS rows, embeddings, translations, and candidate cards
- ingest time
- FTS rebuild time
- vector rebuild time
- doctor time
- FTS/hybrid search p50 and p95 latency
- DB size and vault size

## Hard Metrics

The smoke fails only on consistency breaks:

```json
{
  "critical_doctor_findings": 0,
  "zero_chunk_current_revisions": 0,
  "source_shells_searchable": 0,
  "fts_rebuild_failed_documents": 0,
  "vector_rebuild_failed_chunks": 0,
  "search_failures": 0
}
```

## Latest Result

```text
DOGFOOD_ROOT=E:\indbase\.tmp\mvp-perf-smoke-20260514121330849586
MVP_PERF_SMOKE=passed
```

Latest aggregate:

```json
{
  "candidate_cards": 0,
  "chunks": 2488,
  "critical_doctor_findings": 0,
  "current_chunks": 2488,
  "db_size_bytes_total": 28024832,
  "doctor_seconds_total": 11.854,
  "documents": 1643,
  "embeddings": 2488,
  "errors": 20,
  "failed_items": 20,
  "fts_desync": 0,
  "fts_rebuild_failed_documents": 0,
  "fts_rebuild_seconds_total": 5.949,
  "fts_rows": 2488,
  "ingest_seconds_total": 77.227,
  "input_files": 1643,
  "reviews": 20,
  "scenarios": 6,
  "search_failures": 0,
  "search_latency_p50_ms_max": 31,
  "search_latency_p95_ms_max": 49,
  "source_shells": 0,
  "source_shells_searchable": 0,
  "translations": 0,
  "unsupported_items": 0,
  "vault_size_bytes_total": 48653107,
  "vector_rebuild_failed_chunks": 0,
  "vector_rebuild_seconds_total": 0.482,
  "zero_chunk_current_revisions": 0
}
```

The 20 errors/reviews/failed items are expected source-shell conversion failures in the OCR sidecar batch. OCR later creates searchable revisions, and the final doctor state has no critical findings.

## Boundary

This smoke is synthetic telemetry. Broader release close-out is covered by separate gates:

- [MVP real corpus dogfood checkpoint](mvp-real-corpus-dogfood-checkpoint.md)
- [MVP CLI/TUI acceptance checkpoint](mvp-cli-tui-acceptance-checkpoint.md)
- [MVP backup restore checkpoint](mvp-backup-restore-checkpoint.md)
