# indbase Project Status

**As of:** 2026-06-05
**Package version:** `0.1.0` (PyPI-style; product phases span v0.1 freeze, active v0.2 expansions, v0.3.1 category foundation, v0.3.2 tag governance, v0.3.2.1 tag harness hardening, v0.3.2.2 tag/search governance, and v0.3.2.3/3a/3b consoler probe work)

This document is the **single canonical summary of work completed to date**. It replaces reading many scattered checkpoint files for “what exists now.” Detailed specs and historical milestone evidence remain under `docs/planning/` and `docs/agents/`.

## Executive summary

`indbase` is a **local-first personal knowledge database**. The vault is the system of record: originals, immutable source Markdown revisions, chunks, SQLite FTS (and optional vectors), tasks/errors/reviews, and doctor-visible integrity.

| Layer | Status |
| --- | --- |
| **v0.1 Foundation MVP** | Frozen and shipped as `0.1.0`; full MVP feature stack (ingest → search → doctor → archive, plus M4–M10 features) implemented |
| **v0.2 swallow-backed ingest** | **Active expansion — implemented** in core/CLI; production conversion requires `features.swallow_ingest=true` |
| **v0.2 transition-backed output** | **Active expansion — implemented** in core/CLI; requires explicit `indb output runtime install` |
| **v0.3.1 taxonomy category foundation** | **Active expansion — implemented (core)**; `indbase_default_v1` catalog, profiles/localizations, auditable classification runs, post-ingest taxonomy (`features.category_taxonomy`), category search filter, doctor checks, fixture gate |
| **v0.3.2 tag governance foundation** | **Active expansion — implemented (core)**; migration `0011`, tag resolution/admission/budget/blocklist, deterministic tagger, candidate accept/reject + feedback/audit, relation-backed tag search filter, post-ingest tagging flags (`features.tag_governance`, `features.post_ingest_tagging`), doctor checks, fixture gate + CI job |
| **v0.3.2.1 tag harness hardening** | **Active expansion — implemented (core)**; fixture schema + sanitized suite, isolated eval vault runner (`tag_harness_eval`), stable summary/failure JSON, hard gates, report-only precision/recall, release gate + CI job C2c |
| **v0.3.2.2 tag/search governance** | **Active expansion — implemented (core)**; Search Filter Model, governed `indb search --category`/`--tag`/`--json`, filter-only representative snippets, match explanations, fixture harness + CI job C2d |
| **v0.3.2.3 consoler Source Trust probe** | **Active expansion — implemented (adapter)**; `indbase_agent` command surface, manifest permissions, read-only source/review/task/error/doc views, bounded document artifacts |
| **v0.3.2.3a consoler probe stabilization** | **Active expansion — implemented (local gate)**; deterministic ingest-to-search smoke, document artifact/view assertions, disabled-swallow visibility, environment path checks |
| **v0.3.2.3b consoler read-only views** | **Active expansion — implemented (adapter + local gate)**; document/review/task/error/doctor artifact views, show/list artifact boundaries, bounded current-state view payloads, stable artifact URI errors |
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
| v0.3.1 category foundation spec | `docs/planning/v0.3.1-taxonomy-category-foundation.md` |
| v0.3.2 tag governance spec | `docs/planning/v0.3.2-tag-governance-foundation.md` |
| v0.3.2.1 tag harness spec | `docs/planning/v0.3.2.1-tag-harness-hardening.md` |
| v0.3.2.2 tag/search governance spec | `docs/planning/v0.3.2.2-tag-search-governance.md` |
| v0.3.2.3 consoler Source Trust probe spec | `docs/planning/v0.3.2.3-consoler-source-trust-probe.md` |
| v0.3.2.3a consoler probe stabilization spec | `docs/planning/v0.3.2.3a-consoler-probe-stabilization.md` |
| v0.3.2.3b consoler read-only views spec | `docs/planning/v0.3.2.3b-consoler-read-only-views.md` |
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

- **Spec:** `docs/planning/v0.2-swallow-ingest-integration.md`
- **Agent guide:** `docs/agents/swallow-ingest-integration/AGENT.md`
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

- **Spec:** `docs/planning/v0.2-transition-output-integration.md`
- **Agent guide:** `docs/agents/transition-output-integration/AGENT.md`
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

## v0.3.1 taxonomy category foundation (implemented — core)

