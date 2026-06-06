# v0.3.2.3e Source Trust Real Dogfood Friction Pass Agent Guide

Read this before working on v0.3.2.3e.

Canonical indbase coordination spec:

- `docs/planning/v0.3.2.3e-source-trust-real-dogfood-friction-pass.md`

Canonical consoler execution brief:

- `E:\consoler\docs\planning\v4f-indbase-real-dogfood-friction-pass.md`

Required context:

- `AGENTS.md`
- `CONTEXT.md`
- `docs/planning/v0.3.2.3d-indbase-variant-intent-drafting.md`
- `docs/planning/v0.3.2.3c-consoler-variant-dogfood-ux.md`
- `docs/agents/v0.3.2.3d-indbase-variant-intent-drafting/AGENT.md`
- `E:\consoler\AGENTS.md`
- `E:\consoler\CONTEXT.md`
- `E:\consoler\docs\planning\v4f-indbase-real-dogfood-friction-pass.md`
- `E:\consoler\docs\testing\real-indbase-smokes.md`

## Objective

Coordinate the consoler-owned V4f friction pass without expanding indbase.

Success means:

```text
real Source Trust Loop dogfood produces evidence
friction is recorded and triaged
concrete existing-surface friction is fixed
indbase command and artifact contracts stay stable
no new indbase core feature is added
```

## Scope

Indbase-side scope:

- planning docs
- agent route docs
- optional status/testing closeout docs after evidence exists
- optional coordination contract test
- smallest `indbase_agent` contract fix only if real dogfood proves an adapter defect

Consoler-side implementation is owned by:

- `E:\consoler\docs\planning\v4f-indbase-real-dogfood-friction-pass.md`

## Do Not Touch

- Do not add indbase commands.
- Do not change `indbase_core`.
- Do not add migrations.
- Do not add durable UX state or vault preference storage.
- Do not add title/path lookup.
- Do not add vault browser, source browser, full source viewer, Web UI, or artifact gallery.
- Do not add review/category/tag mutation, doctor repair, retrieval packages, `ask`, embeddings, generated answers, model providers, or default LLM/assisted NL behavior.
- Do not change consoler protocol/runtime/store/schema from this repository.
- Do not commit private vault paths, source snippets, raw traces, temp vaults, runtime SQLite files, logs, screenshots with private content, or local path secrets.

## Start Here

For indbase coordination:

```powershell
git status --short
rg -n "v0\\.3\\.2\\.3e|Source Trust Real Dogfood Friction Pass|Source Trust Loop|indbase_agent" AGENTS.md CONTEXT.md docs src tests
```

For consoler implementation:

```powershell
cd E:\consoler
git status --short
rg -n "v4f|real-indbase|friction|tui:indbase|indbaseVariant|artifact_view|vault_path" AGENTS.md CONTEXT.md docs packages scripts
```

## Steps

1. Read the indbase 3e plan and consoler V4f execution brief.
2. Confirm this is a new phase after 3d/V4e closeout, not an extension of intent drafting.
3. Establish baseline dogfood evidence before implementing friction fixes.
4. Require each implementation change to map to a friction register item.
5. Keep implementation in `E:\consoler` unless a focused real-dogfood failure proves an `indbase_agent` adapter defect.
6. If touching `indbase_agent`, write or update a focused adapter test before fixing.
7. Convert private-vault-only friction into a sanitized synthetic fixture before committing tests.
8. Update indbase project-status/testing docs only after V4f evidence exists.

## Validation

Documentation-only indbase changes:

```powershell
git diff --check
```

If adding an indbase coordination test:

```powershell
uv run python -m pytest tests/test_v0323e_source_trust_friction_coordination.py -q
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
pnpm --filter @consoler/tui test
pnpm test:v4f-indbase-real-dogfood-friction-pass
pnpm test:v4e-indbase-variant-intent-drafting
pnpm test:v4d-indbase-dogfood-ux
pnpm typecheck
pnpm build
git diff --check
```

Local-only real dogfood:

```powershell
cd E:\consoler
CONSOLER_KEEP_REAL_INDBASE_SMOKE=1 pnpm test:real-indbase-smoke
pnpm exec vitest run packages/tui/test/real-indbase-product-tui-smoke.test.tsx
pnpm tui:indbase --
```

Do not report manual TUI dogfood as passed if the environment cannot provide a reliable interactive terminal. Record the skip reason instead.

## Done Means

- V4f gate passes in consoler.
- Real dogfood evidence exists or an explicit local environment skip is recorded.
- A friction register records all findings, fixed items, deferred items, and out-of-scope items.
- Fixed findings are backed by tests or manual retest evidence.
- Private vault evidence is redacted and not committed.
- Indbase adapter changes, if any, are narrow and test-backed.
- No indbase core feature, new command, migration, durable UX state, vault browser, Web UI, mutation workflow, retrieval package, `ask`, embedding, generated answer, default LLM behavior, or consoler protocol/runtime/store/schema change was added.

## Unknowns

- Whether the next real manual TUI session will surface blocker friction not covered by current Ink smoke tests.
- Whether the existing `real-indbase-product-tui-smoke` is sufficient as an automated proxy for manual artifact open/back checks.
- Whether a dedicated indbase 3e coordination test is worth adding, or whether the existing 3c/3d coordination tests are enough unless an adapter defect appears.

