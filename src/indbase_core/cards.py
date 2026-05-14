"""Candidate card extraction and review records for M10."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from indbase_core.ids import new_prefixed_id
from indbase_core.paths import vault_paths
from indbase_core.tasks import add_task_event, create_task, finish_task, start_task
from indbase_core.time import utc_now_iso


CANDIDATE_CARD_MODEL = "local/deterministic-candidate-card-v1"
PROMPT_VERSION = "m10.1"
DEFAULT_STATUS = "reviewing"
MAX_CLAIMS = 3
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class CandidateCardResult:
    candidate_card_id: str
    task_id: str
    source_doc_id: str
    source_revision_id: str
    source_chunk_ids: tuple[str, ...]
    claims_count: int
    status: str


@dataclass(frozen=True)
class CandidateCardReviewResult:
    candidate_card_id: str
    task_id: str
    source_doc_id: str
    source_revision_id: str
    status: str
    accepted_note_path: str | None


def generate_candidate_card(
    connection: sqlite3.Connection,
    *,
    doc_id: str,
    revision_id: str | None = None,
    max_claims: int = MAX_CLAIMS,
) -> CandidateCardResult:
    """Generate a source-bound candidate card record for one current document."""
    if max_claims < 1:
        raise ValueError("max_claims must be >= 1")
    document = _load_candidate_document(connection, doc_id)
    selected_revision_id = revision_id or str(document["current_revision_id"] or "")
    _validate_document_for_candidate(document, selected_revision_id)
    chunks = _load_candidate_chunks(connection, doc_id=doc_id, revision_id=selected_revision_id)
    if not chunks:
        raise ValueError(f"Document current revision has no chunks: {doc_id}")

    task_id = create_task(
        connection,
        "candidate_card_generate",
        input_data={
            "doc_id": doc_id,
            "revision_id": selected_revision_id,
            "max_claims": max_claims,
            "model": CANDIDATE_CARD_MODEL,
            "prompt_version": PROMPT_VERSION,
        },
    )
    start_task(connection, task_id)
    add_task_event(
        connection,
        task_id,
        "candidate_card_started",
        "Candidate card extraction started.",
        {"doc_id": doc_id, "revision_id": selected_revision_id, "max_claims": max_claims},
    )

    candidate_card_id = new_prefixed_id("candidate_card")
    claims = _build_claims(chunks[:max_claims])
    if not claims:
        raise ValueError(f"Document current revision has no extractable claim text: {doc_id}")

    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO candidate_cards(
          candidate_card_id, source_doc_id, source_revision_id, title,
          claims_json, model, prompt_version, status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            candidate_card_id,
            doc_id,
            selected_revision_id,
            _candidate_title(document, claims),
            _json(claims),
            CANDIDATE_CARD_MODEL,
            PROMPT_VERSION,
            DEFAULT_STATUS,
            now,
            now,
        ),
    )
    for claim in claims:
        for source_chunk_id in claim["source_chunk_ids"]:
            quote = _quote_for_chunk(claim, str(source_chunk_id))
            connection.execute(
                """
                INSERT INTO candidate_card_sources(
                  candidate_card_source_id, candidate_card_id, source_doc_id,
                  source_revision_id, source_chunk_id, claim_id, quote,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_prefixed_id("candidate_card_source"),
                    candidate_card_id,
                    doc_id,
                    selected_revision_id,
                    source_chunk_id,
                    claim["claim_id"],
                    quote,
                    now,
                    now,
                ),
            )

    source_chunk_ids = tuple(str(chunk["chunk_id"]) for chunk in chunks[: len(claims)])
    result_data = {
        "candidate_card_id": candidate_card_id,
        "doc_id": doc_id,
        "revision_id": selected_revision_id,
        "claims_count": len(claims),
        "source_chunk_ids": list(source_chunk_ids),
    }
    finish_task(connection, task_id, "succeeded", result_data=result_data)
    add_task_event(
        connection,
        task_id,
        "candidate_card_finished",
        "Candidate card extraction finished.",
        result_data,
    )
    connection.commit()
    return CandidateCardResult(
        candidate_card_id=candidate_card_id,
        task_id=task_id,
        source_doc_id=doc_id,
        source_revision_id=selected_revision_id,
        source_chunk_ids=source_chunk_ids,
        claims_count=len(claims),
        status=DEFAULT_STATUS,
    )


