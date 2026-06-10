"""Real provider smoke gate with explicit environment gating."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    required = os.environ.get("INDBASE_PROVIDER_SMOKE_REQUIRED") == "1"
    checks = {
        "swallow_importable": _module_available("swallow"),
        "node": shutil.which("node") is not None,
    }
    missing = [name for name, ok in checks.items() if not ok]
    if missing and not required:
        print(f"PROVIDER_REAL_SMOKE: skipped missing={','.join(missing)}")
        return
    if missing:
        raise SystemExit(f"PROVIDER_REAL_SMOKE: missing required runtime(s): {', '.join(missing)}")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_swallow_adapter.py", "tests/test_transition_bridge_smoke.py", "-q"],
        cwd=ROOT,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)
    print("PROVIDER_REAL_SMOKE: ok")


def _module_available(name: str) -> bool:
    completed = subprocess.run(
        [sys.executable, "-c", f"import {name}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return completed.returncode == 0


if __name__ == "__main__":
    main()
