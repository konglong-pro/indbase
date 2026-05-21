# v0.3.3 Retrieval Evaluation / Answer Readiness Agent Guide

Read this before implementing v0.3.3 retrieval evaluation work.

Canonical project spec:

- `docs/planning/v0.3.3-retrieval-evaluation-answer-readiness.md`

Baseline context:

- `AGENTS.md`
- `docs/project-status.md`
- `docs/testing.md`
- `docs/planning/mvp-v0.1-spec.md`
- `docs/planning/v0.3.1-taxonomy-foundation.md`
- `docs/planning/v0.3.2-retrieval-intelligence-foundation.md`
- `docs/planning/v0.3.2-retrieval-dogfood-report.md`
- `docs/agents/v0.3.2-retrieval-intelligence-foundation/AGENT.md`

## Mission

Build deterministic retrieval evaluation and answer-readiness infrastructure.

Short version:

```text
Retrieve already builds packages.
Eval measures packages.
Readiness decides whether a package can feed future ask.
Ask remains out of scope.
```

v0.3.3 should make retrieval quality observable and regression-testable before any answer generation begins.

## Non-negotiables

- Do not implement `indb ask`.
- Do not generate answers, summaries, claims, cards, or notes.
- Do not write `citations`.
- Do not call LLMs or model judges.
- Do not add real providers, auth, network calls, or cost-bearing services.
- Do not rewrite v0.3.2 retrieval ranking, filter semantics, quote extraction, vector search, or taxonomy boosts.
- Do not use evaluation to mutate taxonomy, profiles, source documents, revisions, chunks, or output artifacts.
- Do not make real-corpus dogfood a required PR blocker.
- Do not create tasks for eval runs in v0.3.3; use eval table status and `error_json`.

## Architecture Boundary

v0.3.3 owns:

- evaluation cases
- evaluation runs
- evaluation results
- answer-readiness reports
- deterministic evaluator logic
- deterministic readiness policy
- JSONL import/export
- eval CLI rendering
- doctor checks
- C4 release gate

v0.3.3 may read:

- `retrieval_runs`
- `retrieval_items`
- current source document/chunk rows for validation
- v0.3.2 retrieval service when running eval cases

v0.3.3 must not own:

- answer generation
- citation generation
- retrieval ranking policy
- taxonomy governance
- profile generation
- source mutation
- provider calls

## Phase Scope

Implement:

- `indb eval retrieval import <jsonl>`
- `indb eval retrieval export --suite <name>`
- `indb eval retrieval run --suite <name>`
- `indb eval retrieval list`
- `indb eval retrieval show <eval_run_id>`
- `indb eval retrieval readiness <retrieval_run_id>`
- eval/readiness schema
- deterministic JSONL case validation
- deterministic expectation evaluator
- deterministic answer-readiness policy
- doctor checks
- repo-owned fixture suite
- C4 release gate

Do not implement:

- `indb ask`
- `indb ask --run`
- answer drafts
- answer citations
- LLM judge
- semantic similarity judge
- automatic dogfood-to-hard-gate promotion
- retrieval MMR or dedupe rewrite

## Confirmed Execution Rules

These decisions are part of the v0.3.3 implementation contract.

### Goal

The primary deliverable is evaluation plus readiness, not retrieval hardening.

Use v0.3.2 dogfood findings as evaluation dimensions:

- sparse tag filter can return zero
- filter-only query currently fails
- boost reasons can be noisy
- quotes can be exact but weak
- duplicate chunks from one document can dominate
- profile coverage can be incomplete

Do not solve all of those in v0.3.3. Make them measurable and readiness-visible.

### Storage

Add database tables and JSONL import/export.

The database gives vault-local audit state. JSONL gives version-controlled fixtures for CI and review.

### CLI

Use:

```text
indb eval retrieval ...
```

Do not put eval commands under `indb retrieval eval`. Keep `eval` as the future parent namespace for retrieval, answer, and card evaluation.

### Readiness

Answer readiness is a first-class persisted report.

Verdicts:

```text
ready
needs_more_evidence
not_ready
```

Future ask v1 must consume a readiness-checked `retrieval_run_id`. Do not design ask v1 around hidden internal retrieval.

### Evaluation Granularity

Use evidence-level expectations.

Supported expectation concepts:

- expected documents
- forbidden documents
- expected quote substrings
- minimum result count
- minimum unique document count
- expected readiness verdict
- expected or forbidden warning/blocker codes

Avoid a weak "any result exists" gate.

### Judge Source

Use deterministic rules only.

No LLM judge, no remote service, no fake-provider dependency for main v0.3.3 behavior.

