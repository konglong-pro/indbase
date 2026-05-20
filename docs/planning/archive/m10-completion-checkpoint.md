# M10 Candidate Cards Completion Checkpoint

Status: complete

Date: 2026-05-14

Preconditions:

- [M10 Readiness Checkpoint](m10-readiness-checkpoint.md)
- [M10.1 Candidate Extraction Checkpoint](m10-candidate-extraction-checkpoint.md)
- [M10.2 Card Review Checkpoint](m10-card-review-checkpoint.md)
- [M10.3 Candidate Card Hardening Checkpoint](m10-card-hardening-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

M10 Candidate Cards is complete.

M10 remains a source-bound generated-artifact workflow. It does not introduce answer generation, `indb ask`, model-backed extraction, card merging, or knowledge graph behavior.

## Delivered

M10 readiness proved:

- active current documents and chunks are available for candidate extraction
- source shells are not searchable
- archived documents and old revisions do not appear in default search
- translation/generated artifact source binding is diagnosable before card work

M10.1 delivered:

- `candidate_cards`
- `candidate_card_sources`
- deterministic local candidate extraction
- `indb card generate <doc_id>`
- `indb card list`
- `indb card show <candidate_card_id>`
- claim-level source chunk and quote binding
- rejection of source shells, archived documents, old revisions, and no-chunk revisions

M10.2 delivered:

- `indb card accept <candidate_card_id>`
- `indb card reject <candidate_card_id>`
- accepted atomic note writing under `notes/atomic/YYYY/MM/`
- cited accepted notes with `candidate_card_id`, source docs, revisions, and chunks
- reject without atomic note writing
- terminal accepted/rejected state protection

M10.3 delivered:

- doctor checks for candidate card records
- doctor checks for candidate card source bindings
- doctor checks for accepted atomic note drift
- doctor checks for uncited accepted claims
- doctor checks for orphan candidate atomic notes

## Frozen M10 Invariants

Candidate extraction scope:

```sql
documents.status = 'active'
documents.current_revision_id = candidate_cards.source_revision_id
chunks.revision_id = documents.current_revision_id
chunks.is_current = 1
chunks.deleted_at IS NULL
```

Candidate card rules:

- source shells cannot generate cards
- archived documents cannot generate cards
- old revisions cannot generate cards
- current revisions with no chunks cannot generate cards
- every claim must have a `claim_id`
- every claim must have non-empty `source_chunk_ids`
- every claim must have a non-empty quote
- default card list hides old-revision cards
- stale cards remain explicitly viewable for provenance

Card review rules:

- accept requires a reviewing card on an active current revision
- accept writes exactly one atomic note
- accepted notes must cite source chunks
- reject writes no atomic note
- accepted and rejected cards are terminal by default

Doctor rules:

- candidate card corruption is an error
- missing accepted notes are generated-artifact errors
- orphan atomic notes are errors
- candidate card findings do not mutate source documents, revisions, chunks, or FTS

## Gate

The aggregate M10 gate is:

```powershell
.venv\Scripts\python scripts/m10_candidate_cards_gate.py
```

It runs the M10 preflight, M10.1 extraction, M10.2 review, and M10.3 hardening gates and verifies the combined hard metrics.

## Verification

Latest verification commands:

```powershell
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts\m10_candidate_cards_gate.py
.venv\Scripts\python scripts\v01_release_candidate_gate.py
```

Latest result:

```text
compileall passed
187 tests passed
M10_CANDIDATE_CARDS_GATE=passed
V01_RELEASE_CANDIDATE_GATE=passed
```

Latest aggregate gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m10-candidate-cards-gate-20260514105748361984
M10_CANDIDATE_CARDS_GATE=passed
```

Key hard metrics:

```json
{
  "extraction_archived_doc_cards": 0,
  "extraction_atomic_notes_written_before_accept": 0,
  "extraction_claims_with_missing_chunks": 0,
  "extraction_claims_without_quotes": 0,
  "extraction_claims_without_source_chunks": 0,
  "extraction_no_chunk_cards": 0,
  "extraction_old_revision_candidates_default_listed": 0,
  "extraction_old_revision_cards_default": 0,
  "extraction_source_shell_cards": 0,
  "review_accepted_cards_missing_note": 0,
  "review_accepted_cards_reaccepted": 0,
  "review_accepted_cards_rejected": 0,
  "review_accepted_notes_missing_source_chunks": 0,
  "review_accepted_notes_with_uncited_claims": 0,
  "review_rejected_cards_accepted": 0,
  "review_rejected_cards_written_to_atomic": 0,
  "hardening_accepted_card_without_sources_undetected": 0,
  "hardening_missing_accepted_note_undetected": 0,
  "hardening_orphan_atomic_notes_undetected": 0,
  "hardening_orphan_candidate_cards_undetected": 0,
  "hardening_uncited_accepted_claims_undetected": 0
}
```

Positive proof metrics:

```json
{
  "m10_candidate_cards_complete": 1,
  "preflight_candidate_preflight_passed": 1,
  "extraction_candidate_cards_created": 1,
  "extraction_candidate_sources_created": 3,
  "review_accepted_cards_written": 1,
  "review_candidate_sources_preserved_after_reject": 1,
  "hardening_orphan_candidate_cards_detected": 5,
  "hardening_missing_accepted_note_detected": 1,
  "hardening_accepted_card_without_sources_detected": 1,
  "hardening_uncited_accepted_claims_detected": 1,
  "hardening_orphan_atomic_notes_detected": 1
}
```

## Boundary

M10 completion does not include:

- real model-backed extraction
- answer generation
- `indb ask`
- card deduplication
- card merge/split workflows
- knowledge graph
- candidate card UI beyond CLI commands

Before the next milestone starts, rerun:

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python scripts/m10_candidate_cards_gate.py
.venv\Scripts\python scripts/v01_release_candidate_gate.py
```
