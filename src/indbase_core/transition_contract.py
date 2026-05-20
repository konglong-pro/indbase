"""indbase-owned transition bridge contract (v1)."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

CONTRACT_VERSION = "1"


@dataclass(frozen=True)
class ProtectedSpanSpec:
    start: int
    end: int
    kind: str

    def to_dict(self) -> dict[str, object]:
        return {"start": self.start, "end": self.end, "kind": self.kind}


@dataclass(frozen=True)
class BridgeTargetSpec:
    format: str

    def to_dict(self) -> dict[str, object]:
        return {"format": self.format}


@dataclass(frozen=True)
class BridgeRequest:
    contract_version: str
    mode: str
    input_kind: str
    markdown: str
    protected_spans: tuple[ProtectedSpanSpec, ...]
    targets: tuple[BridgeTargetSpec, ...]
    config_hash: str
    cache_dir: str

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "mode": self.mode,
            "input_kind": self.input_kind,
            "markdown": self.markdown,
            "protected_spans": [span.to_dict() for span in self.protected_spans],
            "targets": [target.to_dict() for target in self.targets],
            "config_hash": self.config_hash,
            "cache_dir": self.cache_dir,
        }


@dataclass(frozen=True)
class BridgeTargetResult:
    format: str
    status: str
    path: str | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BridgeTargetResult:
        return cls(
            format=str(payload.get("format") or ""),
            status=str(payload.get("status") or "failed"),
            path=str(payload["path"]) if payload.get("path") else None,
            error=str(payload["error"]) if payload.get("error") else None,
        )


@dataclass(frozen=True)
class BridgeEvidencePaths:
    manifest_path: str | None = None
    trace_path: str | None = None
    report_path: str | None = None
    config_path: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> BridgeEvidencePaths:
        data = payload or {}
        return cls(
            manifest_path=str(data["manifest_path"]) if data.get("manifest_path") else None,
            trace_path=str(data["trace_path"]) if data.get("trace_path") else None,
            report_path=str(data["report_path"]) if data.get("report_path") else None,
            config_path=str(data["config_path"]) if data.get("config_path") else None,
        )


@dataclass(frozen=True)
class BridgeResponse:
    contract_version: str
    status: str
    normalized_markdown: str
    normalized_hash: str
    targets: tuple[BridgeTargetResult, ...] = ()
    evidence_paths: BridgeEvidencePaths = field(default_factory=BridgeEvidencePaths)
    errors: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BridgeResponse:
        if str(payload.get("contract_version")) != CONTRACT_VERSION:
            raise ValueError("unsupported bridge contract_version")
        targets = tuple(
            BridgeTargetResult.from_dict(item)
            for item in payload.get("targets", [])
            if isinstance(item, dict)
        )
        errors = tuple(str(item) for item in payload.get("errors", []) if str(item).strip())
        return cls(
            contract_version=str(payload.get("contract_version") or ""),
            status=str(payload.get("status") or "failed"),
            normalized_markdown=str(payload.get("normalized_markdown") or ""),
            normalized_hash=str(payload.get("normalized_hash") or ""),
            targets=targets,
            evidence_paths=BridgeEvidencePaths.from_dict(
                payload.get("evidence_paths") if isinstance(payload.get("evidence_paths"), dict) else None
            ),
            errors=errors,
        )


def dumps_request(request: BridgeRequest) -> str:
    return json.dumps(request.to_dict(), ensure_ascii=False)


def loads_response(payload: str) -> BridgeResponse:
    return BridgeResponse.from_dict(json.loads(payload))
