from pathlib import Path

import pytest

from indbase_core.config import ConfigError, default_config, load_config, save_config


def test_config_round_trip(tmp_path: Path) -> None:
    config = default_config(tmp_path / "vault")
    config_path = tmp_path / "vault" / ".indbase" / "config" / "config.toml"

    save_config(config, config_path)
    loaded = load_config(config_path)

    assert loaded.schema_version == "indbase.config.v1"
    assert loaded.vault_path == tmp_path / "vault"
    assert loaded.ingest.tier1_extensions == ("md", "txt", "html", "csv", "json")
    assert loaded.ingest.tier2_extensions == ("docx", "xlsx", "pptx", "pdf")
    assert loaded.ingest.swallow.store_dir == ".indbase/cache/swallow"
    assert loaded.ingest.swallow.auto_current_quality_threshold == 0.75
    assert loaded.search.cjk_strategy == "substring_fallback"
    assert loaded.features.swallow_ingest is False
    assert loaded.features.ask is False


def test_config_loads_swallow_policy(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
schema_version = "indbase.config.v1"
vault_path = "vault"

[ingest.swallow]
store_dir = ".indbase/cache/custom-swallow"
config_path = ".indbase/config/swallow.yaml"
auto_current_quality_threshold = 0.8
review_quality_threshold = 0.5
min_markdown_chars = 20
archive_required_artifacts = false

[features]
swallow_ingest = true
web_ingest = true
login_profile_ingest = true
external_ingest_providers = true
ocr = true
asr = true
""".strip(),
        encoding="utf-8",
    )

    loaded = load_config(config_path)

    assert loaded.features.swallow_ingest is True
    assert loaded.features.web_ingest is True
    assert loaded.features.login_profile_ingest is True
    assert loaded.features.external_ingest_providers is True
    assert loaded.features.ocr is True
    assert loaded.features.asr is True
    assert loaded.ingest.swallow.store_dir == ".indbase/cache/custom-swallow"
    assert loaded.ingest.swallow.config_path == ".indbase/config/swallow.yaml"
    assert loaded.ingest.swallow.auto_current_quality_threshold == 0.8
    assert loaded.ingest.swallow.review_quality_threshold == 0.5
    assert loaded.ingest.swallow.min_markdown_chars == 20
    assert loaded.ingest.swallow.archive_required_artifacts is False


def test_load_config_rejects_unknown_schema(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'schema_version = "future"\nvault_path = "vault"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_path)
