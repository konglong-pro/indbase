"""Vault configuration loading and writing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib

CONFIG_SCHEMA_VERSION = "indbase.config.v1"


@dataclass(frozen=True)
class SwallowIngestConfig:
    store_dir: str = ".indbase/cache/swallow"
    config_path: str = ".indbase/config/swallow.config.yaml"
    auto_current_quality_threshold: float = 0.75
    review_quality_threshold: float = 0.45
    min_markdown_chars: int = 80
    archive_required_artifacts: bool = True
    allow_partial_auto_current: bool = False
    allow_ocr_auto_current: bool = True
    allow_asr_auto_current: bool = True
    allow_web_auto_current: bool = True


@dataclass(frozen=True)
class IngestConfig:
    recursive: bool = True
    preserve_original: bool = True
    max_file_size_mb: int = 200
    detect_duplicates: bool = True
    tier1_extensions: tuple[str, ...] = ("md", "txt", "html", "csv", "json")
    tier2_extensions: tuple[str, ...] = ("docx", "xlsx", "pptx", "pdf")
    swallow: SwallowIngestConfig = field(default_factory=SwallowIngestConfig)


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
class TransitionOutputConfig:
    runtime_dir: str = ".indbase/runtime/transition"
    bridge_path: str = ".indbase/runtime/transition/transition-bridge.mjs"
    config_path: str = ".indbase/runtime/transition/transition.config.json"
    cache_dir: str = ".indbase/cache/transition"
    bridge_timeout_seconds: int = 300
    archive_required_artifacts: bool = True
    protect_source_bindings: bool = True


@dataclass(frozen=True)
class FeatureFlags:
    transition_output: bool = False
    swallow_ingest: bool = False
    web_ingest: bool = False
    login_profile_ingest: bool = False
    external_ingest_providers: bool = False
    ocr: bool = False
    asr: bool = False
    embedding: bool = False
    ask: bool = False
    translation: bool = False
    candidate_cards: bool = False
    auto_classification: bool = False
    category_taxonomy: bool = False


@dataclass(frozen=True)
class IndbaseConfig:
    vault_path: Path
    schema_version: str = CONFIG_SCHEMA_VERSION
    default_language: str = "zh"
    ingest: IngestConfig = field(default_factory=IngestConfig)
    output: TransitionOutputConfig = field(default_factory=TransitionOutputConfig)
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
    output = raw.get("output", {})
    transition = output.get("transition", {}) if isinstance(output, dict) else {}
    swallow = ingest.get("swallow", {}) if isinstance(ingest, dict) else {}

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
            swallow=SwallowIngestConfig(
                store_dir=str(swallow.get("store_dir", SwallowIngestConfig().store_dir)),
                config_path=str(swallow.get("config_path", SwallowIngestConfig().config_path)),
                auto_current_quality_threshold=float(
                    swallow.get(
                        "auto_current_quality_threshold",
                        SwallowIngestConfig().auto_current_quality_threshold,
                    )
                ),
                review_quality_threshold=float(
                    swallow.get("review_quality_threshold", SwallowIngestConfig().review_quality_threshold)
                ),
                min_markdown_chars=int(swallow.get("min_markdown_chars", SwallowIngestConfig().min_markdown_chars)),
                archive_required_artifacts=bool(
                    swallow.get("archive_required_artifacts", SwallowIngestConfig().archive_required_artifacts)
                ),
                allow_partial_auto_current=bool(
                    swallow.get("allow_partial_auto_current", SwallowIngestConfig().allow_partial_auto_current)
                ),
                allow_ocr_auto_current=bool(
                    swallow.get("allow_ocr_auto_current", SwallowIngestConfig().allow_ocr_auto_current)
                ),
                allow_asr_auto_current=bool(
                    swallow.get("allow_asr_auto_current", SwallowIngestConfig().allow_asr_auto_current)
                ),
                allow_web_auto_current=bool(
                    swallow.get("allow_web_auto_current", SwallowIngestConfig().allow_web_auto_current)
                ),
            ),
        ),
        search=SearchConfig(
            default_scope=str(search.get("default_scope", "sources_current")),
            top_k=int(search.get("top_k", 20)),
            persist_search_results=bool(search.get("persist_search_results", False)),
            log_queries=bool(search.get("log_queries", True)),
            cjk_strategy=str(search.get("cjk_strategy", "substring_fallback")),
        ),
        output=TransitionOutputConfig(
            runtime_dir=str(transition.get("runtime_dir", TransitionOutputConfig().runtime_dir)),
            bridge_path=str(transition.get("bridge_path", TransitionOutputConfig().bridge_path)),
            config_path=str(transition.get("config_path", TransitionOutputConfig().config_path)),
            cache_dir=str(transition.get("cache_dir", TransitionOutputConfig().cache_dir)),
            bridge_timeout_seconds=int(
                transition.get("bridge_timeout_seconds", TransitionOutputConfig().bridge_timeout_seconds)
            ),
            archive_required_artifacts=bool(
                transition.get("archive_required_artifacts", TransitionOutputConfig().archive_required_artifacts)
            ),
            protect_source_bindings=bool(
                transition.get("protect_source_bindings", TransitionOutputConfig().protect_source_bindings)
            ),
        ),
        tui=TuiConfig(mode=str(tui.get("mode", "lite"))),
        features=FeatureFlags(
            transition_output=bool(features.get("transition_output", False)),
            swallow_ingest=bool(features.get("swallow_ingest", False)),
            web_ingest=bool(features.get("web_ingest", False)),
            login_profile_ingest=bool(features.get("login_profile_ingest", False)),
            external_ingest_providers=bool(features.get("external_ingest_providers", False)),
            ocr=bool(features.get("ocr", False)),
            asr=bool(features.get("asr", False)),
            embedding=bool(features.get("embedding", False)),
            ask=bool(features.get("ask", False)),
            translation=bool(features.get("translation", False)),
            candidate_cards=bool(features.get("candidate_cards", False)),
            auto_classification=bool(features.get("auto_classification", False)),
            category_taxonomy=bool(features.get("category_taxonomy", False)),
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
            "[ingest.swallow]",
            f'store_dir = "{_toml_escape(config.ingest.swallow.store_dir)}"',
            f'config_path = "{_toml_escape(config.ingest.swallow.config_path)}"',
            f"auto_current_quality_threshold = {config.ingest.swallow.auto_current_quality_threshold}",
            f"review_quality_threshold = {config.ingest.swallow.review_quality_threshold}",
            f"min_markdown_chars = {config.ingest.swallow.min_markdown_chars}",
            _bool_line("archive_required_artifacts", config.ingest.swallow.archive_required_artifacts),
            _bool_line("allow_partial_auto_current", config.ingest.swallow.allow_partial_auto_current),
            _bool_line("allow_ocr_auto_current", config.ingest.swallow.allow_ocr_auto_current),
            _bool_line("allow_asr_auto_current", config.ingest.swallow.allow_asr_auto_current),
            _bool_line("allow_web_auto_current", config.ingest.swallow.allow_web_auto_current),
            "",
            "[output.transition]",
            f'runtime_dir = "{_toml_escape(config.output.runtime_dir)}"',
            f'bridge_path = "{_toml_escape(config.output.bridge_path)}"',
            f'config_path = "{_toml_escape(config.output.config_path)}"',
            f'cache_dir = "{_toml_escape(config.output.cache_dir)}"',
            f"bridge_timeout_seconds = {config.output.bridge_timeout_seconds}",
            _bool_line("archive_required_artifacts", config.output.archive_required_artifacts),
            _bool_line("protect_source_bindings", config.output.protect_source_bindings),
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
            _bool_line("transition_output", config.features.transition_output),
            _bool_line("swallow_ingest", config.features.swallow_ingest),
            _bool_line("web_ingest", config.features.web_ingest),
            _bool_line("login_profile_ingest", config.features.login_profile_ingest),
            _bool_line("external_ingest_providers", config.features.external_ingest_providers),
            _bool_line("ocr", config.features.ocr),
            _bool_line("asr", config.features.asr),
            _bool_line("embedding", config.features.embedding),
            _bool_line("ask", config.features.ask),
            _bool_line("translation", config.features.translation),
            _bool_line("candidate_cards", config.features.candidate_cards),
            _bool_line("auto_classification", config.features.auto_classification),
            _bool_line("category_taxonomy", config.features.category_taxonomy),
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
