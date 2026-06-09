"""Durable provider evidence copy helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from typing import Any, Iterable

from indbase_core.artifacts.evidence import ArtifactRef, ArtifactTrustLevel
from indbase_core.paths import VaultPaths
from indbase_core.provider_runs import provider_evidence_indbase_uri


@dataclass(frozen=True)
class CopiedEvidence:
    evidence_root: str
    copied_artifacts: tuple[ArtifactRef, ...]
    manifest: ArtifactRef | None
    trace: ArtifactRef | None
    evidence_index: ArtifactRef
    provider_metadata: ArtifactRef


def copy_artifact_to_provider_evidence(
    paths: VaultPaths,
    *,
    provider_run_id: str,
    source: Path | str,
    destination_name: str,
    kind: str,
    role: str | None = None,
    trust_level: ArtifactTrustLevel | str = ArtifactTrustLevel.EVIDENCE,
) -> ArtifactRef | None:
    source_path = _resolve_source(paths, source)
    if source_path is None:
        return None
    evidence_root = paths.provider_run_evidence_dir(provider_run_id)
    evidence_root.mkdir(parents=True, exist_ok=True)
    destination = _available_path(evidence_root / destination_name)
    shutil.copy2(source_path, destination)
    return ArtifactRef(
        kind=kind,
        vault_path=paths.relative_to_vault(destination),
        indbase_uri=provider_evidence_indbase_uri(provider_run_id),
        trust_level=trust_level,
        role=role,
    )


def write_text_artifact_to_provider_evidence(
    paths: VaultPaths,
    *,
    provider_run_id: str,
    destination_name: str,
    text: str,
    kind: str,
    role: str | None = None,
    trust_level: ArtifactTrustLevel | str = ArtifactTrustLevel.EVIDENCE,
) -> ArtifactRef:
    evidence_root = paths.provider_run_evidence_dir(provider_run_id)
    evidence_root.mkdir(parents=True, exist_ok=True)
    destination = evidence_root / destination_name
    destination.write_text(text, encoding="utf-8")
    return ArtifactRef(
        kind=kind,
        vault_path=paths.relative_to_vault(destination),
        indbase_uri=provider_evidence_indbase_uri(provider_run_id),
        trust_level=trust_level,
        role=role,
    )


def write_json_artifact_to_provider_evidence(
    paths: VaultPaths,
    *,
    provider_run_id: str,
    destination_name: str,
    payload: dict[str, Any],
    kind: str,
    role: str | None = None,
    trust_level: ArtifactTrustLevel | str = ArtifactTrustLevel.EVIDENCE,
) -> ArtifactRef:
    return write_text_artifact_to_provider_evidence(
        paths,
        provider_run_id=provider_run_id,
        destination_name=destination_name,
        text=json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        kind=kind,
        role=role,
        trust_level=trust_level,
    )


def write_provider_evidence_index(
    paths: VaultPaths,
    *,
    provider_run_id: str,
    provider: dict[str, Any],
    artifacts: Iterable[ArtifactRef],
    manifest: ArtifactRef | None = None,
    trace: ArtifactRef | None = None,
) -> CopiedEvidence:
    copied = tuple(artifacts)
    provider_metadata = write_json_artifact_to_provider_evidence(
        paths,
        provider_run_id=provider_run_id,
        destination_name="provider.json",
        payload=provider,
        kind="provider_metadata",
        role="provider_metadata",
    )
    index_payload = {
        "provider_run_id": provider_run_id,
        "provider": provider,
        "artifacts": [artifact.to_dict() for artifact in copied],
        "manifest": manifest.to_dict() if manifest else None,
        "trace": trace.to_dict() if trace else None,
    }
    evidence_index = write_json_artifact_to_provider_evidence(
        paths,
        provider_run_id=provider_run_id,
        destination_name="evidence_index.json",
        payload=index_payload,
        kind="evidence_index",
        role="evidence_index",
    )
    return CopiedEvidence(
        evidence_root=paths.relative_to_vault(paths.provider_run_evidence_dir(provider_run_id)),
        copied_artifacts=copied,
        manifest=manifest,
        trace=trace,
        evidence_index=evidence_index,
        provider_metadata=provider_metadata,
    )


def _resolve_source(paths: VaultPaths, source: Path | str) -> Path | None:
    source_path = Path(source)
    if source_path.is_file():
        return source_path
    vault_candidate = paths.root / source_path
    if vault_candidate.is_file():
        return vault_candidate
    return None


def _available_path(destination: Path) -> Path:
    if not destination.exists():
        return destination
    stem = destination.stem
    suffix = destination.suffix
    index = 2
    while True:
        candidate = destination.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1