def list_candidate_cards(
    connection: sqlite3.Connection,
    *,
    doc_id: str | None = None,
    status: str | None = DEFAULT_STATUS,
    limit: int = 20,
    active_current_only: bool = True,
) -> list[sqlite3.Row]:
    """List candidate cards, hiding stale old-revision cards by default."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    clauses = ["cc.deleted_at IS NULL"]
    params: list[object] = []
    if status is not None:
        clauses.append("cc.status = ?")
        params.append(status)
    if doc_id is not None:
        clauses.append("cc.source_doc_id = ?")
        params.append(doc_id)
    if active_current_only:
        clauses.extend(
            [
                "d.status = 'active'",
                "d.deleted_at IS NULL",
                "d.current_revision_id = cc.source_revision_id",
            ]
        )
    params.append(limit)
    return list(
        connection.execute(
            f"""
            SELECT cc.candidate_card_id, cc.source_doc_id, d.title AS document_title,
                   cc.source_revision_id, cc.title, cc.claims_json, cc.model,
                   cc.prompt_version, cc.status, cc.accepted_note_path,
                   cc.created_at, cc.updated_at,
                   COUNT(ccs.source_chunk_id) AS source_count
            FROM candidate_cards cc
            JOIN documents d ON d.doc_id = cc.source_doc_id
            LEFT JOIN candidate_card_sources ccs
              ON ccs.candidate_card_id = cc.candidate_card_id
             AND ccs.deleted_at IS NULL
            WHERE {" AND ".join(clauses)}
            GROUP BY cc.candidate_card_id
            ORDER BY cc.created_at DESC, cc.candidate_card_id
            LIMIT ?
            """,
            params,
        )
    )


def get_candidate_card(connection: sqlite3.Connection, candidate_card_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT cc.candidate_card_id, cc.source_doc_id, d.title AS document_title,
               d.status AS document_status, d.current_revision_id,
               cc.source_revision_id, cc.title, cc.claims_json, cc.model,
               cc.prompt_version, cc.status, cc.accepted_note_path,
               cc.created_at, cc.updated_at, cc.deleted_at
        FROM candidate_cards cc
        JOIN documents d ON d.doc_id = cc.source_doc_id
        WHERE cc.candidate_card_id = ?
        """,
        (candidate_card_id,),
    ).fetchone()


def list_candidate_card_sources(
    connection: sqlite3.Connection,
    candidate_card_id: str,
) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT candidate_card_source_id, candidate_card_id, source_doc_id,
                   source_revision_id, source_chunk_id, claim_id, quote,
                   created_at, updated_at
            FROM candidate_card_sources
            WHERE candidate_card_id = ?
              AND deleted_at IS NULL
            ORDER BY claim_id, source_chunk_id
            """,
            (candidate_card_id,),
        )
    )


def accept_candidate_card(
    connection: sqlite3.Connection,
    vault_path: Path | str,
    candidate_card_id: str,
    *,
    reviewer: str = "cli",
) -> CandidateCardReviewResult:
    """Accept a reviewing candidate card and write one cited atomic note."""
    row = _require_reviewing_card(connection, candidate_card_id)
    _require_current_active_card(row)
    claims = _parse_claims(row["claims_json"])
    sources = list_candidate_card_sources(connection, candidate_card_id)
    _validate_claim_source_bindings(claims, sources)

    task_id = create_task(
        connection,
        "candidate_card_accept",
        input_data={"candidate_card_id": candidate_card_id, "reviewer": reviewer},
    )
    start_task(connection, task_id)
    add_task_event(
        connection,
        task_id,
        "candidate_card_accept_started",
        "Candidate card accept started.",
        {"candidate_card_id": candidate_card_id},
    )

    note_path = _write_accepted_atomic_note(vault_path, row=row, claims=claims, sources=sources)
    note_rel = vault_paths(vault_path).relative_to_vault(note_path)
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE candidate_cards
        SET status = 'accepted',
            accepted_note_path = ?,
            updated_at = ?
        WHERE candidate_card_id = ?
        """,
        (note_rel, now, candidate_card_id),
    )
    result_data = {
        "candidate_card_id": candidate_card_id,
        "accepted_note_path": note_rel,
        "source_doc_id": row["source_doc_id"],
        "source_revision_id": row["source_revision_id"],
    }
    finish_task(connection, task_id, "succeeded", result_data=result_data)
    add_task_event(
        connection,
        task_id,
        "candidate_card_accepted",
        "Candidate card accepted and atomic note written.",
        result_data,
    )
    connection.commit()
    return CandidateCardReviewResult(
        candidate_card_id=candidate_card_id,
        task_id=task_id,
        source_doc_id=str(row["source_doc_id"]),
        source_revision_id=str(row["source_revision_id"]),
        status="accepted",
        accepted_note_path=note_rel,
    )


