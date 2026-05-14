import json

import pytest
from typer.testing import CliRunner

from indbase_cli.main import app
from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.translations import (
    list_translations,
    resolve_translation_output_path,
    translate_full_document,
    translate_selected_chunks,
)
from indbase_core.vault import init_vault


def _vault_with_chunks(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    vault = tmp_path / "vault"
    source = tmp_path / "source.md"
    source.write_text(
        "# Handbook\n"
        "Intro text for selected translation.\n\n"
        "## Setup\n"
        "Install the package before running translation.\n\n"
        "## Usage\n"
        "Run selected chunk translation from source chunks.\n",
        encoding="utf-8",
    )
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    connection = connect(vault / ".indbase" / "db.sqlite")
    doc = connection.execute(
        "SELECT doc_id, current_revision_id, canonical_path FROM documents"
    ).fetchone()
    chunks = connection.execute(
        """
        SELECT chunk_id, text
        FROM chunks
        WHERE doc_id = ?
          AND revision_id = ?
          AND is_current = 1
        ORDER BY sequence
        """,
        (doc["doc_id"], doc["current_revision_id"]),
    ).fetchall()
    connection.close()
    return vault, source, doc, chunks


class FailingTranslationAdapter:
    model = "local/failing-translation-test"

    def translate(self, text: str, *, source_language: str | None, target_language: str) -> str:
        raise RuntimeError("adapter unavailable")


class FailingSecondChunkTranslationAdapter:
    model = "local/partial-failing-translation-test"

    def __init__(self) -> None:
        self.calls = 0

    def translate(self, text: str, *, source_language: str | None, target_language: str) -> str:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("adapter failed after first chunk")
        return f"[{target_language}] {text}"


def test_translate_selected_chunks_writes_output_records_without_mutating_source(tmp_path) -> None:
    vault, _source, doc, chunks = _vault_with_chunks(tmp_path)
    canonical = vault / doc["canonical_path"]
    before_markdown = canonical.read_text(encoding="utf-8")

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        before_counts = {
            "revisions": connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()["count"],
            "chunks": connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"],
            "fts": connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"],
        }
        result = translate_selected_chunks(
            connection,
            vault,
            doc_id=doc["doc_id"],
            revision_id=doc["current_revision_id"],
            chunk_ids=(chunks[0]["chunk_id"], chunks[1]["chunk_id"]),
            target_language="zh-CN",
        )
        translation = connection.execute("SELECT * FROM translations").fetchone()
        execution = connection.execute("SELECT * FROM executions").fetchone()
        task = connection.execute("SELECT status FROM tasks WHERE task_id = ?", (result.task_id,)).fetchone()
        after_counts = {
            "revisions": connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()["count"],
            "chunks": connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"],
            "fts": connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"],
        }

    output = vault / result.output_path
    output_text = output.read_text(encoding="utf-8")

    assert result.status == "succeeded"
    assert result.source_chunk_ids == (chunks[0]["chunk_id"], chunks[1]["chunk_id"])
    assert output.is_file()
    assert "schema_version: \"indbase.translation.v1\"" in output_text
    assert f"source_doc_id: \"{doc['doc_id']}\"" in output_text
    assert f"source_revision_id: \"{doc['current_revision_id']}\"" in output_text
    assert f"source_chunk_id: `{chunks[0]['chunk_id']}`" in output_text
    assert "[zh-CN]" in output_text
    assert canonical.read_text(encoding="utf-8") == before_markdown
    assert before_counts == after_counts
    assert translation["translation_id"] == result.translation_id
    assert translation["execution_id"] == result.execution_id
    assert translation["source_doc_id"] == doc["doc_id"]
    assert translation["source_revision_id"] == doc["current_revision_id"]
    assert json.loads(translation["source_chunk_ids_json"]) == [chunks[0]["chunk_id"], chunks[1]["chunk_id"]]
    assert translation["output_path"] == result.output_path
    assert translation["translation_mode"] == "selected_chunks"
    assert execution["status"] == "succeeded"
    assert execution["output_path"] == result.output_path
    assert task["status"] == "succeeded"


def test_translate_full_document_writes_all_current_chunks_without_mutating_source(tmp_path) -> None:
    vault, _source, doc, chunks = _vault_with_chunks(tmp_path)
    canonical = vault / doc["canonical_path"]
    before_markdown = canonical.read_text(encoding="utf-8")
    expected_chunk_ids = tuple(row["chunk_id"] for row in chunks)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        before_counts = {
            "revisions": connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()["count"],
            "chunks": connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"],
            "fts": connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"],
        }
        result = translate_full_document(
            connection,
            vault,
            doc_id=doc["doc_id"],
            revision_id=doc["current_revision_id"],
            target_language="ja",
        )
        translation = connection.execute("SELECT * FROM translations").fetchone()
        execution = connection.execute("SELECT * FROM executions").fetchone()
        task = connection.execute("SELECT type, status FROM tasks WHERE task_id = ?", (result.task_id,)).fetchone()
        after_counts = {
            "revisions": connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()["count"],
            "chunks": connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"],
            "fts": connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"],
        }

    output = vault / result.output_path
    output_text = output.read_text(encoding="utf-8")

    assert result.status == "succeeded"
    assert result.source_chunk_ids == expected_chunk_ids
    assert output.is_file()
    assert "translation_mode: \"full_document\"" in output_text
    assert "full current source document" in output_text
    assert "[ja]" in output_text
    assert canonical.read_text(encoding="utf-8") == before_markdown
    assert before_counts == after_counts
    assert translation["translation_id"] == result.translation_id
    assert json.loads(translation["source_chunk_ids_json"]) == list(expected_chunk_ids)
    assert translation["translation_mode"] == "full_document"
    assert translation["prompt_version"] == "m9.2"
    assert execution["type"] == "translation.full_document"
    assert execution["status"] == "succeeded"
    assert task["type"] == "translation_full_document"
    assert task["status"] == "succeeded"


def test_duplicate_multilanguage_and_mode_translation_outputs_do_not_collide(tmp_path) -> None:
    vault, _source, doc, chunks = _vault_with_chunks(tmp_path)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        duplicate_a = translate_full_document(
            connection,
            vault,
            doc_id=doc["doc_id"],
            revision_id=doc["current_revision_id"],
            target_language="zh-CN",
        )
        duplicate_b = translate_full_document(
            connection,
            vault,
            doc_id=doc["doc_id"],
            revision_id=doc["current_revision_id"],
            target_language="zh-CN",
        )
        en = translate_full_document(
            connection,
            vault,
            doc_id=doc["doc_id"],
            revision_id=doc["current_revision_id"],
            target_language="en",
        )
        ja = translate_full_document(
            connection,
            vault,
            doc_id=doc["doc_id"],
            revision_id=doc["current_revision_id"],
            target_language="ja",
        )
        selected = translate_selected_chunks(
            connection,
            vault,
            doc_id=doc["doc_id"],
            revision_id=doc["current_revision_id"],
            chunk_ids=(chunks[0]["chunk_id"],),
            target_language="zh-CN",
        )
        rows = connection.execute(
            "SELECT target_language, translation_mode, source_chunk_ids_json, output_path FROM translations"
        ).fetchall()

    output_paths = [row["output_path"] for row in rows]
    assert duplicate_a.output_path != duplicate_b.output_path
    assert len(output_paths) == len(set(output_paths))
    assert {row["target_language"] for row in rows} == {"zh-CN", "en", "ja"}
    assert {row["translation_mode"] for row in rows} == {"selected_chunks", "full_document"}
    assert len([row for row in rows if row["target_language"] == "zh-CN"]) == 3
    assert json.loads([row for row in rows if row["translation_mode"] == "selected_chunks"][0]["source_chunk_ids_json"]) == [
        chunks[0]["chunk_id"]
    ]
    assert (vault / selected.output_path).is_file()
    assert (vault / en.output_path).is_file()
    assert (vault / ja.output_path).is_file()


def test_translate_selected_chunks_rejects_invalid_chunk_without_side_effects(tmp_path) -> None:
    vault, _source, doc, _chunks = _vault_with_chunks(tmp_path)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        with pytest.raises(ValueError, match="not active current chunks"):
            translate_selected_chunks(
                connection,
                vault,
                doc_id=doc["doc_id"],
                revision_id=doc["current_revision_id"],
                chunk_ids=("chunk_missing",),
                target_language="zh-CN",
            )
        assert connection.execute("SELECT COUNT(*) AS count FROM executions").fetchone()["count"] == 0
        assert connection.execute("SELECT COUNT(*) AS count FROM translations").fetchone()["count"] == 0


def test_partial_full_document_translation_failure_does_not_create_successful_artifact(tmp_path) -> None:
    vault, _source, doc, _chunks = _vault_with_chunks(tmp_path)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        with pytest.raises(RuntimeError, match="after first chunk"):
            translate_full_document(
                connection,
                vault,
                doc_id=doc["doc_id"],
                revision_id=doc["current_revision_id"],
                target_language="zh-CN",
                adapter=FailingSecondChunkTranslationAdapter(),
            )
        task = connection.execute("SELECT status, error_json FROM tasks WHERE type = 'translation_full_document'").fetchone()
        execution = connection.execute("SELECT status, output_path, output_json FROM executions").fetchone()
        error_count = connection.execute("SELECT COUNT(*) AS count FROM errors WHERE component = 'translation'").fetchone()

    assert task["status"] == "failed"
    assert "after first chunk" in task["error_json"]
    assert execution["status"] == "failed"
    assert execution["output_path"] is None
    assert "after first chunk" in execution["output_json"]
    assert error_count["count"] == 1
    assert not list((vault / "outputs" / "translations").glob("*.md"))


def test_translation_adapter_failure_marks_task_and_execution_failed_without_translation(tmp_path) -> None:
    vault, _source, doc, chunks = _vault_with_chunks(tmp_path)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        with pytest.raises(RuntimeError, match="adapter unavailable"):
            translate_selected_chunks(
                connection,
                vault,
                doc_id=doc["doc_id"],
                revision_id=doc["current_revision_id"],
                chunk_ids=(chunks[0]["chunk_id"],),
                target_language="zh-CN",
                adapter=FailingTranslationAdapter(),
            )
        task = connection.execute(
            "SELECT task_id, type, status, error_json FROM tasks WHERE type = 'translation_selected_chunks'"
        ).fetchone()
        execution = connection.execute("SELECT status, output_json FROM executions").fetchone()
        error = connection.execute("SELECT component, error_type, task_id FROM errors").fetchone()
        failed_event = connection.execute(
            "SELECT COUNT(*) AS count FROM task_events WHERE event_type = 'translation_failed'"
        ).fetchone()

        assert connection.execute("SELECT COUNT(*) AS count FROM translations").fetchone()["count"] == 0
        assert task["type"] == "translation_selected_chunks"
        assert task["status"] == "failed"
        assert "adapter unavailable" in task["error_json"]
        assert execution["status"] == "failed"
        assert "adapter unavailable" in execution["output_json"]
        assert error["component"] == "translation"
        assert error["error_type"] == "RuntimeError"
        assert error["task_id"] == task["task_id"]
        assert failed_event["count"] == 1
        assert not list((vault / "outputs" / "translations").glob("*.md"))


def test_translate_full_document_rejects_old_revision_archived_and_source_shell_without_side_effects(
    tmp_path,
    monkeypatch,
) -> None:
    vault, source, doc, _chunks = _vault_with_chunks(tmp_path / "old")
    old_revision = doc["current_revision_id"]
    source.write_text("# Handbook\nChanged content for full document translation.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        current = connection.execute("SELECT current_revision_id FROM documents WHERE doc_id = ?", (doc["doc_id"],)).fetchone()
        with pytest.raises(ValueError, match="requires the current revision"):
            translate_full_document(
                connection,
                vault,
                doc_id=doc["doc_id"],
                revision_id=old_revision,
                target_language="zh-CN",
            )
        connection.execute("UPDATE documents SET status = 'archived' WHERE doc_id = ?", (doc["doc_id"],))
        connection.commit()
        with pytest.raises(ValueError, match="not active"):
            translate_full_document(
                connection,
                vault,
                doc_id=doc["doc_id"],
                revision_id=current["current_revision_id"],
                target_language="zh-CN",
            )
        assert connection.execute("SELECT COUNT(*) AS count FROM executions").fetchone()["count"] == 0
        assert connection.execute("SELECT COUNT(*) AS count FROM translations").fetchone()["count"] == 0

    import indbase_core.normalizers as normalizers

    shell_vault = tmp_path / "shell-vault"
    shell_source = tmp_path / "scan.pdf"
    shell_source.write_bytes(b"%PDF image only")
    init_vault(shell_vault)
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(shell_vault, shell_source)

    with connect(shell_vault / ".indbase" / "db.sqlite") as connection:
        shell_doc = connection.execute("SELECT doc_id FROM documents").fetchone()
        with pytest.raises(ValueError, match="no current revision"):
            translate_full_document(
                connection,
                shell_vault,
                doc_id=shell_doc["doc_id"],
                revision_id="rev_missing",
                target_language="zh-CN",
            )
        assert connection.execute("SELECT COUNT(*) AS count FROM executions").fetchone()["count"] == 0
        assert connection.execute("SELECT COUNT(*) AS count FROM translations").fetchone()["count"] == 0


def test_translate_selected_chunks_requires_current_active_revision(tmp_path) -> None:
    vault, source, doc, chunks = _vault_with_chunks(tmp_path)
    old_revision = doc["current_revision_id"]
    old_chunk = chunks[0]["chunk_id"]
    source.write_text("# Handbook\nChanged content for a new revision.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        with pytest.raises(ValueError, match="requires the current revision"):
            translate_selected_chunks(
                connection,
                vault,
                doc_id=doc["doc_id"],
                revision_id=old_revision,
                chunk_ids=(old_chunk,),
                target_language="zh-CN",
            )

        current = connection.execute("SELECT current_revision_id FROM documents WHERE doc_id = ?", (doc["doc_id"],)).fetchone()
        current_chunk = connection.execute(
            """
            SELECT chunk_id
            FROM chunks
            WHERE doc_id = ?
              AND revision_id = ?
              AND is_current = 1
            LIMIT 1
            """,
            (doc["doc_id"], current["current_revision_id"]),
        ).fetchone()
        connection.execute("UPDATE documents SET status = 'archived' WHERE doc_id = ?", (doc["doc_id"],))
        connection.commit()
        with pytest.raises(ValueError, match="not active"):
            translate_selected_chunks(
                connection,
                vault,
                doc_id=doc["doc_id"],
                revision_id=current["current_revision_id"],
                chunk_ids=(current_chunk["chunk_id"],),
                target_language="zh-CN",
            )


def test_translate_selected_chunks_rejects_source_shell(tmp_path, monkeypatch) -> None:
    import indbase_core.normalizers as normalizers

    vault = tmp_path / "vault"
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"%PDF image only")
    init_vault(vault)
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc = connection.execute("SELECT doc_id FROM documents").fetchone()
        with pytest.raises(ValueError, match="no current revision"):
            translate_selected_chunks(
                connection,
                vault,
                doc_id=doc["doc_id"],
                revision_id="rev_missing",
                chunk_ids=("chunk_missing",),
                target_language="zh-CN",
            )


def test_cli_translate_chunks_list_and_show_json(tmp_path) -> None:
    runner = CliRunner()
    vault = tmp_path / "vault"
    source = tmp_path / "source.md"
    source.write_text("# Handbook\nSelected translation CLI needle.\n", encoding="utf-8")

    assert runner.invoke(app, ["init", str(vault)]).exit_code == 0
    assert runner.invoke(app, ["ingest", str(source), "--vault", str(vault)]).exit_code == 0
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc = connection.execute("SELECT doc_id, current_revision_id FROM documents").fetchone()
        chunk = connection.execute("SELECT chunk_id FROM chunks").fetchone()

    translated = runner.invoke(
        app,
        [
            "translate",
            "chunks",
            doc["doc_id"],
            "--revision",
            doc["current_revision_id"],
            "--chunk",
            chunk["chunk_id"],
            "--target-language",
            "zh-CN",
            "--vault",
            str(vault),
            "--json",
        ],
    )
    payload = json.loads(translated.output)
    listed = runner.invoke(app, ["translate", "list", "--vault", str(vault), "--json"])
    shown = runner.invoke(app, ["translate", "show", payload["translation_id"], "--vault", str(vault)])

    assert translated.exit_code == 0
    assert payload["status"] == "succeeded"
    assert payload["source_doc_id"] == doc["doc_id"]
    assert listed.exit_code == 0
    assert json.loads(listed.output)["translations"][0]["translation_id"] == payload["translation_id"]
    assert shown.exit_code == 0
    assert payload["translation_id"] in shown.output


def test_translation_output_remains_viewable_after_reingest_and_archive(tmp_path) -> None:
    vault, source, doc, chunks = _vault_with_chunks(tmp_path)
    old_revision = doc["current_revision_id"]

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = translate_selected_chunks(
            connection,
            vault,
            doc_id=doc["doc_id"],
            revision_id=old_revision,
            chunk_ids=(chunks[0]["chunk_id"],),
            target_language="zh-CN",
        )
        output_path = resolve_translation_output_path(connection, vault, result.translation_id)

    source.write_text("# Handbook\nNew current revision after translation output.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        current = connection.execute("SELECT current_revision_id FROM documents WHERE doc_id = ?", (doc["doc_id"],)).fetchone()
        translation = list_translations(connection, doc_id=doc["doc_id"])[0]
        still_resolved = resolve_translation_output_path(connection, vault, result.translation_id)
        connection.execute("UPDATE documents SET status = 'archived' WHERE doc_id = ?", (doc["doc_id"],))
        connection.commit()
        archived_listed = list_translations(connection, doc_id=doc["doc_id"])
        archived_resolved = resolve_translation_output_path(connection, vault, result.translation_id)

    assert current["current_revision_id"] != old_revision
    assert translation["source_revision_id"] == old_revision
    assert json.loads(translation["source_chunk_ids_json"]) == [chunks[0]["chunk_id"]]
    assert output_path == still_resolved == archived_resolved
    assert archived_listed[0]["translation_id"] == result.translation_id


def test_cli_translate_document_json(tmp_path) -> None:
    runner = CliRunner()
    vault = tmp_path / "vault"
    source = tmp_path / "source.md"
    source.write_text("# Handbook\nFull document translation CLI needle.\n", encoding="utf-8")

    assert runner.invoke(app, ["init", str(vault)]).exit_code == 0
    assert runner.invoke(app, ["ingest", str(source), "--vault", str(vault)]).exit_code == 0
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc = connection.execute("SELECT doc_id, current_revision_id FROM documents").fetchone()
        current_chunks = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM chunks
            WHERE doc_id = ?
              AND revision_id = ?
              AND is_current = 1
            """,
            (doc["doc_id"], doc["current_revision_id"]),
        ).fetchone()["count"]

    translated = runner.invoke(
        app,
        [
            "translate",
            "document",
            doc["doc_id"],
            "--revision",
            doc["current_revision_id"],
            "--target-language",
            "fr",
            "--vault",
            str(vault),
            "--json",
        ],
    )
    payload = json.loads(translated.output)

    assert translated.exit_code == 0
    assert payload["status"] == "succeeded"
    assert payload["source_doc_id"] == doc["doc_id"]
    assert len(payload["source_chunk_ids"]) == current_chunks
    opened = runner.invoke(app, ["translate", "open", payload["translation_id"], "--vault", str(vault), "--print-path"])
    assert opened.exit_code == 0
    assert payload["output_path"].replace("/", "\\") in opened.output
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        translation = connection.execute("SELECT translation_mode FROM translations").fetchone()
    assert translation["translation_mode"] == "full_document"
