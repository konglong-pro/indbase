# Architecture Overview

`indbase` is a **local-first knowledge substrate**. Intelligence features (ask, auto-cards, etc.) are explicitly deferred until ingest, revision, search, and observability are trustworthy.

## Trust boundaries

```text
┌─────────────────────────────────────────────────────────────┐
│ indbase (system of record)                                   │
│  vault files · SQLite metadata · revisions · chunks · FTS   │
│  tasks · errors · reviews · doctor · promotion · artifacts  │
└─────────────────────────────────────────────────────────────┘
         ▲                              ▲
         │ candidates + evidence        │ bridge JSON + evidence
         │                              │
┌────────┴────────┐            ┌────────┴────────┐
│ swallow (ingest) │            │ transition       │
│ conversion only  │            │ normalize/export │
│ local SDK/Core   │            │ local Node bridge│
└──────────────────┘            └──────────────────┘
```

**Rules:**

- External tools never write canonical `doc_id` / `revision_id` or mutate old revisions.
- Swallow output is a **candidate** until promotion → immutable source revision.
- Transition **export** artifacts live under `outputs/exports/` and are not default-searchable.
- Transition **normalize** creates a **new** source revision; prior revisions remain.

## Repository layout

```text
src/indbase_core/     # Durable services (ingest, revisions, chunker, indexer, doctor, …)
src/indbase_cli/      # Typer CLI (`indb`)
src/indbase_core/migrations/   # SQLite schema versions
scripts/              # Release gates and historical MVP gate scripts
tests/                # Pytest (232 tests)
docs/planning/        # Active specs; archive/ for historical checkpoints
docs/agents/          # Agent implementation guides for v0.2
```

## Key v0.2 modules

| Module | Responsibility |
| --- | --- |
| `swallow_adapter.py` | Map swallow jobs → `ConversionCandidate` |
| `promotion_policy.py` | `trusted-current` vs `review-before-current` |
| `ingest.py` / `conversion.py` | Pipelines, ingest_runs, converter_runs |
| `transition_adapter.py` | Node subprocess bridge |
| `output_service.py` | Export + normalize workflows |
| `output_evidence.py` | Durable artifacts under `.indbase/artifacts/output_runs/` |
| `protected_spans.py` | Span contract + validation (A+B) |
| `doctor.py` | Integrity checks across ingest and output |

## Search model

Default search indexes **current, promoted source revision chunks** only. Archived documents are excluded. Export artifacts and non-promoted candidates are excluded.

Hybrid/vector modes exist for MVP M7 features when enabled in config.

## Further reading

- [project-status.md](project-status.md) — shipped scope
- [planning/mvp-v0.1-spec.md](planning/mvp-v0.1-spec.md)
- [planning/v0.2-swallow-ingest-integration.md](planning/v0.2-swallow-ingest-integration.md)
- [planning/v0.2-transition-output-integration.md](planning/v0.2-transition-output-integration.md)