def reject_candidate_card(
    connection: sqlite3.Connection,
    candidate_card_id: str,
    *,
    reason: str | None = None,
    reviewer: str = "cli",
) -> CandidateCardReviewResult:
    """Reject a reviewing candidate card without writing an atomic note."""
    row = _require_reviewing_card(connection, candidate_card_id)
    task_id = create_task(
        connection,
        "candidate_card_reject",
        input_data={"candidate_card_id": candidate_card_id, "reason": reason, "reviewer": reviewer},
    )
    start_task(connection, task_id)
    add_task_event(
        connection,
        task_id,
        "candidate_card_reject_started",
        "Candidate card reject started.",
        {"candidate_card_id": candidate_card_id, "reason": reason},
    )
    now = utc_now_iso()
    connection.execute(
        """
        UPDATE candidate_cards
        SET status = 'rejected',
            updated_at = ?
        WHERE candidate_card_id = ?
        """,
        (now, candidate_card_id),
    )
    result_data = {
        "candidate_card_id": candidate_card_id,
        "reason": reason,
        "source_doc_id": row["source_doc_id"],
        "source_revision_id": row["source_revision_id"],
    }
    finish_task(connection, task_id, "succeeded", result_data=result_data)
    add_task_event(
        connection,
        task_id,
        "candidate_card_rejected",
        "Candidate card rejected.",
        result_data,
    )
    connection.commit()
    return CandidateCardReviewResult(
        candidate_card_id=candidate_card_id,
        task_id=task_id,
        source_doc_id=str(row["source_doc_id"]),
        source_revision_id=str(row["source_revision_id"]),
        status="rejected",
        accepted_note_path=None,
    )


def _load_candidate_document(connection: sqlite3.Connection, doc_id: str) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT doc_id, title, status, current_revision_id, ingest_status
        FROM documents
        WHERE doc_id = ?
          AND deleted_at IS NULL
        """,
        (doc_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Document not found: {doc_id}")
    return row


def _require_reviewing_card(connection: sqlite3.Connection, candidate_card_id: str) -> sqlite3.Row:
    row = get_candidate_card(connection, candidate_card_id)
    if row is None or row["deleted_at"] is not None:
        raise ValueError(f"Candidate card not found: {candidate_card_id}")
    if row["status"] != DEFAULT_STATUS:
        raise ValueError(f"Candidate card is not reviewing: {candidate_card_id}")
    return row


def _require_current_active_card(row: sqlite3.Row) -> None:
    if row["document_status"] != "active":
        raise ValueError(f"Document is not active: {row['source_doc_id']}")
    if row["current_revision_id"] != row["source_revision_id"]:
        raise ValueError(f"Candidate card is stale for document: {row['source_doc_id']}")


def _validate_document_for_candidate(document: sqlite3.Row, revision_id: str) -> None:
    doc_id = str(document["doc_id"])
    if document["status"] != "active":
        raise ValueError(f"Document is not active: {doc_id}")
    if document["current_revision_id"] is None:
        raise ValueError(f"Document has no current revision: {doc_id}")
    if document["current_revision_id"] != revision_id:
        raise ValueError(f"M10.1 candidate extraction requires the current revision for document {doc_id}: {revision_id}")
    if document["ingest_status"] != "revisioned":
        raise ValueError(f"Document is not a revisioned source document: {doc_id}")


def _load_candidate_chunks(
    connection: sqlite3.Connection,
    *,
    doc_id: str,
    revision_id: str,
) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT chunk_id, doc_id, revision_id, sequence, heading_path_json, text
            FROM chunks
            WHERE doc_id = ?
              AND revision_id = ?
              AND is_current = 1
              AND deleted_at IS NULL
            ORDER BY sequence, chunk_id
            """,
            (doc_id, revision_id),
        )
    )


def _validate_claim_source_bindings(claims: list[dict[str, object]], sources: list[sqlite3.Row]) -> None:
    source_chunk_ids = {str(source["source_chunk_id"]) for source in sources}
    if not source_chunk_ids:
        raise ValueError("Candidate card has no source chunk bindings.")
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        raw_chunk_ids = claim.get("source_chunk_ids", [])
        raw_quotes = claim.get("quotes", [])
        chunk_ids = [str(chunk_id) for chunk_id in raw_chunk_ids] if isinstance(raw_chunk_ids, list) else []
        quotes = raw_quotes if isinstance(raw_quotes, list) else []
        if not claim_id:
            raise ValueError("Candidate card claim is missing claim_id.")
        if not chunk_ids:
            raise ValueError(f"Candidate card claim has no source chunks: {claim_id}")
        if any(chunk_id not in source_chunk_ids for chunk_id in chunk_ids):
            raise ValueError(f"Candidate card claim references missing source chunk: {claim_id}")
        if not quotes:
            raise ValueError(f"Candidate card claim has no citation quote: {claim_id}")
        for quote in quotes:
            if not isinstance(quote, dict) or not str(quote.get("text", "")).strip():
                raise ValueError(f"Candidate card claim has an empty citation quote: {claim_id}")


