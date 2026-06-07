---
doc_type: phase_plan
phase_id: v0.3.2-tag-governance-foundation
title: Tag Governance Foundation
status: shipped
canonical: true
read_by_default: false
closeout: docs/testing/archive/v0.3.2-tag-governance-foundation-closeout.md
related_contracts:
  - docs/contracts/source-search-contract.md
related_adrs:
  - docs/adr/0002-governed-tag-promotion.md
---

# v0.3.2 Tag Governance Foundation

Status: active design

Date: 2026-06-02

Phase: v0.3.2 tag-governance-only foundation

Related docs:

- [MVP v0.1 Spec](../v0.1/mvp-v0.1-spec.plan.md)
- [v0.2 Swallow Ingest Integration](../v0.2/swallow-ingest-integration.plan.md)
- [v0.2 Transition Output Integration](../v0.2/transition-output-integration.plan.md)
- [v0.3.1 Taxonomy Category Foundation](../v0.3.1/taxonomy-category-foundation.plan.md)
- [v0.3.2 Tag Governance Foundation Agent Guide](../../../agents/archive/indbase/v0.3.2-tag-governance-foundation.md)
- [Taxonomy glossary](../../../../CONTEXT.md)
- [ADR 0002: Governed Tag Promotion](../../../adr/0002-governed-tag-promotion.md)

## Decision Summary

v0.3.2 makes tags trustworthy before retrieval packages, `ask`, or real model-backed tagging.

The phase owns **Tag Governance Foundation** only:

```text
Formal Tags -> Candidate Tags -> Tag Resolution -> Tag Admission Policy
-> Tag Volume Budget -> Tag Feedback / Harness -> Tag Search Filter
```

It does not implement retrieval packages, generated answers, real providers, embeddings-based taggers, ontology management, project namespaces, or TUI.

Primary correctness rule:

```text
Wrong automatic trusted tag attachment is a release blocker.
New Formal Tag creation must be explicit and auditable.
Candidate Tags are reviewable evidence, not trusted metadata.
```

## Existing Substrate

The implementation must inspect and preserve the current substrate before changing schema or behavior:

- `tags`, `tag_aliases`, and `document_tags` exist from the foundation schema.
- Current tag services live in `src/indbase_core/tags.py`.
- Current candidate compatibility logic lives in `src/indbase_core/tag_candidates.py`.
- Legacy classification tag suggestions live in `classification_suggestions.suggested_tags_json`.
- FTS metadata already carries tag text, but tag filters must use DB relations.
- Current migration numbering must be discovered from `src/indbase_core/migrations/`; do not assume a fixed next number without checking the tree.
- The repository also contains retrieval documents using a v0.3.2 label. This tag-governance plan is the current tag-system plan; do not rename, rewrite, or delete retrieval docs unless explicitly asked.

Do not create parallel tables that duplicate existing meaning without a migration/compatibility reason.

## Confirmed Decisions

- Formal Tags are governed metadata, not raw strings.
- Formal Tags are multi-select document metadata; Big Category remains single-select.
- Automatic tag workflows must not create new Formal Tags directly.
- Automatic workflows may attach existing active Canonical Tags only when evidence, confidence, lifecycle, scope, and budget checks pass.
- Automatic workflows must not delete, overwrite, or silently replace manual tags.
- Candidate Tags must pass Tag Resolution before persistence.
- New-Tag Proposals must pass Tag Admission Policy and Tag Volume Budget checks before review or promotion.
- Tag Resolution maps raw candidates to Canonical Tags, blocked/deprecated outcomes, alias suggestions, or new-tag proposals.
- Tag Admission Policy blocks low-value proposals: one-off, overlong, over-specific, path/date/version-like, vague, duplicate, or Big Category-equivalent tags.
- Tag Blocklist is user-reviewable governance data, not a hardcoded-only blacklist.
- Default Tag Volume Budget:
  - at most 5 auto-attached tags per document
  - at most 5 candidates per document
  - at most 20 new-tag proposals per run
  - at most 200 total candidates per run
  - unseeded new tags should normally appear in at least 2 documents before promotion
