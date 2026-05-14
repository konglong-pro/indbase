from pathlib import Path

import pytest
from typer.testing import CliRunner

import indbase_core.normalizers as normalizers
from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.ocr import run_ocr_for_document
from indbase_core.search import search_chunks
from indbase_core.vault import init_vault
from indbase_cli.main import app


def _pdf_doc(vault: Path, source: Path) -> tuple[str, str]:
    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        row = connection.execute("SELECT doc_id, original_path FROM documents WHERE source_uri = ?", (str(source),)).fetchone()
        if row is None:
            row = connection.execute("SELECT doc_id, original_path FROM documents").fetchone()
        return str(row["doc_id"]), str(row["original_path"])
    finally:
        connection.close()


def test_ocr_sidecar_creates_revision_pages_chunks_and_fts(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    ingest = run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        original_path = connection.execute("SELECT original_path FROM documents").fetchone()["original_path"]
    finally:
        connection.close()

    sidecar = (vault / original_path).with_name("original.pdf.ocr.txt")
    sidecar.write_text("OCR first page alpha\fOCR second page m62needle evidence", encoding="utf-8")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = run_ocr_for_document(connection, vault, doc_id)
        document = connection.execute(
            "SELECT current_revision_id, canonical_path, ingest_status, fts_status, quality_status FROM documents"
        ).fetchone()
        converter_run = connection.execute("SELECT converter_name, status FROM converter_runs ORDER BY created_at DESC").fetchone()
        pages = connection.execute("SELECT COUNT(*) AS count FROM ocr_pages WHERE doc_id = ?", (doc_id,)).fetchone()
        chunks = connection.execute("SELECT COUNT(*) AS count FROM chunks WHERE doc_id = ? AND is_current = 1", (doc_id,)).fetchone()
        fts = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts WHERE doc_id = ?", (doc_id,)).fetchone()
        search = search_chunks(connection, "m62needle")
        task = connection.execute("SELECT status FROM tasks WHERE task_id = ?", (result.task_id,)).fetchone()
    finally:
        connection.close()

    assert ingest.status == "completed_with_issues"
    assert result.status == "succeeded"
    assert result.page_count == 2
    assert result.revision_id is not None
    assert result.chunk_count >= 1
    assert document["current_revision_id"] == result.revision_id
    assert document["canonical_path"].endswith("__rev_0001.md")
    assert document["ingest_status"] == "revisioned"
    assert document["fts_status"] == "indexed"
    assert document["quality_status"] == "passed"
    assert converter_run["converter_name"] == "ocr_sidecar"
    assert converter_run["status"] == "succeeded"
    assert pages["count"] == 2
    assert chunks["count"] >= 1
    assert fts["count"] >= 1
    assert search.result_count == 1
    assert task["status"] == "succeeded"


def test_ocr_sidecar_missing_records_visible_failure_without_revision(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        result = run_ocr_for_document(connection, vault, doc_id)
        document = connection.execute("SELECT current_revision_id, quality_status, needs_review FROM documents").fetchone()
        error = connection.execute("SELECT component, error_type FROM errors WHERE component = 'ocr'").fetchone()
        review = connection.execute("SELECT type, target_id FROM review_items WHERE type = 'ocr_low_quality'").fetchone()
        revisions = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
        chunks = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        fts = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
    finally:
        connection.close()

    assert result.status == "failed"
    assert result.error_id is not None
    assert document["current_revision_id"] is None
    assert document["quality_status"] == "failed"
    assert document["needs_review"] == 1
    assert error["component"] == "ocr"
    assert error["error_type"] == "ocr_no_extractable_text"
    assert review["target_id"] == doc_id
    assert revisions["count"] == 0
    assert chunks["count"] == 0
    assert fts["count"] == 0


def test_ocr_low_confidence_pages_create_review_but_remain_searchable(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        original_path = connection.execute("SELECT original_path FROM documents").fetchone()["original_path"]
    finally:
        connection.close()
    sidecar = (vault / original_path).with_name("original.pdf.ocr.json")
    sidecar.write_text('[{"page_number": 1, "text": "low confidence OCR lowconfm62needle", "confidence": 0.42}]', encoding="utf-8")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = run_ocr_for_document(connection, vault, doc_id)
        page = connection.execute(
            "SELECT quality_status, needs_review FROM ocr_pages WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        review = connection.execute("SELECT type, target_id FROM review_items WHERE type = 'ocr_low_quality'").fetchone()
        search = search_chunks(connection, "lowconfm62needle")
    finally:
        connection.close()

    assert result.status == "completed_with_issues"
    assert result.review_items == 1
    assert page["quality_status"] == "warning"
    assert page["needs_review"] == 1
    assert review["target_id"] == doc_id
    assert search.result_count == 1


def test_cli_ocr_run_and_pages_show_sidecar_results(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        original_path = connection.execute("SELECT original_path FROM documents").fetchone()["original_path"]
    finally:
        connection.close()
    (vault / original_path).with_name("original.pdf.ocr.txt").write_text("CLI OCR needle", encoding="utf-8")

    runner = CliRunner()
    run_result = runner.invoke(app, ["ocr", "run", doc_id, "--vault", str(vault)])
    pages_result = runner.invoke(app, ["ocr", "pages", doc_id, "--vault", str(vault)])
    search_result = runner.invoke(app, ["search", "CLI OCR needle", "--vault", str(vault), "--json"])

    assert run_result.exit_code == 0
    assert "Status: succeeded" in run_result.output
    assert "Revision: rev_doc_" in run_result.output
    assert pages_result.exit_code == 0
    assert "OCR Pages" in pages_result.output
    assert "CLI OCR needle" in pages_result.output
    assert search_result.exit_code == 0
    assert '"result_count": 1' in search_result.output


def test_ocr_default_blocks_existing_text_pdf_revision_without_mutation(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF text")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: "# Paper\npdfbaselineunique\n")
    ingest = run_m3_ingest_pipeline(vault, source)
    doc_id, original_path = _pdf_doc(vault, source)
    (vault / original_path).with_name("original.pdf.ocr.txt").write_text("ocrreplacementunique", encoding="utf-8")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        before = connection.execute(
            "SELECT current_revision_id, fts_status, quality_status, needs_review FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        result = run_ocr_for_document(connection, vault, doc_id)
        after = connection.execute(
            "SELECT current_revision_id, fts_status, quality_status, needs_review FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        revisions = connection.execute("SELECT COUNT(*) AS count FROM document_revisions WHERE doc_id = ?", (doc_id,)).fetchone()
        errors = connection.execute("SELECT COUNT(*) AS count FROM errors WHERE component = 'ocr'").fetchone()
        reviews = connection.execute("SELECT COUNT(*) AS count FROM review_items WHERE type = 'ocr_low_quality'").fetchone()
        baseline_search = search_chunks(connection, "pdfbaselineunique")
        ocr_search = search_chunks(connection, "ocrreplacementunique")
        task = connection.execute("SELECT status FROM tasks WHERE task_id = ?", (result.task_id,)).fetchone()
    finally:
        connection.close()

    assert ingest.status == "succeeded"
    assert result.status == "blocked"
    assert before["current_revision_id"] == after["current_revision_id"]
    assert after["fts_status"] == "indexed"
    assert after["quality_status"] == "passed"
    assert after["needs_review"] == 0
    assert revisions["count"] == 1
    assert errors["count"] == 0
    assert reviews["count"] == 0
    assert baseline_search.result_count == 1
    assert ocr_search.result_count == 0
    assert task["status"] == "failed"


def test_forced_ocr_failure_preserves_existing_current_revision_and_search(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF text")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: "# Paper\npreserve current revision needle\n")
    run_m3_ingest_pipeline(vault, source)
    doc_id, _original_path = _pdf_doc(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        before = connection.execute(
            "SELECT current_revision_id, fts_status, quality_status FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        fts_before = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts WHERE doc_id = ?", (doc_id,)).fetchone()
        result = run_ocr_for_document(connection, vault, doc_id, force=True)
        after = connection.execute(
            "SELECT current_revision_id, fts_status, quality_status, needs_review FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        fts_after = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts WHERE doc_id = ?", (doc_id,)).fetchone()
        search = search_chunks(connection, "preserve current revision")
    finally:
        connection.close()

    assert result.status == "failed"
    assert before["current_revision_id"] == after["current_revision_id"]
    assert after["fts_status"] == "indexed"
    assert after["quality_status"] == "passed"
    assert after["needs_review"] == 1
    assert fts_before["count"] == fts_after["count"]
    assert search.result_count == 1


def test_forced_ocr_success_replaces_current_revision_and_current_fts_only(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF text")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: "# Paper\noldpdfunique\n")
    run_m3_ingest_pipeline(vault, source)
    doc_id, original_path = _pdf_doc(vault, source)
    (vault / original_path).with_name("original.pdf.ocr.txt").write_text("newocronlyunique", encoding="utf-8")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        before = connection.execute("SELECT current_revision_id FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        result = run_ocr_for_document(connection, vault, doc_id, force=True)
        after = connection.execute("SELECT current_revision_id FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        revisions = list(
            connection.execute(
                """
                SELECT dr.revision_id, dr.converter_name,
                       SUM(CASE WHEN c.is_current = 1 THEN 1 ELSE 0 END) AS current_chunks
                FROM document_revisions dr
                LEFT JOIN chunks c ON c.revision_id = dr.revision_id
                WHERE dr.doc_id = ?
                GROUP BY dr.revision_id, dr.converter_name
                ORDER BY dr.sequence
                """,
                (doc_id,),
            )
        )
        old_search = search_chunks(connection, "oldpdfunique")
        new_search = search_chunks(connection, "newocronlyunique")
    finally:
        connection.close()

    assert result.status == "succeeded"
    assert result.revision_id is not None
    assert before["current_revision_id"] != after["current_revision_id"]
    assert after["current_revision_id"] == result.revision_id
    assert [row["converter_name"] for row in revisions] == ["markitdown", "ocr_sidecar"]
    assert revisions[0]["current_chunks"] == 0
    assert revisions[1]["current_chunks"] >= 1
    assert old_search.result_count == 0
    assert new_search.result_count == 1


def test_ocr_rerun_same_and_changed_content_follow_revision_rules(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)
    doc_id, original_path = _pdf_doc(vault, source)
    sidecar = (vault / original_path).with_name("original.pdf.ocr.txt")
    sidecar.write_text("sameuniquererun", encoding="utf-8")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        first = run_ocr_for_document(connection, vault, doc_id)
        same = run_ocr_for_document(connection, vault, doc_id, force=True)
        sidecar.write_text("changeduniquererun", encoding="utf-8")
        changed = run_ocr_for_document(connection, vault, doc_id, force=True)
        revision_count = connection.execute("SELECT COUNT(*) AS count FROM document_revisions WHERE doc_id = ?", (doc_id,)).fetchone()
        search_same = search_chunks(connection, "sameuniquererun")
        search_changed = search_chunks(connection, "changeduniquererun")
    finally:
        connection.close()

    assert first.status == "succeeded"
    assert same.status == "succeeded"
    assert same.revision_id == first.revision_id
    assert same.chunk_count == 0
    assert changed.status == "succeeded"
    assert changed.revision_id != first.revision_id
    assert revision_count["count"] == 2
    assert search_same.result_count == 0
    assert search_changed.result_count == 1


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        '{"page_number": 1, "text": "not a list"}',
        '[{"page_number": "1", "text": "bad page type"}]',
        '[{"page_number": 1, "text": "one"}, {"page_number": 1, "text": "duplicate"}]',
        '[{"page_number": 1, "text": "bad confidence", "confidence": "0.5"}]',
        '[{"page_number": 1, "text": "bad confidence", "confidence": 1.5}]',
    ],
)
def test_ocr_json_sidecar_malformed_inputs_fail_without_revision(tmp_path: Path, monkeypatch, payload: str) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)
    doc_id, original_path = _pdf_doc(vault, source)
    (vault / original_path).with_name("original.pdf.ocr.json").write_text(payload, encoding="utf-8")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = run_ocr_for_document(connection, vault, doc_id)
        revisions = connection.execute("SELECT COUNT(*) AS count FROM document_revisions WHERE doc_id = ?", (doc_id,)).fetchone()
        chunks = connection.execute("SELECT COUNT(*) AS count FROM chunks WHERE doc_id = ?", (doc_id,)).fetchone()
        fts = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts WHERE doc_id = ?", (doc_id,)).fetchone()
    finally:
        connection.close()

    assert result.status == "failed"
    assert revisions["count"] == 0
    assert chunks["count"] == 0
    assert fts["count"] == 0


def test_ocr_json_sidecar_sorts_pages_and_ignores_extra_fields(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)
    doc_id, original_path = _pdf_doc(vault, source)
    (vault / original_path).with_name("original.pdf.ocr.json").write_text(
        '[{"page_number": 2, "text": "second json page", "confidence": 0.9, "extra": true},'
        '{"page_number": 1, "text": "first json page", "confidence": 1.0}]',
        encoding="utf-8",
    )

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = run_ocr_for_document(connection, vault, doc_id)
        pages = list(connection.execute("SELECT page_number, text FROM ocr_pages WHERE doc_id = ? ORDER BY page_number", (doc_id,)))
    finally:
        connection.close()

    assert result.status == "succeeded"
    assert [row["page_number"] for row in pages] == [1, 2]
    assert "first json page" in pages[0]["text"]


def test_ocr_txt_sidecar_skips_empty_pages_and_supports_cjk_bom_crlf(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)
    doc_id, original_path = _pdf_doc(vault, source)
    (vault / original_path).with_name("original.pdf.ocr.txt").write_text(
        "\ufeff\r\n\f中文 OCR 知識管理\r\n\f\f日本語 OCR 大規模言語モデル\f",
        encoding="utf-8",
    )

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        result = run_ocr_for_document(connection, vault, doc_id)
        pages = list(connection.execute("SELECT page_number, text FROM ocr_pages WHERE doc_id = ? ORDER BY page_number", (doc_id,)))
        zh_search = search_chunks(connection, "知識管理")
        ja_search = search_chunks(connection, "大規模言語モデル")
    finally:
        connection.close()

    assert result.status == "succeeded"
    assert [row["page_number"] for row in pages] == [2, 4]
    assert zh_search.result_count == 1
    assert ja_search.result_count == 1
