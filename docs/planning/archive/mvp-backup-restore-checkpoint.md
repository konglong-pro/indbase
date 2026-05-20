# MVP Backup Restore Checkpoint

Status: complete

Date: 2026-05-14

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

The MVP backup/restore smoke is complete for local vault copy semantics.

The MVP does not implement cloud sync. The supported recovery model at this checkpoint is a filesystem copy of the vault. The copied vault must remain diagnosable, searchable, and able to open source Markdown, archived originals, translation outputs, and accepted atomic notes.

## Gate

Run:

```powershell
.venv\Scripts\python scripts\mvp_backup_restore_gate.py
```

The gate validates:

- create an original vault
- ingest supported source files
- rebuild FTS and vectors
- create a translation output
- generate and accept a candidate card
- copy the whole vault directory
- rebuild FTS and vectors in the restored copy
- run search and hybrid search in the restored copy
- run doctor in the restored copy
- open source Markdown, archived original, translation output, and accepted atomic note from the restored copy
- compare core DB/object counts before and after restore

## Latest Result

```text
DOGFOOD_ROOT=E:\indbase\.tmp\mvp-backup-restore-gate-20260514144613561407
MVP_BACKUP_RESTORE_GATE=passed
```

Latest summary:

```json
{
  "accepted_note_exists": 1,
  "counts_match_after_restore": 1,
  "critical_doctor_findings": 0,
  "doctor_exit_code": 1,
  "hybrid_results": 2,
  "markdown_open_exists": 1,
  "original_open_exists": 1,
  "restored_accepted_notes": 1,
  "restored_candidate_cards": 1,
  "restored_chunks": 2,
  "restored_documents": 2,
  "restored_embeddings": 2,
  "restored_fts_rows": 2,
  "restored_revisions": 2,
  "restored_translations": 1,
  "search_results": 2,
  "translation_open_exists": 1
}
```

Doctor exit code was `1` because generated review state remains visible. There were no critical doctor findings.

## Boundary

This is a local filesystem copy validation. It does not add incremental backup, cloud sync, encrypted backup, or multi-device conflict resolution.
