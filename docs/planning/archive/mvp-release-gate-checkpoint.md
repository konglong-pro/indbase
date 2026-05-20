# MVP Release Gate Checkpoint

> **Superseded for “what ships today”** by [../project-status.md](../project-status.md). This file records MVP-era release evidence.

Status: complete

Date: 2026-05-14

Preconditions:

- [v0.1 RC checkpoint](v0.1-rc-checkpoint.md)
- [M6.3 PDF/OCR hardening checkpoint](m6-pdf-ocr-hardening-checkpoint.md)
- [M7 completion checkpoint](m7-completion-checkpoint.md)
- [M8.1 classification hardening checkpoint](m8-classification-hardening-checkpoint.md)
- [M9.3 translation hardening checkpoint](m9-translation-hardening-checkpoint.md)
- [M10 candidate cards completion checkpoint](m10-completion-checkpoint.md)
- [MVP doctor full negative checkpoint](mvp-doctor-full-negative-checkpoint.md)
- [MVP Windows path and Unicode checkpoint](mvp-windows-path-unicode-checkpoint.md)
- [MVP performance smoke checkpoint](mvp-performance-smoke-checkpoint.md)
- [MVP real corpus dogfood checkpoint](mvp-real-corpus-dogfood-checkpoint.md)
- [MVP CLI/TUI acceptance checkpoint](mvp-cli-tui-acceptance-checkpoint.md)
- [MVP backup restore checkpoint](mvp-backup-restore-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

The MVP feature set is complete enough to enter release-candidate close-out.

This checkpoint does not declare the MVP released. It proves that the automated milestone gates can run together and that the core hard metrics remain zero across ingest, search, doctor, metadata, PDF/OCR, embeddings, hybrid search, classification, translation, candidate cards, and generated-artifact integrity checks.

## Gate

The aggregate release gate is:

```powershell
.venv\Scripts\python scripts\mvp_release_gate.py
```

It runs:

- `m3_dogfood_gate.py`
- `m4_tui_lite_gate.py`
- `m5_catalog_review_gate.py`
- `m63_pdf_ocr_hardening_gate.py`
- `m71_embedding_index_gate.py`
- `m72_hybrid_search_gate.py`
- `m8_classification_gate.py`
- `m81_classification_hardening_gate.py`
- `m91_translation_gate.py`
- `m92_translation_gate.py`
- `m93_translation_hardening_gate.py`
- `m10_candidate_cards_gate.py`
- `v01_release_candidate_gate.py`
- `doctor_negative_gate.py`
- `mvp_doctor_full_negative_gate.py`
- `mvp_windows_path_unicode_gate.py`
- `metadata_consistency_gate.py`

The gate writes the full child summaries to:

```text
.tmp/mvp-release-gate-*/gate_summaries.json
```

## Hard Metrics

All release hard metrics must be zero:

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

Latest result:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\mvp-release-gate-20260514151058313472
MVP_RELEASE_GATE=passed
```

Latest summary:

```json
{
  "accepted_notes_with_uncited_claims": 0,
  "candidate_cards_missing_sources": 0,
  "critical_doctor_findings": 0,
  "embedded_archived_chunks": 0,
  "embedded_old_revision_chunks": 0,
  "fts_desync": 0,
  "gates_run": 17,
  "hybrid_results_missing_source_fields": 0,
  "index_integrity_errors": 0,
  "missing_generated_outputs_undetected": 0,
  "mvp_release_gate_complete": 1,
  "orphan_files": 0,
  "orphan_records": 0,
  "source_shells_searchable": 0,
  "translations_missing_source_binding": 0,
  "unexpected_errors": 0,
  "unsupported_documents_created": 0,
  "zero_chunk_current_revisions": 0
}
```

## Release Close-Out

This checkpoint does not itself run the release close-out gates. The close-out pass is recorded separately:

- [MVP real corpus dogfood checkpoint](mvp-real-corpus-dogfood-checkpoint.md)
- [MVP CLI/TUI acceptance checkpoint](mvp-cli-tui-acceptance-checkpoint.md)
- [MVP backup restore checkpoint](mvp-backup-restore-checkpoint.md)

The remaining optional validation is to rerun the real corpus dogfood gate against a private user corpus with `INDB_REAL_CORPUS`.

## Boundary

No M11 is opened by this checkpoint.

The next work is release-candidate close-out, not feature expansion.
