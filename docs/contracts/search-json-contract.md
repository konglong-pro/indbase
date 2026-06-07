# Search JSON Contract

## Purpose

Define the stable machine-readable shape for trusted source search results.

## Applies To

- `indb search --json`
- consoler `indbase.search_sources`
- tests and gates that assert governed source search behavior

## Required Shape

Search JSON should expose:

- normalized query text
- applied text/category/tag filters
- filter warnings and filter errors
- source bindings: `doc_id`, `revision_id`, and `chunk_id`
- bounded snippets
- result explanations, including why each filter matched
- truncation or limit metadata when applicable

## Rules

- Invalid filters fail explicitly with stable error codes.
- Valid filters with no matches return successful empty result sets.
- Candidate tags, raw FTS metadata tags, export artifacts, generated outputs,
  and unpromoted conversion candidates must not appear as trusted sources.
- Human output may be compact; JSON is the machine contract.

## Non-Goals

- This contract does not define generated answers.
- This contract does not define retrieval package scoring or answer readiness.

## Validation

- Run `uv run python scripts/v0322_tag_search_governance_release_gate.py` when
  search JSON semantics change.
