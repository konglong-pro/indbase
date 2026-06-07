# Source Search Contract

## Purpose

Define trusted source snippet search behavior.

## Applies To

- `indb search`
- governed category/tag filters
- consoler `indbase.search_sources`

## Rules

- Search is retrieval of source snippets, not answer generation.
- Default source search indexes promoted current source revisions only.
- Archived documents are excluded by default.
- Export artifacts, generated outputs, unpromoted candidates, and raw candidate
  tags are not trusted default search sources.
- Category, tag, and text filters combine with AND semantics.
- Trusted tag filters use relation-backed Formal Tags, not raw FTS strings.
- Invalid filters fail explicitly.
- Valid filters with no matches return empty success.
- Stable JSON output is defined in `docs/contracts/search-json-contract.md`.

## Non-Goals

- This contract does not define `ask`.
- This contract does not define retrieval evaluation or answer readiness.
- This contract does not authorize vector/hybrid ranking rewrites.
- This contract does not add semantic expansion, similar-tag expansion, or OR
  filters.

## Validation

- Run `uv run python scripts/v0322_tag_search_governance_release_gate.py` when
  governed search semantics are touched.
