# Revision Contract

## Purpose

Protect source identity and historical auditability.

## Applies To

- `documents`
- `document_revisions`
- source Markdown under the vault
- chunks and indexes derived from source revisions

## Terms

**Document**: Stable logical record identified by `doc_id`.

**Revision**: Immutable source snapshot identified by `revision_id`.

**Current revision**: Pointer from a document to one revision.

## Rules

- `doc_id` is stable across revisions.
- Revision content must not be mutated after creation.
- Changing trusted source content creates a new immutable revision.
- Old revisions and chunks remain available for audit in normal workflows.
- `current_revision_id` is only a pointer.
- Future generated outputs must bind to source revisions, not only documents.
- Physical delete is not a normal user workflow.

## Non-Goals

- This contract does not define output export retention.
- This contract does not define future garbage collection.

## Validation

- Re-ingest and normalize flows must create new revision IDs.
- Search indexes default to current promoted source revisions.
- Doctor checks should surface missing revision files, orphan chunks, and FTS
  drift.
