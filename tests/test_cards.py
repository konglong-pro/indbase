import json

import pytest
from typer.testing import CliRunner

from indbase_cli.main import app
import indbase_core.normalizers as normalizers
from indbase_core.cards import (
    accept_candidate_card,
    generate_candidate_card,
    get_candidate_card,
    list_candidate_card_sources,
    list_candidate_cards,
    reject_candidate_card,
)
from indbase_core.db import connect
from indbase_core.documents import archive_document
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.vault import init_vault


def _vault_with_chunks(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    vault = tmp_path / "vault"
    source = tmp_path / "source.md"
    source.write_text(
        "# Candidate Source\n"
        "Candidate extraction keeps claims bound to source chunks.\n\n"
        "## Evidence\n"
        "Each generated claim must include a quote and a current chunk identifier.\n\n"
        "## Boundary\n"
        "M10.1 creates records only and must not write atomic notes.\n",
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
        ORDER BY sequence, chunk_id
        """,
        (doc["doc_id"], doc["current_revision_id"]),
    ).fetchall()
    connection.close()
    return vault, source, doc, chunks


def test_candidate_card_generate_records_claim_sources_without_atomic_notes(tmp_path) -> None:
    vault, _source, doc, _chunks = _vault_with_chunks(tmp_path)
    atomic_before = set((vault / "notes" / "atomic").glob("**/*.md"))

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        before_counts = {
            "revisions": connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()["count"],
            "chunks": connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"],
            "fts": connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"],
        }
        result = generate_candidate_card(
            connection,
            doc_id=doc["doc_id"],
            revision_id=doc["current_revision_id"],
            max_claims=3,
        )
        card = get_candidate_card(connection, result.candidate_card_id)
        sources = list_candidate_card_sources(connection, result.candidate_card_id)
        task = connection.execute(
            "SELECT type, status, result_json FROM tasks WHERE task_id = ?",
            (result.task_id,),
        ).fetchone()
        after_counts = {
            "revisions": connection.execute("SELECT COUNT(*) AS count FROM document_revisions").fetchone()["count"],
            "chunks": connection.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"],
            "fts": connection.execute("SELECT COUNT(*) AS count FROM chunks_fts").fetchone()["count"],
        }
        existing_chunk_ids = {
            row["chunk_id"]
            for row in connection.execute(
                """
                SELECT chunk_id
                FROM chunks
                WHERE doc_id = ?
                  AND revision_id = ?
                  AND is_current = 1
                  AND deleted_at IS NULL
                """,
                (doc["doc_id"], doc["current_revision_id"]),
            )
        }

    claims = json.loads(card["claims_json"])
    atomic_after = set((vault / "notes" / "atomic").glob("**/*.md"))

    assert result.status == "reviewing"
    assert result.claims_count == len(claims)
    assert result.source_revision_id == doc["current_revision_id"]
    assert card["source_doc_id"] == doc["doc_id"]
    assert card["source_revision_id"] == doc["current_revision_id"]
    assert card["accepted_note_path"] is None
    assert card["status"] == "reviewing"
    assert len(sources) == len(claims)
    assert before_counts == after_counts
    assert atomic_before == atomic_after
    assert task["type"] == "candidate_card_generate"
    assert task["status"] == "succeeded"
    assert result.candidate_card_id in task["result_json"]

    for claim in claims:
        assert claim["claim_id"]
        assert claim["text"]
        assert 0 <= claim["confidence"] <= 1
        assert claim["source_chunk_ids"]
        assert claim["quotes"]
        assert set(claim["source_chunk_ids"]) <= existing_chunk_ids
        for quote in claim["quotes"]:
            assert quote["chunk_id"] in claim["source_chunk_ids"]
            assert quote["text"]


def test_candidate_card_rejects_source_shell_archived_old_revision_and_no_chunk(
    tmp_path,
    monkeypatch,
) -> None:
    vault, source, doc, _chunks = _vault_with_chunks(tmp_path / "normal")
    old_revision = doc["current_revision_id"]
    source.write_text("# Candidate Source\nChanged current revision for cards.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        current = connection.execute(
            "SELECT current_revision_id FROM documents WHERE doc_id = ?",
            (doc["doc_id"],),
        ).fetchone()
        with pytest.raises(ValueError, match="requires the current revision"):
            generate_candidate_card(connection, doc_id=doc["doc_id"], revision_id=old_revision)

        archive_document(connection, doc["doc_id"])
        with pytest.raises(ValueError, match="not active"):
            generate_candidate_card(connection, doc_id=doc["doc_id"], revision_id=current["current_revision_id"])

    shell_vault = tmp_path / "shell-vault"
    shell_source = tmp_path / "scan.pdf"
    shell_source.write_bytes(b"%PDF image only")
    init_vault(shell_vault)
    monkeypatch.setattr(normalizers, "_run_markitdown_file", lambda _path: " ")
    run_m3_ingest_pipeline(shell_vault, shell_source)
    with connect(shell_vault / ".indbase" / "db.sqlite") as connection:
        shell_doc = connection.execute("SELECT doc_id FROM documents").fetchone()
        with pytest.raises(ValueError, match="no current revision"):
            generate_candidate_card(connection, doc_id=shell_doc["doc_id"])
        assert connection.execute("SELECT COUNT(*) AS count FROM candidate_cards").fetchone()["count"] == 0

    no_chunk_vault, _no_chunk_source, no_chunk_doc, _no_chunk_chunks = _vault_with_chunks(tmp_path / "no-chunk")
    with connect(no_chunk_vault / ".indbase" / "db.sqlite") as connection:
        connection.execute(
            "DELETE FROM chunks_fts WHERE doc_id = ? AND revision_id = ?",
            (no_chunk_doc["doc_id"], no_chunk_doc["current_revision_id"]),
        )
        connection.execute(
            "DELETE FROM index_build_entries WHERE doc_id = ? AND revision_id = ?",
            (no_chunk_doc["doc_id"], no_chunk_doc["current_revision_id"]),
        )
        connection.execute(
            "DELETE FROM chunks WHERE doc_id = ? AND revision_id = ?",
            (no_chunk_doc["doc_id"], no_chunk_doc["current_revision_id"]),
        )
        connection.commit()
        with pytest.raises(ValueError, match="no chunks"):
            generate_candidate_card(connection, doc_id=no_chunk_doc["doc_id"])
        assert connection.execute("SELECT COUNT(*) AS count FROM candidate_cards").fetchone()["count"] == 0


def test_candidate_card_default_list_hides_old_revision_after_reingest_but_show_still_works(tmp_path) -> None:
    vault, source, doc, _chunks = _vault_with_chunks(tmp_path)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = generate_candidate_card(connection, doc_id=doc["doc_id"])
        default_before = list_candidate_cards(connection)

    source.write_text("# Candidate Source\nChanged content creates a new current revision.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        default_after = list_candidate_cards(connection)
        include_stale = list_candidate_cards(connection, status=None, active_current_only=False)
        shown = get_candidate_card(connection, result.candidate_card_id)

    assert [row["candidate_card_id"] for row in default_before] == [result.candidate_card_id]
    assert default_after == []
    assert [row["candidate_card_id"] for row in include_stale] == [result.candidate_card_id]
    assert shown["source_revision_id"] == doc["current_revision_id"]


def test_cli_card_generate_list_show_json(tmp_path) -> None:
    runner = CliRunner()
    vault = tmp_path / "vault"
    source = tmp_path / "source.md"
    source.write_text("# Candidate CLI\nCandidate card CLI source binding.\n", encoding="utf-8")

    assert runner.invoke(app, ["init", str(vault)]).exit_code == 0
    assert runner.invoke(app, ["ingest", str(source), "--vault", str(vault)]).exit_code == 0
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc = connection.execute("SELECT doc_id, current_revision_id FROM documents").fetchone()

    generated = runner.invoke(
        app,
        [
            "card",
            "generate",
            doc["doc_id"],
            "--revision",
            doc["current_revision_id"],
            "--vault",
            str(vault),
            "--json",
        ],
    )
    payload = json.loads(generated.output)
    listed = runner.invoke(app, ["card", "list", "--vault", str(vault), "--json"])
    shown = runner.invoke(app, ["card", "show", payload["candidate_card_id"], "--vault", str(vault), "--json"])
    shown_payload = json.loads(shown.output)

    assert generated.exit_code == 0
    assert payload["status"] == "reviewing"
    assert payload["source_doc_id"] == doc["doc_id"]
    assert payload["source_revision_id"] == doc["current_revision_id"]
    assert listed.exit_code == 0
    assert json.loads(listed.output)["candidate_cards"][0]["candidate_card_id"] == payload["candidate_card_id"]
    assert shown.exit_code == 0
    assert shown_payload["candidate_card_id"] == payload["candidate_card_id"]
    assert shown_payload["claims"][0]["source_chunk_ids"]
    assert shown_payload["sources"][0]["source_chunk_id"] in shown_payload["claims"][0]["source_chunk_ids"]


def test_candidate_card_accept_writes_cited_atomic_note(tmp_path) -> None:
    vault, _source, doc, _chunks = _vault_with_chunks(tmp_path)
    atomic_before = set((vault / "notes" / "atomic").glob("**/*.md"))

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        generated = generate_candidate_card(connection, doc_id=doc["doc_id"])
        accepted = accept_candidate_card(connection, vault, generated.candidate_card_id)
        card = get_candidate_card(connection, generated.candidate_card_id)
        sources = list_candidate_card_sources(connection, generated.candidate_card_id)
        task = connection.execute(
            "SELECT type, status, result_json FROM tasks WHERE task_id = ?",
            (accepted.task_id,),
        ).fetchone()

    atomic_after = set((vault / "notes" / "atomic").glob("**/*.md"))
    note_path = vault / accepted.accepted_note_path
    note_text = note_path.read_text(encoding="utf-8")
    claims = json.loads(card["claims_json"])

    assert accepted.status == "accepted"
    assert card["status"] == "accepted"
    assert card["accepted_note_path"] == accepted.accepted_note_path
    assert note_path.is_file()
    assert len(atomic_after - atomic_before) == 1
    assert "schema_version: \"indbase.atomic_note.v1\"" in note_text
    assert f"candidate_card_id: \"{generated.candidate_card_id}\"" in note_text
    assert f"source_doc_ids: [\"{doc['doc_id']}\"]" in note_text
    assert doc["current_revision_id"] in note_text
    for source in sources:
        assert source["source_chunk_id"] in note_text
    for claim in claims:
        assert claim["claim_id"] in note_text
        assert "Citations:" in note_text
        for chunk_id in claim["source_chunk_ids"]:
            assert chunk_id in note_text
    assert task["type"] == "candidate_card_accept"
    assert task["status"] == "succeeded"
    assert accepted.accepted_note_path in task["result_json"]


def test_candidate_card_reject_writes_no_atomic_note_and_keeps_sources(tmp_path) -> None:
    vault, _source, doc, _chunks = _vault_with_chunks(tmp_path)
    atomic_before = set((vault / "notes" / "atomic").glob("**/*.md"))

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        generated = generate_candidate_card(connection, doc_id=doc["doc_id"])
        source_count_before = len(list_candidate_card_sources(connection, generated.candidate_card_id))
        rejected = reject_candidate_card(connection, generated.candidate_card_id, reason="not useful")
        card = get_candidate_card(connection, generated.candidate_card_id)
        source_count_after = len(list_candidate_card_sources(connection, generated.candidate_card_id))
        task = connection.execute(
            "SELECT type, status, result_json FROM tasks WHERE task_id = ?",
            (rejected.task_id,),
        ).fetchone()

    atomic_after = set((vault / "notes" / "atomic").glob("**/*.md"))

    assert rejected.status == "rejected"
    assert rejected.accepted_note_path is None
    assert card["status"] == "rejected"
    assert card["accepted_note_path"] is None
    assert atomic_before == atomic_after
    assert source_count_before == source_count_after
    assert task["type"] == "candidate_card_reject"
    assert task["status"] == "succeeded"
    assert "not useful" in task["result_json"]


def test_candidate_card_terminal_states_cannot_be_reused(tmp_path) -> None:
    vault, _source, doc, _chunks = _vault_with_chunks(tmp_path)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        accepted_card = generate_candidate_card(connection, doc_id=doc["doc_id"])
        accept_candidate_card(connection, vault, accepted_card.candidate_card_id)
        with pytest.raises(ValueError, match="not reviewing"):
            accept_candidate_card(connection, vault, accepted_card.candidate_card_id)
        with pytest.raises(ValueError, match="not reviewing"):
            reject_candidate_card(connection, accepted_card.candidate_card_id)

        rejected_card = generate_candidate_card(connection, doc_id=doc["doc_id"])
        reject_candidate_card(connection, rejected_card.candidate_card_id)
        with pytest.raises(ValueError, match="not reviewing"):
            accept_candidate_card(connection, vault, rejected_card.candidate_card_id)

        accepted_count = connection.execute(
            "SELECT COUNT(*) AS count FROM candidate_cards WHERE status = 'accepted'"
        ).fetchone()["count"]
        rejected_count = connection.execute(
            "SELECT COUNT(*) AS count FROM candidate_cards WHERE status = 'rejected'"
        ).fetchone()["count"]

    assert accepted_count == 1
    assert rejected_count == 1


def test_candidate_card_accept_rejects_stale_or_archived_cards(tmp_path) -> None:
    vault, source, doc, _chunks = _vault_with_chunks(tmp_path)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        stale_card = generate_candidate_card(connection, doc_id=doc["doc_id"])

    source.write_text("# Candidate Source\nChanged before card acceptance.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        with pytest.raises(ValueError, match="stale"):
            accept_candidate_card(connection, vault, stale_card.candidate_card_id)

        active_card = generate_candidate_card(connection, doc_id=doc["doc_id"])
        archive_document(connection, doc["doc_id"])
        with pytest.raises(ValueError, match="not active"):
            accept_candidate_card(connection, vault, active_card.candidate_card_id)


def test_cli_card_accept_and_reject_json(tmp_path) -> None:
    runner = CliRunner()
    vault = tmp_path / "vault"
    source = tmp_path / "source.md"
    source.write_text("# Candidate CLI\nCandidate card review commands.\n", encoding="utf-8")

    assert runner.invoke(app, ["init", str(vault)]).exit_code == 0
    assert runner.invoke(app, ["ingest", str(source), "--vault", str(vault)]).exit_code == 0
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc = connection.execute("SELECT doc_id FROM documents").fetchone()

    generated = runner.invoke(app, ["card", "generate", doc["doc_id"], "--vault", str(vault), "--json"])
    accepted = runner.invoke(
        app,
        ["card", "accept", json.loads(generated.output)["candidate_card_id"], "--vault", str(vault), "--json"],
    )
    second = runner.invoke(app, ["card", "generate", doc["doc_id"], "--vault", str(vault), "--json"])
    rejected = runner.invoke(
        app,
        [
            "card",
            "reject",
            json.loads(second.output)["candidate_card_id"],
            "--vault",
            str(vault),
            "--reason",
            "not useful",
            "--json",
        ],
    )

    accepted_payload = json.loads(accepted.output)
    rejected_payload = json.loads(rejected.output)

    assert generated.exit_code == 0
    assert accepted.exit_code == 0
    assert accepted_payload["status"] == "accepted"
    assert accepted_payload["accepted_note_path"]
    assert (vault / accepted_payload["accepted_note_path"]).is_file()
    assert second.exit_code == 0
    assert rejected.exit_code == 0
    assert rejected_payload["status"] == "rejected"
    assert rejected_payload["accepted_note_path"] is None
