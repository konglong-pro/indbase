"""Verify metadata, archive/restore, FTS, re-ingest, and duplicate consistency."""

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
    root = ROOT / ".tmp" / f"metadata-consistency-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    vault = root / "vault"
    sources = root / "sources with spaces"
    sources.mkdir(parents=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    source = sources / "知识 metadata.md"
    source.write_text("# Metadata\nMetadata body needle before change.\n", encoding="utf-8")

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

    def search_count(query: str) -> int:
        completed = run_indb(0, "search", query, "--vault", str(vault), "--json")
        return int(json.loads(completed.stdout)["result_count"])

    def assert_contains(completed: subprocess.CompletedProcess[str], text: str) -> None:
        if text not in completed.stdout:
            raise RuntimeError(f"missing {text!r} in output\n{completed.stdout}")

    def db_scalar(sql: str, params: tuple[object, ...] = ()) -> object:
        with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
            return connection.execute(sql, params).fetchone()[0]

    def fts_row(doc_id: str) -> tuple[str, str] | None:
        with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
            row = connection.execute(
                "SELECT tags, category FROM chunks_fts WHERE doc_id = ? LIMIT 1",
                (doc_id,),
            ).fetchone()
            return None if row is None else (str(row[0] or ""), str(row[1] or ""))

    run_indb(0, "init", str(vault), "--category-template", "indbase_default_v1")
    run_indb(0, "ingest", str(source), "--vault", str(vault))

    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()[0]
        original_revision = connection.execute("SELECT current_revision_id FROM documents").fetchone()[0]

    run_indb(0, "catalog", "add", "QuantumCatalog", "--vault", str(vault))
    category_id = str(db_scalar("SELECT category_id FROM categories WHERE name = 'QuantumCatalog'"))
    run_indb(0, "doc", "set-category", doc_id, category_id, "--vault", str(vault))
    run_indb(0, "doc", "add-tag", doc_id, "MetadataTag", "--vault", str(vault))

    if search_count("QuantumCatalog") != 1 or search_count("MetadataTag") != 1:
        raise RuntimeError("category/tag additions were not reflected in FTS search")
    row = fts_row(doc_id)
    if row is None or "metadatatag" not in row[0] or "quantumcatalog" not in row[1]:
        raise RuntimeError(f"FTS metadata row did not update after add/set: {row}")

    tag_id = str(db_scalar("SELECT tag_id FROM tags WHERE normalized_name = 'metadatatag'"))
    run_indb(0, "catalog", "update", category_id, "--name", "RenamedCatalog", "--vault", str(vault))
    run_indb(0, "tag", "update", tag_id, "--name", "RenamedTag", "--vault", str(vault))
    if search_count("QuantumCatalog") != 0 or search_count("MetadataTag") != 0:
        raise RuntimeError("old category/tag terms remained searchable after rename")
    if search_count("RenamedCatalog") != 1 or search_count("RenamedTag") != 1:
        raise RuntimeError("renamed category/tag terms were not searchable")
    row = fts_row(doc_id)
    if row is None or "renamedtag" not in row[0] or "renamedcatalog" not in row[1]:
        raise RuntimeError(f"FTS metadata row did not update after rename: {row}")

    active_category = run_indb(0, "doc", "list", "--category-id", category_id, "--vault", str(vault))
    active_tag = run_indb(0, "doc", "list", "--tag", "RenamedTag", "--vault", str(vault))
    assert_contains(active_category, f"doc: {doc_id} status=active")
    assert_contains(active_tag, f"doc: {doc_id} status=active")

    run_indb(0, "doc", "archive", doc_id, "--vault", str(vault))
    if int(db_scalar("SELECT COUNT(*) FROM chunks_fts WHERE doc_id = ?", (doc_id,))) == 0:
        raise RuntimeError("archive deleted FTS rows")
    if search_count("body needle") != 0:
        raise RuntimeError("archived document leaked into default search")
    archived_category = run_indb(0, "doc", "list", "--status", "archived", "--category-id", category_id, "--vault", str(vault))
    archived_tag = run_indb(0, "doc", "list", "--status", "archived", "--tag", "RenamedTag", "--vault", str(vault))
    assert_contains(archived_category, f"doc: {doc_id} status=archived")
    assert_contains(archived_tag, f"doc: {doc_id} status=archived")

    run_indb(0, "doc", "restore", doc_id, "--vault", str(vault))
    if search_count("body needle") != 1:
        raise RuntimeError("restore did not make retained FTS rows searchable")

    run_indb(0, "doc", "archive", doc_id, "--vault", str(vault))
    run_indb(0, "index", "rebuild", "--fts", "--vault", str(vault))
    if search_count("body needle") != 0:
        raise RuntimeError("index rebuild made archived document searchable")
    run_indb(0, "doc", "restore", doc_id, "--vault", str(vault))
    if search_count("body needle") != 1:
        raise RuntimeError("restore after archived rebuild did not refresh FTS rows")

    source.write_text("# Metadata\nMetadata body needle after change with reingest marker.\n", encoding="utf-8")
    run_indb(0, "ingest", str(source), "--vault", str(vault))
    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        doc = connection.execute(
            "SELECT doc_id, current_revision_id, category_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        revision_count = connection.execute(
            "SELECT COUNT(*) FROM document_revisions WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()[0]
        linked_tag_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM document_tags
            WHERE doc_id = ?
              AND tag_id = ?
              AND deleted_at IS NULL
            """,
            (doc_id, tag_id),
        ).fetchone()[0]
    if doc[0] != doc_id or doc[1] == original_revision or doc[2] != category_id:
        raise RuntimeError(f"re-ingest did not preserve doc/category/current revision correctly: {doc}")
    if int(revision_count) != 2 or int(linked_tag_count) != 1:
        raise RuntimeError("re-ingest did not preserve revisions and manual tag metadata")
    if search_count("reingest marker") != 1 or search_count("RenamedTag") != 1:
        raise RuntimeError("re-ingest did not update content FTS while preserving metadata search")

    duplicate = sources / "renamed duplicate.md"
    duplicate.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    run_indb(1, "ingest", str(duplicate), "--vault", str(vault))
    if int(db_scalar("SELECT COUNT(*) FROM documents")) != 1:
        raise RuntimeError("same-content renamed duplicate created a new document")

    run_indb(0, "index", "rebuild", "--fts", "--vault", str(vault))
    if search_count("reingest marker") != 1 or search_count("RenamedCatalog") != 1 or search_count("RenamedTag") != 1:
        raise RuntimeError("index rebuild changed metadata/content search semantics")

    doctor = run_indb(0, "doctor", "--vault", str(vault), "--json")
    doctor_report = json.loads(doctor.stdout)
    critical_doctor_findings = [
        finding
        for finding in doctor_report["findings"]
        if finding["severity"] in {"error", "critical"}
    ]
    if critical_doctor_findings:
        raise RuntimeError(f"critical doctor findings in metadata gate: {critical_doctor_findings}")

    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        summary = {
            "documents": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "revisions": connection.execute("SELECT COUNT(*) FROM document_revisions").fetchone()[0],
            "current_chunks": connection.execute("SELECT COUNT(*) FROM chunks WHERE is_current = 1").fetchone()[0],
            "fts": connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0],
            "manual_tags": connection.execute("SELECT COUNT(*) FROM document_tags WHERE deleted_at IS NULL").fetchone()[0],
            "duplicate_items": connection.execute("SELECT COUNT(*) FROM ingest_items WHERE status = 'duplicate'").fetchone()[0],
            "critical_doctor_findings": len(critical_doctor_findings),
            "zero_chunk_current_revisions": connection.execute(
                """
                SELECT COUNT(*)
                FROM documents d
                WHERE d.current_revision_id IS NOT NULL
                  AND d.deleted_at IS NULL
                  AND NOT EXISTS (
                    SELECT 1
                    FROM chunks c
                    WHERE c.doc_id = d.doc_id
                      AND c.revision_id = d.current_revision_id
                      AND c.is_current = 1
                      AND c.deleted_at IS NULL
                  )
                """
            ).fetchone()[0],
            "unsupported_documents_created": connection.execute(
                "SELECT COUNT(*) FROM documents WHERE source_type IN ('png')"
            ).fetchone()[0],
        }

    print(json.dumps(summary, sort_keys=True))
    print("METADATA_CONSISTENCY_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


if __name__ == "__main__":
    main()
