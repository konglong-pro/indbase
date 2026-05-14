from pathlib import Path

from indbase_core.db import connect
from indbase_core.ingest import plan_ingest_sources
from indbase_core.vault import init_vault


def test_plan_ingest_marks_supported_items_pending(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\n", encoding="utf-8")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = plan_ingest_sources(connection, source)
        run = connection.execute(
            "SELECT status, total_items, unsupported_items, review_items_count FROM ingest_runs WHERE ingest_id = ?",
            (result.ingest_id,),
        ).fetchone()
        item = connection.execute(
            "SELECT status, source_uri, normalized_source_uri FROM ingest_items WHERE ingest_id = ?",
            (result.ingest_id,),
        ).fetchone()
        reviews = connection.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE target_type = 'ingest_item'"
        ).fetchone()
    finally:
        connection.close()

    assert result.status == "pending"
    assert result.supported_items == 1
    assert result.unsupported_items == 0
    assert run["status"] == "pending"
    assert run["total_items"] == 1
    assert run["unsupported_items"] == 0
    assert run["review_items_count"] == 0
    assert item["status"] == "pending"
    assert item["source_uri"] == str(source)
    assert item["normalized_source_uri"].endswith("/note.md")
    assert reviews["count"] == 0


def test_plan_ingest_records_unsupported_item_and_review(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "image.png"
    source.write_bytes(b"png-unsupported")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = plan_ingest_sources(connection, source)
        run = connection.execute(
            "SELECT status, total_items, unsupported_items, review_items_count FROM ingest_runs WHERE ingest_id = ?",
            (result.ingest_id,),
        ).fetchone()
        item = connection.execute(
            "SELECT ingest_item_id, status, finished_at FROM ingest_items WHERE ingest_id = ?",
            (result.ingest_id,),
        ).fetchone()
        review = connection.execute(
            """
            SELECT type, target_type, target_id, status, reason
            FROM review_items
            WHERE target_type = 'ingest_item'
            """
        ).fetchone()
    finally:
        connection.close()

    assert result.status == "completed_with_issues"
    assert result.supported_items == 0
    assert result.unsupported_items == 1
    assert run["status"] == "completed_with_issues"
    assert run["total_items"] == 1
    assert run["unsupported_items"] == 1
    assert run["review_items_count"] == 1
    assert item["status"] == "unsupported"
    assert item["finished_at"] is not None
    assert review["type"] == "unsupported_source"
    assert review["target_id"] == item["ingest_item_id"]
    assert review["status"] == "pending"
    assert ".png" in review["reason"]


def test_plan_folder_with_supported_and_unsupported_items(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "note.txt").write_text("hello", encoding="utf-8")
    (sources / "image.png").write_bytes(b"png")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = plan_ingest_sources(connection, sources, recursive=False)
        statuses = [
            row["status"]
            for row in connection.execute(
                "SELECT status FROM ingest_items WHERE ingest_id = ? ORDER BY source_uri",
                (result.ingest_id,),
            )
        ]
        reviews = connection.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE type = 'unsupported_source'"
        ).fetchone()
    finally:
        connection.close()

    assert result.status == "pending"
    assert result.total_items == 2
    assert result.supported_items == 1
    assert result.unsupported_items == 1
    assert statuses == ["unsupported", "pending"]
    assert reviews["count"] == 1
