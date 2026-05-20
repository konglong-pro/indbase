from pathlib import Path

from indbase_core.db import connect
from indbase_core.output_queries import get_output_run, list_output_runs
from indbase_core.output_service import export_source_revision
from indbase_core.transition_adapter import run_fake_bridge
from indbase_core.transition_runtime import install_runtime
from indbase_core.vault import init_vault
from conftest_output import insert_minimal_document


def test_list_and_show_output_run(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    install_runtime(vault, run_npm_install=False)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc_id = insert_minimal_document(connection, vault, body="# Title\n\nBody.\n")
        result = export_source_revision(
            connection,
            vault,
            doc_id=doc_id,
            bridge_runner=run_fake_bridge,
        )
        rows = list_output_runs(connection, source_doc_id=doc_id)
        assert len(rows) == 1
        view = get_output_run(connection, result.output_run_id)
        assert view is not None
        assert view.evidence_manifest_path is not None
        assert any(artifact.format == "md" and artifact.status == "succeeded" for artifact in view.artifacts)
    finally:
        connection.close()
