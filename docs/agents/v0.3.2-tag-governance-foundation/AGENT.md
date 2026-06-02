# v0.3.2 Tag Governance Foundation Agent Guide

Read this before implementing v0.3.2 tag governance work.

Canonical spec:

- `docs/planning/v0.3.2-tag-governance-foundation.md`

Required context:

- `AGENTS.md`
- `CONTEXT.md`
- `docs/adr/0002-governed-tag-promotion.md`
- `docs/project-status.md`
- `docs/testing.md`
- `docs/planning/v0.3.1-taxonomy-category-foundation.md`
- `docs/agents/v0.3.1-taxonomy-category-foundation/AGENT.md`
- `docs/planning/v0.2-swallow-ingest-integration.md`
- `docs/planning/v0.2-transition-output-integration.md`

## Objective

Make tags trustworthy enough for precise tag search and later retrieval/ask workflows.

Success means:

```text
Existing safe Formal Tags can be auto-attached with evidence.
New Formal Tags are never created automatically.
Candidate Tags are resolved, budgeted, reviewable, and auditable.
Manual tags are preserved.
Tag filter search is exact and relation-backed.
The tag harness blocks sprawl cases and records feedback.
```

## Scope

Implement only Tag Governance Foundation:

- Formal Tag lifecycle
- Canonical Tag and alias resolution
- Candidate Tag resolution and review
- New-Tag Proposal admission policy
- Tag Volume Budget
- Tag Blocklist
- deterministic local Tagger
- Tag Feedback
- Tag Governance Events
- relation-backed Tag Search Filter
- optional feature-flagged Post-Ingest Tagging Stage
- doctor checks
- fixture suite and release gate
- CI job after local gate passes
- stable `--json` output for future consoler integration

Do not implement:

- TUI
- consoler UI
- retrieval packages
- `ask`
- real LLM providers
- embedding-backed tagger
- hidden online learning
- ontology or graph semantics
- project namespaces
- automatic batch propagation apply
- destructive tag cleanup
- source Markdown/frontmatter mutation for tag metadata
- retrieval planning docs or retrieval behavior unless the task explicitly asks for them

## Start Here

Before coding:

```powershell
git status --short
Get-ChildItem src/indbase_core/migrations | Sort-Object Name
rg -n "CREATE TABLE.*tags|tag_candidates|tag_lifecycle_events|classification_suggestions|document_tags" src tests
```

For schema:

- `src/indbase_core/migrations/`
- `src/indbase_core/db.py`
- `tests/test_db.py`
- `tests/test_vault.py`

For current manual tag behavior:

- `src/indbase_core/tags.py`
- `src/indbase_cli/main.py` tag/doc tag commands
- `tests/test_cli.py`

For existing candidate compatibility:

- `src/indbase_core/tag_candidates.py`
- `src/indbase_core/classification.py`
- `tests/test_classification.py`
- `tests/test_taxonomy_slice3.py` when present

For search/filter behavior:

- `src/indbase_core/search.py`
- `src/indbase_core/retrieval.py` if tag filters already exist there
- `src/indbase_core/indexer.py`
- `tests/test_search.py`

For ingest integration:

- `src/indbase_core/ingest.py`
- `src/indbase_core/config.py`
- `tests/test_ingest_pipeline.py`

For doctor:

- `src/indbase_core/doctor.py`
- `tests/test_doctor.py`

For CI/gates:

- `scripts/gate_common.py`
- `scripts/v031_taxonomy_category_release_gate.py`
- `.github/workflows/ci.yml`
- `docs/testing.md`

## Non-Negotiables

- Formal Tags are governed metadata, not raw strings.
- Automatic workflows must not create new Formal Tags directly.
- Automatic workflows may auto-attach only existing active Canonical Tags.
- Auto-attach requires current-revision evidence, confidence, lifecycle, scope, and budget checks.
- Candidate Tags must pass Tag Resolution before persistence.
- New-Tag Proposals must pass Tag Admission Policy and Tag Volume Budget.
- Manual tags must not be deleted, overwritten, or silently replaced by automatic workflows.
- Deprecated, merged, and archived tags are ineligible for auto-attach.
- Accepting a Candidate Tag applies only to the current document unless an explicit propagation workflow is run.
- Merge/deprecate/archive must not rewrite existing `document_tags` unless explicit Tag Link Migration is run.
- Tag Feedback and governance events are explicit audit data, not hidden learning.
- The first Tagger is deterministic and local; do not add real provider dependencies.
- Post-Ingest Tagging is disabled by default and feature-flagged.
- Tag filters must be relation-backed, not raw FTS tag-string matching.
- TUI is out of scope; `--json` output is the future consoler-facing contract.

## Execution Slices

Slice 1: schema and compatibility

- Inspect current tag tables and migration history.
- Add only missing fields/tables for v0.3.2.
- Preserve legacy classification tag suggestions.
- Preserve current manual tag behavior.
- Minimum checks:

```powershell
uv run python -m pytest tests/test_db.py tests/test_vault.py tests/test_cli.py -q
```

Slice 2: tag resolution and governance policy