### Observability

Use `status` and `error_json` on eval tables.

Do not add task records for eval runs in v0.3.3.

## Schema Rules

Add a migration after `0009_retrieval_intelligence.sql`.

Add:

- `retrieval_eval_cases`
- `retrieval_eval_runs`
- `retrieval_eval_results`
- `answer_readiness_reports`

Use ID prefixes:

```text
retrcase
retrievaleval
retrievalevalresult
answerready
```

Required case behavior:

- cases belong to a suite
- cases can be active or archived
- cases have query, options JSON, expectations JSON, and source
- import validates required fields and supported expectations
- export is deterministic

Required eval run behavior:

- run has suite, evaluator version, policy version, counts, status, error JSON, timestamps
- run can finish succeeded, failed, or partial
- failed case expectations must make the eval command exit nonzero

Required result behavior:

- each result links to eval run and case
- successful case execution links to a retrieval run
- readiness report id is stored when readiness is computed
- status is passed, failed, or error

Required readiness behavior:

- each report links to a retrieval run
- verdict is constrained to ready, needs_more_evidence, or not_ready
- blockers, warnings, and metrics are JSON

## JSONL Case Rules

Required import fields:

```text
suite
name
query
expect
```

Optional fields:

```text
case_id
options
notes
```

Supported options:

```text
mode
top_k
candidate_k
per_doc_limit
```

Supported expectations:

```text
expected_doc_ids
expected_quote_contains
forbidden_doc_ids
min_result_count
min_unique_docs
expected_readiness
expected_warnings
forbidden_warnings
```

Rules:

- invalid JSONL rows fail import visibly
- unsupported option or expectation keys fail import
- missing required fields fail import
- export must preserve stable ordering

## Readiness Policy Rules

Policy version:

```text
answer-readiness-v1
```

`not_ready` blockers:

- missing retrieval run
- retrieval run status is failed
- zero items
- missing doc/revision/chunk binding
- empty quote
- quote not found in referenced chunk

`needs_more_evidence` cautions:

- item count below threshold
- unique doc count below threshold
- duplicate doc ratio too high
- profile-missing ratio too high
- warning count too high
- retrieval run status is partial

Default thresholds:

```text
min_items = 2
min_unique_docs = 1
max_duplicate_doc_ratio = 0.75
max_profile_missing_ratio = 0.80
max_warning_count = 10
min_quote_chars = 20
```

Hard source-binding and quote failures always stay `not_ready`, even if an eval case overrides thresholds.

## Implementation Order

1. Add schema migration.
2. Add eval/readiness dataclasses and persistence helpers.
3. Add JSONL import/export validation.
4. Add answer-readiness policy.
5. Add evaluator that runs existing retrieval and compares expectations.
6. Add CLI commands.
7. Add doctor checks.
8. Add repo-owned fixture JSONL.
9. Add tests.
10. Add `scripts/v033_retrieval_eval_release_gate.py`.
11. Update CI, testing docs, project status, and planning index.

Do not begin answer generation in this phase.

## Doctor Expectations

Default doctor must check eval/readiness integrity without rerunning eval and without recomputing readiness.

Add findings for:

- eval case invalid JSON
- eval result missing case
- eval result missing eval run
- eval result missing retrieval run
- readiness report missing retrieval run
- readiness report invalid JSON
- readiness report invalid verdict
- readiness report with no blocker despite quote mismatch when the referenced item is corrupt

Doctor diagnoses only. It must not repair eval or readiness records.

## Test Expectations

Add focused tests for:

- migration applies cleanly
- invalid statuses rejected
- JSONL import/export round trip
- invalid JSONL rejected
- unsupported expectation rejected
- eval run creates results and linked retrieval runs
- passing case passes
- expected doc missing fails
- forbidden doc present fails
- expected quote substring missing fails
- min result count and min unique docs enforced
- readiness reports ready, needs_more_evidence, and not_ready
- zero-item retrieval is not_ready
- quote mismatch is not_ready
- duplicate/profile-missing-heavy package is needs_more_evidence
- doctor catches broken eval/readiness records
- eval does not write citations, output artifacts, source revisions, taxonomy mutations, profiles, or feature atoms

Add gate:

```text
scripts/v033_retrieval_eval_release_gate.py
```

## Completion Report

Every implementation turn should report:

- changed files
- schema changes
- CLI commands touched
- eval/readiness tables touched
- fixture cases added
- readiness policy version
- tests run
- tests not run
- remaining risks
- confirmation that ask, citations, model judges, retrieval ranking rewrites, source mutation, and taxonomy mutation remain out of scope
