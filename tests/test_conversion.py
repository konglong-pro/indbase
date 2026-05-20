from dataclasses import replace
import json
from pathlib import Path

import pytest

import indbase_core.conversion as conversion_module
import indbase_core.normalizers as normalizers
from indbase_core.archive import archive_pending_sources
from indbase_core.candidate_review import (
    accept_conversion_candidate_review,
    reject_conversion_candidate_review,
)
from indbase_core.config import default_config, save_config
from indbase_core.conversion import convert_archived_sources, hash_markdown
from indbase_core.db import connect
from indbase_core.ingest import plan_ingest_sources, run_m3_ingest_pipeline
from indbase_core.swallow_adapter import ArtifactManifest, ConversionCandidate, SwallowProvenance
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
    assert converter_run["converter_name"] == "swallow"
    assert converter_run["converter_version"] == "test"
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


@pytest.mark.no_fake_swallow_conversion
def test_convert_archived_source_without_swallow_records_retired_conversion_failure(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)

        result = convert_archived_sources(connection, vault, plan.ingest_id)
        converter_run = connection.execute(
            """
            SELECT converter_name, status, warnings_json
            FROM converter_runs
            """
        ).fetchone()
        ingest_item = connection.execute("SELECT status, error_id FROM ingest_items").fetchone()
        document = connection.execute("SELECT ingest_status, needs_review FROM documents").fetchone()
        review = connection.execute("SELECT target_type, target_id FROM review_items").fetchone()
        error = connection.execute("SELECT error_type, message FROM errors").fetchone()
    finally:
        connection.close()

    assert result.converted_items == ()
    assert result.failed_items == 1
    assert converter_run["converter_name"] == "swallow_required_gate"
    assert converter_run["status"] == "failed"
    assert "Legacy indbase conversion has been retired" in converter_run["warnings_json"]
    assert ingest_item["status"] == "failed"
    assert ingest_item["error_id"] is not None
    assert document["ingest_status"] == "failed"
    assert document["needs_review"] == 1
    assert review["target_type"] == "converter_run"
    assert error["error_type"] == "legacy_conversion_retired"
    assert "features.swallow_ingest" in error["message"]


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
    assert converter_run["converter_name"] == "swallow"
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
    assert converter_run["converter_name"] == "swallow"
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
    assert converter_run["converter_name"] == "swallow"
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
    assert converter_run["converter_name"] == "swallow"
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
    assert converter_run["converter_name"] == "swallow"
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


def test_swallow_enabled_conversion_records_candidate_and_provenance(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    config = default_config(vault)
    config = replace(
        config,
        features=replace(config.features, swallow_ingest=True),
        ingest=replace(
            config.ingest,
            swallow=replace(config.ingest.swallow, min_markdown_chars=10),
        ),
    )
    save_config(config, vault / ".indbase" / "config" / "config.toml")
    source = tmp_path / "note.md"
    source.write_text("# Note\nSwallow converted body with enough text.\n", encoding="utf-8")
    trace = vault / ".indbase" / "cache" / "swallow" / "jobs" / "swallow_job_1" / "trace.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text('{"event":"ok"}\n', encoding="utf-8")

    class FakeSwallowAdapter:
        def __init__(self, *, vault_path, config) -> None:
            self.vault_path = vault_path
            self.config = config

        def convert_file(self, path: Path) -> ConversionCandidate:
            return ConversionCandidate(
                title="Note",
                markdown_body="# Note\nSwallow converted body with enough text.\n",
                status="success",
                quality_score=0.91,
                warnings=(),
                provenance=SwallowProvenance(
                    swallow_job_id="swallow_job_1",
                    swallow_raw_id="raw_1",
                    swallow_document_id="ingdoc_1",
                    swallow_version="0.1.0-test",
                    primary_worker="plain_text_worker",
                    worker_version="test",
                    worker_chain=("plain_text_worker@test", "quality_checker@test"),
                    trace_path="jobs/swallow_job_1/trace.jsonl",
                    manifest_path=None,
                    ingest_document_path=None,
                ),
                artifact_manifest=ArtifactManifest(required=("jobs/swallow_job_1/trace.jsonl",)),
            )

    monkeypatch.setattr(conversion_module, "SwallowIngestAdapter", FakeSwallowAdapter)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)

        result = convert_archived_sources(connection, vault, plan.ingest_id)
        converted = result.converted_items[0]
        converter_run = connection.execute(
            """
            SELECT converter_name, converter_version, external_job_id,
                   external_trace_path, primary_worker, worker_chain_json,
                   candidate_path, artifact_manifest_json, promotion_status,
                   promotion_reason, status
            FROM converter_runs
            WHERE converter_run_id = ?
            """,
            (converted.converter_run_id,),
        ).fetchone()
        document = connection.execute(
            "SELECT ingest_status, quality_status, needs_review FROM documents WHERE doc_id = ?",
            (converted.doc_id,),
        ).fetchone()
    finally:
        connection.close()

    assert result.failed_items == 0
    assert result.skipped_items == 0
    assert converted.markdown == "# Note\nSwallow converted body with enough text.\n"
    assert converter_run["converter_name"] == "swallow"
    assert converter_run["converter_version"] == "0.1.0-test"
    assert converter_run["external_job_id"] == "swallow_job_1"
    assert converter_run["external_trace_path"] == "jobs/swallow_job_1/trace.jsonl"
    assert converter_run["primary_worker"] == "plain_text_worker"
    assert "plain_text_worker@test" in converter_run["worker_chain_json"]
    assert converter_run["candidate_path"] == converted.candidate_path
    assert ".indbase/artifacts/" in converter_run["artifact_manifest_json"]
    assert converter_run["promotion_status"] == "trusted-current"
    assert converter_run["promotion_reason"] == "Swallow candidate passed indbase promotion gate."
    assert converter_run["status"] == "succeeded"
    assert document["ingest_status"] == "converted"
    assert document["quality_status"] == "passed"
    assert document["needs_review"] == 0