- Add Tag Resolution for canonical tags, aliases, merged tags, deprecated/archived outcomes, and blocked outcomes.
- Add Tag Admission Policy.
- Add Tag Volume Budget.
- Add Tag Blocklist service.
- Minimum checks:

```powershell
uv run python -m pytest tests/test_v032_tag_governance.py tests/test_cli.py -q
```

Slice 3: deterministic tagger and candidates

- Add deterministic local Tagger.
- Persist tagger runs/results.
- Create Attach-Existing Candidates and New-Tag Proposals.
- Auto-attach only safe existing Canonical Tags.
- Preserve manual tags.
- Minimum checks:

```powershell
uv run python -m pytest tests/test_v032_tag_governance.py tests/test_classification.py -q
```

Slice 4: feedback, promotion, and audit

- Add candidate accept/reject.
- Add New-Tag Proposal promotion.
- Add Tag Feedback.
- Add Tag Governance Events.
- Add alias/merge/deprecate/archive/scope audit.
- Ensure candidate acceptance affects only the current document.
- Minimum checks:

```powershell
uv run python -m pytest tests/test_v032_tag_governance.py tests/test_cli.py -q
```

Slice 5: tag search filter and FTS metadata

- Add relation-backed `--tag` and `tag:<tag>` search.
- Resolve aliases and merged tags.
- Ensure candidates/rejected tags are not trusted filters.
- Refresh FTS metadata only for trusted document-tag changes.
- Minimum checks:

```powershell
uv run python -m pytest tests/test_search.py tests/test_v032_tag_governance.py -q
```

Slice 6: post-ingest, doctor, gate, CI

- Add feature flags: `tag_governance`, `post_ingest_tagging`.
- Wire optional post-ingest tagging after trusted revision/chunks/search.
- Add doctor hard findings and warnings.
- Add fixture suite and `scripts/v032_tag_governance_release_gate.py`.
- Add CI job after local gate passes.
- Minimum checks:

```powershell
uv run python -m pytest tests/test_ingest_pipeline.py tests/test_doctor.py tests/test_v032_tag_governance.py -q
uv run python scripts/v032_tag_governance_release_gate.py
```

## CLI Requirements

Extend `indb tag` and `indb search` only. Do not add TUI.

All run/review/search commands must support stable `--json`:

```text
indb tag run --json
indb tag candidates list --json
indb tag candidates show <candidate_id> --json
indb tag candidates accept <candidate_id> --json
indb tag candidates reject <candidate_id> --json
indb tag policy show --json
indb tag block list --json
indb tag feedback ... --json
indb search --tag <tag> --json
```

JSON should include IDs, status, counts, candidate type, raw/normalized candidate, resolved target tag, evidence, warnings, policy decision, budget decision, review item, feedback ID, and audit event ID where relevant.

## Test Requirements

Add or update tests for:

- migration and legacy data preservation
- Formal Tag lifecycle
- alias resolution, multilingual aliases, and alias duplicates
- merge/deprecate/archive behavior
- merge cycle doctor finding
- scope eligibility
- manual tag creation warnings
- blocklist exact/contains behavior
- admission policy sprawl blocking
- volume budget enforcement
- deterministic auto-attach of existing tags
- new-tag proposals never becoming Formal Tags automatically
- candidate accept/reject feedback/audit
- manual tag preservation
- candidate tags not trusted-search filterable
- relation-backed tag filter
- FTS metadata refresh after trusted tag changes
- feature-flagged post-ingest tagging
- stable `--json` output
- doctor hard findings and warnings
- fixture gate metrics

Minimum validation before handoff:

```powershell
uv run python -m pytest tests/test_cli.py tests/test_search.py tests/test_classification.py -q
uv run python -m pytest tests/test_v032_tag_governance.py -q
uv run python scripts/v032_tag_governance_release_gate.py
uv run python -m compileall -q src tests scripts
```

Before declaring complete:

```powershell
uv run python -m pytest -q
uv run python scripts/v02_deterministic_release_gate.py
uv run python scripts/doctor_negative_gate.py
uv run python scripts/v031_taxonomy_category_release_gate.py
uv run python scripts/v032_tag_governance_release_gate.py
uv run python -m compileall -q src tests scripts
```

Run real swallow/transition smoke only when env vars and dependencies are available.

## Done Means

- `wrong_auto_attached_tags = 0`.
- Manual tags are preserved.
- Candidate counts stay within budget.
- New tag sprawl cases are blocked.
- Raw candidates are resolved before persistence.
- Deprecated, merged, and archived tags are not auto-attached.
- Tag filter search is exact and relation-backed.
- Candidate/rejected tags are not trusted-filterable.
- Feedback roundtrip writes audit data.
- Policy suggestions are reviewable and do not mutate policy automatically.
- Doctor hard findings are zero on the healthy gate vault.
- v0.3.2 gate is in CI after passing locally.
- Docs and AGENTS pointers are updated.

## Completion Report

Report:

- changed files
- migration name
- new/changed CLI commands
- tag tables touched
- feature flags added
- doctor findings added
- fixture/gate added
- CI job added
- tests run
- tests not run
- remaining risks
- confirmation that TUI, consoler UI, retrieval packages, `ask`, real providers, hidden learning, automatic batch propagation, source mutation, and destructive cleanup are out of scope
