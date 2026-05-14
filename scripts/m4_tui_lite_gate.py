"""Run the M4 TUI-lite gate against a temporary vault."""

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
    root = ROOT / ".tmp" / f"m4-tui-lite-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
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

    write_text("searchable.md", "# Searchable\nM4 TUI lite search panel verifies source snippets.")
    write_text(
        "chinese.md",
        "# \u4e2d\u6587\u77e5\u8bc6\n"
        "\u5927\u8bed\u8a00\u6a21\u578b\u9700\u8981\u77e5\u8bc6\u6570\u636e\u5e93\u548c\u5e7b\u89c9\u63a7\u5236\u3002",
    )
    write_text("empty.txt", "")
    write_text("unsupported.png", "PNG unsupported")

    init = run_indb(0, "tui", "--action", "init", "--vault", str(vault))
    assert_output_contains(init, "TUI-lite Init")

    ingest = run_indb(
        1,
        "tui",
        "--action",
        "ingest-wizard",
        "--source",
        str(sources),
        "--recursive",
        "--vault",
        str(vault),
    )
    assert_output_contains(ingest, "TUI-lite Ingest Result")
    assert_output_contains(ingest, "completed_with_issues")

    dashboard = run_indb(0, "tui", "--action", "dashboard", "--vault", str(vault))
    assert_output_contains(dashboard, "indbase TUI-lite Dashboard")
    assert_output_contains(dashboard, "documents_active")

    search = run_indb(
        0,
        "tui",
        "--action",
        "search-panel",
        "--query",
        "TUI lite search",
        "--vault",
        str(vault),
    )
    assert_output_contains(search, "TUI-lite Search: TUI lite search")
    assert_output_contains(search, "snippets")

    cjk_search = run_indb(
        0,
        "tui",
        "--action",
        "search-panel",
        "--query",
        "\u5927\u8bed\u8a00\u6a21\u578b",
        "--vault",
        str(vault),
    )
    assert_output_contains(cjk_search, "TUI-lite Search")

    task_queue = run_indb(0, "tui", "--action", "task-queue", "--vault", str(vault))
    assert_output_contains(task_queue, "TUI-lite Task Queue")

    review_list = run_indb(0, "tui", "--action", "review-list", "--vault", str(vault))
    assert_output_contains(review_list, "TUI-lite Review List")
    assert_output_contains(review_list, "unsupported_source")

    error_viewer = run_indb(0, "tui", "--action", "error-viewer", "--vault", str(vault))
    assert_output_contains(error_viewer, "TUI-lite Error Viewer")
    assert_output_contains(error_viewer, "no_extractable_content")

    settings = run_indb(0, "tui", "--action", "settings-summary", "--vault", str(vault))
    assert_output_contains(settings, "TUI-lite Settings Summary")
    assert_output_contains(settings, "features.ask")

    category = run_indb(0, "catalog", "add", "M4 Gate", "--vault", str(vault))
    assert_output_contains(category, "M4 Gate")

    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute(
            "SELECT doc_id FROM documents WHERE normalized_source_uri LIKE ?",
            ("%searchable.md",),
        ).fetchone()[0]
        category_id = connection.execute(
            "SELECT category_id FROM categories WHERE name = 'M4 Gate'",
        ).fetchone()[0]

    run_indb(0, "doc", "set-category", doc_id, category_id, "--vault", str(vault))
    run_indb(0, "tag", "add", "m4-gate", "--vault", str(vault))
    run_indb(0, "doc", "add-tag", doc_id, "m4-gate", "--vault", str(vault))
    doc_tags = run_indb(0, "doc", "tags", doc_id, "--vault", str(vault))
    assert_output_contains(doc_tags, "m4-gate")
    revisions = run_indb(0, "doc", "revisions", doc_id, "--vault", str(vault))
    assert_output_contains(revisions, "__rev_0001.md")

    doctor = run_indb(1, "doctor", "--vault", str(vault), "--json")
    doctor_report = json.loads(doctor.stdout)
    critical_doctor_findings = [
        finding
        for finding in doctor_report["findings"]
        if finding["severity"] in {"error", "critical"}
    ]
    if critical_doctor_findings:
        raise RuntimeError(f"critical doctor findings in M4 gate: {critical_doctor_findings}")

    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        summary = {
            "documents": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "current_chunks": connection.execute("SELECT COUNT(*) FROM chunks WHERE is_current = 1").fetchone()[0],
            "fts": connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0],
            "pending_reviews": connection.execute(
                "SELECT COUNT(*) FROM review_items WHERE status = 'pending'"
            ).fetchone()[0],
            "errors": connection.execute("SELECT COUNT(*) FROM errors").fetchone()[0],
            "tasks": connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            "manual_tags": connection.execute("SELECT COUNT(*) FROM document_tags WHERE deleted_at IS NULL").fetchone()[0],
            "critical_doctor_findings": len(critical_doctor_findings),
        }

    print(json.dumps(summary, sort_keys=True))
    print("M4_TUI_LITE_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


if __name__ == "__main__":
    main()
