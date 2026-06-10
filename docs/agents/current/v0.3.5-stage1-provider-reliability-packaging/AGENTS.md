---
doc_type: agent_rules
phase_id: v0.3.5
stage_id: stage1-provider-reliability-packaging
status: active
read_by_default: true
---

# v0.3.5 Stage 1 Agent Rules

Status: active. Use this file for Stage 1 implementation inside v0.3.5.

## Start Here

- Scope: `docs/planning/active/v0.3.5-engineering-stability-hardening.scope.md`
- Execution plan:
  `docs/planning/active/v0.3.5-stage1-provider-reliability-packaging.execution.md`
- Provider contract: `docs/contracts/provider-capability-contract.md`
- Trust boundary: `docs/contracts/trust-boundary.md`
- Artifact boundary: `docs/contracts/artifact-contract.md`
- Consoler boundary: `docs/contracts/consoler-agent-boundary.md`
- Current repo state: `docs/active/current.md` and `docs/phase-manifest.yaml`

## Operating Rules

- Stage 1 implementation is approved as part of the active v0.3.5 scope.
- Keep `indbase_integrations` as the provider adapter package; do not move
  provider adapter implementation into `indbase_core`.
- Keep provider selection internal. Do not add ordinary user-facing provider
  selection flags.
- Preserve the trust rule: provider output is evidence or candidate; indbase
  decides trusted state.
- Keep fake provider gates deterministic. Real provider smoke remains
  environment-gated.
- Record retry/fallback policy metadata only; do not execute retry or fallback
  workflows in Stage 1.
- Do not globally change unrelated consoler artifact view `vault_path`
  behavior in Stage 1.

## Implementation Order

1. Packaging and installed wheel smoke.
2. Provider failure classification and `provider_runs.failure_class`.
3. Evidence completeness and provider artifact boundary hardening.
4. ProviderHealth, doctor, smoke gates, and CI aggregation.

## Required Validation

Run the smallest focused check first, then the relevant gate:

```powershell
uv run python scripts/check_docs.py
uv run python -m pytest tests/test_provider_contracts.py tests/test_provider_runs.py -q
uv run python scripts/provider_fake_release_gate.py
uv run python scripts/provider_installed_wheel_smoke.py
uv run python scripts/provider_transition_distribution_smoke.py
uv run python scripts/provider_stage1_release_gate.py
```

Run consoler and real provider required modes only in provisioned trusted
environments:

```powershell
$env:INDBASE_CONSOLER_CONTRACT_REQUIRED='1'
uv run python scripts/provider_consoler_contract_smoke.py
$env:INDBASE_PROVIDER_SMOKE_REQUIRED='1'
uv run python scripts/provider_real_smoke.py
```

## Do Not Touch Unless Explicitly Asked

- External consoler protocol/runtime/store/schema files.
- Dynamic plugin discovery, plugin marketplace, HTTP provider, queue provider,
  or MCP provider profiles.
- Retrieval ranking, `ask`, generated answers, or LLM judge behavior.
- Private vaults, runtime SQLite files, provider raw traces with private
  content, and local smoke output.

## Done Means

Report files changed, commands run, checks skipped and why, and remaining
risks. Do not claim a gate passed unless it ran in the current session.
