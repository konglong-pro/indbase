"""Historical v0.1 M3 dogfood gate (compatibility only — not a v0.2 release blocker).

This script encodes pre-v0.2 assumptions:
- default vault keeps swallow_ingest=false
- ingest immediately writes revisions and becomes searchable

Under v0.2 swallow rules those assumptions are intentionally false:
- swallow_ingest=false -> legacy_conversion_retired
- partial/low-quality candidates -> review-before-current (not searchable)

Use scripts/v02_release_gate.py for the active release gate stack.
"""

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
    print("HISTORICAL_V01_M3_DOGFOOD_GATE=compatibility_only")
    print("NOTE=not_a_v02_release_blocker; use scripts/v02_release_gate.py")
    root = ROOT / ".tmp" / f"m3-dogfood-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
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

    def run_indb_loose(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(IND_B), *args],
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def search_json(query: str) -> dict[str, object]:
        completed = run_indb(0, "search", query, "--vault", str(vault), "--json")
        return json.loads(completed.stdout)

    def assert_search(query: str, *, require_snippet_text: bool = True) -> None:
        payload = search_json(query)
        if int(payload["result_count"]) < 1:
            raise RuntimeError(f"search failed for {query!r}: {payload}")
        results = payload["results"]
        if not isinstance(results, list) or not results:
            raise RuntimeError(f"missing results for {query!r}: {payload}")
        hit = results[0]
        for key in ("doc_id", "revision_id", "chunk_id", "snippet"):
            if not hit.get(key):
                raise RuntimeError(f"missing {key} for {query!r}: {payload}")
        if require_snippet_text and query not in str(hit["snippet"]):
            raise RuntimeError(f"snippet missing {query!r}: {hit['snippet']!r}")

    write_text("english.md", "# English Knowledge\nLocal knowledge database stores source snippets and reliable revisions.")
    write_text(
        "chinese.md",
        "# \u4e2d\u6587\u77e5\u8bc6\n"
        "\u5927\u8bed\u8a00\u6a21\u578b\u53ef\u4ee5\u8fdb\u5165\u77e5\u8bc6\u6570\u636e\u5e93"
        "\uff0c\u4f46\u5fc5\u987b\u4fdd\u7559\u5e7b\u89c9\u63a7\u5236\u8bc1\u636e\u3002",
    )
    write_text(
        "japanese.md",
        "# \u65e5\u672c\u8a9e\u30ce\u30fc\u30c8\n"
        "\u5927\u898f\u6a21\u8a00\u8a9e\u30e2\u30c7\u30eb\u3092\u77e5\u8b58\u7ba1\u7406"
        "\u306b\u4f7f\u3046\u5834\u5408\u306f\u6839\u62e0\u3092\u78ba\u8a8d\u3059\u308b\u3002",
    )
    write_text("notes.txt", "plain text local search needle")
    write_text("page.html", "<html><body><h1>HTML Source</h1><p>Browser export knowledge.</p></body></html>")
    write_text("table.csv", "name,value\nalpha,1\nbeta,2")
    write_text("data.json", '{"topic":"knowledge database","status":"local"}')
    write_text("data2.json", '[{"name":"source"},{"name":"revision"}]')
    write_text("long.md", "# Long\n" + "chunkable paragraph with source citation. " * 80)
    write_text("empty.txt", "")
    (sources / "garbled.txt").write_bytes(bytes([0xFF, 0xFE, 0x00, 0x61, 0x00, 0x62, 0x00]))
    write_text("office.docx", "fake docx body for MarkItDown best effort")
    write_text("sheet.xlsx", "fake xlsx body for MarkItDown best effort")
    write_text("slides.pptx", "fake pptx body for MarkItDown best effort")
    write_text("unsupported.gif", "GIF unsupported")
    write_text("unsupported.png", "PNG unsupported")
    write_text("topic-01.md", "# Topic 01\nalpha dogfood one")
    write_text("topic-02.md", "# Topic 02\nalpha dogfood two")
    write_text("topic-03.txt", "alpha dogfood three")
    write_text("topic-04.html", "<h1>Topic Four</h1><p>alpha dogfood four</p>")
    write_text("topic-05.csv", "k,v\ndogfood,5")
    write_text("topic-06.json", '{"dogfood":6}')

    run_indb(0, "init", str(vault), "--category-template", "indbase_default_v1")
    run_indb(1, "ingest", str(sources), "--vault", str(vault), "--recursive")

    assert_search("\u5927\u8bed\u8a00\u6a21\u578b")
    assert_search("\u5e7b\u89c9\u63a7\u5236")
    assert_search("\u5927\u898f\u6a21\u8a00\u8a9e\u30e2\u30c7\u30eb")
    assert_search("local search needle", require_snippet_text=False)

    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute(
            "select doc_id from documents where normalized_source_uri like ?",
            ("%english.md",),
        ).fetchone()[0]
    run_indb(0, "doc", "archive", doc_id, "--vault", str(vault))
    if search_json("reliable revisions")["result_count"] != 0:
        raise RuntimeError("archive did not filter default search")
    run_indb(0, "doc", "show", doc_id, "--vault", str(vault))
    run_indb(0, "doc", "open", doc_id, "--print-path", "--vault", str(vault))
    run_indb(0, "doc", "restore", doc_id, "--vault", str(vault))
    if int(search_json("reliable revisions")["result_count"]) < 1:
        raise RuntimeError("restore did not restore default search")

    write_text(
        "chinese.md",
        "# \u4e2d\u6587\u77e5\u8bc6\n"
        "\u5927\u8bed\u8a00\u6a21\u578b\u53ef\u4ee5\u8fdb\u5165\u77e5\u8bc6\u6570\u636e\u5e93"
        "\uff0c\u65b0\u589e\u53ef\u9a8c\u8bc1\u5f15\u7528\u8ffd\u8e2a\u3002",
    )
    run_indb(0, "ingest", str(sources / "chinese.md"), "--vault", str(vault))
    assert_search("\u53ef\u9a8c\u8bc1\u5f15\u7528\u8ffd\u8e2a")

    (sources / "english-duplicate.md").write_text(
        (sources / "english.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    run_indb(1, "ingest", str(sources / "english-duplicate.md"), "--vault", str(vault))

    index_rebuild = run_indb_loose("index", "rebuild", "--fts", "--vault", str(vault))
    if index_rebuild.returncode not in (0, 1):
        raise RuntimeError(f"index rebuild exited {index_rebuild.returncode}\n{index_rebuild.stdout}")
    assert_search("\u5927\u898f\u6a21\u8a00\u8a9e\u30e2\u30c7\u30eb")

    doctor = run_indb_loose("doctor", "--vault", str(vault), "--json")
    if doctor.returncode not in (0, 1):
        raise RuntimeError(f"doctor exited {doctor.returncode}\n{doctor.stdout}")
    doctor_report = json.loads(doctor.stdout)
    run_indb(0, "review", "list", "--vault", str(vault))
    run_indb(0, "error", "list", "--vault", str(vault))
    run_indb(0, "task", "list", "--vault", str(vault))

    with sqlite3.connect(vault / ".indbase" / "db.sqlite") as connection:
        errors = [
            {
                "component": row[0],
                "error_type": row[1],
                "payload": json.loads(row[2]) if row[2] else {},
            }
            for row in connection.execute("select component, error_type, payload_json from errors")
        ]
        expected_errors = [
            error
            for error in errors
            if _is_expected_dogfood_error(error)
        ]
        unexpected_errors = [
            error
            for error in errors
            if not _is_expected_dogfood_error(error)
        ]
        critical_doctor_findings = [
            finding
            for finding in doctor_report["findings"]
            if finding["severity"] in {"error", "critical"}
        ]
        index_integrity_errors = 0 if index_rebuild.returncode in (0, 1) else 1
        summary = {
            "documents": connection.execute("select count(*) from documents").fetchone()[0],
            "revisions": connection.execute("select count(*) from document_revisions").fetchone()[0],
            "current_chunks": connection.execute("select count(*) from chunks where is_current = 1").fetchone()[0],
            "fts": connection.execute("select count(*) from chunks_fts").fetchone()[0],
            "unsupported_items": connection.execute("select count(*) from ingest_items where status = 'unsupported'").fetchone()[0],
            "duplicate_items": connection.execute("select count(*) from ingest_items where status = 'duplicate'").fetchone()[0],
            "failed_items": connection.execute("select count(*) from ingest_items where status = 'failed'").fetchone()[0],
            "reviews": connection.execute("select count(*) from review_items").fetchone()[0],
            "errors": connection.execute("select count(*) from errors").fetchone()[0],
            "tasks": connection.execute("select count(*) from tasks").fetchone()[0],
            "search_queries": connection.execute("select count(*) from search_queries").fetchone()[0],
            "citations": connection.execute("select count(*) from citations").fetchone()[0],
            "expected_reviews": connection.execute(
                "select count(*) from review_items where type = 'unsupported_source'"
            ).fetchone()[0],
            "expected_failures": len(expected_errors),
            "unexpected_errors": len(unexpected_errors),
            "critical_doctor_findings": len(critical_doctor_findings),
            "index_integrity_errors": index_integrity_errors,
        }
    if summary["unexpected_errors"]:
        raise RuntimeError(f"unexpected errors in dogfood gate: {unexpected_errors}")
    if summary["critical_doctor_findings"]:
        raise RuntimeError(f"critical doctor findings in dogfood gate: {critical_doctor_findings}")
    if summary["index_integrity_errors"]:
        raise RuntimeError(f"index integrity errors in dogfood gate: {index_rebuild.stdout}")
    print(json.dumps(summary, sort_keys=True))
    print(f"INDEX_REBUILD_EXIT={index_rebuild.returncode}")
    print(f"DOCTOR_EXIT={doctor.returncode}")
    print(f"DOGFOOD_ROOT={root}")
    print("HISTORICAL_V01_M3_DOGFOOD_GATE=finished")


def _is_expected_dogfood_error(error: dict[str, object]) -> bool:
    if error["error_type"] == "no_extractable_content":
        return True
    payload = error.get("payload")
    if isinstance(payload, dict) and payload.get("source_type") in {"docx", "xlsx", "pptx", "pdf"}:
        return error.get("component") == "conversion"
    return False


if __name__ == "__main__":
    main()
