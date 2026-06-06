# v0.3.2.3c Consoler Variant Dogfood UX Agent Guide

Read this before working on v0.3.2.3c.

Canonical indbase spec:

- `docs/planning/v0.3.2.3c-consoler-variant-dogfood-ux.md`

Canonical consoler execution brief:

- `E:\consoler\docs\planning\v4d-indbase-dogfood-ux.md`

Required context:

- `AGENTS.md`
- `CONTEXT.md`
- `docs/project-status.md`
- `docs/testing.md`
- `docs/planning/v0.3.2.3-consoler-source-trust-probe.md`
- `docs/planning/v0.3.2.3a-consoler-probe-stabilization.md`
- `docs/planning/v0.3.2.3b-consoler-read-only-views.md`
- `docs/agents/v0.3.2.3b-consoler-read-only-views/AGENT.md`
- `E:\consoler\AGENTS.md`
- `E:\consoler\CONTEXT.md`
- `E:\consoler\docs\adr\0006-product-variants-keep-agent-specific-ui-boundaries.md`

## Objective

Coordinate the consoler-owned indbase Dogfood UX Variant without expanding indbase core.

Success means:

```text
consoler TUI can dogfood the existing Source Trust Loop
indbase command/artifact contracts stay stable
no new indbase core features are added for 3c
```

## Scope

Indbase-side scope:

- planning docs
- agent route docs
- keep adapter/read-only view gates available
- fix only proven `indbase_agent` contract bugs exposed by consoler v4d

Consoler-side scope is owned by:

- `E:\consoler\docs\planning\v4d-indbase-dogfood-ux.md`

## Do Not Touch

- Do not add indbase commands.
- Do not change `indbase_core`.
- Do not add migrations, durable UX state, or vault preference storage.
- Do not add Web UI, vault browser, full source viewer, revision browser, review mutation, category/tag mutation, or doctor repair.
- Do not add `ask`, retrieval packages, embeddings, generated answers, model providers, or LLM-assisted NL.
- Do not change consoler protocol/runtime/store/schema from this repo.

## Start Here

For indbase coordination:

```powershell
git status --short
rg -n "v0\\.3\\.2\\.3c|Source Trust Loop|indbase_agent|artifact_view" AGENTS.md CONTEXT.md docs src tests scripts
```

For consoler implementation:

```powershell
cd E:\consoler
git status --short
rg -n "indbaseVariant|ConsoleVariantConfig|artifact_view|variant|tui:indbase" packages/tui docs scripts
```

Primary consoler files are listed in `E:\consoler\docs\planning\v4d-indbase-dogfood-ux.md`.

## Steps

1. Read the indbase 3c plan and consoler v4d execution brief.
2. Confirm v0.3.2.3a and v0.3.2.3b adapter gates still pass before relying on them.
3. Implement UX work in `E:\consoler`, not indbase.
4. If consoler exposes an indbase adapter defect, add a focused indbase adapter test first.
5. Keep any indbase fix in `src/indbase_agent`, unless there is a proven core read-only bug.
6. Re-run the relevant indbase gates and consoler v4d checks.
7. Update status/testing docs only after implementation evidence exists.

## Validation

Indbase coordination:

```powershell
uv run python -m pytest tests/test_v0323c_indbase_coordination.py -q
uv run python -m pytest tests/test_indbase_agent.py tests/test_indbase_agent_readonly_views.py -q
uv run python scripts/v0323a_probe_stabilization_release_gate.py
uv run python scripts/v0323b_consoler_readonly_views_release_gate.py
uv run python -m compileall -q src tests scripts
```

Consoler implementation:

```powershell
cd E:\consoler
pnpm --filter @consoler/tui test
pnpm test:v4d-indbase-dogfood-ux
pnpm test:real-indbase-smoke
pnpm typecheck
pnpm build
git diff --check
```

## Done Means

- The consoler v4d gate passes.
- The indbase v0.3.2.3a and v0.3.2.3b gates still pass.
- The indbase variant exposes the ten-command Source Trust Loop action surface.
- `vault_path` prefill is session-local only.
- Artifact open/back UX works without artifact browsing or persisted content cache.
- No indbase core, consoler protocol/runtime/schema, Web UI, NL/LLM, or mutation scope was added.

## Unknowns

- Whether the consoler fixture manifest should be a TUI-local fixture or an updated exported protocol fixture.
- Whether existing TUI form state makes session-local vault prefill a small variant helper or requires a narrow app-state change.
- Whether real-indbase smoke should cover all five 3b artifact kinds immediately or stay focused on the walkthrough path first.
