"""Provider evidence artifact references."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class ArtifactTrustLevel(StrEnum):
    """Trust label for evidence and derived provider artifacts."""

    EVIDENCE = "evidence"
    CONVERSION_CANDIDATE = "conversion_candidate"
    DERIVED_CANDIDATE = "derived_candidate"
    DERIVED_OUTPUT = "derived_output"


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to provider-side and indbase-owned artifact locations."""

    kind: str
    provider_uri: str | None = None
    vault_path: str | None = None
    indbase_uri: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    mime_type: str | None = None
    trust_level: ArtifactTrustLevel | str = ArtifactTrustLevel.EVIDENCE
    role: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("artifact kind is required")
        if not (self.provider_uri or self.vault_path or self.indbase_uri):
            raise ValueError("artifact ref needs provider_uri, vault_path, or indbase_uri")
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("artifact size_bytes must be >= 0")
        object.__setattr__(self, "trust_level", ArtifactTrustLevel(str(self.trust_level)))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def copied_to(self, *, vault_path: str, indbase_uri: str | None = None) -> ArtifactRef:
        """Return a copy that includes indbase-owned durable locations."""

        return ArtifactRef(
            kind=self.kind,
            provider_uri=self.provider_uri,
            vault_path=vault_path,
            indbase_uri=indbase_uri,
            sha256=self.sha256,
            size_bytes=self.size_bytes,
            mime_type=self.mime_type,
            trust_level=self.trust_level,
            role=self.role,
            metadata=self.metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "trust_level": str(self.trust_level),
        }
        for key in (
            "provider_uri",
            "vault_path",
            "indbase_uri",
            "sha256",
            "size_bytes",
            "mime_type",
            "role",
        ):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    def to_external_dict(self) -> dict[str, Any]:
        """Return the bounded external view shape for consoler/CLI artifacts."""

        if not self.indbase_uri:
            raise ValueError("external artifact refs require indbase_uri")
        payload: dict[str, Any] = {
            "kind": self.kind,
            "uri": self.indbase_uri,
            "trust_level": str(self.trust_level),
        }
        if self.sha256:
            payload["sha256"] = self.sha256
        if self.role:
            payload["role"] = self.role
        return payload
