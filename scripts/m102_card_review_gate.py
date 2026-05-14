"""Run the M10.2 candidate card review gate against temporary vaults."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from indbase_core.cards import accept_candidate_card, generate_candidate_card, reject_candidate_card
from indbase_core.db import connect
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.vault import init_vault


ROOT = Path.cwd()


def main() -> None:
    root = ROOT / ".tmp" / f"m102-card-review-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    summary = _run_gate(root)

    hard_zero = {
        "rejected_cards_written_to_atomic": summary["rejected_cards_written_to_atomic"],
        "accepted_cards_missing_note": summary["accepted_cards_missing_note"],
        "accepted_notes_missing_source_chunks": summary["accepted_notes_missing_source_chunks"],
        "accepted_notes_with_uncited_claims": summary["accepted_notes_with_uncited_claims"],
        "accepted_cards_reaccepted": summary["accepted_cards_reaccepted"],
        "accepted_cards_rejected": summary["accepted_cards_rejected"],
        "rejected_cards_accepted": summary["rejected_cards_accepted"],
    }
    if any(value != 0 for value in hard_zero.values()):
        raise RuntimeError(f"M10.2 hard zero metrics failed: {hard_zero}")

    positives = {
        "accepted_cards_written": summary["accepted_cards_written"] > 0,
        "candidate_sources_preserved_after_reject": summary["candidate_sources_preserved_after_reject"] == 1,
    }
    if not all(positives.values()):
        raise RuntimeError(f"M10.2 positive metrics failed: {positives}; summary={summary}")

    print(json.dumps(summary, sort_keys=True))
    print("M102_CARD_REVIEW_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_gate(root: Path) -> dict[str, int]:
    accept = _accept_gate(root / "accept")
    reject = _reject_gate(root / "reject")
    terminal = _terminal_gate(root / "terminal")
    return {**accept, **reject, **terminal}


def _accept_gate(root: Path) -> dict[str, int]:
    vault, doc_id = _vault_with_current_doc(root)
    atomic_before = _atomic_note_count(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        generated = generate_candidate_card(connection, doc_id=doc_id)
        accepted = accept_candidate_card(connection, vault, generated.candidate_card_id)
        card = connection.execute(
            "SELECT claims_json, accepted_note_path FROM candidate_cards WHERE candidate_card_id = ?",
            (generated.candidate_card_id,),
        ).fetchone()
        sources = connection.execute(
            "SELECT source_chunk_id FROM candidate_card_sources WHERE candidate_card_id = ?",
            (generated.candidate_card_id,),
        ).fetchall()
    atomic_after = _atomic_note_count(vault)
    note_missing = int(not accepted.accepted_note_path or not (vault / accepted.accepted_note_path).is_file())
    note_text = "" if note_missing else (vault / accepted.accepted_note_path).read_text(encoding="utf-8")
    claims = json.loads(card["claims_json"])
    source_chunk_ids = [str(row["source_chunk_id"]) for row in sources]
    missing_source_chunks = sum(1 for chunk_id in source_chunk_ids if chunk_id not in note_text)
    uncited_claims = 0
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        claim_chunks = [str(chunk_id) for chunk_id in claim.get("source_chunk_ids", [])]
        if claim_id not in note_text or "Citations:" not in note_text:
            uncited_claims += 1
            continue
        if any(chunk_id not in note_text for chunk_id in claim_chunks):
            uncited_claims += 1

    return {
        "accepted_cards_written": atomic_after - atomic_before,
        "accepted_cards_missing_note": note_missing,
        "accepted_notes_missing_source_chunks": missing_source_chunks,
        "accepted_notes_with_uncited_claims": uncited_claims,
    }


def _reject_gate(root: Path) -> dict[str, int]:
    vault, doc_id = _vault_with_current_doc(root)
    atomic_before = _atomic_note_count(vault)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        generated = generate_candidate_card(connection, doc_id=doc_id)
        sources_before = _count(
            connection,
            "SELECT COUNT(*) AS count FROM candidate_card_sources WHERE candidate_card_id = ?",
            (generated.candidate_card_id,),
        )
        reject_candidate_card(connection, generated.candidate_card_id, reason="gate rejected")
        sources_after = _count(
            connection,
            "SELECT COUNT(*) AS count FROM candidate_card_sources WHERE candidate_card_id = ?",
            (generated.candidate_card_id,),
        )
    atomic_after = _atomic_note_count(vault)
    return {
        "rejected_cards_written_to_atomic": atomic_after - atomic_before,
        "candidate_sources_preserved_after_reject": int(sources_before > 0 and sources_before == sources_after),
    }


def _terminal_gate(root: Path) -> dict[str, int]:
    vault, doc_id = _vault_with_current_doc(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        accepted_card = generate_candidate_card(connection, doc_id=doc_id)
        accept_candidate_card(connection, vault, accepted_card.candidate_card_id)
        accepted_cards_reaccepted = _operation_succeeded(
            lambda: accept_candidate_card(connection, vault, accepted_card.candidate_card_id)
        )
        accepted_cards_rejected = _operation_succeeded(
            lambda: reject_candidate_card(connection, accepted_card.candidate_card_id)
        )

        rejected_card = generate_candidate_card(connection, doc_id=doc_id)
        reject_candidate_card(connection, rejected_card.candidate_card_id)
        rejected_cards_accepted = _operation_succeeded(
            lambda: accept_candidate_card(connection, vault, rejected_card.candidate_card_id)
        )
    return {
        "accepted_cards_reaccepted": accepted_cards_reaccepted,
        "accepted_cards_rejected": accepted_cards_rejected,
        "rejected_cards_accepted": rejected_cards_accepted,
    }


def _vault_with_current_doc(root: Path) -> tuple[Path, str]:
    vault = root / "vault"
    source = root / "source.md"
    root.mkdir(parents=True)
    source.write_text(
        "# Candidate Source\n"
        "Candidate review accepts cited claims into atomic notes.\n\n"
        "## Evidence\n"
        "Every accepted note must preserve source chunk identifiers and quotes.\n",
        encoding="utf-8",
    )
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc = connection.execute("SELECT doc_id FROM documents").fetchone()
    return vault, str(doc["doc_id"])


def _atomic_note_count(vault: Path) -> int:
    return len(list((vault / "notes" / "atomic").glob("**/*.md")))


def _operation_succeeded(action) -> int:
    try:
        action()
    except ValueError:
        return 0
    return 1


def _count(connection, sql: str, params: tuple[object, ...] = ()) -> int:
    return int(connection.execute(sql, params).fetchone()["count"] or 0)


if __name__ == "__main__":
    main()
