"""Installed-wheel consoler SDK contract smoke.

Trusted CI installs the consoler SDK from a checked-out source package path.
Package-index installs remain supported for release environments that publish
`consoler-agent-sdk==0.1.0`, but a private index is not required for this gate.
"""

from __future__ import annotations
from urllib.parse import quote
from urllib.parse import SplitResult
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import tomllib

from provider_smoke_common import ROOT, build_wheel, clean_env, create_venv


EXPECTED_CONSOLER_SDK_PACKAGE = "consoler-agent-sdk"
EXPECTED_CONSOLER_SDK_VERSION = "0.1.0"
EXPECTED_CONSOLER_SDK = f"{EXPECTED_CONSOLER_SDK_PACKAGE}=={EXPECTED_CONSOLER_SDK_VERSION}"
DEFAULT_CONSOLER_SDK_SOURCE = ROOT.parent / "consoler" / "sdks" / "python"


def main() -> None:
    _assert_extra_pin()
    required = os.environ.get("INDBASE_CONSOLER_CONTRACT_REQUIRED") == "1"
    requested = required or os.environ.get("INDBASE_RUN_CONSOLER_CONTRACT") == "1"
    source_path = _resolve_source_path()
    package_index = _resolve_package_index()
    has_pip_index = bool(os.environ.get("PIP_EXTRA_INDEX_URL"))
    if not requested and not source_path and not package_index and not has_pip_index:
        print("PROVIDER_CONSOLER_CONTRACT_SMOKE: skipped (consoler SDK source/package index not configured)")
        return
    if required and not source_path and not package_index and not has_pip_index:
        raise SystemExit("CONSOLER_SDK_SOURCE_PATH or consoler SDK package index is required")

    with tempfile.TemporaryDirectory(prefix="indbase-consoler-smoke-") as raw_tmp:
        tmp = Path(raw_tmp)
        wheel = build_wheel(tmp)
        venv_python = create_venv(tmp / "venv")
        env = clean_env()
        if source_path:
            _strip_consoler_index_env(env)
            command = [str(venv_python), "-m", "pip", "install", str(source_path), str(wheel)]
        else:
            _configure_pip_index(env, package_index)
            command = [str(venv_python), "-m", "pip", "install", f"{wheel}[consoler-agent]"]
        completed = subprocess.run(command, cwd=tmp, env=env, check=False)
        if completed.returncode != 0:
            if required:
                raise SystemExit(completed.returncode)
            print("PROVIDER_CONSOLER_CONTRACT_SMOKE: skipped (consoler SDK install unavailable)")
            return

        script = textwrap.dedent(
            """
            import consoler_agent_sdk
            from importlib.metadata import version
            from consoler_agent_sdk import json_block, markdown_block
            from indbase_agent.artifact_view import ARTIFACT_VIEW_TITLES

            assert version("consoler-agent-sdk") == "0.1.0"
            assert getattr(consoler_agent_sdk, "SUPPORTED_PROTOCOL_VERSION") == "0"
            assert "indbase.provider_run" in ARTIFACT_VIEW_TITLES
            assert "indbase.provider_evidence" in ARTIFACT_VIEW_TITLES
            assert json_block({"uri": "indbase://provider_runs/prun_1"}, title="Smoke")["type"] == "json"
            assert markdown_block("# Smoke", title="Smoke")["type"] == "markdown"
            """
        )
        subprocess.run([str(venv_python), "-c", script], cwd=tmp, env=env, check=True)
    print("PROVIDER_CONSOLER_CONTRACT_SMOKE: ok")


def _assert_extra_pin() -> None:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = payload["project"]["optional-dependencies"]
    pins = extras.get("consoler-agent", [])
    if EXPECTED_CONSOLER_SDK not in pins:
        raise SystemExit(f"consoler-agent extra must include {EXPECTED_CONSOLER_SDK}")


def _resolve_source_path() -> Path | None:
    raw_path = _env_value("CONSOLER_SDK_SOURCE_PATH")
    path = Path(raw_path) if raw_path else DEFAULT_CONSOLER_SDK_SOURCE
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    pyproject = path / "pyproject.toml"
    if not pyproject.is_file():
        if raw_path:
            raise SystemExit("CONSOLER_SDK_SOURCE_PATH must point to consoler/sdks/python")
        return None
    _assert_source_metadata(pyproject)
    return path


def _assert_source_metadata(pyproject: Path) -> None:
    payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = payload.get("project", {})
    if project.get("name") != EXPECTED_CONSOLER_SDK_PACKAGE:
        raise SystemExit(f"consoler SDK source package must be named {EXPECTED_CONSOLER_SDK_PACKAGE}")
    if project.get("version") != EXPECTED_CONSOLER_SDK_VERSION:
        raise SystemExit(f"consoler SDK source package must be version {EXPECTED_CONSOLER_SDK_VERSION}")


def _resolve_package_index() -> str | None:
    simple_url, source_name = _configured_simple_url()
    username = _env_value("CONSOLER_PYPI_USERNAME")
    password = _env_value("CONSOLER_PYPI_PASSWORD")

    if bool(username) != bool(password):
        raise SystemExit("CONSOLER_PYPI_USERNAME and CONSOLER_PYPI_PASSWORD must be set together")
    if not simple_url:
        if username or password:
            raise SystemExit("CONSOLER_PYPI_SIMPLE_URL is required when consoler PyPI credentials are configured")
        return None
    if username and password:
        return _with_basic_auth(simple_url, source_name, username, password)
    _parse_simple_url(simple_url, source_name)
    return simple_url


def _configured_simple_url() -> tuple[str | None, str]:
    for name in ("CONSOLER_PYPI_SIMPLE_URL", "CONSOLER_PYPI_REPOSITORY_URL"):
        value = _env_value(name)
        if value:
            return value, name
    return None, "CONSOLER_PYPI_SIMPLE_URL"


def _env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _with_basic_auth(simple_url: str, source_name: str, username: str, password: str) -> str:
    parsed = _parse_simple_url(simple_url, source_name)
    credentials = f"{quote(username, safe='')}:{quote(password, safe='')}"
    return urlunsplit(
        SplitResult(
            scheme=parsed.scheme,
            netloc=f"{credentials}@{parsed.netloc}",
            path=parsed.path,
            query=parsed.query,
            fragment=parsed.fragment,
        )
    )


def _parse_simple_url(simple_url: str, source_name: str) -> SplitResult:
    parsed = urlsplit(simple_url)
    if not parsed.scheme or not parsed.netloc:
        raise SystemExit(f"{source_name} must be an absolute simple index URL")
    if parsed.username or parsed.password or "@" in parsed.netloc:
        raise SystemExit(
            f"{source_name} must not include credentials; use CONSOLER_PYPI_USERNAME and CONSOLER_PYPI_PASSWORD"
        )
    return parsed


def _configure_pip_index(env: dict[str, str], package_index: str | None) -> None:
    _strip_consoler_index_env(env)
    if package_index:
        env["PIP_EXTRA_INDEX_URL"] = package_index


def _strip_consoler_index_env(env: dict[str, str]) -> None:
    for name in (
        "CONSOLER_PYPI_SIMPLE_URL",
        "CONSOLER_PYPI_REPOSITORY_URL",
        "CONSOLER_PYPI_USERNAME",
        "CONSOLER_PYPI_PASSWORD",
        "CONSOLER_SDK_SOURCE_PATH",
    ):
        env.pop(name, None)


if __name__ == "__main__":
    sys.exit(main())
