# M9.1 Selected-Chunk Translation Checkpoint

Status: complete

Date: 2026-05-13

Precondition: [M8.1 Classification Hardening Checkpoint](m8-classification-hardening-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

M9.1 is complete for selected-chunk translation outputs.

This checkpoint adds a deterministic local translation scaffold to validate source binding, output writing, and observability. It does not add model-backed translation, full-document translation, answer generation, `indb ask`, or candidate cards.

## Behavior

Translation commands:

```powershell
indb translate chunks <doc_id> --revision <revision_id> --chunk <chunk_id> --target-language <language>
indb translate list
indb translate show <translation_id>
```

M9.1 selected-chunk translation:

- requires `source_doc_id`
- requires `source_revision_id`
- requires one or more selected `source_chunk_ids`
- requires the source revision to be the active current revision
- rejects archived documents
- rejects source shells
- rejects old revisions
- rejects chunk IDs that do not belong to the selected current revision
- writes output Markdown under `outputs/translations/`
- records `executions`
- records `translations`
- binds translation rows to source document, source revision, and selected chunks
- does not mutate source Markdown
- does not create source revisions or chunks
- does not persist ordinary citations

Output files use:

```text
outputs/translations/<translation_id>.md
```

## Invariants

M9.1 preserves these rules:

- translation outputs are generated artifacts, not source revisions
- source Markdown remains immutable
- source chunks remain unchanged
- translation records bind to `source_doc_id`, `source_revision_id`, and `source_chunk_ids_json`
- selected chunk translation is not an answer-generation workflow
- selected chunk translation does not create candidate cards

## Verification

Latest verification commands:

```powershell
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python -m pytest
.venv\Scripts\python scripts/m71_embedding_index_gate.py
.venv\Scripts\python scripts/m72_hybrid_search_gate.py
.venv\Scripts\python scripts/m8_classification_gate.py
.venv\Scripts\python scripts/m81_classification_hardening_gate.py
.venv\Scripts\python scripts/m91_translation_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

Latest result:

```text
compileall passed
164 passed
```

Latest M9.1 gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m91-translation-gate-20260513213254964934
M91_TRANSLATION_GATE=passed
```

M9.1 hard metrics:

```json
{
  "source_revision_mutations": 0,
  "source_chunk_mutations": 0,
  "source_markdown_mutations": 0,
  "invalid_chunk_partial_records": 0,
  "old_revision_translations": 0,
  "archived_doc_translations": 0,
  "source_shell_translations": 0,
  "translation_missing_doc_revision_chunks": 0,
  "outputs_outside_translations_dir": 0,
  "citations_created": 0
}
```

M9.1 positive proof metrics:

```json
{
  "translations_written": 1,
  "executions_written": 1,
  "output_files_written": 1,
  "translation_chunk_ids_recorded": 2,
  "output_contains_source_ids": 1
}
```

Latest v0.1 RC aggregate after M9.1:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\v01-release-candidate-gate-20260513213254844356
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

M9.2 full-document translation is tracked separately in [m9-full-document-translation-checkpoint.md](m9-full-document-translation-checkpoint.md).

Full-document translation must preserve:

- source revision binding
- chunk-level citation binding
- output-only writes under `outputs/translations/`
- no source Markdown mutation
- no answer generation
- no candidate cards

## Boundary

M9.1 does not include:

- model-backed translation
- full-document translation
- translation quality scoring
- translation review workflow
- answer generation
- `indb ask`
- candidate cards
