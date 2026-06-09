# Current Active Work

Last updated: 2026-06-09

Source of current phase state: `docs/phase-manifest.yaml`

## Current State

There is no active indbase implementation phase after the v0.3.4 closeout.

Latest completed baseline:

- `v0.3.4` Provider Evidence / Trust Correlation.

Shipped or frozen:

- v0.1 Foundation MVP is frozen.
- v0.2 swallow-backed ingest and transition-backed output are shipped
  historical baselines.
- v0.3.1 category taxonomy foundation is shipped.
- v0.3.2 tag governance, v0.3.2.1 tag harness, and v0.3.2.2 governed
  tag/source search are shipped.
- v0.3.2.3a through v0.3.2.3f consoler coordination phases are completed.
- v0.3.3 Retrieval Evaluation / Answer Readiness is completed.
- v0.3.4 Provider Evidence / Trust Correlation is completed.

Next but not approved:

- Future `ask` and answer generation work remains out of scope until a later
  active phase explicitly approves it.

## Latest Completed Scope

v0.3.4 makes indbase consume packaged providers as evidence producers while
remaining the trust boundary and vault state owner.

The frozen provider trust chain is:

```text
indbase command
-> indbase service
-> provider capability
-> evidence package
-> indbase evidence copy
-> promotion/output policy
-> trusted state or visible review/error
```

The trust rule is:

```text
provider output = evidence or candidate
indbase decision = trusted state
```

## Required Reading

For new work:

- `docs/phase-manifest.yaml`
- `docs/project-status.md`
- `docs/testing.md`
- `docs/contracts/`
- `docs/agents/current/indbase.md`

For v0.3.4 archaeology or regression repair:

- `docs/planning/archive/v0.3.4/provider-evidence-trust-correlation.plan.md`
- `docs/agents/archive/indbase/v0.3.4-provider-evidence-trust-correlation.md`
- `docs/testing/archive/v0.3.4-provider-evidence-trust-correlation-closeout.md`

Read older archived phase docs only when a task explicitly asks for phase
archaeology.

## Baseline Gates

Documentation gate:

```powershell
uv run python scripts/check_docs.py
git diff --check
```

Provider baseline gate:

```powershell
uv run python scripts/provider_fake_release_gate.py
```

Real provider smoke remains environment-gated:

```powershell
uv run python scripts/provider_real_smoke.py
```

## Explicitly Out Of Scope

- `indb ask`
- generated answers, summaries, claims, cards, or notes
- writes to `citations`
- LLM judges, hidden network calls, or cost-bearing services
- default HTTP, queue, or MCP provider profiles
- provider backend selection exposed to ordinary users
- direct swallow or transition calls from `indbase_agent`
- provider cache as durable indbase truth
- `swallow://...`, `transition://...`, provider cache paths, or `file://...`
  provider artifacts in consoler artifact blocks
- retrieval ranking rewrites
- taxonomy/profile mutation through provider migration
- source revision mutation outside promotion or normalize `--replace-current`
- consoler protocol/runtime/store/schema changes from this repository
- Web UI, vault browser, source browser, provider job browser, and multi-action
  workflows

## Notes For Implementation Agents

- The vault is the system of record.
- `indbase_core` defines provider ports and evidence package contracts.
- `src/indbase_integrations/` owns provider adapters.
- Provider artifacts must be copied into `.indbase/artifacts/...` before
  promotion or output recording.
- Ingest remains evidence -> promotion -> immutable revision/chunks/FTS.
- Output remains source revision -> derived artifact/output run.
- Default source search indexes trusted current source chunks only.
- Agent operation trace may include provider correlation metadata, but artifact
  blocks remain `indbase://...` URIs.
