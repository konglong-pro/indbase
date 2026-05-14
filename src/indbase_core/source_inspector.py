"""Source file inspection and tiering for ingest."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import mimetypes
from pathlib import Path

from indbase_core.config import IngestConfig
from indbase_core.paths import normalize_source_uri

HASH_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class SourceInspection:
    path: Path
    source_uri: str
    normalized_source_uri: str
    original_filename: str
    original_ext: str
    mime_type: str | None
    size_bytes: int
    source_hash: str
    tier: str

    @property
    def is_supported(self) -> bool:
        return self.tier in {"tier1", "tier2"}


class SourceInspectionError(ValueError):
    """Raised when a source path cannot be inspected."""


def inspect_source(
    path: Path | str,
    *,
    source_uri: str | None = None,
    base_dir: Path | str | None = None,
    ingest_config: IngestConfig | None = None,
) -> SourceInspection:
    source_path = Path(path)
    if not source_path.is_absolute() and base_dir is not None:
        source_path = Path(base_dir) / source_path
    source_path = source_path.resolve(strict=False)

    if not source_path.exists():
        raise SourceInspectionError(f"Source does not exist: {source_path}")
    if not source_path.is_file():
        raise SourceInspectionError(f"Source is not a file: {source_path}")

    config = ingest_config or IngestConfig()
    extension = source_path.suffix.lower().lstrip(".")
    tier = classify_extension(extension, config)
    mime_type, _encoding = mimetypes.guess_type(source_path.name)
    original_uri = source_uri if source_uri is not None else str(path)

    return SourceInspection(
        path=source_path,
        source_uri=original_uri,
        normalized_source_uri=normalize_source_uri(source_path),
        original_filename=source_path.name,
        original_ext=extension,
        mime_type=mime_type,
        size_bytes=source_path.stat().st_size,
        source_hash=hash_file(source_path),
        tier=tier,
    )


def scan_sources(
    path: Path | str,
    *,
    recursive: bool = False,
    ingest_config: IngestConfig | None = None,
) -> list[SourceInspection]:
    root = Path(path).resolve(strict=False)
    if not root.exists():
        raise SourceInspectionError(f"Source does not exist: {root}")
    if root.is_file():
        return [inspect_source(root, source_uri=str(path), ingest_config=ingest_config)]
    if not root.is_dir():
        raise SourceInspectionError(f"Source is neither file nor directory: {root}")

    pattern = "**/*" if recursive else "*"
    files = sorted(candidate for candidate in root.glob(pattern) if candidate.is_file())
    return [
        inspect_source(candidate, source_uri=str(candidate), ingest_config=ingest_config)
        for candidate in files
    ]


def classify_extension(extension: str, ingest_config: IngestConfig | None = None) -> str:
    config = ingest_config or IngestConfig()
    normalized = extension.lower().lstrip(".")
    if normalized in config.tier1_extensions:
        return "tier1"
    if normalized in config.tier2_extensions:
        return "tier2"
    return "unsupported"


def hash_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while chunk := source.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
