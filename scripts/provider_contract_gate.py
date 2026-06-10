"""Provider contract release gate."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    commands = [
        [sys.executable, "-m", "compileall", "-q", "src", "tests/fakes", "tests/test_provider_contracts.py"],
        [sys.executable, "-m", "pytest", "tests/test_provider_contracts.py", "-q"],
    ]
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
    print("PROVIDER_CONTRACT_GATE: ok")


if __name__ == "__main__":
    main()
