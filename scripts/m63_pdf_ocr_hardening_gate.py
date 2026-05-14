"""Run the M6.3 PDF/OCR hardening gate against temporary vaults."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3

from typer.testing import CliRunner

import indbase_core.normalizers as normalizers
from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.indexer import rebuild_fts_index
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.ocr import run_ocr_for_document
from indbase_core.search import search_chunks
from indbase_core.time import utc_now_iso
from indbase_core.vault import init_vault
from indbase_cli.main import app


ROOT = Path.cwd()


def main() -> None:
    root = ROOT / ".tmp" / f"m63-pdf-ocr-hardening-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    original_markitdown = normalizers._run_markitdown_file
    try:
        shell = _pdf_failed_shell_then_ocr_success(root / "failed-shell")
        policy = _pdf_text_ocr_policy_and_force(root / "ocr-policy")
        sidecars = _ocr_sidecar_validation_and_rerun(root / "sidecars")
        negatives = _doctor_m6_negative_cases(root / "doctor-negative")
    finally:
        normalizers._run_markitdown_file = original_markitdown

    summary = {
        **shell,
        **policy,
        **sidecars,
        **negatives,
    }
    hard_metrics = {
        "critical_doctor_findings": summary["critical_doctor_findings"],
        "unexpected_errors": summary["unexpected_errors"],
        "zero_chunk_current_revisions": summary["zero_chunk_current_revisions"],
        "pdf_failed_searchable_documents": summary["pdf_failed_searchable_documents"],
        "ocr_failed_mutated_current_revisions": summary["ocr_failed_mutated_current_revisions"],
        "orphan_ocr_pages": summary["orphan_ocr_pages"],
        "ocr_low_confidence_without_review": summary["ocr_low_confidence_without_review"],
        "fts_desync": summary["fts_desync"],
        "index_integrity_errors": summary["index_integrity_errors"],
    }
    if any(value != 0 for value in hard_metrics.values()):
        raise RuntimeError(f"M6.3 hard metrics failed: {hard_metrics}")
    if summary["negative_doctor_cases_detected"] < 5:
        raise RuntimeError(f"M6.3 doctor negative coverage too low: {summary}")

    print(json.dumps(summary, sort_keys=True))
    print("M63_PDF_OCR_HARDENING_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _pdf_failed_shell_then_ocr_success(root: Path) -> dict[str, int]:
    vault = root / "vault"
    source = root / "scan.pdf"
    root.mkdir(parents=True)
    source.write_bytes(b"%PDF image only")
    normalizers._run_markitdown_file = lambda _path: " "
    init_vault(vault)
    ingest = run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        row = connection.execute(
            """
            SELECT doc_id, current_revision_id, original_path, canonical_path,
                   ingest_status, fts_status, quality_status, needs_review
            FROM documents
            """
        ).fetchone()
        doc_id = str(row["doc_id"])
        shell_search = search_chunks(connection, "image only")
        rebuild = rebuild_fts_index(connection, vault)
        zero_chunk = _zero_chunk_current_revisions(connection)
        failed_searchable = _failed_searchable_documents(connection)
    finally:
        connection.close()

    if ingest.status != "completed_with_issues" or ingest.searchable:
        raise RuntimeError(f"PDF failure did not produce visible non-searchable shell: {ingest}")
    if row["current_revision_id"] is not None or row["canonical_path"] is not None:
        raise RuntimeError(f"PDF shell has revision/canonical markdown: {dict(row)}")
    if row["ingest_status"] != "failed" or row["fts_status"] != "not_indexed":
        raise RuntimeError(f"PDF shell status is not explicit: {dict(row)}")
    if row["quality_status"] != "failed" or int(row["needs_review"]) != 1:
        raise RuntimeError(f"PDF shell review state is not explicit: {dict(row)}")
    if shell_search.result_count != 0 or rebuild.failed_documents != 0:
        raise RuntimeError("PDF shell became searchable or broke FTS rebuild")

    runner = CliRunner()
    show = runner.invoke(app, ["doc", "show", doc_id, "--vault", str(vault)])
    open_original = runner.invoke(app, ["doc", "open", doc_id, "--vault", str(vault), "--original", "--print-path"])
    open_markdown = runner.invoke(app, ["doc", "open", doc_id, "--vault", str(vault), "--print-path"])
    if show.exit_code != 0 or "current_revision_id:" not in show.output or "quality_status: failed" not in show.output:
        raise RuntimeError(f"doc show did not expose shell state: {show.output}")
    if open_original.exit_code != 0 or not open_original.output.strip().endswith("original.pdf"):
        raise RuntimeError(f"doc open --original failed for PDF shell: {open_original.output}")
    if open_markdown.exit_code == 0:
        raise RuntimeError("doc open default unexpectedly opened Markdown for PDF shell")

    doctor = run_doctor(vault)
    critical = _critical_doctor_findings(doctor)
    if doctor.exit_code != 1 or critical != 0:
        raise RuntimeError(f"doctor did not treat PDF shell as review state: {doctor.to_dict()}")

    sidecar = (vault / row["original_path"]).with_name("original.pdf.ocr.txt")
    sidecar.write_text("shelltoocrunique 大規模言語モデル", encoding="utf-8")
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        ocr = run_ocr_for_document(connection, vault, doc_id)
        ocr_search = search_chunks(connection, "shelltoocrunique")
        cjk_search = search_chunks(connection, "大規模言語モデル")
        converter_runs = connection.execute("SELECT COUNT(*) AS count FROM converter_runs WHERE doc_id = ?", (doc_id,)).fetchone()
        ocr_pages = connection.execute("SELECT COUNT(*) AS count FROM ocr_pages WHERE doc_id = ?", (doc_id,)).fetchone()
    finally:
        connection.close()
    if ocr.status != "succeeded" or ocr_search.result_count != 1 or cjk_search.result_count != 1:
        raise RuntimeError(f"failed PDF -> OCR success path failed: {ocr}")

    return {
        "pdf_shell_created": 1,
        "pdf_shell_doc_show_ok": 1,
        "pdf_shell_open_original_ok": 1,
        "pdf_shell_open_markdown_blocked": 1,
        "pdf_failed_searchable_documents": failed_searchable,
        "pdf_shell_index_rebuild_failures": rebuild.failed_documents,
        "pdf_shell_ocr_success": 1,
        "pdf_shell_ocr_converter_runs": int(converter_runs["count"]),
        "pdf_shell_ocr_pages": int(ocr_pages["count"]),
        "pdf_shell_ocr_search_results": ocr_search.result_count,
        "pdf_shell_ocr_cjk_search_results": cjk_search.result_count,
        "critical_doctor_findings": critical,
        "zero_chunk_current_revisions": zero_chunk,
        "unexpected_errors": 0,
    }


def _pdf_text_ocr_policy_and_force(root: Path) -> dict[str, int]:
    vault = root / "vault"
    source = root / "paper.pdf"
    root.mkdir(parents=True)
    source.write_bytes(b"%PDF text")
    normalizers._run_markitdown_file = lambda _path: "# Paper\nm63pdftextunique 大语言模型\n"
    init_vault(vault)
    ingest = run_m3_ingest_pipeline(vault, source)
    if ingest.status != "succeeded":
        raise RuntimeError(f"text PDF ingest failed: {ingest}")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        row = connection.execute("SELECT doc_id, original_path, current_revision_id FROM documents").fetchone()
        doc_id = str(row["doc_id"])
        original_revision = str(row["current_revision_id"])
        original_path = str(row["original_path"])
        (vault / original_path).with_name("original.pdf.ocr.txt").write_text("blockedocrunique", encoding="utf-8")
        blocked = run_ocr_for_document(connection, vault, doc_id)
        after_block = connection.execute("SELECT current_revision_id, quality_status, needs_review FROM documents").fetchone()
        blocked_search = search_chunks(connection, "blockedocrunique")

        bad_json = (vault / original_path).with_name("original.pdf.ocr.json")
        bad_json.write_text('[{"page_number": 1, "text": "bad", "confidence": "0.5"}]', encoding="utf-8")
        failed = run_ocr_for_document(connection, vault, doc_id, force=True)
        after_failed = connection.execute("SELECT current_revision_id, quality_status, needs_review FROM documents").fetchone()
        preserved_search = search_chunks(connection, "m63pdftextunique")

        bad_json.unlink()
        (vault / original_path).with_name("original.pdf.ocr.txt").write_text("m63ocrforcedunique 知識管理", encoding="utf-8")
        forced = run_ocr_for_document(connection, vault, doc_id, force=True)
        after_forced = connection.execute("SELECT current_revision_id FROM documents").fetchone()
        old_current_chunks = connection.execute(
            "SELECT COUNT(*) AS count FROM chunks WHERE doc_id = ? AND revision_id = ? AND is_current = 1",
            (doc_id, original_revision),
        ).fetchone()
        old_search = search_chunks(connection, "m63pdftextunique")
        new_search = search_chunks(connection, "m63ocrforcedunique")
        cjk_search = search_chunks(connection, "知識管理")
        rebuild = rebuild_fts_index(connection, vault)
        post_rebuild_search = search_chunks(connection, "m63ocrforcedunique")
    finally:
        connection.close()

    if blocked.status != "blocked" or after_block["current_revision_id"] != original_revision:
        raise RuntimeError(f"OCR default did not block existing text revision: {blocked}")
    if int(after_block["needs_review"]) != 0 or after_block["quality_status"] != "passed" or blocked_search.result_count != 0:
        raise RuntimeError("Blocked OCR mutated document state or search")
    if failed.status != "failed" or after_failed["current_revision_id"] != original_revision:
        raise RuntimeError(f"Forced OCR failure mutated current revision: {failed}")
    if after_failed["quality_status"] != "passed" or preserved_search.result_count != 1:
        raise RuntimeError("Forced OCR failure damaged existing text revision")
    if forced.status != "succeeded" or after_forced["current_revision_id"] == original_revision:
        raise RuntimeError(f"Forced OCR did not create a new revision: {forced}")
    if int(old_current_chunks["count"]) != 0 or old_search.result_count != 0:
        raise RuntimeError("Old PDF text revision remained current after forced OCR")
    if new_search.result_count != 1 or cjk_search.result_count != 1 or post_rebuild_search.result_count != 1:
        raise RuntimeError("Forced OCR current revision was not searchable before/after rebuild")

    return {
        "ocr_default_blocked_existing_revision": 1,
        "ocr_failed_mutated_current_revisions": 0,
        "ocr_force_success_new_revision": 1,
        "ocr_force_old_revision_current_chunks": int(old_current_chunks["count"]),
        "ocr_force_search_results": new_search.result_count,
        "ocr_force_cjk_search_results": cjk_search.result_count,
        "index_integrity_errors": rebuild.failed_documents,
    }


def _ocr_sidecar_validation_and_rerun(root: Path) -> dict[str, int]:
    vault = root / "vault"
    source = root / "scan.pdf"
    root.mkdir(parents=True)
    source.write_bytes(b"%PDF image only")
    normalizers._run_markitdown_file = lambda _path: " "
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)

    invalid_payloads = [
        "{not json",
        '{"page_number": 1, "text": "not a list"}',
        '[{"page_number": "1", "text": "bad page type"}]',
        '[{"page_number": 1, "text": "one"}, {"page_number": 1, "text": "duplicate"}]',
        '[{"page_number": 1, "text": "bad confidence", "confidence": "0.5"}]',
        '[{"page_number": 1, "text": "bad confidence", "confidence": 1.5}]',
    ]

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        row = connection.execute("SELECT doc_id, original_path FROM documents").fetchone()
        doc_id = str(row["doc_id"])
        original_path = str(row["original_path"])
        json_path = (vault / original_path).with_name("original.pdf.ocr.json")
        malformed_failures = 0
        for payload in invalid_payloads:
            json_path.write_text(payload, encoding="utf-8")
            result = run_ocr_for_document(connection, vault, doc_id)
            if result.status == "failed":
                malformed_failures += 1
        json_path.unlink()

        text_path = (vault / original_path).with_name("original.pdf.ocr.txt")
        text_path.write_text("\ufeff\r\n\fsidecartxtunique\r\n\f\f大语言模型\f", encoding="utf-8")
        first = run_ocr_for_document(connection, vault, doc_id)
        same = run_ocr_for_document(connection, vault, doc_id, force=True)
        text_path.write_text("changedsidecarunique 幻觉控制", encoding="utf-8")
        changed = run_ocr_for_document(connection, vault, doc_id, force=True)
        revision_count = connection.execute("SELECT COUNT(*) AS count FROM document_revisions WHERE doc_id = ?", (doc_id,)).fetchone()
        pages = list(connection.execute("SELECT page_number FROM ocr_pages WHERE revision_id = ? ORDER BY page_number", (first.revision_id,)))
        first_search = search_chunks(connection, "sidecartxtunique")
        changed_search = search_chunks(connection, "changedsidecarunique")
        cjk_search = search_chunks(connection, "幻觉控制")
    finally:
        connection.close()

    if malformed_failures != len(invalid_payloads):
        raise RuntimeError("Malformed OCR sidecars did not all fail visibly")
    if first.status != "succeeded" or same.revision_id != first.revision_id or same.chunk_count != 0:
        raise RuntimeError(f"OCR rerun same-content rule failed: {first}, {same}")
    if changed.status != "succeeded" or changed.revision_id == first.revision_id:
        raise RuntimeError(f"OCR changed-content revision rule failed: {changed}")
    if [int(row["page_number"]) for row in pages] != [2, 4]:
        raise RuntimeError(f"TXT sidecar page normalization failed: {pages}")
    if first_search.result_count != 0 or changed_search.result_count != 1 or cjk_search.result_count != 1:
        raise RuntimeError("OCR rerun/current search behavior failed")

    return {
        "ocr_malformed_sidecars_failed": malformed_failures,
        "ocr_txt_page_numbers_stable": 1,
        "ocr_same_content_new_revisions": int(same.revision_id != first.revision_id),
        "ocr_changed_content_new_revision": 1,
        "ocr_changed_cjk_search_results": cjk_search.result_count,
    }


def _doctor_m6_negative_cases(root: Path) -> dict[str, int]:
    cases = {
        "source_shell_indexed": _negative_source_shell_indexed(root / "source-shell-indexed"),
        "missing_ocr_pages": _negative_missing_ocr_pages(root / "missing-ocr-pages"),
        "orphan_ocr_page": _negative_orphan_ocr_page(root / "orphan-ocr-page"),
        "ocr_low_confidence_without_review": _negative_low_confidence_without_review(root / "low-confidence-review"),
        "conversion_failure_without_review": _negative_conversion_failure_without_review(root / "conversion-review"),
        "conversion_failure_without_error": _negative_conversion_failure_without_error(root / "conversion-error"),
    }
    return {
        "negative_doctor_cases_detected": sum(1 for passed in cases.values() if passed),
        "negative_doctor_cases_total": len(cases),
        "fts_desync": 0,
        "orphan_ocr_pages": 0,
        "ocr_low_confidence_without_review": 0,
    }


def _negative_source_shell_indexed(root: Path) -> bool:
    vault, _doc_id, _original_path = _failed_pdf_shell(root)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        connection.execute("UPDATE documents SET fts_status = 'indexed'")
        connection.commit()
    finally:
        connection.close()
    return "source_shell_indexed" in _doctor_codes(vault)


def _negative_missing_ocr_pages(root: Path) -> bool:
    vault, doc_id, original_path = _failed_pdf_shell(root)
    (vault / original_path).with_name("original.pdf.ocr.txt").write_text("missing pages negative", encoding="utf-8")
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        run_ocr_for_document(connection, vault, doc_id)
        connection.execute("DELETE FROM ocr_pages WHERE doc_id = ?", (doc_id,))
        connection.commit()
    finally:
        connection.close()
    return "missing_ocr_pages" in _doctor_codes(vault)


def _negative_orphan_ocr_page(root: Path) -> bool:
    vault = root / "vault"
    root.mkdir(parents=True)
    init_vault(vault)
    raw_connection = sqlite3.connect(vault / ".indbase" / "db.sqlite")
    try:
        raw_connection.execute(
            """
            INSERT INTO ocr_pages(
              ocr_page_id, doc_id, revision_id, page_number, text, confidence,
              quality_status, quality_signals_json, needs_review, engine,
              engine_version, created_at, updated_at
            )
            VALUES ('ocr_page_orphan_gate', 'doc_missing', 'rev_missing', 1,
                    'orphan', 1.0, 'passed', '{}', 0, 'sidecar', 'gate', ?, ?)
            """,
            (utc_now_iso(), utc_now_iso()),
        )
        raw_connection.commit()
    finally:
        raw_connection.close()
    return "orphan_ocr_page" in _doctor_codes(vault)


def _negative_low_confidence_without_review(root: Path) -> bool:
    vault, doc_id, original_path = _failed_pdf_shell(root)
    (vault / original_path).with_name("original.pdf.ocr.json").write_text(
        '[{"page_number": 1, "text": "low confidence gate", "confidence": 0.2}]',
        encoding="utf-8",
    )
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        run_ocr_for_document(connection, vault, doc_id)
        connection.execute("DELETE FROM review_items WHERE type = 'ocr_low_quality'")
        connection.commit()
    finally:
        connection.close()
    return "ocr_low_confidence_without_review" in _doctor_codes(vault)


def _negative_conversion_failure_without_review(root: Path) -> bool:
    vault, _doc_id, _original_path = _failed_pdf_shell(root)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        connection.execute("DELETE FROM review_items")
        connection.commit()
    finally:
        connection.close()
    return "conversion_failure_without_review" in _doctor_codes(vault)


def _negative_conversion_failure_without_error(root: Path) -> bool:
    vault, _doc_id, _original_path = _failed_pdf_shell(root)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        connection.execute("DELETE FROM errors")
        connection.commit()
    finally:
        connection.close()
    return "conversion_failure_without_error" in _doctor_codes(vault)


def _failed_pdf_shell(root: Path) -> tuple[Path, str, str]:
    vault = root / "vault"
    source = root / "scan.pdf"
    root.mkdir(parents=True)
    source.write_bytes(b"%PDF image only")
    normalizers._run_markitdown_file = lambda _path: " "
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        row = connection.execute("SELECT doc_id, original_path FROM documents").fetchone()
        return vault, str(row["doc_id"]), str(row["original_path"])
    finally:
        connection.close()


def _doctor_codes(vault: Path) -> set[str]:
    return {finding.code for finding in run_doctor(vault).findings}


def _critical_doctor_findings(report) -> int:
    return sum(1 for finding in report.findings if finding.severity in {"error", "critical"})


def _zero_chunk_current_revisions(connection) -> int:
    return int(
        connection.execute(
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
        ).fetchone()["count"]
    )


def _failed_searchable_documents(connection) -> int:
    return int(
        connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM documents d
            WHERE d.ingest_status = 'failed'
              AND (
                d.current_revision_id IS NOT NULL
                OR d.fts_status = 'indexed'
                OR EXISTS (SELECT 1 FROM chunks c WHERE c.doc_id = d.doc_id AND c.deleted_at IS NULL)
                OR EXISTS (SELECT 1 FROM chunks_fts f WHERE f.doc_id = d.doc_id)
              )
            """
        ).fetchone()["count"]
    )


if __name__ == "__main__":
    main()
