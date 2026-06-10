---
doc_type: phase_plan
phase_id: v0.3.2.2-tag-search-governance
title: Tag/Search Governance
status: shipped
canonical: true
read_by_default: false
closeout: docs/testing/archive/v0.3.2.2-tag-search-governance-closeout.md
related_contracts:
  - docs/contracts/source-search-contract.md
related_adrs:
  - docs/adr/0002-governed-tag-promotion.md
---

# v0.3.2.2 Tag/Search Governance

Status: active design

Date: 2026-06-03

Phase: v0.3.2.2 deterministic tag/search governance

Related docs:

- [MVP v0.1 Spec](../v0.1/mvp-v0.1-spec.plan.md)
- [v0.3.1 Taxonomy Category Foundation](../v0.3.1/taxonomy-category-foundation.plan.md)
- [v0.3.2 Tag Governance Foundation](../v0.3.2/tag-governance-foundation.plan.md)
- [v0.3.2.1 Tag Harness Hardening](../v0.3.2.1/tag-harness-hardening.plan.md)
- [v0.3.2.2 Tag/Search Governance Agent Guide](../../../agents/archive/indbase/v0.3.2.2-tag-search-governance.md)
- [Taxonomy glossary](../../../../CONTEXT.md)

## Decision Summary

v0.3.2.2 governs combined tag and search behavior after v0.3.2 Tag Governance Foundation and v0.3.2.1 Tag Harness Hardening.

The phase owns governed source search paths:

```text
original source text search
tag-only filter-only search
tag + text search
category + tag search
category + tag + text search
```

It does not replace v0.3.3 retrieval evaluation / answer readiness and does not implement `ask`.

Core invariants:

```text
search = trusted source snippets
tag/category/text filters use AND semantics
trusted tag filters are relation-backed, not FTS metadata strings
invalid filters fail explicitly
valid filters with no matches return empty success
JSON explains filters, source bindings, and match reasons
```

## Existing Substrate

The implementation must build on existing search and tag/category services:

- `src/indbase_core/search.py`
- `src/indbase_core/search_text.py`
- `src/indbase_core/tag_search.py`
- `src/indbase_core/tag_resolution.py`
- `src/indbase_core/tags.py`
- `src/indbase_core/category_taxonomy.py`
- `src/indbase_core/indexer.py`
- `src/indbase_core/doctor.py`
- `src/indbase_cli/main.py`
- `tests/test_search.py`
- `tests/test_search_text.py`
- `tests/test_v032_tag_governance.py`
- `tests/test_v0321_tag_harness.py`
- `scripts/v032_tag_governance_release_gate.py`
- `scripts/v0321_tag_harness_release_gate.py`

Current behavior already has:

- `indb search`
- `indb search --tag`
- `tag:<ref>` query prefix parsing
- `category:<ref>` query prefix parsing in core search
- relation-backed tag filtering through `document_tags`
- source safety checks for active documents and current chunks
- CJK substring fallback

v0.3.2.2 should formalize and harden these behaviors rather than rewrite search ranking.

## Goals

- Define one normalized Search Filter Model for CLI flags and query prefixes.
- Extend existing `indb search` with governed `--category`, governed `--tag`, prefix parsing, and stable `--json`.
- Preserve `tag:<ref>` and `category:<ref>` shortcuts by normalizing them into the same filter model.
- Implement explicit AND semantics for category, tag, and text constraints.
- Add filter-only source search for valid category/tag filters with no text query.
- Return bounded representative source snippets for filter-only searches.
- Add structured Search Match Explanation and Search JSON Contract.
- Formalize tag lifecycle filter semantics for canonical, alias, merged, deprecated, and archived tag references.
- Distinguish invalid filters, execution errors, and successful empty results.
- Prove source search safety under combined filters.
- Add a deterministic Tag/Search Governance Harness and release gate.
- Add doctor diagnostics for tag/search consistency gaps if needed.
- Add a C2d CI job after the v0.3.2.1 tag harness gate.

## Non-goals

