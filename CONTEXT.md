# indbase Context

indbase is a local-first personal knowledge database. This glossary captures project-specific domain language so planning and implementation use the same terms.

## Language

**Big Category**:
A closed, curated top-level category used to place a document into exactly one broad catalog lane.
_Avoid_: folder, directory, ad hoc category, dynamic category

**Closed Catalog**:
The active user-curated set of **Big Categories** that automatic classification is allowed to choose from.
_Avoid_: generated catalog, open category set

**Default Big Category Template**:
The starting **Closed Catalog** offered by indbase before the user customizes category lanes.
_Avoid_: fixed taxonomy, hardcoded final catalog

**Category Identity**:
The stable language-independent identifier of a **Big Category**.
_Avoid_: display name, localized label

**Localized Category Label**:
A user-facing language-specific name for a **Big Category** that shares the same **Category Identity** across locales.
_Avoid_: separate category, translated category ID

**Language-Neutral Category Profile**:
A single classification boundary for a **Category Identity** that may include multilingual cues but does not fork behavior by display language.
_Avoid_: English classifier profile, Chinese classifier profile

**Category Localization**:
A localized display record for a **Category Identity**, separate from the classification profile.
_Avoid_: category clone, locale-specific identity

**Legacy Category Template**:
An old initialization template that is no longer offered for new vaults but may still exist in older vault data.
_Avoid_: deleted catalog, active default

**Category Profile**:
A user-curated boundary description that explains when a **Big Category** should and should not be used.
_Avoid_: category label, category name only

**Classification Ready**:
A **Big Category** state meaning its **Category Profile** is complete enough for automatic classification.
_Avoid_: active category, visible category

**Inactive for Classification**:
A **Big Category** state that preserves existing manual or accepted assignments while preventing future automatic assignments.
_Avoid_: deleted category, archived category

**Uncategorized**:
The explicit fallback category for a document that has not been confidently assigned to a **Big Category**.
_Avoid_: unknown, miscellaneous

**Needs Review**:
A state for a document whose category evidence is insufficient or conflicting and therefore requires user decision before trusted classification.
_Avoid_: failed classification, guessed category

**Confident Assignment**:
A trusted automatic assignment to a **Big Category** when evidence is strong enough to avoid user review.
_Avoid_: guess, best effort assignment

**Manual Assignment**:
A user-made **Big Category** assignment that represents an explicit human decision.
_Avoid_: override candidate, temporary category

**Accepted Suggestion**:
A classifier suggestion that the user has explicitly approved and promoted into document metadata.
_Avoid_: automatic assignment

**Category Suggestion**:
A proposed **Big Category** assignment that is not trusted metadata until accepted or superseded.
_Avoid_: pending category, weak assignment

**Category Fixture Suite**:
A versioned set of documents with expected category outcomes used to verify **Confident Assignment** and **Abstain** behavior.
_Avoid_: sample docs, demo corpus

**Stale Assignment**:
An automatic category assignment whose supporting category profile or source revision has changed enough to require re-evaluation.
_Avoid_: wrong category, deleted assignment

**Category Classifier**:
The deterministic decision layer that turns **Category Evidence** into **Confident Assignment**, **Category Suggestion**, or **Abstain**.
_Avoid_: LLM classifier, model decision

**Category Harness**:
The model-facing or fake-provider layer that can propose structured category candidates for deterministic validation.
_Avoid_: direct classifier, metadata writer

**Classification Status**:
The user-visible state of taxonomy processing for a document, separate from the document's **Big Category** value.
_Avoid_: category, review type

**Catalog Management**:
The user workflow for curating the **Closed Catalog** and **Category Profiles**.
_Avoid_: classifier run, taxonomy inference

**Classification Run**:
The workflow that evaluates documents against the **Closed Catalog** and records assignments, suggestions, or abstentions.
_Avoid_: catalog edit, ingest conversion

**Classification Result**:
The per-document outcome recorded by a **Classification Run**, including evidence, confidence, margin, and final action.
_Avoid_: document category field, transient score

**Category Feedback**:
A user correction or decision that can inform future category profiles, fixtures, or classifier evaluation.
_Avoid_: hidden training signal, automatic rule rewrite

**Legacy Classification Record**:
An older broad classification table or row retained for compatibility but not used as the core category foundation record.
_Avoid_: category foundation result, trusted run result

**Tag Governance**:
The separate workflow for controlling formal tags, candidate tags, aliases, merges, deprecations, and tag volume.
_Avoid_: big category classification

**Formal Tag**:
A user-approved tag that is allowed to appear in document metadata, tag filters, and trusted search metadata.
_Avoid_: candidate tag, raw model tag, unreviewed tag

**Manual Tag Creation**:
A user-initiated creation of a **Formal Tag** that may proceed despite policy warnings because the user is the authority over their tag vocabulary.
_Avoid_: automatic formal tag creation

**Canonical Tag**:
The active **Formal Tag** that represents a normalized concept after alias or merge resolution.
_Avoid_: alias, duplicate tag, display spelling

**Deprecated Tag**:
A **Formal Tag** that remains attached to historical documents but should not be newly suggested or attached automatically.
_Avoid_: deleted tag, active tag

**Merged Tag**:
A **Formal Tag** that has been redirected into a **Canonical Tag** because it duplicates or overlaps another tag.
_Avoid_: alias only, archived tag

**Archived Tag**:
A **Formal Tag** hidden from normal lists, filters, and automatic tagging without deleting historical records.
_Avoid_: physically deleted tag

**Tag Scope**:
The applicability boundary of a **Formal Tag**, initially either global or limited to one **Big Category**.
_Avoid_: project namespace, duplicate tag name

**Multilingual Tag Alias**:
A language-specific alias that resolves to the same **Canonical Tag** rather than creating a separate tag identity.
_Avoid_: translated duplicate tag, locale-specific tag identity

**Candidate Tag**:
A proposed tag from an agent, harness, or review workflow that has not yet been promoted into trusted document metadata.
_Avoid_: formal tag, applied tag, accepted tag

**Tag Promotion**:
The explicit workflow that turns a **Candidate Tag** into a **Formal Tag** or links it to an existing **Formal Tag**.
_Avoid_: automatic tag creation, hidden training

**Tag Propagation**:
A separate explicit workflow that proposes or applies a **Formal Tag** to additional documents based on prior feedback.
_Avoid_: implicit batch tagging, hidden propagation

**Tag Link Migration**:
An explicit audited workflow that rewrites existing document-tag links after a merge or cleanup decision.
_Avoid_: silent merge rewrite, implicit historical edit

**Tag Resolution**:
The deterministic step that maps a raw tag candidate to a **Canonical Tag**, a blocked/deprecated outcome, or a new-tag proposal before it can enter review.
_Avoid_: raw tag storage, direct string promotion

**Tag Admission Policy**:
The deterministic rules that decide whether a resolved new tag proposal may enter review, should be blocked, or should be mapped to existing metadata.
_Avoid_: accept every candidate, prompt-only filtering

**Tag Blocklist**:
A user-reviewable governance list that blocks low-value raw tag candidates from entering the normal candidate or promotion path.
_Avoid_: hardcoded blacklist, deleted tag

