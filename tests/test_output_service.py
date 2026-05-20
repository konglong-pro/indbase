from pathlib import Path

import pytest

from indbase_core.chunker import chunk_markdown_body
from indbase_core.db import connect, initialize_database
from indbase_core.output_service import export_source_revision, normalize_replace_current
from indbase_core.paths import vault_paths
from indbase_core.transition_adapter import run_fake_bridge
from indbase_core.transition_runtime import install_runtime
from indbase_core.vault import init_vault
from conftest_output import insert_minimal_document


@pytest.fixture
def transition_vault(tmp_path: Path):
    vault = tmp_path / "vault"
    init_vault(vault, category_template="minimal")
    install_runtime(vault, run_npm_install=False)
    connection = connect(vault_paths(vault).db_path)
    try:
        yield vault, connection
    finally:
        connection.close()


def test_migration_includes_output_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    applied = initialize_database(db_path)
    assert "0007_transition_output_integration" in applied
    connection = connect(db_path)
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "output_runs" in tables
        assert "output_artifacts" in tables
        assert "output_sources" in tables
    finally:
        connection.close()


def test_export_source_writes_normalized_md(transition_vault) -> None:
    vault, connection = transition_vault
    doc_id = insert_minimal_document(connection, vault, body="# Hello\n\n```py\nx = 1\n```\n")
    result = export_source_revision(
        connection,
        vault,
        doc_id=doc_id,
        bridge_runner=run_fake_bridge,
    )
    assert result.status == "succeeded"
    export_file = vault_paths(vault).outputs_exports / result.output_run_id / "normalized.md"
    assert export_file.is_file()
    assert "```py" in export_file.read_text(encoding="utf-8")


def test_export_archives_evidence(transition_vault) -> None:
    vault, connection = transition_vault
    doc_id = insert_minimal_document(connection, vault, body="# Hello\n\n```py\npass\n```\n")
    result = export_source_revision(
        connection,
        vault,
        doc_id=doc_id,
        bridge_runner=run_fake_bridge,
    )
    evidence_manifest = (
        vault_paths(vault).output_run_evidence_dir(result.output_run_id) / "transition_manifest.json"
    )
    assert evidence_manifest.is_file()
    row = connection.execute(
        "SELECT evidence_manifest_path, config_hash, input_hash FROM output_runs WHERE output_run_id = ?",
        (result.output_run_id,),
    ).fetchone()
    assert row["evidence_manifest_path"]
    assert row["config_hash"]
    assert row["input_hash"]


def test_normalize_records_exact_locator_mappings(transition_vault) -> None:
    import json

    vault, connection = transition_vault
    body = "# Hello\n\nstable chunk text.\n"
    doc_id = insert_minimal_document(connection, vault, body=body)
    revision_id_value = "rev_doc_20250101_abc123_0001"
    chunk = chunk_markdown_body(body)[0]
    connection.execute(
        """
        INSERT INTO chunks (
          chunk_id, doc_id, revision_id, sequence, heading_path_json, text,
          token_count, content_hash, is_current, created_at, updated_at
        ) VALUES ('chk_old1', ?, ?, 1, ?, ?, 3, 'hash', 1, '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')
        """,
        (
            doc_id,
            revision_id_value,
            json.dumps(list(chunk.heading_path)),
            chunk.text,
        ),
    )
    connection.commit()
    result = normalize_replace_current(connection, vault, doc_id=doc_id, bridge_runner=run_fake_bridge)
    exact_mappings = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM output_sources
        WHERE output_run_id = ?
          AND mapping_confidence = 'exact'
        """,
        (result.output_run_id,),
    ).fetchone()["count"]
    assert int(exact_mappings) >= 1


def test_normalize_creates_new_revision(transition_vault) -> None:
    vault, connection = transition_vault
    doc_id = insert_minimal_document(connection, vault, body="# Hello\n\nplain text.\n")
    result = normalize_replace_current(connection, vault, doc_id=doc_id, bridge_runner=run_fake_bridge)
    assert result.status == "succeeded"
    current = connection.execute(
        "SELECT current_revision_id FROM documents WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    assert str(current["current_revision_id"]).endswith("_0002")