def test_swallow_candidate_with_non_archived_locator_evidence_requires_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    config = default_config(vault)
    config = replace(
        config,
        features=replace(config.features, swallow_ingest=True, web_ingest=True),
        ingest=replace(
            config.ingest,
            swallow=replace(config.ingest.swallow, min_markdown_chars=10),
        ),
    )
    save_config(config, vault / ".indbase" / "config" / "config.toml")
    source = tmp_path / "note.md"
    source.write_text("# Note\nSwallow locator evidence body.\n", encoding="utf-8")
    trace = vault / ".indbase" / "cache" / "swallow" / "jobs" / "locator_job" / "trace.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text('{"event":"ok"}\n', encoding="utf-8")

    class FakeSwallowAdapter:
        def __init__(self, *, vault_path, config) -> None:
            self.vault_path = vault_path
            self.config = config

        def convert_file(self, path: Path) -> ConversionCandidate:
            return ConversionCandidate(
                title="Note",
                markdown_body="# Note\nSwallow locator evidence body.\n",
                status="success",
                quality_score=0.95,
                warnings=(),
                provenance=SwallowProvenance(
                    swallow_job_id="locator_job",
                    swallow_raw_id="raw_locator",
                    swallow_document_id="ingdoc_locator",
                    swallow_version="0.1.0-test",
                    primary_worker="playwright_worker",
                    worker_version="test",
                    worker_chain=("playwright_worker@test", "quality_checker@test"),
                    trace_path="jobs/locator_job/trace.jsonl",
                    manifest_path=None,
                    ingest_document_path=None,
                ),
                artifact_manifest=ArtifactManifest(required=("jobs/locator_job/trace.jsonl",)),
                source_locators=(
                    {
                        "kind": "web_snapshot",
                        "url": "https://example.com/missing",
                        "artifact": "jobs/locator_job/intermediate/playwright/rendered.html",
                    },
                ),
                source_snapshot_path="jobs/locator_job/intermediate/playwright/rendered.html",
            )

    monkeypatch.setattr(conversion_module, "SwallowIngestAdapter", FakeSwallowAdapter)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)
        result = convert_archived_sources(connection, vault, plan.ingest_id)
        converter_run = connection.execute(
            """
            SELECT status, promotion_status, promotion_reason, artifact_manifest_json
            FROM converter_runs
            """
        ).fetchone()
        document = connection.execute(
            "SELECT current_revision_id, ingest_status, needs_review FROM documents"
        ).fetchone()
        review = connection.execute(
            "SELECT type, target_type FROM review_items WHERE type = 'conversion_pending_review'"
        ).fetchone()
    finally:
        connection.close()

    assert result.converted_items == ()
    assert result.failed_items == 0
    assert converter_run["status"] == "pending_review"
    assert converter_run["promotion_status"] == "review-before-current"
    assert "missing durable evidence artifacts" in converter_run["promotion_reason"]
    assert "rendered.html" in converter_run["promotion_reason"]
    assert ".indbase/artifacts/" in converter_run["artifact_manifest_json"]
    assert document["current_revision_id"] is None
    assert document["ingest_status"] == "pending_review"
    assert document["needs_review"] == 1
    assert review["target_type"] == "converter_run"


