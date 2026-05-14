from pathlib import Path

from indbase_core.config import load_config
from indbase_core.db import connect
from indbase_core.vault import init_vault


def test_init_vault_creates_layout_config_db_categories_and_task(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"

    result = init_vault(vault_path, category_template="minimal")

    assert result.vault_path == vault_path
    assert result.db_path.is_file()
    assert result.config_path.is_file()
    assert result.applied_migrations == (
        "0001_initial",
        "0002_review_resolution_metadata",
        "0003_v02_data_substrate",
        "0004_classification_feedback_audit",
        "0005_candidate_cards",
    )
    assert result.inserted_categories == 6

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
        assert categories["count"] == 6
        assert task["status"] == "succeeded"
        assert events["count"] >= 4
    finally:
        connection.close()


def test_init_vault_is_idempotent_for_template_categories(tmp_path: Path) -> None:
    vault_path = tmp_path / "vault"
    init_vault(vault_path, category_template="minimal")

    result = init_vault(vault_path, category_template="minimal")

    assert result.applied_migrations == ()
    assert result.inserted_categories == 0
