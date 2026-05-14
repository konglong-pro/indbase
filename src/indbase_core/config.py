"""Vault configuration loading and writing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib

CONFIG_SCHEMA_VERSION = "indbase.config.v1"


@dataclass(frozen=True)
class IngestConfig:
    recursive: bool = True
    preserve_original: bool = True
    max_file_size_mb: int = 200
    detect_duplicates: bool = True
    tier1_extensions: tuple[str, ...] = ("md", "txt", "html", "csv", "json")
    tier2_extensions: tuple[str, ...] = ("docx", "xlsx", "pptx", "pdf")


@dataclass(frozen=True)
class SearchConfig:
    default_scope: str = "sources_current"
    top_k: int = 20
    persist_search_results: bool = False
    log_queries: bool = True
    cjk_strategy: str = "substring_fallback"


@dataclass(frozen=True)
class TuiConfig:
    mode: str = "lite"


@dataclass(frozen=True)
class FeatureFlags:
    ocr: bool = False
    embedding: bool = False
    ask: bool = False
    translation: bool = False
    candidate_cards: bool = False
    auto_classification: bool = False


@dataclass(frozen=True)
class IndbaseConfig:
    vault_path: Path
    schema_version: str = CONFIG_SCHEMA_VERSION
    default_language: str = "zh"
    ingest: IngestConfig = field(default_factory=IngestConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    tui: TuiConfig = field(default_factory=TuiConfig)
    features: FeatureFlags = field(default_factory=FeatureFlags)


class ConfigError(ValueError):
    """Raised when a vault config cannot be loaded safely."""


def default_config(vault_path: Path | str) -> IndbaseConfig:
    return IndbaseConfig(vault_path=Path(vault_path))


def load_config(path: Path | str) -> IndbaseConfig:
    config_path = Path(path)
    with config_path.open("rb") as config_file:
        raw = tomllib.load(config_file)

    schema_version = raw.get("schema_version")
    if schema_version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"Unsupported config schema_version {schema_version!r}; "
            f"expected {CONFIG_SCHEMA_VERSION!r}."
        )

    vault_path = raw.get("vault_path")
    if not isinstance(vault_path, str) or not vault_path:
        raise ConfigError("Config must define a non-empty vault_path string.")

    ingest = raw.get("ingest", {})
    search = raw.get("search", {})
    tui = raw.get("tui", {})
    features = raw.get("features", {})

    return IndbaseConfig(
        schema_version=schema_version,
        vault_path=Path(vault_path),
        default_language=str(raw.get("default_language", "zh")),
        ingest=IngestConfig(
            recursive=bool(ingest.get("recursive", True)),
            preserve_original=bool(ingest.get("preserve_original", True)),
            max_file_size_mb=int(ingest.get("max_file_size_mb", 200)),
            detect_duplicates=bool(ingest.get("detect_duplicates", True)),
            tier1_extensions=tuple(ingest.get("tier1_extensions", IngestConfig().tier1_extensions)),
            tier2_extensions=tuple(ingest.get("tier2_extensions", IngestConfig().tier2_extensions)),
        ),
        search=SearchConfig(
            default_scope=str(search.get("default_scope", "sources_current")),
            top_k=int(search.get("top_k", 20)),
            persist_search_results=bool(search.get("persist_search_results", False)),
            log_queries=bool(search.get("log_queries", True)),
            cjk_strategy=str(search.get("cjk_strategy", "substring_fallback")),
        ),
        tui=TuiConfig(mode=str(tui.get("mode", "lite"))),
        features=FeatureFlags(
            ocr=bool(features.get("ocr", False)),
            embedding=bool(features.get("embedding", False)),
            ask=bool(features.get("ask", False)),
            translation=bool(features.get("translation", False)),
            candidate_cards=bool(features.get("candidate_cards", False)),
            auto_classification=bool(features.get("auto_classification", False)),
        ),
    )


def save_config(config: IndbaseConfig, path: Path | str) -> None:
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_to_toml(config), encoding="utf-8")


def _to_toml(config: IndbaseConfig) -> str:
    return "\n".join(
        [
            f'schema_version = "{_toml_escape(config.schema_version)}"',
            f'vault_path = "{_toml_escape(config.vault_path.as_posix())}"',
            f'default_language = "{_toml_escape(config.default_language)}"',
            "",
            "[ingest]",
            _bool_line("recursive", config.ingest.recursive),
            _bool_line("preserve_original", config.ingest.preserve_original),
            f"max_file_size_mb = {config.ingest.max_file_size_mb}",
            _bool_line("detect_duplicates", config.ingest.detect_duplicates),
            _list_line("tier1_extensions", config.ingest.tier1_extensions),
            _list_line("tier2_extensions", config.ingest.tier2_extensions),
            "",
            "[search]",
            f'default_scope = "{_toml_escape(config.search.default_scope)}"',
            f"top_k = {config.search.top_k}",
            _bool_line("persist_search_results", config.search.persist_search_results),
            _bool_line("log_queries", config.search.log_queries),
            f'cjk_strategy = "{_toml_escape(config.search.cjk_strategy)}"',
            "",
            "[tui]",
            f'mode = "{_toml_escape(config.tui.mode)}"',
            "",
            "[features]",
            _bool_line("ocr", config.features.ocr),
            _bool_line("embedding", config.features.embedding),
            _bool_line("ask", config.features.ask),
            _bool_line("translation", config.features.translation),
            _bool_line("candidate_cards", config.features.candidate_cards),
            _bool_line("auto_classification", config.features.auto_classification),
            "",
        ]
    )


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _bool_line(key: str, value: bool) -> str:
    return f"{key} = {str(value).lower()}"


def _list_line(key: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f'"{_toml_escape(value)}"' for value in values)
    return f"{key} = [{quoted}]"
