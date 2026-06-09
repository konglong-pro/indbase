"""Provider fake deterministic release gate.

Runs indbase-owned provider contract, provider run, evidence copy, output trust,
and agent/doctor compatibility checks without requiring real swallow workers or
real transition export dependencies.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    commands = [
        [
            sys.executable,
            "-m",
            "compileall",
            "-q",
            "src",
            "tests/fakes",
            "scripts/provider_contract_gate.py",
            "scripts/provider_fake_release_gate.py",
            "scripts/provider_real_smoke.py",
        ],
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_provider_contracts.py",
            "tests/test_provider_runs.py",
            "tests/test_conversion.py",
            "tests/test_output_service.py",
            "tests/test_transition_partial_evidence.py",
            "-q",
        ],
    ]
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
    print("PROVIDER_FAKE_RELEASE_GATE: ok")


if __name__ == "__main__":
    main()
