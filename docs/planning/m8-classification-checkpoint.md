# M8 Classification Suggestions Checkpoint

Status: complete

Date: 2026-05-13

Precondition: [M7 Embedding and Hybrid Search Completion Checkpoint](m7-completion-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](mvp-v0.1-spec.md)

## Decision

M8 is complete for deterministic local category/tag suggestions and classification feedback.

M8 does not introduce model-backed classification, automatic metadata overwrite, answer generation, translation, candidate cards, or `indb ask`.

## Behavior

Classification commands:

```powershell
indb classify suggest [doc_id]
indb classify list
indb classify show <suggestion_id>
indb classify accept <suggestion_id>
indb classify reject <suggestion_id>
```

Suggestion generation:

- scans active current documents only
- skips archived documents
- skips non-searchable source shells
- skips documents without current chunks
- uses deterministic local rules
- writes `classification_suggestions`
- creates `review_items.type = classification_suggestion`
- sets `documents.classification_status = suggested` when a suggestion is created
- does not change `documents.category_id`
- does not add or remove `document_tags`

Confidence and confirmation:

- suggestions below the confidence gate are not created
- created suggestions require user confirmation
- ordinary suggestion creation never applies metadata

Accept behavior:

- updates category only after explicit `classify accept`
- preserves a non-uncategorized manual category unless `--force-category` is used
- adds suggested tags without removing existing manual tags
- refreshes FTS metadata after accepted category/tag changes
- records `classification_feedback`
- resolves the linked classification review item

Reject behavior:

- does not change category or tags
- records `classification_feedback`
- resolves the linked classification review item

## Invariants

M8 preserves these rules:

- manual metadata is authoritative
- suggestions are reviewable records, not hidden side effects
- suggestion acceptance is explicit
- classification feedback is separate from suggestions
- classification does not mutate document revisions or source Markdown
- classification does not affect embedding staleness
- classification does not create generated answers or citations

## Verification

Latest verification commands:

```powershell
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python -m pytest
.venv\Scripts\python scripts/m71_embedding_index_gate.py
.venv\Scripts\python scripts/m72_hybrid_search_gate.py
.venv\Scripts\python scripts/m8_classification_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```

Latest result:

```text
compileall passed
150 passed
```

Latest M8 gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m8-classification-gate-20260513203343987029
M8_CLASSIFICATION_GATE=passed
```

M8 hard metrics:

```json
{
  "auto_metadata_mutations_before_accept": 0,
  "manual_category_overwrites": 0,
  "archived_document_suggestions": 0,
  "source_shell_suggestions": 0,
  "unresolved_classification_reviews_after_decisions": 0,
  "critical_doctor_findings": 0
}
```

M8 gate summary:

```json
{
  "accepted_category_applied": 1,
  "accepted_suggestions": 2,
  "accepted_tags_added": 4,
  "classification_feedback_for_accept": 1,
  "classification_fts_category_results": 3,
  "classification_fts_tag_results": 3,
  "feedback_records": 3,
  "pending_suggestions_created": 3,
  "rejected_suggestions": 1,
  "resolved_classification_reviews": 3,
  "review_items_created": 3
}
```

Latest v0.1 RC aggregate after M8:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\v01-release-candidate-gate-20260513203112625719
V01_RELEASE_CANDIDATE_GATE=passed
```

RC aggregate:

```json
{
  "critical_doctor_findings": 0,
  "fts_desync": 0,
  "index_integrity_errors": 0,
  "orphan_files": 0,
  "orphan_records": 0,
  "unexpected_errors": 0,
  "unsupported_documents_created": 0,
  "zero_chunk_current_revisions": 0
}
```

## Boundary

M8 enables local deterministic classification suggestions and explicit accept/reject feedback only.

Still not included:

- real classification provider
- automatic category/tag overwrite
- automatic tag suggestion during ingest
- answer generation
- `indb ask`
- translation
- candidate cards
