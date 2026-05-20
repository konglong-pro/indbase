# Testing and Release Verification

**As of:** 2026-05-20

This document is the **canonical summary of what is tested and what must pass** for day-to-day development and release. For delivery scope see [project-status.md](project-status.md). For gate policy see [planning/v0.2-release-gate-checkpoint.md](planning/v0.2-release-gate-checkpoint.md).

## Quick commands

```powershell
# Daily (layers A + B)
uv sync --group dev
uv run python -m pytest
uv run python -m compileall -q src tests scripts

# Release (layer C)
uv run python scripts/v02_deterministic_release_gate.py
uv run python scripts/doctor_negative_gate.py

# v0.3.1 taxonomy N1 gates (layer C2)
uv run python scripts/v031_taxonomy_release_gate.py
uv run python scripts/v031_acceptance_e2e.py
uv run python scripts/v032_retrieval_release_gate.py

# v0.3.1 taxonomy dogfood (layer E; repo-local by default)
uv run python scripts/v031_real_corpus_dogfood_gate.py
# Private corpus:
# $env:INDB_REAL_CORPUS='D:\path\to\files'
# uv run python scripts/v031_real_corpus_dogfood_gate.py

# Optional aggregate (D/E skip unless env set)
uv run python scripts/v02_release_gate.py
```

## GitHub Actions (required on PR)

Workflow: [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)

| Check name | Layer | Command |
| --- | --- | --- |
| A/B — pytest + compileall | A, B | `uv run python -m pytest`, `compileall` |
| C — v0.2 deterministic vault + doctor negative | C | deterministic + doctor negative scripts |
| C2 — v0.3.1 taxonomy N1 gates | C2 | `scripts/v031_taxonomy_release_gate.py` (n1–n6) + `scripts/v031_acceptance_e2e.py` |
| C3 — v0.3.2 retrieval release gate | C3 | `scripts/v032_retrieval_release_gate.py` |
| D — real swallow smoke | D | `INDBASE_SWALLOW_SMOKE=1`, `uv sync --extra swallow` |
| D — real Node transition smoke | D | `INDBASE_TRANSITION_SMOKE=1`, Node 20 |

Layer **E** (real corpus): [`.github/workflows/release-dogfood.yml`](../.github/workflows/release-dogfood.yml) — manual or weekly; uses `tests/fixtures/v02_dogfood_corpus/` or repo variable `INDB_REAL_CORPUS`.

**Not run in CI:** `scripts/m3_dogfood_gate.py` (historical v0.1; fails under v0.2 defaults by design).

## Pytest suite (layer A)

**Total:** 232 tests in `tests/` (collected with `python -m pytest --collect-only`).

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
| `test_ingest_pipeline.py` | 12 | M2/M3 pipelines, folder issues, re-ingest |
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
| `test_vault.py` | 2 | Init layout |
| `test_revisions.py` | 2 | Immutable revision files |
| `test_documents.py` | 2 | Document metadata |
| `test_ids.py` | 3 | ID formats |
| `test_imports.py` | 1 | Package import smoke |
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
| `v031_taxonomy_release_gate.py` | C2 | Schema/doctor, profile/features, tag candidates, category manager, janitor audit, fake LLM harness (n1–n6) |
| `v031_real_corpus_dogfood_gate.py` | E (v0.3.1) | Repo-local or `INDB_REAL_CORPUS`: ingest → profiles → taxonomy analyze/suggest → janitor → doctor hard = 0 |
| `v032_retrieval_release_gate.py` | C3 | Deterministic retrieve packages, explicit filters, per-doc limit, no citations/search_results writes |

Shared helpers: `scripts/gate_common.py`, `scripts/taxonomy_gate_common.py`.

### v0.3.2 retrieval tests

| Tests | What they prove |
| --- | --- |
| `test_retrieval.py` | Parser/filters, persistence, exact quotes, tag hard filter, per-doc limit, archive exclusion, CLI retrieve/list/show |

### v0.3.1 taxonomy tests

| Tests | What they prove |
| --- | --- |
| `test_taxonomy_schema.py` | Migration `0008`, typed tags, doctor taxonomy integrity |
| `test_profile_taxonomy.py` | Profile build, feature atoms, deterministic taxonomy analyze |
| `test_taxonomy_slice3.py` | Suggestions accept/reject, category manager, tag candidates, janitor audit-only |
| `test_llm_harness.py` | Fake provider, schema/quote validation, `model_calls`, no direct provider imports |

## Historical MVP gates (reference only)

These scripts validated the **MVP v0.1 era** feature matrix. They remain in `scripts/` for regression archaeology but **are not** the active v0.2 PR standard:

```text
m3_dogfood_gate.py          # v0.1 ingest/search assumptions
m4_tui_lite_gate.py … m10_* # feature milestones
mvp_release_gate.py         # MVP aggregate
v01_release_candidate_gate.py
doctor_negative_gate.py     # REPLACED for v0.2 (same filename, new behavior)
```

## Opt-in local smoke

```powershell
$env:INDBASE_SWALLOW_SMOKE='1'
uv sync --extra swallow
uv run python scripts/v02_swallow_smoke_gate.py

$env:INDBASE_TRANSITION_SMOKE='1'
uv run python scripts/v02_transition_smoke_gate.py
# or: uv run python -m pytest tests/test_transition_bridge_smoke.py -m transition_smoke
```

## What passing means (release bar)

```text
232/232 pytest passed
compileall clean
v02 deterministic gate passed
doctor negative gate passed
GitHub CI green (A–D on ubuntu-latest)
doctor hard findings = 0 on deterministic healthy vault
```

Formal release additionally expects layer **E** on a real corpus (or documented waiver via `INDB_V02_DOGFOOD_WAIVED`).
