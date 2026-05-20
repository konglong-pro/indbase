"""Vault path resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from indbase_core.ids import date_parts_from_doc_id

SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
WINDOWS_RESERVED_BASENAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
MAX_SLUG_LENGTH = 80


@dataclass(frozen=True)
class VaultPaths:
    root: Path

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def sources(self) -> Path:
        return self.root / "sources"

    @property
    def notes_atomic(self) -> Path:
        return self.root / "notes" / "atomic"

    @property
    def outputs_translations(self) -> Path:
        return self.root / "outputs" / "translations"

    @property
    def outputs_exports(self) -> Path:
        return self.root / "outputs" / "exports"

    @property
    def outputs_summaries(self) -> Path:
        return self.root / "outputs" / "summaries"

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    @property
    def indbase_dir(self) -> Path:
        return self.root / ".indbase"

    @property
    def originals(self) -> Path:
        return self.indbase_dir / "originals"

    @property
    def db_path(self) -> Path:
        return self.indbase_dir / "db.sqlite"

    @property
    def indexes(self) -> Path:
        return self.indbase_dir / "indexes"

    @property
    def logs(self) -> Path:
        return self.indbase_dir / "logs"

    @property
    def task_logs(self) -> Path:
        return self.logs / "tasks"

    @property
    def cache(self) -> Path:
        return self.indbase_dir / "cache"

    @property
    def artifacts(self) -> Path:
        return self.indbase_dir / "artifacts"

    @property
    def swallow_cache(self) -> Path:
        return self.cache / "swallow"

    @property
    def transition_cache(self) -> Path:
        return self.cache / "transition"

    @property
    def transition_runtime(self) -> Path:
        return self.indbase_dir / "runtime" / "transition"

    @property
    def converter_run_cache(self) -> Path:
        return self.cache / "converter_runs"

    @property
    def config_dir(self) -> Path:
        return self.indbase_dir / "config"

    @property
    def config_path(self) -> Path:
        return self.config_dir / "config.toml"

    def required_directories(self) -> tuple[Path, ...]:
        return (
            self.inbox,
            self.sources,
            self.notes_atomic,
            self.outputs_translations,
            self.outputs_exports,
            self.outputs_summaries,
            self.transition_cache,
            self.transition_runtime,
            self.assets,
            self.originals,
            self.indexes,
            self.logs,
            self.task_logs,
            self.cache,
            self.artifacts,
            self.swallow_cache,
            self.converter_run_cache,
            self.config_dir,
        )

    def ensure_layout(self) -> None:
        for directory in self.required_directories():
            directory.mkdir(parents=True, exist_ok=True)

    def source_markdown_path(self, doc_id: str, filename_slug: str, sequence: int) -> Path:
        year, month, _day = date_parts_from_doc_id(doc_id)
        slug = slugify(filename_slug)
        filename = f"{slug}__{doc_id}__rev_{sequence:04d}.md"
        return self.sources / year / month / filename

    def original_dir(self, doc_id: str) -> Path:
        year, month, _day = date_parts_from_doc_id(doc_id)
        return self.originals / year / month / doc_id

    def original_path(self, doc_id: str, extension: str) -> Path:
        clean_extension = extension.lower().lstrip(".") or "bin"
        return self.original_dir(doc_id) / f"original.{clean_extension}"

    def converter_candidate_path(self, converter_run_id: str) -> Path:
        return self.converter_run_cache / f"{converter_run_id}.md"

    def artifact_dir(self, doc_id: str, converter_run_id: str) -> Path:
        return self.artifacts / doc_id / converter_run_id

    def output_run_export_dir(self, output_run_id: str) -> Path:
        return self.outputs_exports / output_run_id

    def output_run_evidence_dir(self, output_run_id: str) -> Path:
        return self.artifacts / "output_runs" / output_run_id

    def relative_to_vault(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


def vault_paths(root: Path | str) -> VaultPaths:
    return VaultPaths(root=Path(root).resolve(strict=False))


def normalize_source_uri(path: Path | str, base_dir: Path | str | None = None) -> str:
    source = Path(path)
    if not source.is_absolute() and base_dir is not None:
        source = Path(base_dir) / source
    return source.resolve(strict=False).as_posix()


def slugify(value: str, fallback: str = "untitled", *, max_length: int = MAX_SLUG_LENGTH) -> str:
    if max_length < 1:
        raise ValueError("max_length must be >= 1")
    slug = SLUG_PATTERN.sub("-", value.strip().lower()).strip("-")
    if not slug:
        slug = SLUG_PATTERN.sub("-", fallback.strip().lower()).strip("-") or "untitled"
    slug = slug[:max_length].strip("-") or "untitled"
    if slug in WINDOWS_RESERVED_BASENAMES:
        suffix = "-file"
        slug = f"{slug[: max_length - len(suffix)].strip('-')}{suffix}"
    return slug
