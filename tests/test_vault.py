from pathlib import Path

from indbase_core.config import load_config
from indbase_core.db import connect
from indbase_core.vault import init_vault


def test_init_vault_creates_layout_config_db_categories_and_task(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"

    result = init_vault(vault_path, category_template="indbase_default_v1")

    assert result.vault_path == vault_path
    assert result.db_path.is_file()
    assert result.config_path.is_file()
    assert result.applied_migrations == (
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
        "0013_provider_failure_class",
        "0014_source_fts_lineage",
    )
    assert result.inserted_categories == 10

    config = load_config(result.config_path)
    assert config.vault_path == vault_path

    connection = connect(result.db_path)
    try:
        categories = connection.execute("SELECT COUNT(*) AS count FROM categories").fetchone()
        task = connection.execute(
            "SELECT status FROM tasks WHERE task_id = ?",
            (result.task_id,),
        ).fetchone()
        events = connection.execute(
            "SELECT COUNT(*) AS count FROM task_events WHERE task_id = ?",
            (result.task_id,),
        ).fetchone()
        assert categories["count"] == 10
        profile_count = connection.execute("SELECT COUNT(*) AS count FROM category_profiles").fetchone()
        assert profile_count["count"] == 10
        assert task["status"] == "succeeded"
        assert events["count"] >= 4
    finally:
        connection.close()


def test_init_vault_is_idempotent_for_template_categories(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    init_vault(vault_path, category_template="indbase_default_v1")

    result = init_vault(vault_path, category_template="indbase_default_v1")

    assert result.applied_migrations == ()
    assert result.inserted_categories == 0
