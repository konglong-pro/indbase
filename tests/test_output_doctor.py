from pathlib import Path

from indbase_core.config import load_config, save_config
from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.output_service import export_source_revision
from indbase_core.transition_adapter import run_fake_bridge
from indbase_core.transition_runtime import install_runtime
from indbase_core.vault import init_vault
from conftest_output import insert_minimal_document


def test_doctor_reports_missing_runtime_when_flag_enabled(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    config = load_config(vault / ".indbase" / "config" / "config.toml")
    from dataclasses import replace

    save_config(replace(config, features=replace(config.features, transition_output=True)), vault / ".indbase" / "config" / "config.toml")
    report = run_doctor(vault)
    codes = {finding.code for finding in report.findings}
    assert "transition_runtime_missing" in codes


def test_doctor_detects_artifact_hash_mismatch(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    install_runtime(vault, run_npm_install=False)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc_id = insert_minimal_document(connection, vault, body="# Hi\n\nThere.\n")
        result = export_source_revision(
            connection,
            vault,
            doc_id=doc_id,
            bridge_runner=run_fake_bridge,
        )
        row = connection.execute(
            "SELECT path FROM output_artifacts WHERE output_run_id = ? AND format = 'md'",
            (result.output_run_id,),
        ).fetchone()
        (vault / row["path"]).write_text("corrupted\n", encoding="utf-8")
        connection.commit()
    finally:
        connection.close()
    report = run_doctor(vault)
    codes = {finding.code for finding in report.findings}
    assert "output_artifact_hash_mismatch" in codes
