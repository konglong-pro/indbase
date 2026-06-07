# Planning

Planning docs are lifecycle-managed. Do not infer active work from filename
order.

Current phase identity:

- `../phase-manifest.yaml`
- `../active/current.md`

## Lifecycle Directories

| Directory | Meaning | Read by default |
| --- | --- | --- |
| `active/` | Canonical active implementation plans | yes, only when current task touches that phase |
| `next/` | Planned but not implementation-approved work | no |
| `archive/` | Historical plans, checkpoints, and evidence | no |
| `superseded/` | Replaced rules or plans | no |

Legacy phase plans still present in this directory are retained for old links
while the archive migration proceeds. Their lifecycle state is declared in
`../phase-manifest.yaml`.

Frozen v0.1 and shipped v0.2 planning docs have been moved under `archive/v0.1`
and `archive/v0.2`; their old top-level filenames are compatibility pointers
only.
Completed v0.3.2.3 through v0.3.2.3f planning docs have been physically moved
under `archive/`; their old top-level filenames are compatibility pointers only.
Shipped v0.3.1 through v0.3.2.2 governance planning docs have also been moved
under `archive/`; their old top-level filenames are compatibility pointers only.
The broad v0.3.1 taxonomy foundation plan is superseded context, and the
completed v0.3.2 retrieval intelligence plan is archived under
`archive/v0.3.2-retrieval-intelligence/`.

## Current Active Plan

- `active/v0.3.3-retrieval-evaluation-answer-readiness.md`

## Status And Gates

- Current state: `../project-status.md`
- Testing and gates: `../testing.md`
- Phase lifecycle state: `../phase-manifest.yaml`