- Formal Tags are globally normalized by default.
- v1 supports optional category-bound Tag Scope; it does not implement project namespaces.
- Multilingual synonyms use tag aliases resolving to one Canonical Tag, not separate language-specific tag identities.
- Accepting a Candidate Tag applies only to the current document unless a separate Tag Propagation workflow is explicitly run.
- Merge/deprecate/archive changes future resolution and search interpretation, but do not rewrite historical `document_tags` unless explicit Tag Link Migration is run.
- Tag Feedback is explicit audit data, not hidden training.
- Tag Harness generates and evaluates fixture cases and policy suggestions; it must not mutate policy automatically.
- The first Tagger is deterministic and local; no real LLM, embedding provider, network dependency, API key, or model download.
- Post-Ingest Tagging Stage is disabled by default behind feature flags.
- TUI is out of scope. Commands must provide stable JSON for future consoler integration.
- v0.3.2 completion requires a local release gate and GitHub CI job.
- Legacy Classification Tag Suggestions remain compatible but are not the new core tag governance state.

## Data Model Plan

Add a migration after the current latest migration. If the current latest remains `0010`, the likely next migration is `0011_v032_tag_governance_foundation.sql`, but the implementation must verify before writing.

### Formal Tags

Reuse `tags` as the Formal Tag identity table. Add only missing lifecycle/governance fields:

```text
status: active | deprecated | merged | archived
type
scope: global | category_bound
scope_category_id
merged_into_tag_id
created_by
policy_warning_json
updated_at
deleted_at
```

Rules:

- `normalized_name` remains unique for active canonical identity.
- Manual Tag Creation may proceed after warnings.
- Automatic workflows may use only active Canonical Tags.
- Deprecated, merged, and archived tags are ineligible for automatic attachment.
- A merged tag resolves to its target Canonical Tag.
- Merge cycles are invalid and doctor-visible.

### Tag Aliases

Reuse `tag_aliases`. Add only missing governance fields:

```text
language
locale
source
status
created_by
deleted_at
```

Rules:

- Alias resolution must point to a Canonical Tag.
- Multilingual aliases resolve to the same Canonical Tag.
- Duplicate normalized aliases are hard doctor findings.
- Alias add/remove writes a Tag Governance Event.

### Document Tags

Reuse `document_tags`. Add only missing evidence/governance fields:

```text
revision_id
source: manual | accepted_candidate | auto | legacy_classification
confidence
evidence_chunk_ids_json
candidate_id
status
created_by
updated_at
deleted_at
```

Rules:

- Manual tags are preserved across re-ingest.
- Automatic workflows must not remove manual tags.
- Auto-attached tags require current-revision evidence.
- Search filters must resolve through document-tag relationships and Canonical Tags.

### Tagger Runs

Add `tagger_runs` to record each deterministic tagging workflow:

```sql
CREATE TABLE tagger_runs (
  tagger_run_id TEXT PRIMARY KEY,
  trigger TEXT NOT NULL,
  tagger_version TEXT NOT NULL,
  harness_version TEXT,
  policy_version TEXT NOT NULL,
  auto_attach_threshold REAL NOT NULL,
  per_doc_auto_attach_limit INTEGER NOT NULL,
  per_doc_candidate_limit INTEGER NOT NULL,
  per_run_new_tag_proposal_limit INTEGER NOT NULL,
  per_run_total_candidate_limit INTEGER NOT NULL,
  scanned_documents INTEGER NOT NULL,
  auto_attached_count INTEGER NOT NULL,
  candidate_count INTEGER NOT NULL,
  new_tag_proposal_count INTEGER NOT NULL,
  blocked_candidate_count INTEGER NOT NULL,
  preserved_manual_count INTEGER NOT NULL,
  error_count INTEGER NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
```

Valid triggers:

```text
manual
post_ingest
feedback_eval
fixture_gate
propagation_review
```

