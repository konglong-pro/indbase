---
doc_type: phase_plan
phase_id: v0.3.2.3d
title: Indbase Variant Intent Drafting
status: completed
canonical: true
read_by_default: false
closeout: docs/testing.md#latest-v0323d--consoler-v4e-closeout
related_contracts:
  - docs/contracts/intent-draft-contract.md
  - docs/contracts/consoler-agent-boundary.md
---

# v0.3.2.3d Indbase Variant Intent Drafting

Status: closeout passed

Date: 2026-06-06

Phase: v0.3.2.3d coordination for consoler-owned deterministic indbase variant intent drafting

Related docs:

- [v0.3.2.3c Consoler Variant Dogfood UX](../v0.3.2.3c/consoler-variant-dogfood-ux.plan.md)
- [v0.3.2.3b Consoler Read-Only Views](../v0.3.2.3b/consoler-read-only-views.plan.md)
- [v0.3.2.3a Consoler Probe Stabilization](../v0.3.2.3a/consoler-probe-stabilization.plan.md)
- [v0.3.2.3d Agent Guide](../../../agents/archive/indbase/v0.3.2.3d-indbase-variant-intent-drafting.md)
- `E:\consoler\docs\planning\v4e-indbase-variant-intent-drafting.md`
- `E:\consoler\docs\adr\0003-natural-language-intent-drafting.md`
- `E:\consoler\docs\adr\0006-product-variants-keep-agent-specific-ui-boundaries.md`
- `E:\consoler\CONTEXT.md`
- [indbase glossary](../../../../CONTEXT.md)

## Objective

Coordinate `consoler` V4e so `pnpm tui:indbase --` can use deterministic, indbase-variant-scoped intent drafting for the existing Source Trust Loop.

The product path is:

```text
single-shot natural-language input
-> consoler maps within the indbase Console Variant only
-> one reviewable Source Trust Loop action candidate
-> editable schema form with conservative prefilled_args
-> existing prepare / preview / approval / execute lifecycle
```

This phase is a consoler-owned form-prefill UX phase. It is not an indbase core feature phase.

## Scope

In scope for the overall phase:

- deterministic command matching for the ten-command Source Trust Loop action surface
- conservative field prefill for obvious paths, queries, explicit filters, and object IDs
- TUI form-layer merge of session-local `vault_path` prefill
- copy hygiene for indbase variant hints, including readable Chinese hints
- focused deterministic tests and a consoler V4e gate
- indbase-side planning, agent routing, and coordination documentation

In scope for `E:\indbase` only:

- this planning document
- `docs/agents/archive/indbase/v0.3.2.3d-indbase-variant-intent-drafting.md`
- `AGENTS.md` routing
- optional coordination/status/testing docs after implementation evidence exists
- optional coordination test proving no new indbase command surface is required

Out of scope:

- new indbase commands
- `indbase_core` changes
- `indbase_agent` natural-language parsing
- migrations or durable UX state
- consoler protocol, runtime store schema, replay, transport, or Python SDK changes
- default LLM-assisted intent drafting, model calls, prompt files, provider setup, or chat
- multi-action workflow such as "import then search"
- filesystem reads, cwd inference, history inference, vault discovery, or "latest result" lookup during intent mapping
- Web UI, vault browser, source browser, artifact gallery, arbitrary URI fetch, review/category/tag mutation UI, doctor repair, retrieval packages, `ask`, embeddings, or generated answers

## Accepted Decisions

### Deterministic only

3d uses deterministic intent drafting as the completion path.

Existing V3c assisted/LLM intent gates may remain adjacent regression checks, but they are not 3d completion criteria. A real provider, network access, model credentials, or prompt snapshot must not be required.

### Ten-command action surface

The intent scope must cover the same ten Source Trust Loop commands closed in 3c:

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

Tier A acceptance: every command can be selected by clear deterministic product-language input.

Tier B acceptance: only low-risk fields are prefilled. Missing or ambiguous fields route to an editable form instead of being guessed.

### Implementation ownership

`consoler` owns implementation:

- generic schema-aware extraction in `packages/runtime/src/intent-draft*.ts`
- indbase variant hints and copy in `packages/tui/src/variants/indbase.ts`
- TUI form prefill behavior in `packages/tui`
- V4e gate script and package script

