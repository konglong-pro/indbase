"""Active v0.2 deterministic release gate (replaces M3 as release blocker).

Uses an explicit v0.2 vault configuration and deterministic swallow/transition
stubs. Proves indbase-owned trust boundaries without requiring real swallow workers
or pinned transition npm in CI.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

from gate_common import (
    REVIEW_NEEDLE,
    TRUSTED_NEEDLE,
    TRUSTED_MARKDOWN,
    SHORT_MARKDOWN,
    assert_doctor_clean,
    configure_v02_vault,
    doctor_hard_metrics,
    install_deterministic_swallow_stub,
    make_archive_fixture,
    search_count,
    write_gate_summary,
    ROOT,
)

install_deterministic_swallow_stub()

from indbase_core.db import connect
from indbase_core.ingest import (
    run_m3_archive_ingest_pipeline,
    run_m3_ingest_pipeline,
    run_m3_url_ingest_pipeline,
)
from indbase_core.output_service import export_source_revision, normalize_replace_current
from indbase_core.paths import vault_paths
from indbase_core.transition_adapter import run_fake_bridge
from indbase_core.vault import init_vault


def main() -> None:
    gate_root = ROOT / ".tmp" / f"v02-deterministic-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    vault = gate_root / "vault"
    sources = gate_root / "sources"
    sources.mkdir(parents=True)
    file_sources = sources / "files"
    file_sources.mkdir(parents=True)

    (file_sources / "trusted-trusted.md").write_text(TRUSTED_MARKDOWN, encoding="utf-8")
    (file_sources / "short-review.md").write_text(SHORT_MARKDOWN, encoding="utf-8")
    archive = gate_root / "chatgpt-export.zip"
    make_archive_fixture(archive)

    init_vault(vault, category_template="minimal")
    configure_v02_vault(
        vault,
        swallow_ingest=True,
        transition_output=True,
        web_ingest=True,
        ocr=False,
        asr=False,
    )

    folder_result = run_m3_ingest_pipeline(vault, file_sources, recursive=False)
    if folder_result.written_revisions < 1:
        raise RuntimeError(f"expected trusted revisions from folder ingest: {folder_result}")

    archive_result = run_m3_archive_ingest_pipeline(vault, archive)
    if archive_result.written_revisions < 2:
        raise RuntimeError(f"expected archive child revisions: {archive_result}")

    trusted_hits = search_count(vault, TRUSTED_NEEDLE)
    if trusted_hits < 1:
        raise RuntimeError(f"trusted fixture must be searchable; hits={trusted_hits}")

    review_hits = search_count(vault, REVIEW_NEEDLE)
    if review_hits != 0:
        raise RuntimeError(f"review-before-current fixture must not be in default search; hits={review_hits}")

    connection = connect(vault_paths(vault).db_path)
    try:
        review_row = connection.execute(
            """
            SELECT d.doc_id, d.current_revision_id, cr.promotion_status
            FROM documents d
            JOIN converter_runs cr ON cr.doc_id = d.doc_id
            WHERE d.normalized_source_uri LIKE '%short-review.md'
            """
        ).fetchone()
        if review_row is None:
            raise RuntimeError("short-review document missing")
        if review_row["current_revision_id"] is not None:
            raise RuntimeError("short-review must not have a current revision")
        if review_row["promotion_status"] != "review-before-current":
            raise RuntimeError(f"unexpected promotion status: {review_row['promotion_status']}")

        archive_children = connection.execute(
            """
            SELECT doc_id, source_type, current_revision_id
            FROM documents
            WHERE source_type = 'chatgpt_conversation'
            ORDER BY title
            """
        ).fetchall()
        if len(archive_children) != 2:
            raise RuntimeError(f"expected 2 archive child docs, got {len(archive_children)}")
        parent_items = connection.execute(
            """
            SELECT ingest_item_id, status, parent_ingest_item_id
            FROM ingest_items
            WHERE parent_ingest_item_id IS NOT NULL
            """
        ).fetchall()
        if len(parent_items) != 2:
            raise RuntimeError(f"expected 2 child ingest items, got {len(parent_items)}")

    finally:
        connection.close()

    url_result = run_m3_url_ingest_pipeline(vault, "https://example.com/articles/gate-fixture")
    if url_result.written_revisions < 1:
        raise RuntimeError(f"URL ingest did not produce a revision: {url_result}")
    if search_count(vault, TRUSTED_NEEDLE) < 2:
        raise RuntimeError("URL fixture revision should be searchable")

    connection = connect(vault_paths(vault).db_path)
    try:
        url_doc = connection.execute(
            """
            SELECT source_snapshot_path, access_context
            FROM documents
            WHERE source_type = 'url'
            """
        ).fetchone()
        if not url_doc or not url_doc["source_snapshot_path"]:
            raise RuntimeError("URL ingest missing source_snapshot_path")
        snapshot = vault_paths(vault).root / str(url_doc["source_snapshot_path"])
        if not snapshot.is_file():
            raise RuntimeError(f"URL snapshot artifact missing: {snapshot}")

        trusted_doc_id = connection.execute(
            """
            SELECT doc_id, current_revision_id
            FROM documents
            WHERE normalized_source_uri LIKE '%trusted-trusted.md'
            """
        ).fetchone()["doc_id"]
        rev_before = connection.execute(
            "SELECT COUNT(*) AS count FROM document_revisions WHERE doc_id = ?",
            (trusted_doc_id,),
        ).fetchone()["count"]

        export_result = export_source_revision(
            connection,
            vault,
            doc_id=str(trusted_doc_id),
            bridge_runner=run_fake_bridge,
        )
        if export_result.status not in {"succeeded", "partial"}:
            raise RuntimeError(f"export failed: {export_result}")

        export_dir = vault_paths(vault).outputs_exports / export_result.output_run_id
        normalized = export_dir / "normalized.md"
        if not normalized.is_file():
            raise RuntimeError("export missing normalized.md")

        export_hits = search_count(vault, TRUSTED_NEEDLE)
        normalize_result = normalize_replace_current(
            connection,
            vault,
            doc_id=str(trusted_doc_id),
            bridge_runner=run_fake_bridge,
        )
        if normalize_result.status != "succeeded":
            raise RuntimeError(f"normalize failed: {normalize_result}")
        rev_after = connection.execute(
            "SELECT COUNT(*) AS count FROM document_revisions WHERE doc_id = ?",
            (trusted_doc_id,),
        ).fetchone()["count"]
        if rev_after != rev_before + 1:
            raise RuntimeError("normalize must create exactly one new revision")
        current = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (trusted_doc_id,),
        ).fetchone()["current_revision_id"]
        if not str(current).endswith("_0002"):
            raise RuntimeError(f"normalize must advance current revision, got {current}")
    finally:
        connection.close()

    assert_doctor_clean(vault)

    summary = {
        "layer": "C",
        "gate": "v02_deterministic_release",
        "folder_ingest_status": folder_result.status,
        "trusted_search_hits": trusted_hits,
        "review_search_hits": review_hits,
        "archive_child_documents": 2,
        "archive_ingest_status": archive_result.status,
        "url_revision_written": url_result.written_revisions,
        "export_run_id": export_result.output_run_id,
        "normalize_revision_id": normalize_result.created_revision_id,
        "doctor": doctor_hard_metrics(vault),
    }
    write_gate_summary(summary, gate_name="V02_DETERMINISTIC_RELEASE_GATE", gate_root=gate_root)


if __name__ == "__main__":
    main()
