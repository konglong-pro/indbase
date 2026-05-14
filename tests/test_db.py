from pathlib import Path

from indbase_core.db import connect, initialize_database


def test_initialize_database_applies_initial_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "vault" / ".indbase" / "db.sqlite"

    applied = initialize_database(db_path)

    assert applied == [
        "0001_initial",
        "0002_review_resolution_metadata",
        "0003_v02_data_substrate",
        "0004_classification_feedback_audit",
        "0005_candidate_cards",
    ]

    connection = connect(db_path)
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            )
        }
        assert "schema_migrations" in tables
        assert "documents" in tables
        assert "tasks" in tables
        assert "errors" in tables
        assert "review_items" in tables
        assert "chunks_fts" in tables
        assert "ocr_pages" in tables
        assert "embeddings" in tables
        assert "classification_suggestions" in tables
        assert "classification_feedback" in tables
        assert "executions" in tables
        assert "translations" in tables
        assert "candidate_cards" in tables
        assert "candidate_card_sources" in tables
        versions = [row["version"] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert versions == [
            "0001_initial",
            "0002_review_resolution_metadata",
            "0003_v02_data_substrate",
            "0004_classification_feedback_audit",
            "0005_candidate_cards",
        ]
        review_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(review_items)")
        }
        assert "resolution_note" in review_columns
        assert "resolved_by" in review_columns
        ocr_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(ocr_pages)")
        }
        assert {"doc_id", "revision_id", "page_number", "confidence", "quality_status"} <= ocr_columns
        embedding_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(embeddings)")
        }
        assert {"chunk_id", "provider", "model", "dimension", "vector_ref", "status"} <= embedding_columns
        feedback_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(classification_feedback)")
        }
        assert {"suggestion_id", "revision_id", "action", "forced_category"} <= feedback_columns
        card_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(candidate_cards)")
        }
        assert {"source_doc_id", "source_revision_id", "claims_json", "status", "accepted_note_path"} <= card_columns
        source_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(candidate_card_sources)")
        }
        assert {"candidate_card_id", "source_doc_id", "source_revision_id", "source_chunk_id", "claim_id"} <= source_columns
    finally:
        connection.close()


def test_initialize_database_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "vault" / ".indbase" / "db.sqlite"
    initialize_database(db_path)

    applied = initialize_database(db_path)

    assert applied == []