- Do not implement `ask`.
- Do not change `indb retrieve` package or ranking semantics.
- Do not add LLM providers, embedding providers, API keys, or network calls.
- Do not redesign vector or hybrid ranking.
- Do not add production schema or migrations by default.
- Do not add parallel search commands such as `indb tag-search`, `indb browse`, or `indb search-governance`.
- Do not implement TUI or consoler UI.
- Do not add `doctor --fix`, automatic rebuild, automatic retagging, or automatic repair.
- Do not add multi-tag OR, multi-category OR, similar-tag expansion, semantic expansion, or query expansion.
- Do not let pending/rejected/blocked/stale candidates, raw tag strings, or FTS tag metadata determine trusted tag-filter membership.
- Do not commit raw private vault excerpts or unsanitized dogfood snippets as fixtures.

## Ownership Boundary

v0.3.2.2 owns:

- Search Filter Model
- CLI/prefix filter normalization and conflict validation
- governed `indb search --json` contract
- Search Match Explanation
- filter-only source search behavior
- category/tag/text AND semantics
- tag filter lifecycle semantics in source search
- tag/search harness fixtures
- tag/search release gate
- narrowly scoped doctor diagnostics for search/filter integrity
- CI job and docs after implementation

v0.3.2.2 may read or call:

- current `search_chunks`
- current `resolve_tag_filter`
- current category filter resolution
- `document_tags`, `tags`, `categories`, `documents`, `document_revisions`, and `chunks`
- FTS index state for source text search
- existing doctor checks

v0.3.2.2 must not own:

- retrieval package construction
- answer readiness
- answer generation
- ranking policy redesign
- vector/hybrid quality changes
- tag candidate generation
- tag policy mutation
- production query history or audit storage by default

## Search Filter Model

Introduce a normalized internal filter model. It may be a new module such as:

```text
src/indbase_core/search_filters.py
```

or a narrow extension to existing search modules.

Required fields:

```text
text_query
category_ref
category_id
tag_ref
canonical_tag_id
filter_tag_ids
tag_lifecycle
via_alias
resolved_from_merged
warnings
filter_errors
```

Rules:

- `--category` and `--tag` are structured filters and have highest precedence.
- `category:<ref>` and `tag:<ref>` query prefixes are shortcut filters.
- CLI flags and query prefixes normalize into the same filter model.
- Equivalent flag/prefix filters may be deduplicated.
- Conflicting flag/prefix filters are invalid search filters.
- Prefix filters with spaces require quotes, such as `tag:"retrieval augmented generation" sqlite`.
- v0.3.2.2 supports at most one category filter and one tag filter.
- v0.3.2.2 does not add OR syntax.

## Governed Search Paths

### Original source text search

Input:

```text
indb search "exact source phrase"
```

Rules:

- Search current trusted source chunks.
- Return source snippets with `doc_id`, `revision_id`, and `chunk_id`.
- Archived documents, old revisions, deleted chunks, candidate source shells, and output artifacts are excluded.
- CJK substring fallback must continue to work.

### Tag-only filter-only search

Input:

```text
indb search "" --tag rag
indb search "tag:rag"
```

Rules:

- Resolve the tag through Formal Tag metadata.
- Return bounded representative snippets from documents with trusted document-tag relationships.
- Do not return every chunk.
- Explain active filters and tag resolution.

### Tag/text search

Input:

```text
indb search "sqlite" --tag rag
indb search "tag:rag sqlite"
```

Rules:

- Apply tag filter AND text query.
- Text match must come from trusted current source chunks.
- Tag membership must come from trusted `document_tags`, not FTS metadata strings.
- Candidate/raw/rejected/blocked tags must not affect membership.

### Category/tag search

Input:

```text
indb search "" --category engineering --tag rag
indb search "category:engineering tag:rag"
```

Rules:

- Apply category AND tag.
- Only documents satisfying both filters are returned.
- Documents satisfying only one filter are excluded.
- If both filters are valid but have no intersection, return empty success with explanation.

### Category/tag/text search

Input:

```text
indb search "sqlite" --category engineering --tag rag
indb search "category:engineering tag:rag sqlite"
```

Rules:

- Apply category AND tag AND text.
- Explain all filters and match source.

## Filter-Only Result Policy

