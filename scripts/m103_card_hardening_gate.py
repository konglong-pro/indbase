"""Run the M10.3 candidate card hardening gate against temporary vaults."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3

from indbase_core.cards import accept_candidate_card, generate_candidate_card
from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.vault import init_vault


ROOT = Path.cwd()


def main() -> None:
    root = ROOT / ".tmp" / f"m103-card-hardening-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True)
    summary = _run_gate(root)

    hard_zero = {
        "orphan_candidate_cards_undetected": summary["orphan_candidate_cards_undetected"],
        "missing_accepted_note_undetected": summary["missing_accepted_note_undetected"],
        "accepted_card_without_sources_undetected": summary["accepted_card_without_sources_undetected"],
        "uncited_accepted_claims_undetected": summary["uncited_accepted_claims_undetected"],
        "orphan_atomic_notes_undetected": summary["orphan_atomic_notes_undetected"],
    }
    if any(value != 0 for value in hard_zero.values()):
        raise RuntimeError(f"M10.3 hard zero metrics failed: {hard_zero}")

    positives = {
        "orphan_candidate_cards_detected": summary["orphan_candidate_cards_detected"] > 0,
        "missing_accepted_note_detected": summary["missing_accepted_note_detected"] == 1,
        "accepted_card_without_sources_detected": summary["accepted_card_without_sources_detected"] == 1,
        "uncited_accepted_claims_detected": summary["uncited_accepted_claims_detected"] == 1,
        "orphan_atomic_notes_detected": summary["orphan_atomic_notes_detected"] == 1,
    }
    if not all(positives.values()):
        raise RuntimeError(f"M10.3 positive metrics failed: {positives}; summary={summary}")

    print(json.dumps(summary, sort_keys=True))
    print("M103_CARD_HARDENING_GATE=passed")
    print(f"DOGFOOD_ROOT={root}")


def _run_gate(root: Path) -> dict[str, int]:
    orphan = _orphan_candidate_card_gate(root / "orphan-card")
    missing_note = _missing_accepted_note_gate(root / "missing-note")
    uncited = _uncited_claim_gate(root / "uncited")
    orphan_note = _orphan_atomic_note_gate(root / "orphan-note")
    return {**orphan, **missing_note, **uncited, **orphan_note}


def _orphan_candidate_card_gate(root: Path) -> dict[str, int]:
    vault, doc_id = _vault_with_current_doc(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        generated = generate_candidate_card(connection, doc_id=doc_id)

    raw_connection = sqlite3.connect(vault / ".indbase" / "db.sqlite")
    try:
        raw_connection.execute(
            """
            UPDATE candidate_cards
            SET source_doc_id = 'doc_missing',
                source_revision_id = 'rev_missing'
            WHERE candidate_card_id = ?
            """,
            (generated.candidate_card_id,),
        )
        raw_connection.execute(
            """
            UPDATE candidate_card_sources
            SET source_chunk_id = 'chunk_missing'
            WHERE candidate_card_id = ?
            """,
            (generated.candidate_card_id,),
        )
        raw_connection.commit()
    finally:
        raw_connection.close()

    codes = _doctor_codes(vault)
    expected = {
        "orphan_candidate_card_document",
        "orphan_candidate_card_revision",
        "candidate_card_claim_source_chunk_missing",
        "candidate_card_source_card_mismatch",
        "orphan_candidate_card_source_chunk",
    }
    detected = len(codes & expected)
    return {
        "orphan_candidate_cards_detected": detected,
        "orphan_candidate_cards_undetected": len(expected) - detected,
    }


def _missing_accepted_note_gate(root: Path) -> dict[str, int]:
    vault, doc_id = _vault_with_current_doc(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        generated = generate_candidate_card(connection, doc_id=doc_id)
        accepted = accept_candidate_card(connection, vault, generated.candidate_card_id)
    (vault / accepted.accepted_note_path).unlink()
    codes = _doctor_codes(vault)
    detected = int("missing_accepted_note" in codes)
    return {
        "missing_accepted_note_detected": detected,
        "missing_accepted_note_undetected": 1 - detected,
    }


def _uncited_claim_gate(root: Path) -> dict[str, int]:
    vault, doc_id = _vault_with_current_doc(root)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        generated = generate_candidate_card(connection, doc_id=doc_id)
        accepted = accept_candidate_card(connection, vault, generated.candidate_card_id)
        connection.execute("DELETE FROM candidate_card_sources WHERE candidate_card_id = ?", (generated.candidate_card_id,))
        connection.commit()
    note_path = vault / accepted.accepted_note_path
    note_path.write_text(note_path.read_text(encoding="utf-8").replace("Citations:", "References:"), encoding="utf-8")
    codes = _doctor_codes(vault)
    sources_detected = int("accepted_card_without_sources" in codes)
    uncited_detected = int("accepted_note_uncited_claim" in codes)
    return {
        "accepted_card_without_sources_detected": sources_detected,
        "accepted_card_without_sources_undetected": 1 - sources_detected,
        "uncited_accepted_claims_detected": uncited_detected,
        "uncited_accepted_claims_undetected": 1 - uncited_detected,
    }


def _orphan_atomic_note_gate(root: Path) -> dict[str, int]:
    vault = root / "vault"
    init_vault(vault)
    orphan = vault / "notes" / "atomic" / "2099" / "01" / "candidate_card_missing.md"
    orphan.parent.mkdir(parents=True)
    orphan.write_text(
        "---\n"
        "schema_version: \"indbase.atomic_note.v1\"\n"
        "type: \"candidate_card\"\n"
        "candidate_card_id: \"candidate_card_missing\"\n"
        "---\n"
        "\n"
        "# Orphan\n",
        encoding="utf-8",
    )
    codes = _doctor_codes(vault)
    detected = int("orphan_atomic_note" in codes)
    return {
        "orphan_atomic_notes_detected": detected,
        "orphan_atomic_notes_undetected": 1 - detected,
    }


def _vault_with_current_doc(root: Path) -> tuple[Path, str]:
    vault = root / "vault"
    source = root / "source.md"
    root.mkdir(parents=True)
    source.write_text(
        "# Candidate Source\n"
        "Candidate card hardening verifies generated knowledge artifacts.\n\n"
        "## Evidence\n"
        "Every accepted claim must keep source chunk identifiers and citations.\n",
        encoding="utf-8",
    )
    init_vault(vault)
    run_m3_ingest_pipeline(vault, source)
    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc = connection.execute("SELECT doc_id FROM documents").fetchone()
    return vault, str(doc["doc_id"])


def _doctor_codes(vault: Path) -> set[str]:
    return {finding.code for finding in run_doctor(vault).findings}


if __name__ == "__main__":
    main()
