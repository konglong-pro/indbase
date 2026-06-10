from __future__ import annotations

from pathlib import Path

import pytest

import indbase_core.output_service as output_service_module
from conftest_output import insert_minimal_document
from indbase_core.chunker import chunk_revision
from indbase_core.conversion import hash_markdown
from indbase_core.db import connect
from indbase_core.indexer import rebuild_fts_index
from indbase_core.output_service import normalize_replace_current
from indbase_core.paths import vault_paths
from indbase_core.search import search_chunks
from indbase_core.transition_adapter import run_fake_bridge
from indbase_core.transition_runtime import install_runtime
from indbase_core.vault import init_vault


@pytest.fixture
def normalize_vault(tmp_path: Path):
    vault = tmp_path / "vault"
    init_vault(vault, category_template="indbase_default_v1")
    install_runtime(vault, run_npm_install=False)
    connection = connect(vault_paths(vault).db_path)
    try:
        yield vault, connection
    finally:
        connection.close()


def _insert_chunked_indexed_doc(connection, vault: Path, *, body: str) -> tuple[str, str]:
    doc_id = insert_minimal_document(connection, vault, body=body)
    revision_id = "rev_doc_20250101_abc123_0001"
    connection.execute(
        "UPDATE document_revisions SET content_hash = ? WHERE revision_id = ?",
        (hash_markdown(body), revision_id),
    )
    connection.commit()
    chunk_revision(connection, vault, revision_id)
    rebuild_fts_index(connection, vault)
    return doc_id, revision_id


def test_normalize_replace_promotes_new_revision_and_reindexes_current_search(normalize_vault) -> None:
    vault, connection = normalize_vault
    doc_id, old_revision_id = _insert_chunked_indexed_doc(
        connection,
        vault,
        body="# Hello\n\nplain  text provider normalize needle.\n",
    )
    old_chunks = list(
        connection.execute(
            "SELECT chunk_id FROM chunks WHERE doc_id = ? AND revision_id = ? AND is_current = 1",
            (doc_id, old_revision_id),
        )
    )

    result = normalize_replace_current(connection, vault, doc_id=doc_id, bridge_runner=run_fake_bridge)

    assert result.status == "succeeded"
    assert result.created_revision_id is not None
    current = connection.execute(
        "SELECT current_revision_id, canonical_path, fts_status FROM documents WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    assert current["current_revision_id"] == result.created_revision_id
    assert str(current["current_revision_id"]).endswith("_0002")
    assert current["fts_status"] == "indexed"

    revision_rows = {
        row["revision_id"]: row["promotion_status"]
        for row in connection.execute(
            "SELECT revision_id, promotion_status FROM document_revisions WHERE doc_id = ?",
            (doc_id,),
        )
    }
    assert revision_rows[old_revision_id] == "superseded"
    assert revision_rows[result.created_revision_id] == "promoted"

    old_current_count = connection.execute(
        "SELECT COUNT(*) AS count FROM chunks WHERE revision_id = ? AND is_current = 1",
        (old_revision_id,),
    ).fetchone()["count"]
    new_chunks = list(
        connection.execute(
            "SELECT chunk_id FROM chunks WHERE revision_id = ? AND is_current = 1",
            (result.created_revision_id,),
        )
    )
    fts_rows = list(connection.execute("SELECT revision_id, chunk_id FROM chunks_fts WHERE doc_id = ?", (doc_id,)))
    search = search_chunks(connection, "provider normalize needle")
    provider_run = connection.execute(
        """
        SELECT provider_run_id, provider_status, evidence_status, evidence_root, failure_class
        FROM provider_runs
        WHERE output_run_id = ?
        """,
        (result.output_run_id,),
    ).fetchone()
    output_run = connection.execute(
        "SELECT adopted_provider_run_id, created_revision_id, status FROM output_runs WHERE output_run_id = ?",
        (result.output_run_id,),
    ).fetchone()
    lineage_build = connection.execute(
        """
        SELECT index_build_id, trigger, status
        FROM index_builds
        WHERE doc_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (doc_id,),
    ).fetchone()
    lineage_entries = list(
        connection.execute(
            """
            SELECT chunk_id, revision_id, status
            FROM index_build_entries
            WHERE index_build_id = ?
            ORDER BY chunk_id
            """,
            (lineage_build["index_build_id"],),
        )
    )

    assert old_chunks
    assert old_current_count == 0
    assert new_chunks
    assert {row["revision_id"] for row in fts_rows} == {result.created_revision_id}
    assert search.result_count >= 1
    assert {row.revision_id for row in search.results} == {result.created_revision_id}
    assert provider_run["provider_status"] == "success"
    assert provider_run["evidence_status"] == "copied"
    assert provider_run["failure_class"] is None
    assert (vault / provider_run["evidence_root"] / "evidence_index.json").is_file()
    assert output_run["status"] == "succeeded"
    assert output_run["adopted_provider_run_id"] == provider_run["provider_run_id"]
    assert output_run["created_revision_id"] == result.created_revision_id
    assert lineage_build["trigger"] == "normalize_replace"
    assert lineage_build["status"] == "succeeded"
    assert {row["status"] for row in lineage_entries} == {"indexed"}
    assert {row["revision_id"] for row in lineage_entries} == {result.created_revision_id}


def test_normalize_replace_chunking_failure_restores_previous_current_state(normalize_vault, monkeypatch) -> None:
    vault, connection = normalize_vault
    doc_id, old_revision_id = _insert_chunked_indexed_doc(
        connection,
        vault,
        body="# Hello\n\natomic old search needle.\n",
    )
    old_fts_rows = {
        (row["revision_id"], row["chunk_id"])
        for row in connection.execute("SELECT revision_id, chunk_id FROM chunks_fts WHERE doc_id = ?", (doc_id,))
    }

    def fail_chunk_revision(*args, **kwargs):
        raise RuntimeError("chunking boom")

    monkeypatch.setattr(output_service_module, "chunk_revision", fail_chunk_revision)

    with pytest.raises(RuntimeError, match="chunking boom"):
        normalize_replace_current(connection, vault, doc_id=doc_id, bridge_runner=run_fake_bridge)

    current = connection.execute(
        "SELECT current_revision_id, canonical_path FROM documents WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    revisions = {
        row["revision_id"]: row["promotion_status"]
        for row in connection.execute(
            "SELECT revision_id, promotion_status FROM document_revisions WHERE doc_id = ?",
            (doc_id,),
        )
    }
    old_current_chunks = connection.execute(
        "SELECT COUNT(*) AS count FROM chunks WHERE revision_id = ? AND is_current = 1",
        (old_revision_id,),
    ).fetchone()["count"]
    fts_rows = {
        (row["revision_id"], row["chunk_id"])
        for row in connection.execute("SELECT revision_id, chunk_id FROM chunks_fts WHERE doc_id = ?", (doc_id,))
    }
    search = search_chunks(connection, "atomic old search needle")
    output_run = connection.execute(
        "SELECT status, created_revision_id FROM output_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    provider_run = connection.execute(
        "SELECT provider_status, failure_class FROM provider_runs ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    assert current["current_revision_id"] == old_revision_id
    assert revisions[old_revision_id] == "promoted"
    assert output_run["created_revision_id"] in revisions
    assert revisions[output_run["created_revision_id"]] == "never_promoted"
    assert old_current_chunks > 0
    assert fts_rows == old_fts_rows
    assert search.result_count >= 1
    assert {row.revision_id for row in search.results} == {old_revision_id}
    assert output_run["status"] == "failed"
    assert provider_run["provider_status"] == "failed"
    assert provider_run["failure_class"] == "provider_unknown_failure"
