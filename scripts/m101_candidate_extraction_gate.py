"""Run the M10.1 candidate extraction gate against temporary vaults."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import indbase_core.normalizers as normalizers
from indbase_core.cards import generate_candidate_card, list_candidate_cards
from indbase_core.db import connect
from indbase_core.documents import archive_document
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.vault import init_vault


ROOT = Path.cwd()


def main() -> None:
    root = ROOT / ".tmp" / f"m101-candidate-extraction-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    original_markitdown = normalizers._run_markitdown_file
    try:
        summary = _run_gate(root)
    finally:
        normalizers._run_markitdown_file = original_markitdown

    hard_zero = {
        "atomic_notes_written_before_accept": summary["atomic_notes_written_before_accept"],
        "claims_without_source_chunks": summary["claims_without_source_chunks"],
        "claims_with_missing_chunks": summary["claims_with_missing_chunks"],
        "claims_without_quotes": summary["claims_without_quotes"],
        "source_shell_cards": summary["source_shell_cards"],
        "archived_doc_cards": summary["archived_doc_cards"],
        "old_revision_cards_default": summary["old_revision_cards_default"],
        "no_chunk_cards": summary["no_chunk_cards"],
        "old_revision_candidates_default_listed": summary["old_revision_candidates_default_listed"],
    }
    if any(value != 0 for value in hard_zero.values()):
        raise RuntimeError(f"M10.1 hard zero metrics failed: {hard_zero}")

    positives = {
        "candidate_cards_created": summary["candidate_cards_created"] > 0,
        "candidate_sources_created": summary["candidate_sources_created"] > 0,
        "stale_candidate_cards_viewable": summary["stale_candidate_cards_viewable"] == 1,
    }
    if not all(positives.values()):
        raise RuntimeError(f"M10.1 positive metrics failed: {positives}; summary={summary}")

    print(json.dumps(summary, sort_keys=True))
    print("M101_CANDIDATE_EXTRACTION_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_gate(root: Path) -> dict[str, int]:
    success = _successful_card_gate(root / "success")
    blocked = _blocked_state_gate(root / "blocked")
    stale = _stale_card_gate(root / "stale")
    return {**success, **blocked, **stale}


def _successful_card_gate(root: Path) -> dict[str, int]:
    vault, _source, doc_id, _revision_id = _vault_with_current_doc(root)
    atomic_before = _atomic_note_count(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = generate_candidate_card(connection, doc_id=doc_id, max_claims=3)
        atomic_after = _atomic_note_count(vault)
        card_rows = connection.execute("SELECT * FROM candidate_cards").fetchall()
        source_rows = connection.execute("SELECT * FROM candidate_card_sources").fetchall()
        existing_chunks = {
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
                (result.source_doc_id, result.source_revision_id),
            )
        }
    claim_metrics = _claim_metrics(card_rows, existing_chunks)
    return {
        "candidate_cards_created": len(card_rows),
        "candidate_sources_created": len(source_rows),
        "atomic_notes_written_before_accept": atomic_after - atomic_before,
        **claim_metrics,
    }


def _blocked_state_gate(root: Path) -> dict[str, int]:
    archived_cards = _archived_doc_card_attempt(root / "archived")
    old_revision_cards = _old_revision_card_attempt(root / "old-revision")
    shell_cards = _source_shell_card_attempt(root / "source-shell")
    no_chunk_cards = _no_chunk_card_attempt(root / "no-chunk")
    return {
        "archived_doc_cards": archived_cards,
        "old_revision_cards_default": old_revision_cards,
        "source_shell_cards": shell_cards,
        "no_chunk_cards": no_chunk_cards,
    }


def _archived_doc_card_attempt(root: Path) -> int:
    vault, _source, doc_id, _revision_id = _vault_with_current_doc(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        archive_document(connection, doc_id)
        _expect_value_error(lambda: generate_candidate_card(connection, doc_id=doc_id))
        return _count(connection, "SELECT COUNT(*) AS count FROM candidate_cards")


def _old_revision_card_attempt(root: Path) -> int:
    vault, source, doc_id, old_revision_id = _vault_with_current_doc(root)
    source.write_text("# Candidate Source\nChanged content for the current revision.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        _expect_value_error(lambda: generate_candidate_card(connection, doc_id=doc_id, revision_id=old_revision_id))
        return _count(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM candidate_cards
            WHERE source_doc_id = ?
              AND source_revision_id = ?
            """,
            (doc_id, old_revision_id),
        )