**Attach-Existing Candidate**:
A **Candidate Tag** that proposes attaching an existing **Canonical Tag** to a document.
_Avoid_: new tag proposal

**New-Tag Proposal**:
A **Candidate Tag** that proposes creating a new **Formal Tag** after passing resolution, budget, and review checks.
_Avoid_: auto-created formal tag

**Tag Volume Budget**:
A governance limit that controls how many tags may be suggested, promoted, or attached so the tag system does not fragment into overly specific labels.
_Avoid_: unlimited tagging, tag sprawl

**Explainable Tag Quantity Control**:
The tag governance behavior that reports why candidate volume, new-tag proposal volume, near-budget pressure, or sprawl blocking occurred instead of exposing only a pass/fail budget number.
_Avoid_: opaque budget, hidden throttling, unexplained tag sprawl

**Tag Feedback**:
An explicit user decision about a tag candidate, tag attachment, merge, alias, deprecation, removal, or policy suggestion.
_Avoid_: hidden training signal, implicit preference

**Tag Governance Event**:
An audit record for tag alias, merge, deprecation, archive, scope, promotion, blocklist, or policy changes.
_Avoid_: silent tag mutation, untracked rename

**Tag Harness**:
The evaluation workflow that runs tag policy and candidate generation against fixtures and feedback-derived cases.
_Avoid_: production tagger, hidden model tuning

**Tag Harness Hardening**:
The v0.3.2.1 phase that makes tag governance measurable, regression-testable, feedback-aware, and hard to regress without overwriting the existing v0.3.3 retrieval evaluation phase.
_Avoid_: v0.3.3 tag phase, automatic policy mutation, broad tag/search redesign

**Tag Harness Case**:
The smallest reproducible tag evaluation example, usually one document, snippet, or feedback-derived scenario with expected resolution, candidate, attachment, or block behavior.
_Avoid_: production document, trusted metadata

**Tag Harness Run**:
An auditable evaluation execution that aggregates tag harness cases across candidate, document, and run-level metrics.
_Avoid_: ingest run, hidden training pass

**Tag Harness Eval Vault**:
An isolated temporary or fixture vault created for tag harness execution so evaluation can seed documents, tags, candidates, and search indexes without touching a user's production vault.
_Avoid_: production vault, shared user state, implicit migration target

**Tag Harness Seed Document**:
A trusted current-revision document seeded into a **Tag Harness Eval Vault** with chunks and FTS state so tag governance can be evaluated without invoking real ingest conversion.
_Avoid_: swallow conversion test, transition export test, OCR fixture

**Tag Harness Fixture Suite**:
A stable collection of hand-written, feedback-derived, and approved dogfood cases used to evaluate tag governance behavior.
_Avoid_: live production vault, unreviewed feedback stream

**Tag Harness Case Schema**:
The explicit expected-outcome contract for a tag harness case, including seed tags, manual tags, raw candidates, expected auto-attached tags, expected candidates, expected blocked candidates, expected search hits, and expected warnings.
_Avoid_: inferred expectation, free-text-only case, case-name convention

**Feedback-Derived Tag Harness Case**:
A **Tag Harness Case** explicitly exported from reviewed **Tag Feedback** or **Tag Governance Events** so it can become a stable regression fixture.
_Avoid_: implicit learning, unreviewed feedback, live mutation

**Sanitized Tag Harness Fixture**:
A synthetic or redacted tag harness fixture that preserves governance behavior while removing private document content before it is committed to the repository.
_Avoid_: raw production document, private vault excerpt, unsanitized feedback export

**Tag Feedback Regression Loop**:
The explicit loop that turns reviewed tag feedback or governance events into stable harness cases and verifies that future tag behavior does not regress.
_Avoid_: automatic training, hidden policy update, production-state drift

**Tag Harness Coverage Matrix**:
The minimum fixture coverage for v0.3.2.1, spanning safe canonical auto-attachment, alias resolution, lifecycle ineligibility, blocklist/sprawl rejection, manual tag protection, scope boundaries, search-filter non-pollution, and feedback-derived regression.
_Avoid_: broad corpus wish list, unscoped quality benchmark

**Tag Harness Hard Gate**:
A release-blocking tag harness metric where dangerous governance violations must be zero.
_Avoid_: broad quality score, recall target, advisory warning

**Tag Harness Summary**:
A stable machine-readable evaluation summary, usually JSON, that release gates and CI can read without parsing human output.
_Avoid_: Rich table, ad hoc console text, production vault record

**Tag Harness Artifact**:
A local diagnostic artifact, such as Markdown or JSONL, that explains failed cases, inputs, expectations, actual outcomes, and governance reasons.
_Avoid_: required production metadata, trusted tag state

**Tag Harness Failure Record**:
A stable case-level failure object that includes case identity, evaluation layer, severity, reason code, expected outcome, actual outcome, and governance reasons.
_Avoid_: prose-only failure, traceback as contract

**Minimal Tag Harness Reproduction**:
The smallest case-level failure detail needed to reproduce a tag harness regression, including the case ID, raw candidate, expected outcome, actual outcome, and reason code.
_Avoid_: aggregate-only metric, vague failure summary

**Tag Harness Reason Code**:
A stable machine-readable code for tag harness failures, such as wrong auto-attachment, manual tag mutation, trusted filter pollution, budget violation, lifecycle-ineligible auto-attachment, unresolved candidate persistence, or harness policy mutation.
_Avoid_: localized message, free-form explanation

**Tag Harness Release Gate Script**:
The v0.3.2.1 executable verification entry point for CI and release checks before any formal user-facing tag harness CLI is promised.
_Avoid_: stable user command, consoler UI command, interactive workflow

**Tag Harness CI Gate**:
The pull-request CI job that runs the v0.3.2.1 tag harness release gate after tag governance foundation checks and before retrieval evaluation checks.
_Avoid_: local-only script, post-release manual check

**Tag Harness Validation Suite**:
The required command set for validating v0.3.2.1, including focused tag governance tests, focused tag harness tests, the v0.3.2 governance gate, the v0.3.2.1 harness gate, full pytest, compileall, and the CI harness job.
_Avoid_: single smoke test, unchecked local script, undocumented manual check

**Deterministic Tag Harness**:
A local, repeatable v0.3.2.1 harness that evaluates governed tag behavior without real providers, embeddings, API keys, network calls, or nondeterministic model output.
_Avoid_: provider-backed tagger, embedding evaluation, network-dependent gate

**Pure Tag Harness Evaluation Layer**:
The v0.3.2.1 implementation boundary where the harness orchestrates fixture loading, eval-vault seeding, existing tag governance/search services, metric aggregation, summaries, and artifacts without redefining core tag semantics.
_Avoid_: replacement tagger, alternate tag governance engine, hidden policy fork

**Fixture-First Tag Harness Implementation**:
The v0.3.2.1 implementation order where explicit fixture schemas and failing harness tests are written before the harness runner and release gate.
_Avoid_: runner-first implementation, cherry-picked passing fixtures

