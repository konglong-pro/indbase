# M9.3 Translation Hardening Checkpoint

Status: complete

Date: 2026-05-13

Precondition: [M9.2 Full-Document Translation Checkpoint](m9-full-document-translation-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](mvp-v0.1-spec.md)

## Decision

M9.3 is complete for translation state-machine hardening.

This checkpoint keeps translation local and deterministic. It does not add model-backed translation, answer generation, `indb ask`, candidate cards, glossary management, or translation review workflows.

## Behavior

Translation commands now include:

```powershell
indb translate chunks <doc_id> --revision <revision_id> --chunk <chunk_id> --target-language <language>
indb translate document <doc_id> --revision <revision_id> --target-language <language>
indb translate list
indb translate show <translation_id>
indb translate open <translation_id> --print-path
```

M9.3 hardens translation behavior:

- translation outputs remain bound to the source revision they were created from
- changed-content re-ingest does not rebind old translation records to the new current revision
- old-revision translation outputs remain listable and openable
- archive after translation does not hide or delete existing translation outputs
- archived documents still reject new translation requests by default
- adapter failure records a failed `execution`
- adapter failure marks the task failed
- adapter failure records an `errors` row
- adapter failure does not create a `translations` row
- adapter failure does not leave output files behind
- adapter failure does not leave translation tasks or executions running

## Invariants

M9.3 preserves these rules:

- translation records bind to `source_doc_id`, `source_revision_id`, and `source_chunk_ids_json`
- translation output files live under `outputs/translations/`
- source Markdown remains immutable
- source chunks remain unchanged
- source revisions remain unchanged
- failed translation attempts are visible through `tasks`, `task_events`, `errors`, and failed `executions`
- failed translation attempts are not successful translation records

## Verification

Latest verification commands:

```powershell
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts/m91_translation_gate.py
.venv\Scripts\python scripts/m92_translation_gate.py
.venv\Scripts\python scripts/m93_translation_hardening_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

Latest result:

```text
compileall passed
169 tests collected
pytest passed
```

Latest M9.1 gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m91-translation-gate-20260513220033393882
M91_TRANSLATION_GATE=passed
```

Latest M9.2 gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m92-translation-gate-20260513220033369148
M92_TRANSLATION_GATE=passed
```

Latest M9.3 gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m93-translation-hardening-gate-20260513220007176318
M93_TRANSLATION_HARDENING_GATE=passed
```

M9.3 hard metrics:

```json
{
  "translations_rebound_after_reingest": 0,
  "missing_output_after_reingest": 0,
  "missing_output_after_archive": 0,
  "archived_new_translation_records": 0,
  "failed_translation_records": 0,
  "failed_output_files": 0,
  "failed_executions_not_failed": 0,
  "failed_tasks_not_failed": 0,
  "failed_errors_missing": 0,
  "running_translation_tasks": 0,
  "running_translation_executions": 0,
  "output_paths_missing": 0
}
```

M9.3 positive proof metrics:

```json
{
  "stale_translation_records_after_reingest": 2,
  "archived_existing_translations_listed": 1,
  "failed_execution_records": 1,
  "pre_reingest_outputs_existed": 1
}
```

Latest v0.1 RC aggregate after M9.3:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\v01-release-candidate-gate-20260513220033273485
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

## Next Boundary

The next translation step should not jump straight to external model calls without a provider boundary decision.

Before model-backed translation starts, define:

- provider configuration and local/offline failure behavior
- retry policy
- timeout policy
- output quality/review policy
- whether failed model attempts create review items
- how glossary support is represented, if any

## Boundary

M9.3 does not include:

- model-backed translation
- translation quality scoring
- translation review workflow
- glossary management
- answer generation
- `indb ask`
- candidate cards
