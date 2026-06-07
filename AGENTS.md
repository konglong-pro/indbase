# AGENTS.md

## Purpose

This file is the short operational rulebook for coding agents working in
`indbase`. It is not the product spec, architecture spec, glossary, or phase
history log.

## Start Here

- Current scope: `docs/active/current.md`
- Machine-readable phase state: `docs/phase-manifest.yaml`
- Shipped status: `docs/project-status.md`
- Testing and release gates: `docs/testing.md`
- Architecture: `docs/architecture.md`
- Contracts: `docs/contracts/`
- Glossary index: `CONTEXT.md`
- Historical plans: `docs/planning/archive/`

Do not infer the current phase from filename order. Resolve it from
`docs/phase-manifest.yaml` and then read `docs/active/current.md`.

## Agent Behavior

- State non-obvious assumptions before coding.
- Ask before high-risk guesses involving API behavior, auth, data models,
  migrations, payments, or user-visible behavior.
- For low-risk implementation details, state a reasonable assumption and
  proceed.
- Use the smallest change that solves the task inside the existing
  architecture.
- Do not add speculative features, configurability, frameworks, abstractions,
  or dependencies unless explicitly approved.
- Touch only files required for the task.
- Do not reformat, rename, reorganize, or refactor unrelated code.
- Do not revert user changes unless explicitly asked.

## Documentation Work

Use the current docs skill:

```text
C:\Users\62406\.codex\skills\project-docs-curator\SKILL.md
```

Root entry files are bootloaders. Put durable rules in `docs/contracts/`,
decisions in `docs/adr/`, current implementation scope in
`docs/planning/active/`, compressed status in `docs/project-status.md`, and
historical evidence under archive folders.

## Current Active Scope

Read `docs/active/current.md` first.

Current indbase phase:

- phase id: `v0.3.3`
- title: Retrieval Evaluation / Answer Readiness
- canonical spec:
  `docs/planning/active/v0.3.3-retrieval-evaluation-answer-readiness.md`
- agent rules: `docs/agents/current/indbase.md`
- release gate: `uv run python scripts/v033_retrieval_eval_release_gate.py`

Completed compatibility note: `v0.3.2.3f-indbase-nl-v2-intent-drafting` is
closed out in this repo. Its consoler execution brief was
`E:\consoler\docs\planning\v4g-indbase-nl-v2-intent-drafting.md`. Keep this
string for static coordination tests, but do not treat 3f as current work.

## Repo Map

- `src/indbase_core/`: durable services and vault data model.
- `src/indbase_cli/`: Typer CLI surface for `indb`.
- `src/indbase_agent/`: consoler-facing Source Trust Loop adapter.
- `src/indbase_core/migrations/`: SQLite migrations.
- `scripts/`: release gates and historical gate scripts.
- `tests/`: pytest suite and fixtures.
- `docs/contracts/`: durable behavior contracts.
- `docs/planning/active/`: current implementation plans.
- `docs/planning/archive/`: historical plans and evidence.
- `docs/agents/current/`: current task routing for implementation agents.

## Common Commands

```powershell
uv sync --group dev
uv run python -m pytest
uv run python -m compileall -q src tests scripts
uv run python scripts/check_docs.py
```

Focused gates:

```powershell
uv run python scripts/v02_deterministic_release_gate.py
uv run python scripts/doctor_negative_gate.py
uv run python scripts/v033_retrieval_eval_release_gate.py
```

Use `docs/testing.md` for phase-specific gates before touching taxonomy, tag
governance, governed search, retrieval evaluation, or `indbase_agent`.

## Task Routing

- Core data, revisions, chunks, ingest, output, search, doctor: read
  `docs/contracts/` and `docs/architecture.md`, then inspect
  `src/indbase_core/`.
- Current v0.3.3 retrieval evaluation work: read `docs/active/current.md`,
  `docs/planning/active/v0.3.3-retrieval-evaluation-answer-readiness.md`, and
  `docs/agents/current/indbase.md`.
- Consoler adapter work: read `docs/contracts/consoler-agent-boundary.md` and
  `docs/contracts/artifact-contract.md`, then inspect `src/indbase_agent/`.
- Documentation lifecycle work: read `docs/phase-manifest.yaml`,
  `docs/active/current.md`, and `scripts/check_docs.py`.
- Historical phase archaeology: start from `docs/project-status.md`; read
  archived phase docs only when a task explicitly requires them.

If no active doc covers the task, stop and ask for explicit scope or implement
only safe interface-preserving work.

## Non-Negotiable Rules

- The vault is the system of record.
- Do not bypass `indbase_core` services for durable operations.
- Do not mutate immutable revisions.
- Do not physically delete revisions or chunks in normal workflows.
- Candidate conversion output is not a trusted source revision.
- Export artifacts are not source revisions.
- Default source search indexes promoted current source revisions only.
- Do not write normal search snippets to `citations`.
- Do not add LLM calls, `ask`, generated answers, embeddings, or provider
  behavior unless the active scope explicitly allows it.
- All durable operations must be traceable.
- All failures must be visible through tasks, task events, errors, review
  items, or explicit command output as appropriate.
- Do not implement archived, superseded, or future phase work unless explicitly
  asked.

## Do Not Edit Unless Explicitly Asked

- Private vaults, local smoke output, `.tmp/`, runtime SQLite files, provider
  prompts/responses, raw traces, screenshots with private content, and local
  path secrets.
- External consoler protocol/runtime/store/schema files from this repository.
- Historical archive files except to add lifecycle metadata or link fixes.

## Validation

Use `docs/testing.md`. For docs lifecycle changes, run:

```powershell
uv run python scripts/check_docs.py
git diff --check
```

For code changes, run the smallest focused test first, then the relevant gate.
Do not claim a check passed unless it was run in the current session.

## Done Means

Report:

- files changed
- commands run
- checks skipped and why
- remaining risks
