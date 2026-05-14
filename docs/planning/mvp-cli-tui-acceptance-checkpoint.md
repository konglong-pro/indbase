# MVP CLI/TUI Acceptance Checkpoint

Status: complete

Date: 2026-05-14

Canonical baseline: [mvp-v0.1-spec.md](mvp-v0.1-spec.md)

## Decision

The MVP manual-style CLI/TUI acceptance pass is complete.

This gate runs real `indb` commands rather than calling core services directly. TUI-lite coverage uses non-interactive `--action` paths so it is repeatable in automation while still exercising the same user-facing command surface.

## Gate

Run:

```powershell
.venv\Scripts\python scripts\mvp_cli_tui_acceptance_gate.py
```

The gate validates the daily command surface:

- init and ingest
- search with FTS, vector, and hybrid modes
- document list/show/open/revisions/archive/restore
- catalog add/update/archive/restore
- tag add/update/archive/restore and document tag edits
- index status and FTS/vector rebuild
- classification suggest/list/show/accept/reject
- selected-chunk and full-document translation
- translation list/show/open
- candidate card generate/list/show/accept/reject
- review list/show/resolve
- error list/show
- task list/show
- doctor
- TUI-lite dashboard, search, task queue, review list, error viewer, settings, and ingest action

## Latest Result

```text
DOGFOOD_ROOT=E:\indbase\.tmp\mvp-cli-tui-acceptance-20260514144529873791
MVP_CLI_TUI_ACCEPTANCE_GATE=passed
```

Latest summary:

```json
{
  "accepted_cards": 1,
  "chunks": 3,
  "classification_scanned_documents": 2,
  "cli_core_commands_run": 54,
  "commands_run": 61,
  "critical_doctor_findings": 0,
  "doctor_exit_code": 1,
  "documents": 4,
  "embeddings": 2,
  "errors": 1,
  "fts_rows": 3,
  "rejected_cards": 1,
  "reviews": 4,
  "tasks": 11,
  "translations": 2,
  "tui_actions_run": 7
}
```

Doctor exit code was `1` because the corpus intentionally includes visible review/error state. There were no critical doctor findings.

## Boundary

This gate is automated manual-style acceptance. It does not replace a human visual pass through an actual interactive terminal session, but it verifies the complete CLI/TUI-lite command surface without hidden core bypasses.
