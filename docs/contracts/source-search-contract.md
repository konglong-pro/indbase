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
- Source FTS materialization is traceable through companion lineage tables:
  `index_builds` and `index_build_entries`.
- `chunks_fts` schema remains unchanged. Lineage rows carry
  `index_kind = source_fts`, `doc_id`, `revision_id`, `chunk_id`, and
  `index_build_id`.
- Current source search rows must agree with current document revision pointers
  and current chunks.
- Doctor reports current/search breakage as errors, including missing current
  FTS rows, stale FTS rows, and lineage entries pointing to missing chunks.
- Upgraded historical FTS rows without lineage and historical non-current
  lineage are warnings until a real rebuild creates fresh verified lineage.

## Non-Goals

- This contract does not define `ask`.
- This contract does not define retrieval evaluation or answer readiness.
- This contract does not authorize vector/hybrid ranking rewrites.
- This contract does not add semantic expansion, similar-tag expansion, or OR
  filters.

## Validation

- Run `uv run python scripts/v0322_tag_search_governance_release_gate.py` when
  governed search semantics are touched.
- Run `uv run python scripts/v035_stability_hardening_gate.py` when source FTS
  lineage, normalize replace indexing, or retrieval regression thresholds are
  touched.