- **Spec:** `docs/planning/v0.3.1-taxonomy-category-foundation.md`
- **Agent guide:** `docs/agents/v0.3.1-taxonomy-category-foundation/AGENT.md`
- **ADR:** `docs/adr/0001-auditable-category-classification.md`
- **Migration:** `0008_v031_taxonomy_category_foundation.sql`

Category-only scope delivered in core:

- closed Big Category catalog using `indbase_default_v1`
- English and Chinese localizations over the same stable category IDs
- category profiles with positive and negative boundaries
- post-ingest classification after trusted revisions/chunks/search are created
- auditable classification runs/results, suggestions, and feedback
- category search/filter and doctor checks

**CLI (highlights):** `indb init` defaults to `indbase_default_v1`; `indb catalog list --locale`; `indb catalog ready|unready|profile show`; `indb classify run|feedback`; `indb search --category` or `category:<id> <query>`.

**Gate:** `uv run python scripts/v031_taxonomy_category_release_gate.py` (fixture corpus under `tests/fixtures/v031_category_classification/`).

Enable post-ingest taxonomy in `config.toml`:

```toml
[features]
category_taxonomy = true
```

Not yet implemented in this pass: `catalog profile set`, `catalog migrate-default`, full `classify list/show/accept/reject` for v0.3.1 `category_suggestions` (legacy M8 `classify suggest` remains), exhaustive doctor matrix from the spec.

Tag governance, retrieval packages, `ask`, real model providers, hidden online learning, source mutation, and destructive category migration remain out of scope.

## v0.3.2 tag governance foundation (implemented core)

- **Spec:** `docs/planning/v0.3.2-tag-governance-foundation.md`
- **Agent guide:** `docs/agents/v0.3.2-tag-governance-foundation/AGENT.md`
- **ADR:** `docs/adr/0002-governed-tag-promotion.md`

**Delivered in core/CLI/tests:**

- Migration `0011_v032_tag_governance_foundation.sql` (tagger runs/results, feedback, blocklist, governance events; extended tags/aliases/document_tags/candidates)
- Tag Resolution, Admission Policy, Volume Budget, Blocklist, governance eval compose
- Deterministic local tagger (`run_deterministic_tagger`) with auto-attach of existing canonical tags only
- Candidate accept/reject with Tag Feedback and Tag Governance Events; document-scoped acceptance
- Relation-backed tag filter for `indb search` (`--tag`, `tag:<ref>`) and trusted FTS tag metadata
- Feature flags: `tag_governance`, `post_ingest_tagging` (default off); post-ingest hook after trusted revision/chunks/FTS
- Doctor: tag-governance integrity checks
- Gate: `scripts/v032_tag_governance_release_gate.py` + `tests/fixtures/v032_tag_governance/`; CI job **C2b — v0.3.2 tag governance gate**

**Primary gate metrics (must pass):**

```text
wrong_auto_attached_tags = 0
manual_tags_preserved = true
candidate_count_within_budget = true
new_tag_sprawl_blocked = true
raw_candidates_resolved_before_persist = true
deprecated_merged_archived_not_auto_attached = true
tag_filter_exact = true
candidate_tags_not_search_filterable = true
doctor_hard_findings = 0
```

**Not yet in this pass:** dedicated `indb tag run` / `indb tag candidates` CLI group with stable `--json` (legacy `taxonomy promote-tag` / `reject-tag` call the new review path for scoped candidates). TUI, consoler UI, retrieval packages, `ask`, real providers, embedding-backed taggers, hidden online learning, ontology management, project namespaces, automatic batch propagation, and destructive cleanup remain out of scope.

## v0.3.2.1 tag harness hardening (implemented core)

- **Spec:** `docs/planning/v0.3.2.1-tag-harness-hardening.md`
- **Agent guide:** `docs/agents/v0.3.2.1-tag-harness-hardening/AGENT.md`

**Delivered in core/tests/scripts:**

- Explicit fixture schema and sanitized suite under `tests/fixtures/v0321_tag_harness/`
- Pure evaluation layer `src/indbase_core/tag_harness_eval.py` (isolated eval vault, per-doc deterministic tagger runs, exact expectation checks)
- Stable summary JSON + failure records with reason codes; optional artifact output
- Gate: `scripts/v0321_tag_harness_release_gate.py`; CI job **C2c — v0.3.2.1 tag harness gate**
- Tests: `tests/test_v0321_tag_harness.py`

**Hard gates (must be zero):**

```text
wrong_auto_attached_tags
manual_tag_mutations
trusted_filter_pollution
deprecated_merged_archived_auto_attach
unresolved_candidate_persisted
budget_violations
policy_mutations_by_harness
```

