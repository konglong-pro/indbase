"""Layer E: real-corpus dogfood with v0.2 swallow-enabled vault (requires INDB_REAL_CORPUS)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path

from gate_common import doctor_hard_metrics, write_gate_summary, ROOT
from mvp_closeout_common import stage_real_corpus

from indbase_core.config import load_config, save_config
from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.paths import vault_paths
from indbase_core.vault import init_vault


def main() -> None:
    external = os.environ.get("INDB_REAL_CORPUS")
    if not external:
        raise RuntimeError("INDB_REAL_CORPUS must point at a directory of supported source files.")

    gate_root = ROOT / ".tmp" / f"v02-real-corpus-dogfood-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    gate_root.mkdir(parents=True)
    corpus = stage_real_corpus(gate_root, target_files=85)
    vault = gate_root / "vault"
    init_vault(vault, category_template="indbase_default_v1")

    config = load_config(vault_paths(vault).config_path)
    save_config(
        replace(config, features=replace(config.features, swallow_ingest=True)),
        vault_paths(vault).config_path,
    )

    result = run_m3_ingest_pipeline(vault, Path(str(corpus["sources"])), recursive=True)
    if result.written_revisions < 1:
        raise RuntimeError(
            "real corpus dogfood produced no trusted-current revisions; "
            "check corpus content, swallow availability, and promotion thresholds."
        )

    metrics = doctor_hard_metrics(vault)
    if metrics["critical_doctor_findings"]:
        raise RuntimeError(f"real corpus dogfood doctor hard findings: {metrics}")

    summary = {
        "layer": "E",
        "gate": "v02_real_corpus_dogfood",
        "source_mode": corpus.get("source_mode"),
        "input_files": corpus.get("input_files"),
        "written_revisions": result.written_revisions,
        "ingest_status": result.status,
        "doctor": metrics,
    }
    write_gate_summary(summary, gate_name="V02_REAL_CORPUS_DOGFOOD_GATE", gate_root=gate_root)


if __name__ == "__main__":
    from pathlib import Path

    main()
