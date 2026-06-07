# CONTEXT.md

This is the short glossary entry point for `indbase`. Read only the glossary
pack relevant to the task.

## Core Reading

- Core vault, document, revision, chunk, source, task, and doctor terms:
  `docs/glossary/core.md`
- Source Trust Loop terms:
  `docs/glossary/source-trust.md`
- Category taxonomy terms:
  `docs/glossary/taxonomy-category.md`
- Tag governance terms:
  `docs/glossary/tag-governance.md`
- Governed search terms:
  `docs/glossary/search-governance.md`
- Consoler integration terms:
  `docs/glossary/consoler-integration.md`
- Intent drafting terms:
  `docs/glossary/intent-drafting.md`

The pre-migration long glossary is retained for archaeology at
`docs/glossary/archive/context-pre-lifecycle-migration.md`. Do not read it by
default.

## Essential Terms

**Vault**: Local filesystem and SQLite state that indbase owns. The vault is the
system of record.

**Document**: Stable logical record identified by `doc_id`.

**Revision**: Immutable source snapshot identified by `revision_id`. Currentness
is a pointer, not mutation.

**Candidate**: Untrusted conversion output awaiting indbase promotion policy.
A candidate is not a revision.

**Artifact**: Durable evidence or derived output. Artifacts have trust levels and
must not automatically enter default source search.

**Source Search**: Retrieval of trusted source snippets from promoted current
source revisions. It is not `ask`.

**Source Trust Loop**: The bounded consoler action surface for ingesting,
searching, inspecting, and troubleshooting trusted source records.

**Indbase NL v2 Intent Drafting**: The completed v0.3.2.3f consoler-owned,
explicitly opted-in assisted form-prefill flow. It did not add indbase natural
language parsing, provider setup, `ask`, generated answers, or new indbase
commands.

**Answer Readiness**: A v0.3.3 deterministic report that decides whether a
retrieval run is safe enough for a future answer workflow. It does not generate
answers.

## Ambiguous Words To Avoid

- Do not use "source" for derived exports; say "source revision" or "artifact".
- Do not use "search" for generated answers; say "source search" or `ask`.
- Do not use "tag" for raw strings; say "Formal Tag" or "Candidate Tag".
- Do not use "current phase" without checking `docs/phase-manifest.yaml`.
