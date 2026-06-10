---
doc_type: agent_rules
phase_id: v0.3.5
stage_id: stage2-normalize-index-lineage
status: active
read_by_default: true
---

# v0.3.5 Stage 2 Agent Rules

Status: active. Use this file for Stage 2 implementation after Stage 1 work
inside the active v0.3.5 phase.

## Start Here

- Scope: `docs/planning/active/v0.3.5-engineering-stability-hardening.scope.md`
- Execution plan:
  `docs/planning/active/v0.3.5-stage2-normalize-index-lineage.execution.md`
- Source search contract: `docs/contracts/source-search-contract.md`
- Revision contract: `docs/contracts/revision-contract.md`
- Artifact contract: `docs/contracts/artifact-contract.md`
- Consoler boundary: `docs/contracts/consoler-agent-boundary.md`
- Provider contract: `docs/contracts/provider-capability-contract.md`

## Operating Rules

- Stage 2 implementation is approved as part of the active v0.3.5 scope.
- Keep public output service APIs unchanged.
- Preserve immutable revisions. Currentness is a pointer, not mutation.
- Keep previous revisions and chunks durable after normalize replace.
- Do not change `chunks_fts` schema.
- Implement source FTS lineage through companion tables only.
- Keep Stage 2 lineage mandatory for `source_fts` only; do not require
  embedding/vector lineage.
- Do not backfill synthetic successful index builds for historical FTS rows.
- Treat normalize replace failure as trusted-state atomic: previous current
  revision/search must remain usable.
- Agent-visible artifact views must not expose absolute local paths, provider
  cache paths, provider URIs, `file://`, or raw trace bodies beyond bounded
  summaries.

## Implementation Order

1. Add failing normalize replace regression tests.
2. Add failing source FTS lineage, indexer, and doctor drift tests.
3. Add failing agent-visible artifact leakage tests.
4. Add failing retrieval regression threshold tests.
5. Implement `0014_source_fts_lineage.sql` and lineage writer integration.
6. Add minimal normalize replace internal workflow helpers and fixes.
7. Redact artifact views and update contracts.
8. Implement retrieval regression fixtures and gate.
9. Wire the v0.3.5 stability gate into CI and update docs.

## Required Validation

Run focused checks first, then the gate:

```powershell
uv run python scripts/check_docs.py
uv run python -m pytest tests/test_normalize_replace_regression.py -q
uv run python -m pytest tests/test_indexer.py tests/test_doctor.py -q
uv run python -m pytest tests/test_indbase_agent_readonly_views.py tests/test_provider_runs.py -q
uv run python -m pytest tests/test_retrieval_regression.py -q
uv run python scripts/v035_stability_hardening_gate.py
```

Run existing baseline gates when touched areas require them:

```powershell
uv run python scripts/provider_fake_release_gate.py
uv run python scripts/v033_retrieval_eval_release_gate.py
```

## Do Not Touch Unless Explicitly Asked

- Retrieval ranking rewrites.
- `indb ask`, generated answers, summaries, claims, cards, or LLM judges.
- Physical deletion of revisions or chunks in normal workflows.
- External consoler protocol/runtime/store/schema files.
- Embedding/vector lineage as a mandatory Stage 2 requirement.
- Private vaults, runtime SQLite files, provider raw traces with private
  content, and local smoke output.

## Done Means

Report files changed, commands run, checks skipped and why, and remaining
risks. Do not claim a gate passed unless it ran in the current session.
