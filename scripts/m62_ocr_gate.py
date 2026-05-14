"""Run the M6.2 OCR v0 gate against temporary vaults."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess

import indbase_core.normalizers as normalizers
from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.ocr import run_ocr_for_document
from indbase_core.search import search_chunks
from indbase_core.vault import init_vault


ROOT = Path.cwd()
IND_B = ROOT / ".venv" / "Scripts" / "indb.exe"


def main() -> None:
    root = ROOT / ".tmp" / f"m62-ocr-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    original_markitdown = normalizers._run_markitdown_file
    try:
        success = _run_ocr_success(root / "success")
        low_confidence = _run_ocr_low_confidence(root / "low-confidence")
        failure = _run_ocr_failure(root / "failure")
    finally:
        normalizers._run_markitdown_file = original_markitdown

    doctor = subprocess.run(
        [str(IND_B), "doctor", "--vault", str(root / "success" / "vault"), "--json"],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if doctor.returncode not in {0, 1}:
        raise RuntimeError(f"doctor failed after OCR gate: {doctor.returncode}\n{doctor.stdout}")
    doctor_report = json.loads(doctor.stdout)
    ocr_availability_findings = [
        finding
        for finding in doctor_report["findings"]
        if str(finding["code"]).startswith("ocr_")
    ]
    if not ocr_availability_findings:
        raise RuntimeError(f"doctor did not report OCR availability: {doctor_report}")

    summary = {
        **success,
        **low_confidence,
        **failure,
        "doctor_ocr_availability_findings": len(ocr_availability_findings),
    }
    print(json.dumps(summary, sort_keys=True))
    print("M62_OCR_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _make_failed_pdf(vault: Path, source: Path) -> tuple[str, str]:
    init_vault(vault)
    source.write_bytes(b"%PDF image only")
    normalizers._run_markitdown_file = lambda _path: " "
    result = run_m3_ingest_pipeline(vault, source)
    if result.status != "completed_with_issues":
        raise RuntimeError(f"fixture PDF did not fail conversion visibly: {result}")
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        row = connection.execute("SELECT doc_id, original_path FROM documents").fetchone()
        return str(row["doc_id"]), str(row["original_path"])
    finally:
        connection.close()


def _run_ocr_success(root: Path) -> dict[str, int]:
    vault = root / "vault"
    source = root / "scan.pdf"
    root.mkdir(parents=True)
    doc_id, original_path = _make_failed_pdf(vault, source)
    (vault / original_path).with_name("original.pdf.ocr.txt").write_text(
        "OCR page one alpha\fOCR page two m62gateneedle evidence",
        encoding="utf-8",
    )

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = run_ocr_for_document(connection, vault, doc_id)
        search = search_chunks(connection, "m62gateneedle")
        summary = {
            "ocr_success_pages": connection.execute("SELECT COUNT(*) AS count FROM ocr_pages").fetchone()["count"],
            "ocr_success_revisions": connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()["count"],
            "ocr_success_chunks": connection.execute("SELECT COUNT(*) AS count FROM chunks WHERE is_current = 1").fetchone()["count"],
            "ocr_success_fts": connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"],
            "ocr_success_search_results": search.result_count,
        }
    finally:
        connection.close()
    if result.status != "succeeded" or summary["ocr_success_search_results"] != 1:
        raise RuntimeError(f"OCR success scenario failed: {result}, {summary}")
    return summary


def _run_ocr_low_confidence(root: Path) -> dict[str, int]:
    vault = root / "vault"
    source = root / "scan.pdf"
    root.mkdir(parents=True)
    doc_id, original_path = _make_failed_pdf(vault, source)
    (vault / original_path).with_name("original.pdf.ocr.json").write_text(
        '[{"page_number": 1, "text": "low confidence OCR gate", "confidence": 0.35}]',
        encoding="utf-8",
    )

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = run_ocr_for_document(connection, vault, doc_id)
        summary = {
            "ocr_low_confidence_review_items": connection.execute(
                "SELECT COUNT(*) AS count FROM review_items WHERE type = 'ocr_low_quality'"
            ).fetchone()["count"],
            "ocr_low_confidence_pages_needing_review": connection.execute(
                "SELECT COUNT(*) AS count FROM ocr_pages WHERE needs_review = 1"
            ).fetchone()["count"],
        }
    finally:
        connection.close()
    if result.status != "completed_with_issues" or summary["ocr_low_confidence_review_items"] != 1:
        raise RuntimeError(f"OCR low-confidence scenario failed: {result}, {summary}")
    return summary


def _run_ocr_failure(root: Path) -> dict[str, int]:
    vault = root / "vault"
    source = root / "scan.pdf"
    root.mkdir(parents=True)
    doc_id, _original_path = _make_failed_pdf(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = run_ocr_for_document(connection, vault, doc_id)
        summary = {
            "ocr_failure_errors": connection.execute(
                "SELECT COUNT(*) AS count FROM errors WHERE component = 'ocr'"
            ).fetchone()["count"],
            "ocr_failure_reviews": connection.execute(
                "SELECT COUNT(*) AS count FROM review_items WHERE type = 'ocr_low_quality'"
            ).fetchone()["count"],
            "ocr_failure_revisions": connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()["count"],
            "ocr_failure_chunks": connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"],
            "ocr_failure_fts": connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"],
            "zero_chunk_current_revisions": connection.execute(
                """
                SELECT COUNT(*) AS count
                FROM documents d
                WHERE d.current_revision_id IS NOT NULL
                  AND NOT EXISTS (
                    SELECT 1
                    FROM chunks c
                    WHERE c.doc_id = d.doc_id
                      AND c.revision_id = d.current_revision_id
                      AND c.is_current = 1
                      AND c.deleted_at IS NULL
                  )
                """
            ).fetchone()["count"],
        }
    finally:
        connection.close()
    if result.status != "failed":
        raise RuntimeError(f"OCR failure scenario did not fail: {result}")
    expected_zero = ("ocr_failure_revisions", "ocr_failure_chunks", "ocr_failure_fts", "zero_chunk_current_revisions")
    if any(summary[key] != 0 for key in expected_zero):
        raise RuntimeError(f"OCR failure scenario created searchable state: {summary}")
    if summary["ocr_failure_errors"] != 1 or summary["ocr_failure_reviews"] != 1:
        raise RuntimeError(f"OCR failure scenario did not record visible failure: {summary}")
    return summary


if __name__ == "__main__":
    main()
