"""Capability provider contracts consumed by indbase core."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import re
from typing import Any, Mapping, Protocol

from indbase_core.artifacts.evidence import ArtifactRef

CAPABILITY_CONTRACT_VERSION = "indbase.provider.v1"

_LOGICAL_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$")
_CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+){2,}$")


class ProviderProfile(StrEnum):
    LOCAL_CORE = "local_core"
    LOCAL_SDK = "local_sdk"
    CLI = "cli"
    NODE_BRIDGE = "node_bridge"
    HTTP = "http"
    QUEUE = "queue"
    MCP = "mcp"


class EvidencePackageStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvidenceStatus(StrEnum):
    PENDING = "pending"
    COPIED = "copied"
    MISSING_REQUIRED = "missing_required"
    INVALID = "invalid"
    COPY_FAILED = "copy_failed"


class IndbaseProviderErrorCode(StrEnum):
    PROVIDER_DEPENDENCY_MISSING = "provider_dependency_missing"
    PROVIDER_INPUT_UNSUPPORTED = "provider_input_unsupported"
    PROVIDER_INPUT_TOO_LARGE = "provider_input_too_large"
    PROVIDER_QUALITY_REJECTED = "provider_quality_rejected"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_CANCELLED = "provider_cancelled"
    PROVIDER_PARTIAL_SUCCESS = "provider_partial_success"
    PROVIDER_TRANSPORT_FAILED = "provider_transport_failed"
    PROVIDER_CONFIG_ERROR = "provider_config_error"
    PROVIDER_OUTPUT_WRITE_FAILED = "provider_output_write_failed"
    PROVIDER_UNKNOWN_ERROR = "provider_unknown_error"


@dataclass(frozen=True)
class CapabilityManifest:
    provider_id: str
    provider_package: str
    provider_version: str
    capability_id: str
    capability_contract_version: str
    profiles: tuple[ProviderProfile | str, ...]
    description: str | None = None
    required: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_logical_id(self.provider_id, field_name="provider_id")
        _validate_capability_id(self.capability_id)
        if not self.provider_package.strip():
            raise ValueError("provider_package is required")
        if not self.provider_version.strip():
            raise ValueError("provider_version is required")
        if not self.capability_contract_version.strip():
            raise ValueError("capability_contract_version is required")
        if not self.profiles:
            raise ValueError("at least one provider profile is required")
        object.__setattr__(self, "profiles", tuple(ProviderProfile(str(item)) for item in self.profiles))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def contract_compatible(self) -> bool:
        return self.capability_contract_version == CAPABILITY_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider_id": self.provider_id,
            "provider_package": self.provider_package,
            "provider_version": self.provider_version,
            "capability_id": self.capability_id,
            "capability_contract_version": self.capability_contract_version,
            "profiles": [str(profile) for profile in self.profiles],
            "required": self.required,
        }
        if self.description:
            payload["description"] = self.description
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class ProviderWarning:
    code: str
    message: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("provider warning code is required")
        if not self.message.strip():
            raise ValueError("provider warning message is required")
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class ProviderError:
    provider_code: str | None
    indbase_code: IndbaseProviderErrorCode | str
    message: str
    payload_ref: ArtifactRef | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.provider_code is not None and not self.provider_code.strip():
            raise ValueError("provider_code must be non-empty when supplied")
        if not self.message.strip():
            raise ValueError("provider error message is required")
        object.__setattr__(self, "indbase_code", IndbaseProviderErrorCode(str(self.indbase_code)))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def primary_error_code(self) -> str:
        return str(self.indbase_code)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "indbase_code": str(self.indbase_code),
            "message": self.message,
        }
        if self.provider_code:
            payload["provider_code"] = self.provider_code
        if self.payload_ref:
            payload["payload_ref"] = self.payload_ref.to_dict()
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class InputRef:
    kind: str
    path: str | None = None
    uri: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("input kind is required")
        if not (self.path or self.uri or self.sha256):
            raise ValueError("input ref needs path, uri, or sha256")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("input size_bytes must be >= 0")
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind}
        for key in ("path", "uri", "sha256", "size_bytes"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class SourceBinding:
    doc_id: str
    revision_id: str
    content_sha256: str
    current: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.doc_id.strip():
            raise ValueError("doc_id is required")
        if not self.revision_id.strip():
            raise ValueError("revision_id is required")
        if not self.content_sha256.strip():
            raise ValueError("content_sha256 is required")
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "doc_id": self.doc_id,
            "revision_id": self.revision_id,
            "content_sha256": self.content_sha256,
            "current": self.current,
        }
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True)
class IngestEvidencePackage:
    provider_id: str
    provider_version: str
    capability_id: str
    transport_profile: ProviderProfile | str
    operation_id: str
    status: EvidencePackageStatus | str
    input_ref: InputRef
    provider_job_id: str | None = None
    raw_ref: ArtifactRef | None = None
    candidate_markdown: ArtifactRef | None = None
    ingest_document: ArtifactRef | None = None
    manifest: ArtifactRef | None = None
    trace: ArtifactRef | None = None
    intermediate_artifacts: tuple[ArtifactRef, ...] = ()
    warnings: tuple[ProviderWarning, ...] = ()
    errors: tuple[ProviderError, ...] = ()
    content_hash: str | None = None
    quality: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_common_package(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            capability_id=self.capability_id,
            operation_id=self.operation_id,
        )
        object.__setattr__(self, "transport_profile", ProviderProfile(str(self.transport_profile)))
        object.__setattr__(self, "status", EvidencePackageStatus(str(self.status)))
        object.__setattr__(self, "intermediate_artifacts", tuple(self.intermediate_artifacts))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "errors", tuple(self.errors))
        object.__setattr__(self, "quality", dict(self.quality))

    @property
    def evidence_refs(self) -> tuple[ArtifactRef, ...]:
        refs = [self.raw_ref, self.candidate_markdown, self.ingest_document, self.manifest, self.trace]
        refs.extend(self.intermediate_artifacts)
        return tuple(ref for ref in refs if ref is not None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "capability_id": self.capability_id,
            "transport_profile": str(self.transport_profile),
            "operation_id": self.operation_id,
            "provider_job_id": self.provider_job_id,
            "status": str(self.status),
            "input_ref": self.input_ref.to_dict(),
            "raw_ref": self.raw_ref.to_dict() if self.raw_ref else None,
            "candidate_markdown": self.candidate_markdown.to_dict() if self.candidate_markdown else None,
            "ingest_document": self.ingest_document.to_dict() if self.ingest_document else None,
            "manifest": self.manifest.to_dict() if self.manifest else None,
            "trace": self.trace.to_dict() if self.trace else None,
            "intermediate_artifacts": [item.to_dict() for item in self.intermediate_artifacts],
            "warnings": [item.to_dict() for item in self.warnings],
            "errors": [item.to_dict() for item in self.errors],
            "content_hash": self.content_hash,
            "quality": dict(self.quality),
        }


@dataclass(frozen=True)
class OutputEvidencePackage:
    provider_id: str
    provider_version: str
    capability_id: str
    transport_profile: ProviderProfile | str
    operation_id: str
    status: EvidencePackageStatus | str
    source_binding: SourceBinding
    input_sha256: str
    provider_job_id: str | None = None
    normalized_markdown: ArtifactRef | None = None
    derived_outputs: tuple[ArtifactRef, ...] = ()
    diff: ArtifactRef | None = None
    manifest: ArtifactRef | None = None
    trace: ArtifactRef | None = None
    reports: tuple[ArtifactRef, ...] = ()
    warnings: tuple[ProviderWarning, ...] = ()
    errors: tuple[ProviderError, ...] = ()

    def __post_init__(self) -> None:
        _validate_common_package(
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            capability_id=self.capability_id,
            operation_id=self.operation_id,
        )
        if not self.input_sha256.strip():
            raise ValueError("input_sha256 is required")
        object.__setattr__(self, "transport_profile", ProviderProfile(str(self.transport_profile)))
        object.__setattr__(self, "status", EvidencePackageStatus(str(self.status)))
        object.__setattr__(self, "derived_outputs", tuple(self.derived_outputs))
        object.__setattr__(self, "reports", tuple(self.reports))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "errors", tuple(self.errors))

    @property
    def evidence_refs(self) -> tuple[ArtifactRef, ...]:
        refs = [self.normalized_markdown, self.diff, self.manifest, self.trace]
        refs.extend(self.derived_outputs)
        refs.extend(self.reports)
        return tuple(ref for ref in refs if ref is not None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "capability_id": self.capability_id,
            "transport_profile": str(self.transport_profile),
            "operation_id": self.operation_id,
            "provider_job_id": self.provider_job_id,
            "status": str(self.status),
            "source_binding": self.source_binding.to_dict(),
            "input_sha256": self.input_sha256,
            "normalized_markdown": self.normalized_markdown.to_dict() if self.normalized_markdown else None,
            "derived_outputs": [item.to_dict() for item in self.derived_outputs],
            "diff": self.diff.to_dict() if self.diff else None,
            "manifest": self.manifest.to_dict() if self.manifest else None,
            "trace": self.trace.to_dict() if self.trace else None,
            "reports": [item.to_dict() for item in self.reports],
            "warnings": [item.to_dict() for item in self.warnings],
            "errors": [item.to_dict() for item in self.errors],
        }


@dataclass(frozen=True)
class IngestFileRequest:
    input_ref: InputRef
    binding_key: str = "indbase.ingest.file"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizeMarkdownRequest:
    source_binding: SourceBinding
    markdown: str
    replace_current: bool = False
    binding_key: str = "indbase.output.normalize"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExportMarkdownRequest:
    source_binding: SourceBinding
    markdown: str
    targets: tuple[str, ...]
    binding_key: str = "indbase.output.export"
    metadata: Mapping[str, Any] = field(default_factory=dict)


class IngestProvider(Protocol):
    provider_id: str
    provider_version: str

    def capabilities(self) -> list[CapabilityManifest]:
        ...

    async def ingest_file(
        self,
        request: IngestFileRequest,
        *,
        operation_id: str,
        cancel_token: object | None = None,
    ) -> IngestEvidencePackage:
        ...


class MarkdownOutputProvider(Protocol):
    provider_id: str
    provider_version: str

    def capabilities(self) -> list[CapabilityManifest]:
        ...

    async def normalize(
        self,
        request: NormalizeMarkdownRequest,
        *,
        operation_id: str,
    ) -> OutputEvidencePackage:
        ...

    async def export(
        self,
        request: ExportMarkdownRequest,
        *,
        operation_id: str,
    ) -> OutputEvidencePackage:
        ...


def _validate_common_package(
    *,
    provider_id: str,
    provider_version: str,
    capability_id: str,
    operation_id: str,
) -> None:
    _validate_logical_id(provider_id, field_name="provider_id")
    _validate_capability_id(capability_id)
    if not provider_version.strip():
        raise ValueError("provider_version is required")
    if not operation_id.strip():
        raise ValueError("operation_id is required")


def _validate_logical_id(value: str, *, field_name: str) -> None:
    if not _LOGICAL_ID_RE.match(value):
        raise ValueError(f"{field_name} must be a stable logical id")


def _validate_capability_id(value: str) -> None:
    if not _CAPABILITY_ID_RE.match(value):
        raise ValueError("capability_id must be globally namespaced")
