# Tag Governance Glossary

## Terms

**Formal Tag**: Governed metadata entity, not a raw string.

**Canonical Tag**: Active formal tag that may be attached when policy allows.

**Candidate Tag**: Proposed tag text that must pass resolution before
persistence or review.

**Tag Resolution**: Process that maps candidate text to an existing tag,
rejects it, or creates a governed proposal.

**Tag Admission Policy**: Rules that decide whether a new tag proposal can enter
review.

**Tag Volume Budget**: Limit that prevents tag sprawl.

**Tag Feedback**: Explicit audit data about accepted, rejected, or corrected tag
behavior.

## Relationships

- Automatic workflows may auto-attach only existing active canonical tags.
- Deprecated, merged, and archived tags are ineligible for automatic attach.
- Manual tags must not be silently deleted, overwritten, or replaced.

## Flagged Ambiguities

- Raw/candidate tags are not trusted search filters.
- Governance events are audit data, not hidden learning.
