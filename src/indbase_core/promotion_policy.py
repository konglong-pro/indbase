"""Promotion policy for swallow conversion candidates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from indbase_core.artifact_policy import locator_artifact_values
from indbase_core.config import IndbaseConfig

TRUSTED_CURRENT = "trusted-current"
REVIEW_BEFORE_CURRENT = "review-before-current"
FAILED = "failed"

_SUCCESS_STATUSES = {"success", "succeeded"}
_PARTIAL_STATUSES = {"partial"}
_FAILED_STATUSES = {"failed", "error", "errored", "cancelled", "canceled", "timeout", "timed_out"}
_LOCATOR_REQUIRED_MODES = ("ocr", "asr", "web", "browser_capture", "archive")
_FATAL_WARNING_MARKERS = (
    "security",
    "sandbox",
    "path traversal",
    "path_traversal",
    "zip slip",
    "zip_slip",
    "unsafe path",
    "permission denied",
    "blocked by policy",
    "outside allowed root",
)


@dataclass(frozen=True)
class PromotionDecision:
    status: str
    reason: str
    reason_code: str
    source_modes: tuple[str, ...] = ()


def evaluate_swallow_promotion(
    indbase_config: IndbaseConfig,
    *,
    status: str,
    quality_score: float | None,
    markdown_body: str,
    warnings: Iterable[str] = (),
    errors: Iterable[str] = (),
    provenance: object | None = None,
    source_locators: Iterable[dict[str, object]] = (),
    source_snapshot_path: str | None = None,
    artifact_manifest_required: Iterable[str] = (),
    archived_artifacts: Iterable[str] = (),
    access_context: str = "local_file",
    privacy_flags: Mapping[str, object] | None = None,
    trusted_reason: str = "Swallow candidate passed indbase promotion gate.",
    generic_review_reason: str = "Swallow candidate requires review.",
) -> PromotionDecision:
    """Evaluate whether a swallow candidate may become a current revision."""

    normalized_status = str(status or "").strip().lower()
    normalized_warnings = tuple(str(item) for item in warnings if item is not None)
    normalized_errors = tuple(str(item) for item in errors if item is not None)
    locators = tuple(locator for locator in source_locators if isinstance(locator, dict))
    required_artifacts = tuple(str(item) for item in artifact_manifest_required if item)
    archived = tuple(str(item) for item in archived_artifacts if item)
    privacy = dict(privacy_flags or {})
    source_modes = _source_modes(provenance, locators, access_context)

    if not indbase_config.features.swallow_ingest:
        return _failed("feature_swallow_disabled", "Swallow ingest requires features.swallow_ingest = true.", source_modes)

    disabled_reason = _disabled_source_reason(indbase_config, source_modes, provenance, access_context, privacy)
    if disabled_reason is not None:
        return _failed(disabled_reason[0], disabled_reason[1], source_modes)

    if not markdown_body.strip():
        return _failed("empty_markdown", "Swallow candidate has empty Markdown.", source_modes)

    fatal_warning = _first_fatal_warning((*normalized_warnings, *normalized_errors))
    if fatal_warning is not None:
        return _failed("fatal_warning", f"Swallow candidate has fatal warning: {fatal_warning}", source_modes)

    if normalized_status in _FAILED_STATUSES:
        return _failed("swallow_status_failed", f"Swallow candidate status is {status}.", source_modes)
    if normalized_status in _PARTIAL_STATUSES and not indbase_config.ingest.swallow.allow_partial_auto_current:
        return _review("partial_status", "Swallow returned partial output; candidate requires review.", source_modes)
    if normalized_status not in _SUCCESS_STATUSES | _PARTIAL_STATUSES:
        return _failed("swallow_status_unknown", f"Swallow candidate status is {status}.", source_modes)

    missing_evidence = missing_durable_evidence_artifacts(
        source_snapshot_path=source_snapshot_path,
        source_locators=locators,
        artifact_manifest_required=required_artifacts if indbase_config.ingest.swallow.archive_required_artifacts else (),
        archived_artifacts=archived,
    )
    if missing_evidence:
        return _review(
            "missing_durable_evidence",
            "Swallow candidate is missing durable evidence artifacts: " + ", ".join(missing_evidence),
            source_modes,
        )

    if quality_score is None:
        return _review("missing_quality_score", "Swallow candidate has no quality score.", source_modes)
    if quality_score < indbase_config.ingest.swallow.review_quality_threshold:
        return _failed(
            "quality_below_review_threshold",
            "Swallow candidate quality is below the review threshold.",
            source_modes,
        )
    if len(markdown_body.strip()) < indbase_config.ingest.swallow.min_markdown_chars:
        return _review(
            "markdown_below_min_length",
            "Swallow candidate is below the minimum Markdown length.",
            source_modes,
        )

    locator_reason = _missing_locator_reason(source_modes, locators)
    if locator_reason is not None:
        return _review(locator_reason[0], locator_reason[1], source_modes)

    mode_policy_reason = _mode_policy_review_reason(indbase_config, source_modes)
    if mode_policy_reason is not None:
        return _review(mode_policy_reason[0], mode_policy_reason[1], source_modes)

    provenance_reason = _missing_provenance_reason(provenance)
    if provenance_reason is not None:
        return _review(provenance_reason[0], provenance_reason[1], source_modes)

    if quality_score < indbase_config.ingest.swallow.auto_current_quality_threshold:
        return _review("quality_below_auto_threshold", generic_review_reason, source_modes)

    return PromotionDecision(
        status=TRUSTED_CURRENT,
        reason=trusted_reason,
        reason_code="trusted_current",
        source_modes=source_modes,
    )


def missing_durable_evidence_artifacts(
    *,
    source_snapshot_path: str | None,
    source_locators: Iterable[dict[str, object]],
    artifact_manifest_required: Iterable[str],
    archived_artifacts: Iterable[str],
) -> tuple[str, ...]:
    archived_paths = {str(artifact) for artifact in archived_artifacts if artifact}
    archived_names = {Path(artifact).name for artifact in archived_paths}
    required: list[str] = []
    if source_snapshot_path:
        required.append(str(source_snapshot_path))
    required.extend(str(artifact) for artifact in artifact_manifest_required if artifact)
    for locator in source_locators:
        if isinstance(locator, dict):
            required.extend(locator_artifact_values(locator))

    missing: list[str] = []
    for artifact in required:
        if artifact in archived_paths or Path(artifact).name in archived_names:
            continue
        missing.append(artifact)
    return tuple(dict.fromkeys(missing))


def _source_modes(provenance: object | None, locators: tuple[dict[str, object], ...], access_context: str) -> tuple[str, ...]:
    modes: set[str] = set()
    worker_names = _worker_names(provenance)
    for worker in worker_names:
        if "paddleocr" in worker or worker.endswith("ocr_worker") or worker == "ocr":
            modes.add("ocr")
        if "whisper" in worker or "asr" in worker:
            modes.add("asr")
        if "export_archive" in worker or "archive" in worker:
            modes.add("archive")
        if "browser_capture" in worker or "browser_extension" in worker:
            modes.add("browser_capture")
        if "playwright" in worker or "crawl" in worker or worker.startswith("url_"):
            modes.add("web")

    kinds = {str(locator.get("kind") or "").lower() for locator in locators}
    if kinds & {"ocr", "ocr_page"}:
        modes.add("ocr")
    if kinds & {"asr_transcript", "asr_segment", "time_range"}:
        modes.add("asr")
    if kinds & {"web_snapshot"}:
        modes.add("web")
    if kinds & {"browser_capture"}:
        modes.add("browser_capture")
    if kinds & {"archive_member"}:
        modes.add("archive")

    context = access_context.lower()
    if context in {"public_url", "dynamic_public_url"}:
        modes.add("web")
    if context in {"local_authenticated_profile", "browser_extension_capture"}:
        modes.add("browser_capture")
    if context == "local_archive":
        modes.add("archive")

    priority = ("ocr", "asr", "browser_capture", "web", "archive")
    return tuple(mode for mode in priority if mode in modes)


def _worker_names(provenance: object | None) -> tuple[str, ...]:
    if provenance is None:
        return ()
    names: list[str] = []
    primary_worker = getattr(provenance, "primary_worker", None)
    if primary_worker:
        names.append(str(primary_worker))
    worker_chain = getattr(provenance, "worker_chain", None) or ()
    names.extend(str(worker) for worker in worker_chain if worker)
    return tuple(name.lower() for name in names)


def _disabled_source_reason(
    indbase_config: IndbaseConfig,
    source_modes: tuple[str, ...],
    provenance: object | None,
    access_context: str,
    privacy_flags: Mapping[str, object],
) -> tuple[str, str] | None:
    if "ocr" in source_modes and not indbase_config.features.ocr:
        return ("feature_ocr_disabled", "OCR ingest requires features.ocr = true.")
    if "asr" in source_modes and not indbase_config.features.asr:
        return ("feature_asr_disabled", "ASR ingest requires features.asr = true.")
    if ("web" in source_modes or _uses_network(provenance, privacy_flags)) and not indbase_config.features.web_ingest:
        return ("feature_web_disabled", "Web ingest requires features.web_ingest = true.")
    if _uses_authenticated_context(source_modes, access_context, privacy_flags) and not indbase_config.features.login_profile_ingest:
        return (
            "feature_login_profile_disabled",
            "Authenticated browser capture requires features.login_profile_ingest = true.",
        )
    if _uses_external_provider(provenance, access_context, privacy_flags) and not indbase_config.features.external_ingest_providers:
        return (
            "feature_external_provider_disabled",
            "External ingest providers require features.external_ingest_providers = true.",
        )
    return None


def _uses_network(provenance: object | None, privacy_flags: Mapping[str, object]) -> bool:
    return bool(getattr(provenance, "requires_network", False)) or _truthy(privacy_flags.get("network_required_for_capture"))


def _uses_authenticated_context(
    source_modes: tuple[str, ...],
    access_context: str,
    privacy_flags: Mapping[str, object],
) -> bool:
    return (
        "browser_capture" in source_modes
        or access_context.lower() in {"local_authenticated_profile", "browser_extension_capture"}
        or _truthy(privacy_flags.get("may_contain_cookies"))
        or _truthy(privacy_flags.get("contains_private_account_data"))
    )


def _uses_external_provider(
    provenance: object | None,
    access_context: str,
    privacy_flags: Mapping[str, object],
) -> bool:
    return (
        bool(getattr(provenance, "requires_external_service", False))
        or access_context.lower() == "external_provider_capture"
        or _truthy(privacy_flags.get("requires_external_service"))
    )


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _first_fatal_warning(warnings: Iterable[str]) -> str | None:
    for warning in warnings:
        normalized = warning.lower()
        if any(marker in normalized for marker in _FATAL_WARNING_MARKERS):
            return warning
    return None


def _missing_locator_reason(
    source_modes: tuple[str, ...],
    locators: tuple[dict[str, object], ...],
) -> tuple[str, str] | None:
    for mode in _LOCATOR_REQUIRED_MODES:
        if mode not in source_modes:
            continue
        if not _has_mode_locator(mode, locators):
            return (f"missing_{mode}_source_locators", f"{_mode_label(mode)} candidate is missing source locators.")
        if mode == "asr" and not _has_asr_timestamp_locator(locators):
            return ("missing_asr_timestamp_locators", "ASR candidate is missing timestamp source locators.")
    return None


def _has_mode_locator(mode: str, locators: tuple[dict[str, object], ...]) -> bool:
    expected = {
        "ocr": {"ocr", "ocr_page"},
        "asr": {"asr_transcript", "asr_segment", "time_range"},
        "web": {"web_snapshot"},
        "browser_capture": {"browser_capture"},
        "archive": {"archive_member"},
    }[mode]
    return any(str(locator.get("kind") or "").lower() in expected for locator in locators)


def _has_asr_timestamp_locator(locators: tuple[dict[str, object], ...]) -> bool:
    for locator in locators:
        kind = str(locator.get("kind") or "").lower()
        if kind in {"asr_segment", "time_range"} and (
            locator.get("start_seconds") is not None
            or locator.get("end_seconds") is not None
            or locator.get("start") is not None
            or locator.get("end") is not None
        ):
            return True
    return False


def _mode_label(mode: str) -> str:
    return {
        "ocr": "OCR",
        "asr": "ASR",
        "web": "Web",
        "browser_capture": "Browser capture",
        "archive": "Archive",
    }[mode]


def _mode_policy_review_reason(
    indbase_config: IndbaseConfig,
    source_modes: tuple[str, ...],
) -> tuple[str, str] | None:
    if "ocr" in source_modes and not indbase_config.ingest.swallow.allow_ocr_auto_current:
        return ("ocr_auto_current_disabled", "OCR candidate requires review by policy.")
    if "asr" in source_modes and not indbase_config.ingest.swallow.allow_asr_auto_current:
        return ("asr_auto_current_disabled", "ASR candidate requires review by policy.")
    if (
        "web" in source_modes or "browser_capture" in source_modes
    ) and not indbase_config.ingest.swallow.allow_web_auto_current:
        return ("web_auto_current_disabled", "Web candidate requires review by policy.")
    return None


def _missing_provenance_reason(provenance: object | None) -> tuple[str, str] | None:
    if provenance is None or not getattr(provenance, "trace_path", None):
        return ("missing_trace_provenance", "Swallow candidate is missing required trace provenance.")
    if not tuple(getattr(provenance, "worker_chain", None) or ()):
        return ("missing_worker_chain", "Swallow candidate is missing required worker-chain provenance.")
    return None


def _failed(reason_code: str, reason: str, source_modes: tuple[str, ...]) -> PromotionDecision:
    return PromotionDecision(status=FAILED, reason=reason, reason_code=reason_code, source_modes=source_modes)


def _review(reason_code: str, reason: str, source_modes: tuple[str, ...]) -> PromotionDecision:
    return PromotionDecision(
        status=REVIEW_BEFORE_CURRENT,
        reason=reason,
        reason_code=reason_code,
        source_modes=source_modes,
    )
