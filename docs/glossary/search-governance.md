# Search Governance Glossary

## Terms

**Source Search**: Retrieval of trusted source snippets from current promoted
source revisions.

**Search Filter Model**: Normalized representation of text, category, and tag
filters.

**Filter-Only Query**: Search request that relies on category/tag filters even
when free text is empty or minimal.

**Search Explanation**: Machine-readable reason for why a snippet matched.

**Answer Readiness**: Deterministic report that evaluates whether a retrieval
run can feed a future answer workflow.

## Relationships

- Category, tag, and text filters combine with AND semantics.
- Tag filters must use formal tag relations, not raw FTS metadata.
- v0.3.3 retrieval evaluation measures readiness without implementing `ask`.

## Flagged Ambiguities

- Search is not generated answer synthesis.
- Readiness is not answer generation.