**Exact Tag Harness Expectation Match**:
The rule that hard-gate harness cases must match explicit expected auto-attachments, candidates, blocked outcomes, search hits, warnings, and failure reasons case by case.
_Avoid_: aggregate threshold hiding case failure, best-effort fixture interpretation

**Report-Only Tag Harness Metric**:
A non-blocking v0.3.2.1 metric, such as candidate precision or recall on a small fixture corpus, that informs future policy work without failing release by itself.
_Avoid_: premature global threshold, false stability score

**Schema-Neutral Tag Harness**:
A v0.3.2.1 constraint that tag harness evaluation should use fixture suites, eval vaults, test helpers, and artifacts before adding production vault schema.
_Avoid_: production migration by default, long-term user vault run history

**Tag Harness Search Check**:
A tag harness validation that proves trusted tag filters resolve through formal tag relationships and do not leak candidate, raw, deprecated, archived, or merged tag state.
_Avoid_: ranking evaluation, hybrid search, query expansion, ask

**Wrong Auto-Attached Tag**:
An **Auto-Attached Tag** that the harness expectation says should not have been applied to the document.
_Avoid_: missing candidate, low-confidence suggestion

**Manual Tag Mutation**:
Any automatic or harness-driven change that deletes, overwrites, or silently replaces a manually applied tag.
_Avoid_: reviewable suggestion, explicit user removal

**Trusted Filter Pollution**:
A case where a trusted **Tag Search Filter** returns documents or chunks through candidate, raw, deprecated, archived, merged, or otherwise untrusted tag state.
_Avoid_: broad full-text match, candidate preview

**Tagger**:
The deterministic local workflow that proposes or attaches tags using current-revision evidence, formal tag metadata, aliases, and approved tag policy.
_Avoid_: real provider, hidden LLM call

**Post-Ingest Tagging Stage**:
An optional feature-flagged tag workflow that runs after trusted revision/chunk/search creation and remains bounded by tag policy and volume budgets.
_Avoid_: default ingest tagging, conversion stage

**Tag Governance Foundation**:
The v0.3.2 phase that makes tags trustworthy through formal tags, candidate tags, resolution, admission, budgets, feedback, harness evaluation, and tag search filters.
_Avoid_: retrieval intelligence, ask, ontology

**Legacy Classification Tag Suggestion**:
An older tag suggestion embedded in a broad classification suggestion record, kept for compatibility but not used as the core tag governance state.
_Avoid_: tag governance candidate, formal tag

**Consoler-Facing Command Output**:
Stable JSON command output designed so future consoler UI can call core workflows without parsing human CLI tables.
_Avoid_: TUI implementation, Rich table contract, UI business logic

**Artifact-First Read-Only View**:
A bounded, agent-owned consoler artifact view for trusted indbase objects that are already exposed by read-only Source Trust Loop commands, without adding mutation workflows, vault browsing, or direct consoler access to vault files or database state.
_Avoid_: command-screen contract, full TUI page, vault browser, direct DB reader

**Read-Only View Object Set**:
The first bounded consoler view set for Source Trust Loop dogfood, covering document, review item, task, error, and doctor report objects before broader ingest-run, converter-run, output-artifact, or revision browsing.
_Avoid_: full history browser, conversion pipeline browser, generated artifact gallery

**Current-State Artifact View**:
A read-only artifact view whose URI identifies an indbase object and whose body is resolved from the current vault state when opened, with immutable source bindings preserved where the originating command supplied them.
_Avoid_: durable action snapshot, consoler-owned business cache, replayed historical UI state

**Opaque Indbase Artifact URI**:
An `indbase://...` identifier that names an agent-owned indbase object or view capability without exposing local file paths, SQL selectors, title/path lookup, globbing, or direct vault access as the lookup authority.
_Avoid_: file URI, vault path as capability, query language in artifact URI

**Ephemeral Doctor Report View**:
A read-only consoler artifact view that resolves the current vault health on demand without adding durable doctor-run storage or repair behavior.
_Avoid_: doctor history table, repair workflow, persisted diagnostic ledger

**Bounded View Budget**:
The fixed first-version size limits for read-only artifact views, reported in each view response through `limits` and `truncated` fields instead of user-configurable display settings.
_Avoid_: full source viewer, unlimited trace viewer, vault browser

**Show-Command Artifact Boundary**:
The first-version rule that list commands return bounded rows only, while show commands and primary source-search or doctor commands expose read-only artifact blocks for focused object inspection.
_Avoid_: artifact spam, list-as-browser, row-level artifact flooding

**Read-Only View Contract Test Boundary**:
The verification boundary where indbase agent artifact contracts carry the release risk, while consoler discovery, command run, and artifact-view calls remain smoke/conformance checks unless protocol or renderer code changes.
_Avoid_: renderer-driven acceptance, protocol rewrite, UI snapshot as source of truth

**Agent Projection Layer**:
The `indbase_agent` boundary that turns core read-only data into consoler command results, artifact blocks, artifact URI handling, bounded view envelopes, and display metadata without importing consoler concepts into `indbase_core`.
_Avoid_: consoler SDK in core, UI contract in service layer, direct DB access from consoler

**Artifact View Error Semantics**:
The stable failure contract for read-only artifact views where invalid URI shape, unsupported artifact kind, missing object, scope rejection, uninitialized vault, and generation failure return explicit errors instead of empty successful views.
_Avoid_: empty success for errors, ambiguous not found, silent scope bypass

**Tag Policy Suggestion**:
A reviewable proposal to change tag aliases, merges, deprecations, blocklists, scopes, or admission policy.
_Avoid_: automatic policy change, hidden learning

**Tag Doctor Finding**:
A health finding about tag resolution, tag search correctness, candidate integrity, audit integrity, or tag sprawl risk.
_Avoid_: automatic tag fix, hidden merge

**Tag Harness Doctor Boundary**:
The responsibility split where the tag harness proves expected behavior against fixtures and release gates, while doctor checks real vault health without running the full harness suite.
_Avoid_: doctor as regression harness, harness as vault repair tool

**Tag Harness Completion Boundary**:
The v0.3.2.1 acceptance boundary that proves governed tag behavior is measurable and regression-testable without claiming broader tag/search governance is complete.
_Avoid_: tag/search governance complete, ranking readiness, ask readiness

**Tag Harness Non-Goals**:
The explicit out-of-scope boundary for v0.3.2.1, excluding formal user CLI commitments, production schema expansion by default, provider-backed intelligence, broader search improvements, automatic policy mutation, private fixture leakage, ingest conversion validation, and tag-governance semantic rewrites.
_Avoid_: hidden scope creep, bundled search phase, bundled ask phase

**User-Curated Tag State**:
The active formal tag, alias, lifecycle, scope, and policy metadata after a user has added, edited, deprecated, merged, archived, or scoped tags.
_Avoid_: default seed tags only, fixture-only catalog

**Auto-Attached Tag**:
An existing active **Formal Tag** attached by an automatic workflow only after passing evidence, confidence, lifecycle, and volume-budget checks.
_Avoid_: candidate tag, raw suggestion, manual tag

