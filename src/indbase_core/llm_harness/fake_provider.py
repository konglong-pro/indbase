"""Fake LLM provider for deterministic harness contract tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FakeProviderResponse:
    status: str
    output: dict[str, object] | None = None
    error: str | None = None


class FakeLLMProvider:
    """Deterministic fake provider; never performs network I/O."""

    provider_name = "fake"
    model_name = "fake-taxonomy-v1"

    def complete(
        self,
        *,
        prompt_name: str,
        input_payload: dict[str, object],
        scenario: str = "valid_category",
    ) -> FakeProviderResponse:
        if scenario == "timeout":
            return FakeProviderResponse(status="timeout", error="fake provider timeout")
        if scenario == "invalid_schema":
            return FakeProviderResponse(status="completed", output={"decision": "not_allowed"})
        if scenario == "invalid_quote":
            chunk_id = _first_chunk_id(input_payload)
            return FakeProviderResponse(
                status="completed",
                output={
                    "decision": "new_tag_candidate",
                    "candidate_name": "synthetic-candidate",
                    "confidence": 0.71,
                    "reason": "Synthetic candidate without valid quote evidence.",
                    "evidence_chunk_ids": [chunk_id] if chunk_id else [],
                    "quote": "this substring does not exist in the chunk",
                },
            )
        if scenario == "valid_tag_candidate":
            chunk_id = _first_chunk_id(input_payload)
            quote = _chunk_quote(input_payload, chunk_id)
            return FakeProviderResponse(
                status="completed",
                output={
                    "decision": "new_tag_candidate",
                    "candidate_name": str(input_payload.get("feature_text") or "synthetic-candidate"),
                    "confidence": 0.78,
                    "reason": "Fake provider proposes a new tag candidate.",
                    "evidence_chunk_ids": [chunk_id] if chunk_id else [],
                    "quote": quote,
                },
            )
        if scenario == "valid_alias":
            tag_id = str(input_payload.get("similar_tag_id") or "tag_missing")
            chunk_id = _first_chunk_id(input_payload)
            quote = _chunk_quote(input_payload, chunk_id)
            return FakeProviderResponse(
                status="completed",
                output={
                    "decision": "alias_to_existing",
                    "tag_id": tag_id,
                    "confidence": 0.84,
                    "reason": "Fake provider treats feature as alias of existing tag.",
                    "evidence_chunk_ids": [chunk_id] if chunk_id else [],
                    "quote": quote,
                },
            )
        category_id = str(input_payload.get("default_category_id") or "cat_uncategorized")
        return FakeProviderResponse(
            status="completed",
            output={
                "decision": "category_assign",
                "category_id": category_id,
                "confidence": 0.81,
                "reason": "Fake provider category arbitration.",
                "evidence_chunk_ids": list((input_payload.get("chunk_quotes") or {}).keys())[:3],
            },
        )


def _first_chunk_id(input_payload: dict[str, object]) -> str | None:
    chunk_quotes = input_payload.get("chunk_quotes")
    if isinstance(chunk_quotes, dict) and chunk_quotes:
        return str(next(iter(chunk_quotes.keys())))
    return None


def _chunk_quote(input_payload: dict[str, object], chunk_id: str | None) -> str:
    if chunk_id is None:
        return "evidence"
    chunk_quotes = input_payload.get("chunk_quotes")
    if isinstance(chunk_quotes, dict):
        text = str(chunk_quotes.get(chunk_id) or "")
        if text:
            return text[: min(32, len(text))]
    return "evidence"
