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
    assert loaded.search.cjk_strategy == "substring_fallback"
    assert loaded.features.ask is False


def test_load_config_rejects_unknown_schema(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'schema_version = "future"\nvault_path = "vault"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_path)
