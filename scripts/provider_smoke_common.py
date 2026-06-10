"""Shared helpers for provider packaging smoke scripts."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    completed = subprocess.run(command, cwd=cwd, env=env, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def build_wheel(work_dir: Path) -> Path:
    dist = work_dir / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    if uv:
        run([uv, "build", "--wheel", "--out-dir", str(dist)], cwd=ROOT)
    else:
        run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(dist)], cwd=ROOT)
    wheels = sorted(dist.glob("indbase-*.whl"))
    if not wheels:
        raise SystemExit("No indbase wheel was built.")
    return wheels[-1]


def create_venv(path: Path, *, system_site_packages: bool = True) -> Path:
    command = [sys.executable, "-m", "venv"]
    if system_site_packages:
        command.append("--system-site-packages")
    command.append(str(path))
    run(command, cwd=ROOT)
    if os.name == "nt":
        return path / "Scripts" / "python.exe"
    return path / "bin" / "python"


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    return env
