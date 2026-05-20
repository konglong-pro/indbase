# Swallow Ingest Integration Agent Guide

Read this before implementing the v0.2 swallow-backed ingest integration.

Canonical project spec:

- `docs/planning/v0.2-swallow-ingest-integration.md`

Baseline context:

- `AGENTS.md`
- `docs/planning/v0.2-entry-plan.md`
- `docs/planning/mvp-v0.1-spec.md`

## Mission

Replace indbase's format-specific conversion layer with swallow-backed conversion while preserving indbase's trust boundary.

Short version:

```text
swallow owns conversion
indbase owns trust
candidate != revision
default search = promoted current revisions only
```

## Non-negotiables

- Do not let swallow write canonical indbase revisions.
- Do not use swallow's HTTP service for indbase ingest.
- Do not replace `.indbase/originals` with swallow raw storage.
- Do not treat swallow success as indbase ingest success.
- Do not index review candidates in default search.
- Do not archive browser cookies, profiles, or session storage.
- Do not enable Firecrawl or any external SaaS provider without explicit opt-in.
- Do not reintroduce old direct-normalizer or MarkItDown fallback conversion.
- Do not implement ask, answer generation, or model-backed synthesis as part of this work.

## Architecture Boundary

indbase keeps ownership of:

- source scanning
- duplicate detection
- ingest runs and ingest items
- original archive
- source snapshots
- documents and source files
- converter run records
- promotion decisions
- immutable revisions
- chunking
- FTS/vector indexing
- tasks, errors, and reviews
- doctor checks

swallow owns:

- file conversion
- PDF text extraction
- OCR execution
- ASR execution
- URL capture
- Playwright capture
- browser capture parsing
- archive extraction
- worker routing
- worker trace and manifest output

## Implementation Order

1. Add schema migration for provenance, candidates, artifacts, promotion, source locators, privacy flags, source snapshots, and parent/child ingest items.
2. Add indbase boundary models: `ConversionCandidate`, `PromotionDecision`, `SourceLocator`, `ArtifactManifest`, and `SwallowProvenance`.
3. Add optional pinned swallow dependency and feature/config parsing.
4. Add the local swallow adapter. Use in-process local SDK or core runner only.
5. Map swallow results to conversion candidates without using swallow frontmatter.
6. Copy required evidence artifacts into `.indbase/artifacts`.
7. Add the promotion gate.
8. Route `convert_archived_sources()` through the swallow adapter.
9. Add candidate review accept/reject promotion.
10. Add source locator support to chunking and search rendering.
11. Extend doctor with lightweight swallow checks.
12. Add deep doctor checks for heavy runtimes.
13. Convert tests to trust-behavior assertions.
14. Retire old normalizers from the main path. Done for production ingest.
15. Delete remaining legacy fixture code only after tests construct swallow candidates directly.

## Required Decisions Already Made

- This work is v0.2 ingest expansion.
- Integration uses optional pinned Python dependency.
- Runtime uses local SDK/Core adapter.
- HTTP service is out of scope.
- swallow raw store is cache/provenance only.
- indbase archived originals are authoritative.
- URL and browser captures must have local durable snapshots.
- OCR and ASR may become searchable when they pass promotion gates.
- Review candidates are not revisions.
- Required artifacts must be copied into indbase vault.
- External SaaS providers are explicit opt-in.
- Folder ingest remains indbase-orchestrated.
- Old indbase direct/MarkItDown conversion is retired from the production ingest path.

## Promotion Policy

Use `indbase_core.promotion_policy` as the only promotion decision implementation. Do not add separate promotion branches in conversion, archive, URL, OCR, or ASR code.

Auto-promote only when all are true:

- swallow status is success
- quality score is at or above configured auto-current threshold
- Markdown body is non-empty and long enough
- local original or snapshot exists
- required artifacts are archived
- worker chain and trace provenance exist
- source type is enabled by feature flag
- no fatal warning is present
- external provider usage is explicitly enabled if present

Review instead of promote when:

- swallow status is partial and `allow_partial_auto_current=false`
- quality is between review and auto-current thresholds
- OCR/ASR lacks precise locators
- ASR lacks timestamp locators
- web, browser, or archive extraction lacks source locators
- required provenance is incomplete but recoverable
- fallback worker chain or low-confidence warning needs human judgment

Fail when:

- swallow fails
- Markdown is empty
- quality is below hard threshold
- source snapshot is missing
- security or sandbox validation fails
- dependency is missing for an enabled source type
- external provider is disabled
- feature flag for the source mode is disabled
- worker status is failed, error, cancelled, timeout, or unknown

Partial output may auto-promote only when `allow_partial_auto_current=true` and the candidate still passes every normal gate: quality, durable artifacts, locators, trace, worker chain, feature flags, and provider policy.

## Legacy Conversion Retirement

Production ingest must not call `indbase_core.normalizers` from `convert_archived_sources()`.

Rules:

- If `features.swallow_ingest=false`, conversion fails visibly with `legacy_conversion_retired`.
- The failure must write `errors`, `review_items`, `converter_runs`, and failed ingest/document state.
- `converter_runs.converter_name` for normal conversion is `swallow`, not `direct_normalizer`, `html_fallback_normalizer`, or `markitdown`.
- Doctor should validate swallow conversion failures, not MarkItDown availability.
- `normalizers.py` may remain only as a temporary test baseline until all legacy fixture tests are rewritten.
- Tests may use `tests/conftest.py` fake swallow conversion to keep existing file-ingest workflows deterministic, but production code must not call that fixture or `normalizers.py`.

## Artifact Rules

Required evidence artifacts must be durable inside indbase vault:

- swallow manifest
- swallow trace summary or trace file
- swallow ingest document JSON
- rendered or captured HTML
- primary screenshot for browser captures
- OCR result JSON
- ASR transcript JSON
- browser capture JSON
- archive extraction map

Diagnostic artifacts may stay in swallow cache unless they are needed to explain a chunk locator.

Real artifact policy:

- Trusted-current source evidence must be copied into `.indbase/artifacts/...`.
- Snapshot paths and locator artifact fields must not point at swallow cache, absolute paths, temp paths, or parent-directory escapes.
- Locator `source_path` must point at the archived original under `.indbase/originals/...`.
- Missing real evidence means review-before-current or failure, not automatic searchable.
- `candidate_path` may stay in converter cache while pending review.

## Source Locator Rules

Add and use `chunks.source_locator_json`.

Expected locator kinds:

- `page`
- `page_region`
- `time_range`
- `web_snapshot`
- `browser_capture`
- `archive_member`

Search results must still bind to `doc_id`, `revision_id`, and `chunk_id`.

## Test Expectations

Do not assert exact swallow Markdown formatting unless the adapter contract requires it. Assert indbase behavior:

- durable original or snapshot
- mapped converter run
- correct promotion decision
- no default search row for candidates
- immutable revision after acceptance
- current revision unchanged after rejection
- source locator on non-plain-text chunks
- required artifact checks
- visible errors and review items
- doctor findings for broken states

Run the narrowest relevant tests first, then the ingest/search/doctor gates affected by the change.

When tests need converted file content, prefer explicit `ConversionCandidate` or fake `SwallowIngestAdapter` fixtures. Do not add new production fallback code to satisfy old direct-normalizer expectations.

## Completion Report

Every implementation turn should report:

- changed files
- schema changes
- source types covered
- tests run
- tests not run
- remaining risks
- whether any legacy test fixture code still depends on `normalizers.py`
