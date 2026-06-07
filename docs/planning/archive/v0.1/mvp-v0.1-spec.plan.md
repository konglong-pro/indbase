---
doc_type: phase_plan
phase_id: v0.1
title: Foundation MVP
status: frozen
canonical: true
read_by_default: false
closeout: docs/testing/archive/v0.1-foundation-closeout.md
evidence:
  - docs/planning/archive/v0.1/mvp-v0.1-release-manifest.json
  - docs/planning/archive/mvp-v0.1-freeze.md
release_gate: scripts/mvp_release_gate.py
---

# v0.1 Foundation MVP

This is the cleaned frozen v0.1 archive entry. The migrated source file
contained unrecoverable mojibake in large Chinese sections, so this version
preserves the lifecycle, scope, and release boundary in readable form. Historical
gate evidence remains linked from the closeout and freeze notes.

## Goal

Build a reliable local-first knowledge substrate before intelligent workflows.

## Scope

v0.1 shipped:

- vault initialization and schema migration
- local file/folder ingest
- original preservation and readable Markdown output
- immutable source revisions
- chunking and SQLite FTS source snippet search
- task, error, review, and doctor visibility
- archive/restore workflows
- lightweight CLI/TUI operation

v0.1 did not ship:

- `ask`
- generated answers
- embeddings or hybrid search as release requirements
- default OCR/PDF intelligence
- automatic classification or tag suggestion
- translation, candidate cards, cloud sync, or multi-user behavior

## Frozen Rules

- The vault is the system of record.
- `doc_id` is stable; source revisions are immutable.
- `current_revision_id` is a pointer, not mutable content.
- Default search returns source snippets, not generated answers.
- Search citations are rendered from chunks and are not ordinary persisted
  `citations` rows.
- Failures must be visible through tasks, task events, errors, or review items.
- Normal user workflows archive/restore; physical delete is not a v0.1 feature.

## Superseded Rules

v0.1 direct normalizer and MarkItDown production conversion rules are superseded
by v0.2 swallow ingest:

- `docs/planning/superseded/v0.1-direct-normalizer-rules.md`
- `docs/planning/archive/v0.2/swallow-ingest-integration.plan.md`

## Evidence

- Closeout: `docs/testing/archive/v0.1-foundation-closeout.md`
- Freeze note: `docs/planning/archive/mvp-v0.1-freeze.md`
- Release manifest: `docs/planning/archive/v0.1/mvp-v0.1-release-manifest.json`
