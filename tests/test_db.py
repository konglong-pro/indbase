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
        "0006_swallow_ingest_integration",
        "0007_transition_output_integration",
        "0008_taxonomy_foundation",
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
            "0006_swallow_ingest_integration",
            "0007_transition_output_integration",
            "0008_taxonomy_foundation",
        ]
        assert "document_profiles" in tables
        assert "feature_atoms" in tables
        assert "tag_candidates" in tables
        assert "taxonomy_suggestions" in tables
        assert "tag_lifecycle_events" in tables
        assert "model_calls" in tables
        tag_columns = {row["name"] for row in connection.execute("PRAGMA table_info(tags)")}
        assert {"type", "status", "created_by"} <= tag_columns
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
        converter_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(converter_runs)")
        }
        assert {
            "external_job_id",
            "external_trace_path",
            "external_manifest_path",
            "primary_worker",
            "worker_chain_json",
            "candidate_path",
            "artifact_manifest_json",
            "promotion_status",
            "promotion_reason",
        } <= converter_columns
        chunk_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(chunks)")
        }
        assert "source_locator_json" in chunk_columns
        document_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(documents)")
        }
        assert {
            "access_context",
            "privacy_flags_json",
            "source_snapshot_path",
            "category_source",
            "category_suggestion_id",
            "category_updated_by",
            "category_updated_at",
        } <= document_columns
    finally:
        connection.close()


def test_initialize_database_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "vault" / ".indbase" / "db.sqlite"
    initialize_database(db_path)

    applied = initialize_database(db_path)

    assert applied == []