def _build_claims(chunks: list[sqlite3.Row]) -> list[dict[str, object]]:
    claims: list[dict[str, object]] = []
    for index, chunk in enumerate(chunks, start=1):
        quote = _compact_text(str(chunk["text"] or ""))
        if not quote:
            continue
        quote = _first_sentence_or_slice(quote, limit=240)
        claim_text = quote if len(quote) <= 180 else quote[:177].rstrip() + "..."
        claim_id = f"claim_{index:04d}"
        claims.append(
            {
                "claim_id": claim_id,
                "text": claim_text,
                "source_chunk_ids": [str(chunk["chunk_id"])],
                "quotes": [{"chunk_id": str(chunk["chunk_id"]), "text": quote}],
                "confidence": 0.72,
            }
        )
    return claims


def _write_accepted_atomic_note(
    vault_path: Path | str,
    *,
    row: sqlite3.Row,
    claims: list[dict[str, object]],
    sources: list[sqlite3.Row],
) -> Path:
    paths = vault_paths(vault_path)
    year, month = _note_year_month()
    note_dir = paths.notes_atomic / year / month
    note_dir.mkdir(parents=True, exist_ok=True)
    candidate_card_id = str(row["candidate_card_id"])
    note_path = note_dir / f"{candidate_card_id}.md"
    frontmatter = {
        "schema_version": "indbase.atomic_note.v1",
        "type": "candidate_card",
        "candidate_card_id": candidate_card_id,
        "source_doc_ids": sorted({str(row["source_doc_id"])}),
        "source_revision_ids": sorted({str(row["source_revision_id"])}),
        "source_chunk_ids": sorted({str(source["source_chunk_id"]) for source in sources}),
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "status": "accepted",
    }
    body = [
        "---",
        *(_yaml_line(key, value) for key, value in frontmatter.items()),
        "---",
        "",
        f"# {row['title'] or row['document_title'] or candidate_card_id}",
        "",
        f"candidate_card_id: `{candidate_card_id}`",
        f"source_doc_id: `{row['source_doc_id']}`",
        f"source_revision_id: `{row['source_revision_id']}`",
        "",
        "## Claims",
        "",
    ]
    for claim in claims:
        claim_id = str(claim["claim_id"])
        body.extend(
            [
                f"### {claim_id}",
                "",
                str(claim["text"]),
                "",
                "Citations:",
            ]
        )
        quotes = claim.get("quotes", [])
        if isinstance(quotes, list):
            for quote in quotes:
                if not isinstance(quote, dict):
                    continue
                body.extend(
                    [
                        f"- source_doc_id: `{row['source_doc_id']}`",
                        f"  source_revision_id: `{row['source_revision_id']}`",
                        f"  source_chunk_id: `{quote.get('chunk_id')}`",
                        f"  quote: {json.dumps(str(quote.get('text') or ''), ensure_ascii=False)}",
                    ]
                )
        body.append("")
    _write_new_file(note_path, "\n".join(body).rstrip() + "\n")
    return note_path


def _candidate_title(document: sqlite3.Row, claims: list[dict[str, object]]) -> str:
    title = str(document["title"] or "").strip()
    if title:
        return title
    return str(claims[0]["text"])[:80]


def _quote_for_chunk(claim: dict[str, object], chunk_id: str) -> str:
    quotes = claim.get("quotes", [])
    if isinstance(quotes, list):
        for quote in quotes:
            if isinstance(quote, dict) and quote.get("chunk_id") == chunk_id:
                return str(quote.get("text") or "")
    return str(claim.get("text") or "")


def _first_sentence_or_slice(text: str, *, limit: int) -> str:
    first = _SENTENCE_RE.split(text, maxsplit=1)[0].strip()
    selected = first or text
    if len(selected) <= limit:
        return selected
    return selected[: limit - 3].rstrip() + "..."


def _compact_text(value: str) -> str:
    return " ".join(value.split())


def _parse_claims(value: object) -> list[dict[str, object]]:
    parsed = json.loads(str(value or "[]"))
    if not isinstance(parsed, list):
        raise ValueError("Candidate card claims_json is not a list.")
    claims: list[dict[str, object]] = []
    for item in parsed:
        if not isinstance(item, dict):
            raise ValueError("Candidate card claims_json contains a non-object claim.")
        claims.append(item)
    return claims


def _note_year_month() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return f"{now:%Y}", f"{now:%m}"


def _write_new_file(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError(f"Atomic note already exists: {path}")
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _yaml_line(key: str, value: object) -> str:
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if value is None:
        return f"{key}: null"
    if isinstance(value, list):
        return f"{key}: {json.dumps(value, ensure_ascii=False)}"
    return f"{key}: {json.dumps(str(value), ensure_ascii=False)}"
