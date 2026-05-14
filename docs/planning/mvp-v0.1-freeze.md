# MVP v0.1 Freeze

Status: frozen

Version: `0.1.0`

Freeze date: 2026-05-14

Canonical baseline: [mvp-v0.1-spec.md](mvp-v0.1-spec.md)

Machine-readable manifest: [mvp-v0.1-release-manifest.json](mvp-v0.1-release-manifest.json)

## Decision

`indbase` MVP v0.1 is frozen as the deterministic local release candidate baseline.

The frozen version is not defined by every feature merely being callable. It is defined by the release close-out standard:

```text
Real material can be ingested.
Bad states are detectable by doctor.
Generated artifacts keep source binding.
Indexes are rebuildable.
Markdown outputs are readable in an Obsidian-style vault.
Repeated use does not drift across revisions, generated outputs, archive/restore, or backup copy.
```

## Checkpoint Documents

The frozen release keeps the checkpoint history under `docs/planning/`:

- [M0-M3 Foundation checkpoint](m0-m3-checkpoint.md)
- [M4 TUI-lite checkpoint](m4-checkpoint.md)
- [M5 Catalog Review checkpoint](m5-checkpoint.md)
- [v0.1 RC checkpoint](v0.1-rc-checkpoint.md)
- [M6 PDF/OCR hardening checkpoint](m6-pdf-ocr-hardening-checkpoint.md)
- [M7 completion checkpoint](m7-completion-checkpoint.md)
- [M8.1 classification hardening checkpoint](m8-classification-hardening-checkpoint.md)
- [M9.3 translation hardening checkpoint](m9-translation-hardening-checkpoint.md)
- [M10 completion checkpoint](m10-completion-checkpoint.md)
- [MVP release gate checkpoint](mvp-release-gate-checkpoint.md)
- [MVP doctor full negative checkpoint](mvp-doctor-full-negative-checkpoint.md)
- [MVP Windows path and Unicode checkpoint](mvp-windows-path-unicode-checkpoint.md)
- [MVP performance smoke checkpoint](mvp-performance-smoke-checkpoint.md)
- [MVP real corpus dogfood checkpoint](mvp-real-corpus-dogfood-checkpoint.md)
- [MVP CLI/TUI acceptance checkpoint](mvp-cli-tui-acceptance-checkpoint.md)
- [MVP backup restore checkpoint](mvp-backup-restore-checkpoint.md)

## Release Gate Results

Latest aggregate release gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\mvp-release-gate-20260514151058313472
MVP_RELEASE_GATE=passed
gates_run=17
```

Hard metrics:

```json
{
  "critical_doctor_findings": 0,
  "unexpected_errors": 0,
  "index_integrity_errors": 0,
  "fts_desync": 0,
  "orphan_files": 0,
  "orphan_records": 0,
  "zero_chunk_current_revisions": 0,
  "unsupported_documents_created": 0,
  "source_shells_searchable": 0,
  "embedded_archived_chunks": 0,
  "embedded_old_revision_chunks": 0,
  "hybrid_results_missing_source_fields": 0,
  "translations_missing_source_binding": 0,
  "candidate_cards_missing_sources": 0,
  "accepted_notes_with_uncited_claims": 0,
  "missing_generated_outputs_undetected": 0
}
```

Release close-out gates:

- `MVP_REAL_CORPUS_DOGFOOD=passed`
- `MVP_CLI_TUI_ACCEPTANCE_GATE=passed`
- `MVP_BACKUP_RESTORE_GATE=passed`
- `MVP_PERF_SMOKE=passed`

## Schema Versions

Frozen schema identities:

- Config: `indbase.config.v1`
- Source Markdown frontmatter: `indbase.source.v1`
- Translation output: `indbase.translation.v1`
- Accepted atomic note: `indbase.atomic_note.v1`
- Database schema state: `schema_migrations` through `0005_candidate_cards`

## Migration Versions

Frozen database migrations:

- `0001_initial`
- `0002_review_resolution_metadata`
- `0003_v02_data_substrate`
- `0004_classification_feedback_audit`
- `0005_candidate_cards`

## Supported Commands

Top-level commands:

```text
indb init
indb ingest
indb doctor
indb search
indb tui
```

Catalog and tags:

```text
indb catalog list
indb catalog add
indb catalog update
indb catalog archive
indb catalog restore
indb tag list
indb tag add
indb tag update
indb tag archive
indb tag restore
```

Documents:

```text
indb doc list
indb doc show
indb doc open
indb doc revisions
indb doc archive
indb doc restore
indb doc set-category
indb doc add-tag
indb doc remove-tag
indb doc tags
```

Observability:

```text
indb task list
indb task show
indb review list
indb review show
indb review resolve
indb review resolve-many
indb error list
indb error show
```

Indexes and search:

```text
indb index status
indb index rebuild --fts
indb index rebuild --vectors
indb search <query> --mode fts
indb search <query> --mode vector
indb search <query> --mode hybrid
```

OCR, classification, translation, and cards:

```text
indb ocr run
indb ocr pages
indb classify suggest
indb classify list
indb classify show
indb classify accept
indb classify reject
indb translate chunks
indb translate document
indb translate list
indb translate show
indb translate open
indb card generate
indb card list
indb card show
indb card accept
indb card reject
```

## Known Limitations

- The default real-corpus gate uses repo-local real project material. Private corpus validation must be run explicitly with `INDB_REAL_CORPUS`.
- `indb ask` and model-backed answer generation are not enabled.
- Embeddings are deterministic local vectors for index and hybrid-search contract validation, not production semantic embeddings.
- Classification, translation, and candidate-card extraction are deterministic local workflows, not model-quality intelligence.
- OCR v0 uses explicit sidecar files; there is no bundled image OCR engine.
- MarkItDown is optional. Office/PDF conversion quality depends on local availability and produces visible review/error state on failure.
- TUI-lite is a guided CLI surface, not a full Textual application.
- Doctor diagnoses corruption and invariant violations but does not auto-fix vaults.
- Backup/restore is validated as whole-vault filesystem copy, not incremental backup, cloud sync, or conflict resolution.
- Performance smoke records telemetry and consistency; it does not define a production SLA.

## Non-Goals

Frozen v0.1 does not include:

- cloud sync
- multi-user support
- physical delete
- hosted database
- hosted queue
- model-backed `ask`
- answer generation
- URL/platform ingest
- full Textual TUI
- external OCR service integration
- remote embedding provider integration
- production SLA guarantees

## Freeze Rules

After this freeze:

- Bug fixes should target a post-freeze patch branch or tag.
- New feature work should start after this baseline, not mutate the frozen checkpoint history.
- New generated artifacts must keep source document, revision, and chunk binding.
- Schema changes must add a new migration instead of editing existing migrations.
