# Provider Capability Contract

## Purpose

Define how indbase consumes packaged capability providers without letting a
provider become a trusted state owner.

## Applies To

- `src/indbase_core/capabilities/`
- `src/indbase_core/artifacts/`
- `src/indbase_integrations/swallow/`
- `src/indbase_integrations/transition/`
- `src/indbase_agent`
- `capability-bindings.yaml`

## Terms

**Capability Provider**: Replaceable implementation that performs a bounded
operation and returns evidence.

**Provider Profile**: Local SDK, CLI, Node bridge, HTTP, queue, or MCP transport
mode for a provider. The default profile must be local and pinned unless an
active phase explicitly approves otherwise.

**Provider Binding**: Internal mapping from an indbase product command to a
provider capability and profile. Repository/package defaults may be overridden
by explicit vault-local configuration; ordinary user commands must not expose
backend selection.

**Evidence Package**: Indbase-owned typed summary of a provider result plus
artifact references, warnings, errors, hashes, and trace metadata.

**Provider Run**: Durable indbase record correlating an indbase operation with
one provider execution attempt and its copied evidence.

**ArtifactRef**: Internal artifact reference that may carry a provider-side URI,
an indbase-owned vault path after copy, and an indbase URI for bounded external
views. External callers see only `indbase://...` URIs.

## Rules

- Providers produce evidence, candidates, or derived artifacts. They do not
  create trusted source state.
- indbase owns promotion policy, source revisions, chunks, search indexes,
  output records, tasks, errors, review items, vault artifacts, and doctor
  findings.
- `indbase_core` defines ports and contracts. Provider-specific behavior lives
  under `src/indbase_integrations/`.
- `indbase_core` must not depend on swallow backend details or transition
  Node/Pandoc details.
- User-facing commands remain indbase commands such as `indb ingest`,
  `indb output export`, `indbase.ingest_file`, and `indbase.search_sources`.
- Provider capability names are internal binding names, not user-facing product
  names.
- Binding precedence is repository/package default first, then explicit
  vault-local override. Overrides must still satisfy the provider contract,
  allowed profiles, feature flags, and trust rules.
- Vault-local overrides are administrative configuration. They must not appear
  as ordinary CLI or agent command options such as `--provider swallow`.
- Provider cache paths are disposable. Required provider evidence must be
  copied into `.indbase/artifacts/...` before promotion or output recording.
- Provider artifact refs such as `swallow://...` or `transition://...` must not
  be returned to consoler as artifact block URIs.
- Agent-visible artifact blocks use only `indbase://...` URIs.
- Source search indexes promoted current source revision chunks only.
- Export artifacts and unpromoted ingest candidates must not enter default
  source search.
- HTTP, queue, and MCP provider profiles are forbidden by default unless the
  active binding explicitly enables them.
- Provider errors must be mapped into indbase task, error, review, or command
  output surfaces. They must not remain only in provider logs.
- Required provider contract/version mismatches are hard failures. Optional
  capability mismatches become warnings and disabled features.
- `provider_id` is a stable logical id. Package name and package version are
  implementation metadata, not the provider identity.
- Provider capability ids must be globally namespaced, such as
  `swallow.ingest.file` or `vendor_x.ingest.file`.
- v0.3.4 uses a static registry and explicit adapters. It does not dynamically
  import arbitrary provider classes or discover providers through entry points.
- v0.3.4 does not perform automatic retry or cross-profile fallback. Multiple
  provider attempts are supported for explicit reruns, tests, and future retry
  policy only.

## Machine-Facing Shape

Core ingest code consumes an `IngestProvider` port that returns an
`IngestEvidencePackage`.

Core output code consumes a `MarkdownOutputProvider` port that returns an
`OutputEvidencePackage`.

Minimum common evidence fields:

```text
provider_id
provider_version
capability_id
transport_profile
operation_id
provider_job_id
status
warnings
errors
manifest
trace
```

Ingest evidence additionally includes input/raw references, candidate Markdown,
the provider ingest document, intermediate artifacts, content hash, and quality
metadata.

Output evidence additionally includes the source revision binding, input hash,
normalized Markdown, derived outputs, diffs, reports, and output warnings.

## Provider Runs

`provider_runs` is one row per provider attempt, not one row per indbase
operation.

