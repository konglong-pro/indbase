# Governed Tag Promotion

Status: accepted

Date: 2026-06-02

## Context

indbase needs tags to support precise search, review, and future retrieval workflows. Existing manual tags are simple strings stored as document metadata, while older classification workflows can suggest tags. Without governance, automatic tag generation can create too many narrow, duplicate, vague, or one-off tags and make search less reliable.

Tags differ from Big Categories:

- Big Categories are closed, single-select catalog lanes.
- Tags are multi-select metadata and can represent cross-cutting concepts.

This makes tags useful but also more prone to sprawl.

## Decision

indbase treats tags as governed metadata.

Automatic workflows may:

- attach existing active canonical Formal Tags when evidence, confidence, lifecycle, and volume-budget checks pass;
- propose Candidate Tags for review;
- generate explicit feedback, fixture cases, and policy suggestions.

Automatic workflows must not:

- create new Formal Tags directly;
- persist raw tag strings without Tag Resolution;
- promote new tags without Tag Admission Policy and Tag Volume Budget checks;
- delete, overwrite, or silently replace manual tags;
- merge, deprecate, archive, unblock, rescope, or otherwise mutate tag governance policy without an auditable user-approved action;
- learn through hidden online policy or prompt changes.

New Formal Tags, aliases, merges, deprecations, blocklist changes, scope changes, and policy changes require explicit auditable governance operations.

## Consequences

- Tag precision and tag-count control are prioritized over automatic coverage.
- Candidate tags are reviewable evidence, not trusted metadata.
- Tag search filters can rely on Formal Tags and canonical resolution instead of raw string matches.
- The tag harness can improve policy through fixtures and suggestions while keeping changes explicit.
- The first tagger can remain deterministic and local, with real model providers deferred until governance gates are stable.
- Implementation requires extra audit records, candidate state, resolution logic, admission policy, budget checks, and release gates.
