"""Swallow-backed conversion adapter.

This module deliberately maps swallow output into indbase-owned boundary
objects. It does not let swallow write canonical indbase revisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
import json
import re

from indbase_core.config import SwallowIngestConfig


@dataclass(frozen=True)
class ArtifactManifest:
    required: tuple[str, ...] = ()
    diagnostic: tuple[str, ...] = ()
    missing_allowed: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "required": list(self.required),
            "diagnostic": list(self.diagnostic),
            "missing_allowed": list(self.missing_allowed),
        }


@dataclass(frozen=True)
class SwallowProvenance:
    swallow_job_id: str
    swallow_raw_id: str | None
    swallow_document_id: str | None
    swallow_version: str
    primary_worker: str | None
    worker_version: str | None
    worker_chain: tuple[str, ...]
    trace_path: str | None
    manifest_path: str | None
    ingest_document_path: str | None
    requires_network: bool = False
    requires_external_service: bool = False
    access_context: str = "local_file"

    def to_dict(self) -> dict[str, object]:
        return {
            "swallow_job_id": self.swallow_job_id,
            "swallow_raw_id": self.swallow_raw_id,
            "swallow_document_id": self.swallow_document_id,
            "swallow_version": self.swallow_version,
            "primary_worker": self.primary_worker,
            "worker_version": self.worker_version,
            "worker_chain": list(self.worker_chain),
            "trace_path": self.trace_path,
            "manifest_path": self.manifest_path,
            "ingest_document_path": self.ingest_document_path,
            "requires_network": self.requires_network,
            "requires_external_service": self.requires_external_service,
            "access_context": self.access_context,
        }


@dataclass(frozen=True)
class ConversionCandidate:
    title: str | None
    markdown_body: str
    status: str
    quality_score: float | None
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    language: str | None = None
    provenance: SwallowProvenance | None = None
    artifact_manifest: ArtifactManifest = field(default_factory=ArtifactManifest)
    source_locators: tuple[dict[str, object], ...] = ()
    source_snapshot_path: str | None = None
    access_context: str = "local_file"
    privacy_flags: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ArchiveLogicalDocumentCandidate:
    logical_source_id: str
    title: str
    markdown_body: str
    source_locators: tuple[dict[str, object], ...]
    metadata: dict[str, object] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ArchiveExpansionCandidate:
    archive_type: str
    aggregate_candidate: ConversionCandidate
    logical_documents: tuple[ArchiveLogicalDocumentCandidate, ...]


class SwallowAdapterUnavailable(RuntimeError):
    """Raised when swallow is enabled but unavailable."""


class SwallowConversionError(RuntimeError):
    """Raised when swallow returns an unusable conversion result."""


class SwallowIngestAdapter:
    def __init__(
        self,
        *,
        vault_path: Path | str,
        config: SwallowIngestConfig,
    ) -> None:
        self.vault_path = Path(vault_path)
        self.config = config
        self.store_root = _resolve_vault_relative(self.vault_path, config.store_dir)
        self.config_path = _resolve_vault_relative(self.vault_path, config.config_path)

    def convert_file(self, path: Path | str) -> ConversionCandidate:
        runner = self._runner()
        result = runner.ingest_file(Path(path))
        return self._candidate_from_run_result(result)

    def convert_url(self, url: str) -> ConversionCandidate:
        runner = self._runner()
        prepared = runner.prepare_url_job(url)
        result = runner.run_prepared_job(
            prepared,
            plan_override=["playwright_worker", "quality_checker", "markdown_normalizer"],
        )
        return self._candidate_from_run_result(
            result,
            access_context="public_url",
            requires_network=True,
            source_locators=(
                {
                    "kind": "web_snapshot",
                    "url": url,
                    "artifact": _source_snapshot_path(result),
                    "selector": None,
                },
            ),
        )

    def expand_archive(self, path: Path | str) -> ArchiveExpansionCandidate:
        archive_path = Path(path)
        worker_module = _import_swallow_module("swallow.workers.export_archive_worker")
        archive_type = str(worker_module.detect_archive_type(archive_path))
        if archive_type != "chatgpt_export":
            raise SwallowConversionError(f"Unsupported archive type: {archive_type}")

        runner = self._runner()
        prepared = runner.prepare_archive_job(archive_path)
        result = runner.run_prepared_job(
            prepared,
            plan_override=["export_archive_worker", "quality_checker", "markdown_normalizer"],
        )
        aggregate = self._candidate_from_run_result(
            result,
            access_context="local_archive",
        )

        payload = worker_module.read_chatgpt_conversations_payload(archive_path)
        conversations = worker_module.parse_chatgpt_export(payload)
        raw_conversations = _raw_chatgpt_conversations(payload)
        conversation_artifact = _artifact_path_by_type(result, "chatgpt_conversations_json")
        normalizer_module = _import_swallow_module("swallow.normalizers.conversation_to_markdown")
        logical_documents = tuple(
            _archive_logical_document(
                conversation=conversation,
                raw_conversation=raw_conversations[index - 1] if index <= len(raw_conversations) else None,
                index=index,
                archive_type=archive_type,
                conversation_artifact=conversation_artifact,
                normalize_role=normalizer_module.normalize_role,
                stringify_content=normalizer_module.stringify_content,
            )
            for index, conversation in enumerate(conversations, start=1)
        )
        return ArchiveExpansionCandidate(
            archive_type=archive_type,
            aggregate_candidate=aggregate,
            logical_documents=logical_documents,
        )

    def _runner(self):
        runner_module = _import_swallow_module("swallow.core.runner")
        config_module = _import_swallow_module("swallow.core.config")
        runner_class = getattr(runner_module, "IngestRunner")
        load_config = getattr(config_module, "load_config")

        swallow_config = load_config(self.config_path) if self.config_path.is_file() else None
        return runner_class(store_root=self.store_root, config=swallow_config)

    def _candidate_from_run_result(
        self,
        result: Any,
        *,
        access_context: str = "local_file",
        requires_network: bool = False,
        requires_external_service: bool = False,
        source_locators: tuple[dict[str, object], ...] = (),
    ) -> ConversionCandidate:
        document = result.document
        content = document.content
        provenance = document.provenance
        quality = document.quality
        manifest_path = getattr(result, "manifest_path", None)
        ingest_document_path = getattr(result, "ingest_document_path", None)
        trace_path = getattr(result, "trace_path", None) or getattr(provenance, "trace_path", None)
        status = _manifest_status(self.store_root, manifest_path) or "success"
        markdown = str(getattr(content, "markdown", "") or "")
        if not markdown.strip():
            raise SwallowConversionError("Swallow returned empty Markdown.")

        required_artifacts = _existing_result_paths(
            self.store_root,
            (
                trace_path,
                manifest_path,
                ingest_document_path,
                *_artifact_paths(result, self.store_root),
            ),
        )
        artifact_manifest = ArtifactManifest(required=required_artifacts)
        primary_worker = getattr(provenance, "primary_worker", None)
        worker_version = getattr(provenance, "worker_version", None)
        worker_chain = tuple(str(item) for item in (getattr(provenance, "worker_chain", None) or ()))

        resolved_source_locators = source_locators or _source_locators(result, self.store_root)

        return ConversionCandidate(
            title=getattr(content, "title", None),
            markdown_body=markdown,
            status=status,
            quality_score=_optional_float(getattr(quality, "score", None)),
            warnings=tuple(str(item) for item in (getattr(quality, "warnings", None) or ())),
            errors=(),
            language=getattr(content, "language", None),
            provenance=SwallowProvenance(
                swallow_job_id=str(result.job.id),
                swallow_raw_id=str(getattr(document, "raw_id", "")) or None,
                swallow_document_id=str(getattr(document, "id", "")) or None,
                swallow_version=_swallow_version(),
                primary_worker=str(primary_worker) if primary_worker is not None else None,
                worker_version=str(worker_version) if worker_version is not None else None,
                worker_chain=worker_chain,
                trace_path=str(trace_path) if trace_path is not None else None,
                manifest_path=str(manifest_path) if manifest_path is not None else None,
                ingest_document_path=str(ingest_document_path) if ingest_document_path is not None else None,
                requires_network=requires_network,
                requires_external_service=requires_external_service,
                access_context=access_context,
            ),
            artifact_manifest=artifact_manifest,
            source_locators=resolved_source_locators,
            source_snapshot_path=_source_snapshot_path(result),
            access_context=access_context,
        )


def candidate_to_quality_signals(candidate: ConversionCandidate) -> dict[str, object]:
    return {
        "converter": "swallow",
        "status": candidate.status,
        "quality_score": candidate.quality_score,
        "warnings": list(candidate.warnings),
        "errors": list(candidate.errors),
        "provenance": candidate.provenance.to_dict() if candidate.provenance else None,
        "artifact_manifest": candidate.artifact_manifest.to_dict(),
        "source_locators": list(candidate.source_locators),
        "access_context": candidate.access_context,
        "privacy_flags": candidate.privacy_flags,
    }


def artifact_manifest_json(candidate: ConversionCandidate, archived_required: tuple[str, ...]) -> str:
    payload = candidate.artifact_manifest.to_dict()
    payload["archived_required"] = list(archived_required)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def swallow_package_version() -> str:
    return _swallow_version()


def _import_swallow_module(name: str):
    try:
        return import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name and (exc.name == "swallow" or exc.name.startswith("swallow.")):
            raise SwallowAdapterUnavailable(
                "swallow is enabled for ingest conversion but is not installed. "
                "Install indbase with the swallow extra or disable features.swallow_ingest."
            ) from exc
        raise


def _resolve_vault_relative(vault_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return vault_path / path


def _existing_result_paths(root: Path, paths: tuple[str | None, ...]) -> tuple[str, ...]:
    existing: list[str] = []
    for path in paths:
        if not path:
            continue
        candidate = root / path
        if candidate.is_file():
            existing.append(path)
    return tuple(existing)


def _artifact_paths(result: Any, root: Path) -> tuple[str, ...]:
    artifacts = getattr(result.document.provenance, "artifacts", None) or ()
    paths: list[str] = []
    for artifact in artifacts:
        path = _artifact_path(result, artifact)
        if path is None:
            continue
        artifact_type = str(_read_artifact_value(artifact, "type") or "")
        if artifact_type == "page_image_dir":
            paths.extend(_artifact_directory_files(root, path))
        elif (root / path).is_file() or Path(path).is_absolute():
            paths.append(path)
    return tuple(paths)


def _artifact_directory_files(root: Path, artifact_path: str) -> tuple[str, ...]:
    path = Path(artifact_path)
    absolute = path if path.is_absolute() else root / path
    if not absolute.is_dir():
        return ()
    files: list[str] = []
    for candidate in sorted(child for child in absolute.rglob("*") if child.is_file()):
        files.append(candidate.as_posix() if path.is_absolute() else candidate.relative_to(root).as_posix())
    return tuple(files)


def _source_snapshot_path(result: Any) -> str | None:
    artifacts = getattr(result.document.provenance, "artifacts", None) or ()
    for artifact in artifacts:
        artifact_type = str(_read_artifact_value(artifact, "type") or "")
        if artifact_type in {"rendered_html", "html"}:
            return _artifact_path(result, artifact)
    return None


def _source_locators(result: Any, root: Path) -> tuple[dict[str, object], ...]:
    primary_worker = str(getattr(result.document.provenance, "primary_worker", "") or "")
    if primary_worker == "paddleocr_worker":
        return _ocr_source_locators(result, root)
    if primary_worker == "faster_whisper_worker":
        return _asr_source_locators(result, root)
    return ()


def _ocr_source_locators(result: Any, root: Path) -> tuple[dict[str, object], ...]:
    ocr_json = _artifact_path_by_type(result, "ocr_json")
    if ocr_json is None:
        return ()
    pages = _read_json_artifact(root, ocr_json)
    if not isinstance(pages, list):
        return ({"kind": "ocr", "artifact": ocr_json},)

    locators: list[dict[str, object]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_number = page.get("page_number")
        locator: dict[str, object] = {
            "kind": "ocr_page",
            "artifact": ocr_json,
        }
        if isinstance(page_number, int):
            locator["page"] = page_number
        image_artifact = _ocr_page_image_artifact(result, root, page)
        if image_artifact is not None:
            locator["page_image_artifact"] = image_artifact
        locators.append(locator)
    if locators:
        return tuple(locators)
    return ({"kind": "ocr", "artifact": ocr_json},)


def _asr_source_locators(result: Any, root: Path) -> tuple[dict[str, object], ...]:
    transcript_json = _artifact_path_by_type(result, "transcript_json")
    if transcript_json is None:
        return ()
    normalized_audio = _artifact_path_by_type(result, "normalized_audio")
    transcript = _read_json_artifact(root, transcript_json)
    if not isinstance(transcript, dict):
        return _asr_document_locator(transcript_json, normalized_audio)
    segments = transcript.get("segments")
    if not isinstance(segments, list) or not segments:
        return _asr_document_locator(transcript_json, normalized_audio, transcript=transcript)

    locators: list[dict[str, object]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        locator: dict[str, object] = {
            "kind": "asr_segment",
            "artifact": transcript_json,
        }
        start = _optional_float(segment.get("start"))
        end = _optional_float(segment.get("end"))
        if start is not None:
            locator["start_seconds"] = start
        if end is not None:
            locator["end_seconds"] = end
        if normalized_audio is not None:
            locator["normalized_audio_artifact"] = normalized_audio
        locators.append(locator)
    if locators:
        return tuple(locators)
    return _asr_document_locator(transcript_json, normalized_audio, transcript=transcript)


def _asr_document_locator(
    transcript_json: str,
    normalized_audio: str | None,
    *,
    transcript: dict[str, object] | None = None,
) -> tuple[dict[str, object], ...]:
    locator: dict[str, object] = {
        "kind": "asr_transcript",
        "artifact": transcript_json,
    }
    if normalized_audio is not None:
        locator["normalized_audio_artifact"] = normalized_audio
    if transcript is not None:
        duration = _optional_float(transcript.get("duration_seconds"))
        if duration is not None:
            locator["duration_seconds"] = duration
        language = transcript.get("language")
        if isinstance(language, str) and language:
            locator["language"] = language
    return (locator,)


def _artifact_path_by_type(result: Any, artifact_type: str) -> str | None:
    artifacts = getattr(result.document.provenance, "artifacts", None) or ()
    for artifact in artifacts:
        if _read_artifact_value(artifact, "type") == artifact_type:
            return _artifact_path(result, artifact)
    return None


def _read_json_artifact(root: Path, artifact_path: str) -> object:
    path = Path(artifact_path)
    if not path.is_absolute():
        path = root / artifact_path
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _ocr_page_image_artifact(result: Any, root: Path, page: dict[str, object]) -> str | None:
    page_image_dir = _artifact_path_by_type(result, "page_image_dir")
    if page_image_dir is None:
        return None
    page_number = page.get("page_number")
    if not isinstance(page_number, int):
        return None
    candidate = f"{page_image_dir}/page_{page_number:04d}.png"
    path = root / candidate
    if path.is_file():
        return candidate
    return None


def _artifact_path(result: Any, artifact: Any) -> str | None:
    path = _read_artifact_value(artifact, "path")
    if not isinstance(path, str) or not path:
        return None
    if Path(path).is_absolute():
        return path
    return f"{result.job.job_dir}/{path}"


def _read_artifact_value(artifact: Any, key: str) -> Any:
    if isinstance(artifact, dict):
        return artifact.get(key)
    return getattr(artifact, key, None)


def _manifest_status(root: Path, manifest_path: str | None) -> str | None:
    if not manifest_path:
        return None
    path = root / manifest_path
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status
    return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _swallow_version() -> str:
    try:
        return version("swallow")
    except PackageNotFoundError:
        return "0+unknown"


def _raw_chatgpt_conversations(payload: Any) -> list[Any]:
    conversations = payload.get("conversations") if isinstance(payload, dict) else payload
    if isinstance(conversations, list):
        return conversations
    return []


def _archive_logical_document(
    *,
    conversation: dict[str, Any],
    raw_conversation: Any,
    index: int,
    archive_type: str,
    conversation_artifact: str | None,
    normalize_role: Any,
    stringify_content: Any,
) -> ArchiveLogicalDocumentCandidate:
    title = str(conversation.get("title") or f"Conversation {index}").strip() or f"Conversation {index}"
    logical_source_id = _archive_logical_source_id(raw_conversation, index)
    messages = conversation.get("messages")
    if not isinstance(messages, list):
        messages = []
    chunks = [f"# {title}"]
    meta_lines = [
        f"archive_type: {archive_type}",
        f"logical_source_id: {logical_source_id}",
        f"conversation_index: {index}",
    ]
    created_at = conversation.get("created_at")
    updated_at = conversation.get("updated_at")
    if created_at is not None:
        meta_lines.append(f"created_at: {created_at}")
    if updated_at is not None:
        meta_lines.append(f"updated_at: {updated_at}")
    chunks.append("<!-- archive:conversation\n" + "\n".join(meta_lines) + "\n-->")
    message_count = 0
    for message_index, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            continue
        role = normalize_role(message.get("role"))
        content = stringify_content(message.get("content")).strip()
        if not content:
            continue
        message_count += 1
        chunks.append(f"## {message_index}. {role}")
        chunks.append(content)

    locator: dict[str, object] = {
        "kind": "archive_member",
        "archive_type": archive_type,
        "member_path": "conversations.json",
        "logical_source_id": logical_source_id,
        "conversation_index": index,
    }
    if conversation_artifact:
        locator["artifact"] = conversation_artifact

    warnings: list[str] = []
    if message_count == 0:
        warnings.append("archive_conversation_no_messages")

    return ArchiveLogicalDocumentCandidate(
        logical_source_id=logical_source_id,
        title=title,
        markdown_body="\n\n".join(chunks).strip() + "\n",
        source_locators=(locator,),
        metadata={
            "archive_type": archive_type,
            "conversation_index": index,
            "created_at": created_at,
            "updated_at": updated_at,
            "message_count": message_count,
        },
        warnings=tuple(warnings),
    )


def _archive_logical_source_id(raw_conversation: Any, index: int) -> str:
    if isinstance(raw_conversation, dict):
        for key in ("id", "conversation_id"):
            value = raw_conversation.get(key)
            if isinstance(value, str) and value.strip():
                return _safe_logical_source_id(value)
    return f"conversation-{index:04d}"


def _safe_logical_source_id(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._:-]+", "-", value.strip()).strip("-._:")
    return normalized[:120] or "conversation"