**Report-only:** candidate precision/recall, near-budget pressure, sprawl reason tallies.

**Out of scope:** formal `indb tag harness` CLI, production schema/migrations, providers/embeddings, search ranking changes, retrieval/`ask`, automatic policy mutation, raw private fixture commits.

## v0.3.2.2 tag/search governance (implemented core)

- **Spec:** `docs/planning/v0.3.2.2-tag-search-governance.md`
- **Agent guide:** `docs/agents/v0.3.2.2-tag-search-governance/AGENT.md`

**Delivered in core/tests/scripts:**

- `src/indbase_core/search_filters.py` — normalized filter model (CLI flags + `category:`/`tag:` prefixes, conflict validation)
- `src/indbase_core/search_explanations.py` — governed Search JSON contract
- `governed_search_chunks()` + filter-only representative snippets in `search.py`
- `resolve_governed_tag_filter()` lifecycle semantics in `tag_search.py` (deprecated bindings, archived invalid)
- CLI: `indb search --category`, governed `--tag`, stable `--json`
- Fixture suite `tests/fixtures/v0322_tag_search_governance/` + `tag_search_governance_eval.py`
- Gate: `scripts/v0322_tag_search_governance_release_gate.py`; CI job **C2d — v0.3.2.2 tag/search governance gate** (`needs: [test, tag-harness-v0321]`)
- Tests: `tests/test_v0322_tag_search_governance.py`

**Hard gates:** source exact hits, relation-backed tag filters only, category/tag/text AND, valid empty success, invalid filter errors, stable JSON contract, deterministic filter-only ordering.

**Out of scope:** `ask`, retrieval ranking changes, providers/embeddings, production schema by default, parallel search commands, TUI, doctor repair, OR/semantic expansion.

## v0.3.2.3 / 3a / 3b consoler Source Trust probe (implemented adapter + local gate)

- **Specs:** `docs/planning/v0.3.2.3-consoler-source-trust-probe.md`, `docs/planning/v0.3.2.3a-consoler-probe-stabilization.md`, `docs/planning/v0.3.2.3b-consoler-read-only-views.md`
- **Agent guides:** `docs/agents/v0.3.2.3-consoler-source-trust-probe/AGENT.md`, `docs/agents/v0.3.2.3a-consoler-probe-stabilization/AGENT.md`, `docs/agents/v0.3.2.3b-consoler-read-only-views/AGENT.md`

**Delivered in adapter/tests/scripts:**

- `src/indbase_agent` exposes the first-version Source Trust Loop command surface for consoler.
- Manifest commands include explicit `permissions`; `indbase.ingest_file` is the only write command.
- `indbase.search_sources` delegates to governed v0.3.2.2 source/tag/category semantics and emits bounded `indbase.document` artifacts with `metadata.vault_path`.
- `doc_show` and document artifact views are `doc_id`-only, read-only, and bounded.
- `indbase.document`, `indbase.review_item`, `indbase.task`, `indbase.error`, and `indbase.doctor_report` artifact views report `view_semantics`, `limits`, and `truncated`.
- show commands emit one focused artifact block; list commands emit none; `doctor` emits `indbase://doctor-reports/current`.
- Artifact URI failures use stable explicit errors for invalid URI, unsupported kind, missing object, rejected scope, uninitialized vault, and view-generation failure.
- Gate: `scripts/v0323a_probe_stabilization_release_gate.py` proves deterministic ingest -> revision/chunks/FTS -> search hit -> document artifact/view, plus ordinary `uv run` and local path pollution checks.
- Gate: `scripts/v0323b_consoler_readonly_views_release_gate.py` proves read-only view contracts for document/review/task/error/doctor artifacts on an isolated synthetic vault.

**Out of scope:** consoler protocol/runtime schema changes, Web UI, full TUI, retrieval packages, `ask`, embeddings, generated answers, review/category/tag mutations, doctor repair, full source viewer, revision browser, vault browser, title/path lookup.

## Release gates and CI (current)

**Canonical gate doc:** `docs/planning/v0.2-release-gate-checkpoint.md`

| Layer | What | PR blocker on GitHub |
| --- | --- | --- |
| A | `pytest` (342 collected) | Yes |
| B | `compileall` | Yes |
| C | `v02_deterministic_release_gate.py` + `doctor_negative_gate.py` | Yes |
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
5. Relevant v0.2/v0.3 spec + `docs/agents/*/AGENT.md` before changing ingest, output, category, or tag behavior
