"""Copy transition bridge evidence into durable vault artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import shutil
from pathlib import Path

from indbase_core.paths import VaultPaths
from indbase_core.transition_config import config_hash
from indbase_core.transition_contract import BridgeEvidencePaths, BridgeResponse


@dataclass(frozen=True)
class ArchivedEvidence:
    manifest_path: str | None
    trace_path: str | None
    report_path: str | None
    config_snapshot_path: str | None
    input_hashes_path: str | None


def archive_partial_evidence(
    paths: VaultPaths,
    output_run_id: str,
    transition_config: dict[str, object],
    input_markdown: str,
    *,
    response: BridgeResponse | None = None,
    job_dir: Path | None = None,
    provider_run_id: str | None = None,
) -> ArchivedEvidence | None:
    """Archive durable evidence for failed runs when bridge output is partial."""
    if response is not None and (
        response.evidence_paths.manifest_path
        or response.evidence_paths.trace_path
        or response.evidence_paths.report_path
        or response.normalized_markdown
    ):
        return archive_transition_evidence(
            paths,
            output_run_id,
            response,
            transition_config=transition_config,
            input_markdown=input_markdown,
            provider_run_id=provider_run_id,
        )
    if job_dir is not None:
        from indbase_core.transition_adapter import evidence_paths_from_job_dir

        evidence_paths = evidence_paths_from_job_dir(job_dir)
        if not (
            evidence_paths.manifest_path or evidence_paths.trace_path or evidence_paths.report_path
        ):
            return None
        synthetic = BridgeResponse(
            contract_version="1",
            status="failed",
            normalized_markdown="",
            normalized_hash="",
            evidence_paths=evidence_paths,
            errors=("partial_evidence_only",),
        )
        return archive_transition_evidence(
            paths,
            output_run_id,
            synthetic,
            transition_config=transition_config,
            input_markdown=input_markdown,
            provider_run_id=provider_run_id,
        )
    return None


def archive_transition_evidence(
    paths: VaultPaths,
    output_run_id: str,
    response: BridgeResponse,
    *,
    transition_config: dict[str, object],
    input_markdown: str,
    provider_run_id: str | None = None,
) -> ArchivedEvidence:
    evidence_dir = (
        paths.provider_run_evidence_dir(provider_run_id)
        if provider_run_id
        else paths.output_run_evidence_dir(output_run_id)
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)

    manifest_rel = _copy_evidence_file(
        paths,
        response.evidence_paths.manifest_path,
        evidence_dir / "transition_manifest.json",
    )
    trace_rel = _copy_evidence_file(
        paths,
        response.evidence_paths.trace_path,
        evidence_dir / "transition_trace.jsonl",
    )
    report_rel = _copy_evidence_file(
        paths,
        response.evidence_paths.report_path,
        evidence_dir / "transition_report.json",
    )
    config_dest = evidence_dir / "config.json"
    config_dest.write_text(json.dumps(transition_config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    config_rel = paths.relative_to_vault(config_dest)

    input_hashes_dest = evidence_dir / "input_hashes.json"
    input_payload = {
        "input_sha256": hashlib.sha256(input_markdown.encode("utf-8")).hexdigest(),
        "normalized_sha256": response.normalized_hash or None,
        "config_hash": config_hash(transition_config),
        "partial": response.status == "failed" and not response.normalized_markdown,
    }
    input_hashes_dest.write_text(json.dumps(input_payload, indent=2) + "\n", encoding="utf-8")
    input_hashes_rel = paths.relative_to_vault(input_hashes_dest)
    if provider_run_id:
        _mirror_output_run_evidence(paths, output_run_id, evidence_dir)

    return ArchivedEvidence(
        manifest_path=manifest_rel,
        trace_path=trace_rel,
        report_path=report_rel,
        config_snapshot_path=config_rel,
        input_hashes_path=input_hashes_rel,
    )


def _copy_evidence_file(paths: VaultPaths, source: str | None, destination: Path) -> str | None:
    if not source:
        return None
    source_path = Path(source)
    if not source_path.is_file():
        vault_candidate = paths.root / source
        if vault_candidate.is_file():
            source_path = vault_candidate
        else:
            return None
    shutil.copy2(source_path, destination)
    return paths.relative_to_vault(destination)


def _mirror_output_run_evidence(paths: VaultPaths, output_run_id: str, evidence_dir: Path) -> None:
    mirror_dir = paths.output_run_evidence_dir(output_run_id)
    mirror_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "transition_manifest.json",
        "transition_trace.jsonl",
        "transition_report.json",
        "config.json",
        "input_hashes.json",
    ):
        source = evidence_dir / name
        if source.is_file():
            shutil.copy2(source, mirror_dir / name)
