# Taxonomy Category Glossary

## Terms

**Big Category**: Closed, user-curated category in the indbase taxonomy.

**Category Catalog**: The closed set of category IDs, labels, and lifecycle
state available in a vault.

**Category Profile**: Positive and negative evidence boundaries used by
classification.

**Classification Ready**: Category lifecycle state that allows automatic
classification to consider the category.

**Category Suggestion**: Auditable automatic or assisted category assignment
candidate.

**Manual Category Assignment**: User-owned assignment that automatic workflows
must not overwrite.

## Relationships

- Automatic classification can only use categories that already exist and are
  classification-ready.
- Chinese labels localize stable category IDs; they do not create a separate
  category tree.

## Flagged Ambiguities

- Automatic classification must not create categories.
- Abstain / needs-review is valid.
