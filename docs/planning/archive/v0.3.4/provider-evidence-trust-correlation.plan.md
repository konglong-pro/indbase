---
doc_type: phase_plan
phase_id: v0.3.4
title: Provider Evidence / Trust Correlation
status: completed
owner: indbase
canonical: true
read_by_default: false
supersedes:
  - docs/planning/archive/v0.2/swallow-ingest-integration.plan.md
  - docs/planning/archive/v0.2/transition-output-integration.plan.md
superseded_by: null
depends_on:
  - v0.2-swallow-ingest
  - v0.2-transition-output
  - v0.3.2.3f-indbase-nl-v2-intent-drafting
  - v0.3.3
related_contracts:
  - docs/contracts/provider-capability-contract.md
  - docs/contracts/trust-boundary.md
  - docs/contracts/artifact-contract.md
  - docs/contracts/consoler-agent-boundary.md
  - docs/contracts/source-search-contract.md
  - docs/contracts/revision-contract.md
related_adrs: []
release_gate: scripts/provider_fake_release_gate.py
---

# v0.3.4 Provider Evidence / Trust Correlation

Status: completed baseline; read the closeout first for verification state.

Date: 2026-06-09

## Goal

Turn indbase from a deep swallow/transition integration into a capability
consumer, trust boundary, evidence compiler, and vault state owner.

The core rule is:

```text
provider output = evidence or candidate
indbase decision = trusted state
```

## Current Boundary

swallow is a source ingest provider. It converts raw input into traceable
Markdown evidence. It does not manage memory, RAG, agents, chat, or knowledge
base state.

transition is a Markdown normalization and conversion provider. It normalizes
Markdown and converts it to derived outputs. It does not ingest raw sources,
perform OCR/ASR/web scraping, or rewrite content with an LLM.

indbase owns:

- trusted source revisions
- promotion policy
- vault artifact copies
- provider run records
- search indexes
- output records
- task, error, review, and audit state
- consoler-facing `indbase://...` artifact views

## Product Vocabulary

External command names remain indbase names:

```text
indb ingest
indb ingest url
indb output export
indb doc normalize
indbase.ingest_file
indbase.search_sources
indbase.doc_show
```

Provider capability names are internal bindings:

```text
indbase.ingest.file -> swallow.ingest.file
indbase.ingest.url -> swallow.ingest.url
indbase.output.normalize -> transition.markdown.normalize
indbase.output.export -> transition.markdown.export
```

## Implementation Plan

### Step 1: Contracts Only

Add provider contracts without calling real providers:

```text
src/indbase_core/capabilities/contracts.py
src/indbase_core/artifacts/evidence.py
tests/test_provider_contracts.py
```

Define:

- `CapabilityManifest`
- `ProviderProfile`
- `ArtifactRef`
- `ProviderError`
- `IngestEvidencePackage`
- `OutputEvidencePackage`

Success criteria:

- packages validate required provider/capability/profile/version fields
- artifact refs carry trust and URI/path metadata without exposing provider
  cache as durable indbase state
- provider error codes map to indbase provider error taxonomy
- provider capability ids are globally namespaced
- `provider_id` is logical identity; package name/version are implementation
  metadata

### Step 2: Fake Providers

Add deterministic fake providers:

```text
tests/fakes/fake_ingest_provider.py
tests/fakes/fake_output_provider.py
```

Wire indbase services to run through fake provider ports first.

Success criteria:

- fake ingest success creates evidence, promotion decision, revision, chunks,
  and FTS rows
- fake ingest failed/cancelled creates visible task/error state and no revision
- fake ingest partial creates review/candidate state and does not pollute
  default source search
- fake output export creates output artifacts only
- fake normalize with replace creates a new immutable source revision while
  leaving the old revision intact

### Step 3: Swallow Adapter Extraction

Move existing swallow integration behind:

```text
src/indbase_integrations/swallow/provider.py
src/indbase_integrations/swallow/mapper.py
src/indbase_integrations/swallow/errors.py
src/indbase_integrations/swallow/doctor.py
```

Do not change ingest product semantics in this step. Only change the call
boundary from "special integration stage" to `IngestProvider`.

Success criteria:

- existing ingest behavior still passes through promotion policy
- swallow result is mapped to `IngestEvidencePackage`
- core ingest code does not import swallow backend internals

### Step 4: Transition Adapter Extraction

Move the existing transition bridge behind:

```text
src/indbase_integrations/transition/provider.py
src/indbase_integrations/transition/node_bridge.py
src/indbase_integrations/transition/mapper.py
src/indbase_integrations/transition/errors.py
src/indbase_integrations/transition/doctor.py
```

