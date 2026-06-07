"""Harness entry point: fake provider, validation, model call records."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from pathlib import Path

from indbase_core.errors import record_error
from indbase_core.ids import new_prefixed_id
from indbase_core.llm_harness.arbitration import apply_taxonomy_decision
from indbase_core.llm_harness.errors import HarnessTimeoutError, HarnessValidationError
from indbase_core.llm_harness.fake_provider import FakeLLMProvider
from indbase_core.llm_harness.registry import get_prompt
from indbase_core.llm_harness.schemas import validate_quote_evidence, validate_taxonomy_arbitration_output
from indbase_core.time import utc_now_iso

ALLOWED_PROVIDERS = frozenset({"fake"})

BUSINESS_MODULE_DENYLIST = (
    "indbase_core.classification",
    "indbase_core.taxonomy_manager",
    "indbase_core.category_manager",
    "indbase_core.tag_candidates",
    "indbase_core.taxonomy_mutations",
)


@dataclass(frozen=True)
class HarnessCallResult:
    model_call_id: str
    status: str
    suggestion_ids: tuple[str, ...]
    rejected: bool = False
    error_message: str | None = None


class LLMHarness:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        provider_name: str = "fake",
        vault_path: Path | str | None = None,
    ) -> None:
        if provider_name not in ALLOWED_PROVIDERS:
            raise ValueError(f"Unsupported harness provider: {provider_name}")
        self.connection = connection
        self.provider_name = provider_name
        self.vault_path = Path(vault_path) if vault_path is not None else None
        self._provider = FakeLLMProvider()

    def call_taxonomy_arbitration(
        self,
        *,
        prompt_name: str,
        doc_id: str,
        revision_id: str,
        input_payload: dict[str, object],
        scenario: str | None = None,
    ) -> HarnessCallResult:
        prompt = get_prompt(prompt_name)
        model_call_id = new_prefixed_id("modelcall")
        now = utc_now_iso()
        self._insert_model_call(
            model_call_id,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            status="pending",
            input_payload=input_payload,
            started_at=now,
        )
        try:
            response = self._provider.complete(
                prompt_name=prompt_name,
                input_payload=input_payload,
                scenario=scenario or "valid_category",
            )
            if response.status == "timeout":
                raise HarnessTimeoutError(response.error or "provider timeout")
            if response.output is None:
                raise HarnessValidationError("provider returned no output")
            validated = validate_taxonomy_arbitration_output(response.output)
            chunk_quotes = input_payload.get("chunk_quotes")
            if isinstance(chunk_quotes, dict):
                validate_quote_evidence(validated, chunk_quotes={str(k): str(v) for k, v in chunk_quotes.items()})
            suggestion_ids = tuple(
                apply_taxonomy_decision(
                    self.connection,
                    validated,
                    doc_id=doc_id,
                    revision_id=revision_id,
                )
            )
            self._finish_model_call(
                model_call_id,
                status="completed",
                output_payload=validated,
                finished_at=utc_now_iso(),
            )
            self.connection.commit()
            return HarnessCallResult(
                model_call_id=model_call_id,
                status="completed",
                suggestion_ids=suggestion_ids,
            )
        except HarnessValidationError as exc:
            self._finish_model_call(
                model_call_id,
                status="rejected",
                error_payload={"message": str(exc), "kind": "validation"},
                finished_at=utc_now_iso(),
            )
            self._record_harness_error(doc_id, str(exc))
            self.connection.commit()
            return HarnessCallResult(
                model_call_id=model_call_id,
                status="rejected",
                suggestion_ids=(),
                rejected=True,
                error_message=str(exc),
            )
        except HarnessTimeoutError as exc:
            self._finish_model_call(
                model_call_id,
                status="timeout",
                error_payload={"message": str(exc), "kind": "timeout"},
                finished_at=utc_now_iso(),
            )
            self._record_harness_error(doc_id, str(exc))
            self.connection.commit()
            return HarnessCallResult(
                model_call_id=model_call_id,
                status="timeout",
                suggestion_ids=(),
                rejected=True,
                error_message=str(exc),
            )

    def _insert_model_call(
        self,
        model_call_id: str,
        *,
        prompt_name: str,
        prompt_version: str,
        status: str,
        input_payload: dict[str, object],
        started_at: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO model_calls(
              model_call_id, provider, model, prompt_name, prompt_version,
              status, input_json, created_at, started_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_call_id,
                self.provider_name,
                self._provider.model_name,
                prompt_name,
                prompt_version,
                status,
                json.dumps(input_payload, ensure_ascii=False),
                started_at,
                started_at,
            ),
        )

    def _finish_model_call(
        self,
        model_call_id: str,
        *,
        status: str,
        output_payload: dict[str, object] | None = None,
        error_payload: dict[str, object] | None = None,
        finished_at: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE model_calls
            SET status = ?,
                output_json = ?,
                error_json = ?,
                finished_at = ?
            WHERE model_call_id = ?
            """,
            (
                status,
                json.dumps(output_payload, ensure_ascii=False) if output_payload is not None else None,
                json.dumps(error_payload, ensure_ascii=False) if error_payload is not None else None,
                finished_at,
                model_call_id,
            ),
        )

    def _record_harness_error(self, doc_id: str, message: str) -> None:
        record_error(
            self.connection,
            component="llm_harness",
            error_type="harness_failure",
            message=message,
            payload={"doc_id": doc_id, "vault_path": str(self.vault_path) if self.vault_path else None},
        )


def assert_no_direct_provider_usage() -> list[str]:
    """Return business module paths that import non-fake provider adapters directly."""
    root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for module_path in root.rglob("*.py"):
        if "llm_harness" in module_path.parts:
            continue
        rel = module_path.relative_to(root)
        module_name = ".".join(rel.with_suffix("").parts)
        if not any(module_name.startswith(prefix) for prefix in BUSINESS_MODULE_DENYLIST):
            continue
        text = module_path.read_text(encoding="utf-8")
        if "openai" in text.lower() or "anthropic" in text.lower():
            violations.append(module_name)
        if "FakeLLMProvider" in text and "llm_harness" not in text:
            violations.append(module_name)
    return violations
