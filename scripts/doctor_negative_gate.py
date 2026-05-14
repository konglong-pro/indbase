"""Corrupt temporary vaults and verify doctor catches each invariant break."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import subprocess


ROOT = Path.cwd()
IND_B = ROOT / ".venv" / "Scripts" / "indb.exe"


def main() -> None:
    root = ROOT / ".tmp" / f"doctor-negative-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    def run_indb(expected: int, *args: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [str(IND_B), *args],
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if completed.returncode != expected:
            raise RuntimeError(
                f"indb {' '.join(args)} exited {completed.returncode}, "
                f"expected {expected}\n{completed.stdout}"
            )
        return completed

    def make_vault(name: str) -> tuple[Path, dict[str, str]]:
        scenario_root = root / name
        vault = scenario_root / "vault"
        source = scenario_root / "source.md"
        scenario_root.mkdir(parents=True)
        source.write_text("# Negative\nDoctor negative search body.\n", encoding="utf-8")
        run_indb(0, "init", str(vault), "--category-template", "minimal")
        run_indb(0, "ingest", str(source), "--vault", str(vault))
        with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT d.doc_id, d.current_revision_id, d.canonical_path, d.original_path,
                       dr.markdown_path
                FROM documents d
                JOIN document_revisions dr ON dr.revision_id = d.current_revision_id
                """
            ).fetchone()
            data = {key: str(row[key]) for key in row.keys()}
        return vault, data

    def doctor_codes(vault: Path) -> tuple[int, list[dict[str, str]]]:
        completed = subprocess.run(
            [str(IND_B), "doctor", "--vault", str(vault), "--json"],
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        payload = json.loads(completed.stdout)
        return completed.returncode, list(payload["findings"])

    def assert_doctor_finds(vault: Path, expected_codes: set[str]) -> list[dict[str, str]]:
        exit_code, findings = doctor_codes(vault)
        codes = {str(finding["code"]) for finding in findings}
        missing = expected_codes - codes
        if exit_code != 2 or missing:
            raise RuntimeError(
                f"doctor failed negative check for {vault.name}: "
                f"exit={exit_code}, missing={sorted(missing)}, findings={findings}"
            )
        bad_severity = [
            finding
            for finding in findings
            if finding["code"] in expected_codes and finding["severity"] not in {"error", "critical"}
        ]
        if bad_severity:
            raise RuntimeError(f"doctor severity too weak for {vault.name}: {bad_severity}")
        return findings

    scenarios: list[tuple[str, set[str], list[dict[str, str]]]] = []

    vault, data = make_vault("missing-original")
    (vault / data["original_path"]).unlink()
    scenarios.append(("missing_original_file", {"missing_original_file"}, assert_doctor_finds(vault, {"missing_original_file"})))

    vault, data = make_vault("missing-source-markdown")
    (vault / data["canonical_path"]).unlink()
    scenarios.append(
        (
            "missing_source_markdown",
            {"missing_canonical_markdown", "missing_revision_markdown"},
            assert_doctor_finds(vault, {"missing_canonical_markdown", "missing_revision_markdown"}),
        )
    )

    vault, data = make_vault("missing-current-revision-record")
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM document_revisions WHERE revision_id = ?", (data["current_revision_id"],))
        connection.commit()
    scenarios.append(
        (
            "missing_current_revision_record",
            {"missing_current_revision", "orphan_chunk"},
            assert_doctor_finds(vault, {"missing_current_revision", "orphan_chunk"}),
        )
    )

    vault, data = make_vault("missing-current-chunks")
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute("DELETE FROM chunks WHERE revision_id = ?", (data["current_revision_id"],))
        connection.commit()
    scenarios.append(
        (
            "missing_current_chunks",
            {"missing_current_chunks", "fts_stale_row"},
            assert_doctor_finds(vault, {"missing_current_chunks", "fts_stale_row"}),
        )
    )

    vault, _data = make_vault("fts-cleared")
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute("DELETE FROM chunks_fts")
        connection.commit()
    scenarios.append(("fts_cleared", {"fts_missing_chunk"}, assert_doctor_finds(vault, {"fts_missing_chunk"})))

    vault, _data = make_vault("orphan-original-file")
    orphan_original = vault / ".indbase" / "originals" / "2099" / "01" / "doc_orphan" / "original.txt"
    orphan_original.parent.mkdir(parents=True)
    orphan_original.write_text("orphan", encoding="utf-8")
    scenarios.append(
        (
            "orphan_original_file",
            {"orphan_original_file"},
            assert_doctor_finds(vault, {"orphan_original_file"}),
        )
    )

    vault, _data = make_vault("orphan-markdown-file")
    orphan_markdown = vault / "sources" / "2099" / "01" / "orphan__doc_20990101_deadbe__rev_0001.md"
    orphan_markdown.parent.mkdir(parents=True)
    orphan_markdown.write_text("# Orphan\n", encoding="utf-8")
    scenarios.append(
        (
            "orphan_markdown_file",
            {"orphan_markdown_file"},
            assert_doctor_finds(vault, {"orphan_markdown_file"}),
        )
    )

    vault, _data = make_vault("orphan-db-row")
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO source_files(source_file_id, doc_id, original_path, created_at)
            VALUES ('source_file_orphan', 'doc_missing', NULL, '2099-01-01T00:00:00+00:00')
            """
        )
        connection.commit()
    scenarios.append(("orphan_db_row", {"orphan_source_file"}, assert_doctor_finds(vault, {"orphan_source_file"})))

    vault, data = make_vault("archived-active-fts-desync")
    run_indb(0, "doc", "archive", data["doc_id"], "--vault", str(vault))
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute("DELETE FROM chunks_fts WHERE doc_id = ?", (data["doc_id"],))
        connection.execute(
            "UPDATE documents SET status = 'active', archived_at = NULL WHERE doc_id = ?",
            (data["doc_id"],),
        )
        connection.commit()
    scenarios.append(
        (
            "archived_active_fts_desync",
            {"fts_missing_chunk"},
            assert_doctor_finds(vault, {"fts_missing_chunk"}),
        )
    )

    vault, data = make_vault("nonexistent-current-revision")
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute(
            "UPDATE documents SET current_revision_id = 'rev_doc_missing_0001' WHERE doc_id = ?",
            (data["doc_id"],),
        )
        connection.commit()
    scenarios.append(
        (
            "nonexistent_current_revision",
            {"missing_current_revision", "missing_current_chunks", "fts_stale_row"},
            assert_doctor_finds(vault, {"missing_current_revision", "missing_current_chunks", "fts_stale_row"}),
        )
    )

    all_findings = [finding for _name, _expected, findings in scenarios for finding in findings]
    summary = {
        "scenarios": len(scenarios),
        "passed": len(scenarios),
        "expected_codes": sorted({code for _name, codes, _findings in scenarios for code in codes}),
        "detected_codes": sorted({str(finding["code"]) for finding in all_findings}),
        "orphan_records_detected": sum(
            1
            for finding in all_findings
            if str(finding["code"]) in {"orphan_source_file", "orphan_revision", "orphan_chunk"}
        ),
        "orphan_files_detected": sum(1 for finding in all_findings if str(finding["code"]) in {"orphan_original_file", "orphan_markdown_file"}),
        "fts_desync_detected": sum(1 for finding in all_findings if str(finding["code"]).startswith("fts_")),
    }
    print(json.dumps(summary, sort_keys=True))
    print("DOCTOR_NEGATIVE_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


if __name__ == "__main__":
    main()
