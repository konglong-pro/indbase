"""Real swallow smoke gate (opt-in). Requires INDBASE_SWALLOW_SMOKE=1."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import json
import os

from gate_common import TRUSTED_NEEDLE, TRUSTED_MARKDOWN, env_enabled, gate_skip, write_gate_summary, ROOT

from indbase_core.config import default_config, load_config, save_config
from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.paths import vault_paths
from indbase_core.search import SearchOptions, search_chunks
from indbase_core.vault import init_vault


def main() -> None:
    if not env_enabled("INDBASE_SWALLOW_SMOKE"):
        summary = gate_skip("D", "v02_swallow_smoke", "Set INDBASE_SWALLOW_SMOKE=1 to run real swallow smoke.")
        print(json.dumps(summary, sort_keys=True))
        print("V02_SWALLOW_SMOKE_GATE=skipped")
        return

    gate_root = ROOT / ".tmp" / f"v02-swallow-smoke-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    source = gate_root / "trusted-long.md"
    source.parent.mkdir(parents=True)
    source.write_text(TRUSTED_MARKDOWN, encoding="utf-8")
    vault = gate_root / "vault"
    init_vault(vault, category_template="indbase_default_v1")
    config = load_config(vault_paths(vault).config_path)
    save_config(
        replace(
            config,
            features=replace(config.features, swallow_ingest=True),
            ingest=replace(
                config.ingest,
                swallow=replace(config.ingest.swallow, min_markdown_chars=80),
            ),
        ),
        vault_paths(vault).config_path,
    )

    result = run_m3_ingest_pipeline(vault, source, recursive=False)
    if result.written_revisions != 1 or not result.searchable:
        raise RuntimeError(f"real swallow smoke ingest did not produce searchable revision: {result}")

    connection = connect(vault_paths(vault).db_path)
    try:
        row = connection.execute(
            """
            SELECT cr.converter_name, cr.promotion_status, cr.status
            FROM converter_runs cr
            ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()
        if row["converter_name"] != "swallow":
            raise RuntimeError(f"expected swallow converter, got {row['converter_name']}")
        if row["promotion_status"] != "trusted-current":
            raise RuntimeError(f"expected trusted-current, got {row['promotion_status']}")
        hits = search_chunks(connection, TRUSTED_NEEDLE, options=SearchOptions(top_k=3))
        if not hits.results:
            raise RuntimeError("trusted fixture not searchable after real swallow ingest")
    finally:
        connection.close()

    summary = {
        "layer": "D",
        "gate": "v02_swallow_smoke",
        "written_revisions": result.written_revisions,
        "promotion_status": row["promotion_status"],
        "search_hits": len(hits.results),
    }
    write_gate_summary(summary, gate_name="V02_SWALLOW_SMOKE_GATE", gate_root=gate_root)


if __name__ == "__main__":
    main()
