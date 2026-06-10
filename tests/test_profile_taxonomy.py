import sqlite3
from pathlib import Path

import pytest

from indbase_core.db import connect
from indbase_core.feature_extraction import extract_feature_drafts, load_known_tag_phrases
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.profile import build_document_profile, list_stale_profiles, show_document_profile
from indbase_core.tags import add_tag
from indbase_core.taxonomy_manager import analyze_document_taxonomy
from indbase_core.vault import init_vault


def test_profile_build_rejects_source_shell(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="indbase_default_v1")
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        now = "2026-05-20T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO documents(
              doc_id, status, ingest_status, created_at
            )
            VALUES ('doc_20260520_shell01', 'active', 'revisioned', ?)
            """,
            (now,),
        )
        connection.commit()
        with pytest.raises(ValueError, match="Source shell"):
            build_document_profile(connection, "doc_20260520_shell01")


def test_profile_build_and_taxonomy_analyze(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "rag-guide.md"
    source.write_text(
        "# RAG Guide\n\n"
        "Retrieval augmented generation uses SQLite FTS and hybrid search patterns.\n"
        "The rag workflow helps knowledge base design.\n",
        encoding="utf-8",
    )
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        add_tag(connection, "rag", tag_type="method")
        profile_result = build_document_profile(connection, doc_id)
        profile = show_document_profile(connection, doc_id)
        features = connection.execute(
            """
            SELECT feature_id, chunk_id, type, confidence, quote, status
            FROM feature_atoms
            WHERE doc_id = ?
              AND status = 'active'
            """,
            (doc_id,),
        ).fetchall()
        analyze_result = analyze_document_taxonomy(connection, doc_id)
        suggestions = connection.execute(
            """
            SELECT type, status, confidence
            FROM taxonomy_suggestions
            WHERE doc_id = ?
            """,
            (doc_id,),
        ).fetchall()

    assert profile_result.feature_count > 0
    assert profile is not None
    assert "RAG Guide" in (profile["summary_for_classification"] or "")
    assert features
    for feature in features:
        chunk = connection.execute(
            "SELECT text FROM chunks WHERE chunk_id = ?",
            (feature["chunk_id"],),
        ).fetchone()
        assert feature["quote"] in chunk["text"]
    assert analyze_result.features_scanned == len(features)
    assert suggestions


def test_feature_quote_must_be_chunk_substring(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    init_vault(vault, category_template="indbase_default_v1")
    chunk_text = "SQLite FTS indexes current source chunks for hybrid search."
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            INSERT INTO documents(doc_id, current_revision_id, status, ingest_status, created_at)
            VALUES ('doc_20260520_ab12cd', 'rev_doc_20260520_ab12cd_0001', 'active', 'revisioned', '2026-05-20T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO document_revisions(
              revision_id, doc_id, sequence, markdown_path, content_hash, created_at
            )
            VALUES (
              'rev_doc_20260520_ab12cd_0001',
              'doc_20260520_ab12cd',
              1,
              'sources/2026/05/note.md',
              'hash',
              '2026-05-20T00:00:00+00:00'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO chunks(
              chunk_id, doc_id, revision_id, sequence, text, is_current, created_at
            )
            VALUES (
              'chunk_rev_doc_20260520_ab12cd_0001_0001',
              'doc_20260520_ab12cd',
              'rev_doc_20260520_ab12cd_0001',
              1,
              ?,
              1,
              '2026-05-20T00:00:00+00:00'
            )
            """,
            (chunk_text,),
        )
        connection.commit()
        chunks = connection.execute(
            "SELECT chunk_id, text FROM chunks WHERE doc_id = 'doc_20260520_ab12cd'"
        ).fetchall()
        drafts = extract_feature_drafts(
            connection,
            doc_id="doc_20260520_ab12cd",
            revision_id="rev_doc_20260520_ab12cd_0001",
            chunks=chunks,
            known_phrases=load_known_tag_phrases(connection),
        )
    assert drafts
    for draft in drafts:
        assert draft.quote in chunk_text


def test_profile_stale_after_revision_change(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "note.md"
    source.write_text("# Note\nfirst version\n", encoding="utf-8")
    init_vault(vault, category_template="indbase_default_v1")
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        build_document_profile(connection, doc_id)
    source.write_text("# Note\nsecond version with more rag content\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        stale = list_stale_profiles(connection)

    assert stale