def test_swallow_ocr_candidate_writes_durable_chunk_source_locators(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    config = default_config(vault)
    config = replace(
        config,
        features=replace(config.features, swallow_ingest=True, ocr=True),
        ingest=replace(
            config.ingest,
            swallow=replace(config.ingest.swallow, min_markdown_chars=10),
        ),
    )
    save_config(config, vault / ".indbase" / "config" / "config.toml")
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF-1.4 fake")

    class FakeSwallowAdapter:
        def __init__(self, *, vault_path, config) -> None:
            self.store_root = Path(vault_path) / ".indbase" / "cache" / "swallow"

        def convert_file(self, path: Path) -> ConversionCandidate:
            result_json = self.store_root / "jobs" / "ocr_job" / "intermediate" / "paddleocr" / "result.json"
            page_image = self.store_root / "jobs" / "ocr_job" / "intermediate" / "pages" / "page_0001.png"
            trace = self.store_root / "jobs" / "ocr_job" / "trace.jsonl"
            result_json.parent.mkdir(parents=True, exist_ok=True)
            page_image.parent.mkdir(parents=True, exist_ok=True)
            result_json.write_text('[{"page_number":1,"entries":[{"text":"ocr locator needle"}]}]', encoding="utf-8")
            page_image.write_bytes(b"page")
            trace.write_text("{}", encoding="utf-8")
            return ConversionCandidate(
                title="scan",
                markdown_body="# Scan\n\n## Page 1\nocr locator needle searchable.\n",
                status="success",
                quality_score=0.95,
                provenance=SwallowProvenance(
                    swallow_job_id="ocr_job",
                    swallow_raw_id="raw_ocr",
                    swallow_document_id="ingdoc_ocr",
                    swallow_version="0.1.0-test",
                    primary_worker="paddleocr_worker",
                    worker_version="0.1.0",
                    worker_chain=("paddleocr_worker@0.1.0", "quality_checker@0.1.0"),
                    trace_path="jobs/ocr_job/trace.jsonl",
                    manifest_path=None,
                    ingest_document_path=None,
                ),
                artifact_manifest=ArtifactManifest(
                    required=(
                        "jobs/ocr_job/trace.jsonl",
                        "jobs/ocr_job/intermediate/paddleocr/result.json",
                        "jobs/ocr_job/intermediate/pages/page_0001.png",
                    )
                ),
                source_locators=(
                    {
                        "kind": "ocr_page",
                        "page": 1,
                        "artifact": "jobs/ocr_job/intermediate/paddleocr/result.json",
                        "page_image_artifact": "jobs/ocr_job/intermediate/pages/page_0001.png",
                    },
                ),
            )

    monkeypatch.setattr(conversion_module, "SwallowIngestAdapter", FakeSwallowAdapter)

    result = run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        document = connection.execute("SELECT original_path, current_revision_id FROM documents").fetchone()
        locator_row = connection.execute(
            "SELECT source_locator_json FROM chunks WHERE source_locator_json IS NOT NULL"
        ).fetchone()
    finally:
        connection.close()

    locators = json.loads(locator_row["source_locator_json"])
    locator = locators[0]
    assert result.status == "succeeded"
    assert document["current_revision_id"] is not None
    assert locator["kind"] == "ocr_page"
    assert locator["page"] == 1
    assert locator["source_path"] == document["original_path"]
    assert locator["artifact"].startswith(".indbase/artifacts/")
    assert locator["artifact"].endswith("result.json")
    assert (vault / locator["artifact"]).is_file()
    assert locator["page_image_artifact"].startswith(".indbase/artifacts/")
    assert locator["page_image_artifact"].endswith("page_0001.png")
    assert (vault / locator["page_image_artifact"]).is_file()


def test_swallow_asr_candidate_writes_durable_chunk_source_locators(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    config = default_config(vault)
    config = replace(
        config,
        features=replace(config.features, swallow_ingest=True, asr=True),
        ingest=replace(
            config.ingest,
            swallow=replace(config.ingest.swallow, min_markdown_chars=10),
            tier2_extensions=(*config.ingest.tier2_extensions, "mp3"),
        ),
    )
    save_config(config, vault / ".indbase" / "config" / "config.toml")
    source = tmp_path / "clip.mp3"
    source.write_bytes(b"fake mp3")

    class FakeSwallowAdapter:
        def __init__(self, *, vault_path, config) -> None:
            self.store_root = Path(vault_path) / ".indbase" / "cache" / "swallow"

        def convert_file(self, path: Path) -> ConversionCandidate:
            transcript = self.store_root / "jobs" / "asr_job" / "intermediate" / "asr" / "transcript.json"
            audio = self.store_root / "jobs" / "asr_job" / "intermediate" / "audio" / "normalized.wav"
            trace = self.store_root / "jobs" / "asr_job" / "trace.jsonl"
            transcript.parent.mkdir(parents=True, exist_ok=True)
            audio.parent.mkdir(parents=True, exist_ok=True)
            transcript.write_text(
                '{"segments":[{"start":1.25,"end":3.5,"text":"asr locator needle"}]}',
                encoding="utf-8",
            )
            audio.write_bytes(b"wav")
            trace.write_text("{}", encoding="utf-8")
            return ConversionCandidate(
                title="clip",
                markdown_body="# Transcript\n\n[00:00:01.250 --> 00:00:03.500] asr locator needle searchable.\n",
                status="success",
                quality_score=0.95,
                provenance=SwallowProvenance(
                    swallow_job_id="asr_job",
                    swallow_raw_id="raw_asr",
                    swallow_document_id="ingdoc_asr",
                    swallow_version="0.1.0-test",
                    primary_worker="faster_whisper_worker",
                    worker_version="0.1.0",
                    worker_chain=("faster_whisper_worker@0.1.0", "quality_checker@0.1.0"),
                    trace_path="jobs/asr_job/trace.jsonl",
                    manifest_path=None,
                    ingest_document_path=None,
                ),
                artifact_manifest=ArtifactManifest(
                    required=(
                        "jobs/asr_job/trace.jsonl",
                        "jobs/asr_job/intermediate/asr/transcript.json",
                        "jobs/asr_job/intermediate/audio/normalized.wav",
                    )
                ),
                source_locators=(
                    {
                        "kind": "asr_segment",
                        "start_seconds": 1.25,
                        "end_seconds": 3.5,
                        "artifact": "jobs/asr_job/intermediate/asr/transcript.json",
                        "normalized_audio_artifact": "jobs/asr_job/intermediate/audio/normalized.wav",
                    },
                ),
            )

    monkeypatch.setattr(conversion_module, "SwallowIngestAdapter", FakeSwallowAdapter)

    result = run_m3_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        document = connection.execute("SELECT original_path, current_revision_id FROM documents").fetchone()
        locator_row = connection.execute(
            "SELECT source_locator_json FROM chunks WHERE source_locator_json IS NOT NULL"
        ).fetchone()
    finally:
        connection.close()

    locator = json.loads(locator_row["source_locator_json"])[0]
    assert result.status == "succeeded"
    assert document["current_revision_id"] is not None
    assert locator["kind"] == "asr_segment"
    assert locator["start_seconds"] == 1.25
    assert locator["end_seconds"] == 3.5
    assert locator["source_path"] == document["original_path"]
    assert locator["artifact"].startswith(".indbase/artifacts/")
    assert locator["artifact"].endswith("transcript.json")
    assert (vault / locator["artifact"]).is_file()
    assert locator["normalized_audio_artifact"].startswith(".indbase/artifacts/")
    assert locator["normalized_audio_artifact"].endswith("normalized.wav")
    assert (vault / locator["normalized_audio_artifact"]).is_file()


def test_swallow_low_quality_candidate_goes_to_review_without_revision(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    config = default_config(vault)
    config = replace(
        config,
        features=replace(config.features, swallow_ingest=True),
        ingest=replace(
            config.ingest,
            swallow=replace(config.ingest.swallow, min_markdown_chars=10, archive_required_artifacts=False),
        ),
    )
    save_config(config, vault / ".indbase" / "config" / "config.toml")
    source = tmp_path / "note.md"
    source.write_text("# Note\nLow quality body.\n", encoding="utf-8")

    class FakeSwallowAdapter:
        def __init__(self, *, vault_path, config) -> None:
            pass

        def convert_file(self, path: Path) -> ConversionCandidate:
            return ConversionCandidate(
                title="Note",
                markdown_body="# Note\nLow quality body.\n",
                status="success",
                quality_score=0.50,
                provenance=SwallowProvenance(
                    swallow_job_id="swallow_job_low",
                    swallow_raw_id="raw_low",
                    swallow_document_id="ingdoc_low",
                    swallow_version="0.1.0-test",
                    primary_worker="plain_text_worker",
                    worker_version="test",
                    worker_chain=("plain_text_worker@test",),
                    trace_path="jobs/swallow_job_low/trace.jsonl",
                    manifest_path=None,
                    ingest_document_path=None,
                ),
            )

    monkeypatch.setattr(conversion_module, "SwallowIngestAdapter", FakeSwallowAdapter)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)

        result = convert_archived_sources(connection, vault, plan.ingest_id)
        converter_run = connection.execute(
            """
            SELECT converter_run_id, status, promotion_status, promotion_reason
            FROM converter_runs
            """
        ).fetchone()
        document = connection.execute(
            "SELECT ingest_status, current_revision_id, needs_review FROM documents"
        ).fetchone()
        review = connection.execute(
            """
            SELECT type, target_type, target_id, status
            FROM review_items
            WHERE type = 'conversion_pending_review'
            """
        ).fetchone()
    finally:
        connection.close()

    assert result.converted_items == ()
    assert result.failed_items == 0
    assert result.skipped_items == 1
    assert converter_run["status"] == "pending_review"
    assert converter_run["promotion_status"] == "review-before-current"
    assert converter_run["promotion_reason"] == "Swallow candidate requires review."
    assert document["ingest_status"] == "pending_review"
    assert document["current_revision_id"] is None
    assert document["needs_review"] == 1
    assert review["target_type"] == "converter_run"
    assert review["target_id"] == converter_run["converter_run_id"]
    assert review["status"] == "pending"


def test_accept_swallow_candidate_review_promotes_revision_chunks_and_fts(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    config = default_config(vault)
    config = replace(
        config,
        features=replace(config.features, swallow_ingest=True),
        ingest=replace(
            config.ingest,
            swallow=replace(config.ingest.swallow, min_markdown_chars=10, archive_required_artifacts=False),
        ),
    )
    save_config(config, vault / ".indbase" / "config" / "config.toml")
    source = tmp_path / "note.md"
    source.write_text("# Note\nReview body with searchable promoted text.\n", encoding="utf-8")

    class FakeSwallowAdapter:
        def __init__(self, *, vault_path, config) -> None:
            pass

        def convert_file(self, path: Path) -> ConversionCandidate:
            return ConversionCandidate(
                title="Note",
                markdown_body="# Note\nReview body with searchable promoted text.\n",
                status="success",
                quality_score=0.50,
                provenance=SwallowProvenance(
                    swallow_job_id="swallow_job_review",
                    swallow_raw_id="raw_review",
                    swallow_document_id="ingdoc_review",
                    swallow_version="0.1.0-test",
                    primary_worker="plain_text_worker",
                    worker_version="test",
                    worker_chain=("plain_text_worker@test",),
                    trace_path="jobs/swallow_job_review/trace.jsonl",
                    manifest_path=None,
                    ingest_document_path=None,
                ),
            )

    monkeypatch.setattr(conversion_module, "SwallowIngestAdapter", FakeSwallowAdapter)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)
        convert_archived_sources(connection, vault, plan.ingest_id)
        review = connection.execute(
            "SELECT review_id FROM review_items WHERE type = 'conversion_pending_review'"
        ).fetchone()

        accepted = accept_conversion_candidate_review(
            connection,
            vault,
            review["review_id"],
            note="looks good",
            resolved_by="test",
        )

        document = connection.execute(
            """
            SELECT current_revision_id, ingest_status, quality_status, needs_review, fts_status
            FROM documents
            WHERE doc_id = ?
            """,
            (accepted.doc_id,),
        ).fetchone()
        converter_run = connection.execute(
            """
            SELECT status, promotion_status, revision_id
            FROM converter_runs
            WHERE converter_run_id = ?
            """,
            (accepted.converter_run_id,),
        ).fetchone()
        chunks = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        fts = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        resolved = connection.execute(
            "SELECT status, resolved_by, resolution_note FROM review_items WHERE review_id = ?",
            (review["review_id"],),
        ).fetchone()
        ingest_item = connection.execute("SELECT status FROM ingest_items").fetchone()
    finally:
        connection.close()

    assert accepted.action == "accepted"
    assert accepted.revision_id is not None
    assert document["current_revision_id"] == accepted.revision_id
    assert document["ingest_status"] == "revisioned"
    assert document["quality_status"] == "passed"
    assert document["needs_review"] == 0
    assert document["fts_status"] == "indexed"
    assert converter_run["status"] == "succeeded"
    assert converter_run["promotion_status"] == "promoted"
    assert converter_run["revision_id"] == accepted.revision_id
    assert chunks["count"] >= 1
    assert fts["count"] >= 1
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by"] == "test"
    assert resolved["resolution_note"] == "looks good"
    assert ingest_item["status"] == "succeeded"


def test_reject_swallow_candidate_review_leaves_no_revision(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    config = default_config(vault)
    config = replace(
        config,
        features=replace(config.features, swallow_ingest=True),
        ingest=replace(
            config.ingest,
            swallow=replace(config.ingest.swallow, min_markdown_chars=10, archive_required_artifacts=False),
        ),
    )
    save_config(config, vault / ".indbase" / "config" / "config.toml")
    source = tmp_path / "note.md"
    source.write_text("# Note\nReject body.\n", encoding="utf-8")

    class FakeSwallowAdapter:
        def __init__(self, *, vault_path, config) -> None:
            pass

        def convert_file(self, path: Path) -> ConversionCandidate:
            return ConversionCandidate(
                title="Note",
                markdown_body="# Note\nReject body.\n",
                status="success",
                quality_score=0.50,
                provenance=SwallowProvenance(
                    swallow_job_id="swallow_job_reject",
                    swallow_raw_id="raw_reject",
                    swallow_document_id="ingdoc_reject",
                    swallow_version="0.1.0-test",
                    primary_worker="plain_text_worker",
                    worker_version="test",
                    worker_chain=("plain_text_worker@test",),
                    trace_path="jobs/swallow_job_reject/trace.jsonl",
                    manifest_path=None,
                    ingest_document_path=None,
                ),
            )

    monkeypatch.setattr(conversion_module, "SwallowIngestAdapter", FakeSwallowAdapter)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)
        convert_archived_sources(connection, vault, plan.ingest_id)
        review = connection.execute(
            "SELECT review_id FROM review_items WHERE type = 'conversion_pending_review'"
        ).fetchone()

        rejected = reject_conversion_candidate_review(
            connection,
            review["review_id"],
            note="not useful",
            resolved_by="test",
        )

        document = connection.execute(
            "SELECT current_revision_id, ingest_status, needs_review, quality_status FROM documents"
        ).fetchone()
        converter_run = connection.execute(
            "SELECT status, promotion_status, revision_id FROM converter_runs"
        ).fetchone()
        chunks = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        fts = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        ingest_item = connection.execute("SELECT status FROM ingest_items").fetchone()
    finally:
        connection.close()

    assert rejected.action == "rejected"
    assert document["current_revision_id"] is None
    assert document["ingest_status"] == "failed"
    assert document["needs_review"] == 0
    assert document["quality_status"] == "failed"
    assert converter_run["status"] == "rejected"
    assert converter_run["promotion_status"] == "rejected"
    assert converter_run["revision_id"] is None
    assert chunks["count"] == 0
    assert fts["count"] == 0
    assert ingest_item["status"] == "rejected"


def test_swallow_enabled_conversion_failure_is_visible(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    config = default_config(vault)
    config = replace(config, features=replace(config.features, swallow_ingest=True))
    save_config(config, vault / ".indbase" / "config" / "config.toml")
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")

    class FakeSwallowAdapter:
        def __init__(self, *, vault_path, config) -> None:
            pass

        def convert_file(self, path: Path) -> ConversionCandidate:
            raise RuntimeError("swallow missing")

    monkeypatch.setattr(conversion_module, "SwallowIngestAdapter", FakeSwallowAdapter)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        plan = plan_ingest_sources(connection, source)
        archive_pending_sources(connection, vault, plan.ingest_id)

        result = convert_archived_sources(connection, vault, plan.ingest_id)
        converter_run = connection.execute(
            "SELECT converter_name, status, warnings_json FROM converter_runs"
        ).fetchone()
        ingest_item = connection.execute("SELECT status, error_id FROM ingest_items").fetchone()
        review = connection.execute("SELECT target_type, target_id FROM review_items").fetchone()
    finally:
        connection.close()

    assert result.converted_items == ()
    assert result.failed_items == 1
    assert converter_run["converter_name"] == "swallow"
    assert converter_run["status"] == "failed"
    assert "swallow missing" in converter_run["warnings_json"]
    assert ingest_item["status"] == "failed"
    assert ingest_item["error_id"] is not None
    assert review["target_type"] == "converter_run"
    assert review["target_id"] is not None
