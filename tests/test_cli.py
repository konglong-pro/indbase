from dataclasses import replace
import json
import zipfile

from typer.testing import CliRunner

import indbase_core.normalizers as normalizers
from indbase_core.chunker import chunk_current_revision
from indbase_core.config import load_config, save_config
from indbase_core.db import connect
from indbase_core.ingest import run_m2_ingest_pipeline
from indbase_core.swallow_adapter import (
    ArchiveExpansionCandidate,
    ArchiveLogicalDocumentCandidate,
    ArtifactManifest,
    ConversionCandidate,
    SwallowProvenance,
)
from indbase_cli.main import app


def test_cli_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "indbase" in result.output


def test_cli_init_catalog_task_and_doctor(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"

    init_result = runner.invoke(app, ["init", str(vault_path)])

    assert init_result.exit_code == 0
    assert (vault_path / ".indbase" / "db.sqlite").is_file()
    assert (vault_path / ".indbase" / "config" / "config.toml").is_file()

    catalog_result = runner.invoke(app, ["catalog", "list", "--vault", str(vault_path)])
    assert catalog_result.exit_code == 0
    assert "cat_uncategorized" in catalog_result.output

    add_result = runner.invoke(app, ["catalog", "add", "Research", "--vault", str(vault_path)])
    assert add_result.exit_code == 0
    assert "Research" in add_result.output

    task_list_result = runner.invoke(app, ["task", "list", "--vault", str(vault_path)])
    assert task_list_result.exit_code == 0
    assert "vault_init" in task_list_result.output

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        task_id = connection.execute("SELECT task_id FROM tasks LIMIT 1").fetchone()["task_id"]
    finally:
        connection.close()

    task_show_result = runner.invoke(app, ["task", "show", task_id, "--vault", str(vault_path)])
    assert task_show_result.exit_code == 0
    assert task_id in task_show_result.output

    doctor_result = runner.invoke(app, ["doctor", "--vault", str(vault_path), "--json"])
    assert doctor_result.exit_code in {0, 1}
    assert '"vault_path"' in doctor_result.output


def test_cli_ingest_runs_m3_searchable_pipeline(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# CLI\nBody\n", encoding="utf-8")

    init_result = runner.invoke(app, ["init", str(vault_path)])
    ingest_result = runner.invoke(app, ["ingest", str(source), "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        revision_count = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
        chunks = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        ingest_run = connection.execute(
            "SELECT status, succeeded_items FROM ingest_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        connection.close()

    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 0
    assert "Revisions written: 1" in ingest_result.output
    assert "Searchable: yes" in ingest_result.output
    assert "Chunks written: 1" in ingest_result.output
    assert "Indexed chunks: 1" in ingest_result.output
    assert revision_count["count"] == 1
    assert chunks["count"] == 1
    assert ingest_run["status"] == "succeeded"
    assert ingest_run["succeeded_items"] == 1


def test_cli_ingest_file_and_folder_source_type_commands(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    file_source = tmp_path / "file-note.md"
    folder_source = tmp_path / "folder"
    nested = folder_source / "nested"
    file_source.write_text("# File Source\nBody\n", encoding="utf-8")
    nested.mkdir(parents=True)
    (nested / "folder-note.md").write_text("# Folder Source\nBody\n", encoding="utf-8")

    init_result = runner.invoke(app, ["init", str(vault_path)])
    file_result = runner.invoke(app, ["ingest", "file", str(file_source), "--vault", str(vault_path)])
    folder_result = runner.invoke(
        app,
        ["ingest", "folder", str(folder_source), "--recursive", "--vault", str(vault_path)],
    )

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        revision_count = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
    finally:
        connection.close()

    assert init_result.exit_code == 0
    assert file_result.exit_code == 0
    assert "Revisions written: 1" in file_result.output
    assert folder_result.exit_code == 0
    assert "Total: 1" in folder_result.output
    assert "Searchable: yes" in folder_result.output
    assert revision_count["count"] == 2


def test_cli_ingest_url_source_type_is_feature_gated(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"

    init_result = runner.invoke(app, ["init", str(vault_path)])
    disabled_result = runner.invoke(
        app,
        ["ingest", "url", "https://example.com", "--vault", str(vault_path)],
    )

    config_path = vault_path / ".indbase" / "config" / "config.toml"
    config = load_config(config_path)
    save_config(replace(config, features=replace(config.features, swallow_ingest=True)), config_path)
    web_disabled_result = runner.invoke(
        app,
        ["ingest", "url", "https://example.com", "--vault", str(vault_path)],
    )

    assert init_result.exit_code == 0
    assert disabled_result.exit_code == 2
    assert "features.swallow_ingest" in disabled_result.output
    assert web_disabled_result.exit_code == 2
    assert "features.web_ingest" in web_disabled_result.output


def test_cli_ingest_url_uses_playwright_snapshot_chain(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    url = "https://Example.com/articles/playwright?b=1#fragment"

    init_result = runner.invoke(app, ["init", str(vault_path)])
    config_path = vault_path / ".indbase" / "config" / "config.toml"
    config = load_config(config_path)
    save_config(
        replace(
            config,
            features=replace(config.features, swallow_ingest=True, web_ingest=True),
            ingest=replace(
                config.ingest,
                swallow=replace(config.ingest.swallow, min_markdown_chars=10),
            ),
        ),
        config_path,
    )

    def fake_convert_url(self, source_url: str) -> ConversionCandidate:
        rendered = self.store_root / "jobs" / "job_url" / "intermediate" / "playwright" / "rendered.html"
        screenshot = self.store_root / "jobs" / "job_url" / "intermediate" / "playwright" / "screenshot.png"
        trace = self.store_root / "jobs" / "job_url" / "trace.jsonl"
        manifest = self.store_root / "jobs" / "job_url" / "manifest.json"
        ingest_document = self.store_root / "jobs" / "job_url" / "ingest_document.json"
        rendered.parent.mkdir(parents=True, exist_ok=True)
        rendered.write_text("<html><body>Playwright URL Snapshot Needle</body></html>", encoding="utf-8")
        screenshot.write_bytes(b"png")
        trace.write_text("{}", encoding="utf-8")
        manifest.write_text("{}", encoding="utf-8")
        ingest_document.write_text("{}", encoding="utf-8")
        return ConversionCandidate(
            title="Playwright URL Snapshot",
            markdown_body="# Playwright URL Snapshot\n\nNeedle from local rendered snapshot.\n",
            status="success",
            quality_score=0.92,
            provenance=SwallowProvenance(
                swallow_job_id="job_url",
                swallow_raw_id="raw_url",
                swallow_document_id="swallow_doc_url",
                swallow_version="test",
                primary_worker="playwright_worker",
                worker_version="0.1.0",
                worker_chain=("playwright_worker@0.1.0", "quality_checker@0.1.0", "markdown_normalizer@0.1.0"),
                trace_path="jobs/job_url/trace.jsonl",
                manifest_path="jobs/job_url/manifest.json",
                ingest_document_path="jobs/job_url/ingest_document.json",
                requires_network=True,
                access_context="public_url",
            ),
            artifact_manifest=ArtifactManifest(
                required=(
                    "jobs/job_url/trace.jsonl",
                    "jobs/job_url/manifest.json",
                    "jobs/job_url/ingest_document.json",
                    "jobs/job_url/intermediate/playwright/rendered.html",
                    "jobs/job_url/intermediate/playwright/screenshot.png",
                )
            ),
            source_locators=(
                {
                    "kind": "web_snapshot",
                    "url": source_url,
                    "artifact": "jobs/job_url/intermediate/playwright/rendered.html",
                    "selector": None,
                },
            ),
            source_snapshot_path="jobs/job_url/intermediate/playwright/rendered.html",
            access_context="public_url",
        )

    monkeypatch.setattr("indbase_core.swallow_adapter.SwallowIngestAdapter.convert_url", fake_convert_url)

    ingest_result = runner.invoke(app, ["ingest", "url", url, "--vault", str(vault_path)])
    search_result = runner.invoke(app, ["search", "Needle", "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        document = connection.execute(
            """
            SELECT source_type, source_uri, normalized_source_uri, source_snapshot_path,
                   current_revision_id, ingest_status, fts_status, access_context
            FROM documents
            """
        ).fetchone()
        source_file = connection.execute(
            """
            SELECT original_ext, source_snapshot_path, access_context
            FROM source_files
            """
        ).fetchone()
        converter = connection.execute(
            """
            SELECT converter_name, primary_worker, promotion_status
            FROM converter_runs
            """
        ).fetchone()
        source_locator = connection.execute("SELECT source_locator_json FROM chunks").fetchone()
    finally:
        connection.close()

    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 0
    assert "Searchable: yes" in ingest_result.output
    assert search_result.exit_code == 0
    assert "Needle" in search_result.output
    assert document["source_type"] == "url"
    assert document["source_uri"] == url
    assert document["normalized_source_uri"] == "https://example.com/articles/playwright?b=1"
    assert document["source_snapshot_path"].endswith("rendered.html")
    assert (vault_path / document["source_snapshot_path"]).is_file()
    assert document["current_revision_id"] is not None
    assert document["ingest_status"] == "revisioned"
    assert document["fts_status"] == "indexed"
    assert document["access_context"] == "public_url"
    assert source_file["original_ext"] == "url"
    assert source_file["source_snapshot_path"] == document["source_snapshot_path"]
    assert source_file["access_context"] == "public_url"
    assert converter["converter_name"] == "swallow"
    assert converter["primary_worker"] == "playwright_worker"
    assert converter["promotion_status"] == "trusted-current"
    locator = json.loads(source_locator["source_locator_json"])[0]
    assert locator["kind"] == "web_snapshot"
    assert locator["artifact"] == document["source_snapshot_path"]


def test_cli_ingest_archive_expands_one_to_many_logical_documents(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    archive = tmp_path / "chatgpt-export.zip"
    with zipfile.ZipFile(archive, "w") as archive_file:
        archive_file.writestr(
            "conversations.json",
            json.dumps({"conversations": []}, ensure_ascii=False),
        )

    init_result = runner.invoke(app, ["init", str(vault_path)])
    config_path = vault_path / ".indbase" / "config" / "config.toml"
    config = load_config(config_path)
    save_config(replace(config, features=replace(config.features, swallow_ingest=True)), config_path)

    def fake_expand_archive(self, source_archive):
        conversations = self.store_root / "jobs" / "job_archive" / "intermediate" / "export_archive" / "conversations.json"
        trace = self.store_root / "jobs" / "job_archive" / "trace.jsonl"
        manifest = self.store_root / "jobs" / "job_archive" / "manifest.json"
        ingest_document = self.store_root / "jobs" / "job_archive" / "ingest_document.json"
        conversations.parent.mkdir(parents=True, exist_ok=True)
        conversations.write_text('{"conversations": []}\n', encoding="utf-8")
        trace.write_text("{}", encoding="utf-8")
        manifest.write_text("{}", encoding="utf-8")
        ingest_document.write_text("{}", encoding="utf-8")
        conversation_artifact = "jobs/job_archive/intermediate/export_archive/conversations.json"
        aggregate = ConversionCandidate(
            title="ChatGPT Export",
            markdown_body="# ChatGPT Export\n\nAlpha archive needle\n\nBeta archive needle\n",
            status="success",
            quality_score=0.96,
            provenance=SwallowProvenance(
                swallow_job_id="job_archive",
                swallow_raw_id="raw_archive",
                swallow_document_id="swallow_doc_archive",
                swallow_version="test",
                primary_worker="export_archive_worker",
                worker_version="0.1.0",
                worker_chain=("export_archive_worker@0.1.0", "quality_checker@0.1.0", "markdown_normalizer@0.1.0"),
                trace_path="jobs/job_archive/trace.jsonl",
                manifest_path="jobs/job_archive/manifest.json",
                ingest_document_path="jobs/job_archive/ingest_document.json",
                access_context="local_archive",
            ),
            artifact_manifest=ArtifactManifest(
                required=(
                    "jobs/job_archive/trace.jsonl",
                    "jobs/job_archive/manifest.json",
                    "jobs/job_archive/ingest_document.json",
                    conversation_artifact,
                )
            ),
            access_context="local_archive",
        )
        logical_documents = (
                ArchiveLogicalDocumentCandidate(
                    logical_source_id="conv-alpha",
                    title="Alpha Conversation",
                    markdown_body=(
                        "# Alpha Conversation\n\n"
                        "## 1. user\n\n"
                        "Alpha archive needle. This fixture includes enough trusted conversation "
                        "text to satisfy the promotion policy length gate.\n"
                    ),
                source_locators=(
                    {
                        "kind": "archive_member",
                        "archive_type": "chatgpt_export",
                        "member_path": "conversations.json",
                        "logical_source_id": "conv-alpha",
                        "conversation_index": 1,
                        "artifact": conversation_artifact,
                    },
                ),
                metadata={"archive_type": "chatgpt_export", "conversation_index": 1, "message_count": 1},
            ),
                ArchiveLogicalDocumentCandidate(
                    logical_source_id="conv-beta",
                    title="Beta Conversation",
                    markdown_body=(
                        "# Beta Conversation\n\n"
                        "## 1. user\n\n"
                        "Beta archive needle. This fixture includes enough trusted conversation "
                        "text to satisfy the promotion policy length gate.\n"
                    ),
                source_locators=(
                    {
                        "kind": "archive_member",
                        "archive_type": "chatgpt_export",
                        "member_path": "conversations.json",
                        "logical_source_id": "conv-beta",
                        "conversation_index": 2,
                        "artifact": conversation_artifact,
                    },
                ),
                metadata={"archive_type": "chatgpt_export", "conversation_index": 2, "message_count": 1},
            ),
        )
        return ArchiveExpansionCandidate(
            archive_type="chatgpt_export",
            aggregate_candidate=aggregate,
            logical_documents=logical_documents,
        )

    monkeypatch.setattr("indbase_core.swallow_adapter.SwallowIngestAdapter.expand_archive", fake_expand_archive)

    ingest_result = runner.invoke(app, ["ingest", "archive", str(archive), "--vault", str(vault_path)])
    alpha_search = runner.invoke(app, ["search", "Alpha", "--vault", str(vault_path)])
    beta_search = runner.invoke(app, ["search", "Beta", "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        documents = connection.execute(
            """
            SELECT doc_id, source_type, original_path, source_snapshot_path,
                   current_revision_id, fts_status, access_context
            FROM documents
            ORDER BY title
            """
        ).fetchall()
        original_paths = {
            row["original_path"]
            for row in connection.execute("SELECT original_path FROM source_files").fetchall()
        }
        items = connection.execute(
            """
            SELECT ingest_item_id, status, parent_ingest_item_id, logical_source_id
            FROM ingest_items
            ORDER BY parent_ingest_item_id IS NOT NULL, logical_source_id
            """
        ).fetchall()
        converter_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM converter_runs
            WHERE primary_worker = 'export_archive_worker'
              AND promotion_status = 'trusted-current'
            """
        ).fetchone()["count"]
        chunk_locators = connection.execute(
            "SELECT source_locator_json FROM chunks ORDER BY sequence"
        ).fetchall()
    finally:
        connection.close()

    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 0
    assert "Total: 3" in ingest_result.output
    assert "Revisions written: 2" in ingest_result.output
    assert "Searchable: yes" in ingest_result.output
    assert alpha_search.exit_code == 0
    assert "Alpha archive needle" in alpha_search.output
    assert beta_search.exit_code == 0
    assert "Beta archive needle" in beta_search.output
    assert len(documents) == 2
    assert {document["source_type"] for document in documents} == {"chatgpt_conversation"}
    assert all(document["current_revision_id"] for document in documents)
    assert all(document["fts_status"] == "indexed" for document in documents)
    assert all(document["access_context"] == "local_archive" for document in documents)
    assert len(original_paths) == 1
    assert (vault_path / next(iter(original_paths))).is_file()
    assert len(items) == 3
    assert items[0]["parent_ingest_item_id"] is None
    assert {item["parent_ingest_item_id"] for item in items[1:]} == {items[0]["ingest_item_id"]}
    assert {item["logical_source_id"] for item in items[1:]} == {"conv-alpha", "conv-beta"}
    assert converter_count == 2
    assert len(chunk_locators) == 2
    for row in chunk_locators:
        locator = json.loads(row["source_locator_json"])[0]
        assert locator["kind"] == "archive_member"
        assert locator["source_path"] in original_paths
        assert locator["artifact"].endswith("conversations.json")


def test_cli_ingest_media_source_type_is_asr_gated(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    media_source = tmp_path / "clip.mp3"
    media_source.write_bytes(b"not real audio")

    init_result = runner.invoke(app, ["init", str(vault_path)])
    config_path = vault_path / ".indbase" / "config" / "config.toml"
    config = load_config(config_path)
    save_config(replace(config, features=replace(config.features, swallow_ingest=True)), config_path)
    result = runner.invoke(app, ["ingest", "media", str(media_source), "--vault", str(vault_path)])

    assert init_result.exit_code == 0
    assert result.exit_code == 2
    assert "features.asr" in result.output


def test_cli_index_rebuild_fts_indexes_current_chunks(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    source = tmp_path / "knowledge.md"
    source.write_text("# 知识库\nlocal search body\n", encoding="utf-8")

    init_result = runner.invoke(app, ["init", str(vault_path)])
    ingest_result = runner.invoke(app, ["ingest", str(source), "--vault", str(vault_path)])
    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        revision_id = connection.execute("SELECT current_revision_id FROM documents").fetchone()["current_revision_id"]
        chunk_current_revision(connection, vault_path, doc_id)
    finally:
        connection.close()

    rebuild_result = runner.invoke(app, ["index", "rebuild", "--fts", "--vault", str(vault_path)])
    status_result = runner.invoke(app, ["index", "status", "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        document = connection.execute("SELECT fts_status FROM documents").fetchone()
        fts_rows = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        cjk_hit = connection.execute(
            "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ?",
            ("知识",),
        ).fetchone()
    finally:
        connection.close()

    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 0
    assert rebuild_result.exit_code == 0
    assert "FTS rebuild complete" in rebuild_result.output
    assert "Indexed documents: 1" in rebuild_result.output
    assert "Indexed chunks: 1" in rebuild_result.output
    assert status_result.exit_code == 0
    assert "Index Status" in status_result.output
    assert "FTS rows: 1" in status_result.output
    assert document["fts_status"] == "indexed"
    assert fts_rows["count"] == 1
    assert cjk_hit is not None


def test_cli_index_rebuild_fts_reports_missing_chunks(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")

    init_result = runner.invoke(app, ["init", str(vault_path)])
    run_m2_ingest_pipeline(vault_path, source)
    ingest_result = runner.invoke(app, ["index", "status", "--vault", str(vault_path)])
    rebuild_result = runner.invoke(app, ["index", "rebuild", "--fts", "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        document = connection.execute("SELECT doc_id, fts_status FROM documents").fetchone()
        error = connection.execute(
            "SELECT error_type FROM errors WHERE component = 'fts_indexer'"
        ).fetchone()
        review = connection.execute(
            "SELECT type, target_id FROM review_items WHERE type = 'indexing_failed'"
        ).fetchone()
    finally:
        connection.close()

    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 0
    assert rebuild_result.exit_code == 2
    assert "Failed documents: 1" in rebuild_result.output
    assert "missing_current_chunks" in rebuild_result.output
    assert document["fts_status"] == "failed"
    assert error["error_type"] == "missing_current_chunks"
    assert review["target_id"] == document["doc_id"]


def test_cli_index_rebuild_requires_supported_target(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"

    init_result = runner.invoke(app, ["init", str(vault_path)])
    rebuild_result = runner.invoke(app, ["index", "rebuild", "--vault", str(vault_path)])

    assert init_result.exit_code == 0
    assert rebuild_result.exit_code == 2
    assert "Use --fts" in rebuild_result.output


def test_cli_search_returns_snippets_and_json(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    source = tmp_path / "guide.md"
    source.write_text("# Guide\nLocal search returns snippets.\n", encoding="utf-8")

    init_result = runner.invoke(app, ["init", str(vault_path)])
    ingest_result = runner.invoke(app, ["ingest", str(source), "--vault", str(vault_path)])
    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        revision_id = connection.execute("SELECT current_revision_id FROM documents").fetchone()["current_revision_id"]
        chunk_current_revision(connection, vault_path, doc_id)
    finally:
        connection.close()
    rebuild_result = runner.invoke(app, ["index", "rebuild", "--fts", "--vault", str(vault_path)])

    table_result = runner.invoke(app, ["search", "snippets", "--vault", str(vault_path)])
    json_result = runner.invoke(app, ["search", "snippets", "--vault", str(vault_path), "--json"])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        citations = connection.execute("SELECT COUNT(*) AS count FROM citations").fetchone()
        search_results = connection.execute("SELECT COUNT(*) AS count FROM search_results").fetchone()
    finally:
        connection.close()

    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 0
    assert rebuild_result.exit_code == 0
    assert table_result.exit_code == 0
    assert "Search: snippets" in table_result.output
    assert "chunk_rev_doc_" in table_result.output
    assert "Local search returns snippets" in table_result.output
    assert json_result.exit_code == 0
    assert '"result_count": 1' in json_result.output
    assert '"doc_id": "doc_' in json_result.output
    assert citations["count"] == 0
    assert search_results["count"] == 0


def test_cli_search_returns_cjk_snippets_and_identifiers(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "zh.md").write_text(
        "# 中文知识\n大语言模型可以进入知识数据库，但必须保留幻觉控制证据。\n",
        encoding="utf-8",
    )
    (sources / "ja.md").write_text(
        "# 日本語ノート\n大規模言語モデルを知識管理に使う場合は根拠を確認する。\n",
        encoding="utf-8",
    )

    init_result = runner.invoke(app, ["init", str(vault_path)])
    ingest_result = runner.invoke(app, ["ingest", str(sources), "--vault", str(vault_path), "--recursive"])
    zh_model = runner.invoke(app, ["search", "大语言模型", "--vault", str(vault_path), "--json"])
    zh_hallucination = runner.invoke(app, ["search", "幻觉控制", "--vault", str(vault_path), "--json"])
    ja_model = runner.invoke(app, ["search", "大規模言語モデル", "--vault", str(vault_path), "--json"])

    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 0
    for result, expected_text in (
        (zh_model, "大语言模型"),
        (zh_hallucination, "幻觉控制"),
        (ja_model, "大規模言語モデル"),
    ):
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["result_count"] == 1
        hit = payload["results"][0]
        assert hit["doc_id"].startswith("doc_")
        assert hit["revision_id"].startswith("rev_doc_")
        assert hit["chunk_id"].startswith("chunk_rev_doc_")
        assert expected_text in hit["snippet"]


def test_cli_tui_lite_actions_cover_dashboard_ingest_search_and_state_views(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    source = tmp_path / "tui-note.md"
    source.write_text("# TUI Note\nM4 guided search panel body.\n", encoding="utf-8")

    init_result = runner.invoke(app, ["tui", "--action", "init", "--vault", str(vault_path)])
    ingest_result = runner.invoke(
        app,
        ["tui", "--action", "ingest", "--source", str(source), "--vault", str(vault_path)],
    )
    dashboard_result = runner.invoke(app, ["tui", "--action", "dashboard", "--vault", str(vault_path)])
    search_result = runner.invoke(
        app,
        ["tui", "--action", "search", "--query", "guided search", "--vault", str(vault_path)],
    )
    tasks_result = runner.invoke(app, ["tui", "--action", "tasks", "--vault", str(vault_path)])
    task_queue_result = runner.invoke(app, ["tui", "--action", "task-queue", "--vault", str(vault_path)])
    review_result = runner.invoke(app, ["tui", "--action", "review", "--vault", str(vault_path)])
    errors_result = runner.invoke(app, ["tui", "--action", "errors", "--vault", str(vault_path)])
    settings_result = runner.invoke(app, ["tui", "--action", "settings", "--vault", str(vault_path)])
    settings_alias_result = runner.invoke(app, ["tui", "--action", "settings-summary", "--vault", str(vault_path)])

    assert init_result.exit_code == 0
    assert "TUI-lite Init" in init_result.output
    assert ingest_result.exit_code == 0
    assert "TUI-lite Ingest Result" in ingest_result.output
    assert "searchable" in ingest_result.output
    assert dashboard_result.exit_code == 0
    assert "indbase TUI-lite Dashboard" in dashboard_result.output
    assert "documents_active" in dashboard_result.output
    assert search_result.exit_code == 0
    assert "TUI-lite Search: guided search" in search_result.output
    assert "panel body" in search_result.output
    assert tasks_result.exit_code == 0
    assert "TUI-lite Task Queue" in tasks_result.output
    assert task_queue_result.exit_code == 0
    assert "TUI-lite Task Queue" in task_queue_result.output
    assert review_result.exit_code == 0
    assert "No pending review items" in review_result.output
    assert errors_result.exit_code == 0
    assert "No errors" in errors_result.output
    assert settings_result.exit_code == 0
    assert "TUI-lite Settings Summary" in settings_result.output
    assert "search.log_queries" in settings_result.output
    assert settings_alias_result.exit_code == 0
    assert "TUI-lite Settings Summary" in settings_alias_result.output


def test_cli_manual_category_and_tag_commands_do_not_mutate_revisions(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    source = tmp_path / "tagged.md"
    source.write_text("# Tagged\nmanual metadata body\n", encoding="utf-8")

    init_result = runner.invoke(app, ["init", str(vault_path)])
    ingest_result = runner.invoke(app, ["ingest", str(source), "--vault", str(vault_path)])
    add_category_result = runner.invoke(app, ["catalog", "add", "Research", "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        category_id = connection.execute(
            "SELECT category_id FROM categories WHERE name = 'Research'"
        ).fetchone()["category_id"]
        revision_count_before = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
    finally:
        connection.close()

    set_category_result = runner.invoke(
        app,
        ["doc", "set-category", doc_id, category_id, "--vault", str(vault_path)],
    )
    tag_add_result = runner.invoke(app, ["tag", "add", "important", "--vault", str(vault_path)])
    tag_list_result = runner.invoke(app, ["tag", "list", "--vault", str(vault_path)])
    doc_add_tag_result = runner.invoke(app, ["doc", "add-tag", doc_id, "important", "--vault", str(vault_path)])
    doc_tags_result = runner.invoke(app, ["doc", "tags", doc_id, "--vault", str(vault_path)])
    doc_remove_tag_result = runner.invoke(
        app,
        ["doc", "remove-tag", doc_id, "important", "--vault", str(vault_path)],
    )
    revisions_result = runner.invoke(app, ["doc", "revisions", doc_id, "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        document = connection.execute("SELECT category_id FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
        active_tags = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM document_tags
            WHERE doc_id = ?
              AND deleted_at IS NULL
            """,
            (doc_id,),
        ).fetchone()
        revision_count_after = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
    finally:
        connection.close()

    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 0
    assert add_category_result.exit_code == 0
    assert set_category_result.exit_code == 0
    assert category_id in set_category_result.output
    assert tag_add_result.exit_code == 0
    assert "important" in tag_add_result.output
    assert tag_list_result.exit_code == 0
    assert "important" in tag_list_result.output
    assert doc_add_tag_result.exit_code == 0
    assert "added" in doc_add_tag_result.output
    assert doc_tags_result.exit_code == 0
    assert "important" in doc_tags_result.output
    assert doc_remove_tag_result.exit_code == 0
    assert "removed" in doc_remove_tag_result.output
    assert revisions_result.exit_code == 0
    assert "Document Revisions" in revisions_result.output
    assert "rev_doc_" in revisions_result.output
    assert "__rev_0001.md" in revisions_result.output
    assert document["category_id"] == category_id
    assert active_tags["count"] == 0
    assert revision_count_before["count"] == revision_count_after["count"]


def test_cli_m5_category_and_tag_crud_is_non_destructive(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    source = tmp_path / "catalog.md"
    source.write_text("# Catalog\nmanual catalog body\n", encoding="utf-8")

    init_result = runner.invoke(app, ["init", str(vault_path)])
    ingest_result = runner.invoke(app, ["ingest", str(source), "--vault", str(vault_path)])
    category_add = runner.invoke(app, ["catalog", "add", "Scratch", "--vault", str(vault_path)])
    tag_add = runner.invoke(app, ["tag", "add", "alpha", "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        category_id = connection.execute(
            "SELECT category_id FROM categories WHERE name = 'Scratch'"
        ).fetchone()["category_id"]
        tag_id = connection.execute("SELECT tag_id FROM tags WHERE name = 'alpha'").fetchone()["tag_id"]
        revision_count_before = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
    finally:
        connection.close()

    category_update = runner.invoke(
        app,
        ["catalog", "update", category_id, "--name", "Scratch Renamed", "--vault", str(vault_path)],
    )
    category_archive = runner.invoke(app, ["catalog", "archive", category_id, "--vault", str(vault_path)])
    category_list_hidden = runner.invoke(app, ["catalog", "list", "--vault", str(vault_path)])
    category_list_inactive = runner.invoke(
        app,
        ["catalog", "list", "--include-inactive", "--vault", str(vault_path)],
    )
    category_restore = runner.invoke(app, ["catalog", "restore", category_id, "--vault", str(vault_path)])
    system_archive = runner.invoke(app, ["catalog", "archive", "cat_uncategorized", "--vault", str(vault_path)])

    tag_update = runner.invoke(
        app,
        ["tag", "update", tag_id, "--name", "beta", "--language", "en", "--vault", str(vault_path)],
    )
    doc_add_tag = runner.invoke(app, ["doc", "add-tag", doc_id, "beta", "--vault", str(vault_path)])
    doc_tags_before_archive = runner.invoke(app, ["doc", "tags", doc_id, "--vault", str(vault_path)])
    tag_archive = runner.invoke(app, ["tag", "archive", tag_id, "--vault", str(vault_path)])
    tag_list_hidden = runner.invoke(app, ["tag", "list", "--vault", str(vault_path)])
    tag_list_inactive = runner.invoke(app, ["tag", "list", "--include-inactive", "--vault", str(vault_path)])
    doc_tags_after_archive = runner.invoke(app, ["doc", "tags", doc_id, "--vault", str(vault_path)])
    tag_restore = runner.invoke(app, ["tag", "restore", tag_id, "--vault", str(vault_path)])
    doc_tags_after_restore = runner.invoke(app, ["doc", "tags", doc_id, "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        archived_category = connection.execute(
            "SELECT name, is_active, deleted_at FROM categories WHERE category_id = ?",
            (category_id,),
        ).fetchone()
        restored_tag = connection.execute(
            "SELECT name, language, deleted_at FROM tags WHERE tag_id = ?",
            (tag_id,),
        ).fetchone()
        linked_tag = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM document_tags
            WHERE doc_id = ?
              AND tag_id = ?
              AND deleted_at IS NULL
            """,
            (doc_id, tag_id),
        ).fetchone()
        revision_count_after = connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()
    finally:
        connection.close()

    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 0
    assert category_add.exit_code == 0
    assert tag_add.exit_code == 0
    assert category_update.exit_code == 0
    assert "updated" in category_update.output
    assert category_archive.exit_code == 0
    assert "archived" in category_archive.output
    assert "Scratch Renamed" not in category_list_hidden.output
    assert "Scratch Renamed" in category_list_inactive.output
    assert category_restore.exit_code == 0
    assert "restored" in category_restore.output
    assert system_archive.exit_code == 1
    assert "System category cannot be archived" in system_archive.output
    assert tag_update.exit_code == 0
    assert "beta" in tag_update.output
    assert doc_add_tag.exit_code == 0
    assert "added" in doc_add_tag.output
    assert "beta" in doc_tags_before_archive.output
    assert tag_archive.exit_code == 0
    assert "archived" in tag_archive.output
    assert "beta" not in tag_list_hidden.output
    assert "beta" in tag_list_inactive.output
    assert "No document tags" in doc_tags_after_archive.output
    assert tag_restore.exit_code == 0
    assert "restored" in tag_restore.output
    assert "beta" in doc_tags_after_restore.output
    assert archived_category["name"] == "Scratch Renamed"
    assert archived_category["is_active"] == 1
    assert archived_category["deleted_at"] is None
    assert restored_tag["name"] == "beta"
    assert restored_tag["language"] == "en"
    assert restored_tag["deleted_at"] is None
    assert linked_tag["count"] == 1
    assert revision_count_before["count"] == revision_count_after["count"]


def test_cli_doc_archive_and_restore_affect_search(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nSearchable archive body\n", encoding="utf-8")

    init_result = runner.invoke(app, ["init", str(vault_path)])
    ingest_result = runner.invoke(app, ["ingest", str(source), "--vault", str(vault_path)])
    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        revision_id = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()["current_revision_id"]
        chunk_current_revision(connection, vault_path, doc_id)
    finally:
        connection.close()
    rebuild_result = runner.invoke(app, ["index", "rebuild", "--fts", "--vault", str(vault_path)])

    before = runner.invoke(app, ["search", "archive", "--vault", str(vault_path), "--json"])
    archive_result = runner.invoke(app, ["doc", "archive", doc_id, "--vault", str(vault_path)])
    show_archived = runner.invoke(app, ["doc", "show", doc_id, "--vault", str(vault_path)])
    open_archived = runner.invoke(app, ["doc", "open", doc_id, "--print-path", "--vault", str(vault_path)])
    open_original = runner.invoke(
        app,
        ["doc", "open", doc_id, "--original", "--print-path", "--vault", str(vault_path)],
    )
    open_folder = runner.invoke(
        app,
        ["doc", "open", doc_id, "--folder", "--print-path", "--vault", str(vault_path)],
    )
    open_revision = runner.invoke(
        app,
        ["doc", "open", doc_id, "--revision", revision_id, "--print-path", "--vault", str(vault_path)],
    )
    after_archive = runner.invoke(app, ["search", "archive", "--vault", str(vault_path), "--json"])
    restore_result = runner.invoke(app, ["doc", "restore", doc_id, "--vault", str(vault_path)])
    after_restore = runner.invoke(app, ["search", "archive", "--vault", str(vault_path), "--json"])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        fts_rows = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        document = connection.execute("SELECT status, archived_at FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
    finally:
        connection.close()

    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 0
    assert rebuild_result.exit_code == 0
    assert before.exit_code == 0
    assert '"result_count": 1' in before.output
    assert archive_result.exit_code == 0
    assert "archived" in archive_result.output
    assert show_archived.exit_code == 0
    assert "status: archived" in show_archived.output
    assert f"current_revision_id: {revision_id}" in show_archived.output
    assert open_archived.exit_code == 0
    assert "__rev_0001.md" in open_archived.output
    assert open_original.exit_code == 0
    assert "original.md" in open_original.output
    assert open_folder.exit_code == 0
    assert "sources" in open_folder.output
    assert open_revision.exit_code == 0
    assert "__rev_0001.md" in open_revision.output
    assert after_archive.exit_code == 0
    assert '"result_count": 0' in after_archive.output
    assert restore_result.exit_code == 0
    assert "restored" in restore_result.output
    assert after_restore.exit_code == 0
    assert '"result_count": 1' in after_restore.output
    assert fts_rows["count"] == 1
    assert document["status"] == "active"
    assert document["archived_at"] is None


def test_cli_doc_list_filters_status_category_and_tag(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    source = tmp_path / "visible.md"
    source.write_text("# Visible\nM5 visible archive listing needle.\n", encoding="utf-8")

    init_result = runner.invoke(app, ["init", str(vault_path)])
    ingest_result = runner.invoke(app, ["ingest", str(source), "--vault", str(vault_path)])
    category_add = runner.invoke(app, ["catalog", "add", "M5 Catalog", "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        category_id = connection.execute(
            "SELECT category_id FROM categories WHERE name = 'M5 Catalog'"
        ).fetchone()["category_id"]
    finally:
        connection.close()

    set_category = runner.invoke(app, ["doc", "set-category", doc_id, category_id, "--vault", str(vault_path)])
    add_tag = runner.invoke(app, ["doc", "add-tag", doc_id, "m5-visible", "--vault", str(vault_path)])
    active_list = runner.invoke(app, ["doc", "list", "--vault", str(vault_path)])
    category_list = runner.invoke(
        app,
        ["doc", "list", "--category-id", category_id, "--vault", str(vault_path)],
    )
    tag_list = runner.invoke(app, ["doc", "list", "--tag", "m5-visible", "--vault", str(vault_path)])
    archive_result = runner.invoke(app, ["doc", "archive", doc_id, "--vault", str(vault_path)])
    active_after_archive = runner.invoke(app, ["doc", "list", "--vault", str(vault_path)])
    archived_list = runner.invoke(app, ["doc", "list", "--status", "archived", "--vault", str(vault_path)])
    all_list = runner.invoke(app, ["doc", "list", "--status", "all", "--vault", str(vault_path)])
    search_archived = runner.invoke(app, ["search", "listing needle", "--vault", str(vault_path), "--json"])
    restore_result = runner.invoke(app, ["doc", "restore", doc_id, "--vault", str(vault_path)])
    active_after_restore = runner.invoke(app, ["doc", "list", "--vault", str(vault_path)])
    search_restored = runner.invoke(app, ["search", "listing needle", "--vault", str(vault_path), "--json"])

    stable_active_line = f"doc: {doc_id} status=active"
    stable_archived_line = f"doc: {doc_id} status=archived"

    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 0
    assert category_add.exit_code == 0
    assert set_category.exit_code == 0
    assert add_tag.exit_code == 0
    assert active_list.exit_code == 0
    assert stable_active_line in active_list.output
    assert "M5 Catalog" in active_list.output
    assert "m5-visible" in active_list.output
    assert category_list.exit_code == 0
    assert stable_active_line in category_list.output
    assert tag_list.exit_code == 0
    assert stable_active_line in tag_list.output
    assert archive_result.exit_code == 0
    assert active_after_archive.exit_code == 0
    assert stable_active_line not in active_after_archive.output
    assert archived_list.exit_code == 0
    assert stable_archived_line in archived_list.output
    assert all_list.exit_code == 0
    assert stable_archived_line in all_list.output
    assert search_archived.exit_code == 0
    assert '"result_count": 0' in search_archived.output
    assert restore_result.exit_code == 0
    assert active_after_restore.exit_code == 0
    assert stable_active_line in active_after_restore.output
    assert search_restored.exit_code == 0
    assert '"result_count": 1' in search_restored.output


def test_cli_review_and_error_commands_show_visible_failures(tmp_path, monkeypatch) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    source = tmp_path / "slides.pptx"
    source.write_bytes(b"placeholder office bytes")

    def fail_markitdown(_path) -> str:
        raise RuntimeError("markitdown boom")

    monkeypatch.setattr(normalizers, "_run_markitdown_file", fail_markitdown)

    init_result = runner.invoke(app, ["init", str(vault_path)])
    ingest_result = runner.invoke(app, ["ingest", str(source), "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        review = connection.execute(
            """
            SELECT review_id
            FROM review_items
            WHERE type = 'conversion_low_quality'
            """
        ).fetchone()
        error = connection.execute(
            """
            SELECT error_id
            FROM errors
            WHERE component = 'conversion'
            """
        ).fetchone()
    finally:
        connection.close()

    review_list = runner.invoke(app, ["review", "list", "--vault", str(vault_path)])
    review_show = runner.invoke(app, ["review", "show", review["review_id"], "--vault", str(vault_path)])
    review_filtered = runner.invoke(
        app,
        ["review", "list", "--type", "conversion_low_quality", "--target-type", "converter_run", "--vault", str(vault_path)],
    )
    review_resolve = runner.invoke(
        app,
        [
            "review",
            "resolve",
            review["review_id"],
            "--note",
            "checked visible failure",
            "--resolved-by",
            "pytest",
            "--vault",
            str(vault_path),
        ],
    )
    review_list_all = runner.invoke(
        app,
        ["review", "list", "--status", "all", "--vault", str(vault_path)],
    )
    error_list = runner.invoke(
        app,
        ["error", "list", "--component", "conversion", "--vault", str(vault_path)],
    )
    error_show = runner.invoke(app, ["error", "show", error["error_id"], "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        resolved = connection.execute(
            "SELECT status, resolved_at FROM review_items WHERE review_id = ?",
            (review["review_id"],),
        ).fetchone()
    finally:
        connection.close()

    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 1
    assert "completed_with_issues" in ingest_result.output
    assert review_list.exit_code == 0
    assert "conversion_low_quality" in review_list.output
    assert review_filtered.exit_code == 0
    assert review["review_id"] in review_filtered.output
    assert review_show.exit_code == 0
    assert f"review_id: {review['review_id']}" in review_show.output
    assert "target_type: converter_run" in review_show.output
    assert review_resolve.exit_code == 0
    assert "resolved" in review_resolve.output
    assert review_list_all.exit_code == 0
    assert review["review_id"] in review_list_all.output
    assert "resolution_note: checked visible failure" in review_list_all.output
    assert "resolved_by: pytest" in review_list_all.output
    assert error_list.exit_code == 0
    assert "RuntimeError" in error_list.output
    assert "markitdown boom" in error_list.output
    assert error_show.exit_code == 0
    assert f"error_id: {error['error_id']}" in error_show.output
    assert "component: conversion" in error_show.output
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"] is not None


def test_cli_review_resolve_many_records_shared_note(tmp_path) -> None:
    runner = CliRunner()
    vault_path = tmp_path / "vault"
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "empty.txt").write_text("", encoding="utf-8")
    (sources / "unsupported.png").write_bytes(b"png")

    init_result = runner.invoke(app, ["init", str(vault_path)])
    ingest_result = runner.invoke(app, ["ingest", str(sources), "--recursive", "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        reviews = [
            row["review_id"]
            for row in connection.execute(
                "SELECT review_id FROM review_items WHERE status = 'pending' ORDER BY created_at"
            )
        ]
    finally:
        connection.close()

    resolve_many = runner.invoke(
        app,
        [
            "review",
            "resolve-many",
            *reviews,
            "--note",
            "expected gate fixtures",
            "--resolved-by",
            "pytest",
            "--vault",
            str(vault_path),
        ],
    )
    review_list = runner.invoke(app, ["review", "list", "--status", "all", "--vault", str(vault_path)])

    connection = connect(vault_path / ".indbase" / "db.sqlite")
    try:
        pending = connection.execute(
            "SELECT COUNT(*) AS count FROM review_items WHERE status = 'pending'"
        ).fetchone()
        notes = {
            row["resolution_note"]
            for row in connection.execute("SELECT resolution_note FROM review_items")
        }
    finally:
        connection.close()

    assert init_result.exit_code == 0
    assert ingest_result.exit_code == 1
    assert len(reviews) == 2
    assert resolve_many.exit_code == 0
    assert "Resolved review items: 2" in resolve_many.output
    assert review_list.exit_code == 0
    assert "expected gate fixtures" in review_list.output
    assert pending["count"] == 0
    assert notes == {"expected gate fixtures"}
