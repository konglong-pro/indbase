# M10.1 Candidate Extraction Checkpoint

Status: complete

Date: 2026-05-14

Precondition: [M10 Readiness Checkpoint](m10-readiness-checkpoint.md)

Canonical baseline: [mvp-v0.1-spec.md](../mvp-v0.1-spec.md)

## Decision

M10.1 Candidate Extraction is complete as a records-only checkpoint.

This checkpoint creates source-bound candidate card records and claim source bindings. It does not write accepted atomic notes, does not provide accept/reject card review actions, and does not generate answers.

## Behavior

M10.1 adds:

- `candidate_cards`
- `candidate_card_sources`
- deterministic local candidate extraction
- `indb card generate <doc_id>`
- `indb card list`
- `indb card show <candidate_card_id>`

Candidate extraction requires:

- active document
- current revision
- `documents.ingest_status = 'revisioned'`
- current chunks
- claim records with non-empty `source_chunk_ids`
- claim quotes tied to existing source chunks

Candidate extraction rejects:

- source shells
- archived documents
- old revisions
- current revisions with no chunks

Default card listing hides cards bound to old revisions. Old candidate cards remain viewable with an explicit stale-inclusive list or direct `show`, so source provenance is preserved without treating old revision cards as current.

M10.1 writes no files under `notes/atomic/`.

## Gate

The M10.1 gate is:

```powershell
.venv\Scripts\python scripts/m101_candidate_extraction_gate.py
```

It verifies:

- candidate card records are created
- candidate card source bindings are created
- no atomic notes are written before accept/reject exists
- every claim has source chunk IDs
- every claim source chunk exists
- every claim has a quote
- source shells cannot generate cards
- archived documents cannot generate cards
- old revisions cannot generate cards
- no-chunk current revisions cannot generate cards
- old-revision candidate cards are hidden from default list after re-ingest
- stale candidate cards remain explicitly viewable

## Verification

Latest verification commands:

```powershell
.venv\Scripts\python -m compileall -q src tests scripts
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pytest tests\test_db.py tests\test_cards.py -q
.venv\Scripts\python -m pytest tests\test_classification.py tests\test_translations.py -q
.venv\Scripts\python scripts\m10_candidate_card_preflight_gate.py
.venv\Scripts\python scripts\m101_candidate_extraction_gate.py
.venv\Scripts\python scripts\v01_release_candidate_gate.py
```

Latest result:

```text
compileall passed
177 tests passed
M10_CANDIDATE_CARD_PREFLIGHT_GATE=passed
M101_CANDIDATE_EXTRACTION_GATE=passed
V01_RELEASE_CANDIDATE_GATE=passed
```

Latest M10.1 gate:

```text
DOGFOOD_ROOT=E:\indbase\.tmp\m101-candidate-extraction-gate-20260514095213730470
M101_CANDIDATE_EXTRACTION_GATE=passed
```

Hard metrics:

```json
{
  "archived_doc_cards": 0,
  "atomic_notes_written_before_accept": 0,
  "claims_with_missing_chunks": 0,
  "claims_without_quotes": 0,
  "claims_without_source_chunks": 0,
  "no_chunk_cards": 0,
  "old_revision_candidates_default_listed": 0,
  "old_revision_cards_default": 0,
  "source_shell_cards": 0
}
```

Positive proof metrics:

```json
{
  "candidate_cards_created": 1,
  "candidate_sources_created": 3,
  "stale_candidate_cards_viewable": 1
}
```

## Boundary

This checkpoint does not include:

- card accept/reject workflow
- accepted atomic note writing
- card review UI
- card doctor negative tests
- model-backed extraction
- answer generation
- `indb ask`

M10.2 may add card review actions and accepted note writing after M10.1 source binding remains stable.