**Category Search Filter**:
A search constraint that returns only documents assigned to a selected **Big Category**.
_Avoid_: tag filter, full-text category mention

**Tag Search Filter**:
A relationship-backed search constraint that returns only documents or chunks attached to a resolved **Formal Tag**.
_Avoid_: full-text tag mention, candidate tag match, raw FTS tag string

**FTS Tag Metadata Non-Authority**:
The rule that FTS tag metadata text may support display or broad full-text search, but cannot be used as the authority for trusted tag filtering.
_Avoid_: metadata-string filter, raw tag search as trusted filter

**Trusted Document Tag Source**:
A document-tag relationship source that may participate in trusted tag filtering: manual, accepted candidate, auto, or legacy classification.
_Avoid_: pending candidate, rejected candidate, blocked candidate, raw metadata

**Tag/Search Governance**:
The v0.3.2.2 phase that governs combined tag and search behavior after tag governance foundation and tag harness hardening, without replacing retrieval evaluation or implementing ask.
_Avoid_: retrieval evaluation, ask readiness, broad retrieval rewrite

**Tag/Search Governance Harness**:
The v0.3.2.2 evaluation workflow that validates governed source search paths, filter semantics, non-pollution, explanations, and deterministic ordering.
_Avoid_: tag governance harness, retrieval evaluation harness, ask evaluation

**Sanitized Tag/Search Fixture**:
A synthetic or redacted source-search fixture that preserves tag/search governance behavior while removing private document content before it is committed to the repository.
_Avoid_: raw vault excerpt, private exact quote, unsanitized dogfood case

**Tag/Search Governance Gate**:
The release-blocking verification that runs the **Tag/Search Governance Harness** before broader retrieval or ask work depends on governed tag/search behavior.
_Avoid_: local-only smoke, retrieval eval gate

**Tag/Search Governance CI Gate**:
The pull-request CI job that runs the v0.3.2.2 tag/search governance release gate after the v0.3.2.1 tag harness gate and before retrieval gates depend on governed search behavior.
_Avoid_: local-only search smoke, unordered CI gate

**Source Search Hard Gate**:
A zero-tolerance v0.3.2.2 release check proving exact source text search still returns trusted current source chunks with stable source bindings.
_Avoid_: broad retrieval score, semantic recall metric

**Search Source Safety Gate**:
A zero-tolerance v0.3.2.2 release check proving governed source search returns only active documents, current trusted source revisions, current chunks, and trusted document-tag/category state.
_Avoid_: archived document leakage, old revision leakage, output artifact search, candidate source shell search

**Schema-Neutral Search Governance**:
A v0.3.2.2 constraint that governed tag/search behavior should be implemented through existing source, category, tag, document-tag, and chunk state before adding production vault schema.
_Avoid_: search audit migration by default, persisted query history

**Deterministic Search Governance**:
A local, repeatable v0.3.2.2 constraint where hard gates use deterministic source search behavior and do not depend on embeddings, providers, vector quality, or hybrid ranking changes.
_Avoid_: embedding-backed gate, provider-dependent search, hybrid ranking redesign

**Tag/Search Governance Validation Suite**:
The required command set for validating v0.3.2.2, including focused search/tag/harness tests, v0.3.2/v0.3.2.1/v0.3.2.2 gates, full pytest, compileall, and retrieval-eval regression when retrieval behavior is touched.
_Avoid_: single smoke test, unchecked CLI demo

**Tag/Search Governance Non-Goals**:
The explicit v0.3.2.2 out-of-scope boundary excluding ask, retrieval package/ranking changes, providers, embeddings, vector/hybrid ranking redesign, production schema by default, parallel search commands, UI, doctor repair, OR/semantic expansion, and untrusted tag-string filtering.
_Avoid_: hidden ask phase, retrieval rewrite, search engine rewrite

**Governed Search Path**:
A supported search route whose filter semantics, source binding, match explanation, and non-pollution rules are explicit and regression-tested.
_Avoid_: best-effort query behavior, hidden retrieval logic, ask

**Source Search**:
Search that returns trusted source snippets tied to current source chunks rather than generated answers, retrieval packages, or derived artifacts.
_Avoid_: retrieve, ask, summary search, output artifact search

**Source Trust Loop**:
The dogfood workflow that proves a local source can move from ingest through visible task/error/review state, trusted category/tag metadata, governed source search, source binding inspection, and doctor verification without confusing candidates, generated artifacts, or answers with trusted current source revisions.
_Avoid_: full product UI, generated output loop, ask workflow, unbounded vault browser

**Consoler Variant Dogfood UX**:
The consoler-owned product experience layer that makes the existing indbase Source Trust Loop usable through a configured console variant without making natural-language drafting the primary path or adding indbase core capabilities, new mutations, generated answers, or vault browsing.
_Avoid_: indbase core phase, Web UI, protocol expansion, full product UI, chat-first workflow

**Indbase Variant Intent Drafting**:
The consoler-owned deterministic form-prefill layer that maps one natural-language request inside the indbase console variant to one reviewable Source Trust Loop action. It does not send natural language to indbase, infer vault state, execute actions, add indbase commands, mutate indbase core, or become chat/LLM workflow.
_Avoid_: indbase NL parser, direct execution, multi-action workflow, vault inference, ask

**Single-Source Trust Walkthrough**:
The first dogfood UX path through the consoler variant, focused on one vault and one source moving through health check, ingest or prepared fixture state, governed search, document artifact inspection, review/task/error inspection, and traceability.
_Avoid_: all-command dashboard, multi-vault browser, generated answer workflow

**Variant Vault Context**:
A consoler variant convenience value that remembers the last successful vault path for form prefill without scanning disks, discovering vaults, managing vault lists, or becoming indbase business state.
_Avoid_: vault manager, vault browser, auto-discovery, indbase configuration

**Composed Variant Surface**:
A dogfood UX approach that combines existing consoler home, form, timeline, result block, artifact view, history, and trace surfaces instead of introducing a separate wizard or variant-specific runtime state machine.
_Avoid_: Source Trust wizard, parallel lifecycle, custom workflow engine

**Artifact Open/Back Path**:
The minimal artifact-view UX where a user can discover an artifact block, open it explicitly, inspect a bounded current-state view, and return to the originating timeline or trace without creating an artifact browser or persisted view cache.
_Avoid_: artifact gallery, arbitrary URI fetch, stored artifact content

**Deterministic Variant Dogfood Smoke**:
A repeatable consoler variant validation that uses disposable synthetic indbase vault state and the real agent path to prove the product walkthrough without depending on private vaults, real swallow availability, or manual-only evidence.
_Avoid_: private-vault gate, manual-only smoke, environment-specific success

**Source Trust Loop Action Surface**:
The ten-command consoler variant action set that exposes vault health, single-file ingest, governed source search, document inspection, review inspection, task inspection, and error inspection while keeping only ingest as a write action.
_Avoid_: category/tag mutation surface, retrieval/ask surface, all-command admin console

**Tag/Text Search**:
A governed search path that combines a resolved **Tag Search Filter** with a full-text query over trusted current source chunks.
_Avoid_: raw tag-string FTS match, candidate tag search, semantic retrieval

