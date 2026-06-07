# indbase

[![CI](https://github.com/konglong-pro/indbase/actions/workflows/ci.yml/badge.svg)](https://github.com/konglong-pro/indbase/actions/workflows/ci.yml)

Local-first personal knowledge database: ingest local files into a vault, keep immutable source revisions, chunk and index content, and return searchable snippets tied to `doc_id` / `revision_id` / `chunk_id`.

**Current status:** v0.1 is frozen; v0.2 swallow ingest, v0.2 transition output, taxonomy, tag governance, governed source search, consoler Source Trust coordination, and v0.3.3 retrieval evaluation/readiness are implemented. See [Current active work](docs/active/current.md) and [Project status](docs/project-status.md).

## Who this is for

Developers and operators who want a **trustworthy local knowledge substrate** before adding LLM “ask” or agent workflows. The vault is the system of record—not swallow or transition.

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended)
- Node.js 20+ only if you use transition output (`indb output …`)

## Install

```powershell
git clone https://github.com/konglong-pro/indbase.git
cd indbase
uv sync --group dev
uv run indb --version
```

Swallow-backed ingest (optional extra):

```powershell
uv sync --group dev --extra swallow
```

## Quick start

```powershell
# Create a vault
uv run indb init .\my-vault

# Enable swallow for real conversion (required for v0.2 ingest)
# Edit my-vault\.indbase\config\config.toml → features.swallow_ingest = true
# Or use ingest flows that set it after runtime install.

uv run indb ingest .\path\to\sources --vault .\my-vault --recursive
uv run indb search "your query" --vault .\my-vault
uv run indb doctor --vault .\my-vault
```

Transition output (after runtime install):

```powershell
uv run indb output runtime install --vault .\my-vault
uv run indb output export source <doc_id> --vault .\my-vault
uv run indb doc normalize <doc_id> --replace-current --vault .\my-vault
```

## Common commands

| Area | Examples |
| --- | --- |
| Vault | `indb init`, `indb doctor` |
| Ingest | `indb ingest <path>`, `indb ingest url <url>`, `indb ingest archive <zip>` |
| Search | `indb search <query>`, `indb search <query> --mode hybrid` |
| Documents | `indb doc show`, `indb doc archive`, `indb doc restore`, `indb doc revisions` |
| Output (v0.2) | `indb output runtime install`, `indb output export source`, `indb doc normalize` |
| Ops | `indb task list`, `indb review list`, `indb error list`, `indb index rebuild --fts` |

Full CLI surface and flags: run `indb --help` and subcommand `--help`.

## Verify changes (matches CI)

```powershell
uv run python -m pytest
uv run python -m compileall -q src tests scripts
uv run python scripts/v02_deterministic_release_gate.py
uv run python scripts/doctor_negative_gate.py
uv run python scripts/check_docs.py
```

Details: [Testing](docs/testing.md).

## Documentation

| Document | Purpose |
| --- | --- |
| [docs/project-status.md](docs/project-status.md) | **What is shipped** (replaces reading many old checkpoints) |
| [docs/active/current.md](docs/active/current.md) | Current active scope and must-read docs |
| [docs/phase-manifest.yaml](docs/phase-manifest.yaml) | Machine-readable phase lifecycle state |
| [docs/testing.md](docs/testing.md) | **What passes** — pytest, gates, CI |
| [docs/development.md](docs/development.md) | Local setup, feature flags |
| [docs/architecture.md](docs/architecture.md) | Trust boundaries and module map |
| [docs/contracts/](docs/contracts/) | Durable trust, revision, artifact, search, and adapter contracts |
| [docs/planning/](docs/planning/) | Lifecycle-managed product specs; [archive/](docs/planning/archive/) for historical checkpoints |
| [AGENTS.md](AGENTS.md) | Rules for coding agents |

## License

Proprietary — see package metadata in `pyproject.toml`.