### Tagger Results

Add `tagger_results` to record per-document outcomes:

```sql
CREATE TABLE tagger_results (
  tagger_result_id TEXT PRIMARY KEY,
  tagger_run_id TEXT NOT NULL,
  doc_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  outcome TEXT NOT NULL,
  auto_attached_tag_ids_json TEXT NOT NULL,
  candidate_ids_json TEXT NOT NULL,
  blocked_candidates_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  warnings_json TEXT NOT NULL,
  error_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(tagger_run_id) REFERENCES tagger_runs(tagger_run_id),
  FOREIGN KEY(doc_id) REFERENCES documents(doc_id),
  FOREIGN KEY(revision_id) REFERENCES document_revisions(revision_id)
);
```

Valid outcomes:

```text
auto_attached
candidates_created
no_candidates
preserved_manual
completed_with_warnings
error
```

### Tag Candidates

Reuse or extend existing `tag_candidates` for v0.3.2. It must be able to represent:

```text
candidate_type: attach_existing | propose_new | alias_suggestion | merge_suggestion | remove_suggestion
raw_name
normalized_name
target_tag_id
proposed_name
doc_id
revision_id
tagger_run_id
tagger_result_id
resolution_status
admission_status
budget_status
confidence
evidence_json
policy_decision_json
budget_decision_json
status: pending | accepted | rejected | superseded | blocked | stale
review_item_id
created_at
updated_at
```

Rules:

- Raw strings are never persisted as trusted metadata without resolution fields.
- Attach-Existing Candidates point to a Canonical Tag.
- New-Tag Proposals require admission and budget approval.
- Candidate Tags are not searchable through trusted tag filters until accepted/promoted.
- Accept/reject writes Tag Feedback and Tag Governance Events where applicable.

### Tag Feedback

Add `tag_feedback`:

```sql
CREATE TABLE tag_feedback (
  tag_feedback_id TEXT PRIMARY KEY,
  doc_id TEXT,
  revision_id TEXT,
  tag_id TEXT,
  candidate_id TEXT,
  action TEXT NOT NULL,
  reason TEXT,
  old_json TEXT,
  new_json TEXT,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

Valid actions:

```text
accepted
rejected
manual_created
manual_removed
merged
deprecated
alias_added
alias_removed
blocked
unblocked
policy_suggestion_created
fixture_candidate_created
```

### Tag Governance Events

Use or extend the existing tag lifecycle/audit event table if present; otherwise add `tag_governance_events`.

Events must cover:

```text
created
promoted
alias_added
alias_removed
merged
deprecated
archived
restored
scope_changed
blocked
unblocked
policy_suggestion_created
link_migration_started
link_migration_finished
```

Rules:

- No governance mutation is silent.
- Events explain changes to resolution, search, alias, merge, scope, and lifecycle behavior.
- Doctor must flag missing/invalid audit links for governance operations.

### Tag Blocklist

Add `tag_blocklist`:

```sql
CREATE TABLE tag_blocklist (
  blocked_id TEXT PRIMARY KEY,
  pattern TEXT NOT NULL,
  normalized_pattern TEXT NOT NULL,
  match_type TEXT NOT NULL,
  reason TEXT,
  source TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  deleted_at TEXT
);
```

Valid match types:

```text
exact
contains
```

Avoid unrestricted regex in v1.

### Policy Suggestions

Add `tag_policy_suggestions` only if policy suggestions cannot be cleanly represented by `tag_candidates` plus `tag_feedback`.

Suggestion types:

```text
alias
merge
deprecate
block
scope_change
admission_rule
fixture_case
```

Policy suggestions are reviewable and must not mutate policy automatically.

## Tagger Behavior

Inputs:

- current active document
- current revision chunks
- title and headings
- existing active Canonical Tags
- tag aliases
- tag blocklist
- tag admission policy
- Big Category as weak context for category-bound scope
- prior explicit Tag Feedback

Forbidden trusted evidence:

- old revisions
- archived documents
- rejected candidates
- generated summaries
- model-only topic inference
- source path or filename as sole strong evidence

Decision flow:

```text
extract raw candidates deterministically
for each raw candidate:
  normalize
  run Tag Resolution
  run Tag Admission Policy
  run Tag Volume Budget
  if existing active Canonical Tag and confidence/evidence pass:
    auto-attach within per-doc budget
  elif attach-existing candidate is useful:
    create Attach-Existing Candidate
  elif new tag proposal passes admission and budget:
    create New-Tag Proposal
  else:
    record blocked/rejected decision in run/result metrics
