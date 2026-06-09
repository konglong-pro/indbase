# Testing and Release Verification

**As of:** 2026-06-09

This document is the **canonical summary of what is tested and what must pass** for day-to-day development and release. For delivery scope see [project-status.md](project-status.md). For historical v0.2 gate policy see [planning/archive/v0.2/release-gate-checkpoint.md](planning/archive/v0.2/release-gate-checkpoint.md).

## Quick commands

```powershell
# Daily (layers A + B)
uv sync --group dev
uv run python -m pytest
uv run python -m compileall -q src tests scripts

# Release (layer C)
uv run python scripts/v02_deterministic_release_gate.py
uv run python scripts/doctor_negative_gate.py

# v0.3.1 category foundation (when touching taxonomy)
uv run python scripts/v031_taxonomy_category_release_gate.py

# v0.3.2 tag governance foundation (when touching tag governance)
uv run python scripts/v032_tag_governance_release_gate.py

# v0.3.2.1 tag harness hardening (when touching tag harness)
uv run python scripts/v0321_tag_harness_release_gate.py

# v0.3.2.2 tag/search governance (when touching governed search)
uv run python scripts/v0322_tag_search_governance_release_gate.py

# v0.3.2.3a consoler probe stabilization (when touching indbase_agent)
uv run python scripts/v0323a_probe_stabilization_release_gate.py

# v0.3.2.3b consoler read-only views (when touching indbase_agent)
uv run python scripts/v0323b_consoler_readonly_views_release_gate.py

# v0.3.2.3c consoler variant coordination (when coordinating consoler v4d)
uv run python -m pytest tests/test_v0323c_indbase_coordination.py -q

# v0.3.2.3d indbase variant intent drafting coordination (when coordinating consoler v4e)
uv run python -m pytest tests/test_v0323d_indbase_intent_coordination.py -q

# v0.3.2.3e Source Trust real dogfood friction pass coordination
# Docs/status coordination only unless indbase_agent code changes.
# Consoler owns the V4f gate: pnpm test:v4f-indbase-real-dogfood-friction-pass

# v0.3.2.3f indbase NL v2 intent drafting coordination
uv run python -m pytest tests/test_v0323f_indbase_nl_v2_coordination.py -q
# Consoler owns the V4g gate: pnpm test:v4g-indbase-nl-v2-intent-drafting

# v0.3.3 retrieval evaluation / answer readiness
uv run python -m pytest tests/test_retrieval_evaluation.py -q
uv run python scripts/v033_retrieval_eval_release_gate.py

# v0.3.4 provider evidence / trust correlation
uv run python scripts/check_docs.py
uv run python -m pytest tests/test_provider_contracts.py tests/test_provider_runs.py -q
uv run python scripts/provider_fake_release_gate.py
$env:INDBASE_PROVIDER_SMOKE_REQUIRED='1'  # formal release only
uv run python scripts/provider_real_smoke.py

# Optional aggregate (D/E skip unless env set)
uv run python scripts/v02_release_gate.py
```

## GitHub Actions (required on PR)

Workflow: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

The `test` job also runs `uv run python scripts/check_docs.py` to enforce the
documentation lifecycle gate. The `provider-v034` job runs the v0.3.4 provider
fake release gate.

