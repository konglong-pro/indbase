---
doc_type: phase_plan
phase_id: v0.3.2.3b
title: Consoler Read-Only Views
status: completed
canonical: true
read_by_default: false
closeout: docs/testing/archive/v0.3.2.3b-closeout.md
related_contracts:
  - docs/contracts/artifact-contract.md
  - docs/contracts/consoler-agent-boundary.md
---

# v0.3.2.3b Consoler Read-Only Views

Status: active design

Date: 2026-06-05

Phase: v0.3.2.3b artifact-first read-only view expansion after probe stabilization

Related docs:

- [v0.3.2.2 Tag/Search Governance](../v0.3.2.2/tag-search-governance.plan.md)
- [v0.3.2.3 Consoler Source Trust Probe](../v0.3.2.3/consoler-source-trust-probe.plan.md)
- [v0.3.2.3a Consoler Probe Stabilization](../v0.3.2.3a/consoler-probe-stabilization.plan.md)
- [v0.3.2.3b Consoler Read-Only Views Agent Guide](../../../agents/archive/indbase/v0.3.2.3b-consoler-read-only-views.md)
- `E:\consoler\docs\planning\v4c-indbase-probe-stabilization.md`
- `E:\consoler\docs\adr\0002-agent-owned-artifact-retrieval.md`
- [Taxonomy glossary](../../../../CONTEXT.md)

## Objective

Expand the stabilized consoler Source Trust Loop with bounded, agent-owned, read-only artifact views for operational inspection.

The phase should make dogfood inspection work through:

```text
source search -> document view
review_show -> review item view
task_show -> task view
error_show -> error view
doctor -> doctor report view
```

The goal is not a full TUI or vault browser. The goal is a reliable inspection contract for current vault state through existing consoler artifact retrieval.

## Scope

In scope:

- artifact-first read-only views owned by `indbase_agent`
- view object set: document, review item, task, error, doctor report
- current-state view semantics with explicit `view_semantics = current_vault_state`
- opaque `indbase://...` artifact URIs that do not expose paths or query languages
- fixed first-version view budgets and explicit `limits` / `truncated` fields
- stable artifact view error semantics
- list/show artifact boundary: list commands return rows only; focused show commands emit object artifacts
- focused indbase agent contract tests
- a v0.3.2.3b release gate for read-only view contracts
- local consoler smoke through `agentctl artifact-view`

Out of scope:

- Web UI, full TUI, vault browser, file browser, arbitrary URI fetch, or title/path lookup
- review resolve, review mutation, category/tag mutation, doctor repair, or output mutation
- generated answers, retrieval packages, `ask`, embeddings, semantic search, or reranking
- transition/translation/export artifact gallery
- full document revision browsing or old revision source viewer
- ingest-run or converter-run browser expansion
- consoler protocol schema changes, runtime storage changes, or renderer rewrites
- production schema migration unless a narrow read-only core query cannot be expressed safely otherwise

## Assumptions

- v0.3.2.3a has already stabilized successful ingest-to-search and document artifact retrieval.
- `indbase_agent` remains the only indbase package importing `consoler_agent_sdk`.
- `indbase_core` may expose minimal read-only helpers, but must not know about consoler artifact views.
- `metadata.vault_path` supplies vault context to the agent and display/debug metadata; it must not be encoded into the artifact URI or treated as a user-entered artifact selector.
- Existing minimal `indbase.ingest_run` artifact behavior may remain for compatibility with ingest smoke tests, but this phase does not enrich ingest-run or converter-run views.

## Current Gap

The v0.3.2.3a layer proves the document search artifact path. The current read-only view surface is still uneven:

- document view exists, but its envelope and limits are not the final 3b contract
- review, task, error, and doctor outputs are command blocks, not focused artifact views
- `doctor` does not expose a `doctor_report` artifact
- artifact URI failures currently collapse into generic artifact errors
- list commands and show commands do not yet have a documented artifact emission rule
- artifact view contract tests are concentrated in `tests/test_indbase_agent.py`, not a dedicated read-only view suite

## Read-Only View Contract

### Object set

First-version read-only view kinds:

```text
indbase.document
indbase.review_item
indbase.task
indbase.error
indbase.doctor_report
```

Do not add first-version view kinds for:

```text
indbase.ingest_run
indbase.converter_run
indbase.output_artifact
indbase.document_revision
```

Compatibility note: if `indbase.ingest_run` already exists for v0.3.2.3a smoke, do not remove it just to satisfy this phase. Leave it minimal and out of the v0.3.2.3b expansion gate.

### Artifact URI shape

Supported URI shapes:

```text
indbase://documents/{doc_id}
indbase://reviews/{review_id}
indbase://tasks/{task_id}
indbase://errors/{error_id}
indbase://doctor-reports/current
```

Rules:

- URI authority and path identify only a supported object kind and stable object id.
- Reject query strings, fragments, empty ids, extra path segments, path traversal, absolute paths, glob-like selectors, SQL-like selectors, and title/path lookup.
- The `kind` argument passed by consoler must match the URI kind.
- Do not fetch arbitrary user-entered URIs. Consoler retrieval must still flow through `agentctl artifact-view <action_id> <block_id>` or runtime `fetchArtifactView(action_id, block_id)` against an accepted artifact block.

### View semantics

Every 3b view is a current-state view:

```json
{
  "view_semantics": "current_vault_state"
}
```

The view is resolved from the current vault state when opened. It is not a durable action snapshot.

For document views produced from search artifacts, keep immutable source bindings when the originating metadata includes them:

```text
doc_id
source_revision_id or revision_id
chunk_id
source_command
vault_path
```

Do not promise that a later artifact open reproduces the exact historical search result. It shows current document state while preserving the original source binding metadata where available.

### Fixed view budgets

First-version budgets are fixed and not user-configurable:

| View | Limit |
| --- | --- |
| document source preview | 4000 chars |
| document chunks/snippets | 10 rows |
| review related rows/events | 20 rows |
| task events | 50 rows |
| error message/trace excerpt | 4000 chars |
| doctor findings | 50 rows |

Each view payload must include:

```json
{
  "limits": {},
  "truncated": false
}
```

`truncated` is true when any field or row set was shortened by the budget. Detailed per-field truncation metadata may be included, but the top-level boolean is required.

### Command artifact emission

Rules:

- `review_list`, `task_list`, and `error_list` return bounded rows only and emit no artifact blocks.
- `review_show`, `task_show`, `error_show`, and `doc_show` each emit at most one artifact block for the focused object.
- `search_sources` continues to emit at most five distinct `indbase.document` artifacts.
- `doctor` emits one `indbase.doctor_report` artifact for `indbase://doctor-reports/current`.
- Artifact view responses must not include nested artifact blocks.
- All emitted artifact blocks must include `metadata.vault_path`.

### Doctor report semantics

`indbase.doctor` remains diagnostic only.

The doctor artifact:

```text
indbase://doctor-reports/current
```

opens an ephemeral current diagnostic view:

```json
{
  "view_semantics": "current_vault_state",
  "persistence": "ephemeral_diagnostic"
}
```

Do not add a doctor run table, repair workflow, persistent diagnostic ledger, or historical doctor browser.

### Error semantics

Artifact view failures must use explicit error codes and must not return empty successful views.

Required codes:

```text
invalid_artifact_uri
unsupported_artifact_kind
artifact_not_found
artifact_scope_rejected
vault_not_initialized
view_generation_failed
```

Examples:

- no doctor findings: success, `findings: []`
- missing document id: failure, `artifact_not_found`
- URI containing a local path or extra selector: failure, `invalid_artifact_uri` or `artifact_scope_rejected`
- metadata points to a path that is not an initialized vault: failure, `vault_not_initialized`

## Implementation Plan

### Slice 1: artifact view contract helpers

Start in:

```text
src/indbase_agent/artifact_view.py
src/indbase_agent/adapter.py
```

Add:

- constants for the fixed budgets
- strict `indbase://...` parser
- object-kind to URI-kind mapping
- view envelope helper for `view_semantics`, `limits`, and `truncated`
- stable error conversion to the required codes
- nested artifact block guard for all view builders

Keep consoler-specific structures in `indbase_agent`.

### Slice 2: document view upgrade

Update the existing document view:

- source preview limit becomes 4000 chars
- include up to 10 current chunks/snippets for the current revision
- include `limits`, `truncated`, and `view_semantics`
- keep trusted category and formal tag display
- keep `doc_show` as `doc_id` only
- keep old revision content out of scope

Do not add title/path/fuzzy lookup.

### Slice 3: review, task, and error views

Add view loaders and block builders for:

```text
indbase.review_item
indbase.task
indbase.error
```

Use existing core read-only functions where available:

```text
src/indbase_core/reviews.py
src/indbase_core/tasks.py
src/indbase_core/errors.py
```

If a core helper is missing, add the narrowest read-only helper in core. Do not add consoler vocabulary to core.

### Slice 4: doctor report view

Add `indbase.doctor_report` artifact support by running the existing doctor read path on demand.

Rules:

- `indbase.doctor` command emits the doctor report artifact
- artifact view for `indbase://doctor-reports/current` re-runs current doctor checks
- response is bounded to 50 findings
- no DB writes, no repair, no persistent doctor history

### Slice 5: command artifact boundary

Update command rendering:

- show commands emit one focused artifact block
- list commands emit none
- search keeps document artifact limit at five
- doctor emits one doctor report artifact
- all artifact metadata includes `vault_path`

Do not add new commands for this phase.

### Slice 6: focused tests

Add a dedicated test file such as:

```text
tests/test_indbase_agent_readonly_views.py
```

Keep existing 3a tests in `tests/test_indbase_agent.py` passing.

Required coverage:

- each supported URI opens a non-empty bounded view
- every view includes `view_semantics`, `limits`, and `truncated`
- every emitted artifact block includes `metadata.vault_path`
- list commands emit no artifact blocks
- show commands, `search_sources`, and `doctor` emit artifact blocks according to the documented rules
- unsupported URI shape fails with `invalid_artifact_uri`
- unsupported kind fails with `unsupported_artifact_kind`
- missing object fails with `artifact_not_found`
- path/title/glob/SQL-like selectors fail explicitly
- uninitialized vault context fails with `vault_not_initialized`
- artifact view blocks contain no nested artifact blocks
- doctor report view does not write DB rows or repair vault state
- `indbase_core` still does not import `consoler_agent_sdk`

### Slice 7: release gate

Add a release gate such as:

```text
scripts/v0323b_consoler_readonly_views_release_gate.py
```

The gate should create an isolated disposable vault with synthetic or sanitized content, seed one document plus review/task/error state, run doctor, and assert all read-only view contracts.

Suggested hard-gate summary:

```json
{
  "phase": "v0.3.2.3b",
  "status": "passed",
  "hard_gates": {
    "document_view_failures": 0,
    "review_item_view_failures": 0,
    "task_view_failures": 0,
    "error_view_failures": 0,
    "doctor_report_view_failures": 0,
    "missing_vault_path_metadata": 0,
    "list_artifact_blocks": 0,
    "show_artifact_blocks_missing": 0,
    "nested_artifact_blocks": 0,
    "unbounded_view_fields": 0,
    "unstable_error_semantics": 0,
    "doctor_db_writes": 0,
    "core_consoler_imports": 0,
    "local_path_pollution": 0
  },
  "warnings": [],
  "failures": []
}
```

### Slice 8: docs

After implementation, update only the docs that changed behavior:

- `docs/testing.md`
- `docs/project-status.md`
- `docs/planning/README.md` if gate names change
- `AGENTS.md` only if phase routing or validation changes

Do not update README unless user-facing quick-start behavior changes.

## Required Validation

Focused indbase checks:

```powershell
uv run python -m pytest tests/test_indbase_agent.py tests/test_indbase_agent_readonly_views.py -q
uv run python scripts/v0323a_probe_stabilization_release_gate.py
uv run python scripts/v0323b_consoler_readonly_views_release_gate.py
uv run python -m pytest tests/test_v0322_tag_search_governance.py tests/test_v032_tag_governance.py tests/test_v031_category_taxonomy.py -q
uv run python scripts/v0322_tag_search_governance_release_gate.py
uv run python -m compileall -q src tests scripts
```

Environment checks:

```powershell
uv run python -c "print('uv-ok')"
rg -n "E:/consoler|E:\\consoler|file:///E:/consoler" uv.lock pyproject.toml
```

The `rg` command should produce no matches.

Local consoler smoke:

```powershell
cd E:\consoler
pnpm agentctl -- discover indbase
pnpm agentctl -- run indbase indbase.search_sources --args <args.json> --approve
pnpm agentctl -- artifact-view <action_id> <block_id> --json
pnpm test:real-indbase-smoke
```

Use the `action_id` and `block_id` from a real accepted artifact block. Do not fetch arbitrary user-entered URIs directly.

If the real consoler smoke cannot run because local indbase, consoler SDK, or swallow prerequisites are unavailable, it must skip explicitly with a non-misleading reason.

## Acceptance Checklist

- The read-only view object set is implemented without expanding into full TUI, Web UI, vault browser, or generated-output workflows.
- Supported artifact URIs are opaque and reject path/title/glob/SQL-like selectors.
- Document, review item, task, error, and doctor report views open through `get_artifact_view`.
- Every view is bounded and reports `view_semantics`, `limits`, and `truncated`.
- `doctor_report` is ephemeral, current-state, read-only, and non-repairing.
- List commands emit no artifact blocks.
- Show commands, search, and doctor emit artifact blocks according to the contract.
- Artifact views contain no nested artifact blocks.
- Artifact view failures use stable explicit error codes.
- Every emitted artifact block includes `metadata.vault_path`.
- `indbase_core` does not import `consoler_agent_sdk`.
- Ordinary `uv run` works without a private consoler SDK index.
- No committed file contains `E:/consoler`, `E:\consoler`, or `file:///E:/consoler` SDK paths.
- Focused adapter tests, v0.3.2.3a gate, v0.3.2.3b gate, tag/search regressions, compileall, and local consoler smoke pass or skip explicitly where optional.

## Completion Report

Report:

- changed files
- object kinds implemented
- URI shapes supported and rejected
- budget values and truncation behavior
- artifact emission behavior for list/show/search/doctor
- error code coverage
- doctor persistence/repair check
- adapter/core dependency boundary check
- local path pollution check
- indbase tests and gate summary JSON
- consoler smoke command and result
- tests not run and why
- remaining risks
- confirmation that Web UI, full TUI, vault browser, generated answers, retrieval packages, `ask`, embeddings, review/category/tag mutation, doctor repair, full revision browsing, and consoler protocol/runtime changes remain out of scope
