"""Run the M5 catalog/review/archive gate against a temporary vault."""

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
    root = ROOT / ".tmp" / f"m5-catalog-review-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    vault = root / "vault"
    sources = root / "sources"
    sources.mkdir(parents=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    def write_text(name: str, text: str) -> None:
        (sources / name).write_text(text, encoding="utf-8")

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

    def assert_output_contains(completed: subprocess.CompletedProcess[str], text: str) -> None:
        if text not in completed.stdout:
            raise RuntimeError(f"missing {text!r} in output\n{completed.stdout}")

    def search_count(query: str) -> int:
        completed = run_indb(0, "search", query, "--vault", str(vault), "--json")
        return int(json.loads(completed.stdout)["result_count"])

    write_text("active.md", "# Active\nM5 catalog review archive needle is searchable.")
    write_text("second.txt", "M5 secondary document keeps the active document list non-empty.")
    write_text("empty.txt", "")
    write_text("unsupported.png", "PNG unsupported")

    run_indb(0, "init", str(vault), "--category-template", "minimal")
    run_indb(1, "ingest", str(sources), "--vault", str(vault), "--recursive")

    category = run_indb(0, "catalog", "add", "M5 Gate", "--vault", str(vault))
    assert_output_contains(category, "M5 Gate")

    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute(
            "SELECT doc_id FROM documents WHERE normalized_source_uri LIKE ?",
            ("%active.md",),
        ).fetchone()[0]
        category_id = connection.execute(
            "SELECT category_id FROM categories WHERE name = 'M5 Gate'",
        ).fetchone()[0]

    run_indb(0, "doc", "set-category", doc_id, category_id, "--vault", str(vault))
    run_indb(0, "doc", "add-tag", doc_id, "m5-gate", "--vault", str(vault))

    active_list = run_indb(0, "doc", "list", "--vault", str(vault))
    assert_output_contains(active_list, f"doc: {doc_id} status=active")
    assert_output_contains(active_list, "M5 Gate")
    assert_output_contains(active_list, "m5-gate")
    category_list = run_indb(0, "doc", "list", "--category-id", category_id, "--vault", str(vault))
    assert_output_contains(category_list, f"doc: {doc_id} status=active")
    tag_list = run_indb(0, "doc", "list", "--tag", "m5-gate", "--vault", str(vault))
    assert_output_contains(tag_list, f"doc: {doc_id} status=active")

    assigned_archive = run_indb(1, "catalog", "archive", category_id, "--vault", str(vault))
    assert_output_contains(assigned_archive, "still assigned")
    run_indb(0, "doc", "set-category", doc_id, "cat_uncategorized", "--vault", str(vault))
    run_indb(0, "catalog", "update", category_id, "--name", "M5 Gate Renamed", "--vault", str(vault))
    run_indb(0, "catalog", "archive", category_id, "--vault", str(vault))
    hidden_category_list = run_indb(0, "catalog", "list", "--vault", str(vault))
    if "M5 Gate Renamed" in hidden_category_list.stdout:
        raise RuntimeError(f"archived category leaked into active list\n{hidden_category_list.stdout}")
    inactive_category_list = run_indb(0, "catalog", "list", "--include-inactive", "--vault", str(vault))
    assert_output_contains(inactive_category_list, "M5 Gate Renamed")
    run_indb(0, "catalog", "restore", category_id, "--vault", str(vault))
    run_indb(0, "doc", "set-category", doc_id, category_id, "--vault", str(vault))

    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        tag_id = connection.execute("SELECT tag_id FROM tags WHERE normalized_name = 'm5-gate'").fetchone()[0]

    run_indb(0, "tag", "update", tag_id, "--name", "m5-gate-renamed", "--language", "en", "--vault", str(vault))
    doc_tags_before = run_indb(0, "doc", "tags", doc_id, "--vault", str(vault))
    assert_output_contains(doc_tags_before, "m5-gate-renamed")
    run_indb(0, "tag", "archive", tag_id, "--vault", str(vault))
    doc_tags_archived = run_indb(0, "doc", "tags", doc_id, "--vault", str(vault))
    assert_output_contains(doc_tags_archived, "No document tags")
    tag_filtered_archived = run_indb(0, "doc", "list", "--tag", "m5-gate-renamed", "--vault", str(vault))
    assert_output_contains(tag_filtered_archived, "No documents")
    inactive_tag_list = run_indb(0, "tag", "list", "--include-inactive", "--vault", str(vault))
    assert_output_contains(inactive_tag_list, "m5-gate-renamed")
    run_indb(0, "tag", "restore", tag_id, "--vault", str(vault))
    doc_tags_restored = run_indb(0, "doc", "tags", doc_id, "--vault", str(vault))
    assert_output_contains(doc_tags_restored, "m5-gate-renamed")

    review_filter = run_indb(0, "review", "list", "--type", "unsupported_source", "--vault", str(vault))
    assert_output_contains(review_filter, "unsupported_source")
    error_list = run_indb(0, "error", "list", "--vault", str(vault))
    assert_output_contains(error_list, "no_extractable_content")

    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        review_ids = [
            row[0]
            for row in connection.execute(
                "SELECT review_id FROM review_items WHERE status = 'pending' ORDER BY created_at, review_id",
            )
        ]
    if len(review_ids) != 2:
        raise RuntimeError(f"expected 2 pending reviews, got {review_ids!r}")
    run_indb(
        0,
        "review",
        "resolve-many",
        *review_ids,
        "--note",
        "expected M5 gate fixtures",
        "--resolved-by",
        "m5-gate",
        "--vault",
        str(vault),
    )
    review_all = run_indb(0, "review", "list", "--status", "all", "--vault", str(vault))
    assert_output_contains(review_all, "expected M5 gate fixtures")
    pending_reviews = run_indb(0, "review", "list", "--vault", str(vault))
    assert_output_contains(pending_reviews, "No review items")

    if search_count("archive needle") != 1:
        raise RuntimeError("search did not find active document before archive")
    run_indb(0, "doc", "archive", doc_id, "--vault", str(vault))
    archived_list = run_indb(0, "doc", "list", "--status", "archived", "--vault", str(vault))
    assert_output_contains(archived_list, f"doc: {doc_id} status=archived")
    active_after_archive = run_indb(0, "doc", "list", "--vault", str(vault))
    if f"doc: {doc_id} status=active" in active_after_archive.stdout:
        raise RuntimeError(f"archived document leaked into active list\n{active_after_archive.stdout}")
    if search_count("archive needle") != 0:
        raise RuntimeError("archive did not filter default search")
    run_indb(0, "doc", "restore", doc_id, "--vault", str(vault))
    active_after_restore = run_indb(0, "doc", "list", "--vault", str(vault))
    assert_output_contains(active_after_restore, f"doc: {doc_id} status=active")
    if search_count("archive needle") != 1:
        raise RuntimeError("restore did not restore default search")

    run_indb(0, "index", "rebuild", "--fts", "--vault", str(vault))
    doctor = run_indb(0, "doctor", "--vault", str(vault), "--json")
    doctor_report = json.loads(doctor.stdout)
    critical_doctor_findings = [
        finding
        for finding in doctor_report["findings"]
        if finding["severity"] in {"error", "critical"}
    ]
    if critical_doctor_findings:
        raise RuntimeError(f"critical doctor findings in M5 gate: {critical_doctor_findings}")

    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        summary = {
            "documents": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "active_documents": connection.execute(
                "SELECT COUNT(*) FROM documents WHERE status = 'active' AND deleted_at IS NULL",
            ).fetchone()[0],
            "archived_documents": connection.execute(
                "SELECT COUNT(*) FROM documents WHERE status = 'archived' AND deleted_at IS NULL",
            ).fetchone()[0],
            "current_chunks": connection.execute("SELECT COUNT(*) FROM chunks WHERE is_current = 1").fetchone()[0],
            "fts": connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0],
            "pending_reviews": connection.execute(
                "SELECT COUNT(*) FROM review_items WHERE status = 'pending'",
            ).fetchone()[0],
            "resolved_reviews": connection.execute(
                "SELECT COUNT(*) FROM review_items WHERE status = 'resolved'",
            ).fetchone()[0],
            "errors": connection.execute("SELECT COUNT(*) FROM errors").fetchone()[0],
            "manual_tags": connection.execute(
                "SELECT COUNT(*) FROM document_tags WHERE deleted_at IS NULL",
            ).fetchone()[0],
            "critical_doctor_findings": len(critical_doctor_findings),
        }

    print(json.dumps(summary, sort_keys=True))
    print("M5_CATALOG_REVIEW_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


if __name__ == "__main__":
    main()
