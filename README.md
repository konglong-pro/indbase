# indbase

`indbase` is a local-first personal knowledge database. The canonical Foundation MVP is described in `docs/planning/mvp-v0.1-spec.md`.

Current status: MVP v0.1 is frozen as version `0.1.0`. See [MVP v0.1 Freeze](docs/planning/mvp-v0.1-freeze.md) and the machine-readable [release manifest](docs/planning/mvp-v0.1-release-manifest.json). Release close-out is complete for the deterministic local scope: [MVP Release Gate](docs/planning/mvp-release-gate-checkpoint.md), [MVP Doctor Full Negative](docs/planning/mvp-doctor-full-negative-checkpoint.md), [MVP Windows Path/Unicode](docs/planning/mvp-windows-path-unicode-checkpoint.md), [MVP Performance Smoke](docs/planning/mvp-performance-smoke-checkpoint.md), [MVP Real Corpus Dogfood](docs/planning/mvp-real-corpus-dogfood-checkpoint.md), [MVP CLI/TUI Acceptance](docs/planning/mvp-cli-tui-acceptance-checkpoint.md), and [MVP Backup Restore](docs/planning/mvp-backup-restore-checkpoint.md) are passing. Feature checkpoints are complete through [M10 Candidate Cards](docs/planning/m10-completion-checkpoint.md). The v0.2 entry plan is tracked in [v0.2 Entry Plan](docs/planning/v0.2-entry-plan.md).

## Quick Start

This repository uses Python 3.11+ with a `src/` layout.

```powershell
uv sync --group dev
uv run pytest
uv run indb --version
uv run python scripts/m3_dogfood_gate.py
uv run python scripts/m4_tui_lite_gate.py
uv run python scripts/m5_catalog_review_gate.py
uv run python scripts/m6_pdf_ingest_gate.py
uv run python scripts/m62_ocr_gate.py
uv run python scripts/m63_pdf_ocr_hardening_gate.py
uv run python scripts/m71_embedding_index_gate.py
uv run python scripts/m72_hybrid_search_gate.py
uv run python scripts/m8_classification_gate.py
uv run python scripts/m81_classification_hardening_gate.py
uv run python scripts/m91_translation_gate.py
uv run python scripts/m92_translation_gate.py
uv run python scripts/m93_translation_hardening_gate.py
uv run python scripts/m10_candidate_card_preflight_gate.py
uv run python scripts/m101_candidate_extraction_gate.py
uv run python scripts/m102_card_review_gate.py
uv run python scripts/m103_card_hardening_gate.py
uv run python scripts/m10_candidate_cards_gate.py
uv run python scripts/v01_release_candidate_gate.py
uv run python scripts/mvp_doctor_full_negative_gate.py
uv run python scripts/mvp_windows_path_unicode_gate.py
uv run python scripts/mvp_perf_smoke.py
uv run python scripts/mvp_real_corpus_dogfood.py
uv run python scripts/mvp_cli_tui_acceptance_gate.py
uv run python scripts/mvp_backup_restore_gate.py
uv run python scripts/mvp_release_gate.py
```

Optional conversion support:

```powershell
uv sync --group dev --extra convert
uv run indb doctor --vault <vault>
```

Explicit OCR v0:

```powershell
uv run indb ocr run <doc_id> --vault <vault>
uv run indb ocr run <doc_id> --vault <vault> --force
uv run indb ocr pages <doc_id> --vault <vault>
```

Embedding index rebuild:

```powershell
uv run indb index rebuild --vectors --vault <vault>
uv run indb index status --vault <vault>
```

Search modes:

```powershell
uv run indb search <query> --mode fts --vault <vault>
uv run indb search <query> --mode vector --vault <vault>
uv run indb search <query> --mode hybrid --vault <vault>
```

Classification suggestions:

```powershell
uv run indb classify suggest [doc_id] --vault <vault>
uv run indb classify list --vault <vault>
uv run indb classify accept <suggestion_id> --vault <vault>
uv run indb classify reject <suggestion_id> --vault <vault>
```

Translation outputs:

```powershell
uv run indb translate chunks <doc_id> --revision <revision_id> --chunk <chunk_id> --target-language <language> --vault <vault>
uv run indb translate document <doc_id> --revision <revision_id> --target-language <language> --vault <vault>
uv run indb translate list --vault <vault>
uv run indb translate show <translation_id> --vault <vault>
uv run indb translate open <translation_id> --vault <vault> --print-path
```

Candidate extraction records:

```powershell
uv run indb card generate <doc_id> --vault <vault>
uv run indb card list --vault <vault>
uv run indb card show <candidate_card_id> --vault <vault>
uv run indb card accept <candidate_card_id> --vault <vault>
uv run indb card reject <candidate_card_id> --vault <vault>
```

MVP feature work and release-candidate close-out are complete for the current deterministic local scope. For private real-corpus validation, set `INDB_REAL_CORPUS` before running `scripts/mvp_real_corpus_dogfood.py`. Ask and model-backed extraction are still not enabled.
