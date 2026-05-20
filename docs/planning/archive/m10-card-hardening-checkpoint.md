# M10.3 Candidate Card Hardening Checkpoint

Status: complete

Date: 2026-05-14

Precondition: [M10.2 Card Review Checkpoint](m10-card-review-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

M10.3 Candidate Card Hardening is complete.

This checkpoint adds doctor coverage for candidate card records, source bindings, claim citations, and accepted atomic note files. It does not add new generation behavior.

## Behavior

Doctor now detects:

- candidate cards referencing missing documents
- candidate cards referencing missing revisions
- candidate card revision/document mismatches
- invalid or empty `claims_json`
- claims without source chunks
- claims referencing missing source chunks
- claims missing citation quotes
- candidate card source rows referencing missing cards, documents, revisions, or chunks
- accepted cards without source rows
- accepted cards without `accepted_note_path`
- missing accepted atomic note files
- accepted notes outside `notes/atomic/`
- accepted notes with frontmatter/card ID mismatches
- accepted notes missing source chunk frontmatter
- accepted notes with uncited claims
- candidate atomic notes not referenced by `candidate_cards`

These findings are generated-artifact integrity errors. They do not mutate source documents, revisions, chunks, FTS, or accepted notes.

## Gate

The M10.3 gate is:

```powershell
.venv\Scripts\python scripts/m103_card_hardening_gate.py
```

It verifies:

- orphan candidate card records are detected
- missing accepted notes are detected
- accepted cards without source bindings are detected
- uncited accepted claims are detected
- orphan candidate atomic notes are detected

## Verification

Latest verification commands:

```powershell
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pytest tests\test_doctor.py tests\test_cards.py -q
.venv\Scripts\python scripts\m10_candidate_card_preflight_gate.py
.venv\Scripts\python scripts\m101_candidate_extraction_gate.py
.venv\Scripts\python scripts\m102_card_review_gate.py
.venv\Scripts\python scripts\m103_card_hardening_gate.py
.venv\Scripts\python scripts\v01_release_candidate_gate.py
```

Latest result:

```text
compileall passed
187 tests passed
M10_CANDIDATE_CARD_PREFLIGHT_GATE=passed
M101_CANDIDATE_EXTRACTION_GATE=passed
M102_CARD_REVIEW_GATE=passed
M103_CARD_HARDENING_GATE=passed
V01_RELEASE_CANDIDATE_GATE=passed
```

Latest M10.3 gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m103-card-hardening-gate-20260514102421950705
M103_CARD_HARDENING_GATE=passed
```

Hard metrics:

```json
{
  "accepted_card_without_sources_undetected": 0,
  "missing_accepted_note_undetected": 0,
  "orphan_atomic_notes_undetected": 0,
  "orphan_candidate_cards_undetected": 0,
  "uncited_accepted_claims_undetected": 0
}
```

Positive proof metrics:

```json
{
  "accepted_card_without_sources_detected": 1,
  "missing_accepted_note_detected": 1,
  "orphan_atomic_notes_detected": 1,
  "orphan_candidate_cards_detected": 5,
  "uncited_accepted_claims_detected": 1
}
```

## Boundary

This checkpoint does not include:

- model-backed extraction
- card deduplication or merge workflows
- stale-card status migration beyond current default hiding and accept-time blocking
- answer generation
- `indb ask`

M10 is now ready for an aggregate candidate-card gate before deciding whether to enter the next milestone.