**Search Filter Model**:
The normalized internal representation of structured category/tag filters parsed from CLI flags or query prefixes before source search runs.
_Avoid_: separate CLI semantics, string-only parser state

**Search Query Prefix Filter**:
A shortcut filter syntax inside a query string, such as `tag:<ref>` or `category:<ref>`, that must normalize into the same **Search Filter Model** as structured CLI flags.
_Avoid_: independent filter logic, hidden OR syntax

**Category/Tag Search**:
A governed search path that combines a **Category Search Filter** with a resolved **Tag Search Filter** over trusted current source documents.
_Avoid_: multiple big categories, raw metadata match, broad retrieval package

**Category/Tag Intersection Gate**:
A v0.3.2.2 hard gate proving category and tag filters use AND semantics, return only documents satisfying both filters, and treat valid no-intersection searches as successful empty results.
_Avoid_: category-only fallback, tag-only fallback, invalid-filter conflation

**Conjunctive Search Filters**:
The rule that category, tag, and text constraints combine with explicit AND semantics in governed source search.
_Avoid_: implicit OR, hidden query expansion, similar-tag expansion

**Filter-Only Source Search**:
A governed source search with category and/or tag filters but no text query, returning representative current source snippets while clearly reporting the active filters.
_Avoid_: unbounded browse, candidate search, generated summary

**Representative Source Snippet**:
A bounded snippet selected from a trusted current source document when a filter-only search has no exact text match to anchor the result.
_Avoid_: all chunks, generated summary, arbitrary document preview

**Deterministic Search Ordering**:
The governed rule that source search results must be stable for the same vault state and query without introducing new semantic reranking.
_Avoid_: ranking rewrite, provider score, nondeterministic ordering

**Search Match Explanation**:
A structured explanation of why a search result matched, including applied filters, resolved tag/category identities, text match source, chunk binding, and warnings.
_Avoid_: opaque ranking score, generated answer, unsupported inference

**Search Explanation JSON Contract**:
The stable machine-readable search result explanation used by `--json`, including applied filters, resolved tag/category identities, text and filter match details, chunk/document/revision bindings, and warnings.
_Avoid_: human table parsing, localized prose contract

**Search JSON Contract**:
The stable machine-readable response shape for governed `indb search --json`, including normalized query, applied filters, filter errors, result count, source bindings, snippets, explanations, and warnings.
_Avoid_: Rich table parsing, consoler-specific UI state, generated answer format

**Governed Search CLI Surface**:
The v0.3.2.2 command boundary that extends existing `indb search` with governed category/tag filters and JSON output without adding parallel search commands.
_Avoid_: tag-search command, browse command, retrieve rewrite

**Search Filter/Explanation Layer**:
The implementation boundary that may normalize filters, validate filter conflicts, and build structured explanations without rewriting chunk scoring, chunking, indexing, or retrieval ranking.
_Avoid_: search engine rewrite, FTS scoring redesign, retrieval package logic

**Tag/Search Doctor Finding**:
A health finding about governed source search, tag filter resolution, category/tag filter composition, FTS tag metadata consistency, or candidate/raw tag pollution.
_Avoid_: automatic repair, hidden rebuild, search ranking advice

**Tag Filter Lifecycle Semantics**:
The governed search rules for canonical, alias, merged, deprecated, and archived tag references in a **Tag Search Filter**.
_Avoid_: lifecycle-blind search, archived tag leakage

**Deprecated Tag Search Binding**:
A trusted search behavior where a deprecated tag reference may query existing document-tag bindings to that deprecated tag itself, with warnings, without expanding to canonical targets or migrating links.
_Avoid_: automatic link migration, deprecated-to-canonical expansion

**Invalid Search Filter**:
A category or tag filter reference that cannot participate in trusted source search because it is unknown, blocked, archived, candidate-only, or otherwise outside governed metadata.
_Avoid_: zero-result search, low-confidence match

**Search Filter Error**:
A parse or validation failure in a requested search filter, such as unknown filter reference, conflicting filters, malformed quoted prefix, candidate tag reference, or archived tag reference.
_Avoid_: execution error, empty result

**Search Execution Error**:
A failure to execute governed source search after filters were valid, such as database, FTS, index, or unexpected service failure.
_Avoid_: filter error, no matching document

**Empty Governed Search Result**:
A successful governed source search where valid filters and/or text constraints match no current trusted source snippets.
_Avoid_: invalid filter, execution error

**Taxonomy Execution Error**:
A failure to complete the classification workflow itself, distinct from an intentional **Abstain**.
_Avoid_: abstain, low confidence

**Abstain**:
The classifier's deliberate choice to leave a document **Uncategorized** or **Needs Review** instead of making a low-confidence assignment.
_Avoid_: failure, error

**Post-Ingest Taxonomy Stage**:
The classification step that runs after a source document has a trusted current revision and searchable chunks.
_Avoid_: ingest conversion, source promotion

**Category Evidence**:
Current-revision text or trusted metadata that supports or rejects a **Big Category** assignment.
_Avoid_: model intuition, stale evidence, unsupported inference

**Classification Margin**:
The confidence gap between the strongest **Big Category** candidate and the next strongest candidate.
_Avoid_: top score only

## Relationships

