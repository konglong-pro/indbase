# Intent Drafting Glossary

## Terms

**Intent Drafting**: Mapping a single user text input into one editable form
candidate.

**Deterministic Drafting**: Offline rules that draft a form without provider
calls.

**Assisted Drafting**: Explicitly opted-in provider-assisted fallback for form
prefill.

**Editable Form**: Human-reviewable command form that must be submitted before
normal prepare/preview/approval/execute lifecycle can continue.

**Indbase NL v2 Intent Drafting**: Completed v0.3.2.3f consoler-owned assisted
drafting coordination for the existing Source Trust Loop.

**Provider Context Boundary**: Rule set limiting what user text and schema
metadata can be sent to an assisted provider.

## Relationships

- Drafting may prefill fields; it must not execute.
- Assisted drafting is not default behavior.
- Session vault path can be merged only at the form layer.

## Flagged Ambiguities

- Do not infer object IDs from latest results or artifacts.
- Do not infer tags/categories from vague semantics.
- Do not persist raw provider requests or responses.
