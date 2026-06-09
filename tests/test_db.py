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
        "0008_v031_taxonomy_category_foundation",
        "0009_retrieval_intelligence",
        "0010_retrieval_evaluation",
        "0011_v032_tag_governance_foundation",
        "0012_provider_evidence",
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
        assert "provider_runs" in tables
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
            "0008_v031_taxonomy_category_foundation",
            "0009_retrieval_intelligence",
            "0010_retrieval_evaluation",
            "0011_v032_tag_governance_foundation",
            "0012_provider_evidence",
        ]
        assert "category_profiles" in tables
        assert "category_classification_runs" in tables
        review_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(review_items)")
        }
        assert "resolution_note" in review_columns
        assert "resolved_by" in review_columns
        assert {"ingest_run_id", "provider_run_id"} <= review_columns
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
            "adopted_provider_run_id",
        } <= converter_columns
        provider_run_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(provider_runs)")
        }
        assert {
            "provider_run_id",
            "operation_id",
            "action_id",
            "provider_id",
            "provider_version",
            "capability_id",
            "transport_profile",
            "provider_status",
            "evidence_status",
            "evidence_root",
        } <= provider_run_columns
        ingest_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(ingest_runs)")
        }
        assert "adopted_provider_run_id" in ingest_columns
        output_run_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(output_runs)")
        }
        assert "adopted_provider_run_id" in output_run_columns
        output_artifact_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(output_artifacts)")
        }
        assert {"artifact_role", "trust_level"} <= output_artifact_columns
        error_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(errors)")
        }
        assert "provider_run_id" in error_columns
        chunk_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(chunks)")
        }
        assert "source_locator_json" in chunk_columns
        document_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(documents)")
        }
        assert {"access_context", "privacy_flags_json", "source_snapshot_path"} <= document_columns
    finally:
        connection.close()


def test_initialize_database_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "vault" / ".indbase" / "db.sqlite"
    initialize_database(db_path)

    applied = initialize_database(db_path)

    assert applied == []