- A **Document** belongs to exactly one **Big Category** or to **Uncategorized**.
- **Needs Review** prevents uncertain classification from being treated as a trusted **Big Category** assignment.
- **Uncategorized** is a category value, while **Classification Status** explains how classification reached or avoided that value.
- A **Big Category** is chosen from the **Closed Catalog** and is not created automatically during ingest or classification.
- **Big Category** assignment is single-select; multi-dimensional meaning belongs in tags, not multiple big categories.
- A **Category Identity** is stable across languages; a **Localized Category Label** changes display text, not category identity.
- A **Language-Neutral Category Profile** drives classification; localization changes labels and initial wording, not category behavior.
- **Category Identity**, **Language-Neutral Category Profile**, and **Category Localization** are separate concerns.
- A **Legacy Category Template** is retired from new initialization but is migrated explicitly rather than deleted from existing vaults.
- A **Confident Assignment** must be correct; an **Abstain** is acceptable when evidence is weak or conflicting.
- A **Default Big Category Template** can be edited by the user, and the resulting active set becomes the **Closed Catalog**.
- A **Big Category** can be manually used while not **Classification Ready**.
- Only **Classification Ready** **Big Categories** are eligible for **Confident Assignment**.
- A **Category Profile** includes both positive and negative boundaries, not just a name or description.
- A used **Big Category** can become **Inactive for Classification** without changing existing document assignments.
- A used **Big Category** should be migrated before it is archived.
- A **Manual Assignment** outranks an **Accepted Suggestion**, which outranks a **Confident Assignment**, which outranks **Abstain**.
- Automatic classification must not replace a **Manual Assignment** or **Accepted Suggestion**; it may only propose a review.
- A **Confident Assignment** may become document metadata immediately, while a **Category Suggestion** requires review or acceptance.
- **Legacy Classification Records** may be preserved for compatibility, but new category decisions use **Classification Runs** and **Classification Results**.
- A **Category Fixture Suite** measures confident precision and expected **Abstain** behavior, not forced classification coverage.
- A changed **Category Profile** may make prior automatic assignments **Stale Assignments**, but it must not directly rewrite document categories.
- The **Category Classifier** owns trusted category decisions; the **Category Harness** may propose candidates but cannot bypass deterministic validation.
- **Catalog Management** changes category definitions; a **Classification Run** applies those definitions to documents.
- A **Classification Run** contains one or more **Classification Results**.
- **Category Feedback** is explicit input for future review and evaluation, not hidden online learning.
- **Tag Governance** is separate from **Big Category** classification; tags may provide context but are not governed in the category foundation phase.
- A **Formal Tag** is trusted metadata; a **Candidate Tag** is reviewable evidence and must not be treated as applied metadata.
- A **Formal Tag** is multi-select document metadata, unlike the single-select **Big Category**.
- A **Formal Tag** is globally normalized by default; **Tag Scope** may limit automatic use to a **Big Category** without creating a separate tag namespace.
- **Manual Tag Creation** may proceed after policy warnings, but automatic workflows remain bound by **Tag Admission Policy** and **Tag Volume Budget**.
- A **Canonical Tag** is the target used after resolving aliases and merges.
- A **Multilingual Tag Alias** lets searches and candidates in different languages resolve to the same **Canonical Tag**.
- **Deprecated Tags**, **Merged Tags**, and **Archived Tags** preserve history without participating in new automatic tagging.
- Merging or deprecating a tag changes future resolution and search interpretation, but does not rewrite existing document-tag links unless **Tag Link Migration** is explicitly run.
- Automatic tag workflows may use existing **Formal Tags** or propose **Candidate Tags**, but they must not create new **Formal Tags** without **Tag Promotion**.
- An **Auto-Attached Tag** may only use an existing active **Canonical Tag** with current-revision evidence and sufficient confidence.
- Automatic tag workflows must not delete, overwrite, or silently replace manual tags.
- **Deprecated Tags**, **Merged Tags**, and **Archived Tags** are ineligible for automatic attachment.
- **Tag Resolution** runs before a **Candidate Tag** is persisted.
- **Tag Admission Policy** blocks low-value new tag proposals such as one-off, overlong, over-specific, path/date/version-like, vague, duplicate, or **Big Category**-equivalent tags.
- **Tag Blocklist** entries prevent matching raw candidates from entering normal review or promotion, but do not delete existing tags.
- An **Attach-Existing Candidate** points to a **Canonical Tag** rather than duplicating the raw candidate string.
- A **New-Tag Proposal** requires review and budget checks before **Tag Promotion** can create a **Formal Tag**.
- Accepting a **Candidate Tag** applies only to the current document unless a separate **Tag Propagation** workflow is explicitly run.
- **Tag Propagation** must create reviewable proposals or require an explicit apply command with limits; it is not hidden batch tagging.
- A **Tag Volume Budget** prevents tag suggestions and promotions from producing tag sprawl; the v1 default is at most 5 auto-attached tags per document, 5 candidates per document, 20 new-tag proposals per run, 200 total candidates per run, and new unseeded tags should normally appear in at least 2 documents before promotion.
- **Explainable Tag Quantity Control** makes tag volume governance auditable: budget violations are hard failures, while near-budget documents and dominant sprawl reasons are reportable warnings for policy review.
- **Category Search Filter** belongs to the category foundation phase; tag-aware search belongs to **Tag Governance**.
- A **Tag Search Filter** is authoritative only when it follows `document_tags` through resolved **Formal Tags**.
- Full-text matches against tag metadata can help broad search, but they are not a trusted **Tag Search Filter**.
- **FTS Tag Metadata Non-Authority** means `chunks_fts.tags` or metadata tag strings must never determine trusted tag-filter membership.
- A **Trusted Document Tag Source** may be `manual`, `accepted_candidate`, `auto`, or `legacy_classification`; pending, rejected, blocked, stale, or raw candidates are not trusted filter sources.
- **Tag Feedback** is explicit audit data, not a hidden training signal.
- **Tag Governance Events** explain why tag resolution, search, alias, merge, scope, or lifecycle behavior changed.
- The **Tag Harness** evaluates approved tag policy and feedback-derived cases before release.
- **Tag Harness Hardening** follows **Tag Governance Foundation** as v0.3.2.1 and precedes broader **Tag Search Filter** governance work.
- **Tag Harness Hardening** must not replace the existing v0.3.3 retrieval evaluation / answer readiness phase.
- **Tag Harness Hardening** may produce metrics, reports, release-gate failures, and **Tag Policy Suggestions**, but it must not mutate tag policy, aliases, merges, scopes, blocklists, budgets, or applied tags without an explicit review/accept workflow.
- **Tag Harness Hardening** evaluates tag behavior at four layers: **Tag Harness Case**, candidate, document, and **Tag Harness Run**.
- A **Tag Harness Case** is the smallest regression unit; hard release gates usually aggregate candidate, document, and run-level failures.
- A **Tag Harness Case Schema** must encode expected outcomes explicitly; harness behavior must not be inferred from case names or descriptive prose.
- A **Tag Harness Fixture Suite** may include hand-written cases, explicitly exported **Feedback-Derived Tag Harness Cases**, and approved dogfood cases.
- A **Feedback-Derived Tag Harness Case** must come from reviewed feedback or governance events; unreviewed feedback must not silently become a release gate.
- Repository fixtures for v0.3.2.1 must be **Sanitized Tag Harness Fixtures**; raw production vault content or private feedback exports must not be committed.
- Feedback-derived or dogfood tag harness cases may enter the repo only after explicit scrub/redact review and should preserve a sanitized-source marker.
- A **Tag Feedback Regression Loop** verifies accepted, rejected, merged, blocked, or scoped tag decisions as future harness cases, but it must not train a model or mutate production tag policy by itself.
- The v0.3.2.1 **Tag Harness Coverage Matrix** must cover active canonical auto-attachment, multilingual alias resolution, deprecated/merged/archived ineligibility, blocklist and sprawl rejection, manual tag protection, category-scope boundaries, trusted search-filter non-pollution, and feedback-derived regression.
- A **Tag Harness Run** must not directly mutate a production vault, tag policy, or applied tag state.
- A **Tag Harness Run** uses a **Tag Harness Eval Vault** by default, seeds required state through core services, and must not reuse or mutate a user's production vault.
- A **Tag Harness Seed Document** represents already-trusted source state; v0.3.2.1 does not retest swallow conversion, transition export, OCR, PDF ingest, network calls, or model providers.
- A **Tag Harness Search Check** may prove seeded documents are searchable through current FTS and trusted tag filters, but ingest/conversion failures are outside the v0.3.2.1 failure domain.
- **Tag Harness Hard Gates** follow a precision-first rule: missing or low-confidence candidates may be reported, but wrong trusted metadata is a blocker.
- **Wrong Auto-Attached Tag**, **Manual Tag Mutation**, **Trusted Filter Pollution**, deprecated/merged/archived auto-attachment, unresolved candidate persistence, budget violations, and harness policy mutation are zero-tolerance **Tag Harness Hard Gates**.
- Candidate tag recall is a report metric in v0.3.2.1, while candidate precision may become a threshold gate once fixture expectations are stable.
- A **Tag Harness Summary** is the contract for CI and release gates; human-readable output must not be the only source of gate truth.
- A **Tag Harness Artifact** may explain case-level failures for local debugging, but it must not become trusted user metadata.
- A **Tag Harness Failure Record** must expose stable **Tag Harness Reason Codes**; release gates and future consoler integration must not parse localized prose to understand failure causes.
- A **Tag Harness Artifact** should include a **Minimal Tag Harness Reproduction** for each failing case so an implementation agent can reproduce the exact raw candidate, expectation, actual outcome, and reason code.
- **Tag Harness Run** results should live in fixture/eval vaults or test artifacts by default, not in a user's production vault.
- v0.3.2.1 exposes tag harness validation through a **Tag Harness Release Gate Script** and test helpers first; a formal `indb tag harness` user command is deferred until the contract is proven.
- A **Tag Harness CI Gate** should run after the v0.3.2 tag governance gate and before v0.3.3 retrieval evaluation, so tag governance stability is proven before broader retrieval or `ask` work.
- The **Tag Harness Validation Suite** includes focused v0.3.2/v0.3.2.1 tests, both tag governance and tag harness release gates, full pytest, compileall, and the new CI harness job.
- A **Deterministic Tag Harness** must not read API keys, call network services, use embedding-backed taggers, or depend on nondeterministic provider output.
- A **Pure Tag Harness Evaluation Layer** may add a harness orchestration module, but it should call existing tag resolution, admission, budget, tagger, and search services; core service semantics change only when a harness case exposes a real defect.
- A **Fixture-First Tag Harness Implementation** starts with explicit case schemas, sanitized fixture expectations, and focused failing tests before implementing the harness runner, release gate, CI job, and documentation updates.
- **Exact Tag Harness Expectation Match** is required for hard-gate fixtures; aggregate metrics summarize results but must not hide individual case failures.
- Candidate precision and recall are **Report-Only Tag Harness Metrics** in v0.3.2.1 until the fixture corpus is large enough to support meaningful global thresholds.
- A **Schema-Neutral Tag Harness** should not add production vault migrations or long-term run tables unless the implementation proves existing fixture/eval artifacts cannot support the release gate.
- A **Tag Harness Search Check** verifies exact trusted tag-filter behavior, alias-to-canonical resolution, and filter non-pollution; it does not expand search ranking, semantic retrieval, query expansion, retrieval packages, or `ask`.
- The first **Tagger** is deterministic and local; real model or embedding providers are out of scope until governance gates are stable.
- The **Post-Ingest Tagging Stage** is disabled by default and must not roll back trusted source ingestion.
- A **Post-Ingest Tagging Stage** may auto-attach only safe existing tags and may create only budgeted reviewable candidates.
- **Tag Governance Foundation** follows the category foundation and does not implement retrieval packages, generated answers, or ontology management.
- **Legacy Classification Tag Suggestions** may remain compatible with older classification commands, but v0.3.2 tag governance uses dedicated tag runs, candidates, feedback, and governance events.
- **Tag Governance Foundation** does not implement TUI; run, review, policy, and search commands expose **Consoler-Facing Command Output** for future UI integration.
- A **Tag Policy Suggestion** is reviewable and must not mutate aliases, merges, scopes, or admission rules without approval.
- A **Tag Doctor Finding** reports correctness issues as hard findings and tag-sprawl risks as warnings; doctor does not merge, delete, promote, or repair tags automatically.
- A **Tag Harness Doctor Boundary** keeps release regression separate from real-vault diagnosis: harness runs fixture behavior checks, while doctor may report tag integrity, lifecycle, candidate-resolution, sprawl, and filter-health issues without executing the full fixture suite.
- A **Tag Harness Completion Boundary** means v0.3.2.1 proves tag governance regressions can be caught; it does not complete ranking, combined filter behavior, search UX, retrieval packages, or `ask` readiness.
- **Tag/Search Governance** follows **Tag Harness Hardening** as v0.3.2.2 and owns governed tag/search composition, not v0.3.3 retrieval evaluation or `ask`.
- **Tag/Search Governance** stabilizes **Governed Search Paths** for original text search, **Tag Search Filter**, **Tag/Text Search**, and **Category/Tag Search**.
- A **Tag/Search Governance Harness** is separate from **Tag Harness Hardening**: it evaluates source search paths and filter composition rather than tag candidate generation or tag policy behavior.
- Repository fixtures for the **Tag/Search Governance Harness** must be **Sanitized Tag/Search Fixtures**; raw private vault snippets or unsanitized dogfood cases must not be committed.
- A **Tag/Search Governance Gate** must cover original text search, filter-only tag search, tag/text AND search, category/tag AND search, lifecycle filter behavior, invalid-filter versus empty-result behavior, candidate/raw-tag non-pollution, explanation JSON, and deterministic ordering.
- A **Tag/Search Governance CI Gate** should run after the v0.3.2.1 tag harness CI gate and before broader retrieval/ask work relies on governed tag/search behavior.
- A **Source Search Hard Gate** must prove exact phrase or source substring search returns the expected current chunk, archived documents and old revisions stay out of default search, result source bindings include `doc_id`, `revision_id`, and `chunk_id`, and CJK substring fallback still works under governed filters.
- A **Search Source Safety Gate** must prove governed source search does not return archived documents, deleted chunks, non-current revisions, derived output artifacts, review/promotion candidate source shells, or archived/deleted tag relation leakage.
- **Schema-Neutral Search Governance** should use existing source, category, tag, document-tag, and chunk state by default; v0.3.2.2 should not add production migrations unless implementation proves existing state cannot support governed source search.
- **Deterministic Search Governance** keeps v0.3.2.2 hard gates on deterministic source search, usually FTS plus current substring fallback; embeddings, providers, vector quality, and hybrid ranking changes are out of scope.
- The **Tag/Search Governance Validation Suite** includes focused search/tag/harness tests, the v0.3.2 tag governance gate, the v0.3.2.1 tag harness gate, the v0.3.2.2 tag/search governance gate, full pytest, compileall, and v0.3.3 retrieval eval regression when retrieval behavior is touched.
- **Tag/Search Governance Non-Goals** must be listed in the v0.3.2.2 execution plan so the phase does not absorb `ask`, retrieval package ranking, providers, embeddings, vector/hybrid redesign, production schema design, parallel search commands, UI, doctor repair, OR/semantic expansion, or untrusted tag-string filtering.
- A **Category/Tag Intersection Gate** must prove documents satisfying only one of category or tag are excluded, documents satisfying both are included, valid no-intersection queries return empty success, and explanations show both filters were applied.
- A **Governed Search Path** must return source snippets tied to current trusted chunks and must explain applied filters and match sources through a **Search Match Explanation**.
- **Tag/Text Search** and **Category/Tag Search** must use resolved **Formal Tags** and trusted category assignments, not raw tag strings, candidate tags, or full-text metadata coincidences.
- A **Tag/Search Governance Gate** must include a case where FTS tag metadata mentions a tag but `document_tags` lacks the trusted relationship, proving `--tag` does not return that document.
- **Search Match Explanation** should expose the **Trusted Document Tag Source** behind a trusted tag-filter match when available.
- v0.3.2.2 governs **Source Search** only; retrieval packages, answer readiness, generated answers, and `ask` remain owned by later retrieval/ask phases.
- **Conjunctive Search Filters** are mandatory for v0.3.2.2: category, tag, and text constraints combine as AND, without implicit OR, semantic expansion, similar-tag expansion, or candidate-tag participation.
- CLI flags such as `--tag` and `--category` and **Search Query Prefix Filters** such as `tag:<ref>` and `category:<ref>` must normalize into one **Search Filter Model** before execution.
- Structured CLI filters have highest precedence; conflicting CLI and prefix filters are **Invalid Search Filters**, while equivalent filters may be deduplicated.
- **Search Query Prefix Filters** that contain spaces must use explicit quoting, and v0.3.2.2 does not introduce multi-tag OR, multi-category OR, or similar-tag syntax.
- **Filter-Only Source Search** is allowed for trusted category/tag filters with no text query, but it must report active filters and return bounded representative current source snippets.
- **Filter-Only Source Search** defaults to document-level **Representative Source Snippets**, not all matching chunks.
- **Deterministic Search Ordering** is required: text queries may keep existing FTS relevance ordering, while filter-only searches must use a stable document/chunk ordering; v0.3.2.2 does not introduce new boost, rerank, or semantic score policy.
- A **Search Match Explanation** must be exposed through a stable **Search Explanation JSON Contract**; human CLI output may show a concise explanation but must not be the machine contract.
- **Search JSON Contract** is the future consoler-facing contract for governed `indb search --json`; v0.3.2.2 does not implement TUI or consoler UI.
- The **Governed Search CLI Surface** extends existing `indb search` with `--category`, governed `--tag`, prefix parsing, and stable `--json`; it does not add parallel search commands or rewrite `indb retrieve`.
- v0.3.2.2 may introduce a **Search Filter/Explanation Layer** or narrowly extend existing search modules, but it must not rewrite FTS scoring, chunking, indexing, retrieval ranking, or retrieval package logic.
- A **Tag/Search Doctor Finding** diagnoses governed source-search integrity problems such as stale trusted tag metadata, broken filter resolution, category/tag relation inconsistency, or candidate/raw tag pollution; doctor must not repair, rebuild, retag, or rerank automatically.
- **Tag Filter Lifecycle Semantics** allow active canonical tags, active aliases, and merged-tag references to resolve to the canonical tag for trusted filtering; deprecated tag references may query existing bindings with warnings; archived tag references are excluded from trusted filtering by default.
- A **Deprecated Tag Search Binding** queries only existing document-tag bindings for the deprecated tag itself and does not expand to canonical targets or run link migration.
- **Search Match Explanation** must indicate alias resolution, merged-tag resolution, deprecated warnings, and archived-tag exclusion when those lifecycle states affect a search.
- An **Invalid Search Filter** is a user/request error and should fail explicitly with a stable error code; an **Empty Governed Search Result** is a successful search with no matches and should return an empty result set plus explanation.
- Unknown, blocked, archived, or candidate-only tag references are **Invalid Search Filters** for trusted source search.
- **Search Filter Errors** and **Search Execution Errors** must be represented separately in governed search output; an **Empty Governed Search Result** must not be reported as either kind of error.
- **Tag Harness Non-Goals** must be listed in the v0.3.2.1 execution plan so the harness phase does not absorb formal CLI design, production schema design, provider intelligence, broader search governance, ingest validation, or automatic policy mutation.
- The **Tag Harness Coverage Matrix** must include **User-Curated Tag State**, proving that user-added active canonical tags, edited aliases, and scoped tags are evaluated through current formal metadata rather than default seed tags only.
- A **Taxonomy Execution Error** is an issue; an intentional **Abstain** is a valid classification outcome.
- The **Post-Ingest Taxonomy Stage** may classify or abstain after ingest, but it must not roll back trusted source ingestion.
- A **Confident Assignment** requires **Category Evidence** from the current trusted revision, not unsupported model inference or stale source state.
- A **Confident Assignment** requires both high confidence and sufficient **Classification Margin** over competing categories.

