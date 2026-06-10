---
doc_type: gate_checkpoint
phase_id: v0.2
title: v0.2 Release Gate Checkpoint
status: archived
canonical: true
read_by_default: false
related_specs:
  - docs/planning/archive/v0.2/swallow-ingest-integration.plan.md
  - docs/planning/archive/v0.2/transition-output-integration.plan.md
---
# v0.2 Release Gate Checkpoint

Status: active gate stack defined (deterministic layer replaces M3 as release blocker)

Canonical specs:

- [v0.2 swallow ingest integration](swallow-ingest-integration.plan.md)
- [v0.2 transition output integration](transition-output-integration.plan.md)
- Historical v0.1 baseline: [mvp-v0.1-spec.plan.md](../v0.1/mvp-v0.1-spec.plan.md)

## Release standard (five layers)

| Layer | Script / command | Required when |
| --- | --- | --- |
| A | `pytest` | Daily + every release |
| B | `python -m compileall -q src tests scripts` | Daily + every release |
| C | `python scripts/v02_deterministic_release_gate.py` + `python scripts/doctor_negative_gate.py` | Every release |
| D | `v02_swallow_smoke_gate.py`, `v02_transition_smoke_gate.py` | When target env is available; **skip, never fake-pass** |
| E | `v02_real_corpus_dogfood_gate.py` | Formal release / dogfood; waiver only with explicit env |

Aggregate entry point:

```powershell
.venv\Scripts\python scripts\v02_release_gate.py
```

## Historical gates (not v0.2 blockers)

| Script | Role |
| --- | --- |
| `scripts/m3_dogfood_gate.py` | **Historical v0.1 compatibility** — assumes immediate revision + search on default vault |
| `scripts/v01_release_candidate_gate.py` | MVP RC aggregate — predates v0.2 swallow/transition defaults |
| `scripts/mvp_release_gate.py` | Full MVP feature aggregate — historical |

A red **M3** result under v0.2 defaults does **not** mean core regression. It means the script still encodes v0.1 assumptions (`swallow_ingest=false` → no conversion).

## Layer C: deterministic v0.2 vault gate

`v02_deterministic_release_gate.py` builds an explicit v0.2 vault:

- `features.swallow_ingest=true`
- `features.transition_output=true` (runtime scaffold installed)
- deterministic swallow stub (stable CI; proves indbase trust boundaries)
- fake transition bridge for export/normalize

Proves:

- trusted-current file ingest → revision, chunks, FTS, searchable
- `review-before-current` candidate → **not** in default search
- archive one-to-many → parent/child ingest items + child docs
- URL snapshot → artifact + searchable revision
- `output export` → derived artifacts only
- `doc normalize --replace-current` → new `rev_0002`, old revision retained
- doctor hard findings = 0 on the healthy vault

## Layer C-negative: doctor negative gate

`doctor_negative_gate.py` no longer depends on default-config ingest succeeding.

Flow:

1. Build a healthy v0.2 vault (deterministic swallow + export).
2. Copy per scenario and corrupt one invariant.
3. Assert doctor returns exit code 2 with expected hard finding codes.

## Layer D: real-tool smoke (opt-in)

```powershell
$env:INDBASE_SWALLOW_SMOKE='1'
.venv\Scripts\python scripts\v02_swallow_smoke_gate.py

$env:INDBASE_TRANSITION_SMOKE='1'
# optional first-time npm install:
$env:INDBASE_TRANSITION_SMOKE_INSTALL='1'
.venv\Scripts\python scripts/v02_transition_smoke_gate.py
```

Swallow smoke uses a **long** trusted fixture (not short Chinese samples that correctly land in `review-before-current`).

Transition smoke runs the real Node bridge subprocess. PDF/DOCX/HTML render targets are not unconditional CI blockers unless the environment installs pandoc/xelatex/CJK fonts.

## Layer E: real-corpus dogfood

```powershell
$env:INDB_REAL_CORPUS='D:\path\to\corpus'
.venv\Scripts\python scripts/v02_real_corpus_dogfood_gate.py
```

Waiver (documented only):

```powershell
$env:INDB_V02_DOGFOOD_WAIVED='1'
$env:INDB_V02_DOGFOOD_WAIVER_REASON='reason recorded in release notes'
```

## GitHub Actions CI

Workflows under `.github/workflows/`:

| Workflow | Trigger | Jobs |
| --- | --- | --- |
| `ci.yml` | push/PR to `main` or `master` | A pytest, B compileall, C deterministic + doctor negative, D swallow smoke, D transition smoke |
| `release-dogfood.yml` | `workflow_dispatch`, weekly schedule | E real-corpus dogfood |

Required on every PR (cloud runners):

```text
test → v02-deterministic → swallow-smoke + transition-smoke (parallel)
```

Install uses `uv sync` per `README.md`. Swallow smoke installs `--extra swallow` and sets `INDBASE_SWALLOW_SMOKE=1`. Transition smoke uses Node 20 and `INDBASE_TRANSITION_SMOKE=1` (scaffold bridge; no pandoc/xelatex gate).

Layer E is **not** a PR blocker. Use:

- Repository variable `INDB_REAL_CORPUS` (self-hosted / custom runner with corpus on disk), or
- `workflow_dispatch` with repo fixture `tests/fixtures/v02_dogfood_corpus/`, or
- weekly schedule against that fixture.

Historical `m3_dogfood_gate.py` is intentionally **not** invoked by `ci.yml`.

### Branch protection (recommended)

Require these GitHub checks on `main`:

```text
A/B — pytest + compileall
C — v0.2 deterministic vault + doctor negative
D — real swallow smoke
D — real Node transition smoke
```

Do **not** require `Release dogfood` on every PR unless you maintain a private corpus runner.

## What “release-ready” means now

```text
pytest green
compileall green
v02 deterministic gate green
doctor negative gate green
doctor hard findings = 0 on deterministic healthy vault
real swallow smoke run in target env (or skipped with reason)
real transition smoke run in target env (or skipped with reason)
real corpus dogfood run (or explicit waiver)
```

Fake/stub layers prove **indbase** owns revision, artifact, search, and doctor boundaries. Real-tool layers prove external packages are wired; they must not bypass indbase trust checks.