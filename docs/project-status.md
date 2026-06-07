# Project Status

As of: 2026-06-07

This is the compressed status layer for indbase. It answers what exists now and
where to find canonical detail. It is not an evidence archive or phase plan.

Current phase identity is owned by `docs/phase-manifest.yaml`.

## Current Summary

`indbase` is a local-first knowledge substrate. It ingests local materials into
a vault, preserves originals, writes immutable source revisions, chunks and
indexes trusted source text, records observability state, and returns reliable
source snippets.

Current active work:

- `v0.3.3` Retrieval Evaluation / Answer Readiness.

Completed recent coordination:

- `v0.3.2.3f` indbase NL v2 intent drafting coordination is closed out in this
  repo. Consoler owned the V4g TUI/provider behavior.

## Default Reading Set

- `README.md`
- `AGENTS.md`
- `docs/active/current.md`
- `docs/project-status.md`
- `docs/testing.md`

Read contracts, ADRs, current agent rules, and active phase specs only when the
task needs them. Historical docs are not default reading.

## Shipped / Frozen

| Area | Status | Canonical docs | Gates |
| --- | --- | --- | --- |
| v0.1 Foundation MVP | frozen | archived spec and closeout in `docs/phase-manifest.yaml` | historical `scripts/mvp_release_gate.py` |
| v0.2 swallow ingest | shipped | archived spec and closeout in `docs/phase-manifest.yaml` | `scripts/v02_swallow_smoke_gate.py` |
| v0.2 transition output | shipped | archived spec and closeout in `docs/phase-manifest.yaml` | `scripts/v02_transition_smoke_gate.py` |
| v0.3.1 category foundation | shipped | archived spec and closeout in `docs/phase-manifest.yaml` | `scripts/v031_taxonomy_category_release_gate.py` |
| v0.3.2 tag governance | shipped | archived spec and closeout in `docs/phase-manifest.yaml` | `scripts/v032_tag_governance_release_gate.py` |
| v0.3.2.1 tag harness | shipped | archived spec and closeout in `docs/phase-manifest.yaml` | `scripts/v0321_tag_harness_release_gate.py` |
| v0.3.2.2 tag/search governance | shipped | archived spec and closeout in `docs/phase-manifest.yaml` | `scripts/v0322_tag_search_governance_release_gate.py` |
| v0.3.2 retrieval intelligence | completed | archived spec, closeout, and dogfood evidence in `docs/phase-manifest.yaml` | `scripts/v032_retrieval_release_gate.py` |
| v0.3.2.3 through v0.3.2.3f consoler Source Trust coordination | completed | archived specs and closeouts in `docs/phase-manifest.yaml` | adapter gates, coordination tests, and consoler-owned gates in `docs/testing.md` |

## Active

| Phase | Owner | Spec | Agent rules | Gate |
| --- | --- | --- | --- | --- |
| v0.3.3 Retrieval Evaluation / Answer Readiness | indbase | `docs/planning/active/v0.3.3-retrieval-evaluation-answer-readiness.md` | `docs/agents/current/indbase.md` | `uv run python scripts/v033_retrieval_eval_release_gate.py` |

## Active Behavior Summary

v0.3.3 adds deterministic retrieval evaluation and answer-readiness reports.

It owns:

- retrieval evaluation cases, runs, and results
- answer readiness reports
- deterministic JSONL import/export
- deterministic readiness policy
- `indb eval retrieval ...` CLI behavior
- doctor integrity checks for eval/readiness records
- `scripts/v033_retrieval_eval_release_gate.py`

It does not implement:

- `indb ask`
- generated answers, summaries, claims, cards, or notes
- LLM judges, providers, or network calls
- retrieval ranking rewrites
- source, taxonomy, profile, or output artifact mutation through eval itself

## Superseded

| Old rule | Superseded by |
| --- | --- |
| v0.1 direct normalizer / MarkItDown production conversion | v0.2 swallow ingest, `docs/planning/superseded/v0.1-direct-normalizer-rules.md` |
| v0.3.1 broad taxonomy/directory intelligence plan | split into category foundation, tag governance, and retrieval intelligence; see `docs/planning/superseded/v0.3.1-taxonomy-foundation.md` |
| root `AGENTS.md` as full phase encyclopedia | `docs/active/current.md`, `docs/phase-manifest.yaml`, `docs/contracts/`, and archive files |
| root `CONTEXT.md` as full glossary | `CONTEXT.md` index plus `docs/glossary/*.md` packs |

## Explicitly Out Of Scope

- Cloud sync, hosted queues, or multi-user SaaS.
- Physical delete of revisions/chunks in normal workflows.
- Default LLM behavior, provider calls, `ask`, or generated answers.
- Web UI, vault browser, source browser, and mutation UI from consoler.
- Treating candidates or export artifacts as trusted source revisions.

## Current Release Gates

Daily:

```powershell
uv run python -m pytest
uv run python -m compileall -q src tests scripts
uv run python scripts/check_docs.py
```

Current focused gate:

```powershell
uv run python scripts/v033_retrieval_eval_release_gate.py
```

See `docs/testing.md` for the full gate matrix and closeout evidence.
