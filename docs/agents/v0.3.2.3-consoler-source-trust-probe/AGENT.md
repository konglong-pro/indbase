# v0.3.2.3 Consoler Source Trust Probe Agent Guide

Read this before implementing the indbase side of the consoler Source Trust Loop probe.

Canonical spec:

- `docs/planning/v0.3.2.3-consoler-source-trust-probe.md`

Related consoler docs:

- `E:\consoler\docs\planning\v4b-indbase-source-trust-probe.md`
- `E:\consoler\docs\adr\0006-product-variants-keep-agent-specific-ui-boundaries.md`
- `E:\consoler\docs\planning\v4a-versioned-python-agent-sdk.md`
- `E:\consoler\docs\adr\0005-versioned-python-agent-sdk.md`

Required indbase context:

- `AGENTS.md`
- `CONTEXT.md`
- `docs/project-status.md`
- `docs/testing.md`
- `docs/planning/v0.3.1-taxonomy-category-foundation.md`
- `docs/planning/v0.3.2-tag-governance-foundation.md`
- `docs/planning/v0.3.2.1-tag-harness-hardening.md`
- `docs/planning/v0.3.2.2-tag-search-governance.md`
- `docs/agents/v0.3.2.2-tag-search-governance/AGENT.md`

## Objective

Expose a narrow consoler adapter for the trusted source loop:

```text
doctor -> ingest_file -> search_sources
-> review/task/error visibility -> doc_show
```

The adapter must let consoler dogfood indbase without bypassing indbase core services or weakening source, category, tag, search, review, task, error, and revision boundaries.

## Scope

Implement only:

- `src/indbase_agent` source restoration or minimal adapter implementation
- optional `consoler-agent` package extra
- indbase wheel inclusion for `indbase_agent`
- the ten first-version consoler commands
- bounded `indbase://...` artifact views
- focused adapter tests
- optional local cross-repo smoke notes

Do not implement:

- Web UI
- full TUI inside indbase
- consoler protocol/runtime/TUI/SDK changes
- output/generated loop
- transition export UI
- translation UI
- retrieval packages
- `ask`
- category/tag governance mutations
- `review_resolve`
- full source file viewer
- title/path/fuzzy document lookup
- direct CLI shell-out from the adapter

## Start Here

Before coding:

```powershell
git status --short
rg -n "indbase_agent|consoler_agent_sdk|AgentManifest|indbase://" src tests pyproject.toml docs
rg -n "search_chunks|SearchOptions|resolve_tag_filter|review|task|error|doc show" src tests
```

Primary areas:

- `src/indbase_agent`
- `src/indbase_core/search.py`
- `src/indbase_core/tag_search.py`
- `src/indbase_core/category_taxonomy.py`
- `src/indbase_core/doctor.py`
- `src/indbase_cli/main.py`
- `pyproject.toml`

Likely tests:

- `tests/test_indbase_agent.py`
- `tests/test_v0322_tag_search_governance.py`
- `tests/test_v032_tag_governance.py`
- `tests/test_v031_category_taxonomy.py`

## Do Not Touch

- Do not decompile or depend on `src/indbase_agent/__pycache__`.
- Do not import `consoler_agent_sdk` from `indbase_core`.
- Do not add consoler SDK to default dependencies or dev dependencies.
- Do not commit `file:///E:/consoler` package references.
- Do not make the committed `consoler-agent` extra depend on an unpublished SDK
  package; the consoler runtime or local test setup must provide the SDK.
- Do not make regular pytest depend on `E:\consoler`.
- Do not mutate existing category/tag/search governance behavior unless a focused regression proves the adapter needs a narrow core helper.

## Command Contract

Expose exactly this first-version surface unless the planning doc is updated:

```text
indbase.doctor
indbase.ingest_file
indbase.search_sources
indbase.review_list
indbase.review_show
indbase.task_list
indbase.task_show
indbase.error_list
indbase.error_show
indbase.doc_show
```

Rules:

- `indbase.ingest_file` is the only write command.
- `indbase.ingest_file` is one local file only and keeps `probe_readonly` preview approval.
- `indbase.search_sources` supports `vault_path`, `query`, `category?`, `tag?`, and `top_k?`.
- `query = ""` is valid only with `category` or `tag`.
- Invalid filters fail the action with structured `AgentError.details`.
- Valid empty search succeeds.
- `indbase.doc_show` accepts `doc_id` only.
- Review/task/error/doc commands are read-only.

## Steps

1. Recover or reimplement `src/indbase_agent` without using bytecode.
2. Add optional package extra `consoler-agent = []` as a packaging hook while the
   SDK remains private/unpublished.
3. Include `src/indbase_agent` in the wheel package list.
4. Implement manifest discovery for the ten first-version commands.
5. Implement command adapters by calling `indbase_core` services directly.
6. Add bounded result blocks and `indbase://...` artifacts with `metadata.vault_path`.
7. Add focused adapter tests and dependency-boundary tests.
8. Run focused indbase validation.
9. Run optional consoler cross-repo smoke only after the indbase tests pass.

## Validation

Focused checks:

```powershell
uv run python -m pytest tests/test_indbase_agent.py -q
uv run python -m pytest tests/test_v0322_tag_search_governance.py tests/test_v032_tag_governance.py tests/test_v031_category_taxonomy.py -q
uv run python scripts/v0322_tag_search_governance_release_gate.py
uv run python -m compileall -q src tests scripts
```

Optional local smoke after implementation:

```powershell
cd E:\consoler
pnpm agentctl -- discover indbase
pnpm agentctl -- test indbase --command indbase.search_sources --args <args.json> --json
```

Do not report optional smoke as passed unless it was actually run against a disposable or explicitly selected vault.

## Done Means

- `indbase_agent` source exists and imports `consoler_agent_sdk` only at the
  adapter layer.
- `indbase_core` has no consoler SDK import.
- The first-version manifest exposes the accepted command surface.
- `ingest_file` is the only write command.
- Search/filter behavior delegates to trusted v0.3.2.2 semantics.
- `doc_show` is bounded and `doc_id` only.
- Review/task/error commands are read-only.
- Adapter tests and focused source/tag/category search checks pass.
- Any skipped cross-repo smoke is explicitly reported.

## Unknowns

- Whether old `indbase_agent` source can be recovered quickly.
- Where the private/internal `consoler_agent_sdk` package will be provided from
  in developer environments and consoler runtime execution.
- Whether existing core helpers expose every read-only task/error/doc field cleanly enough for the adapter without adding a narrow helper.