| Check name | Layer | Command |
| --- | --- | --- |
| A/B — pytest + compileall | A, B | `uv run python -m pytest`, `compileall` |
| C — v0.2 deterministic vault + doctor negative | C | deterministic + doctor negative scripts |
| D — real swallow smoke | D | `INDBASE_SWALLOW_SMOKE=1`, `uv sync --extra swallow` |
| D — real Node transition smoke | D | `INDBASE_TRANSITION_SMOKE=1`, Node 20 |
| v0.3.1 — taxonomy category gate | v0.3.1 | `scripts/v031_taxonomy_category_release_gate.py` |
| C2b — v0.3.2 tag governance gate | v0.3.2 | `scripts/v032_tag_governance_release_gate.py` |
| C2c — v0.3.2.1 tag harness gate | v0.3.2.1 | `scripts/v0321_tag_harness_release_gate.py` |
| C2d — v0.3.2.2 tag/search governance gate | v0.3.2.2 | `scripts/v0322_tag_search_governance_release_gate.py` |
| C2e - v0.3.2.3 consoler coordination gates | v0.3.2.3c/3d/3f | `tests/test_v0323c_indbase_coordination.py`, `tests/test_v0323d_indbase_intent_coordination.py`, `tests/test_v0323f_indbase_nl_v2_coordination.py` |
| C4 - v0.3.3 retrieval eval/readiness gate | v0.3.3 | `scripts/v033_retrieval_eval_release_gate.py` |
| C5 - v0.3.4 provider fake gate | v0.3.4 | `scripts/provider_fake_release_gate.py` |
| retired template guard | guard | no `minimal` template in `scripts/`, `README.md`, `docs/development.md` |

Layer **E** (real corpus): [`.github/workflows/release-dogfood.yml`](../.github/workflows/release-dogfood.yml) — manual or weekly; uses `tests/fixtures/v02_dogfood_corpus/` or repo variable `INDB_REAL_CORPUS`.

**Not run in CI:** `scripts/m3_dogfood_gate.py` (historical v0.1; fails under v0.2 defaults by design).

## Pytest suite (layer A)

**Closeout run:** 362 passed and 1 skipped in `tests/` on 2026-06-09.
Environment-gated smoke tests skip unless their prerequisites are enabled.

Configuration: `pyproject.toml` → `[tool.pytest.ini_options]` (`testpaths = ["tests"]`, `pythonpath = ["src"]`).

### Global fixtures

| Mechanism | Role |
| --- | --- |
| `tests/conftest.py` `fake_swallow_file_conversion` (autouse) | Patches `SwallowIngestAdapter.convert_file` for deterministic ingest tests; enables `swallow_ingest` in test vault configs |
| `@pytest.mark.no_fake_swallow_conversion` | Opt out of fake swallow for specific tests |
| `@pytest.mark.transition_smoke` | Real Node bridge test; requires `INDBASE_TRANSITION_SMOKE=1` |

### Coverage by module

