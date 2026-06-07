# Source Trust Glossary

## Terms

**Source Trust Loop**: Bounded workflow for ingesting, searching, inspecting,
and troubleshooting trusted source records.

**Trusted Current**: Promotion decision that allows a candidate to become the
current source revision.

**Review Before Current**: Promotion decision that records evidence and review
state without making the candidate searchable as current source.

**Candidate**: Untrusted conversion output. It can include evidence but is not a
trusted revision.

**Promotion Policy**: indbase-owned rules that decide whether a candidate can
become a source revision.

**Durable Evidence**: Copied artifact data retained under indbase-owned storage
so later trust decisions can be audited.

## Relationships

- External converters produce candidates.
- indbase promotion policy creates trusted source revisions.
- Default search sees trusted current source revisions, not raw candidates.

## Flagged Ambiguities

- "Converted" does not mean trusted.
- "Evidence exists" does not mean promotion succeeded.
