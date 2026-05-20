"""v0.2 doctor negative gate: corrupt a healthy vault and assert hard findings."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

from gate_common import (
    TRUSTED_MARKDOWN,
    TRUSTED_NEEDLE,
    assert_doctor_clean,
    configure_v02_vault,
    copy_vault,
    install_deterministic_swallow_stub,
    write_gate_summary,
    ROOT,
)

install_deterministic_swallow_stub()

from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.output_service import export_source_revision
from indbase_core.paths import vault_paths
from indbase_core.transition_adapter import run_fake_bridge
from indbase_core.vault import init_vault


def build_healthy_v02_vault(root: Path) -> dict[str, str]:
    vault = root / "healthy" / "vault"
    source = root / "healthy" / "trusted.md"
    source.parent.mkdir(parents=True)
    source.write_text(TRUSTED_MARKDOWN, encoding="utf-8")
    init_vault(vault, category_template="minimal")
    configure_v02_vault(vault, swallow_ingest=True, transition_output=True, web_ingest=False)
    result = run_m3_ingest_pipeline(vault, source, recursive=False)
    if result.written_revisions != 1:
        raise RuntimeError(f"healthy vault ingest failed: {result}")

    connection = connect(vault_paths(vault).db_path)
    try:
        row = connection.execute(
            """
            SELECT d.doc_id, d.current_revision_id, d.canonical_path, d.original_path,
                   dr.markdown_path, dr.revision_id
            FROM documents d
            JOIN document_revisions dr ON dr.revision_id = d.current_revision_id
            """
        ).fetchone()
        export_source_revision(
            connection,
            vault,
            doc_id=str(row["doc_id"]),
            bridge_runner=run_fake_bridge,
        )
        connection.commit()
        data = {key: str(row[key]) for key in row.keys()}
    finally:
        connection.close()

    assert_doctor_clean(vault)
    return data


def assert_doctor_finds(vault: Path, expected_codes: set[str]) -> list[str]:
    report = run_doctor(vault)
    codes = {finding.code for finding in report.findings}
    missing = expected_codes - codes
    if report.exit_code != 2 or missing:
        raise RuntimeError(
            f"doctor negative check failed for {vault.name}: "
            f"exit={report.exit_code}, missing={sorted(missing)}, findings={report.to_dict()['findings']}"
        )
    weak = [
        finding.code
        for finding in report.findings
        if finding.code in expected_codes and finding.severity not in {"error", "critical"}
    ]
    if weak:
        raise RuntimeError(f"doctor severity too weak for {vault.name}: {weak}")
    return sorted(codes & expected_codes)


def main() -> None:
    gate_root = ROOT / ".tmp" / f"v02-doctor-negative-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    gate_root.mkdir(parents=True)
    healthy_root = gate_root / "baseline"
    healthy_data = build_healthy_v02_vault(healthy_root)
    scenarios: list[tuple[str, set[str], list[str]]] = []

    def scenario(name: str, expected: set[str], corrupt) -> None:
        vault = copy_vault(healthy_root / "healthy" / "vault", gate_root / name / "vault")
        corrupt(vault, healthy_data)
        detected = assert_doctor_finds(vault, expected)
        scenarios.append((name, expected, detected))

    scenario(
        "missing_original",
        {"missing_original_file"},
        lambda vault, data: (vault / data["original_path"]).unlink(),
    )
    scenario(
        "missing_source_markdown",
        {"missing_canonical_markdown", "missing_revision_markdown"},
        lambda vault, data: (vault / data["canonical_path"]).unlink(),
    )
    scenario(
        "missing_current_revision_record",
        {"missing_current_revision", "orphan_chunk"},
        lambda vault, data: _delete_revision_row(vault, data["current_revision_id"]),
    )
    scenario(
        "missing_current_chunks",
        {"missing_current_chunks", "fts_stale_row"},
        lambda vault, data: _delete_current_chunks(vault, data["current_revision_id"]),
    )
    scenario(
        "fts_cleared",
        {"fts_missing_chunk"},
        lambda vault, _data: _clear_fts(vault),
    )
    scenario(
        "output_artifact_hash_mismatch",
        {"output_artifact_hash_mismatch"},
        lambda vault, _data: _corrupt_export_artifact(vault),
    )
    scenario(
        "orphan_markdown_file",
        {"orphan_markdown_file"},
        lambda vault, _data: _add_orphan_markdown(vault),
    )
    scenario(
        "orphan_db_row",
        {"orphan_source_file"},
        lambda vault, _data: _add_orphan_source_file_row(vault),
    )

    summary = {
        "layer": "C-negative",
        "gate": "v02_doctor_negative",
        "scenarios": len(scenarios),
        "expected_codes": sorted({code for _name, codes, _detected in scenarios for code in codes}),
        "detected_codes": sorted({code for _name, _codes, detected in scenarios for code in detected}),
    }
    write_gate_summary(summary, gate_name="V02_DOCTOR_NEGATIVE_GATE", gate_root=gate_root)


def _delete_revision_row(vault: Path, revision_id: str) -> None:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM document_revisions WHERE revision_id = ?", (revision_id,))
        connection.commit()


def _delete_current_chunks(vault: Path, revision_id: str) -> None:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute("DELETE FROM chunks WHERE revision_id = ?", (revision_id,))
        connection.commit()


def _clear_fts(vault: Path) -> None:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute("DELETE FROM chunks_fts")
        connection.commit()


def _corrupt_export_artifact(vault: Path) -> None:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT path FROM output_artifacts
            WHERE format = 'md' AND status = 'succeeded'
            ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("healthy vault missing export artifact for corruption scenario")
    target = vault / str(row["path"])
    target.write_text("corrupted-by-negative-gate\n", encoding="utf-8")


def _add_orphan_markdown(vault: Path) -> None:
    orphan = vault / "sources" / "2099" / "01" / "orphan__doc_20990101_deadbe__rev_0001.md"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("# Orphan\n", encoding="utf-8")


def _add_orphan_source_file_row(vault: Path) -> None:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO source_files(source_file_id, doc_id, original_path, created_at)
            VALUES ('source_file_orphan_gate', 'doc_missing_gate', NULL, '2099-01-01T00:00:00+00:00')
            """
        )
        connection.commit()


if __name__ == "__main__":
    main()
