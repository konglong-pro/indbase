---
doc_type: agent_rules
status: completed
read_by_default: true
---

# Current Indbase Agent Rules

There is no active indbase implementation phase after the v0.3.4 closeout.

Use this file as a routing note only. The latest completed provider-evidence
rules are archived at
`docs/agents/archive/indbase/v0.3.4-provider-evidence-trust-correlation.md`.

## Default Routing

- Start from `docs/phase-manifest.yaml` and `docs/active/current.md`.
- Use `docs/project-status.md` for shipped and completed behavior.
- Use `docs/contracts/` for durable trust, artifact, revision, source search,
  provider capability, and consoler boundary rules.
- Do not implement archived, superseded, or future phase work unless the user
  explicitly asks for it.

## Baseline Validation

```powershell
uv run python scripts/check_docs.py
uv run python scripts/provider_fake_release_gate.py
```

Real provider smoke remains environment-gated:

```powershell
uv run python scripts/provider_real_smoke.py
```
