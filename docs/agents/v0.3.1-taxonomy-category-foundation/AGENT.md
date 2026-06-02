# v0.3.1 Taxonomy Category Foundation Agent Guide

Read this before implementing v0.3.1 category foundation work.

Canonical spec:

- `docs/planning/v0.3.1-taxonomy-category-foundation.md`

Required context:

- `AGENTS.md`
- `CONTEXT.md`
- `docs/adr/0001-auditable-category-classification.md`
- `docs/project-status.md`
- `docs/testing.md`
- `docs/planning/mvp-v0.1-spec.md`
- `docs/planning/v0.2-swallow-ingest-integration.md`
- `docs/planning/v0.2-transition-output-integration.md`

## Objective

Make Big Category assignment trustworthy enough to support later tag governance, retrieval, and ask.

Success means:

```text
Confident automatic category assignments are correct in fixture gates.
Uncertain documents abstain or go to review.
Manual and accepted category decisions are preserved.
Every automatic decision is auditable.
```

## Scope

Implement only category foundation:

- closed Big Category catalog
- `indbase_default_v1`
- English and Chinese localizations for the same category IDs
- category profiles and readiness
- deterministic category classifier
- post-ingest taxonomy stage
- auditable classification runs/results
- category suggestions and feedback
- category search/filter
- doctor checks
- fixture suite and release gate

Do not implement:

- tag governance
- tag aliases, merges, deprecations, blocked tags
- tag candidate volume control
- retrieval packages
- ask or answer generation
- real LLM providers
- hidden online learning
- destructive category migration

## Assumptions

- This phase starts from the current v0.2 substrate: trusted current revisions, chunks, FTS, tasks/errors/reviews, swallow ingest, and transition output already exist.
- The latest migration number must be discovered from `src/indbase_core/migrations/` before adding a new migration.
- Existing `classification_suggestions` and `classification_feedback` remain compatibility surfaces unless the spec explicitly says to bridge a behavior.
- The first classifier should be deterministic and testable. Do not add a real provider, network dependency, embedding requirement, or hidden model call.
- The release bar favors precision over recall: abstain/review is acceptable; wrong confident assignment is not.

## Start Here

For schema:

- `src/indbase_core/migrations/`
- `src/indbase_core/db.py`
- `tests/test_db.py`
- `tests/test_vault.py`

For existing category behavior:

- `src/indbase_core/categories.py`
- `src/indbase_core/documents.py`
- `src/indbase_cli/main.py` catalog/doc commands
- `tests/test_cli.py`

For legacy classification behavior:

- `src/indbase_core/classification.py`
- `tests/test_classification.py`

For search metadata:

- `src/indbase_core/indexer.py`
- `src/indbase_core/search.py`
- `tests/test_search.py`

For ingest integration:

- `src/indbase_core/ingest.py`
- `tests/test_ingest_pipeline.py`

For health checks:

- `src/indbase_core/doctor.py`
- `tests/test_doctor.py`

Before coding:

```powershell
git status --short
rg -n "CREATE TABLE categories|classification_suggestions|classification_feedback|category_id" src tests
Get-ChildItem src/indbase_core/migrations
```

Do not overwrite user changes in a dirty worktree.

## Non-Negotiables

- Category IDs are language-neutral and stable.
- Chinese category support is localization, not a separate category set.
- New vaults use `indbase_default_v1`; legacy templates must not remain default options.
- Existing vault categories are not automatically deleted or rewritten.
- A document has exactly one Big Category or `uncategorized`.
- Automatic classification cannot create categories.
- Automatic classification can use only classification-ready categories.
- Confident assignment requires current-revision evidence, high confidence, and sufficient margin.
- Abstain is valid and must not be treated as failure.
- Taxonomy execution error is visible and distinct from abstain.
- Manual assignment and accepted suggestion must not be overwritten by automatic classification.
- Profile changes may mark old automatic assignments stale but must not rewrite document categories.
- Every automatic classification attempt writes run/result audit records.
- Existing broad `classification_*` tables are legacy compatibility, not the new core.
- Do not write source Markdown or mutate old revisions for category metadata.

## Implementation Steps

1. Add migration for category profiles, localizations, classification runs/results, category suggestions, and category feedback.
2. Update category initialization to use `indbase_default_v1` with stable IDs and locale labels.
3. Retire `minimal`, `academic`, and `full` from new-vault template selection while preserving existing vault data.
4. Add category profile service:
   - profile create/update/show
   - readiness validation
   - profile versioning or version markers
