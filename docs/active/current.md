# Current Active Work

Last updated: 2026-06-07

Source of current phase: `docs/phase-manifest.yaml`

## Current State

`indbase` is a local-first knowledge substrate. The vault is the system of
record for originals, immutable source revisions, chunks, indexes,
observability records, and durable artifacts.

Shipped or frozen:

- v0.1 Foundation MVP is frozen.
- v0.2 swallow-backed ingest is shipped.
- v0.2 transition-backed output is shipped.
- v0.3.1 category taxonomy foundation is shipped.
- v0.3.2 tag governance, v0.3.2.1 tag harness, and v0.3.2.2 governed
  tag/source search are shipped.
- v0.3.2.3a through v0.3.2.3f consoler coordination phases are completed.

Active:

- `v0.3.3` Retrieval Evaluation / Answer Readiness.

Next but not approved:

- Future `ask` and answer generation work remains out of scope until a later
  active phase explicitly approves it.

## Required Reading For Current Work

- `docs/phase-manifest.yaml`
- `docs/planning/active/v0.3.3-retrieval-evaluation-answer-readiness.md`
- `docs/agents/current/indbase.md`
- `docs/contracts/revision-contract.md`
- `docs/contracts/source-search-contract.md`
- `docs/contracts/search-json-contract.md`
- `docs/contracts/retrieval-evaluation-contract.md`
- `docs/contracts/trust-boundary.md`
- `docs/testing.md`

Read archived or completed phase docs only when the task explicitly asks for
phase archaeology.

## Current Goal

`v0.3.3` makes retrieval quality measurable and adds deterministic answer
readiness reports. It prepares a future `ask` input contract without generating
answers.

The current product chain is:

```text
retrieve -> persisted retrieval_run_id -> readiness report -> future ask
```

## Current Gates

```powershell
uv run python scripts/v033_retrieval_eval_release_gate.py
uv run python -m pytest tests/test_retrieval_evaluation.py -q
uv run python scripts/check_docs.py
```

Run the broader suite from `docs/testing.md` when touching shared retrieval,
search, doctor, database, or CLI behavior.

## Explicitly Out Of Scope

- `indb ask`
- generated answers, summaries, claims, cards, or notes
- writes to `citations`
- LLM judges, providers, hidden network calls, or cost-bearing services
- retrieval ranking rewrites
- taxonomy/profile/source mutation through evaluation
- consoler protocol/runtime/store/schema changes from this repository
- Web UI, vault browser, source browser, and multi-action workflows

## Implementation Notes

- Eval/readiness status and `error_json` fields make failures visible; v0.3.3
  does not create task records for lightweight eval runs.
- Readiness verdicts are `ready`, `needs_more_evidence`, and `not_ready`.
- Hard source-binding and quote failures must remain `not_ready`.
- Future `ask` must consume an explicit readiness-checked `retrieval_run_id`;
  it must not run hidden retrieval internally.