Filter-only source search is allowed when category and/or tag filters are valid and no text query remains.

Default behavior:

- return document-level representative source snippets
- at most one or two snippets per document in v1
- use deterministic ordering
- include active filters in JSON explanation

Do not:

- return all chunks
- generate summaries
- browse unbounded vault contents
- use candidate tags
- include output artifacts

## Tag Filter Lifecycle Semantics

Use these rules for tag references in trusted source search:

| Reference | Behavior |
| --- | --- |
| active canonical tag | trusted filter allowed |
| active alias | resolves to canonical tag; explanation sets `via_alias = true` |
| merged tag | resolves to target canonical tag; explanation sets `resolved_from_merged = true` |
| deprecated tag | may query existing bindings for that deprecated tag itself; warning required |
| archived tag | invalid filter by default |
| blocked tag ref | invalid filter |
| candidate tag ref | invalid filter |
| unknown tag ref | invalid filter |

Deprecated tag search must not:

- expand to canonical targets
- run tag link migration
- rewrite document-tag links

Merged tag search may resolve to the canonical target according to existing tag resolution semantics.

## Trusted Tag Membership

Trusted tag-filter membership is authoritative only through:

```text
document_tags -> tags -> canonical/lifecycle resolution
```

Trusted `document_tags.source` values:

```text
manual
accepted_candidate
auto
legacy_classification
```

Not trusted for filtering:

```text
pending candidate
rejected candidate
blocked candidate
stale candidate
raw tag string
chunks_fts.tags
metadata tag text
```

Gate fixtures must include a document whose FTS tag metadata mentions a tag while `document_tags` lacks the trusted relation; `--tag` must not return that document.

## Search JSON Contract

`indb search --json` should return a stable shape:

```json
{
  "query_id": null,
  "query": "tag:rag sqlite",
  "normalized_query": "sqlite",
  "applied_filters": {
    "category": null,
    "tag": {
      "input": "rag",
      "canonical_tag_id": "tag_...",
      "filter_tag_ids": ["tag_..."],
      "via_alias": false,
      "resolved_from_merged": false,
      "deprecated": false
    }
  },
  "filter_errors": [],
  "warnings": [],
  "result_count": 1,
  "results": [
    {
      "rank": 1,
      "doc_id": "doc_...",
      "revision_id": "rev_doc_..._0001",
      "chunk_id": "chk_...",
      "title": "RAG note",
      "source_path": "sources/...",
      "snippet": "source text...",
      "score": 0.0,
      "match_source": "fts",
      "explanation": {
        "text_match": {"query": "sqlite", "source": "fts"},
        "filter_match": {"tag_source": "manual"},
        "applied_filters": ["tag"],
        "warnings": []
      }
    }
  ]
}
```

Rules:

- Human CLI output may stay concise.
- Machine consumers must use JSON, not Rich table parsing.
- JSON must distinguish `filter_errors`, `warnings`, and execution errors.
- Empty governed search returns `result_count = 0`, `results = []`, and no filter error.
- Invalid filter requests should return a stable error code and nonzero CLI exit.

## Error Semantics

Search filter errors:

- unknown category or tag
- conflicting CLI flag and query prefix filters
- malformed quoted prefix
- archived tag reference
- blocked tag reference
- candidate tag reference

Search execution errors:

- database execution failure
- FTS/index inconsistency
- unexpected service failure after filters are valid

Empty governed search result:

- valid filters and/or text constraints match no current trusted snippets
- successful command
- empty result set with explanation

## Deterministic Ordering

Text query present:

- keep existing deterministic FTS/CJK behavior
- do not add new ranking policy

Filter-only search:

- use stable document/chunk ordering
- recommended order: document created/updated timestamp, then `doc_id`, then chunk sequence
- limit snippets per document

Do not add:

- semantic rerank
- boost policy
- provider score
- vector quality gate

## Doctor Extensions

Add doctor findings only where they expose real governed source-search risks.

Candidate hard findings:

```text
tag_search_filter_resolution_broken
tag_search_candidate_pollution
tag_search_metadata_relation_mismatch
tag_search_archived_tag_filterable
tag_search_category_tag_relation_inconsistent
search_json_contract_invalid
```