5. Add deterministic classifier:
   - reads active current revision chunks
   - scores classification-ready categories
   - applies threshold and margin
   - records evidence
6. Add run/result persistence and category suggestion/feedback persistence.
7. Integrate post-ingest taxonomy stage after trusted revision/chunk/search creation.
8. Extend CLI:
   - `catalog profile show/set`
   - `catalog ready/unready`
   - `catalog migrate-default`
   - `classify run/list/show/accept/reject/feedback`
9. Add category search/filter support distinct from full-text category mentions.
10. Add doctor checks for profiles, localizations, runs, results, suggestions, stale assignments, and category FTS metadata.
11. Add fixture suite and `scripts/v031_taxonomy_category_release_gate.py`.
12. Update project status/testing docs only after implementation and gates pass.

## Execution Slices

Slice 1: schema and default catalog

- Add migration and table helpers.
- Create `indbase_default_v1` defaults with stable IDs.
- Retire legacy template options for new vaults only.
- Preserve existing vault categories unless explicit migration command is used.
- Minimum checks:

```powershell
uv run python -m pytest tests/test_db.py tests/test_vault.py -q
```

Slice 2: profiles and catalog lifecycle

- Add category profile/localization service.
- Enforce readiness: positive and negative boundaries are required.
- Add manual-only, classification-ready, inactive, and archived lifecycle behavior.
- Protect used categories from destructive archive/migration.
- Minimum checks:

```powershell
uv run python -m pytest tests/test_cli.py tests/test_classification.py -q
```

Slice 3: deterministic classifier and audit records

- Score only current revision evidence and classification-ready profiles.
- Apply confidence and margin gates.
- Persist run/result/evidence/warnings for every attempt.
- Preserve manual and accepted assignments.
- Minimum checks:

```powershell
uv run python -m pytest tests/test_classification.py tests/test_ingest_pipeline.py -q
```

Slice 4: post-ingest, review, and feedback

- Run taxonomy after trusted revision/chunks/search.
- Treat abstain/review as normal outcomes.
- Treat taxonomy execution errors as visible issues.
- Persist suggestions, review items, and feedback.
- Minimum checks:

```powershell
uv run python -m pytest tests/test_ingest_pipeline.py tests/test_doctor.py -q
```

Slice 5: search/filter, doctor, and gate

- Add deterministic category search/filter.
- Add doctor findings for broken profiles/results/suggestions/metadata.
- Add fixture suite and `scripts/v031_taxonomy_category_release_gate.py`.
- Minimum checks:

```powershell
uv run python -m pytest tests/test_search.py tests/test_doctor.py -q
uv run python scripts/v031_taxonomy_category_release_gate.py
```

## Test Requirements

Add or update tests for:

- migration and schema constraints
- default catalog creation
- locale labels sharing category IDs
- legacy template rejection for new vaults
- explicit legacy migration plan behavior
- profile readiness validation
- manual-only vs classification-ready categories
- inactive-for-classification behavior
- used category archive protection
- post-ingest classification success, abstain, and error
- confidence and margin gates
- manual assignment preservation
- accepted suggestion preservation
- profile-change stale assignment behavior
- category feedback persistence
- fixture suite runner
- category search/filter
- doctor findings

Minimum validation before handoff:

```powershell
uv run python -m pytest tests/test_db.py tests/test_vault.py tests/test_classification.py tests/test_search.py -q
uv run python scripts/v031_taxonomy_category_release_gate.py
uv run python -m compileall -q src tests scripts
```

Before declaring complete:

```powershell
uv run python -m pytest -q
uv run python scripts/v02_deterministic_release_gate.py
uv run python scripts/doctor_negative_gate.py
```

Run real swallow/transition smoke only when the environment variables and dependencies are available.

## Done Means

- Category fixture gate has zero wrong confident assignments.
- Expected abstain/review cases pass.
- Manual and accepted category decisions are preserved across re-ingest.
- Every automatic assignment has evidence and run/result audit records.
- Category profile/localization corruption is doctor-visible.
- Category filter search returns only documents in the selected category.
- Tag governance remains untouched.
- Source revisions and old chunks remain immutable.
- Docs and AGENTS pointers are updated.

## Completion Report

Report:

- changed files
- migration name
- new/changed CLI commands
- category tables touched
- doctor findings added
- fixture/gate added
- tests run
- tests not run
- remaining risks
- confirmation that tag governance, retrieval, ask, real providers, hidden online learning, source mutation, and destructive migration are out of scope
