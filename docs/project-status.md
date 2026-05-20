# indbase Project Status

**As of:** 2026-05-20  
**Package version:** `0.1.0` (PyPI-style; product phases span v0.1 freeze + active v0.2 expansions)

This document is the **single canonical summary of work completed to date**. It replaces reading many scattered checkpoint files for “what exists now.” Detailed specs and historical milestone evidence remain under `docs/planning/` and `docs/agents/`.

## Executive summary

`indbase` is a **local-first personal knowledge database**. The vault is the system of record: originals, immutable source Markdown revisions, chunks, SQLite FTS (and optional vectors), tasks/errors/reviews, and doctor-visible integrity.

| Layer | Status |
| --- | --- |
| **v0.1 Foundation MVP** | Frozen and shipped as `0.1.0`; full MVP feature stack (ingest → search → doctor → archive, plus M4–M10 features) implemented |
| **v0.2 swallow-backed ingest** | **Active expansion — implemented** in core/CLI; production conversion requires `features.swallow_ingest=true` |
| **v0.2 transition-backed output** | **Active expansion — implemented** in core/CLI; requires explicit `indb output runtime install` |
| **v0.3.1 taxonomy foundation** | **Implemented** — typed tags, profiles, taxonomy suggestions, fake LLM harness; see `docs/planning/v0.3.1-taxonomy-foundation.md` |
| **v0.3.2 retrieval intelligence foundation** | **Implemented** — `indb retrieve`, persisted retrieval packages, taxonomy-aware boosts; see `docs/planning/v0.3.2-retrieval-intelligence-foundation.md` |
| **v0.3 intelligent workflow** | Not started (`indb ask`, accepted atomic notes at scale, etc.) |

**Trust model (non-negotiable):** External tools (swallow, transition) may convert or render, but **indbase** owns identity, revisions, promotion, chunks, indexes, artifacts, tasks, errors, and doctor. Candidates and export artifacts are not interchangeable with trusted source revisions.

## Documentation map

| Need | Read |
| --- | --- |
| Current delivery (this file) | `docs/project-status.md` |
| How to test and what passes | `docs/testing.md` |
| Local setup | `docs/development.md` |
| Trust boundaries and modules | `docs/architecture.md` |
| v0.1 product spec | `docs/planning/mvp-v0.1-spec.md` |
| v0.2 swallow spec | `docs/planning/v0.2-swallow-ingest-integration.md` |
| v0.2 transition spec | `docs/planning/v0.2-transition-output-integration.md` |
| v0.3.1 taxonomy spec | `docs/planning/v0.3.1-taxonomy-foundation.md` |
| v0.3.2 retrieval spec | `docs/planning/v0.3.2-retrieval-intelligence-foundation.md` |
| Agent implementation rules | `AGENTS.md`, `docs/agents/*/AGENT.md` |
| Historical milestone checkpoints | `docs/planning/archive/` (evidence archives, not “current status”) |

## v0.1 Foundation MVP (frozen)

Delivered capabilities:

- **Vault:** `indb init`, layout under `.indbase/`, `config.toml`, SQLite migrations `0001`–`0002`
- **Ingest (historical Tier 1/2):** folder/file ingest, originals archive, unsupported/duplicate handling, `ingest_runs` / `ingest_items`
- **Revisions:** immutable `sources/...__rev_NNNN.md`, `doc_id` / `revision_id` stability, re-ingest → new revision
- **Chunks + FTS:** chunking, CJK-friendly `search_text`, default search on **current** source revisions only
- **Observability:** `tasks`, `task_events`, `errors`, `review_items`
- **CLI:** search, catalog, doc, task, review, error, index rebuild (FTS), archive/restore, doctor
- **No** `indb ask`, no physical delete, no cloud sync

Evidence: `docs/planning/archive/mvp-v0.1-freeze.md`, `docs/planning/mvp-v0.1-release-manifest.json`, historical `scripts/mvp_release_gate.py` (MVP-era aggregate).

## MVP extensions (M4–M10, implemented)

Built on the v0.1 substrate; tracked in planning checkpoints:

| Area | Capabilities | Schema / core touchpoints |
| --- | --- | --- |
| **M4 TUI-lite** | Interactive flows (InquirerPy) | CLI |
| **M5 catalog/review** | Catalog templates, review resolution metadata | `0002` |
| **M6 PDF + OCR** | PDF ingest path, OCR runs, page-level artifacts | ingest, `ocr.py`, doctor |
| **M7 embeddings + hybrid** | Vector index, FTS/vector/hybrid search modes | `embeddings.py`, `indexer.py`, `search.py` |
| **M8 classification** | Suggest/accept/reject, feedback audit | `classification.py`, `0004` |
| **M9 translation** | Chunk + full-document translation outputs | `translations.py`, `0003` substrate |
| **M10 candidate cards** | Generate/review/accept cards, atomic notes | `cards.py`, `0005` |

Substrate migration `0003_v02_data_substrate.sql` supports later v0.2 tables and columns.

## v0.2 swallow-backed ingest (implemented)

**Spec:** `docs/planning/v0.2-swallow-ingest-integration.md`  
**Agent guide:** `docs/agents/swallow-ingest-integration/AGENT.md`  
**Migration:** `0006_swallow_ingest_integration.sql`

### Product rules in force