Candidate warnings:

```text
tag_search_deprecated_filter_used
tag_search_fts_metadata_stale
tag_search_filter_only_large_result_set
```

Doctor must not:

- repair tags
- rebuild FTS
- migrate tag links
- retag documents
- change categories
- rerank search

## Fixture And Harness Plan

Add:

```text
tests/fixtures/v0322_tag_search_governance/
  README.md
  cases.jsonl
```

Add focused tests:

```text
tests/test_v0322_tag_search_governance.py
```

Add gate:

```text
scripts/v0322_tag_search_governance_release_gate.py
```

Suggested fixture case fields:

```json
{
  "case_id": "tagsearch_tag_text_and",
  "source": "synthetic",
  "query": "sqlite",
  "category": null,
  "tag": "rag",
  "documents": [],
  "expected_doc_ids": [],
  "forbidden_doc_ids": [],
  "expected_chunk_text": [],
  "expected_filter_errors": [],
  "expected_warnings": [],
  "expected_explanation_fields": []
}
```

Minimum coverage:

- exact source phrase search hits current chunk
- CJK substring fallback still works
- archived document excluded
- old revision excluded
- derived output artifact excluded
- tag-only filter-only returns representative snippets
- tag/text AND returns only documents satisfying both
- category/tag AND returns only intersection
- category/tag valid no-intersection returns empty success
- category-only match excluded when tag also required
- tag-only match excluded when category also required
- active alias resolves to canonical tag
- merged tag resolves to canonical tag
- deprecated tag searches existing deprecated bindings with warning
- archived tag reference is invalid
- unknown tag/category is invalid
- candidate tag reference is invalid
- FTS tag metadata string does not create trusted tag membership
- trusted tag source appears in explanation when available
- CLI flag and prefix conflict fails
- equivalent flag and prefix dedupe succeeds
- `--json` includes normalized query, applied filters, source bindings, snippets, explanation, warnings, and filter errors
- filter-only ordering is deterministic

Fixtures committed to the repository must be synthetic or sanitized.

## Implementation Plan

### Slice 1: test-first fixture schema

Add:

- `tests/fixtures/v0322_tag_search_governance/README.md`
- `tests/fixtures/v0322_tag_search_governance/cases.jsonl`
- `tests/test_v0322_tag_search_governance.py`

Initial tests should assert:

- fixture schema validates
- unsupported fixture keys are rejected
- unsanitized fixture sources are rejected
- summary JSON and failure records have stable shapes
- exact source hit, tag/text AND, category/tag AND, invalid filter, empty result, and JSON explanation cases exist

Focused command:

```powershell
uv run python -m pytest tests/test_v0322_tag_search_governance.py -q
```

### Slice 2: search filter model

Add a search filter/explanation layer or narrowly extend existing modules.

Likely files:

```text
src/indbase_core/search_filters.py
src/indbase_core/search_explanations.py
```

Allowed alternative:

```text
src/indbase_core/search.py
src/indbase_core/tag_search.py
```

Implement:

- parse CLI and prefix filters into one filter model
- validate conflicts
- resolve category and tag references
- represent filter errors separately from execution errors
- preserve existing search options compatibility

### Slice 3: governed source search behavior

Extend `search_chunks` or add a wrapper used by CLI:

- exact source search remains current chunk search
- tag/text/category filters combine with AND
- filter-only search returns representative snippets
- FTS metadata tag strings do not determine membership
- source safety gates apply to all paths
- deterministic ordering is stable

Do not rewrite scoring.

### Slice 4: JSON contract and CLI

Extend existing `indb search`:

- add `--category`
- keep governed `--tag`
- keep `tag:<ref>` and `category:<ref>` prefixes
- add stable governed `--json`
- make invalid filter errors exit nonzero
- keep human output concise

Do not add parallel search commands.

### Slice 5: doctor diagnostics

Add doctor findings only if needed by fixtures and search integrity checks.

Focused command:

```powershell
uv run python -m pytest tests/test_doctor.py tests/test_v0322_tag_search_governance.py -q
```

### Slice 6: release gate and CI

