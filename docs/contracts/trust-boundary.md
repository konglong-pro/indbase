# Trust Boundary Contract

## Purpose

Define what indbase owns and what external tools may provide.

## Applies To

- Core ingest, output, search, retrieval, evaluation, and doctor services.
- External local tools such as swallow, transition, and consoler.

## Rules

- The vault is the system of record.
- indbase owns `doc_id`, `revision_id`, chunks, indexes, task/error/review
  state, promotion policy, artifacts, and doctor findings.
- External tools may produce candidates, evidence, previews, or derived
  artifacts; they do not decide trusted source identity.
- Durable writes should go through `indbase_core` services.
- A successful command must not hide failed essential steps.
- Failures must be visible through explicit command output or durable
  observability records.

## Non-Goals

- This contract does not define every table or CLI command.
- This contract does not authorize cloud sync, hosted queues, or remote
  providers.

## Validation

- Review new integrations for direct database/file writes outside core services.
- Run relevant doctor and release gates from `docs/testing.md`.