| Tests | Count | What they prove |
| --- | ---: | --- |
| `test_doctor.py` | 29 | Vault health: missing files, orphans, FTS desync, swallow/output integrity, cards, translations |
| `test_cli.py` | 20 | CLI ingest/search/archive; swallow archive one-to-many; URL snapshot |
| `test_conversion.py` | 16 | Swallow promotion, legacy retired, OCR/ASR/URL paths, artifacts |
| `test_ocr.py` | 16 | OCR runs, pages, doctor |
| `test_classification.py` | 14 | Suggest/accept/reject, audit |
| `test_search.py` | 13 | FTS snippets, doc/rev/chunk binding, archive filter |
| `test_ingest_pipeline.py` | 14 | M2/M3 pipelines, folder issues, re-ingest |
| `test_translations.py` | 12 | Chunk/document translation records |
| `test_cards.py` | 9 | Candidate card lifecycle |
| `test_search_text.py` | 9 | CJK/text normalization for search |
| `test_embeddings.py` | 7 | Vector index behavior |
| `test_indexer.py` | 6 | FTS rebuild, per-doc reindex |
| `test_normalizers.py` | 6 | Tier 1 normalizers (fixture baseline) |
| `test_paths.py` | 6 | Vault path resolver |
| `test_promotion_policy.py` | 6 | Swallow promotion gate rules |
| `test_archive.py` | 3 | Archive/restore search filtering |
| `test_config.py` | 3 | Config load/save, feature flags |
| `test_chunker.py` | 5 | Chunk boundaries, hashes |
| `test_observability.py` | 3 | Tasks, errors |
| `test_ingest_plan.py` | 3 | Source inspection / planning |
| `test_source_inspector.py` | 4 | Tier classification |
| `test_output_service.py` | 5 | Export, normalize rev_0002, evidence, locators |
| `test_protected_spans.py` | 3 | Protected span extraction |
| `test_transition_config.py` | 2 | Whitelist transition config |
| `test_transition_partial_evidence.py` | 2 | Failed export partial evidence |
| `test_output_doctor.py` | 2 | Output hash mismatch, runtime flag |
| `test_output_queries.py` | 1 | Output run queries |
| `test_swallow_adapter.py` | 2 | Adapter boundary |
| `test_db.py` | 2 | Migrations applied |
| `test_vault.py` | 2 | Init layout, `indbase_default_v1` template |
| `test_v031_category_taxonomy.py` | 8 | v0.3.1 profiles, classifier, search filter, post-ingest taxonomy |
| `test_v032_tag_governance.py` | 22 | v0.3.2 migration, resolution, tagger, accept/reject, relation-backed tag filter |
| `test_v032_post_ingest_tagging.py` | 2 | Post-ingest tag governance feature flags |
| `test_v0321_tag_harness.py` | 8 | v0.3.2.1 fixture schema, summary/failure contracts, harness suite |
| `test_v0322_tag_search_governance.py` | 8 | v0.3.2.2 search filter model, governed `--json`, tag/search harness suite |
| `test_revisions.py` | 2 | Immutable revision files |
| `test_documents.py` | 2 | Document metadata |
| `test_ids.py` | 3 | ID formats |
| `test_imports.py` | 1 | Package import smoke |
| `test_indbase_agent.py` | 14 | Agent adapter previews, ingest execution blocks, source-trust stabilization, duplicate interaction, artifact views |
| `test_indbase_agent_readonly_views.py` | 6 | v0.3.2.3b read-only artifact views, budgets, URI errors, show/list artifact boundaries, doctor read-only semantics |
| `test_v0323c_indbase_coordination.py` | 2 | v0.3.2.3c indbase-side manifest contract for the consoler variant action surface and artifact open/back kinds |
| `test_v0323d_indbase_intent_coordination.py` | 3 | v0.3.2.3d indbase-side manifest/static contract for consoler-owned deterministic intent drafting and no indbase core NL parser |
| `test_v0323f_indbase_nl_v2_coordination.py` | 3 | v0.3.2.3f indbase-side docs/manifest/static contract for consoler-owned opt-in assisted NL v2 and no indbase core NL/provider dependency |
| `test_retrieval_evaluation.py` | 10 | v0.3.3 eval JSONL import/export, eval runs, answer readiness, CLI, and doctor integrity checks |
| `test_transition_bridge_smoke.py` | 1 | Real Node subprocess (opt-in env) |

### v0.2-specific automated proofs

| Concern | Tests / gates |
| --- | --- |
| Swallow-only production path | `test_conversion.py`, promotion tests, deterministic gate |
| Candidate ≠ revision | `test_promotion_policy.py`, deterministic gate (review not searchable) |
| Archive parent/child | `test_cli.py`, `test_doctor.py`, deterministic gate |
| Output export ≠ source mutation | `test_output_service.py`, deterministic gate |
| Normalize → new revision | `test_output_service.py`, deterministic gate |
| Partial export evidence | `test_transition_partial_evidence.py` |
| Doctor negative scenarios | `scripts/doctor_negative_gate.py` (8 corruption scenarios) |

## Script gates (layers C–E)

