# Consoler Agent Boundary Contract

## Purpose

Define what the indbase consoler adapter may expose.

## Applies To

- `src/indbase_agent`
- `src/indbase_agent/manifest.json`
- Consoler Source Trust Loop integration

## Command Surface

The bounded Source Trust Loop command surface is:

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

## Rules

- `indbase.ingest_file` is the only first-version write command.
- Review, task, error, search, doctor, and doc commands are read-only.
- The adapter calls `indbase_core` services directly; it does not shell out to
  `indb`.
- `indbase_core` must not import consoler SDKs or consoler vocabulary.
- `doc_show` accepts `doc_id`; it must not add title/path lookup.
- List commands should not emit row-level artifact blocks.
- Focused show commands and allowed source search/doctor operations may emit
  bounded artifacts.
- Artifact block metadata may carry `vault_path` as internal retrieval metadata.
  Dereferenced artifact views returned by indbase must redact local absolute
  vault paths from top-level metadata, JSON blocks, and visible blocks.
- Consoler artifact blocks must use `indbase://...` URIs, not provider URIs,
  provider cache paths, `file://`, or private temporary paths.

## Non-Goals

- No Web UI, vault browser, full source viewer, revision browser, review
  mutation, category/tag mutation, doctor repair, generated answers, `ask`, or
  retrieval packages.

## Validation

- Run `uv run python -m pytest tests/test_indbase_agent.py tests/test_indbase_agent_readonly_views.py -q`
  when adapter behavior changes.
- Run `uv run python scripts/v0323a_probe_stabilization_release_gate.py` and
  `uv run python scripts/v0323b_consoler_readonly_views_release_gate.py` when
  artifact/view behavior changes.
