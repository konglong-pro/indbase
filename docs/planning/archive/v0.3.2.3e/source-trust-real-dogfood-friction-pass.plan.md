---
doc_type: phase_plan
phase_id: v0.3.2.3e
title: Source Trust Real Dogfood Friction Pass
status: completed
canonical: true
read_by_default: false
closeout: docs/testing.md#latest-v0323e--consoler-v4f-closeout
related_contracts:
  - docs/contracts/artifact-contract.md
  - docs/contracts/intent-draft-contract.md
  - docs/contracts/consoler-agent-boundary.md
---

# v0.3.2.3e Source Trust Real Dogfood Friction Pass

Status: closeout passed

Date: 2026-06-06

Phase: v0.3.2.3e coordination for consoler-owned real Source Trust Loop dogfood closeout and UX friction fixes

Related docs:

- [v0.3.2.3d Indbase Variant Intent Drafting](../v0.3.2.3d/indbase-variant-intent-drafting.plan.md)
- [v0.3.2.3c Consoler Variant Dogfood UX](../v0.3.2.3c/consoler-variant-dogfood-ux.plan.md)
- [v0.3.2.3b Consoler Read-Only Views](../v0.3.2.3b/consoler-read-only-views.plan.md)
- [v0.3.2.3e Agent Guide](../../../agents/archive/indbase/v0.3.2.3e-source-trust-real-dogfood-friction-pass.md)
- `E:\consoler\docs\planning\v4f-indbase-real-dogfood-friction-pass.md`
- `E:\consoler\docs\planning\v4e-indbase-variant-intent-drafting.md`
- `E:\consoler\docs\planning\v4d-indbase-dogfood-ux.md`
- `E:\consoler\docs\testing\real-indbase-smokes.md`
- `E:\consoler\CONTEXT.md`
- [indbase glossary](../../../../CONTEXT.md)

## Objective

Coordinate a consoler-owned evidence-first friction pass over the existing indbase Console Variant.

The phase proves the real Source Trust Loop is usable enough to keep dogfooding:

```text
real local dogfood
-> friction register
-> narrow allowed fixes
-> deterministic regression gate
-> local real smoke and manual checklist evidence
-> closeout record
```

This phase is not a new indbase capability phase. It is a closeout and UX friction pass for the existing ten-command Source Trust Loop.

## Phase Definition

3e is an evidence-first friction-fix pass.

It may fix concrete friction discovered during real dogfood in:

- consoler TUI variant UX
- product copy
- form flow
- artifact open/back flow
- history/trace navigation
- deterministic product TUI smoke coverage
- documentation and closeout evidence
- narrow `indbase_agent` adapter defects only when real dogfood proves a contract bug

It must not add new Source Trust commands, protocol behavior, durable UI state, vault browsing, Web UI, generated answers, `ask`, review/category/tag mutation, or broader NL capability.

## Scope

In scope for the overall phase:

- disposable synthetic vault dogfood as required automated evidence
- optional real or semi-real private vault dogfood as local-only evidence
- a privacy-preserving friction register
- narrow consoler-owned UX fixes for existing Source Trust Loop paths
- deterministic V4f gate in `E:\consoler`
- local-only real indbase smoke evidence
- manual `pnpm tui:indbase --` checklist when a real interactive terminal is available
- indbase-side planning, agent routing, and status/testing closeout records

In scope for `E:\indbase` only:

- this planning document
- `docs/agents/archive/indbase/v0.3.2.3e-source-trust-real-dogfood-friction-pass.md`
- `AGENTS.md` phase routing
- optional status/testing updates after implementation evidence exists
- optional coordination tests only if they verify stable contracts without adding product behavior
- narrow `indbase_agent` fixes only if V4f dogfood proves an adapter contract defect

Out of scope:

- new indbase commands
- `indbase_core` changes
- migrations
- durable UX state or vault preference storage
- title/path lookup
- vault browser or source browser
- full file content viewer
- review/category/tag mutation
- doctor repair
- retrieval packages
- `ask`
- embeddings
- generated answers
- chat
- LLM or assisted intent expansion
- broader NL v2 behavior
- consoler protocol, runtime lifecycle, runtime store schema, replay, transport, or Python SDK changes
- default CI dependency on `E:\indbase`, private vaults, real swallow, manual TUI, or network access

## Accepted Decisions

### Phase type

3e is evidence-first. No planned feature work should start until dogfood friction is recorded.

Allowed fixes must map back to a concrete friction finding.

### Dogfood vaults

Use two evidence layers:

```text
required: disposable synthetic vault
optional: real or semi-real private vault
```

The disposable layer may become an automated gate. The private-vault layer is local-only and privacy-preserving.

### Fix budget

Allowed fix classes:

- copy
- form
- navigation
- artifact
- error-state
- test
- docs
- adapter-bug

