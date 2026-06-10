# Core Glossary

## Terms

**Vault**: Local filesystem and SQLite state owned by indbase. The vault is the
system of record.

**Original**: Archived source file as received from local ingest.

**Document**: Stable logical record identified by `doc_id`.

**Document Revision**: Immutable source Markdown snapshot identified by
`revision_id`.

**Current Revision**: The revision currently indexed and shown by default for a
document.

**Chunk**: Indexed text segment derived from one source revision.

**Task**: Durable observability record for an operation that can succeed, fail,
or complete with issues.

**Error**: Durable failure record used for visible troubleshooting.

**Review Item**: Durable human-review record for unsupported, partial, or
policy-blocked workflows.

**Doctor**: Diagnostic command and service that reports integrity findings
without silently repairing state.

## Relationships

- A document can have many immutable revisions.
- A revision can have many chunks.
- Search results bind to document, revision, and chunk identifiers.
- Tasks, errors, and review items make operational state visible.

## Flagged Ambiguities

- Do not call a derived export a source revision.
- Do not call a candidate a revision.
- Do not call generated text a search result.
