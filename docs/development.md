# Development Guide

## Prerequisites

- Python **3.11+**
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- **Node.js 20+** only for transition output runtime / smoke tests

## Install

```powershell
git clone https://github.com/konglong-pro/indbase.git
cd indbase
uv sync --group dev
```

Optional extras:

```powershell
uv sync --group dev --extra swallow
uv sync --group dev --extra swallow-playwright
```

## First vault

```powershell
uv run indb init .\my-vault
uv run indb ingest .\samples --vault .\my-vault --recursive
uv run indb search "needle" --vault .\my-vault
uv run indb doctor --vault .\my-vault
```

## v0.2 feature flags

Edit `my-vault/.indbase/config/config.toml`:

| Flag | Meaning |
| --- | --- |
| `features.swallow_ingest = true` | Required for production conversion (swallow adapter) |
| `features.transition_output = true` | Set automatically after `indb output runtime install` |
| `features.web_ingest`, `ocr`, `asr` | Optional ingest paths per spec |

New vaults default `swallow_ingest=false` and `transition_output=false`.

## Transition output runtime

```powershell
uv run indb output runtime install --vault .\my-vault
uv run indb output runtime status --vault .\my-vault
uv run indb output export source <doc_id> --vault .\my-vault
uv run indb doc normalize <doc_id> --replace-current --vault .\my-vault
```

## Run tests before PR

See [testing.md](testing.md).

## Agent / spec workflow

- Operational rules: `AGENTS.md`
- Current scope: `docs/active/current.md`
- Phase state: `docs/phase-manifest.yaml`
- Current agent rules: `docs/agents/current/indbase.md`
- Durable contracts: `docs/contracts/`
