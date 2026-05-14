from pathlib import Path

import indbase_core.normalizers as normalizers
from indbase_core.archive import archive_pending_sources
from indbase_core.conversion import convert_archived_sources, hash_markdown
from indbase_core.db import connect
from indbase_core.ingest import plan_ingest_sources
from indbase_core.vault import init_vault


def test_convert_archived_markdown_source_records_converter_run(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_bytes(b"# Note\r\nBody")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)

        result = convert_archived_sources(connection, vault, plan.ingest_id)

        assert result.failed_items == 0
        assert result.skipped_items == 0
        assert len(result.converted_items) == 1
        converted = result.converted_items[0]
        candidate = vault / converted.candidate_path
        converter_run = connection.execute(
            """
            SELECT converter_name, converter_version, output_hash, warnings_json, status
            FROM converter_runs
            WHERE converter_run_id = ?
            """,
            (converted.converter_run_id,),
        ).fetchone()
        document = connection.execute(
            "SELECT ingest_status, quality_status, current_revision_id, canonical_path FROM documents WHERE doc_id = ?",
            (converted.doc_id,),
        ).fetchone()
        ingest_item = connection.execute(
            "SELECT status FROM ingest_items WHERE ingest_item_id = ?",
            (converted.ingest_item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert converted.markdown == "# Note\nBody\n"
    assert converted.output_hash == hash_markdown("# Note\nBody\n")
    assert candidate.is_file()
    assert candidate.read_text(encoding="utf-8") == "# Note\nBody\n"
    assert converter_run["converter_name"] == "direct_normalizer"
    assert converter_run["converter_version"] == "indbase.v0.1"
    assert converter_run["output_hash"] == converted.output_hash
    assert converter_run["warnings_json"] == "[]"
    assert converter_run["status"] == "succeeded"
    assert document["ingest_status"] == "converted"
    assert document["quality_status"] == "passed"
    assert document["current_revision_id"] is None
    assert document["canonical_path"] is None
    assert ingest_item["status"] == "running"


def test_convert_archived_csv_and_json_sources(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "data.csv").write_text("name,value\nalpha,1\n", encoding="utf-8")
    (sources / "data.json").write_text('{"b": 2, "a": 1}', encoding="utf-8")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, sources)
        archive_pending_sources(connection, vault, plan.ingest_id)

        result = convert_archived_sources(connection, vault, plan.ingest_id)
        markdown_outputs = sorted(item.markdown for item in result.converted_items)
        run_count = connection.execute("SELECT COUNT(*) AS count FROM converter_runs").fetchone()
    finally:
        connection.close()

    assert result.failed_items == 0
    assert result.skipped_items == 0
    assert run_count["count"] == 2
    assert markdown_outputs == [
        "```json\n{\n  \"a\": 1,\n  \"b\": 2\n}\n```\n",
        "| name | value |\n| --- | --- |\n| alpha | 1 |\n",
    ]


def test_convert_archived_html_source_records_converter_run(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "page.html"
    source.write_text("<html><body><h1>Hello</h1><p>World</p></body></html>", encoding="utf-8")
    monkeypatch.setattr(normalizers, "_run_markitdown_html", lambda _path: "# Hello\n\nWorld\n")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)

        result = convert_archived_sources(connection, vault, plan.ingest_id)
        converter_run = connection.execute(
            """
            SELECT converter_name, warnings_json, status
            FROM converter_runs
            """
        ).fetchone()
    finally:
        connection.close()

    assert len(result.converted_items) == 1
    assert result.skipped_items == 0
    assert result.failed_items == 0
    assert "# Hello" in result.converted_items[0].markdown
    assert "World" in result.converted_items[0].markdown
    assert converter_run["converter_name"] == "markitdown"
    assert converter_run["warnings_json"] == "[]"
    assert converter_run["status"] == "succeeded"


def test_convert_archived_html_source_records_fallback_warning(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "page.html"
    source.write_text("<h1>Hello</h1><p>World</p>", encoding="utf-8")

    def fail_markitdown(_path: Path) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(normalizers, "_run_markitdown_html", fail_markitdown)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)

        result = convert_archived_sources(connection, vault, plan.ingest_id)
        converter_run = connection.execute(
            "SELECT converter_name, warnings_json FROM converter_runs"
        ).fetchone()
        document = connection.execute(
            "SELECT quality_status FROM documents WHERE doc_id = ?",
            (result.converted_items[0].doc_id,),
        ).fetchone()
    finally:
        connection.close()

    assert result.failed_items == 0
    assert result.skipped_items == 0
    assert result.converted_items[0].markdown == "# Hello\n\nWorld\n"
    assert result.converted_items[0].warnings == ("markitdown_failed_fallback_html",)
    assert converter_run["converter_name"] == "html_fallback_normalizer"
    assert converter_run["warnings_json"] == '["markitdown_failed_fallback_html"]'
    assert document["quality_status"] == "warning"


def test_convert_archived_tier2_source_uses_markitdown_when_available(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "slides.pptx"
    source.write_bytes(b"placeholder office bytes")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: "# Slides\nTier2 body\n")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)

        result = convert_archived_sources(connection, vault, plan.ingest_id)
        converter_run = connection.execute(
            """
            SELECT converter_name, status, output_hash, warnings_json
            FROM converter_runs
            """
        ).fetchone()
        document = connection.execute(
            "SELECT ingest_status, quality_status, needs_review FROM documents"
        ).fetchone()
        errors = connection.execute("SELECT COUNT(*) AS count FROM errors").fetchone()
        reviews = connection.execute("SELECT COUNT(*) AS count FROM review_items").fetchone()
    finally:
        connection.close()

    assert result.failed_items == 0
    assert result.skipped_items == 0
    assert len(result.converted_items) == 1
    assert result.converted_items[0].markdown == "# Slides\nTier2 body\n"
    assert converter_run["converter_name"] == "markitdown"
    assert converter_run["status"] == "succeeded"
    assert converter_run["output_hash"] == hash_markdown("# Slides\nTier2 body\n")
    assert converter_run["warnings_json"] == "[]"
    assert document["ingest_status"] == "converted"
    assert document["quality_status"] == "passed"
    assert document["needs_review"] == 0
    assert errors["count"] == 0
    assert reviews["count"] == 0