| Script | Layer | Pass criteria |
| --- | --- | --- |
| `v02_deterministic_release_gate.py` | C | v0.2 vault: trusted ingest, review blocked from search, archive, URL, export, normalize, doctor hard = 0 |
| `doctor_negative_gate.py` | C | Healthy vault corrupted; doctor exit 2 with expected codes |
| `v02_swallow_smoke_gate.py` | D | Real swallow, long fixture → `trusted-current` + searchable |
| `v02_transition_smoke_gate.py` | D | Real `node` bridge subprocess + evidence archive |
| `v02_real_corpus_dogfood_gate.py` | E | Folder ingest + doctor hard = 0 on real/staged corpus |
| `v02_release_gate.py` | A–E aggregate | All required layers; D/E per env |
| `v031_taxonomy_category_release_gate.py` | v0.3.1 | Fixture classifier: zero wrong confident assignments; manual preserve; doctor hard = 0 |
| `v032_tag_governance_release_gate.py` | v0.3.2 | Fixture tagger: zero wrong auto-attaches; tag filter exact; candidates not filterable; scoped doctor hard = 0 |
| `v0321_tag_harness_release_gate.py` | v0.3.2.1 | Isolated eval vault harness: hard gates zero; stable summary JSON; report-only precision/recall |
| `v0322_tag_search_governance_release_gate.py` | v0.3.2.2 | Governed source search harness: AND filters, filter-only snippets, JSON contract, hard gates zero |
| `v0323a_probe_stabilization_release_gate.py` | v0.3.2.3a | Deterministic consoler Source Trust probe: ingest -> search hit -> document artifact/view, environment checks |
| `v0323b_consoler_readonly_views_release_gate.py` | v0.3.2.3b | Isolated read-only view gate: document/review/task/error/doctor artifact views, metadata, budgets, stable URI errors, doctor read-only |
| `v033_retrieval_eval_release_gate.py` | v0.3.3 | Deterministic eval fixture import/run, answer readiness verdict coverage, no citations writes, doctor hard = 0 |
| `provider_contract_gate.py` | v0.3.4 | Provider contract compile and focused contract tests |
| `provider_fake_release_gate.py` | v0.3.4 C | Provider contracts, fake ingest/output behavior, evidence copy, provider runs, output trust, and provider artifact views |
| `provider_real_smoke.py` | v0.3.4 D | Environment-gated real provider smoke; missing runtime is explicit skip by default and fail when `INDBASE_PROVIDER_SMOKE_REQUIRED=1` |

Shared helpers: `scripts/gate_common.py`.

New vault initialization in gates and tests uses `indbase_default_v1` only (`minimal` / `academic` / `full` are retired for new init).

## Historical MVP gates (reference only)

These scripts validated the **MVP v0.1 era** feature matrix. They remain in `scripts/` for regression archaeology but **are not** the active v0.2 PR standard:

```text
m3_dogfood_gate.py          # v0.1 ingest/search assumptions
m4_tui_lite_gate.py … m10_* # feature milestones
mvp_release_gate.py         # MVP aggregate
v01_release_candidate_gate.py
doctor_negative_gate.py     # REPLACED for v0.2 (same filename, new behavior)
```

`m6_pdf_ingest_gate.py` and `m63_pdf_ocr_hardening_gate.py` are retained as
historical PDF/OCR guards. In the provider era they validate that legacy PDF
direct conversion is visibly retired as `legacy_conversion_retired` and does
not create revisions or default-searchable chunks. Real PDF provider success is
covered by environment-gated provider smoke, not by these historical gates.

## Opt-in local smoke

```powershell
$env:INDBASE_SWALLOW_SMOKE='1'
uv sync --extra swallow
uv run python scripts/v02_swallow_smoke_gate.py

$env:INDBASE_TRANSITION_SMOKE='1'
uv run python scripts/v02_transition_smoke_gate.py
# or: uv run python -m pytest tests/test_transition_bridge_smoke.py -m transition_smoke
```

## Closeout Archive

Historical closeout evidence is retained under `docs/testing/archive/`. These
files are not default reading; start from `docs/project-status.md` or
`docs/phase-manifest.yaml` when doing phase archaeology.

