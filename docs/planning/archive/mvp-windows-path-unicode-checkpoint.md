# MVP Windows Path and Unicode Checkpoint

Status: complete

Date: 2026-05-14

Precondition: [MVP Release Gate Checkpoint](mvp-release-gate-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

The Windows path and Unicode filename gate is complete for MVP release close-out.

This gate validates Windows-local file system behavior with realistic path shapes. It does not add a product feature; it verifies that ingest, archive, source Markdown writing, search, doctor, vector rebuild, and CLI `doc open` keep working when paths include spaces, CJK characters, Japanese text, emoji, long names, read-only files, case variants, duplicate renamed files, unsupported files, and Windows reserved basenames.

## Gate

Run:

```powershell
.venv\Scripts\python scripts\mvp_windows_path_unicode_gate.py
```

The gate covers:

- vault path containing spaces and Chinese characters
- source folder containing spaces, Chinese characters, and Japanese characters
- Chinese file names
- Japanese file names
- emoji file names
- long file names
- nested Unicode paths
- read-only source files
- same filename with different case in different folders
- renamed duplicate file
- unsupported `png` and `zip`
- `CON.txt` and `AUX.md` when the OS allows creating them
- slug output for Windows reserved names and illegal filename characters
- `doc open` and `doc open --original`

## Slug Rule Hardening

This checkpoint hardens `slugify`:

- source Markdown slugs are capped at 80 characters
- Windows reserved basenames such as `CON`, `AUX`, `NUL`, `COM1`, and `LPT1` are suffixed with `-file`
- illegal Windows filename characters are converted to hyphen-separated ASCII slugs

The stable identity remains `doc_id`; slug changes only affect future readability names.

## Latest Result

```text
DOGFOOD_ROOT=E:\indbase\.tmp\mvp-windows-path-unicode-gate-20260514120041333806
MVP_WINDOWS_PATH_UNICODE_GATE=passed
```

Latest hard metrics:

```json
{
  "critical_doctor_findings": 0,
  "doc_open_failures": 0,
  "fts_rebuild_failed_documents": 0,
  "missing_canonical_files": 0,
  "missing_original_files": 0,
  "slug_illegal_char_outputs": 0,
  "slug_overlong_outputs": 0,
  "slug_reserved_collisions": 0,
  "source_shells_searchable": 0,
  "unsafe_canonical_paths": 0,
  "unsafe_original_paths": 0,
  "unsupported_documents_created": 0,
  "vector_rebuild_failed_chunks": 0,
  "zero_chunk_current_revisions": 0
}
```

Positive proof metrics:

```json
{
  "cjk_search_results": 1,
  "duplicate_items": 1,
  "emoji_path_search_results": 1,
  "japanese_search_results": 1,
  "readonly_documents": 1,
  "space_path_search_results": 1,
  "unicode_documents": 11,
  "unsupported_items": 2
}
```

## Boundary

This gate is still synthetic. Broader release close-out is covered by separate gates:

- [MVP real corpus dogfood checkpoint](mvp-real-corpus-dogfood-checkpoint.md)
- [MVP CLI/TUI acceptance checkpoint](mvp-cli-tui-acceptance-checkpoint.md)
- [MVP backup restore checkpoint](mvp-backup-restore-checkpoint.md)
