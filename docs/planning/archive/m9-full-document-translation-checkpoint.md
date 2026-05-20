# M9.2 Full-Document Translation Checkpoint

Status: complete

Date: 2026-05-13

Precondition: [M9.1 Selected-Chunk Translation Checkpoint](m9-selected-chunk-translation-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

M9.2 is complete for full-document translation outputs.

This checkpoint extends the deterministic local translation scaffold from selected chunks to the full current source document. It still does not add model-backed translation, answer generation, `indb ask`, candidate cards, or translation quality scoring.

## Behavior

Translation commands now include:

```powershell
indb translate chunks <doc_id> --revision <revision_id> --chunk <chunk_id> --target-language <language>
indb translate document <doc_id> --revision <revision_id> --target-language <language>
indb translate list
indb translate show <translation_id>
```

M9.2 full-document translation:

- requires `source_doc_id`
- requires `source_revision_id`
- requires the source revision to be the active current revision
- uses every current chunk for that revision, ordered by chunk sequence
- records all translated chunk IDs in `source_chunk_ids_json`
- rejects archived documents
- rejects source shells
- rejects old revisions
- rejects current revisions with no active chunks
- writes output Markdown under `outputs/translations/`
- records `executions`
- records `translations`
- does not mutate source Markdown
- does not create source revisions or chunks
- does not update FTS rows
- does not persist ordinary citations

Output files use:

```text
outputs/translations/<translation_id>.md
```

## Invariants

M9.2 preserves these rules:

- translation outputs are generated artifacts, not source revisions
- full-document translation means all active current chunks for the current revision
- source Markdown remains immutable
- source chunks remain unchanged
- translation records bind to `source_doc_id`, `source_revision_id`, and `source_chunk_ids_json`
- full-document translation is not an answer-generation workflow
- full-document translation does not create candidate cards

## Verification

Latest verification commands:

```powershell
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts/m91_translation_gate.py
.venv\Scripts\python scripts/m92_translation_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

Latest result:

```text
compileall passed
167 tests collected
pytest passed
```

Latest M9.1 gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m91-translation-gate-20260513214503274707
M91_TRANSLATION_GATE=passed
```

Latest M9.2 gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m92-translation-gate-20260513214448587508
M92_TRANSLATION_GATE=passed
```

M9.2 hard metrics:

```json
{
  "source_revision_mutations": 0,
  "source_chunk_mutations": 0,
  "source_markdown_mutations": 0,
  "old_revision_full_document_translations": 0,
  "archived_doc_full_document_translations": 0,
  "source_shell_full_document_translations": 0,
  "no_chunk_full_document_translations": 0,
  "translation_missing_doc_revision_chunks": 0,
  "full_document_missing_current_chunk_ids": 0,
  "outputs_outside_translations_dir": 0,
  "citations_created": 0
}
```

M9.2 positive proof metrics:

```json
{
  "translations_written": 1,
  "executions_written": 1,
  "output_files_written": 1,
  "full_document_chunk_ids_recorded": 3,
  "full_document_mode_records": 1,
  "output_contains_source_ids": 1
}
```

Latest v0.1 RC aggregate after M9.2:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\v01-release-candidate-gate-20260513214712639069
V01_RELEASE_CANDIDATE_GATE=passed
```

RC aggregate:

```json
{
  "critical_doctor_findings": 0,
  "fts_desync": 0,
  "index_integrity_errors": 0,
  "orphan_files": 0,
  "orphan_records": 0,
  "unexpected_errors": 0,
  "unsupported_documents_created": 0,
  "zero_chunk_current_revisions": 0
}
```

## Follow-Up

M9.3 translation hardening is tracked separately in [m9-translation-hardening-checkpoint.md](m9-translation-hardening-checkpoint.md).

Translation hardening must preserve:

- selected-chunk and full-document records should remain source-bound after re-ingest
- old-revision translation outputs should remain viewable but not treated as current source outputs
- archived document behavior should stay explicit
- translation outputs should be listed and opened without touching source documents
- failed model-backed adapters, if introduced later, must not create partial successful translations

## Boundary

M9.2 does not include:

- model-backed translation
- translation quality scoring
- translation review workflow
- glossary management
- answer generation
- `indb ask`
- candidate cards
