from pathlib import Path

import pytest

from indbase_core.transition_config import TransitionConfigError, load_transition_config, validate_transition_config


def test_rejects_forbidden_binding_key() -> None:
    with pytest.raises(TransitionConfigError):
        validate_transition_config({"version": 1, "rewrite_source_bindings": True})


def test_loads_default_config(tmp_path: Path) -> None:
    config_path = tmp_path / "transition.config.json"
    config_path.write_text(
        '{"version":1,"render":{"html":{"enabled":true}},"trace":{"level":"info"}}',
        encoding="utf-8",
    )
    loaded = load_transition_config(config_path)
    assert loaded["version"] == 1
