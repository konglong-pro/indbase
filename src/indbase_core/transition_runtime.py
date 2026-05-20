"""Explicit per-vault transition runtime install and status."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import json
import shutil
import subprocess

from indbase_core.config import IndbaseConfig, load_config, save_config
from indbase_core.paths import vault_paths
from indbase_core.transition_adapter import TRANSITION_PIN_COMMIT, install_bridge_template
from indbase_core.transition_config import (
    TransitionConfigError,
    config_hash,
    load_transition_config,
    write_default_transition_config,
)


@dataclass(frozen=True)
class RuntimeInstallResult:
    vault_path: Path
    runtime_dir: Path
    transition_commit: str
    previous_run_count: int
    npm_install_ran: bool


@dataclass(frozen=True)
class RuntimeStatus:
    vault_path: Path
    transition_output_enabled: bool
    runtime_dir: Path
    bridge_present: bool
    config_present: bool
    node_modules_present: bool
    node_available: bool
    recorded_commit: str | None
    existing_output_runs: int


class TransitionRuntimeError(RuntimeError):
    """Raised when runtime install or status checks fail."""


def install_runtime(
    vault_path: Path | str,
    *,
    run_npm_install: bool = True,
) -> RuntimeInstallResult:
    paths = vault_paths(vault_path)
    runtime_dir = paths.transition_runtime
    previous_run_count = _count_output_runs(paths.db_path)
    install_bridge_template(runtime_dir)
    config_path = runtime_dir / "transition.config.json"
    write_default_transition_config(config_path)
    load_transition_config(config_path)
    npm_install_ran = False
    if run_npm_install:
        _run_npm_install(runtime_dir)
        npm_install_ran = True
    else:
        (runtime_dir / "node_modules").mkdir(parents=True, exist_ok=True)
    config = load_config(paths.config_path)
    updated = _replace(
        config,
        transition_output=True,
        bridge_path=paths.relative_to_vault(runtime_dir / "transition-bridge.mjs"),
        config_path=paths.relative_to_vault(config_path),
        runtime_dir=paths.relative_to_vault(runtime_dir),
    )
    save_config(updated, paths.config_path)
    return RuntimeInstallResult(
        vault_path=paths.root,
        runtime_dir=runtime_dir,
        transition_commit=TRANSITION_PIN_COMMIT,
        previous_run_count=previous_run_count,
        npm_install_ran=npm_install_ran,
    )


def runtime_status(vault_path: Path | str) -> RuntimeStatus:
    paths = vault_paths(vault_path)
    config = load_config(paths.config_path) if paths.config_path.is_file() else None
    runtime_dir = paths.transition_runtime
    node_modules = runtime_dir / "node_modules"
    recorded_commit = _read_installed_commit(runtime_dir)
    return RuntimeStatus(
        vault_path=paths.root,
        transition_output_enabled=bool(config and config.features.transition_output),
        runtime_dir=runtime_dir,
        bridge_present=(runtime_dir / "transition-bridge.mjs").is_file(),
        config_present=(runtime_dir / "transition.config.json").is_file(),
        node_modules_present=node_modules.is_dir(),
        node_available=shutil.which("node") is not None,
        recorded_commit=recorded_commit,
        existing_output_runs=_count_output_runs(paths.db_path),
    )


def transition_bridge_smoke_available(vault_path: Path | str) -> tuple[bool, str]:
    """Return whether real Node bridge smoke tests can run for this vault."""
    if os.environ.get("INDBASE_TRANSITION_SMOKE") != "1":
        return False, "Set INDBASE_TRANSITION_SMOKE=1 to run real transition bridge smoke tests."
    if shutil.which("node") is None:
        return False, "Node is not available on PATH."
    status = runtime_status(vault_path)
    if not status.bridge_present or not status.config_present:
        return False, "Run `indb output runtime install` in the vault first."
    if not status.node_modules_present:
        return False, "Transition runtime node_modules is missing; run `indb output runtime install`."
    return True, ""


def ensure_runtime_ready(config: IndbaseConfig) -> Path:
    if not config.features.transition_output:
        raise TransitionRuntimeError("transition_output_disabled")
    paths = vault_paths(config.vault_path)
    runtime_dir = paths.transition_runtime
    bridge = paths.root / config.output.bridge_path
    transition_config = paths.root / config.output.config_path
    if not bridge.is_file() or not transition_config.is_file():
        raise TransitionRuntimeError("transition_runtime_missing")
    try:
        load_transition_config(transition_config)
    except TransitionConfigError as exc:
        raise TransitionRuntimeError("transition_config_invalid") from exc
    if not (runtime_dir / "node_modules").is_dir():
        raise TransitionRuntimeError("transition_runtime_missing")
    return runtime_dir


def _run_npm_install(runtime_dir: Path) -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise TransitionRuntimeError("npm is not available on PATH")
    completed = subprocess.run(
        [npm, "install", "--no-audit", "--no-fund"],
        cwd=str(runtime_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise TransitionRuntimeError(stderr or "npm install failed")


def _count_output_runs(db_path: Path) -> int:
    if not db_path.is_file():
        return 0
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        try:
            row = connection.execute("SELECT COUNT(*) AS count FROM output_runs").fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0]) if row else 0
    finally:
        connection.close()


def _read_installed_commit(runtime_dir: Path) -> str | None:
    package_path = runtime_dir / "package.json"
    if not package_path.is_file():
        return None
    payload = json.loads(package_path.read_text(encoding="utf-8"))
    dependency = payload.get("dependencies", {}).get("transition", "")
    if "#" in str(dependency):
        return str(dependency).rsplit("#", 1)[-1]
    return None


def _replace(
    config: IndbaseConfig,
    *,
    transition_output: bool,
    bridge_path: str,
    config_path: str,
    runtime_dir: str,
) -> IndbaseConfig:
    from dataclasses import replace

    return replace(
        config,
        features=replace(config.features, transition_output=transition_output),
        output=replace(
            config.output,
            bridge_path=bridge_path,
            config_path=config_path,
            runtime_dir=runtime_dir,
        ),
    )