- All **production** conversion goes through **swallow** (local SDK/Core adapter), not HTTP.
- `features.swallow_ingest=false` → `legacy_conversion_retired` (no MarkItDown / direct normalizer fallback).
- Swallow output is a **candidate** until indbase **promotion** accepts it (`trusted-current`, `review-before-current`, etc.).
- **Review / partial** candidates do not become current revision and do not enter default FTS search.
- **Trusted-current** requires durable artifacts (trace, manifest, snapshot where applicable), locators, and policy gates.
- **Archive one-to-many**, **URL/Playwright**, **OCR**, **ASR** paths integrated with promotion and doctor checks.

### Core modules

- `swallow_adapter.py` — boundary types, file/URL/archive expansion
- `promotion_policy.py` — indbase-owned promotion decisions
- `artifact_policy.py`, `candidate_review.py` — review queue integration
- `conversion.py`, `ingest.py` — pipeline wiring
- `doctor.py` — swallow conversion/artifact/archive parent-child integrity

### Configuration

- `features.swallow_ingest` (default `false` on new vaults; enable explicitly or via install flows)
- Optional deps: `uv sync --extra swallow`, `--extra swallow-playwright`

## v0.2 transition-backed output (implemented)

**Spec:** `docs/planning/v0.2-transition-output-integration.md`  
**Agent guide:** `docs/agents/transition-output-integration/AGENT.md`  
**Migration:** `0007_transition_output_integration.sql`

### Product rules in force

- **transition** standardizes/exports via local Node bridge (pinned scaffold in vault runtime; not HTTP, not CLI text parsing).
- `indb output export` → derived artifacts under `outputs/exports/` only; **does not** mutate source documents.
- `indb doc normalize <doc_id> --replace-current` → **new immutable revision**; old revisions and chunks preserved.
- Export artifacts **excluded** from default source search; only promoted source revisions index by default.
- Bridge evidence copied to `.indbase/artifacts/output_runs/<id>/`; transition cache is disposable.
- **Protected content** (frontmatter, IDs, code, math, quotes, etc.) validated before/after bridge (A+B).
- **Locator carry-forward v1 (P1):** exact mapping when heading path + chunk text match.

### Core modules

- `transition_contract.py`, `transition_config.py`, `transition_adapter.py`, `transition_runtime.py`
- `protected_spans.py`, `output_service.py`, `output_evidence.py`, `output_locators.py`, `output_queries.py`
- `transition_templates/` — `transition-bridge.mjs`, pinned `package.json`

### CLI surface

```text
indb output runtime install|status
indb output export source|translation|note
indb output list|show|open
indb doc normalize <doc_id> --replace-current
```

`features.transition_output=true` only after successful `output runtime install`.

## v0.3.2 retrieval intelligence foundation (implemented)

**Spec:** `docs/planning/v0.3.2-retrieval-intelligence-foundation.md`  
**Agent guide:** `docs/agents/v0.3.2-retrieval-intelligence-foundation/AGENT.md`  
**Migration:** `0009_retrieval_intelligence.sql`

### Product rules in force

- **`indb retrieve`** builds citation-ready, persisted retrieval packages (`retrieval_runs`, `retrieval_items`).
- Default base mode **hybrid**; default **per_doc_limit = 3**; does not auto-build profiles.
- Explicit `tag:` / `category:` filters are hard filters; natural-language taxonomy matches are soft boosts only.
- Pending taxonomy suggestions and tag candidates do not affect retrieval.
- Does **not** write `citations`, `search_results`, source revisions, chunks, or taxonomy mutations.
- **`indb search`** behavior unchanged.

### Core modules

- `retrieval.py` — parser, filter resolver, hybrid candidate generation, boosts, quote extraction, persistence

### CLI surface

```text
indb retrieve <query> [--mode fts|vector|hybrid] [--top-k N] [--candidate-k N] [--per-doc-limit N]
indb retrieval list
indb retrieval show <retrieval_run_id>
```

## Release gates and CI (current)

**Canonical gate doc:** `docs/planning/v0.2-release-gate-checkpoint.md`

| Layer | What | PR blocker on GitHub |
| --- | --- | --- |
| A | `pytest` (232 tests) | Yes |
| B | `compileall` | Yes |
| C | `v02_deterministic_release_gate.py` + `doctor_negative_gate.py` | Yes |
| C2 | `v031_taxonomy_release_gate.py` (N1 taxonomy gates) | Yes |
| C3 | `v032_retrieval_release_gate.py` | Yes |
| D | Real swallow + real Node transition smoke | Yes (with deps installed in CI) |
| E | Real-corpus dogfood | No (manual/scheduled workflow) |

**Not PR blockers:** `scripts/m3_dogfood_gate.py` (historical v0.1 assumptions), `scripts/mvp_release_gate.py`, `scripts/v01_release_candidate_gate.py`.

**CI:** `.github/workflows/ci.yml`, `.github/workflows/release-dogfood.yml` on [konglong-pro/indbase](https://github.com/konglong-pro/indbase).

## Explicitly not done / out of scope

- `indb ask` / LLM answers with persisted citations
- Cloud sync, multi-user, hosted queues
- Implicit `npm install` on init/doctor/first export
- transition HTTP service; swallow HTTP service
- Physical delete of revisions/chunks in normal workflows
- Full pinned transition npm HTML/PDF/DOCX render as unconditional CI gates (environment-dependent)

## Recommended reading order for new contributors

1. `README.md` — install and first commands  
2. `docs/architecture.md` — trust boundaries  
3. `docs/project-status.md` (this file) — what is shipped  
4. `docs/testing.md` — how quality is verified  
5. Relevant v0.2 spec + `docs/agents/*/AGENT.md` before changing ingest or output  
