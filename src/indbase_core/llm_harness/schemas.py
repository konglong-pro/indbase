"""Schema validation for harness outputs (stdlib only)."""

from __future__ import annotations

from indbase_core.llm_harness.errors import HarnessValidationError

ALLOWED_DECISIONS = frozenset(
    {
        "alias_to_existing",
        "new_tag_candidate",
        "local_keyword",
        "ambiguous",
        "category_assign",
        "no_category",
    }
)

REQUIRED_BASE_FIELDS = ("decision", "confidence", "reason")


def validate_taxonomy_arbitration_output(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise HarnessValidationError("LLM output must be a JSON object.")
    decision = str(payload.get("decision", "")).strip()
    if decision not in ALLOWED_DECISIONS:
        raise HarnessValidationError(f"Unsupported decision: {decision!r}")
    for field in REQUIRED_BASE_FIELDS:
        if field not in payload:
            raise HarnessValidationError(f"Missing required field: {field}")
    confidence = payload["confidence"]
    if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
        raise HarnessValidationError("confidence must be a number between 0 and 1.")
    reason = str(payload["reason"]).strip()
    if not reason:
        raise HarnessValidationError("reason must not be empty.")
    if decision == "alias_to_existing" and not payload.get("tag_id"):
        raise HarnessValidationError("alias_to_existing requires tag_id.")
    if decision == "new_tag_candidate" and not payload.get("candidate_name"):
        raise HarnessValidationError("new_tag_candidate requires candidate_name.")
    if decision == "category_assign" and not payload.get("category_id"):
        raise HarnessValidationError("category_assign requires category_id.")
    evidence = payload.get("evidence_chunk_ids")
    if evidence is not None and not isinstance(evidence, list):
        raise HarnessValidationError("evidence_chunk_ids must be a list when provided.")
    return payload


def validate_quote_evidence(
    payload: dict[str, object],
    *,
    chunk_quotes: dict[str, str],
) -> None:
    """Ensure quoted evidence is present in the referenced chunk text."""
    decision = str(payload["decision"])
    if decision in {"category_assign", "no_category", "ambiguous"}:
        return
    evidence_ids = payload.get("evidence_chunk_ids") or []
    if not isinstance(evidence_ids, list) or not evidence_ids:
        raise HarnessValidationError("decision requires evidence_chunk_ids.")
    quote = payload.get("quote")
    if quote is None or not str(quote).strip():
        raise HarnessValidationError("decision requires quote evidence.")
    quote_text = str(quote)
    for chunk_id in evidence_ids:
        chunk_id_str = str(chunk_id)
        chunk_text = chunk_quotes.get(chunk_id_str)
        if chunk_text is None:
            raise HarnessValidationError(f"Unknown evidence chunk: {chunk_id_str}")
        if quote_text not in chunk_text:
            raise HarnessValidationError(
                f"quote is not an exact substring of chunk {chunk_id_str}"
            )