Keep the pinned Node bridge as the default profile.

Success criteria:

- output export calls `MarkdownOutputProvider.export`
- normalize calls `MarkdownOutputProvider.normalize`
- transition Node/Pandoc details stay outside `indbase_core`
- provider capability `transition.markdown.export` maps to indbase
  `output export`

### Step 5: Unified Evidence Store

Move provider evidence copying into:

```text
src/indbase_core/artifacts/evidence_store.py
```

Provider adapters return refs; indbase copies evidence into:

```text
.indbase/artifacts/provider_runs/<provider_run_id>/
```

Success criteria:

- provider cache is never durable truth
- copied evidence includes provider metadata, manifest, trace, primary output,
  reports, warnings/errors, and evidence index
- promotion/output recording happens only after required evidence is copied and
  validated
- `ingest_runs` and `output_runs` store adopted provider run pointers and
  indbase decision summaries, not provider attempt evidence
- external artifact views expose provider evidence summaries, not file-level
  evidence browsing

### Step 6: Provider Run Records And Bindings

Add provider binding configuration and durable run metadata.

Planned binding file:

```text
capability-bindings.yaml
```

The file lives at the repository root for reviewability. Packaged installs must
also have an internal default fallback.

Binding precedence:

```text
repository/package default
-> explicit vault-local administrative override
```

Vault-local overrides are allowed so a vault can pin a compatible replacement
provider or profile. They must remain configuration, not ordinary command
surface. Commands such as `indb ingest` and `indbase.ingest_file` still resolve
through indbase product semantics and must not expose `--provider` style backend
selection.

Default profiles:

```text
swallow -> local_core
transition -> node_bridge
```

Default forbidden profiles:

```text
http
mcp
queue unless explicitly enabled for a binding
```

Provider run metadata must correlate:

```text
consoler action_id
-> indbase task_id
-> ingest_run_id or output_run_id
-> provider_run_id
-> provider job id
-> copied manifest and trace
```

`provider_runs` is one row per provider attempt. Create the row and evidence
root before calling the provider. v0.3.4 does not perform automatic retry or
cross-profile fallback; multiple attempts come only from explicit reruns, tests,
or future retry policy.

Use the field shape in `docs/contracts/provider-capability-contract.md`.
Important implementation rules:

```text
provider_status = success | partial | failed | cancelled
evidence_status = pending | copied | missing_required | invalid | copy_failed
started_at, finished_at, and evidence_copied_at are separate
provider and indbase error codes are both preserved
table rows keep summary and artifact refs, not full provider manifest JSON
```

New state links:

```text
ingest_runs.adopted_provider_run_id
converter_runs.adopted_provider_run_id
output_runs.adopted_provider_run_id
errors.provider_run_id
review_items.provider_run_id
```

`task_events.metadata_json` carries provider correlation metadata. `tasks` does
not store a single provider run id.

Success criteria:

- ingest/output records store provider run identity
- provider version, capability id, profile, operation id, job id, manifest ref,
  trace ref, warning count, error count, and primary error code are visible
- legacy `features.swallow_ingest=true` maps to `providers.swallow.enabled=true`
  for compatibility
- repo default bindings and vault-local override precedence are deterministic
  and covered by tests
- ordinary user commands do not expose provider backend selection
- required capability contract/version mismatch fails doctor and command
  execution; optional mismatch disables that feature with a warning
- legacy `converter_runs.external_*` fields may still be written for
  compatibility, but new behavior depends on `provider_runs`

### Step 7: Doctor Provider Section

Add provider-aware doctor output.

Doctor must distinguish:

- required dependency missing: fail
- optional dependency missing: warning
- profile unavailable while enabled: fail
- feature disabled: explicit disabled state
- last copied evidence health: warning or fail based on missing required files
- adopted provider run evidence problems: hard when trusted state or successful
  output depends on that evidence
- non-adopted failed/cancelled attempts with missing evidence: warning or info

Success criteria:

- doctor reports provider config, package version, profile, capabilities, and
  required/optional dependency status
- no doctor check shells into HTTP/MCP by default

### Step 8: Agent Trace And Artifact Views

Keep `indbase_agent` backend-agnostic.

All agent commands emit consoler operation trace. Provider-backed commands add
capability refs; read-only commands emit operation/domain refs without provider
refs. Artifact blocks remain indbase-owned:

```text
indbase://ingest_runs/<id>
indbase://ingest_runs/<id>/evidence
indbase://output_runs/<id>
indbase://output_runs/<id>/evidence
indbase://provider_runs/<id>
indbase://provider_runs/<id>/evidence
```

Success criteria:

- `indbase_agent` never calls swallow or transition directly
- no provider cache path or provider URI appears as an artifact block URI
- operation trace contains provider/capability/run correlation when available
- views summarize evidence, manifest, trace tail, warnings/errors, and
  promotion/output decision without becoming a vault browser
- no file-level provider evidence URI is exposed through consoler artifact
  blocks

### Step 9: Release Gates

Add:

```text
scripts/provider_contract_gate.py
scripts/provider_fake_release_gate.py
scripts/provider_real_smoke.py
```

Gate layers:

- A: unit tests and compileall
- B: fake provider deterministic gate
- C: doctor negative provider scenarios
- D: real swallow/transition smoke when environment is available
- E: real-corpus dogfood for formal release only

The current manifest points at `scripts/check_docs.py` until Step 9 creates the
provider fake gate.

Real provider smoke defaults to explicit skip when provider runtime is missing.
`INDBASE_PROVIDER_SMOKE_REQUIRED=1` makes missing runtime a failure.

## Output And Normalize Decisions

Output remains source revision -> provider -> derived artifact/output run.

Normalize without replace-current:

```text
create output_run
copy provider evidence
record normalized markdown as output_artifacts row
do not create source revision
do not re-chunk
do not update FTS
```

Normalize with replace-current:

```text
copy provider evidence
validate diff/evidence
create new immutable source revision
re-chunk
update current FTS rows
preserve old revision
```

`output_artifacts` gets:

```text
artifact_role = normalized_candidate | export_output | diff | report
trust_level = derived_candidate | derived_output | evidence
```

Default mapping:

```text
normalized markdown -> normalized_candidate + derived_candidate
normalize diff -> diff + derived_candidate
html/pdf/docx export -> export_output + derived_output
```

Do not add an ingest artifact row table in v0.3.4. Ingest provider evidence
stays in `provider_runs/<id>/evidence_index.json`; trusted sources stay in
document revisions and chunks; rejected/partial candidates are reached through
review/task/error records.

## Provider Extensibility

The contract is provider-generic. v0.3.4 defaults to swallow and transition,
but future contract-compatible providers may be bound through admin config or
future repo defaults.

v0.3.4 does not implement dynamic provider loading. Use a static registry and
explicit adapters. Do not add entry point discovery, arbitrary class import, or
provider marketplace behavior in this phase.

## Error Mapping

Provider errors must map into indbase error taxonomy.

Examples:

```text
swallow QUALITY_BELOW_THRESHOLD -> provider_quality_rejected
swallow INPUT_TOO_LARGE_SYNC -> provider_input_too_large
swallow WORKER_TIMEOUT -> provider_timeout
transition PDF_ENGINE_MISSING -> provider_dependency_missing
transition PANDOC_FAILED for PDF only -> provider_partial_success
transition WRITE_REFUSED -> provider_output_write_failed
```

Errors must enter tasks, task events, errors, review items, output warnings, or
explicit command output as appropriate.

## Search Pollution Rules

Required tests:

- rejected swallow candidate is not searchable
- partial swallow candidate is not searchable by default
- copied provider evidence text is not indexed as source search
- transition PDF/DOCX/HTML export output is not searchable
- normalize without replace does not affect current source search
- normalize with replace indexes only the new current source revision
- old revisions remain immutable and are not default searchable after replace

## Non-Goals

- Do not add `ask`, generated answers, summaries, claims, or LLM judges.
- Do not expose provider backend selection to ordinary users.
- Do not default to HTTP, queue, or MCP profiles.
- Do not let consoler call swallow or transition directly.
- Do not return `swallow://...`, `transition://...`, or provider cache paths as
  artifact block URIs.
- Do not make provider cache the indbase system of record.
- Do not rewrite retrieval ranking, taxonomy, or answer readiness.
- Do not turn artifact views into a vault browser or provider job browser.

## Acceptance Checklist

- Provider contracts exist and are tested.
- Fake providers cover success, partial, failed, and cancelled states.
- Swallow ingest is behind `SwallowIngestProvider`.
- Transition output is behind `TransitionOutputProvider`.
- Provider results map into evidence packages before promotion/output records.
- Required evidence is copied into `.indbase/artifacts`.
- Provider runs are durably recorded and trace-correlatable.
- Provider errors map into indbase observability surfaces.
- Ingest remains evidence -> promotion -> revision/chunks/FTS.
- Output remains source revision -> derived artifact/output run.
- Doctor reports provider availability and optional dependencies.
- Agent operation trace and artifact views expose indbase-owned summaries.
- Fake provider release gate passes.
- Real provider smoke is environment-gated.
