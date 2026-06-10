"""v0.3.5 Engineering Stability Hardening aggregate gate."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    commands = [
        [sys.executable, "scripts/provider_stage1_release_gate.py"],
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_db.py",
            "tests/test_indexer.py",
            "tests/test_doctor.py",
            "tests/test_normalize_replace_regression.py",
            "tests/test_indbase_agent_readonly_views.py",
            "tests/test_provider_runs.py",
            "tests/test_retrieval_regression.py",
            "-q",
        ],
        [sys.executable, "-m", "compileall", "-q", "src", "tests", "scripts"],
        [sys.executable, "scripts/check_docs.py"],
    ]
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
    print("V035_STABILITY_HARDENING_GATE: ok")


if __name__ == "__main__":
    main()