## Example Dialogue

> **Dev:** "If a document looks half work note and half reference, should we create a new category for it?"
> **Domain expert:** "No. A **Big Category** comes from the closed catalog. If evidence conflicts, mark it **Needs Review** instead of inventing a category."

> **Dev:** "Should the classifier always pick the closest **Big Category**?"
> **Domain expert:** "No. If it cannot make a **Confident Assignment**, it should **Abstain**."

> **Dev:** "Can the user add a new top-level category?"
> **Domain expert:** "Yes. User-curated changes update the **Closed Catalog**; automatic classification still cannot invent categories on its own."

> **Dev:** "Should the Chinese category template create different category IDs?"
> **Domain expert:** "No. Chinese labels are **Localized Category Labels** for the same **Category Identities**."

> **Dev:** "Can a category named 'Important' participate in automatic classification?"
> **Domain expert:** "Only after it has a **Category Profile** and becomes **Classification Ready**; otherwise it is manual-only."

> **Dev:** "If re-ingest makes a manually categorized document look like another category, should the classifier move it?"
> **Domain expert:** "No. A **Manual Assignment** is trusted; the classifier may create a review suggestion, not overwrite it."

> **Dev:** "If classification fails after ingest, should we undo the document revision?"
> **Domain expert:** "No. The **Post-Ingest Taxonomy Stage** records a review or issue; trusted source ingestion remains intact."

