---
doc_type: phase_plan
phase_id: v0.3.2.3c
title: Consoler Variant Dogfood UX
status: completed
canonical: true
read_by_default: false
closeout: docs/testing.md#latest-v0323c--consoler-v4d-closeout
related_contracts:
  - docs/contracts/artifact-contract.md
  - docs/contracts/consoler-agent-boundary.md
---

# v0.3.2.3c Consoler Variant Dogfood UX

Status: active design

Date: 2026-06-05

Phase: v0.3.2.3c consoler-owned dogfood UX for the Source Trust Loop

Related docs:

- [v0.3.2.3 Consoler Source Trust Probe](../v0.3.2.3/consoler-source-trust-probe.plan.md)
- [v0.3.2.3a Consoler Probe Stabilization](../v0.3.2.3a/consoler-probe-stabilization.plan.md)
- [v0.3.2.3b Consoler Read-Only Views](../v0.3.2.3b/consoler-read-only-views.plan.md)
- [v0.3.2.3c Consoler Variant Dogfood UX Agent Guide](../../../agents/archive/indbase/v0.3.2.3c-consoler-variant-dogfood-ux.md)
- `E:\consoler\docs\planning\v4d-indbase-dogfood-ux.md`
- `E:\consoler\docs\adr\0006-product-variants-keep-agent-specific-ui-boundaries.md`
- `E:\consoler\CONTEXT.md`
- [indbase glossary](../../../../CONTEXT.md)

## Objective

Make the existing indbase Source Trust Loop usable through the checked-in consoler indbase Console Variant.

The primary dogfood path is:

```text
pnpm tui:indbase --
-> choose or enter vault_path
-> doctor
-> ingest_file or use prepared synthetic source state
-> search_sources
-> open document artifact view
-> inspect review/task/error state
-> open focused read-only views
-> return to history/trace
```

This is a UX dogfood phase, not an indbase core feature phase.

## Scope

In scope:

- consoler-owned indbase variant action ordering, labels, empty states, and hints
- complete Source Trust Loop action surface in the variant
- session-local `vault_path` form prefill inside the TUI
- composed use of existing home, schema form, action timeline, result blocks, artifact view panel, history, and trace surfaces
- discoverable artifact open/back path
- deterministic real-agent dogfood smoke using disposable synthetic vault state
- consoler-side v4d gate, coordinated with indbase v0.3.2.3c
- indbase-side documentation and agent routing only

Out of scope:

- new indbase commands, indbase core changes, migrations, schema changes, or new durable state
- consoler protocol, runtime lifecycle, store schema, replay, transport, or Python SDK changes
- Web UI, full vault browser, artifact browser, source file browser, revision browser, or arbitrary URI fetch
- review resolve, review/category/tag mutation, doctor repair, output/generated workflow, translation UI, or export gallery
- retrieval packages, `ask`, embeddings, generated answers, semantic search, or reranking
- natural-language / intent drafting as the primary path
- LLM-assisted UX, chat, multi-action workflow, provider setup, or model calls
- default CI dependency on real private vaults, real swallow, or `E:\indbase`

## Accepted Decisions

### Ownership

v0.3.2.3c maps to consoler v4d and is implemented primarily in `E:\consoler`.

indbase responsibilities:

- keep `indbase_agent` command and artifact contracts stable
- keep v0.3.2.3a and v0.3.2.3b gates available
- document phase boundaries and coordination points

consoler responsibilities:

- implement the TUI product variant changes
- add focused TUI tests and gate script
- keep the variant within existing action lifecycle and artifact retrieval boundaries

### Natural language boundary

NL/intent drafting is not the 3c core path.

Existing NL behavior may receive regression coverage only. A later stage, such as `v0.3.2.3d Indbase Variant Intent Drafting`, can add deterministic variant-scoped intent drafting after the explicit walkthrough is proven usable.

### Primary walkthrough

3c optimizes one single-source trust walkthrough instead of making an all-command dashboard.

The UI may still expose all approved commands, but ordering and empty-state copy should guide users through the Source Trust Loop.

### Variant vault context

The TUI may remember the last successful `vault_path` inside the current TUI session and prefill it into later forms.

Rules:

- session-local only
- user can override
- no runtime SQLite persistence
- no config file
- no history-derived default
- no cwd inference
- no vault scanning or vault list

### Surface composition

