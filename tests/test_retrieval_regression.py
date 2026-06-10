from __future__ import annotations

from pathlib import Path

from indbase_core.chunker import chunk_current_revision
from indbase_core.db import connect
from indbase_core.indexer import rebuild_fts_index
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.retrieval_evaluation import import_eval_cases_from_jsonl, run_eval_suite
from indbase_core.vault import init_vault

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "retrieval_regression" / "v035_core.jsonl"


def _ingest_index(vault: Path, source: Path) -> str:
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute(
            "SELECT doc_id FROM documents WHERE normalized_source_uri LIKE ?",
            (f"%{source.name}",),
        ).fetchone()["doc_id"]
        chunk_current_revision(connection, vault, doc_id)
        rebuild_fts_index(connection, vault)
        return str(doc_id)


def test_v035_retrieval_regression_thresholds(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="indbase_default_v1")
    provider_note = tmp_path / "provider.md"
    cjk_note = tmp_path / "cjk.md"
    provider_note.write_text(
        "# Provider\n\nprovider failure class evidence completeness should remain retrievable.\n",
        encoding="utf-8",
    )
    cjk_note.write_text(
        "# CJK\n\n鐭ヨ瘑 鐐瑰嚮 lineage should remain retrievable.\n",
        encoding="utf-8",
    )
    _ingest_index(vault, provider_note)
    _ingest_index(vault, cjk_note)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        imported = import_eval_cases_from_jsonl(connection, FIXTURE, source="fixture")
        result = run_eval_suite(connection, suite="v035_retrieval_regression")

    assert imported.rejected == 0
    assert result.case_count == 2
    assert result.failed_count == 0
    assert result.error_count == 0
