# Auditable Category Classification

Category classification is trusted metadata that affects navigation, review, and future retrieval quality, so indbase records classification as auditable runs and per-document results instead of only mutating `documents.category_id`. Direct metadata mutation would be simpler, but it would hide which category profile, evidence, confidence, margin, and classifier version produced a category assignment, making stale assignments and wrong confident assignments difficult to diagnose or repair.