Rows are created before invoking the provider. indbase preallocates
`provider_run_id` and an evidence root, then updates the row after provider
completion and evidence copy.

Required correlation fields:

```text
provider_run_id
operation_id
action_id
task_id
ingest_run_id
output_run_id
provider_id
provider_package
provider_version
capability_id
capability_contract_version
transport_profile
provider_job_id
```

`action_id`, `task_id`, `ingest_run_id`, and `output_run_id` may be null when
the workflow does not have that context.

State fields are split:

```text
provider_status   = success | partial | failed | cancelled
evidence_status   = pending | copied | missing_required | invalid | copy_failed
```

Time fields are split:

```text
started_at
finished_at
evidence_copied_at
```

Error fields preserve both taxonomies:

```text
provider_error_code
provider_error_message
indbase_error_code
primary_error_code
provider_error_payload_ref
```

Unknown provider errors map to `provider_unknown_error` and must keep bounded
raw provider error context. Known provider errors must not fall into unknown in
contract tests.

The table stores summary and artifact refs only:

```text
evidence_root
manifest_artifact_ref
trace_artifact_ref
warning_count
error_count
input_sha256
content_sha256
output_count
```

Complete manifests, traces, reports, and provider outputs live under the
provider evidence root.

## Evidence Roots And Views

Each provider attempt owns one evidence root:

```text
.indbase/artifacts/provider_runs/<provider_run_id>/
```

`ingest_runs` and `output_runs` may store decision summaries, but they do not
own provider attempt evidence. They point at the adopted provider attempt.

External artifact views are summaries:

```text
indbase://provider_runs/<provider_run_id>
indbase://provider_runs/<provider_run_id>/evidence
```

File-level evidence URIs such as
`indbase://provider_runs/<id>/files/manifest.json` are not part of the consoler
artifact block surface.

## Adopted Runs And Existing Tables

Ingest/output state records use `adopted_provider_run_id` when they depend on a
provider attempt:

```text
ingest_runs.adopted_provider_run_id
converter_runs.adopted_provider_run_id
output_runs.adopted_provider_run_id
```

`converter_runs` remains in place for v0.3.4. Legacy fields such as
`external_job_id`, `external_trace_path`, and `external_manifest_path` may be
written for compatibility, but new provider logic depends on `provider_runs`.

Review items store both ingest workflow context and evidence attempt context:

```text
ingest_run_id
provider_run_id
```

`errors` has an explicit `provider_run_id`. `task_events.metadata_json` carries
provider correlation metadata. `tasks` does not store a single provider run
because one task may have multiple attempts.

## Output Artifact Roles

Normalize results default to derived output artifacts, not source revisions.
Only explicit replace-current creates a new immutable source revision.

`output_artifacts` carries both purpose and trust:

```text
artifact_role = normalized_candidate | export_output | diff | report
trust_level   = derived_candidate | derived_output | evidence
```

Provider evidence files are not ordinary `output_artifacts` by default. They
remain in the provider evidence index and provider evidence view.

## Default Provider Bindings

Default active bindings should express these implementation choices:

```text
indbase.ingest.file -> swallow.ingest.file via local_core
indbase.ingest.url -> swallow.ingest.url behind an explicit feature flag
indbase.output.normalize -> transition.markdown.normalize via node_bridge
indbase.output.export -> transition.markdown.export via node_bridge
```

The transition provider calls the capability that transition names
`transition.markdown.export`; indbase may still expose this as `output export`
because export is indbase product vocabulary.

Default bindings live in the repository/package `capability-bindings.yaml`.
Vault-local config may override a binding for a vault, but only with an
explicit administrative config change and only to another compatible provider
contract/profile. Normal product commands continue to resolve through indbase
services and do not accept provider backend selection.

The root `capability-bindings.yaml` is the reviewable repository default.
Packaged installs must also provide an internal fallback default.

## Validation

- Contract tests validate manifests, artifact refs, provider version/profile
  recording, and error mapping.
- Fake provider gates validate success, partial, failed, and cancelled behavior
  without real swallow or transition.
- Search pollution tests prove rejected candidates, exports, and copied
  provider evidence are not indexed in default source search.
- Real provider smoke tests are environment-gated and optional by default.
- Real smoke skips explicitly when runtime dependencies are missing by default.
  `INDBASE_PROVIDER_SMOKE_REQUIRED=1` upgrades missing runtime to failure.
