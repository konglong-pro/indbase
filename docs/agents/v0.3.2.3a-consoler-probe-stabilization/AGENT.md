# v0.3.2.3a Consoler Probe Stabilization Agent Guide

Read this before implementing v0.3.2.3a stabilization work.

Canonical spec:

- `docs/planning/v0.3.2.3a-consoler-probe-stabilization.md`

Required context:

- `AGENTS.md`
- `CONTEXT.md`
- `docs/project-status.md`
- `docs/testing.md`
- `docs/planning/v0.2-swallow-ingest-integration.md`
- `docs/planning/v0.3.2.2-tag-search-governance.md`
- `docs/planning/v0.3.2.3-consoler-source-trust-probe.md`
- `docs/agents/v0.3.2.3-consoler-source-trust-probe/AGENT.md`
- `E:\consoler\docs\planning\v4c-indbase-probe-stabilization.md`

## Objective

Turn the current consoler probe into a stable Source Trust smoke with clean pass/fail signals.

Success means:

```text
ingest_file succeeds in a deterministic fixture vault
search_sources finds the ingested source
doc_show and document artifact view are bounded/read-only
consoler smoke no longer fails on irrelevant generic block expectations
ordinary uv run remains usable
```

## Scope

Implement only stabilization:

- deterministic source trust gate
- focused adapter tests
- successful ingest-to-search fixture path
- artifact metadata and artifact view assertions
- environment checks for local path pollution and `uv run`
- docs/testing and project status updates after implementation

Do not implement:

- new search modes
- richer review, doctor, or artifact view features
- review/category/tag mutations
- `ask`, retrieval packages, embeddings, semantic search, or generated answers
- Web UI, full TUI, file browser, or row-level table interaction
- consoler protocol schema changes
- production schema migration unless a narrow existing-state read requires it and the planning doc is updated

## Start Here

Before coding:

```powershell
git status --short
rg -n "indbase_agent|search_sources|get_artifact_view|doc_show|legacy_conversion_retired|swallow_ingest" src tests scripts docs
rg -n "E:/consoler|E:\\consoler|file:///E:/consoler" uv.lock pyproject.toml
```

Primary indbase files:

- `src/indbase_agent/adapter.py`
- `src/indbase_agent/artifact_view.py`
- `src/indbase_agent/ingest_probe.py`
- `src/indbase_agent/ingest_state_snapshot.py`
- `src/indbase_core/conversion.py`
- `src/indbase_core/ingest.py`
- `src/indbase_core/search.py`
- `src/indbase_core/tag_search.py`
- `src/indbase_core/config.py`
- `tests/test_indbase_agent.py`

Likely new file:

- `scripts/v0323a_probe_stabilization_release_gate.py`

## Do Not Touch

- Do not import `consoler_agent_sdk` from `indbase_core`.
- Do not shell out to `indb` from `indbase_agent`.
- Do not commit local wheel paths, `file:///E:/consoler`, private index URLs, or generated smoke vaults.
- Do not add consoler SDK to default dependencies or dev dependencies.
- Do not make regular pytest require `E:\consoler`.
- Do not mutate old revisions or bypass core durable operations.
- Do not weaken the visible `legacy_conversion_retired` behavior for disabled swallow ingest.

## Steps

1. Add a deterministic fixture strategy for successful conversion in tests or the stabilization gate.
2. Add a source trust gate that creates a disposable vault and verifies ingest -> revision -> chunks -> FTS -> search hit.
3. Assert search result bindings include `doc_id`, `revision_id`, `chunk_id`, and snippet.
4. Assert search result document artifacts are bounded and include `metadata.vault_path`.
5. Assert `doc_show` and `get_artifact_view` return bounded read-only blocks for the found document.
6. Assert disabled swallow still produces visible `legacy_conversion_retired`.
7. Add environment checks for ordinary `uv run` and no local consoler path pollution.
8. Coordinate with consoler v4c so read-only search conformance uses correct block expectations.
9. Update docs/testing and project status after implementation.

## Validation

Focused checks:

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

Cross-repo smoke after consoler v4c work:

```powershell
cd E:\consoler
pnpm agentctl -- discover indbase
pnpm agentctl -- test indbase --command indbase.search_sources --args <args.json> --approve --json
pnpm agentctl -- run indbase indbase.search_sources --args <args.json> --approve
pnpm agentctl -- trace <action_id> --json
```

## Done Means

- The new v0.3.2.3a gate passes locally.
- Successful ingest-to-search is proven with synthetic/sanitized fixture content.
- `legacy_conversion_retired` remains visible when swallow ingest is disabled.
- `search_sources`, `doc_show`, and document artifact view are bounded and read-only.
- Search document artifacts include `metadata.vault_path`.
- Ordinary `uv run` works.
- No local consoler SDK path is committed.
- Focused adapter, tag/search, compileall, and consoler smoke checks pass.

## Unknowns

- Whether the deterministic successful conversion path should be implemented only inside the Python gate or also exposed to the consoler local smoke.
- Whether consoler should adapt generic conformance expectations or add a source-trust-specific smoke command.
- Whether real swallow is available often enough locally to make optional real swallow smoke useful.