> **Dev:** "If the classifier abstains, should ingest be marked as failed?"
> **Domain expert:** "No. **Abstain** is valid; only a **Taxonomy Execution Error** is an issue."

> **Dev:** "Can a classifier assign a category because a model says the document feels like that topic?"
> **Domain expert:** "No. A **Confident Assignment** needs **Category Evidence** tied to the current trusted revision."

> **Dev:** "If two categories score almost the same, should we still pick the top one?"
> **Domain expert:** "No. Low **Classification Margin** means the classifier should **Abstain** or request review."

> **Dev:** "Can an automatic tagger create a new tag and attach it immediately?"
> **Domain expert:** "No. It may attach existing **Formal Tags** when rules allow, or create **Candidate Tags** for review and **Tag Promotion**."

> **Dev:** "Can the automatic tagger remove a user's manual tag if it seems wrong?"
> **Domain expert:** "No. It can create a review suggestion, but manual tags are not deleted or overwritten automatically."

## Flagged Ambiguities

- "Chinese template" could mean a separate category set or localized display; resolved: it means **Localized Category Labels** and profile text for the same **Category Identities**.

- "目录" can mean filesystem directory or broad document category; resolved: use **Big Category** for the taxonomy concept and avoid "directory" unless referring to paths on disk.
- "完全准确" does not mean every document must be auto-classified; resolved: **Confident Assignment** must be correct, while **Abstain** is an accepted outcome.