Add:

```text
scripts/v0322_tag_search_governance_release_gate.py
```

Gate output should be stable JSON with hard-gate metrics.

Add CI job after local pass:

```text
tag-search-governance-v0322
name: C2d - v0.3.2.2 tag/search governance gate
command: uv run python scripts/v0322_tag_search_governance_release_gate.py
```

Recommended CI dependency:

```text
needs: [test, tag-harness-v0321]
```

Update docs after implementation:

- `docs/testing.md`
- `docs/project-status.md`
- `docs/planning/README.md` if not already updated
- root `AGENTS.md` if not already updated

Do not update README unless a user-visible command contract beyond existing `indb search` needs quick-start coverage.

## Release Gate Metrics

Suggested summary shape:

```json
{
  "phase": "v0.3.2.2",
  "status": "passed",
  "case_count": 0,
  "passed_case_count": 0,
  "failed_case_count": 0,
  "hard_gates": {
    "source_exact_hits": true,
    "source_safety_violations": 0,
    "tag_filter_pollution": 0,
    "category_tag_intersection_failures": 0,
    "invalid_filter_failures": 0,
    "empty_result_semantics_failures": 0,
    "json_contract_failures": 0,
    "ordering_instability": 0
  },
  "warnings": [],
  "failures": []
}
```

Hard gates:

- exact source phrase or substring finds expected current chunk
- result bindings include `doc_id`, `revision_id`, and `chunk_id`
- archived docs and old revisions are excluded
- output artifacts and candidate shells are excluded
- trusted tag filters are relation-backed only
- FTS tag metadata does not pollute trusted filters
- tag/text/category filters use AND
- valid no-intersection search is empty success
- invalid filters fail with stable error code
- JSON contract is stable
- filter-only ordering is deterministic

## Required Validation

Focused validation:

```powershell
uv run python -m pytest tests/test_search.py tests/test_v032_tag_governance.py tests/test_v0321_tag_harness.py tests/test_v0322_tag_search_governance.py -q
uv run python scripts/v032_tag_governance_release_gate.py
uv run python scripts/v0321_tag_harness_release_gate.py
uv run python scripts/v0322_tag_search_governance_release_gate.py
```

Before declaring complete:

```powershell
uv run python -m pytest -q
uv run python -m compileall src tests scripts
uv run python scripts/v032_tag_governance_release_gate.py
uv run python scripts/v0321_tag_harness_release_gate.py
uv run python scripts/v0322_tag_search_governance_release_gate.py
```

When retrieval behavior is touched:

```powershell
uv run python scripts/v033_retrieval_eval_release_gate.py
```

## Acceptance Checklist

- Existing `indb search` is the only user-facing search command changed.
- `--category`, `--tag`, `category:<ref>`, and `tag:<ref>` normalize into one filter model.
- Conflicting filters fail explicitly.
- Equivalent filters dedupe.
- Category, tag, and text constraints use AND semantics.
- Filter-only source search returns bounded representative snippets.
- Exact source search still returns expected current chunks.
- Source safety excludes archived docs, old revisions, deleted chunks, output artifacts, and candidate shells.
- Trusted tag filtering uses `document_tags -> tags`, not FTS metadata strings.
- Trusted document tag sources are limited to manual, accepted candidate, auto, and legacy classification.
- Alias, merged, deprecated, and archived tag semantics match this spec.
- Empty result and invalid filter are distinguishable.
- `--json` exposes the governed Search JSON Contract.
- Human output remains concise.
- Doctor diagnostics are read-only.
- v0.3.2.2 gate passes locally.
- CI C2d job is added after local pass.
- Full pytest and compileall pass.

## Completion Report

Every implementation handoff should report:

- changed files
- new fixture cases
- search modules touched
- CLI options changed
- JSON fields added
- doctor findings added, if any
- gate summary metrics
- CI job added, if any
- tests run
- tests not run
- remaining risks
- confirmation that `ask`, retrieval ranking changes, providers, embeddings, vector/hybrid redesign, production schema, parallel search commands, UI, doctor repair, OR/semantic expansion, and untrusted tag-string filtering remain out of scope
