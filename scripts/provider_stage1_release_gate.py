"""Aggregate v0.3.5 Stage 1 provider reliability / packaging gate."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    commands = [
        [sys.executable, "scripts/provider_fake_release_gate.py"],
        [sys.executable, "scripts/provider_installed_wheel_smoke.py"],
        [sys.executable, "scripts/provider_transition_distribution_smoke.py"],
        [sys.executable, "scripts/provider_consoler_contract_smoke.py"],
    ]
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
    print("PROVIDER_STAGE1_RELEASE_GATE: ok")


if __name__ == "__main__":
    main()
