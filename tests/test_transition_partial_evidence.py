from pathlib import Path

import pytest

from indbase_core.db import connect
from indbase_core.output_queries import get_output_run
from indbase_core.output_service import export_source_revision
from indbase_core.transition_adapter import run_fake_bridge_failed_with_evidence
from indbase_core.transition_runtime import install_runtime
from indbase_core.vault import init_vault
from indbase_core.paths import vault_paths
from conftest_output import insert_minimal_document


def test_failed_export_preserves_partial_evidence(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    install_runtime(vault, run_npm_install=False)
    connection = connect(vault_paths(vault).db_path)
    try:
        doc_id = insert_minimal_document(connection, vault, body="# Title\n\nBody.\n")
        with pytest.raises(Exception):
            export_source_revision(
                connection,
                vault,
                doc_id=doc_id,
                bridge_runner=run_fake_bridge_failed_with_evidence,
            )
        row = connection.execute(
            """
            SELECT output_run_id, status, evidence_manifest_path
            FROM output_runs ORDER BY created_at DESC LIMIT 1
            """,
        ).fetchone()
        assert row["status"] == "failed"
        assert row["evidence_manifest_path"]
        evidence_file = vault_paths(vault).root / row["evidence_manifest_path"]
        assert evidence_file.is_file()
        export_dir = vault_paths(vault).outputs_exports / str(row["output_run_id"])
        assert not export_dir.exists() or not any(export_dir.iterdir())
        hashes_path = evidence_file.parent / "input_hashes.json"
        assert hashes_path.is_file()
        assert '"partial": true' in hashes_path.read_text(encoding="utf-8")
    finally:
        connection.close()


def test_failed_export_no_export_artifacts(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    install_runtime(vault, run_npm_install=False)
    connection = connect(vault_paths(vault).db_path)
    try:
        doc_id = insert_minimal_document(connection, vault, body="# X\n\nY.\n")
        with pytest.raises(Exception):
            export_source_revision(
                connection,
                vault,
                doc_id=doc_id,
                bridge_runner=run_fake_bridge_failed_with_evidence,
            )
        run = connection.execute(
            "SELECT output_run_id FROM output_runs ORDER BY created_at DESC LIMIT 1",
        ).fetchone()
        export_dir = vault_paths(vault).outputs_exports / str(run["output_run_id"])
        assert not export_dir.exists() or not any(export_dir.iterdir())
        view = get_output_run(connection, str(run["output_run_id"]))
        assert view is not None
        assert not any(artifact.format == "md" and artifact.status == "succeeded" for artifact in view.artifacts)
    finally:
        connection.close()
