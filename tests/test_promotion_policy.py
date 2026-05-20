from dataclasses import replace
from pathlib import Path

from indbase_core.config import default_config
from indbase_core.promotion_policy import (
    FAILED,
    REVIEW_BEFORE_CURRENT,
    TRUSTED_CURRENT,
    evaluate_swallow_promotion,
)
from indbase_core.swallow_adapter import SwallowProvenance


def _config(vault: Path, **feature_overrides):
    config = default_config(vault)
    features = replace(config.features, swallow_ingest=True, **feature_overrides)
    return replace(
        config,
        features=features,
        ingest=replace(
            config.ingest,
            swallow=replace(config.ingest.swallow, min_markdown_chars=10),
        ),
    )


def _provenance(
    *,
    primary_worker: str = "plain_text_worker",
    worker_chain: tuple[str, ...] | None = None,
    trace_path: str | None = "jobs/job/trace.jsonl",
    requires_network: bool = False,
    requires_external_service: bool = False,
    access_context: str = "local_file",
) -> SwallowProvenance:
    return SwallowProvenance(
        swallow_job_id="job",
        swallow_raw_id="raw",
        swallow_document_id="doc",
        swallow_version="test",
        primary_worker=primary_worker,
        worker_version="test",
        worker_chain=worker_chain if worker_chain is not None else (f"{primary_worker}@test", "quality_checker@test"),
        trace_path=trace_path,
        manifest_path="jobs/job/manifest.json",
        ingest_document_path="jobs/job/ingest_document.json",
        requires_network=requires_network,
        requires_external_service=requires_external_service,
        access_context=access_context,
    )


def _decision(config, **overrides):
    payload = {
        "status": "success",
        "quality_score": 0.92,
        "markdown_body": "# Title\n\nBody with enough searchable text.",
        "warnings": (),
        "errors": (),
        "provenance": _provenance(),
        "source_locators": (),
        "source_snapshot_path": None,
        "artifact_manifest_required": ("jobs/job/trace.jsonl",),
        "archived_artifacts": (".indbase/artifacts/doc/run/trace.jsonl",),
        "access_context": "local_file",
        "privacy_flags": {},
    }
    payload.update(overrides)
    return evaluate_swallow_promotion(config, **payload)


def test_partial_candidate_requires_review_unless_policy_allows_auto_current(tmp_path: Path) -> None:
    config = _config(tmp_path)

    review = _decision(config, status="partial")
    allowed_config = replace(
        config,
        ingest=replace(
            config.ingest,
            swallow=replace(config.ingest.swallow, allow_partial_auto_current=True),
        ),
    )
    trusted = _decision(allowed_config, status="partial")

    assert review.status == REVIEW_BEFORE_CURRENT
    assert review.reason == "Swallow returned partial output; candidate requires review."
    assert trusted.status == TRUSTED_CURRENT


def test_web_source_requires_feature_flag_and_can_be_review_only_by_policy(tmp_path: Path) -> None:
    locator = {
        "kind": "web_snapshot",
        "url": "https://example.com",
        "artifact": "jobs/job/rendered.html",
    }
    required = ("jobs/job/trace.jsonl", "jobs/job/rendered.html")
    archived = (
        ".indbase/artifacts/doc/run/trace.jsonl",
        ".indbase/artifacts/doc/run/rendered.html",
    )

    disabled = _decision(
        _config(tmp_path),
        provenance=_provenance(primary_worker="playwright_worker", requires_network=True, access_context="public_url"),
        source_locators=(locator,),
        source_snapshot_path="jobs/job/rendered.html",
        artifact_manifest_required=required,
        archived_artifacts=archived,
        access_context="public_url",
    )
    review_config = _config(tmp_path, web_ingest=True)
    review_config = replace(
        review_config,
        ingest=replace(
            review_config.ingest,
            swallow=replace(review_config.ingest.swallow, allow_web_auto_current=False),
        ),
    )
    review = _decision(
        review_config,
        provenance=_provenance(primary_worker="playwright_worker", requires_network=True, access_context="public_url"),
        source_locators=(locator,),
        source_snapshot_path="jobs/job/rendered.html",
        artifact_manifest_required=required,
        archived_artifacts=archived,
        access_context="public_url",
    )

    assert disabled.status == FAILED
    assert disabled.reason == "Web ingest requires features.web_ingest = true."
    assert review.status == REVIEW_BEFORE_CURRENT
    assert review.reason == "Web candidate requires review by policy."


def test_external_provider_requires_explicit_opt_in(tmp_path: Path) -> None:
    config = _config(tmp_path, web_ingest=True)

    decision = _decision(
        config,
        provenance=_provenance(
            primary_worker="firecrawl_worker",
            requires_network=True,
            requires_external_service=True,
            access_context="external_provider_capture",
        ),
        access_context="external_provider_capture",
        privacy_flags={"requires_external_service": True},
    )

    assert decision.status == FAILED
    assert decision.reason == "External ingest providers require features.external_ingest_providers = true."


def test_fatal_security_or_sandbox_warning_fails(tmp_path: Path) -> None:
    config = _config(tmp_path)

    decision = _decision(config, warnings=("security sandbox blocked unsafe path",))

    assert decision.status == FAILED
    assert decision.reason == "Swallow candidate has fatal warning: security sandbox blocked unsafe path"


def test_missing_worker_chain_blocks_auto_promotion(tmp_path: Path) -> None:
    config = _config(tmp_path)

    decision = _decision(config, provenance=_provenance(worker_chain=()))

    assert decision.status == REVIEW_BEFORE_CURRENT
    assert decision.reason == "Swallow candidate is missing required worker-chain provenance."


def test_archive_conversion_requires_logical_source_locators(tmp_path: Path) -> None:
    config = _config(tmp_path)

    decision = _decision(
        config,
        provenance=_provenance(primary_worker="export_archive_worker", access_context="local_archive"),
        access_context="local_archive",
    )

    assert decision.status == REVIEW_BEFORE_CURRENT
    assert decision.reason == "Archive candidate is missing source locators."