`indbase` owns coordination only:

- stable adapter command/artifact contracts
- phase boundary docs
- optional coordination tests

Indbase must not receive natural-language input or implement an indbase-specific NL parser.

### Vault context layering

Runtime `draftIntent({ text, scope })` must not know or infer session vault context.

The TUI may merge session-local `vault_path` into the schema form only after a runtime candidate or partial candidate is selected. This merge is form convenience, not mapper inference, and all fields remain editable.

### Search and governed filters

`search_sources.query` may be prefilled from quoted text or clearly indicated search/query text.

`tag` and `category` may be prefilled only from explicit low-ambiguity syntax, such as:

```text
tag:<ref>
category:<ref>
with tag <ref>
in category <ref>
```

Do not infer governed tag/category filters from vague natural language. Filter validity remains the responsibility of the indbase search command during the normal lifecycle.

### Object ID extraction

`doc_id`, `review_id`, `task_id`, and `error_id` may be extracted when the action intent is already unique or the token format is object-kind-specific.

Do not read history, trace, artifact state, or "last search result" to infer IDs.

### Clarification UX

`missing_required_args` with a partial candidate should open the matched schema form with a deterministic notice.

`no_match` and `ambiguous_command` should stay on the product home with the explicit task list available.

`ambiguous_args` may open the form only when the action is already clear; otherwise it stays on the home fallback.

Clarification is not a chat loop.

### Copy hygiene

3d must repair indbase variant mojibake in touched intent hints and deterministic keyword lists. This is a narrow product-variant copy hygiene task, not repository-wide localization or an i18n framework.

## Implementation Plan

The implementation belongs in `E:\consoler\docs\planning\v4e-indbase-variant-intent-drafting.md`.

Indbase-side work should stay limited to:

1. Keep this planning document and the 3d agent guide current.
2. Update `AGENTS.md` phase routing.
3. Keep v0.3.2.3a, v0.3.2.3b, and v0.3.2.3c coordination checks available.
4. Add an indbase coordination test only if it stays documentation/manifest-contract focused and does not require consoler runtime code.
5. If V4e exposes a true adapter contract bug, fix only the smallest `indbase_agent` issue and update the relevant adapter/read-only view tests.

Do not change `indbase_core` for 3d unless a proven existing read-only adapter defect cannot be fixed in `indbase_agent`.

## Required Validation

Indbase documentation/coordination checks:

```powershell
git diff --check
```

If an indbase coordination test is added later:

```powershell
uv run python -m pytest tests/test_v0323d_indbase_intent_coordination.py -q
uv run python -m compileall -q src tests scripts
```

If any adapter code is touched:

```powershell
uv run python -m pytest tests/test_indbase_agent.py tests/test_indbase_agent_readonly_views.py -q
uv run python scripts/v0323a_probe_stabilization_release_gate.py
uv run python scripts/v0323b_consoler_readonly_views_release_gate.py
uv run python -m compileall -q src tests scripts
```

Consoler implementation checks, run from `E:\consoler` after V4e implementation:

```powershell
pnpm --filter @consoler/runtime test
pnpm --filter @consoler/tui test
pnpm test:v4e-indbase-variant-intent-drafting
pnpm test:v3b-intent-gate
pnpm typecheck
pnpm build
git diff --check
```

Adjacent regression checks when shared assisted intent paths are touched:

```powershell
pnpm test:v3c-assisted-intent-gate
pnpm test:v3c-tui-assisted-intent-gate
```

Real indbase smoke remains optional/local-only unless the real-agent smoke script changes:

```powershell
pnpm test:real-indbase-smoke
```

## Acceptance Checklist

- Natural-language drafting is deterministic, single-shot, variant-scoped, and offline.
- The ten-command Source Trust Loop action surface is covered for clear action selection.
- `prefilled_args` are conservative and editable.
- Missing/ambiguous values route to form fallback, not guessing.
- TUI session `vault_path` prefill is merged only at the form layer.
- `tag` and `category` filters are extracted only from explicit low-ambiguity syntax.
- Object IDs are not inferred from history, trace, artifact state, or "latest result".
- Mojibake in touched indbase variant hints/copy is repaired and covered by copy hygiene tests.
- Raw natural language and intent drafts are not persisted.
- No action, approval, preview, execution, trace, history, or artifact retrieval is created by NL submit alone.
- No indbase core, schema, command, or adapter feature is added unless a focused adapter bug is proven.
- No consoler protocol/runtime store schema, Python SDK, Web UI, vault browser, mutation UI, retrieval package, `ask`, embedding, generated answer, or default LLM path is added.

