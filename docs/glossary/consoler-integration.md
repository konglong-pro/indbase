# Consoler Integration Glossary

## Terms

**Consoler Adapter**: `indbase_agent` package that exposes Source Trust Loop
commands to consoler.

**Artifact Block**: Result block that advertises an opaque artifact URI and kind.

**Artifact Retrieval**: Dereferencing an opaque artifact URI into a bounded
artifact view.

**Artifact View**: Current-state read-only object payload with limits and
truncation metadata.

**Variant**: Consoler product surface scoped to an agent-specific workflow.

**Session Vault Path**: TUI-local convenience value used only to prefill editable
forms.

## Relationships

- indbase owns adapter contracts.
- consoler owns TUI variant behavior.
- `indbase_core` must not import consoler SDKs or renderer vocabulary.

## Flagged Ambiguities

- Artifact URI is not a filesystem path.
- Session vault memory is not durable vault preference storage.