Use existing consoler surfaces:

- variant home
- schema form
- action timeline
- result blocks
- artifact view panel
- history
- trace

Do not add a Source Trust wizard, parallel workflow state machine, or product-specific runtime lifecycle.

### Artifact UX

3c should make artifact opening discoverable:

- result artifact blocks clearly show they are openable
- `Enter` opens the selected artifact view
- `Esc` returns to the originating timeline or trace
- product labels are visible
- raw `indbase://...` remains audit/detail information, not the main product title
- fetched artifact view content is not persisted into consoler history or replay

### Test boundary

The hard UX evidence should be deterministic and local:

- use disposable synthetic vault state
- use the real agent path where practical
- keep real private vault dogfood manual-only
- keep real swallow optional or environment-gated

## Source Trust Loop Action Surface

The consoler indbase variant should expose these ten commands:

```text
indbase.doctor
indbase.ingest_file
indbase.search_sources
indbase.doc_show
indbase.review_list
indbase.review_show
indbase.task_list
indbase.task_show
indbase.error_list
indbase.error_show
```

Suggested product order:

1. Check vault
2. Import file
3. Search trusted sources
4. Open document by id
5. Review queue
6. Review item
7. Task list
8. Task details
9. Error list
10. Error details

Only `indbase.ingest_file` is a write action.

Do not add category/tag mutation, review resolution, doctor repair, retrieval, or ask commands to this surface.

## Implementation Plan

The actual implementation belongs in `E:\consoler\docs\planning\v4d-indbase-dogfood-ux.md`.

Indbase-side work should stay limited to:

1. Keep this planning document and agent guide current.
2. Update `AGENTS.md` phase routing.
3. Keep v0.3.2.3a and v0.3.2.3b gates available for consoler smoke coordination.
4. If consoler v4d exposes a true adapter contract bug, fix only the smallest `indbase_agent` issue and update the relevant v0.3.2.3b contract tests.

Do not change `indbase_core` for 3c unless a proven adapter contract bug cannot be fixed in `indbase_agent`.

## Required Validation

Indbase coordination checks:

```powershell
uv run python -m pytest tests/test_v0323c_indbase_coordination.py -q
uv run python -m pytest tests/test_indbase_agent.py tests/test_indbase_agent_readonly_views.py -q
uv run python scripts/v0323a_probe_stabilization_release_gate.py
uv run python scripts/v0323b_consoler_readonly_views_release_gate.py
uv run python -m compileall -q src tests scripts
```

Consoler implementation checks, run from `E:\consoler` after v4d implementation:

```powershell
pnpm --filter @consoler/tui test
pnpm test:v4d-indbase-dogfood-ux
pnpm test:real-indbase-smoke
pnpm typecheck
pnpm build
git diff --check
```

If `pnpm test:real-indbase-smoke` cannot run because local indbase, consoler SDK, or swallow prerequisites are unavailable, it must skip explicitly with a non-misleading reason.

## Acceptance Checklist

- `pnpm tui:indbase --` exposes the full ten-command Source Trust Loop action surface.
- Product order prioritizes the single-source trust walkthrough.
- `vault_path` is remembered for the current TUI session and can be overridden.
- Artifact blocks are clearly openable, and `Enter` / `Esc` open and return without losing context.
- History and trace remain variant-scoped in product mode.
- The TUI does not require users to understand raw protocol command names for the main path.
- Raw IDs and `indbase://...` remain available on audit/debug surfaces.
- No indbase core capability, command, mutation, schema, or provider behavior is added.
- No consoler protocol/runtime/store/schema behavior changes are needed.
- Default CI remains fake-agent safe; real indbase smoke remains local-only or environment-gated.
- NL/intent drafting is not part of the 3c completion criteria.

## Completion Report

Report:

- indbase files changed
- consoler files changed
- action surface and order
- variant UX config fields added, if any
- vault context behavior
- artifact open/back behavior
- copy hygiene result
- consoler v4d gate result
- indbase v0.3.2.3a/3b gate results
- real-indbase smoke result or explicit skip reason
- tests not run and why
- remaining risks
- confirmation that Web UI, full vault browser, new indbase commands, indbase core changes, review/category/tag mutations, doctor repair, generated answers, retrieval packages, `ask`, embeddings, LLM-assisted NL, and consoler protocol/runtime changes remain out of scope
