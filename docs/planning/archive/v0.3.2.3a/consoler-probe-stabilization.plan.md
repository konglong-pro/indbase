---
doc_type: phase_plan
phase_id: v0.3.2.3a
title: Consoler Probe Stabilization
status: completed
canonical: true
read_by_default: false
closeout: docs/testing/archive/v0.3.2.3a-closeout.md
related_contracts:
  - docs/contracts/artifact-contract.md
  - docs/contracts/consoler-agent-boundary.md
  - docs/contracts/source-search-contract.md
---

# v0.3.2.3a Consoler Probe Stabilization

Status: active design

Date: 2026-06-04

Phase: v0.3.2.3a Source Trust Loop stabilization before read-only view expansion

Related docs:

- [v0.2 Swallow Ingest Integration](../v0.2/swallow-ingest-integration.plan.md)
- [v0.3.2.2 Tag/Search Governance](../v0.3.2.2/tag-search-governance.plan.md)
- [v0.3.2.3 Consoler Source Trust Probe](../v0.3.2.3/consoler-source-trust-probe.plan.md)
- [v0.3.2.3a Consoler Probe Stabilization Agent Guide](../../../agents/archive/indbase/v0.3.2.3a-consoler-probe-stabilization.md)
- `E:\consoler\docs\planning\v4b-indbase-source-trust-probe.md`
- `E:\consoler\docs\planning\v4c-indbase-probe-stabilization.md`
- `E:\consoler\docs\adr\0006-product-variants-keep-agent-specific-ui-boundaries.md`

## Objective

Stabilize the consoler-backed Source Trust Loop before expanding search, review, doctor, or read-only artifact views.

The phase must produce clean, repeatable evidence for:

```text
agent discover -> approved ingest_file -> searchable revision/chunks/index
-> search_sources hit -> indbase.document artifact
-> doc_show / artifact_view bounded read-only inspection
```

## Scope

In scope:

- deterministic Source Trust smoke fixture or gate
- `indbase_agent` adapter tests for successful ingest-to-search behavior
- search result artifact assertions, including `metadata.vault_path`
- read-only `doc_show` / `get_artifact_view` smoke coverage for the document found by search
- explicit environment checks for ordinary `uv run` and no local consoler path pollution
- coordination with consoler conformance changes for read-only command expectations
- documentation updates for the stabilization gate

Out of scope:

- expanding search syntax, ranking, retrieval packages, `ask`, embeddings, or semantic search
- adding review mutation, tag/category mutation, doctor repair, or `review_resolve`
- adding Web UI, full TUI, vault browser, row-level tables, or new renderers
- changing consoler protocol schemas or runtime action lifecycle semantics
- making default CI depend on real private vaults, private package indexes, or real swallow availability
- committing raw private vault data or unsanitized dogfood snippets

## Current Gap

v0.3.2.3 proves the adapter and consoler lifecycle are connected. It does not yet prove a clean, successful business loop in default local smoke:

- `agentctl discover indbase` works.
- `agentctl run indbase.search_sources` can terminal-succeed.
- `agentctl run indbase.ingest_file` can terminal-succeed while business output is `completed_with_issues`.
- With `features.swallow_ingest=false`, ingest correctly records `legacy_conversion_retired` and produces no searchable revision.
- Generic `agentctl test --approve indbase.search_sources` can fail if it expects diff/artifact blocks from every executed command.

v0.3.2.3a exists to make those signals unambiguous.

## Stabilization Targets

### Target 1: deterministic Source Trust smoke

Add a deterministic smoke path that proves the successful source loop without requiring private user data.

Required evidence:

- a disposable vault is initialized
- conversion is enabled through an approved deterministic test path
- `indbase.ingest_file` writes one trusted current revision
- chunks and FTS index rows are created
- `indbase.search_sources` hits a unique fixture token
- result JSON includes `doc_id`, `revision_id`, `chunk_id`, snippet, explanation, and no filter errors
- search emits at least one `indbase.document` artifact
- every emitted document artifact includes `metadata.vault_path`
- `indbase.doc_show` for that `doc_id` returns bounded trusted metadata and preview
- `get_artifact_view` for the `indbase.document` artifact returns bounded read-only blocks

Recommended implementation split:

1. Add a Python-side deterministic gate for adapter/core behavior using sanitized synthetic content.
2. Add or extend a local-only consoler smoke for real process/lifecycle coverage.
3. Keep a real swallow smoke optional and environment-gated.

### Target 2: conformance signal cleanup

`agentctl test --approve indbase.search_sources` should not fail because a read-only search command returns markdown/table/json without diff/artifact blocks.

Acceptable approaches:

- teach consoler conformance about command expectations, such as read-only search commands not requiring diff/artifact blocks
- add a source-trust-specific smoke command that asserts the exact expected blocks for `search_sources`
- keep generic conformance broad, but make the stabilization gate use a more precise source-trust smoke

Do not weaken event schema, terminal event, history, trace, replay, approval, or manifest checks.

### Target 3: environment stability

Ordinary indbase development must not depend on consoler packaging state.

Required evidence:

