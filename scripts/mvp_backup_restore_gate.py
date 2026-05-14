"""Validate local vault copy backup and restore behavior."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3

from mvp_closeout_common import ROOT, directory_size, run_indb


def main() -> None:
    root = ROOT / ".tmp" / f"mvp-backup-restore-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    original_vault = root / "vault-original"
    restored_vault = root / "vault-restored"
    sources = root / "sources"
    sources.mkdir()
    (sources / "backup.md").write_text(
        "# Backup Source\nbackup restore doctor search translation candidate card source binding.\n",
        encoding="utf-8",
    )
    (sources / "backup-notes.txt").write_text("backup restore local vault copy smoke\n", encoding="utf-8")

    run_indb(0, "init", str(original_vault))
    run_indb(0, "ingest", str(sources), "--recursive", "--vault", str(original_vault))
    run_indb(0, "index", "rebuild", "--fts", "--vault", str(original_vault))
    run_indb(0, "index", "rebuild", "--vectors", "--vault", str(original_vault))
    doc = _workflow_document(original_vault)
    translation = json.loads(
        run_indb(
            0,
            "translate",
            "document",
            doc["doc_id"],
            "--revision",
            doc["revision_id"],
            "--target-language",
            "zh",
            "--vault",
            str(original_vault),
            "--json",
        ).stdout
    )
    card = json.loads(run_indb(0, "card", "generate", doc["doc_id"], "--vault", str(original_vault), "--json").stdout)
    accepted = json.loads(
        run_indb(0, "card", "accept", card["candidate_card_id"], "--vault", str(original_vault), "--json").stdout
    )
    run_indb({0, 1}, "doctor", "--vault", str(original_vault), "--json")

    shutil.copytree(original_vault, restored_vault)
    run_indb(0, "index", "rebuild", "--fts", "--vault", str(restored_vault))
    run_indb(0, "index", "rebuild", "--vectors", "--vault", str(restored_vault))
    search = json.loads(run_indb(0, "search", "backup restore", "--vault", str(restored_vault), "--json").stdout)
    hybrid = json.loads(
        run_indb(0, "search", "backup restore", "--mode", "hybrid", "--vault", str(restored_vault), "--json").stdout
    )
    doctor = json.loads(run_indb({0, 1}, "doctor", "--vault", str(restored_vault), "--json").stdout)
    markdown_path = _printed_path(
        run_indb(0, "doc", "open", doc["doc_id"], "--vault", str(restored_vault), "--print-path").stdout
    )
    original_path = _printed_path(
        run_indb(
            0,
            "doc",
            "open",
            doc["doc_id"],
            "--vault",
            str(restored_vault),
            "--original",
            "--print-path",
        ).stdout
    )
    translation_path = _printed_path(
        run_indb(
            0,
            "translate",
            "open",
            translation["translation_id"],
            "--vault",
            str(restored_vault),
            "--print-path",
        ).stdout
    )
    accepted_note_path = restored_vault / accepted["accepted_note_path"]

    before = _counts(original_vault)
    after = _counts(restored_vault)
    summary = {
        "counts_match_after_restore": int(before == after),
        "search_results": int(search["result_count"]),
        "hybrid_results": int(hybrid["result_count"]),
        "critical_doctor_findings": sum(
            1 for finding in doctor["findings"] if finding["severity"] in {"error", "critical"}
        ),
        "doctor_exit_code": doctor["exit_code"],
        "markdown_open_exists": int(markdown_path.is_file()),
        "original_open_exists": int(original_path.is_file()),
        "translation_open_exists": int(translation_path.is_file()),
        "accepted_note_exists": int(accepted_note_path.is_file()),
        "original_vault_size_bytes": directory_size(original_vault),
        "restored_vault_size_bytes": directory_size(restored_vault),
        **{f"restored_{key}": value for key, value in after.items()},
    }
    hard_zero = {"critical_doctor_findings": summary["critical_doctor_findings"]}
    hard_positive = {
        "counts_match_after_restore": summary["counts_match_after_restore"],
        "search_results": summary["search_results"],
        "hybrid_results": summary["hybrid_results"],
        "markdown_open_exists": summary["markdown_open_exists"],
        "original_open_exists": summary["original_open_exists"],
        "translation_open_exists": summary["translation_open_exists"],
        "accepted_note_exists": summary["accepted_note_exists"],
    }
    if any(int(value) != 0 for value in hard_zero.values()) or any(
        int(value) <= 0 for value in hard_positive.values()
    ):
        raise RuntimeError(f"backup/restore validation failed: zero={hard_zero}; positive={hard_positive}")

    (root / "backup_restore_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    print("MVP_BACKUP_RESTORE_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _workflow_document(vault: Path) -> dict[str, str]:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT d.doc_id, d.current_revision_id AS revision_id
            FROM documents d
            WHERE d.normalized_source_uri LIKE '%backup.md'
              AND d.current_revision_id IS NOT NULL
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("backup workflow document not found")
    return {"doc_id": str(row["doc_id"]), "revision_id": str(row["revision_id"])}


def _counts(vault: Path) -> dict[str, int]:
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        return {
            "documents": _count(connection, "SELECT COUNT(*) FROM documents WHERE deleted_at IS NULL"),
            "revisions": _count(connection, "SELECT COUNT(*) FROM document_revisions WHERE deleted_at IS NULL"),
            "chunks": _count(connection, "SELECT COUNT(*) FROM chunks WHERE deleted_at IS NULL"),
            "fts_rows": _count(connection, "SELECT COUNT(*) FROM chunks_fts"),
            "embeddings": _count(connection, "SELECT COUNT(*) FROM embeddings WHERE deleted_at IS NULL"),
            "translations": _count(connection, "SELECT COUNT(*) FROM translations WHERE deleted_at IS NULL"),
            "candidate_cards": _count(connection, "SELECT COUNT(*) FROM candidate_cards WHERE deleted_at IS NULL"),
            "accepted_notes": len(list((vault / "notes" / "atomic").rglob("*.md"))),
        }


def _count(connection: sqlite3.Connection, query: str) -> int:
    return int(connection.execute(query).fetchone()[0] or 0)


def _printed_path(stdout: str) -> Path:
    return Path("".join(line.strip() for line in stdout.splitlines() if line.strip()))


if __name__ == "__main__":
    main()
