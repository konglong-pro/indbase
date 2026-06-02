"""Post-ingest tag governance feature flag tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from indbase_core.config import load_config, save_config
from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.vault import init_vault


def test_post_ingest_tagging_disabled_by_default(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.txt"
    source.write_text("sqlite database notes\n", encoding="utf-8")
    init_vault(vault)
    result = run_m3_ingest_pipeline(vault, source)
    assert result.status in {"succeeded", "completed_with_issues"}
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        run_count = connection.execute("SELECT COUNT(*) AS count FROM tagger_runs").fetchone()["count"]
    assert int(run_count) == 0


def test_post_ingest_tagging_runs_when_enabled(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "tagged.txt"
    source.write_text("# Note\n\nsqlite local database storage.\n", encoding="utf-8")
    init_vault(vault)
    config = load_config(vault / ".indbase" / "config" / "config.toml")
    save_config(
        replace(
            config,
            features=replace(
                config.features,
                tag_governance=True,
                post_ingest_tagging=True,
            ),
        ),
        vault / ".indbase" / "config" / "config.toml",
    )
    result = run_m3_ingest_pipeline(vault, source)
    assert result.status in {"succeeded", "completed_with_issues"}
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        run_count = connection.execute("SELECT COUNT(*) AS count FROM tagger_runs").fetchone()["count"]
    assert int(run_count) == 1