- `uv run python -c "print('uv-ok')"` works in `E:\indbase`
- `uv.lock` and `pyproject.toml` do not contain `E:/consoler`, `E:\consoler`, or `file:///E:/consoler`
- `consoler-agent` remains a safe optional packaging hook while the SDK is provided by consoler runtime or explicit local test setup
- adapter tests do not require committing local wheel paths

## Implementation Plan

### Slice 1: deterministic indbase gate

Add a gate such as:

```text
scripts/v0323a_probe_stabilization_release_gate.py
```

The gate should:

- create an isolated disposable vault
- use synthetic fixture content only
- enable the approved deterministic conversion path
- call the adapter/core path for `indbase.ingest_file`
- assert revision/chunk/index/search success
- assert `doc_show` and document artifact view are bounded and read-only
- assert no `legacy_conversion_retired` appears in the successful path
- print stable JSON summary
- exit nonzero on hard failures

Suggested hard-gate fields:

```json
{
  "phase": "v0.3.2.3a",
  "status": "passed",
  "hard_gates": {
    "uv_run_available": true,
    "local_path_pollution": 0,
    "manifest_schema_failures": 0,
    "successful_ingest_revisions": 1,
    "search_hits": 1,
    "missing_document_artifacts": 0,
    "artifact_metadata_vault_path_missing": 0,
    "doc_show_failures": 0,
    "artifact_view_failures": 0,
    "legacy_conversion_retired_in_success_path": 0
  },
  "warnings": [],
  "failures": []
}
```

### Slice 2: focused adapter tests

Extend `tests/test_indbase_agent.py` or add a focused companion test.

Cover:

- successful ingest emits searchable source state under deterministic conversion
- search after ingest returns the expected document and chunk
- search result document artifact count is bounded
- document artifacts include `metadata.vault_path`
- `doc_show` and `get_artifact_view` return bounded read-only blocks
- invalid filters still fail with structured details
- valid empty search still succeeds
- `legacy_conversion_retired` remains visible when `features.swallow_ingest=false`

### Slice 3: consoler smoke contract

Coordinate with `E:\consoler\docs\planning\v4c-indbase-probe-stabilization.md`.

The indbase side should provide whatever fixture, args JSON, or gate output the consoler smoke needs. Do not make consoler read the indbase vault directly; consoler should interact through the agent protocol.

### Slice 4: environment checks

Add explicit checks to the gate or focused tests:

```powershell
uv run python -c "print('uv-ok')"
rg -n "E:/consoler|E:\\consoler|file:///E:/consoler" uv.lock pyproject.toml
```

The second command should produce no matches.

### Slice 5: docs

After implementation, update:

- `docs/testing.md`
- `docs/project-status.md`
- `docs/planning/README.md` if command names or gates change
- `AGENTS.md` only if the phase routing or validation command changes

Do not update README unless user-facing quick-start behavior changes.

## Required Validation

Focused indbase checks:

```powershell
uv run python -m pytest tests/test_indbase_agent.py -q
uv run python -m pytest tests/test_v0322_tag_search_governance.py tests/test_v032_tag_governance.py tests/test_v031_category_taxonomy.py -q
uv run python scripts/v0322_tag_search_governance_release_gate.py
uv run python -m compileall -q src tests scripts
```

New gate after implementation:

```powershell
uv run python scripts/v0323a_probe_stabilization_release_gate.py
```

Environment checks:

```powershell
uv run python -c "print('uv-ok')"
rg -n "E:/consoler|E:\\consoler|file:///E:/consoler" uv.lock pyproject.toml
```

Cross-repo smoke after consoler v4c implementation:

```powershell
cd E:\consoler
pnpm agentctl -- discover indbase
pnpm agentctl -- test indbase --command indbase.search_sources --args <args.json> --approve --json
pnpm agentctl -- run indbase indbase.search_sources --args <args.json> --approve
pnpm agentctl -- trace <action_id> --json
```

Optional real swallow smoke:

```powershell
cd E:\consoler
pnpm test:real-indbase-smoke
```

Run the optional real swallow smoke only when the local environment has the required indbase, consoler SDK, and swallow setup.

## Acceptance Checklist

- Ordinary `uv run` works without a private consoler SDK index.
- No committed file contains a local consoler SDK path.
- The source trust stabilization gate passes.
- A successful synthetic ingest writes one trusted current revision.
- Chunks and FTS index rows exist for the ingested source.
- `search_sources` hits the synthetic token and returns trusted source bindings.
- Search emits bounded `indbase.document` artifacts with `metadata.vault_path`.
- `doc_show` for the search result document is bounded and read-only.
- Artifact view for the search result document is bounded and read-only.
- Disabled swallow still fails visibly with `legacy_conversion_retired`.
- `agentctl discover indbase` passes.
- The chosen consoler search smoke passes without misleading generic conformance failures.
- Focused adapter, tag/search regression, gate, compileall, consoler TUI, typecheck, build, and diff checks pass.

## Completion Report

Report:

- changed files
- deterministic smoke strategy used
- whether real swallow smoke ran or was skipped
- gate summary JSON
- consoler smoke command and result
- adapter/core dependency boundary check
- local path pollution check
- tests run
- tests not run and why
- remaining risks
- confirmation that search expansion, review mutation, doctor repair, full UI, Web UI, retrieval packages, `ask`, embeddings, and protocol/runtime schema changes remain out of scope
