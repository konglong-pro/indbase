# AGENTS.md

This file defines how Codex agents should work in this repository.

For detailed product and architecture planning, use:

- `docs/planning/mvp-v0.1-spec.md`

Treat `docs/planning/mvp-v0.1-spec.md` as the canonical v0.1 specification. Treat overall-plan drafts as orientation only unless they are explicitly reconciled with the v0.1 spec.

Do not duplicate the full MVP specification in this file. Treat this file as the operational rulebook for agents, and treat the planning docs as the source of truth for product and architecture details.

## Documentation Rule

For all documentation work, use the `project-docs-splitter` skill from:

```text
C:\Users\62406\.codex\skills\project-docs-splitter\SKILL.md
```

Documentation rules:

- Keep `README.md` as the quick-start entry point when it exists.
- Put long-form architecture, development, testing, maintenance, troubleshooting, security, and operations content under `docs/`.
- Do not duplicate detailed specs across files.
- Prefer linking to canonical docs over copying large sections.
- If moving Markdown content, preserve links and check relocated references.

## Project Mission

`indbase` is a local-first personal knowledge database. It ingests local materials into a vault, preserves originals, writes readable Markdown, records metadata and immutable revisions, chunks content, builds SQLite FTS indexes, and returns reliable search snippets tied to source chunks.

The first implementation target is **v0.1 Foundation MVP**.

Do not implement v0.2 or v0.3 functionality unless explicitly instructed in a task.

Correct development attitude:

```text
Build a reliable local knowledge substrate first.
Do not build a clever agent before the data layer is trustworthy.
Do not add LLM features before citation, revision, search, and task observability are stable.
```

## Non-Negotiable Principles

### Local First

The vault is the system of record.

Do not design v0.1 around cloud sync, external services, hosted databases, hosted queues, or background SaaS.

### Data Before Intelligence

Do not build `ask`, auto-classification, embeddings, translation, OCR, or candidate cards before the v0.1 data substrate is stable.

### Immutable Revisions

A document revision is an immutable snapshot.

Rules:

```text
doc_id is stable.
current_revision_id is only a pointer.
revision content must not be mutated after creation.
old revisions must not be deleted in normal workflows.
chunks attached to old revisions must not be deleted in normal workflows.
future outputs must bind to source_revision_id, not just doc_id.
```

### Search Is Not Ask

In v0.1:

```text
indb search = retrieve source snippets.
indb ask = not implemented.
```

Do not generate synthetic answers in v0.1.

### Render Search Citations, Do Not Persist Them

In v0.1, search results display citation snippets from chunks, but normal `indb search` must not write rows to the `citations` table by default.

The `citations` table is reserved for generated artifacts in later phases:

```text
answer
card
translation
summary
export
saved_search
```

### No Silent Success

Never mark a task, ingest item, conversion, or index operation as successful if an essential step failed.

Every failure must be visible through:

```text
tasks
task_events
errors
review_items when human review is needed
```

### No Physical Delete In v0.1

v0.1 supports archive/restore, not destructive deletion.

```text
archive -> documents.status = archived
restore -> documents.status = active
delete -> not exposed in v0.1
```

### Do Not Bypass Core

Skills and UI must not write directly to the database or file system in inconsistent ways.

All durable operations should pass through `indbase-core` services.

### No Hidden Intelligence

Do not add LLM calls in v0.1.

`indbase-harness` may contain interfaces and no-op/test providers only. Core must not call harness in v0.1.

### Prefer Boring, Testable Code

MVP v0.1 should be reliable, observable, and testable. Avoid clever abstractions that hide state transitions.

## Phase Boundaries

The project is split into three MVP layers.

### v0.1 Foundation MVP

Goal:

```text
Reliable ingest, metadata, immutable revision, chunking, SQLite FTS,
search snippets, task/error/review visibility, lightweight CLI/TUI.
```

Included:

```text
vault init
schema migration
category template
local file/folder ingest
original archive
Markdown writer
metadata DB
immutable revision
chunker
SQLite FTS
basic search
citation snippets
task table
error log
review queue
ingest_runs / ingest_items
converter_runs
duplicate detection v0
archive/restore
indb doctor
Typer + Rich + InquirerPy TUI-lite
```

M3 is the first automated Foundation checkpoint. At M3, the project must prove the core data chain works through an automated test suite: vault init, category template, Tier 1 ingest, unsupported-source handling, immutable revisions, chunking, SQLite FTS search snippets, archive/restore search filtering, `index rebuild --fts`, and `doctor --json`.