Not allowed:

- new Source Trust Loop commands
- title/path lookup
- vault browser
- full source viewer
- review/category/tag mutation
- `ask`
- chat
- LLM/default assisted intent
- multi-action workflow
- protocol/runtime/store/schema change
- persisted vault preference

If a friction finding requires a forbidden capability, record it as a future-phase candidate.

### Evidence structure

Closeout evidence must separate:

- automated deterministic evidence
- local real-agent evidence
- manual dogfood evidence
- skipped manual evidence with reason
- friction findings and fixes

Automated tests must not be described as a manually typed TUI session.

### Branch and PR boundary

3e should be a new phase branch/PR after 3d/V4e closeout, not mixed into the 3d intent-drafting PR.

`consoler` should treat the implementation as V4f. `indbase` should treat it as v0.3.2.3e coordination.

### Repository ownership

`consoler` owns implementation. `indbase` owns coordination and the real agent adapter contract.

`indbase_core` should not change for 3e.

### Friction register

Every actionable friction item needs:

```text
id
scenario
evidence
impact
allowed_fix_class
owner_repo
status
verification
```

Only `blocker`, `confusing`, and high-frequency or low-cost `repetitive` items should be fixed in 3e. `cosmetic` items are not release blockers.

### NL boundary

3e may dogfood deterministic intent drafting from 3d, but it must not expand NL capability.

Do not infer vaults, objects, filters, or "latest result" from history, trace, artifacts, cwd, filesystem state, or private vault content.

### Privacy

Record behavior evidence, not private content evidence.

Do not commit or document private vault paths, source excerpts, titles, tag/category names from private data, raw trace JSON, runtime SQLite files, logs, screenshots with private content, or generated temp vault data.

Private-vault-only friction must be reduced to a synthetic or redacted reproduction before it becomes an automated test.

### Acceptance threshold

3e does not need to clear every friction item.

It must:

- record all discovered items
- fix blockers unless the fix requires an explicit future phase
- fix or clearly defer confusing items
- fix only high-value repetitive items
- leave cosmetic items non-blocking
- verify fixed items with tests or manual retest

### Gate layering

`consoler` should add a deterministic V4f gate suitable for CI.

Real indbase smoke and manually typed TUI dogfood stay local-only closeout evidence unless a future provisioned environment changes that boundary.

### Closeout to next phase

3e closeout produces follow-up categories only:

- immediate bugfix
- next UX phase
- product expansion candidate

It must not pre-commit the project to NL v2, Web UI, vault browser, `ask`, or broader retrieval work.

## Friction Register Template

Use this shape in the consoler closeout/testing doc:

| Field | Meaning |
| --- | --- |
| `id` | Stable finding id, for example `v4f-friction-001`. |
| `scenario` | Source Trust Loop path where the issue appeared. |
| `evidence` | Automated smoke, manual checklist step, screenshot description, or reproduction steps. |
| `impact` | `blocker`, `confusing`, `repetitive`, or `cosmetic`. |
| `allowed_fix_class` | `copy`, `form`, `navigation`, `artifact`, `error-state`, `test`, `docs`, or `adapter-bug`. |
| `owner_repo` | `consoler` or `indbase`. |
| `status` | `recorded`, `fixed`, `deferred`, or `not_in_scope`. |
| `verification` | Test name, command, manual retest step, or deferred reason. |

## Implementation Plan

The implementation belongs in `E:\consoler\docs\planning\v4f-indbase-real-dogfood-friction-pass.md`.

Indbase-side work should stay limited to:

1. Keep this planning document and the 3e agent guide current.
2. Update `AGENTS.md` phase routing.
3. Keep 3a, 3b, 3c, and 3d coordination checks available.
4. Record closeout evidence in `docs/project-status.md` and `docs/testing.md` only after V4f implementation evidence exists.
5. If V4f exposes a true adapter defect, add a focused indbase adapter test before fixing it.
6. If a private-vault finding requires indbase reproduction, convert it to a sanitized synthetic fixture before committing tests.

Do not change `indbase_core` for 3e unless the user explicitly starts a new indbase core phase.

## Required Validation

Indbase documentation-only checks:

```powershell
git diff --check
```

If an indbase coordination test is added:

```powershell
uv run python -m pytest tests/test_v0323e_source_trust_friction_coordination.py -q
uv run python -m compileall -q src tests scripts
```

If any adapter code is touched:

```powershell
uv run python -m pytest tests/test_indbase_agent.py tests/test_indbase_agent_readonly_views.py -q
uv run python scripts/v0323a_probe_stabilization_release_gate.py
uv run python scripts/v0323b_consoler_readonly_views_release_gate.py
uv run python -m compileall -q src tests scripts
```

Consoler implementation checks, run from `E:\consoler` after V4f implementation:

```powershell
pnpm --filter @consoler/tui test
pnpm test:v4f-indbase-real-dogfood-friction-pass
pnpm test:v4e-indbase-variant-intent-drafting
pnpm test:v4d-indbase-dogfood-ux
pnpm typecheck
pnpm build
git diff --check
```

Local-only real dogfood evidence:

```powershell
CONSOLER_KEEP_REAL_INDBASE_SMOKE=1 pnpm test:real-indbase-smoke
pnpm exec vitest run packages/tui/test/real-indbase-product-tui-smoke.test.tsx
pnpm tui:indbase --
```

The last command is a manual checklist step, not a CI gate. If a reliable interactive terminal is unavailable, record the skip reason.

## Acceptance Checklist

- V4f starts from 3d/V4e closeout and does not mix into the 3d PR scope.
- Real dogfood is run or explicitly skipped with reason before friction fixes are claimed.
- Disposable synthetic vault evidence is required and automated.
- Private-vault evidence is local-only and privacy-preserving.
- Every fixed issue has a friction register entry.
- Blockers are fixed or explicitly deferred because they require a future phase.
- Confusing items are fixed or have clear deferred reasons.
- High-value repetitive items are fixed when low-risk.
- Cosmetic items are not release blockers.
- Deterministic V4f gate passes in consoler.
- Real indbase smoke passes or records a clear skip reason.
- Manual TUI checklist is completed or records a clear PTY/environment skip reason.
- No new Source Trust Loop commands, indbase core features, migrations, persisted vault preferences, vault browser, Web UI, mutation UI, retrieval package, `ask`, embedding, generated answer, default LLM behavior, or consoler protocol/runtime/store/schema change is added.

## Closeout Evidence

Latest closeout: 2026-06-06.

Indbase files changed:

- `AGENTS.md`
- `CONTEXT.md`
- `docs/planning/archive/v0.3.2.3e/source-trust-real-dogfood-friction-pass.plan.md`
- `docs/agents/archive/indbase/v0.3.2.3e-source-trust-real-dogfood-friction-pass.md`
- `docs/project-status.md`
- `docs/testing.md`

Consoler files changed:

- `AGENTS.md`
- `CONTEXT.md`
- `package.json`
- `scripts/test-v4f-indbase-real-dogfood-friction-pass.mjs`
- `packages/tui/src/app.tsx`
- `packages/tui/src/blocks.tsx`
- `packages/tui/src/trace-panel.tsx`
- `packages/tui/src/result-blocks-panel.tsx`
- `packages/tui/test/artifact-browser-flow.test.tsx`
- `packages/tui/test/real-indbase-product-tui-smoke.test.tsx`
- `docs/planning/v4f-indbase-real-dogfood-friction-pass.md`
- `docs/testing/v4f-indbase-real-dogfood-friction-pass.md`
- `docs/testing/real-indbase-smokes.md`

Automated deterministic evidence:

```text
E:\consoler
pnpm --filter @consoler/tui test
  -> 14 test files passed, 1 skipped; 54 tests passed, 1 skipped
pnpm test:v4f-indbase-real-dogfood-friction-pass
  -> V4f indbase real dogfood friction pass gate passed
pnpm test:v4e-indbase-variant-intent-drafting
  -> V4e indbase variant intent drafting gate passed
pnpm test:v4d-indbase-dogfood-ux
  -> V4d indbase dogfood UX gate passed
pnpm typecheck
  -> passed
pnpm build
  -> passed
```

Real indbase local smoke evidence:

```text
E:\consoler
CONSOLER_KEEP_REAL_INDBASE_SMOKE=1 pnpm test:real-indbase-smoke
  -> real indbase smoke passed
pnpm exec vitest run packages/tui/test/real-indbase-product-tui-smoke.test.tsx
  -> 1 test passed
```

Manual dogfood evidence:

```text
pnpm tui:indbase --
  -> not run in this Codex shell; no reliable interactive PTY
```

Friction register summary:

- `v4f-friction-001`: fixed terminal-visible non-ASCII control copy in touched TUI surfaces.
- `v4f-friction-002`: fixed local-only real product TUI smoke gap for deterministic NL form prefill with a real discovered indbase manifest.
- `v4f-friction-003`: fixed missing V4f deterministic gate and friction register.
- `v4f-friction-004`: deferred same-session continuation from real finished doctor result to home until a manual PTY run can confirm whether this is a test-harness issue or real navigation friction.

Boundary check: 3e remained a friction pass. It did not add indbase adapter commands, indbase core features, migrations, durable UX state, vault browser, source browser, Web UI, review/category/tag mutation, doctor repair, retrieval packages, `ask`, embeddings, generated answers, default LLM behavior, broader NL capability, or consoler protocol/runtime/store/schema changes.