write tagger run/result/candidate/feedback/audit records
refresh FTS metadata only for trusted document_tags changes
```

Auto-attach threshold:

```text
auto_attach_threshold = 0.85
```

Budget defaults:

```text
per_doc_auto_attach_limit = 5
per_doc_candidate_limit = 5
per_run_new_tag_proposal_limit = 20
per_run_total_candidate_limit = 200
min_docs_for_new_tag_without_manual_seed = 2
formal_tags_soft_limit = 500
```

## Manual Tag Creation

Manual tag creation is user-authoritative:

- `indb tag add` may create a Formal Tag after policy warnings.
- Warnings should be returned in human output and `--json`.
- Manual creation writes feedback/audit events.
- Manual tags are eligible for later auto-attach only when active, canonical, scoped correctly, and evidence-backed.
- Doctor/harness may warn about low-quality manual tags but must not delete or rewrite them.

## Candidate Acceptance

Accepting a candidate:

- applies only to the current document by default;
- may attach an existing Canonical Tag;
- may promote a New-Tag Proposal into a Formal Tag and attach it to the current document;
- writes Tag Feedback;
- writes Tag Governance Events;
- refreshes FTS metadata only after trusted document-tags change.

It must not:

- propagate to other documents implicitly;
- delete manual tags;
- rewrite historical revisions or Markdown frontmatter;
- bypass admission and budget checks for new Formal Tags.

## Tag Propagation

v0.3.2 may create propagation proposals, but automatic propagation apply is out of scope.

Allowed:

```text
tag run --from-feedback
tag propagation proposal records
candidate creation for similar documents
```

Out of scope:

```text
hidden batch tagging
automatic apply across the vault
TUI review of propagation batches
```

## Post-Ingest Integration

Default configuration:

```toml
[features]
tag_governance = false
post_ingest_tagging = false
```

Rules:

- Run only after trusted current revision, chunks, and source search metadata exist.
- Do not run on source shells or untrusted candidates.
- Abstain/no candidates is not an ingest failure.
- Tagger execution error is visible through tasks/errors/reviews and may make ingest `completed_with_issues`.
- Post-ingest tagging may auto-attach only safe existing tags and create only budgeted candidates.
- Post-ingest tagging must not create new Formal Tags.
- Post-ingest tagging must not affect Big Category classification outcome.

## CLI Scope

Use existing `indb tag` commands where possible and extend them. All run/review/search commands below must support stable `--json` output for future consoler integration.

Formal Tag management:

```text
indb tag list [--include-inactive] [--status active|deprecated|merged|archived] [--json]
indb tag add <name> --type <type> [--description ...] [--scope global|category] [--category <id>] [--json]
indb tag update <tag_id> [--name ...] [--description ...] [--json]
indb tag alias add <tag_id> <alias> [--language ...] [--json]
indb tag alias remove <alias> [--json]
indb tag merge <source_tag_id> <target_tag_id> [--reason ...] [--json]
indb tag deprecate <tag_id> [--reason ...] [--json]
indb tag archive <tag_id> [--json]
indb tag restore <tag_id> [--json]
```

Tagger and candidate review:

```text
indb tag run [--doc-id <doc_id>] [--from-feedback] [--json]
indb tag candidates list [--status pending|accepted|rejected|blocked|stale] [--json]
indb tag candidates show <candidate_id> [--json]
indb tag candidates accept <candidate_id> [--json]
indb tag candidates reject <candidate_id> [--reason ...] [--json]
indb tag feedback <doc_id> --tag <tag> --action accept|reject|remove|merge [--reason ...] [--json]
```

Policy and blocklist:

```text
indb tag policy show [--json]
indb tag block list [--json]
indb tag block add <name-or-pattern> [--match exact|contains] [--reason ...] [--json]
indb tag block remove <blocked_id> [--json]
```

Search:

```text
indb search <query> --tag <tag-id-or-name-or-alias> [--json]
indb search "tag:<tag-id-or-name-or-alias> <query>" [--json]
```

Explicitly out of scope:

```text
TUI
project namespace
automatic propagation apply
complex regex blocklist
real provider configuration
```

## JSON Contract

Every tag governance run/review/search command should return stable JSON containing relevant fields:

```text
ids
status
counts
candidate_type
raw_candidate
normalized_candidate
target_tag_id
resolved_tag_id
evidence
warnings
budget_decision
policy_decision
review_item_id
feedback_id
audit_event_id
```

Rules:

- Human CLI output may be concise.
- JSON output is the future consoler-facing command contract.
- Business rules live in core services, not CLI rendering.

## Tag Search Filter

Tag filter must be relation-backed:

```text
document_tags -> tags -> canonical resolution
```

Rules:

- `indb search --tag <tag>` returns chunks from active current documents attached to the resolved Formal Tag.
- `tag:<tag> query` must be parsed as a tag filter plus text query.
- Alias and merged tag references resolve to the Canonical Tag.
- Candidate Tags are not filterable as trusted tags.
- Rejected candidates must not affect tag filter results.
- FTS `chunks_fts.tags` is useful for broad full-text search but is not the authoritative tag filter.
- Deprecated tag queries may resolve historical bindings, but deprecated tags remain ineligible for auto-attach.
- Archived tags are excluded by default unless a future explicit include option is added.

## Doctor Extensions

Hard findings:

```text
tag_alias_points_missing_tag
tag_alias_duplicate_normalized
tag_merged_target_missing
tag_merge_cycle
document_tag_points_missing_tag
document_tag_points_missing_doc
tag_candidate_missing_run
tag_candidate_invalid_json
tag_candidate_without_resolution
auto_attached_tag_without_evidence
auto_attached_deprecated_or_archived
tag_filter_metadata_stale
tag_governance_event_missing
tag_blocklist_invalid_pattern
```

Warnings:

```text
formal_tag_soft_limit_exceeded
new_tag_proposal_budget_exceeded
tag_without_description_high_usage
duplicate_like_tags
blocked_candidate_repeated
manual_tag_policy_warning
```

Doctor must not:

- merge tags
- delete tags
- promote candidates
- rewrite document-tag links
- repair blocklist or policy automatically

## Fixture And Gate Plan

Add:

```text
tests/fixtures/v032_tag_governance/
  cases.jsonl
  docs/