| Phase | Closeout | Notes |
| --- | --- | --- |
| v0.1 | [archive/v0.1-foundation-closeout.md](testing/archive/v0.1-foundation-closeout.md) | Foundation baseline |
| v0.2 swallow | [archive/v0.2-swallow-ingest-closeout.md](testing/archive/v0.2-swallow-ingest-closeout.md) | Swallow ingest integration |
| v0.2 transition | [archive/v0.2-transition-output-closeout.md](testing/archive/v0.2-transition-output-closeout.md) | Transition output integration |
| v0.3.1 broad taxonomy | [archive/v0.3.1-taxonomy-foundation-closeout.md](testing/archive/v0.3.1-taxonomy-foundation-closeout.md) | Superseded broad taxonomy plan |
| v0.3.1 | [archive/v0.3.1-taxonomy-category-foundation-closeout.md](testing/archive/v0.3.1-taxonomy-category-foundation-closeout.md) | Taxonomy category foundation |
| v0.3.2 | [archive/v0.3.2-tag-governance-foundation-closeout.md](testing/archive/v0.3.2-tag-governance-foundation-closeout.md) | Tag governance foundation |
| v0.3.2 retrieval | [archive/v0.3.2-retrieval-intelligence-closeout.md](testing/archive/v0.3.2-retrieval-intelligence-closeout.md) | Retrieval intelligence foundation |
| v0.3.2.1 | [archive/v0.3.2.1-tag-harness-hardening-closeout.md](testing/archive/v0.3.2.1-tag-harness-hardening-closeout.md) | Tag harness hardening |
| v0.3.2.2 | [archive/v0.3.2.2-tag-search-governance-closeout.md](testing/archive/v0.3.2.2-tag-search-governance-closeout.md) | Tag/search governance |
| v0.3.2.3 | [archive/v0.3.2.3-closeout.md](testing/archive/v0.3.2.3-closeout.md) | Source Trust probe |
| v0.3.2.3a | [archive/v0.3.2.3a-closeout.md](testing/archive/v0.3.2.3a-closeout.md) | Probe stabilization |
| v0.3.2.3b | [archive/v0.3.2.3b-closeout.md](testing/archive/v0.3.2.3b-closeout.md) | Read-only artifact views |
| v0.3.2.3c | [archive/v0.3.2.3c-closeout.md](testing/archive/v0.3.2.3c-closeout.md) | Consoler variant dogfood UX |
| v0.3.2.3d | [archive/v0.3.2.3d-closeout.md](testing/archive/v0.3.2.3d-closeout.md) | Deterministic intent drafting coordination |
| v0.3.2.3e | [archive/v0.3.2.3e-closeout.md](testing/archive/v0.3.2.3e-closeout.md) | Real dogfood friction pass |
| v0.3.2.3f | [archive/v0.3.2.3f-closeout.md](testing/archive/v0.3.2.3f-closeout.md) | Opt-in assisted NL v2 coordination |
| v0.3.4 | [archive/v0.3.4-provider-evidence-trust-correlation-closeout.md](testing/archive/v0.3.4-provider-evidence-trust-correlation-closeout.md) | Provider evidence / trust correlation |

## What passing means (release bar)

```text
362 passed, 1 skipped
compileall clean
v02 deterministic gate passed
doctor negative gate passed
v031 taxonomy category gate passed (when touching taxonomy)
v032 tag governance gate passed (when touching tag governance)
v0321 tag harness gate passed (when touching tag harness)
v0322 tag/search governance gate passed (when touching governed search)
v0323a consoler probe stabilization gate passed (when touching indbase_agent)
v0323b consoler read-only views gate passed (when touching indbase_agent)
v0323c indbase coordination contract passed (when coordinating consoler v4d)
v0323d indbase intent coordination contract passed (when coordinating consoler v4e)
v0323e consoler V4f friction gate passed in E:\consoler (when coordinating consoler v4f)
v0323f indbase NL v2 coordination contract passed (when coordinating consoler v4g)
v033 retrieval eval/readiness gate passed (when touching retrieval evaluation)
v034 provider fake gate passed
GitHub CI green (A–D + v0.3.1/v0.3.2/v0.3.2.1/v0.3.2.2 gates on ubuntu-latest)
doctor hard findings = 0 on deterministic healthy vault
```

Formal release additionally expects layer **E** on a real corpus (or documented waiver via `INDB_V02_DOGFOOD_WAIVED`).