## Closeout Evidence

Latest closeout: 2026-06-06.

PR/base strategy:

```text
indbase PR #1
  URL: https://github.com/konglong-pro/indbase/pull/1
  head: feat/v031-taxonomy-foundation
  base: main
  merge state: CLEAN

consoler PR #4
  URL: https://github.com/konglong-pro/consoler/pull/4
  head: feat/v2-artifact-retrieval
  base: feat/v1k-v1l-on-main
  merge state: CLEAN
```

`consoler` PR #4 is intentionally treated as a stacked PR. Keep the stack unless the branch series is intentionally flattened; merge its base branch before merging PR #4.

GitHub CI evidence:

```text
E:\consoler / PR #4
CI / typecheck and test
  -> passed
CI / windows focused
  -> passed

E:\indbase / PR #1
A/B — pytest + compileall
  -> passed
C — v0.2 deterministic vault + doctor negative
  -> passed
C2 — v0.3.1 taxonomy N1 gates
  -> passed
C2b — v0.3.2 tag governance gate
  -> passed
C2c — v0.3.2.1 tag harness gate
  -> passed
C2d — v0.3.2.2 tag/search governance gate
  -> passed
C2e — v0.3.2.3 consoler coordination gates
  -> passed
C3 — v0.3.2 retrieval release gate
  -> passed
C4 — v0.3.3 retrieval eval release gate
  -> passed
D — real swallow smoke
  -> passed
D — real Node transition smoke
  -> passed
retired template guard
  -> passed
v0.3.1 — taxonomy category gate
  -> passed
```

Local validation evidence:

```text
E:\indbase
uv run python -m pytest
  -> 345 passed, 2 skipped
uv run python -m pytest tests/test_v0323c_indbase_coordination.py tests/test_v0323d_indbase_intent_coordination.py -q
  -> 5 passed
uv run python -m compileall -q src tests scripts
  -> passed
git diff --check
  -> passed with LF/CRLF warnings only

E:\consoler
pnpm test:v2-release-gate
  -> passed
pnpm test:v3c-assisted-intent-gate
  -> passed
pnpm test:v3c-tui-assisted-intent-gate
  -> passed
pnpm test:v4d-indbase-dogfood-ux
  -> passed
pnpm test:v4e-indbase-variant-intent-drafting
  -> passed
pnpm test:python-sdk-package
  -> passed
git diff --check
  -> passed with LF/CRLF warnings only
```

Product dogfood evidence:

```text
E:\consoler
CONSOLER_KEEP_REAL_INDBASE_SMOKE=1 pnpm test:real-indbase-smoke
  -> real indbase smoke passed
pnpm exec vitest run packages/tui/test/real-indbase-product-tui-smoke.test.tsx
  -> 1 test passed
```

Dogfood coverage:

- Real disposable vault: `doctor`, `search_sources`, `ingest_file`, duplicate skip/continue, trace, replay, and artifact-view retrieval passed through the real `indbase` agent.
- Product TUI path: selected `Check knowledge base status`, filled the vault field, approved execution, observed success, opened variant-scoped history/trace, opened an artifact view, and returned to trace.
- Focused V4e tests continue to cover deterministic NL form prefill and no direct execution from NL submit.

Operator note: the closeout environment did not expose an interactive terminal PTY, so the product TUI pass used the checked-in Ink smoke test rather than a manually typed `pnpm tui:indbase --` session. The test uses the same `App`, `indbaseVariant`, real manifest discovery, and runtime store.

## Completion Report

Report:

- indbase files changed
- consoler files changed
- commands and fields covered
- clarification behavior
- session `vault_path` behavior
- copy hygiene result
- V4e gate result
- V3b deterministic gate result
- V3c assisted regression result if run
- indbase coordination/adapter gate results if run
- tests not run and why
- remaining risks
- confirmation that no indbase core feature, consoler protocol/runtime store schema change, Web UI, vault browser, mutation UI, retrieval package, `ask`, embedding, generated answer, or default LLM behavior was added
