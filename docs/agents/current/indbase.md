---
doc_type: agent_rules
status: active
read_by_default: true
---

# Current Indbase Agent Rules

No newer implementation phase is open in this worktree. Use this file as the
current routing note. The latest completed baseline is v0.3.5 Engineering
Stability Hardening; read its stage-specific rules before regression repair or
phase archaeology:

- Stage 1:
  `docs/agents/current/v0.3.5-stage1-provider-reliability-packaging/AGENTS.md`
- Stage 2:
  `docs/agents/current/v0.3.5-stage2-normalize-index-lineage/AGENTS.md`

## Default Routing

- Start from `docs/phase-manifest.yaml` and `docs/active/current.md`.
- Use `docs/project-status.md` for shipped and completed behavior.
- Use `docs/contracts/` for durable trust, artifact, revision, source search,
  provider capability, and consoler boundary rules.
- Do not start a new phase without explicit user authorization.
- Use the completed v0.3.5 scope for regression repair in touched areas unless
  a newer phase is explicitly opened.
- Stage 1 owns provider reliability, packaging, failure classification,
  evidence completeness, doctor provider health, and smoke gates.
- Stage 2 owns normalize replace regression, source FTS lineage, doctor drift,
  artifact view leak hardening, and retrieval regression thresholds.
- Keep the v0.3.4 trust rule: provider output is evidence or candidate;
  indbase decides trusted state.

## Baseline Validation

```powershell
uv run python scripts/check_docs.py
uv run python scripts/provider_fake_release_gate.py
uv run python scripts/provider_stage1_release_gate.py
uv run python scripts/v035_stability_hardening_gate.py
```

Real provider smoke remains environment-gated:

```powershell
uv run python scripts/provider_real_smoke.py
```