```

Each fixture case should include:

```json
{
  "case_id": "tagcase_attach_existing_rag",
  "title": "RAG retrieval notes",
  "body": "Evidence text...",
  "formal_tags_seed": ["rag"],
  "aliases_seed": [{"tag": "rag", "alias": "retrieval augmented generation"}],
  "expected_auto_attached": ["rag"],
  "expected_candidates": [],
  "forbidden_auto_attached": ["misc"],
  "expected_blocked": [],
  "notes": "Why this fixture exists."
}
```

Minimum fixture coverage:

- existing Formal Tag auto-attach positive cases
- ambiguous cases expected to create candidate or no candidate
- low-information documents expected to produce no candidate
- new-tag proposal budget cases
- over-specific/path/date/version-like candidates blocked
- Big Category-equivalent candidates blocked
- alias and multilingual alias resolution
- merged/deprecated/archived tags not auto-attached
- manual tag preservation
- candidate accept/reject roundtrip
- tag filter exactness
- candidate tags not filterable
- feedback-derived fixture/policy suggestion generation
- doctor corruption cases

Suggested gate:

```text
scripts/v032_tag_governance_release_gate.py
```

Gate metrics:

```json
{
  "wrong_auto_attached_tags": 0,
  "manual_tags_preserved": true,
  "candidate_count_within_budget": true,
  "new_tag_sprawl_blocked": true,
  "raw_candidates_resolved_before_persist": true,
  "deprecated_merged_archived_not_auto_attached": true,
  "tag_filter_exact": true,
  "candidate_tags_not_search_filterable": true,
  "feedback_roundtrip": true,
  "policy_suggestions_reviewable": true,
  "doctor_hard_findings": 0
}
```

## CI Plan

Add a GitHub Actions job after the script exists and passes locally:

```text
v0.3.2 - tag governance gate
```

Command:

```powershell
uv run python scripts/v032_tag_governance_release_gate.py
```

Also update `docs/testing.md` only after the script exists and the command has passed locally.

## Test Plan

Focused tests:

- migration creates/extends tag governance tables without dropping legacy data
- Formal Tag lifecycle: active, deprecated, merged, archived, restored
- manual tag creation warnings do not block user creation
- Tag Scope: global vs category-bound eligibility
- multilingual alias resolution
- alias duplicate rejection
- merge target resolution
- merge cycle doctor finding
- Tag Blocklist exact/contains matching
- Tag Admission Policy blocks low-value proposals
- Tag Volume Budget limits per-doc and per-run output
- deterministic tagger auto-attaches safe existing tags
- deterministic tagger creates candidates instead of new Formal Tags
- deprecated/merged/archived tags are not auto-attached
- manual tags are preserved across tagger run and re-ingest
- Candidate Tag accept/reject writes feedback and audit
- accepting a New-Tag Proposal creates Formal Tag only through promotion
- accepting a candidate affects only the current document
- rejected/candidate tags do not affect trusted tag filter
- `indb search --tag` and `tag:<tag> query` are relation-backed
- FTS tag metadata refreshes after trusted tag changes
- post-ingest tagging is disabled by default
- post-ingest tagging feature flag creates only bounded safe changes
- JSON output contains stable IDs/status/counts/evidence/policy fields
- doctor catches broken aliases, candidates, auto-attach evidence, merge cycles, and stale tag metadata

Regression tests:

- existing manual tag CRUD tests
- existing classification compatibility tests
- existing search tests
- v0.3.1 taxonomy category gate
- v0.2 deterministic and doctor negative gates

Suggested validation during implementation:

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

Run real swallow/transition smoke only when dependencies and env vars are available.

## Implementation Order

1. Add this planning doc, agent guide, AGENTS pointers, project-status pointer, and planning index.
2. Inspect current tag schema/services/commands and decide which existing fields/tables can be reused.
3. Add migration for missing tag governance fields/tables.
4. Add Tag Resolution service.
5. Add Tag Admission Policy and Tag Volume Budget service.
6. Add Tag Blocklist service.
7. Add deterministic Tagger service with run/result persistence.
8. Add Candidate Tag accept/reject/promotion workflow with feedback and governance events.
9. Add alias/merge/deprecate/scope governance operations with audit.
10. Add relation-backed Tag Search Filter.
11. Add optional Post-Ingest Tagging Stage behind feature flags.
12. Add JSON output to tag run/review/policy/search commands.
13. Add doctor findings.
14. Add fixture suite and release gate.
15. Add CI job after the gate passes locally.
16. Update `docs/testing.md` and `docs/project-status.md` only after implementation and gates pass.

## Out Of Scope

- TUI.
- consoler implementation.
- Retrieval packages.
- `indb ask`.
- Real LLM providers.
- Embedding-backed tagger.
- Hidden online learning.
- Ontology or graph semantics.
- Project namespaces.
- Automatic batch propagation apply.
- Complex regex blocklist.
- Automatic destructive tag cleanup.
- Source Markdown/frontmatter mutation for tag metadata.