def _source_shell_card_attempt(root: Path) -> int:
    vault = root / "vault"
    source = root / "scan.pdf"
    root.mkdir(parents=True)
    source.write_bytes(b"%PDF image only")
    init_vault(vault)
    normalizers._run_markitdown_file = lambda _path: " "
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc = connection.execute("SELECT doc_id FROM documents").fetchone()
        _expect_value_error(lambda: generate_candidate_card(connection, doc_id=doc["doc_id"]))
        return _count(connection, "SELECT COUNT(*) AS count FROM candidate_cards")


def _no_chunk_card_attempt(root: Path) -> int:
    vault, _source, doc_id, revision_id = _vault_with_current_doc(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        connection.execute("DELETE FROM chunks_fts WHERE doc_id = ? AND revision_id = ?", (doc_id, revision_id))
        connection.execute("DELETE FROM chunks WHERE doc_id = ? AND revision_id = ?", (doc_id, revision_id))
        connection.commit()
        _expect_value_error(lambda: generate_candidate_card(connection, doc_id=doc_id))
        return _count(connection, "SELECT COUNT(*) AS count FROM candidate_cards")


def _stale_card_gate(root: Path) -> dict[str, int]:
    vault, source, doc_id, old_revision_id = _vault_with_current_doc(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        result = generate_candidate_card(connection, doc_id=doc_id)
    source.write_text("# Candidate Source\nNew current revision should hide old candidate cards.\n", encoding="utf-8")
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        default_rows = list_candidate_cards(connection)
        stale_rows = list_candidate_cards(connection, status=None, active_current_only=False)
        old_default_rows = [
            row for row in default_rows
            if row["source_doc_id"] == doc_id and row["source_revision_id"] == old_revision_id
        ]
        stale_viewable = any(row["candidate_card_id"] == result.candidate_card_id for row in stale_rows)
    return {
        "old_revision_candidates_default_listed": len(old_default_rows),
        "stale_candidate_cards_viewable": int(stale_viewable),
    }


def _vault_with_current_doc(root: Path) -> tuple[Path, Path, str, str]:
    vault = root / "vault"
    source = root / "source.md"
    root.mkdir(parents=True)
    source.write_text(
        "# Candidate Source\n"
        "Candidate extraction keeps claims bound to source chunks.\n\n"
        "## Evidence\n"
        "Each generated claim must cite a current chunk and preserve a quote.\n\n"
        "## Boundary\n"
        "M10.1 writes candidate records but no accepted atomic notes.\n",
        encoding="utf-8",
    )
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc = connection.execute("SELECT doc_id, current_revision_id FROM documents").fetchone()
    return vault, source, str(doc["doc_id"]), str(doc["current_revision_id"])


def _claim_metrics(card_rows, existing_chunks: set[str]) -> dict[str, int]:
    without_source_chunks = 0
    with_missing_chunks = 0
    without_quotes = 0
    for card in card_rows:
        claims = json.loads(card["claims_json"] or "[]")
        for claim in claims:
            chunk_ids = [str(chunk_id) for chunk_id in claim.get("source_chunk_ids", [])]
            quotes = claim.get("quotes", [])
            if not chunk_ids:
                without_source_chunks += 1
            if any(chunk_id not in existing_chunks for chunk_id in chunk_ids):
                with_missing_chunks += 1
            if not quotes or any(not str(quote.get("text", "")).strip() for quote in quotes):
                without_quotes += 1
    return {
        "claims_without_source_chunks": without_source_chunks,
        "claims_with_missing_chunks": with_missing_chunks,
        "claims_without_quotes": without_quotes,
    }


def _atomic_note_count(vault: Path) -> int:
    return len(list((vault / "notes" / "atomic").glob("**/*.md")))


def _expect_value_error(action) -> None:
    try:
        action()
    except ValueError:
        return
    raise RuntimeError("expected candidate extraction to reject invalid source state")


def _count(connection, sql: str, params: tuple[object, ...] = ()) -> int:
    return int(connection.execute(sql, params).fetchone()["count"] or 0)


if __name__ == "__main__":
    main()