M3 implementation boundaries:

```text
Tier 1 md/txt/html/csv/json is blocking for M3.
Tier 2 docx/xlsx/pptx is best-effort and must not block M3.
HTML uses MarkItDown first and falls back to basic HTML text extraction.
MarkItDown availability is optional; doctor should report it.
Ingest may run synchronously first, but must write tasks/task_events/errors/review state.
Folder ingest with unsupported, duplicate, or failed items uses completed_with_issues.
review list/show is required; review resolve may stay a thin status update.
doctor diagnoses only; no doctor --fix in M3.
```

Not included:

```text
OCR
PDF ingest as a supported pipeline
embedding
hybrid search
LLM answer
ask
auto classification
tag suggestion
translation
candidate cards
Textual full TUI
Bot
URL platform ingest
cloud sync
multi-user
```

### v0.2 Dogfood MVP

Planned additions only. Do not implement during v0.1 unless explicitly requested:

```text
PDF ingest v1
OCR v0
local OCR
OCR quality flags
embedding adapter
vector index
hybrid search
index rebuild for vectors
category suggestion
tag suggestion
confidence gate
classification feedback
selected chunk translation
full document translation
translation records
stronger review queue
stronger harness
```

### v0.3 Intelligent Workflow MVP

Planned additions only. Do not implement during v0.1 unless explicitly requested:

```text
indb ask
answer with citations
candidate card extraction
claim-level citation schema
card review
accepted atomic notes
richer workflows
Textual TUI evaluation or adoption
export markdown bundle
URL ingest v0 evaluation
```

## v0.1 Implementation Rules

### File Support Tiers

Tier 1 strong support:

```text
md
txt
html
csv
json
```

Tier 2 best-effort ingest:

```text
docx
xlsx
pptx
```

Tier 3 unsupported:

```text
all other extensions
```

Rules:

- Unsupported files must not create source Markdown.
- Unsupported files must not update FTS.
- Unsupported files must not block other files in the same folder ingest.
- Unsupported files should create `review_items.type = unsupported_source`.
- PDF is `unsupported_source` in v0.1.

### Technology Choices

Use in v0.1:

```text
Typer
Rich
InquirerPy
SQLite task table
in-process worker
SQLite FTS5
```

Do not use in v0.1:

```text
Textual
Taskiq
Celery
RQ
embedding
hybrid search
rerank
query expansion
LLM answer
```

### CLI Scope

v0.1 commands may include:

```text
indb init
indb ingest <path> [--recursive]
indb search <query>
indb catalog list
indb catalog add <name>
indb task list
indb task show <task_id>
indb task worker
indb review list
indb review show <review_id>
indb review resolve <review_id>
indb error list
indb doc show <doc_id>
indb doc open <doc_id>
indb doc open <doc_id> --original
indb doc open <doc_id> --folder
indb doc open <doc_id> --revision <revision_id>
indb doc archive <doc_id>
indb doc restore <doc_id>
indb doc revisions <doc_id>
indb doc set-category <doc_id> <category_id>
indb doc add-tag <doc_id> <tag>
indb doc remove-tag <doc_id> <tag>
indb index status
indb index rebuild --fts
indb doctor
```

Do not provide in v0.1:

```text
indb ask
indb translate
indb card
indb index rebuild --vectors
indb index rebuild --all
```

### Vault Layout

Create the vault as:

```text
vault/
  inbox/
  sources/
    YYYY/
      MM/
        short-title__doc_YYYYMMDD_<shortid>__rev_0001.md
  notes/
    atomic/
  outputs/
    translations/
    summaries/
  assets/
  .indbase/
    originals/
      YYYY/
        MM/
          doc_YYYYMMDD_<shortid>/
            original.<ext>
    db.sqlite
    indexes/
    logs/
      app.log
      tasks/
    cache/
    config/
      config.toml
```

v0.1 rules:

```text
Only write source documents to sources/.
inbox/ is reserved and unused in v0.1.
notes/atomic/ is reserved for v0.3.
outputs/ is reserved for v0.2/v0.3.
SQLite FTS data lives inside db.sqlite.
.indbase/indexes/ is reserved in v0.1.
```

### Naming Rules

Source Markdown filename format:

```text
<short-title-slug>__doc_YYYYMMDD_<shortid>__rev_0001.md
```

Rules:

```text
doc_id is the only stable identity.
revision_id uses the doc-bound sequence form rev_<doc_id>_0001.
each content revision writes a new immutable Markdown file.
title slug is for Obsidian readability only.
User may provide filename slug during ingest.
If missing, generate slug from title or source filename.
All DB relations, search, chunks, and revisions use doc_id.
Renaming must go through a controlled command that updates canonical_path.
```

Path field meanings:

```text
source_uri = original user input path or future URL, preserved for traceability
normalized_source_uri = normalized source identity used for re-ingest matching
original_path = current/latest archived original in vault
canonical_path = current revision source Markdown path in vault
source_files.original_path = historical immutable archived original path
document_revisions.markdown_path = immutable Markdown path for that revision
```

Do not confuse `source_uri`, `normalized_source_uri`, `original_path`, `canonical_path`, and revision/source-file historical paths.

Metadata edits such as category, tag, title, and archive status must not mutate old revision Markdown or frontmatter. Search and doctor trust the database as the authoritative durable state.

## Required Testing Attitude

When implementing v0.1, tests should cover:

```text
ID generation
path resolver
config loader
schema migrations
task state machine
error recorder
review queue
source inspector
duplicate detector
markdown normalizer
metadata builder
quality validator
chunker
CJK substring fallback
FTS indexer
search renderer
archive/restore
doctor checks
```

Fixture tests should include:

```text
md
txt
html
csv
json
unsupported pdf
unsupported image
Chinese document
English document
Japanese document
duplicate file
low-quality HTML
changed re-ingest producing rev_0002
```

Tier 2 Office fixtures may exist as optional tests, but `docx` / `xlsx` / `pptx` must not block the M3 checkpoint.

## Hard Acceptance Criteria

Foundation MVP is complete only if all 12 conditions are true:

```text
1. indb init creates vault and DB.
2. category template can be selected and written to DB.
3. indb ingest succeeds for a single supported file.
4. indb ingest succeeds for folders; unsupported items do not block the whole task.
5. successful documents preserve original files.
6. successful documents generate readable Markdown with slug + doc_id + revision filename.
7. documents / document_revisions / source_files / chunks records are complete.
8. SQLite FTS can search current source documents.
9. search results include doc_id / revision_id / chunk_id / snippet.
10. task / error / review records are queryable.
11. archive / restore affects default search results.
12. indb doctor can detect missing files, orphan records, and FTS desynchronization.
```

## Implementation Style

When coding:

```text
keep changes small and testable
prefer explicit services over hidden side effects
write migrations before code that depends on schema
write tests for each new workflow
do not add LLM dependencies in v0.1
do not introduce remote services in v0.1
do not add a queue backend in v0.1
do not add Textual in v0.1
```

Recommended implementation order:

```text
1. package skeleton
2. config + path resolver
3. DB connection + schema migrations
4. ID generator
5. vault init
6. category templates
7. task/error/review tables
8. source inspector + archive
9. direct normalizers for Tier 1
10. MarkItDown adapter for Tier 2
11. metadata + revision writing
12. chunker
13. FTS indexer with CJK search_text
14. search command
15. doctor
16. archive/restore
17. TUI-lite flows
```

## Forbidden In v0.1

Do not implement:

```text
PDF ingest pipeline
OCR
embedding
hybrid search
rerank
query expansion
LLM answer
indb ask
auto classification
tag suggestion
translation
candidate cards
Bot
URL platform ingest
Textual full TUI
Taskiq/Celery/RQ
physical delete
cloud sync
multi-user
```

Do not:

```text
mark failed conversions as successful
create documents for unsupported files without Markdown/revision/chunks
persist ordinary search citations by default
use title or filename as stable identity
mutate old revisions
delete old chunks from historical revisions
let skill modules bypass core durable state
call model providers directly
```

## Final Instruction To Codex

When asked to implement a task, first determine which MVP phase it belongs to.

If the task belongs to v0.1, implement it according to this file and `docs/planning/mvp-v0.1-spec.md`.

If the task belongs to v0.2 or v0.3 and the user did not explicitly ask to start that phase, do not implement it. Instead, preserve interfaces only if useful and keep v0.1 stable.

The success metric for v0.1 is not intelligence. The success metric is:

```text
Can indbase reliably ingest local files, preserve originals, write Markdown,
track revisions, chunk content, search with FTS, return source snippets,
show task/error/review state, archive/restore docs, and diagnose vault health?
```

If yes, v0.1 is complete.