def test_convert_archived_pdf_source_uses_markitdown_when_available(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "paper.pdf"
    source.write_bytes(b"%PDF placeholder bytes")
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: "# Paper\nPDF text body\n")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)

        result = convert_archived_sources(connection, vault, plan.ingest_id)
        converter_run = connection.execute(
            """
            SELECT converter_name, status, output_hash, warnings_json
            FROM converter_runs
            """
        ).fetchone()
        document = connection.execute(
            "SELECT source_type, ingest_status, quality_status, needs_review FROM documents"
        ).fetchone()
    finally:
        connection.close()

    assert result.failed_items == 0
    assert result.skipped_items == 0
    assert len(result.converted_items) == 1
    assert result.converted_items[0].markdown == "# Paper\nPDF text body\n"
    assert converter_run["converter_name"] == "markitdown"
    assert converter_run["status"] == "succeeded"
    assert converter_run["output_hash"] == hash_markdown("# Paper\nPDF text body\n")
    assert converter_run["warnings_json"] == "[]"
    assert document["source_type"] == "pdf"
    assert document["ingest_status"] == "converted"
    assert document["quality_status"] == "passed"
    assert document["needs_review"] == 0


def test_convert_archived_tier2_source_records_visible_failure(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "slides.pptx"
    source.write_bytes(b"placeholder office bytes")

    def fail_markitdown(_path: Path) -> str:
        raise RuntimeError("markitdown boom")

    monkeypatch.setattr(normalizers, "_run_markitdown_file", fail_markitdown)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)

        result = convert_archived_sources(connection, vault, plan.ingest_id)
        converter_run = connection.execute(
            """
            SELECT converter_run_id, converter_name, status, warnings_json
            FROM converter_runs
            """
        ).fetchone()
        ingest_item = connection.execute(
            "SELECT status, error_id, finished_at FROM ingest_items WHERE ingest_id = ?",
            (plan.ingest_id,),
        ).fetchone()
        document = connection.execute(
            "SELECT ingest_status, fts_status, needs_review, quality_status FROM documents"
        ).fetchone()
        review = connection.execute(
            """
            SELECT type, target_type, target_id, status
            FROM review_items
            WHERE type = 'conversion_low_quality'
            """
        ).fetchone()
        errors = connection.execute("SELECT COUNT(*) AS count FROM errors").fetchone()
    finally:
        connection.close()

    assert result.converted_items == ()
    assert result.skipped_items == 0
    assert result.failed_items == 1
    assert converter_run["converter_name"] == "markitdown"
    assert converter_run["status"] == "failed"
    assert "markitdown boom" in converter_run["warnings_json"]
    assert ingest_item["status"] == "failed"
    assert ingest_item["error_id"] is not None
    assert ingest_item["finished_at"] is not None
    assert document["ingest_status"] == "failed"
    assert document["fts_status"] == "not_indexed"
    assert document["needs_review"] == 1
    assert document["quality_status"] == "failed"
    assert review["target_type"] == "converter_run"
    assert review["target_id"] == converter_run["converter_run_id"]
    assert review["status"] == "pending"
    assert errors["count"] == 1
