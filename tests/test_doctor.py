from pathlib import Path
import sqlite3

import indbase_core.doctor as doctor_module
import indbase_core.normalizers as normalizers
from indbase_core.cards import accept_candidate_card, generate_candidate_card
from indbase_core.chunker import chunk_current_revision
from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.indexer import rebuild_fts_index
from indbase_core.ingest import run_m2_ingest_pipeline, run_m3_ingest_pipeline
from indbase_core.ocr import run_ocr_for_document
from indbase_core.time import utc_now_iso
from indbase_core.translations import translate_full_document
from indbase_core.vault import init_vault


def _m3_indexed_vault(vault: Path, source: Path) -> str:
    init_vault(vault)
    run_m2_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        chunk_current_revision(connection, vault, doc_id)
        rebuild_fts_index(connection, vault)
    finally:
        connection.close()
    return doc_id


def _codes(report) -> set[str]:
    return {finding.code for finding in report.findings}


def _finding(report, code: str):
    return next(finding for finding in report.findings if finding.code == code)


def test_doctor_accepts_indexed_m3_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    _m3_indexed_vault(vault, source)

    report = run_doctor(vault)

    assert report.exit_code in {0, 1}
    assert "missing_current_chunks" not in _codes(report)
    assert "fts_missing_chunk" not in _codes(report)
    assert "revision_content_hash_mismatch" not in _codes(report)


