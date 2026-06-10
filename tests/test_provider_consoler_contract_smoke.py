from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "provider_consoler_contract_smoke", SCRIPTS / "provider_consoler_contract_smoke.py"
)
assert SPEC is not None
assert SPEC.loader is not None
provider_consoler_contract_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provider_consoler_contract_smoke)


def _clear_consoler_pypi_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CONSOLER_PYPI_SIMPLE_URL",
        "CONSOLER_PYPI_REPOSITORY_URL",
        "CONSOLER_PYPI_USERNAME",
        "CONSOLER_PYPI_PASSWORD",
        "CONSOLER_SDK_SOURCE_PATH",
        "PIP_EXTRA_INDEX_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def _write_sdk_pyproject(path: Path, *, name: str = "consoler-agent-sdk", version: str = "0.1.0") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(
        f"""
[project]
name = "{name}"
version = "{version}"
""".lstrip(),
        encoding="utf-8",
    )


def test_resolve_source_path_accepts_relative_sdk_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_consoler_pypi_env(monkeypatch)
    monkeypatch.setattr(provider_consoler_contract_smoke, "ROOT", tmp_path)
    relative = Path("consoler") / "sdks" / "python"
    sdk_path = tmp_path / relative
    _write_sdk_pyproject(sdk_path)
    monkeypatch.setenv("CONSOLER_SDK_SOURCE_PATH", str(relative))

    assert provider_consoler_contract_smoke._resolve_source_path() == sdk_path.resolve()


def test_resolve_source_path_rejects_wrong_sdk_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_consoler_pypi_env(monkeypatch)
    sdk_path = tmp_path / "consoler" / "sdks" / "python"
    _write_sdk_pyproject(sdk_path, version="9.9.9")
    monkeypatch.setenv("CONSOLER_SDK_SOURCE_PATH", str(sdk_path))

    with pytest.raises(SystemExit, match="must be version 0.1.0"):
        provider_consoler_contract_smoke._resolve_source_path()


def test_resolve_package_index_uses_simple_url_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_consoler_pypi_env(monkeypatch)
    monkeypatch.setenv("CONSOLER_PYPI_SIMPLE_URL", "https://packages.example.com/pypi/internal/simple/")

    assert (
        provider_consoler_contract_smoke._resolve_package_index()
        == "https://packages.example.com/pypi/internal/simple/"
    )


def test_resolve_package_index_uses_legacy_repository_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_consoler_pypi_env(monkeypatch)
    monkeypatch.setenv("CONSOLER_PYPI_REPOSITORY_URL", "https://packages.example.com/pypi/internal/simple/")

    assert (
        provider_consoler_contract_smoke._resolve_package_index()
        == "https://packages.example.com/pypi/internal/simple/"
    )


def test_resolve_package_index_adds_separate_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_consoler_pypi_env(monkeypatch)
    monkeypatch.setenv("CONSOLER_PYPI_SIMPLE_URL", "https://packages.example.com/pypi/internal/simple/")
    monkeypatch.setenv("CONSOLER_PYPI_USERNAME", "__token__")
    monkeypatch.setenv("CONSOLER_PYPI_PASSWORD", "tok/en@1")

    assert (
        provider_consoler_contract_smoke._resolve_package_index()
        == "https://__token__:tok%2Fen%401@packages.example.com/pypi/internal/simple/"
    )


def test_resolve_package_index_rejects_credentials_in_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_consoler_pypi_env(monkeypatch)
    monkeypatch.setenv("CONSOLER_PYPI_SIMPLE_URL", "https://__token__:bad@packages.example.com/simple/")

    with pytest.raises(SystemExit, match="must not include credentials"):
        provider_consoler_contract_smoke._resolve_package_index()


def test_configure_pip_index_strips_source_secret_names() -> None:
    env = {
        "CONSOLER_PYPI_SIMPLE_URL": "https://packages.example.com/simple/",
        "CONSOLER_PYPI_USERNAME": "__token__",
        "CONSOLER_PYPI_PASSWORD": "secret",
        "CONSOLER_SDK_SOURCE_PATH": "../consoler/sdks/python",
        "PIP_EXTRA_INDEX_URL": "https://old.example.com/simple/",
    }

    provider_consoler_contract_smoke._configure_pip_index(env, "https://packages.example.com/simple/")

    assert env == {"PIP_EXTRA_INDEX_URL": "https://packages.example.com/simple/"}
