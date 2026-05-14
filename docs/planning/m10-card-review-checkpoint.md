# M10.2 Card Review Checkpoint

Status: complete

Date: 2026-05-14

Precondition: [M10.1 Candidate Extraction Checkpoint](m10-candidate-extraction-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](mvp-v0.1-spec.md)

## Decision

M10.2 Card Review is complete.

This checkpoint adds candidate card accept/reject actions. Accept writes a cited atomic note under `notes/atomic/`. Reject records a terminal rejected state and writes no atomic note.

## Behavior

M10.2 adds:

- `indb card accept <candidate_card_id>`
- `indb card reject <candidate_card_id>`
- accepted atomic note writing
- terminal state protection for accepted/rejected cards
- task records and task events for accept/reject actions

Accept requires:

- candidate card status is `reviewing`
- source document is active
- candidate card source revision is still the document current revision
- candidate card has source chunk bindings
- every claim has a source chunk and quote

Accept writes:

```text
notes/atomic/YYYY/MM/<candidate_card_id>.md
```

The accepted note frontmatter records:

- `candidate_card_id`
- `source_doc_ids`
- `source_revision_ids`
- `source_chunk_ids`
- `model`
- `prompt_version`

Reject:

- changes status to `rejected`
- preserves candidate source bindings
- writes no atomic note

Terminal states are not reusable:

- accepted cards cannot be accepted again
- accepted cards cannot be rejected
- rejected cards cannot be accepted

## Gate

The M10.2 gate is:

```powershell
.venv\Scripts\python scripts/m102_card_review_gate.py
```

It verifies:

- reject writes no atomic note
- reject preserves source bindings
- accept writes exactly one atomic note
- accepted cards record `accepted_note_path`
- accepted notes include source chunk IDs
- accepted claims include citations
- accepted/rejected cards cannot be reused

## Verification

Latest verification commands:

```powershell
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pytest tests\test_cards.py -q
.venv\Scripts\python scripts\m10_candidate_card_preflight_gate.py
.venv\Scripts\python scripts\m101_candidate_extraction_gate.py
.venv\Scripts\python scripts\m102_card_review_gate.py
.venv\Scripts\python scripts\v01_release_candidate_gate.py
```

Latest result:

```text
compileall passed
182 tests passed
M10_CANDIDATE_CARD_PREFLIGHT_GATE=passed
M101_CANDIDATE_EXTRACTION_GATE=passed
M102_CARD_REVIEW_GATE=passed
V01_RELEASE_CANDIDATE_GATE=passed
```

Latest M10.2 gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m102-card-review-gate-20260514100930362337
M102_CARD_REVIEW_GATE=passed
```

Hard metrics:

```json
{
  "accepted_cards_missing_note": 0,
  "accepted_cards_reaccepted": 0,
  "accepted_cards_rejected": 0,
  "accepted_notes_missing_source_chunks": 0,
  "accepted_notes_with_uncited_claims": 0,
  "rejected_cards_accepted": 0,
  "rejected_cards_written_to_atomic": 0
}
```

Positive proof metrics:

```json
{
  "accepted_cards_written": 1,
  "candidate_sources_preserved_after_reject": 1
}
```

## Boundary

This checkpoint does not include:

- card doctor negative tests
- stale-card cleanup or status migration beyond accept-time blocking
- model-backed extraction
- answer generation
- `indb ask`

M10.3 should harden candidate card lifecycle and doctor checks before broader generated-knowledge workflows.