def test_doctor_reports_markitdown_available_as_info(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    monkeypatch.setattr(doctor_module, "find_spec", lambda name: object() if name == "markitdown" else None)

    report = run_doctor(vault)

    assert report.exit_code == 0
    assert _finding(report, "markitdown_available").severity == "info"
    assert "ok" in _codes(report)


def test_doctor_reports_markitdown_unavailable_as_non_blocking_info(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    monkeypatch.setattr(doctor_module, "find_spec", lambda name: None)

    report = run_doctor(vault)

    assert report.exit_code == 0
    assert _finding(report, "markitdown_unavailable").severity == "info"
    assert "ok" in _codes(report)


def test_doctor_detects_missing_original_and_canonical_markdown(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    _m3_indexed_vault(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc = connection.execute("SELECT original_path, canonical_path FROM documents").fetchone()
    finally:
        connection.close()
    (vault / doc["original_path"]).unlink()
    (vault / doc["canonical_path"]).unlink()

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "missing_original_file" in _codes(report)
    assert "missing_canonical_markdown" in _codes(report)
    assert "missing_revision_markdown" in _codes(report)


def test_doctor_detects_frontmatter_and_content_hash_mismatch(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    _m3_indexed_vault(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        canonical_path = connection.execute("SELECT canonical_path FROM documents").fetchone()["canonical_path"]
    finally:
        connection.close()
    markdown_path = vault / canonical_path
    markdown = markdown_path.read_text(encoding="utf-8")
    markdown = markdown.replace('doc_id: "doc_', 'doc_id: "tampered_doc_', 1)
    markdown += "tampered body\n"
    markdown_path.write_text(markdown, encoding="utf-8")

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "frontmatter_mismatch" in _codes(report)
    assert "revision_content_hash_mismatch" in _codes(report)


def test_doctor_detects_missing_current_chunks_after_chunk_records_removed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    _m3_indexed_vault(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        connection.execute("DELETE FROM chunks")
        connection.commit()
    finally:
        connection.close()

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "missing_current_chunks" in _codes(report)
    assert "fts_stale_row" in _codes(report)


def test_doctor_detects_orphan_original_archive_file(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    orphan = vault / ".indbase" / "originals" / "2099" / "01" / "doc_orphan" / "original.txt"
    orphan.parent.mkdir(parents=True)
    orphan.write_text("orphan", encoding="utf-8")

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "orphan_original_file" in _codes(report)


def test_doctor_allows_ocr_sidecar_next_to_referenced_original(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    init_vault(vault)

    run_m3_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        original_path = connection.execute("SELECT original_path FROM documents").fetchone()["original_path"]
    finally:
        connection.close()
    (vault / original_path).with_name("original.pdf.ocr.txt").write_text("ocr text", encoding="utf-8")

    report = run_doctor(vault)

    assert "orphan_original_file" not in _codes(report)


def test_doctor_accepts_failed_pdf_source_shell_as_review_state(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    init_vault(vault)
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)

    report = run_doctor(vault)

    assert report.exit_code == 1
    assert "pending_review_items" in _codes(report)
    assert "missing_current_chunks" not in _codes(report)
    assert "source_shell_indexed" not in _codes(report)
    assert "source_shell_missing_review" not in _codes(report)
    assert "source_shell_missing_error" not in _codes(report)


def test_doctor_detects_searchable_source_shell_corruption(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    init_vault(vault)
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        connection.execute("UPDATE documents SET fts_status = 'indexed'")
        connection.commit()
    finally:
        connection.close()

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "source_shell_indexed" in _codes(report)


def test_doctor_detects_ocr_page_orphans_and_missing_ocr_pages(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    init_vault(vault)
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        row = connection.execute("SELECT doc_id, original_path FROM documents").fetchone()
        doc_id = row["doc_id"]
        (vault / row["original_path"]).with_name("original.pdf.ocr.txt").write_text("doctor OCR needle", encoding="utf-8")
        result = run_ocr_for_document(connection, vault, doc_id)
        connection.execute("DELETE FROM ocr_pages WHERE doc_id = ?", (doc_id,))
        connection.commit()
    finally:
        connection.close()
    raw_connection = sqlite3.connect(vault / ".indbase" / "db.sqlite")
    try:
        raw_connection.execute(
            """
            INSERT INTO ocr_pages(
              ocr_page_id, doc_id, revision_id, page_number, text, confidence,
              quality_status, quality_signals_json, needs_review, engine,
              engine_version, created_at, updated_at
            )
            VALUES ('ocr_page_orphan', 'doc_missing', 'rev_missing', 1, 'orphan',
                    1.0, 'passed', '{}', 0, 'sidecar', 'test', ?, ?)
            """,
            (utc_now_iso(), utc_now_iso()),
        )
        raw_connection.commit()
    finally:
        raw_connection.close()

    report = run_doctor(vault)

    assert result.status == "succeeded"
    assert report.exit_code == 2
    assert "missing_ocr_pages" in _codes(report)
    assert "orphan_ocr_page" in _codes(report)


def test_doctor_detects_low_confidence_ocr_without_review(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    init_vault(vault)
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        row = connection.execute("SELECT doc_id, original_path FROM documents").fetchone()
        doc_id = row["doc_id"]
        (vault / row["original_path"]).with_name("original.pdf.ocr.json").write_text(
            '[{"page_number": 1, "text": "low confidence", "confidence": 0.2}]',
            encoding="utf-8",
        )
        result = run_ocr_for_document(connection, vault, doc_id)
        connection.execute("DELETE FROM review_items WHERE type = 'ocr_low_quality'")
        connection.commit()
    finally:
        connection.close()

    report = run_doctor(vault)

    assert result.status == "completed_with_issues"
    assert report.exit_code == 2
    assert "ocr_low_confidence_without_review" in _codes(report)


def test_doctor_detects_failed_markitdown_without_error_or_review(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    init_vault(vault)
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        connection.execute("DELETE FROM review_items")
        connection.execute("DELETE FROM errors")
        connection.commit()
    finally:
        connection.close()

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "conversion_failure_without_review" in _codes(report)
    assert "conversion_failure_without_error" in _codes(report)


def test_doctor_detects_orphan_revision_and_chunk_records(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    db_path = vault / ".indbase" / "db.sqlite"
    now = utc_now_iso()
    raw_connection = sqlite3.connect(db_path)
    try:
        raw_connection.execute(
            """
            INSERT INTO document_revisions(
              revision_id, doc_id, sequence, markdown_path, content_hash,
              converter_name, converter_version, chunk_strategy, text_length,
              chunk_count, created_at, updated_at
            )
            VALUES ('rev_missing_0001', 'doc_missing', 1, 'sources/missing.md',
                    'sha256:missing', 'test', 'test', 'test', 0, 0, ?, ?)
            """,
            (now, now),
        )
        raw_connection.execute(
            """
            INSERT INTO chunks(
              chunk_id, doc_id, revision_id, sequence, heading_path_json,
              text, start_offset, end_offset, source_page, language,
              token_count, content_hash, is_current, created_at, updated_at
            )
            VALUES ('chunk_orphan_0001', 'doc_missing', 'rev_missing_0001', 1,
                    '[]', 'orphan text', 0, 11, NULL, NULL, 2, 'sha256:chunk', 1, ?, ?)
            """,
            (now, now),
        )
        raw_connection.execute(
            """
            INSERT INTO source_files(
              source_file_id, doc_id, source_uri, normalized_source_uri,
              original_filename, original_ext, mime_type, size_bytes,
              source_hash, original_path, created_at, updated_at
            )
            VALUES (
              'source_file_orphan_0001', 'doc_missing', 'missing.txt', 'missing.txt',
              'missing.txt', 'txt', 'text/plain', 7,
              'sha256:missing', '.indbase/originals/2099/01/doc_missing/original.txt', ?, ?
            )
            """,
            (now, now),
        )
        raw_connection.commit()
    finally:
        raw_connection.close()

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "orphan_revision" in _codes(report)
    assert "orphan_chunk" in _codes(report)
    assert "orphan_source_file" in _codes(report)
    assert "missing_revision_markdown" in _codes(report)


def test_doctor_detects_fts_desync(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    _m3_indexed_vault(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        connection.execute("DELETE FROM chunks_fts")
        connection.commit()
    finally:
        connection.close()

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "fts_missing_chunk" in _codes(report)


def test_doctor_allows_archived_document_fts_rows_to_remain(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    _m3_indexed_vault(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        connection.execute(
            "UPDATE documents SET status = 'archived', archived_at = ?, updated_at = ?",
            (utc_now_iso(), utc_now_iso()),
        )
        connection.commit()
    finally:
        connection.close()

    report = run_doctor(vault)

    assert "fts_stale_row" not in _codes(report)


def test_doctor_detects_stale_fts_rows_for_old_revision(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    _m3_indexed_vault(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc = connection.execute("SELECT doc_id, current_revision_id FROM documents").fetchone()
        connection.execute(
            "UPDATE documents SET current_revision_id = 'rev_missing_current_0001', updated_at = ? WHERE doc_id = ?",
            (utc_now_iso(), doc["doc_id"]),
        )
        connection.commit()
    finally:
        connection.close()

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "fts_stale_row" in _codes(report)


def test_doctor_detects_missing_translation_output_as_generated_artifact_warning(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nTranslation artifact body.\n", encoding="utf-8")
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc = connection.execute("SELECT doc_id, current_revision_id FROM documents").fetchone()
        result = translate_full_document(
            connection,
            vault,
            doc_id=doc["doc_id"],
            revision_id=doc["current_revision_id"],
            target_language="zh-CN",
        )
    finally:
        connection.close()
    (vault / result.output_path).unlink()

    report = run_doctor(vault)

    assert report.exit_code == 1
    assert _finding(report, "missing_translation_output").severity == "warning"
    assert "missing_canonical_markdown" not in _codes(report)
    assert "missing_revision_markdown" not in _codes(report)


def test_doctor_detects_orphan_translation_records_and_output_mismatch(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nTranslation orphan body.\n", encoding="utf-8")
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc = connection.execute("SELECT doc_id, current_revision_id FROM documents").fetchone()
        translate_full_document(
            connection,
            vault,
            doc_id=doc["doc_id"],
            revision_id=doc["current_revision_id"],
            target_language="zh-CN",
        )
        row = connection.execute("SELECT translation_id, execution_id FROM translations").fetchone()
    finally:
        connection.close()

    raw_connection = sqlite3.connect(vault / ".indbase" / "db.sqlite")
    try:
        raw_connection.execute(
            """
            UPDATE translations
            SET source_doc_id = 'doc_missing',
                source_revision_id = 'rev_missing',
                source_chunk_ids_json = '["chunk_missing"]'
            WHERE translation_id = ?
            """,
            (row["translation_id"],),
        )
        raw_connection.execute(
            "UPDATE executions SET output_path = 'outputs/translations/mismatch.md' WHERE execution_id = ?",
            (row["execution_id"],),
        )
        raw_connection.commit()
    finally:
        raw_connection.close()

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "orphan_translation_document" in _codes(report)
    assert "orphan_translation_revision" in _codes(report)
    assert "translation_source_chunk_missing" in _codes(report)
    assert "translation_execution_output_mismatch" in _codes(report)


def test_doctor_accepts_valid_accepted_candidate_card(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nCandidate card doctor body.\n", encoding="utf-8")
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc = connection.execute("SELECT doc_id FROM documents").fetchone()
        generated = generate_candidate_card(connection, doc_id=doc["doc_id"])
        accepted = accept_candidate_card(connection, vault, generated.candidate_card_id)
    finally:
        connection.close()

    report = run_doctor(vault)

    assert (vault / accepted.accepted_note_path).is_file()
    assert "missing_accepted_note" not in _codes(report)
    assert "accepted_note_uncited_claim" not in _codes(report)
    assert "orphan_atomic_note" not in _codes(report)


def test_doctor_detects_missing_accepted_candidate_note(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nCandidate card missing note body.\n", encoding="utf-8")
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc = connection.execute("SELECT doc_id FROM documents").fetchone()
        generated = generate_candidate_card(connection, doc_id=doc["doc_id"])
        accepted = accept_candidate_card(connection, vault, generated.candidate_card_id)
    finally:
        connection.close()
    (vault / accepted.accepted_note_path).unlink()

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "missing_accepted_note" in _codes(report)
    assert "missing_canonical_markdown" not in _codes(report)


def test_doctor_detects_candidate_card_orphans_and_missing_claim_sources(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nCandidate card orphan body.\n", encoding="utf-8")
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc = connection.execute("SELECT doc_id FROM documents").fetchone()
        generated = generate_candidate_card(connection, doc_id=doc["doc_id"])
    finally:
        connection.close()

    raw_connection = sqlite3.connect(vault / ".indbase" / "db.sqlite")
    try:
        raw_connection.execute(
            """
            UPDATE candidate_cards
            SET source_doc_id = 'doc_missing',
                source_revision_id = 'rev_missing'
            WHERE candidate_card_id = ?
            """,
            (generated.candidate_card_id,),
        )
        raw_connection.execute(
            """
            UPDATE candidate_card_sources
            SET source_chunk_id = 'chunk_missing'
            WHERE candidate_card_id = ?
            """,
            (generated.candidate_card_id,),
        )
        raw_connection.commit()
    finally:
        raw_connection.close()

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "orphan_candidate_card_document" in _codes(report)
    assert "orphan_candidate_card_revision" in _codes(report)
    assert "candidate_card_claim_source_chunk_missing" in _codes(report)
    assert "candidate_card_source_card_mismatch" in _codes(report)
    assert "orphan_candidate_card_source_chunk" in _codes(report)


def test_doctor_detects_accepted_candidate_without_sources_or_citations(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nCandidate card uncited body.\n", encoding="utf-8")
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc = connection.execute("SELECT doc_id FROM documents").fetchone()
        generated = generate_candidate_card(connection, doc_id=doc["doc_id"])
        accepted = accept_candidate_card(connection, vault, generated.candidate_card_id)
        connection.execute("DELETE FROM candidate_card_sources WHERE candidate_card_id = ?", (generated.candidate_card_id,))
        connection.commit()
    finally:
        connection.close()
    note_path = vault / accepted.accepted_note_path
    note_path.write_text(note_path.read_text(encoding="utf-8").replace("Citations:", "References:"), encoding="utf-8")

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "accepted_card_without_sources" in _codes(report)
    assert "candidate_card_claim_source_binding_missing" in _codes(report)
    assert "accepted_note_uncited_claim" in _codes(report)


def test_doctor_detects_orphan_candidate_atomic_note(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    orphan = vault / "notes" / "atomic" / "2099" / "01" / "candidate_card_missing.md"
    orphan.parent.mkdir(parents=True)
    orphan.write_text(
        "---\n"
        "schema_version: \"indbase.atomic_note.v1\"\n"
        "type: \"candidate_card\"\n"
        "candidate_card_id: \"candidate_card_missing\"\n"
        "---\n"
        "\n"
        "# Orphan\n",
        encoding="utf-8",
    )

    report = run_doctor(vault)

    assert report.exit_code == 2
    assert "orphan_atomic_note" in _codes(report)
