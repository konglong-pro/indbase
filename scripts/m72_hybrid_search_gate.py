"""Run the M7.2 hybrid search gate against a temporary vault."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from typer.testing import CliRunner

import indbase_core.normalizers as normalizers
from indbase_core.db import connect
from indbase_core.documents import archive_document, restore_document
from indbase_core.embeddings import rebuild_vector_index
from indbase_core.indexer import rebuild_fts_index
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.ocr import run_ocr_for_document
from indbase_core.search import SearchOptions, search_chunks
from indbase_core.vault import init_vault
from indbase_cli.main import app


ROOT = Path.cwd()


def main() -> None:
    root = ROOT / ".tmp" / f"m72-hybrid-search-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    original_markitdown = normalizers._run_markitdown_file
    try:
        summary = _run_gate(root)
    finally:
        normalizers._run_markitdown_file = original_markitdown

    hard_metrics = {
        "hybrid_old_revision_results": summary["hybrid_old_revision_results"],
        "hybrid_archived_results": summary["hybrid_archived_results"],
        "hybrid_source_shell_results": summary["hybrid_source_shell_results"],
        "hybrid_answer_fields": summary["hybrid_answer_fields"],
    }
    required_positive = {
        "hybrid_results_with_doc_id": summary["hybrid_results_with_doc_id"],
        "hybrid_results_with_revision_id": summary["hybrid_results_with_revision_id"],
        "hybrid_results_with_chunk_id": summary["hybrid_results_with_chunk_id"],
        "hybrid_results_with_snippet": summary["hybrid_results_with_snippet"],
        "hybrid_results_with_source_path": summary["hybrid_results_with_source_path"],
        "vector_only_results": summary["vector_only_results"],
        "fts_only_results": summary["fts_only_results"],
        "forced_ocr_hybrid_results": summary["forced_ocr_hybrid_results"],
    }
    if any(value != 0 for value in hard_metrics.values()):
        raise RuntimeError(f"M7.2 hard metrics failed: {hard_metrics}")
    if any(value <= 0 for value in required_positive.values()):
        raise RuntimeError(f"M7.2 required positive metrics failed: {required_positive}")
    if summary["archive_restore_archived_doc_results_after_archive"] != 0:
        raise RuntimeError(f"M7.2 archive filter failed: {summary}")
    if summary["archive_restore_archived_doc_results_after_restore"] <= 0:
        raise RuntimeError(f"M7.2 restore filter failed: {summary}")

    print(json.dumps(summary, sort_keys=True))
    print("M72_HYBRID_SEARCH_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_gate(root: Path) -> dict[str, int]:
    vault = root / "vault"
    root.mkdir(parents=True, exist_ok=True)
    init_vault(vault)

    active = root / "active.md"
    changing = root / "changing.md"
    archived = root / "archived.md"
    shell_pdf = root / "shell.pdf"
    ocr_pdf = root / "ocr.pdf"
    active.write_text("# Active\nhybrid active needle source text\n", encoding="utf-8")
    changing.write_text("# Changing\nold revision vector ghost\n", encoding="utf-8")
    archived.write_text("# Archived\narchived hybrid hidden text\n", encoding="utf-8")
    shell_pdf.write_bytes(b"%PDF source shell")
    ocr_pdf.write_bytes(b"%PDF text")

    run_m3_ingest_pipeline(vault, active)
    run_m3_ingest_pipeline(vault, changing)
    run_m3_ingest_pipeline(vault, archived)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        archived_doc = connection.execute("SELECT doc_id FROM documents WHERE title = 'archived'").fetchone()["doc_id"]
        archive_document(connection, archived_doc)
        rebuild_vector_index(connection)
        old_revision = connection.execute("SELECT current_revision_id FROM documents WHERE title = 'changing'").fetchone()[
            "current_revision_id"
        ]
    finally:
        connection.close()

    changing.write_text("# Changing\nnew current revision vector text\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, changing)

    normalizers._run_markitdown_file = lambda _path: " "
    run_m3_ingest_pipeline(vault, shell_pdf)
    normalizers._run_markitdown_file = lambda _path: "# PDF\nold pdf text before forced ocr\n"
    run_m3_ingest_pipeline(vault, ocr_pdf)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        ocr_doc = connection.execute("SELECT doc_id, original_path FROM documents WHERE source_uri = ?", (str(ocr_pdf),)).fetchone()
        (vault / ocr_doc["original_path"]).with_name("original.pdf.ocr.txt").write_text(
            "forced ocr hybrid current text",
            encoding="utf-8",
        )
        ocr = run_ocr_for_document(connection, vault, ocr_doc["doc_id"], force=True)
        rebuild_vector_index(connection)

        fts_only = search_chunks(connection, "hybrid active needle", options=SearchOptions(mode="fts"))
        vector_only = search_chunks(connection, "vector probe", options=SearchOptions(mode="vector"))
        hybrid = search_chunks(connection, "hybrid active needle", options=SearchOptions(mode="hybrid", top_k=10))
        forced_ocr = search_chunks(connection, "forced ocr hybrid", options=SearchOptions(mode="hybrid"))

        archived_doc = connection.execute("SELECT doc_id FROM documents WHERE title = 'archived'").fetchone()["doc_id"]
        archived_while_archived = search_chunks(connection, "archived hybrid hidden", options=SearchOptions(mode="hybrid"))
        restore_document(connection, archived_doc)
        rebuild_fts_index(connection, vault)
        rebuild_vector_index(connection)
        archived_after_restore = search_chunks(connection, "archived hybrid hidden", options=SearchOptions(mode="hybrid"))

        shell_doc_ids = {
            str(row["doc_id"])
            for row in connection.execute("SELECT doc_id FROM documents WHERE current_revision_id IS NULL").fetchall()
        }
        runner = CliRunner()
        cli_result = runner.invoke(
            app,
            ["search", "hybrid active needle", "--mode", "hybrid", "--vault", str(vault), "--json"],
        )
        cli_payload = json.loads(cli_result.output)
    finally:
        connection.close()

    if ocr.status != "succeeded":
        raise RuntimeError(f"forced OCR setup failed: {ocr}")
    if cli_result.exit_code != 0:
        raise RuntimeError(f"CLI hybrid search failed: {cli_result.output}")

    hybrid_results = list(hybrid.results)
    cli_text = json.dumps(cli_payload, ensure_ascii=False)
    return {
        "fts_only_results": fts_only.result_count,
        "vector_only_results": vector_only.result_count,
        "hybrid_results": hybrid.result_count,
        "hybrid_results_with_doc_id": sum(1 for row in hybrid_results if row.doc_id),
        "hybrid_results_with_revision_id": sum(1 for row in hybrid_results if row.revision_id),
        "hybrid_results_with_chunk_id": sum(1 for row in hybrid_results if row.chunk_id),
        "hybrid_results_with_snippet": sum(1 for row in hybrid_results if row.snippet),
        "hybrid_results_with_source_path": sum(1 for row in hybrid_results if row.source_path),
        "hybrid_old_revision_results": sum(1 for row in hybrid_results + list(vector_only.results) if row.revision_id == old_revision),
        "hybrid_archived_results": sum(1 for row in hybrid_results if row.doc_id == archived_doc),
        "hybrid_source_shell_results": sum(1 for row in hybrid_results if row.doc_id in shell_doc_ids),
        "hybrid_answer_fields": 1 if '"answer"' in cli_text else 0,
        "forced_ocr_hybrid_results": forced_ocr.result_count,
        "forced_ocr_current_revision_results": sum(1 for row in forced_ocr.results if row.revision_id == ocr.revision_id),
        "archive_restore_hybrid_results_after_archive": archived_while_archived.result_count,
        "archive_restore_hybrid_results_after_restore": archived_after_restore.result_count,
        "archive_restore_archived_doc_results_after_archive": sum(
            1 for row in archived_while_archived.results if row.doc_id == archived_doc
        ),
        "archive_restore_archived_doc_results_after_restore": sum(
            1 for row in archived_after_restore.results if row.doc_id == archived_doc
        ),
        "cli_hybrid_results": int(cli_payload["result_count"]),
        "cli_hybrid_has_source_path": sum(1 for row in cli_payload["results"] if row.get("source_path")),
    }


if __name__ == "__main__":
    main()
