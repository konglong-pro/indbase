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

**Candidate Tag**:
A proposed tag from an agent, harness, or review workflow that has not yet been promoted into trusted document metadata.
_Avoid_: formal tag, applied tag, accepted tag

**Tag Promotion**:
The explicit workflow that turns a **Candidate Tag** into a **Formal Tag** or links it to an existing **Formal Tag**.
_Avoid_: automatic tag creation, hidden training

**Tag Volume Budget**:
A governance limit that controls how many tags may be suggested, promoted, or attached so the tag system does not fragment into overly specific labels.
_Avoid_: unlimited tagging, tag sprawl

**Category Search Filter**:
A search constraint that returns only documents assigned to a selected **Big Category**.
_Avoid_: tag filter, full-text category mention

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
- A **Canonical Tag** is the target used after resolving aliases and merges.
- **Deprecated Tags**, **Merged Tags**, and **Archived Tags** preserve history without participating in new automatic tagging.
- Automatic tag workflows may use existing **Formal Tags** or propose **Candidate Tags**, but they must not create new **Formal Tags** without **Tag Promotion**.
- A **Tag Volume Budget** prevents tag suggestions and promotions from producing tag sprawl.
- **Category Search Filter** belongs to the category foundation phase; tag-aware search belongs to **Tag Governance**.
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

## Flagged Ambiguities

- "Chinese template" could mean a separate category set or localized display; resolved: it means **Localized Category Labels** and profile text for the same **Category Identities**.

- "目录" can mean filesystem directory or broad document category; resolved: use **Big Category** for the taxonomy concept and avoid "directory" unless referring to paths on disk.
- "完全准确" does not mean every document must be auto-classified; resolved: **Confident Assignment** must be correct, while **Abstain** is an accepted outcome.
