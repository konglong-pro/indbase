# Intent Draft Contract

## Purpose

Define the boundary between natural-language intent drafting and executable
indbase commands.

## Applies To

- Consoler-owned indbase variant intent drafting.
- Completed indbase coordination phases v0.3.2.3d through v0.3.2.3f.

## Rules

- Intent drafting produces editable form prefill only.
- Drafting must not prepare, preview, approve, execute, fetch artifacts, or
  write history/trace by itself.
- Runtime mapping must not infer `vault_path`, object IDs, tags, or categories
  from session history, traces, artifacts, previous results, cwd, or filesystem
  scans.
- Session `vault_path` may be merged only by the TUI form layer.
- Provider-returned paths and object IDs must come from current user text as
  literals.
- Provider-returned `tag` and `category` require explicit filter markers in the
  current user text.
- Assisted provider calls require explicit local opt-in and must not be default.
- Provider context must exclude vault contents, source snippets, file contents,
  history, trace, artifacts, previous results, credentials, prompts, endpoints,
  model names, and raw provider responses.

## Non-Goals

- No chat.
- No multi-action workflows.
- No generated answers or `ask`.
- No indbase natural-language parser or new indbase command surface.

## Validation

- Keep `src/indbase_agent/manifest.json` limited to the Source Trust Loop
  commands unless a later active phase explicitly changes it.
- Run coordination tests for completed v0.3.2.3d/f behavior when modifying
  consoler-facing manifest assumptions.
