"""Transition bridge adapters (fake + Node subprocess)."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
import json
from typing import Callable
import os
from pathlib import Path
import shutil
import subprocess
import hashlib

from indbase_core.conversion import hash_markdown
from indbase_core.protected_spans import ProtectedSpan, apply_spans, validate_protected_spans
from indbase_core.transition_contract import (
    CONTRACT_VERSION,
    BridgeEvidencePaths,
    BridgeRequest,
    BridgeResponse,
    BridgeTargetResult,
    BridgeTargetSpec,
    ProtectedSpanSpec,
    dumps_request,
    loads_response,
)


TRANSITION_PIN_COMMIT = "cf4a726bf9a5c3c9cb300fda38bcb54fac005f23"
FILE_TRANSPORT_THRESHOLD = 1_000_000


class TransitionBridgeError(RuntimeError):
    """Raised when the transition bridge cannot complete."""


@dataclass(frozen=True)
class BridgeRunContext:
    vault_root: Path
    runtime_dir: Path
    bridge_path: Path
    cache_dir: Path
    timeout_seconds: int


@dataclass(frozen=True)
class BridgeInvocation:
    response: BridgeResponse | None
    job_dir: Path
    error: str | None = None

    @property
    def has_evidence(self) -> bool:
        if self.response is None:
            return False
        evidence = self.response.evidence_paths
        return bool(
            evidence.manifest_path
            or evidence.trace_path
            or evidence.report_path
            or self.job_dir.joinpath("transition_manifest.json").is_file()
        )


def run_fake_bridge(request: BridgeRequest) -> BridgeResponse:
    original = request.markdown
    protected = tuple(
        ProtectedSpan(span.start, span.end, span.kind) for span in request.protected_spans
    )
    updated = _normalize_outside_protected(original, protected)
    errors = validate_protected_spans(original, updated, protected)
    if errors:
        return BridgeResponse(
            contract_version=CONTRACT_VERSION,
            status="failed",
            normalized_markdown="",
            normalized_hash="",
            errors=tuple(errors),
        )
    target_results = tuple(
        BridgeTargetResult(
            format=target.format,
            status="skipped",
            error="render target not requested in fake bridge",
        )
        for target in request.targets
    )
    status = "succeeded"
    if request.targets:
        status = "partial"
    evidence_paths = _write_fake_evidence(Path(request.cache_dir))
    return BridgeResponse(
        contract_version=CONTRACT_VERSION,
        status=status,
        normalized_markdown=updated,
        normalized_hash=hash_markdown(updated),
        targets=target_results,
        evidence_paths=evidence_paths,
    )


def evidence_paths_from_job_dir(job_dir: Path) -> BridgeEvidencePaths:
    manifest = job_dir / "transition_manifest.json"
    trace = job_dir / "transition_trace.jsonl"
    report = job_dir / "transition_report.json"
    return BridgeEvidencePaths(
        manifest_path=manifest.as_posix() if manifest.is_file() else None,
        trace_path=trace.as_posix() if trace.is_file() else None,
        report_path=report.as_posix() if report.is_file() else None,
    )


def _write_fake_evidence(cache_dir: Path) -> BridgeEvidencePaths:
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest = cache_dir / "transition_manifest.json"
    trace = cache_dir / "transition_trace.jsonl"
    report = cache_dir / "transition_report.json"
    manifest.write_text(json.dumps({"bridge": "fake"}, indent=2) + "\n", encoding="utf-8")
    trace.write_text('{"event":"fake_bridge"}\n', encoding="utf-8")
    report.write_text(json.dumps({"status": "ok"}, indent=2) + "\n", encoding="utf-8")
    return BridgeEvidencePaths(
        manifest_path=manifest.as_posix(),
        trace_path=trace.as_posix(),
        report_path=report.as_posix(),
    )


def invoke_transition_bridge(
    request: BridgeRequest,
    context: BridgeRunContext,
    *,
    runner: Callable[[BridgeRequest], BridgeResponse] | None = None,
) -> BridgeInvocation:
    job_dir = Path(request.cache_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        if runner is not None:
            response = runner(request)
        else:
            response = run_node_bridge(request, context)
        return BridgeInvocation(response=response, job_dir=job_dir)
    except TransitionBridgeError as exc:
        response = load_bridge_response_from_job_dir(job_dir)
        return BridgeInvocation(response=response, job_dir=job_dir, error=str(exc))


def load_bridge_response_from_job_dir(job_dir: Path) -> BridgeResponse | None:
    response_path = job_dir / "response.json"
    if response_path.is_file():
        payload = response_path.read_text(encoding="utf-8").strip()
        if payload:
            try:
                return loads_response(payload)
            except ValueError:
                pass
    evidence_paths = evidence_paths_from_job_dir(job_dir)
    if evidence_paths.manifest_path or evidence_paths.trace_path or evidence_paths.report_path:
        return BridgeResponse(
            contract_version=CONTRACT_VERSION,
            status="failed",
            normalized_markdown="",
            normalized_hash="",
            evidence_paths=evidence_paths,
            errors=("bridge_failed",),
        )
    return None


def run_fake_bridge_failed_with_evidence(request: BridgeRequest) -> BridgeResponse:
    """Test helper: failed status but leaves evidence files in cache_dir."""
    job_dir = Path(request.cache_dir)
    evidence_paths = _write_fake_evidence(job_dir)
    return BridgeResponse(
        contract_version=CONTRACT_VERSION,
        status="failed",
        normalized_markdown="",
        normalized_hash="",
        evidence_paths=evidence_paths,
        errors=("simulated_bridge_failure",),
    )


def run_node_bridge(request: BridgeRequest, context: BridgeRunContext) -> BridgeResponse:
    if not context.bridge_path.is_file():
        raise TransitionBridgeError(f"transition bridge not found: {context.bridge_path}")
    job_dir = Path(request.cache_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    request_path = job_dir / "request.json"
    response_path = job_dir / "response.json"
    request_path.write_text(dumps_request(request), encoding="utf-8")
    command = ["node", str(context.bridge_path), "--request", str(request_path), "--response", str(response_path)]
    try:
        completed = subprocess.run(
            command,
            cwd=str(context.runtime_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=context.timeout_seconds,
            check=False,
            env=_runtime_env(context),
        )
    except subprocess.TimeoutExpired as exc:
        raise TransitionBridgeError("transition_bridge_timeout") from exc
    if response_path.is_file():
        payload = response_path.read_text(encoding="utf-8").strip()
        if payload:
            try:
                response = loads_response(payload)
                if completed.returncode != 0 and response.status != "failed":
                    stderr = (completed.stderr or "").strip()
                    raise TransitionBridgeError(stderr or "transition_bridge_failed")
                return response
            except ValueError as exc:
                if completed.returncode == 0:
                    raise TransitionBridgeError("transition bridge returned invalid JSON") from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise TransitionBridgeError(stderr or "transition_bridge_failed")
    raise TransitionBridgeError("transition bridge did not write response file")


def build_request(
    *,
    mode: str,
    input_kind: str,
    markdown: str,
    protected_spans: tuple[ProtectedSpan, ...],
    targets: tuple[str, ...],
    config_hash: str,
    cache_dir: Path,
) -> BridgeRequest:
    return BridgeRequest(
        contract_version=CONTRACT_VERSION,
        mode=mode,
        input_kind=input_kind,
        markdown=markdown,
        protected_spans=tuple(
            ProtectedSpanSpec(start=span.start, end=span.end, kind=span.kind) for span in protected_spans
        ),
        targets=tuple(BridgeTargetSpec(format=target) for target in targets),
        config_hash=config_hash,
        cache_dir=cache_dir.as_posix(),
    )


def install_bridge_template(runtime_dir: Path) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    bridge_dest = runtime_dir / "transition-bridge.mjs"
    package_dest = runtime_dir / "package.json"
    with resources.as_file(resources.files("indbase_core.transition_templates") / "transition-bridge.mjs") as bridge_src:
        shutil.copyfile(bridge_src, bridge_dest)
    with resources.as_file(resources.files("indbase_core.transition_templates") / "package.json") as package_src:
        shutil.copyfile(package_src, package_dest)
    return bridge_dest


def _normalize_outside_protected(markdown: str, spans: tuple[ProtectedSpan, ...]) -> str:
    if not spans:
        return markdown
    parts: list[str] = []
    cursor = 0
    for span in spans:
        middle = markdown[cursor : span.start]
        middle = middle.replace("  \n", "\n")
        while "\n\n\n" in middle:
            middle = middle.replace("\n\n\n", "\n\n")
        parts.append(middle)
        parts.append(markdown[span.start : span.end])
        cursor = span.end
    tail = markdown[cursor:]
    while "\n\n\n" in tail:
        tail = tail.replace("\n\n\n", "\n\n")
    parts.append(tail)
    return "".join(parts)


def _runtime_env(context: BridgeRunContext) -> dict[str, str]:
    env = os.environ.copy()
    env["INDBASE_VAULT_ROOT"] = str(context.vault_root)
    env["INDBASE_TRANSITION_CACHE"] = str(context.cache_dir)
    return env
