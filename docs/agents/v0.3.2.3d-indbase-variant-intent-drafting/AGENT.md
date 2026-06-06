# v0.3.2.3d Indbase Variant Intent Drafting Agent Guide

Read this before working on v0.3.2.3d.

Canonical indbase coordination spec:

- `docs/planning/v0.3.2.3d-indbase-variant-intent-drafting.md`

Canonical consoler execution brief:

- `E:\consoler\docs\planning\v4e-indbase-variant-intent-drafting.md`

Required context:

- `AGENTS.md`
- `CONTEXT.md`
- `docs/planning/v0.3.2.3c-consoler-variant-dogfood-ux.md`
- `docs/planning/v0.3.2.3b-consoler-read-only-views.md`
- `docs/agents/v0.3.2.3c-consoler-variant-dogfood-ux/AGENT.md`
- `E:\consoler\AGENTS.md`
- `E:\consoler\CONTEXT.md`
- `E:\consoler\docs\adr\0003-natural-language-intent-drafting.md`
- `E:\consoler\docs\adr\0006-product-variants-keep-agent-specific-ui-boundaries.md`

## Objective

Coordinate consoler-owned deterministic indbase variant intent drafting without expanding indbase.

Success means:

```text
consoler can draft one Source Trust Loop action from natural language
the draft opens an editable form
indbase command and artifact contracts stay stable
no new indbase core feature is added
```

## Scope

Indbase-side scope:

- planning docs
- agent route docs
- optional coordination contract test
- status/testing docs after implementation evidence exists
- smallest `indbase_agent` contract fix only if consoler V4e proves a real adapter defect

Consoler-side implementation is owned by:

- `E:\consoler\docs\planning\v4e-indbase-variant-intent-drafting.md`

## Do Not Touch

- Do not add indbase commands.
- Do not add natural-language parsing to `indbase_agent`.
- Do not change `indbase_core`.
- Do not add migrations, durable UX state, or vault preference storage.
- Do not add Web UI, vault browser, full source viewer, revision browser, review/category/tag mutation, or doctor repair.
- Do not add `ask`, retrieval packages, embeddings, generated answers, model providers, or default LLM/assisted NL.
- Do not change consoler protocol/runtime/store/schema from this repo.

## Start Here

For indbase coordination:

```powershell
git status --short
rg -n "v0\\.3\\.2\\.3d|Indbase Variant Intent Drafting|Source Trust Loop|intent" AGENTS.md CONTEXT.md docs
```

For consoler implementation:

```powershell
cd E:\consoler
git status --short
rg -n "draftIntent|IntentScope|intentHints|prefilled_args|indbaseVariant|tui:indbase" packages/runtime packages/tui docs scripts
```

Primary consoler files are listed in `E:\consoler\docs\planning\v4e-indbase-variant-intent-drafting.md`.

## Steps

1. Read the indbase 3d plan and consoler V4e execution brief.
2. Confirm the ten-command Source Trust Loop action surface stays unchanged.
3. Implement deterministic intent drafting changes in `E:\consoler`, not indbase.
4. Keep indbase `vault_path`, search filter, object ID, and artifact semantics agent-owned; consoler may prefill form fields but must not validate vault state during drafting.
5. If consoler exposes an indbase adapter defect, add a focused indbase adapter test before fixing it.
6. Update indbase status/testing docs only after implementation evidence exists.

## Validation

Documentation-only indbase changes:

```powershell
git diff --check
```

If adding an indbase coordination test:

```powershell
uv run python -m pytest tests/test_v0323d_indbase_intent_coordination.py -q
uv run python -m compileall -q src tests scripts
```

If touching indbase adapter code:

```powershell
uv run python -m pytest tests/test_indbase_agent.py tests/test_indbase_agent_readonly_views.py -q
uv run python scripts/v0323a_probe_stabilization_release_gate.py
uv run python scripts/v0323b_consoler_readonly_views_release_gate.py
uv run python -m compileall -q src tests scripts
```

Consoler implementation:

```powershell
cd E:\consoler
pnpm --filter @consoler/runtime test
pnpm --filter @consoler/tui test
pnpm test:v4e-indbase-variant-intent-drafting
pnpm test:v3b-intent-gate
pnpm typecheck
pnpm build
git diff --check
```

Run V3c assisted gates only when shared assisted intent paths are touched:

```powershell
pnpm test:v3c-assisted-intent-gate
pnpm test:v3c-tui-assisted-intent-gate
```

## Done Means

- The consoler V4e gate passes.
- Clear natural-language input can select all ten Source Trust Loop commands.
- Conservative `prefilled_args` open editable forms and do not bypass lifecycle steps.
- Missing or ambiguous data routes to form/home fallback, not chat.
- Session `vault_path` is form-layer convenience only.
- No indbase command, core feature, schema, durable UX state, Web UI, vault browser, mutation workflow, retrieval package, `ask`, embedding, generated answer, or default LLM behavior was added.

## Unknowns

- Whether an indbase-side coordination test is worth adding before implementation or only after consoler V4e exposes a stable contract expectation.
- Whether the existing consoler deterministic mapper can support all required search/query/id extraction with small generic changes.
- Whether real-indbase smoke should be run for V4e closeout or left to local-only regression when smoke scripts change.
