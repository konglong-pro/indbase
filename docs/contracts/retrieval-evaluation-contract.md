# Retrieval Evaluation Contract

## Purpose

Define deterministic retrieval evaluation and answer-readiness boundaries.

## Applies To

- v0.3.3 retrieval evaluation cases, runs, and results.
- Answer-readiness reports for persisted retrieval runs.
- `scripts/v033_retrieval_eval_release_gate.py`.

## Rules

- Evaluation measures persisted retrieval evidence; it does not generate
  answers.
- Readiness reports classify whether a retrieval run is safe enough for a
  future answer workflow.
- Evaluation may create eval/readiness records and retrieval runs produced by
  normal retrieval execution.
- Evaluation must not write `citations`, source revisions, chunks, source
  Markdown, output artifacts, taxonomy records, profiles, or feature atoms.
- No LLM judge, provider call, network call, or model credential is part of the
  default v0.3.3 evaluation path.
- Fixture import/export must be deterministic JSONL.

## Non-Goals

- No `indb ask`.
- No answer, summary, claim, card, or note generation.
- No retrieval ranking rewrite.

## Validation

- Run `uv run python -m pytest tests/test_retrieval_evaluation.py -q`.
- Run `uv run python scripts/v033_retrieval_eval_release_gate.py`.
