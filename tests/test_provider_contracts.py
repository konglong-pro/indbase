import pytest

from indbase_core.artifacts.evidence import ArtifactRef, ArtifactTrustLevel
from indbase_core.capabilities.contracts import (
    CAPABILITY_CONTRACT_VERSION,
    CapabilityManifest,
    EvidencePackageStatus,
    IndbaseProviderErrorCode,
    IngestEvidencePackage,
    InputRef,
    OutputEvidencePackage,
    ProviderError,
    ProviderProfile,
    ProviderWarning,
    SourceBinding,
)


def test_capability_manifest_keeps_logical_provider_id_separate_from_package() -> None:
    manifest = CapabilityManifest(
        provider_id="acme_ingest",
        provider_package="acme-indbase-provider",
        provider_version="1.0.0",
        capability_id="acme.ingest.file",
        capability_contract_version=CAPABILITY_CONTRACT_VERSION,
        profiles=(ProviderProfile.LOCAL_CORE,),
    )

    assert manifest.provider_id == "acme_ingest"
    assert manifest.provider_package == "acme-indbase-provider"
    assert manifest.contract_compatible is True
    assert manifest.to_dict()["profiles"] == ["local_core"]


def test_capability_manifest_requires_namespaced_capability_id() -> None:
    with pytest.raises(ValueError, match="globally namespaced"):
        CapabilityManifest(
            provider_id="swallow",
            provider_package="swallow",
            provider_version="0.2.0",
            capability_id="ingest_file",
            capability_contract_version=CAPABILITY_CONTRACT_VERSION,
            profiles=(ProviderProfile.LOCAL_CORE,),
        )


def test_artifact_ref_tracks_provider_copy_and_external_uri_boundaries() -> None:
    provider_ref = ArtifactRef(
        kind="trace",
        provider_uri="swallow://jobs/job_1/trace",
        trust_level=ArtifactTrustLevel.EVIDENCE,
    )
    copied = provider_ref.copied_to(
        vault_path=".indbase/artifacts/provider_runs/prun_1/trace.jsonl",
        indbase_uri="indbase://provider_runs/prun_1/evidence",
    )

    assert copied.provider_uri == "swallow://jobs/job_1/trace"
    assert copied.vault_path.endswith("trace.jsonl")
    assert copied.to_external_dict() == {
        "kind": "trace",
        "uri": "indbase://provider_runs/prun_1/evidence",
        "trust_level": "evidence",
    }


def test_artifact_ref_without_indbase_uri_is_not_external() -> None:
    provider_ref = ArtifactRef(kind="manifest", provider_uri="transition://jobs/job_1/manifest")

    with pytest.raises(ValueError, match="indbase_uri"):
        provider_ref.to_external_dict()


def test_provider_error_preserves_raw_and_mapped_codes() -> None:
    error = ProviderError(
        provider_code="QUALITY_BELOW_THRESHOLD",
        indbase_code=IndbaseProviderErrorCode.PROVIDER_QUALITY_REJECTED,
        message="Candidate quality was below promotion threshold.",
    )

    assert error.primary_error_code == "provider_quality_rejected"
    assert error.to_dict()["provider_code"] == "QUALITY_BELOW_THRESHOLD"


def test_ingest_evidence_package_collects_provider_artifact_refs() -> None:
    package = IngestEvidencePackage(
        provider_id="swallow",
        provider_version="0.2.0",
        capability_id="swallow.ingest.file",
        transport_profile=ProviderProfile.LOCAL_CORE,
        operation_id="op_1",
        provider_job_id="job_1",
        status=EvidencePackageStatus.SUCCESS,
        input_ref=InputRef(kind="file", path="notes.pdf", sha256="input_sha"),
        candidate_markdown=ArtifactRef(
            kind="candidate_markdown",
            vault_path=".indbase/artifacts/provider_runs/prun_1/document.md",
            trust_level=ArtifactTrustLevel.CONVERSION_CANDIDATE,
        ),
        ingest_document=ArtifactRef(
            kind="ingest_document",
            vault_path=".indbase/artifacts/provider_runs/prun_1/ingest_document.json",
        ),
        manifest=ArtifactRef(kind="manifest", vault_path=".indbase/artifacts/provider_runs/prun_1/manifest.json"),
        trace=ArtifactRef(kind="trace", vault_path=".indbase/artifacts/provider_runs/prun_1/trace.jsonl"),
        warnings=(ProviderWarning(code="minor_locator_gap", message="Some locators were approximate."),),
        content_hash="content_sha",
        quality={"score": 0.91},
    )

    assert package.status == EvidencePackageStatus.SUCCESS
    assert package.transport_profile == ProviderProfile.LOCAL_CORE
    assert len(package.evidence_refs) == 4
    assert package.to_dict()["quality"] == {"score": 0.91}


def test_output_evidence_package_models_normalized_candidate_and_exports() -> None:
    package = OutputEvidencePackage(
        provider_id="transition",
        provider_version="0.1.0",
        capability_id="transition.markdown.export",
        transport_profile=ProviderProfile.NODE_BRIDGE,
        operation_id="op_2",
        provider_job_id="job_2",
        status="partial",
        source_binding=SourceBinding(
            doc_id="doc_20260609_abcd12",
            revision_id="rev_doc_20260609_abcd12_0001",
            content_sha256="source_sha",
        ),
        input_sha256="source_sha",
        normalized_markdown=ArtifactRef(
            kind="markdown",
            vault_path=".indbase/artifacts/provider_runs/prun_2/normalized.md",
            trust_level=ArtifactTrustLevel.DERIVED_CANDIDATE,
            role="normalized_candidate",
        ),
        derived_outputs=(
            ArtifactRef(
                kind="html",
                vault_path=".indbase/artifacts/provider_runs/prun_2/outputs/source.html",
                trust_level=ArtifactTrustLevel.DERIVED_OUTPUT,
                role="export_output",
            ),
        ),
        errors=(
            ProviderError(
                provider_code="PDF_ENGINE_MISSING",
                indbase_code=IndbaseProviderErrorCode.PROVIDER_DEPENDENCY_MISSING,
                message="PDF engine is not installed.",
            ),
        ),
    )

    assert package.status == EvidencePackageStatus.PARTIAL
    assert package.normalized_markdown is not None
    assert package.normalized_markdown.trust_level == ArtifactTrustLevel.DERIVED_CANDIDATE
    assert package.derived_outputs[0].trust_level == ArtifactTrustLevel.DERIVED_OUTPUT
    assert package.errors[0].primary_error_code == "provider_dependency_missing"
