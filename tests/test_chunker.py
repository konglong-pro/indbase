from pathlib import Path

import pytest

from indbase_core.chunker import (
    CHUNK_STRATEGY,
    ChunkingOptions,
    chunk_current_revision,
    chunk_markdown_body,
    strip_frontmatter,
)
from indbase_core.db import connect
from indbase_core.ingest import run_m2_ingest_pipeline
from indbase_core.vault import init_vault


def test_chunk_markdown_body_strips_frontmatter_and_preserves_heading_paths() -> None:
    markdown = (
        "---\n"
        "schema_version: indbase.source.v1\n"
        "---\n\n"
        "# Handbook\n"
        "Intro paragraph.\n\n"
        "## Setup\n"
        "Install the package.\n\n"
        "## Usage\n"
        "Run a search.\n"
    )

    body, body_start = strip_frontmatter(markdown)
    chunks = chunk_markdown_body(body, options=ChunkingOptions(target_tokens=50, max_tokens=100))

    assert body_start > 0
    assert body.startswith("# Handbook")
    assert [chunk.heading_path for chunk in chunks] == [
        ("Handbook",),
        ("Handbook", "Setup"),
        ("Handbook", "Usage"),
    ]
    assert chunks[0].text == "# Handbook\n\nIntro paragraph."
    assert chunks[1].text == "# Handbook\n## Setup\n\nInstall the package."
    assert "schema_version" not in chunks[0].text
    assert body[chunks[1].start_offset : chunks[1].end_offset] == "Install the package."


def test_chunk_markdown_body_splits_large_blocks_without_crossing_revision() -> None:
    body = "# Long\n" + " ".join(f"word{i}" for i in range(20)) + "\n"

    chunks = chunk_markdown_body(body, options=ChunkingOptions(target_tokens=5, max_tokens=7))

    assert len(chunks) > 1
    assert all(chunk.heading_path == ("Long",) for chunk in chunks)
    assert all(chunk.text.startswith("# Long") for chunk in chunks)
    assert chunks[0].sequence == 1
    assert chunks[-1].sequence == len(chunks)


def test_chunk_current_revision_writes_chunk_records_and_updates_revision_metadata(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "handbook.md"
    source.write_text(
        "# Handbook\n"
        "Intro paragraph.\n\n"
        "## Setup\n"
        "Install the package.\n\n"
        "## Usage\n"
        "Run a search.\n",
        encoding="utf-8",
    )
    ingest = run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc = connection.execute(
            "SELECT doc_id, current_revision_id FROM documents"
        ).fetchone()
        result = chunk_current_revision(connection, vault, doc["doc_id"])
        revision = connection.execute(
            """
            SELECT chunk_strategy, chunk_count
            FROM document_revisions
            WHERE revision_id = ?
            """,
            (doc["current_revision_id"],),
        ).fetchone()
        rows = list(
            connection.execute(
                """
                SELECT sequence, heading_path_json, text, token_count, content_hash, is_current
                FROM chunks
                WHERE revision_id = ?
                ORDER BY sequence
                """,
                (doc["current_revision_id"],),
            )
        )
        fts_rows = connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()
        document = connection.execute("SELECT fts_status FROM documents").fetchone()
    finally:
        connection.close()

    assert ingest.status == "succeeded"
    assert result.doc_id == doc["doc_id"]
    assert result.revision_id == doc["current_revision_id"]
    assert result.inserted_count == 3
    assert result.existing_count == 0
    assert result.chunk_count == 3
    assert revision["chunk_strategy"] == CHUNK_STRATEGY
    assert revision["chunk_count"] == 3
    assert [row["sequence"] for row in rows] == [1, 2, 3]
    assert rows[0]["heading_path_json"] == '["Handbook"]'
    assert rows[1]["heading_path_json"] == '["Handbook", "Setup"]'
    assert rows[1]["text"] == "# Handbook\n## Setup\n\nInstall the package."
    assert rows[1]["token_count"] > 0
    assert rows[1]["content_hash"].startswith("sha256:")
    assert rows[1]["is_current"] == 1
    assert fts_rows["count"] == 0
    assert document["fts_status"] == "not_indexed"


def test_chunk_current_revision_is_idempotent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        first = chunk_current_revision(connection, vault, doc_id)
        second = chunk_current_revision(connection, vault, doc_id)
        row_count = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
    finally:
        connection.close()

    assert first.inserted_count == 1
    assert first.existing_count == 0
    assert second.inserted_count == 0
    assert second.existing_count == 1
    assert second.chunks[0].chunk_id == first.chunks[0].chunk_id
    assert row_count["count"] == 1


def test_chunk_current_revision_rejects_tampered_markdown_body(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault)
    source = tmp_path / "note.md"
    source.write_text("# Note\nBody\n", encoding="utf-8")
    run_m2_ingest_pipeline(vault, source)

    connection = connect(vault / ".indbase" / "db.sqlite")
    try:
        doc = connection.execute(
            "SELECT doc_id, canonical_path FROM documents"
        ).fetchone()
        markdown_path = vault / doc["canonical_path"]
        markdown_path.write_text(markdown_path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

        with pytest.raises(ValueError, match="content hash mismatch"):
            chunk_current_revision(connection, vault, doc["doc_id"])

        row_count = connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()
        revision = connection.execute("SELECT chunk_count FROM document_revisions").fetchone()
    finally:
        connection.close()

    assert row_count["count"] == 0
    assert revision["chunk_count"] == 0
