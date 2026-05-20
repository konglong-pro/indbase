# M0-M3 Foundation Checkpoint

Status: complete; M3.1 hardening passed

Date: 2026-05-13

Canonical scope: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

M0 through M3 are complete for the first automated Foundation checkpoint. The M3.1 hardening pass fixed the no-content ingest boundary and unblocks M4 planning when explicitly started.

This status means the core v0.1 data chain is implemented and repeatably verified:

- vault initialization, schema migration, config, categories, task/error/review tables
- single-file and folder ingest
- Tier 1 conversion for `md`, `txt`, `html`, `csv`, `json`
- Tier 2 best-effort MarkItDown adapter for `docx`, `xlsx`, `pptx`
- unsupported-source review visibility
- original preservation
- immutable source Markdown revisions
- re-ingest exact duplicate, no-content-change, and changed-content revision paths
- no-content conversion failure before revision/chunk/FTS creation
- chunking and SQLite FTS indexing
- search snippets with `doc_id`, `revision_id`, `chunk_id`, and snippet
- search query logging without ordinary citation persistence
- archive/restore filtering for default search
- `index rebuild --fts`
- `doctor --json`, including MarkItDown availability reporting

## Verification

Automated checks:

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m compileall -q src tests scripts
```

Latest result:

```text
103 passed
compileall passed
```

Doctor checkpoint smoke:

```text
init -> doctor --json -> ingest -> re-ingest -> search -> doctor
-> archive -> restore -> folder ingest with duplicate/unsupported/Tier2
-> review list -> error list -> task list -> doctor
```

Latest smoke root:

```text
E:\indbase\.tmp\m3-doctor-checkpoint-20260512212353676
```

Smoke summary:

```json
{
  "current_chunks": 3,
  "documents": 3,
  "errors": 0,
  "fts": 3,
  "index_errors": 0,
  "index_reviews": 0,
  "reviews": 1,
  "revisions": 4,
  "source_files": 4,
  "tasks": 4,
  "tier2_converter": {
    "converter_name": "markitdown",
    "status": "succeeded"
  }
}
```

## Dogfood Gate

The M3 dogfood gate is repeatable:

```powershell
.venv\Scripts\python scripts\m3_dogfood_gate.py
```

It creates a temporary vault and ingests a realistic local corpus with:

- Markdown, text, HTML, CSV, JSON
- best-effort `docx`, `xlsx`, `pptx`
- unsupported `pdf` and `png`
- Chinese, English, and Japanese documents
- exact duplicate ingest
- changed-content re-ingest
- empty and garbled text inputs

Latest dogfood result:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m3-dogfood-gate-20260513095857658840
INDEX_REBUILD_EXIT=0
DOCTOR_EXIT=1
```

Latest dogfood summary:

```json
{
  "citations": 0,
  "critical_doctor_findings": 0,
  "current_chunks": 19,
  "documents": 20,
  "duplicate_items": 1,
  "errors": 1,
  "expected_failures": 1,
  "expected_reviews": 2,
  "failed_items": 1,
  "fts": 19,
  "index_integrity_errors": 0,
  "reviews": 3,
  "revisions": 20,
  "search_queries": 8,
  "tasks": 4,
  "unexpected_errors": 0,
  "unsupported_items": 2
}
```

Dogfood warning/failure classification:

- `unsupported.pdf` and `unsupported.png` create `unsupported_source` review items.
- `empty.txt` fails conversion as `no_extractable_content`, keeps the original/source_file trace, and does not create a current revision, chunks, or FTS rows.
- `index rebuild --fts` exits `0` because the index is internally valid.
- `doctor --json` exits `1` because expected review items remain pending; there are no error or critical findings.

CJK search was verified through CLI search against Chinese and Japanese source snippets:

- `大语言模型`
- `幻觉控制`
- `大規模言語モデル`

## Remaining Boundary

At the time of this checkpoint, this did not start M4 or later work. Current M4 status is tracked in [m4-checkpoint.md](m4-checkpoint.md).

Not included in M0-M3:

- full TUI-lite workflows
- OCR
- embeddings or hybrid search
- `indb ask`
- translation
- candidate cards
- cloud sync
- physical delete
- background queue backend
