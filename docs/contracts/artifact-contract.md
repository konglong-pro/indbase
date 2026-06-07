# Artifact Contract

## Purpose

Define how evidence and derived outputs are represented without confusing them
with trusted source revisions.

## Applies To

- Swallow conversion evidence.
- Transition output evidence and exports.
- Consoler artifact blocks and artifact views.
- Doctor and Source Trust Loop views.

## Terms

**Artifact**: Evidence, preview, rendered output, or read-only view associated
with a command or object.

**Artifact URI**: Opaque identifier such as `indbase://...`.

**Artifact view**: Bounded read-only payload returned after dereferencing an
artifact URI.

## Rules

- Artifact URIs must stay opaque; callers must not treat them as local paths or
  SQL selectors.
- Artifact views are bounded and must report limits and truncation state.
- Artifact failures must return explicit stable errors.
- Export artifacts are not source revisions.
- Non-promoted conversion candidates are not source revisions.
- Default source search must not index derived export artifacts or untrusted
  candidates.
- Required evidence must be copied into indbase-owned durable artifact storage
  before automatic promotion or trusted output recording.

## Non-Goals

- This contract does not define a vault browser or full source viewer.
- This contract does not allow generated answers.

## Validation

- `indbase_agent` artifact retrieval tests should cover URI kind, scope,
  truncation, and failure behavior.
- Output and ingest gates should verify durable evidence exists before trusted
  state changes.
