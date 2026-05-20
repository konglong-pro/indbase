"""Manual acceptance: v0.3.1 taxonomy CLI + doctor on a fresh vault."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from typer.testing import CliRunner

from gate_common import TRUSTED_NEEDLE, configure_v02_vault, install_deterministic_swallow_stub
from indbase_cli.main import app
from indbase_core.db import connect
from indbase_core.doctor import run_doctor
from indbase_core.ingest import run_m3_ingest_pipeline
from indbase_core.llm_harness import assert_no_direct_provider_usage
from indbase_core.profile import build_document_profile
from indbase_core.vault import init_vault

# Deterministic gate stub satisfies ingest; doctor still errors if swallow is not installed.
_DOCTOR_IGNORE_WITH_GATE_STUB = frozenset({"swallow_unavailable"})


def main() -> None:
    root = Path(".tmp") / f"accept-v031-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    root.mkdir(parents=True, exist_ok=True)
    vault = root / "vault"
    source = root / "note.md"
    source.write_text(
        "# Acceptance Note\n\n"
        "AI research LLM RAG SQLite FTS hybrid search knowledge base.\n"
        f"Needle: {TRUSTED_NEEDLE}\n",
        encoding="utf-8",
    )
    install_deterministic_swallow_stub()
    init_vault(vault, category_template="academic")
    configure_v02_vault(vault, swallow_ingest=True, transition_output=False, min_markdown_chars=80)
    result = run_m3_ingest_pipeline(vault, source)
    if result.written_revisions < 1:
        raise RuntimeError(f"ingest failed: {result}")

    with connect(vault / ".indbase" / "db.sqlite") as connection:
        doc_id = connection.execute("SELECT doc_id FROM documents").fetchone()["doc_id"]
        build_document_profile(connection, doc_id)

    runner = CliRunner()
    commands = [
        ["profile", "show", doc_id, "--vault", str(vault), "--json"],
        ["taxonomy", "analyze", doc_id, "--vault", str(vault), "--json"],
        ["classify", "suggest", doc_id, "--vault", str(vault), "--min-confidence", "0.5", "--json"],
        ["taxonomy", "audit", "--vault", str(vault), "--json"],
        ["tag", "add", "accept-tag", "--type", "topic", "--vault", str(vault)],
        ["doctor", "--vault", str(vault), "--json"],
    ]
    failures: list[str] = []
    for command in commands:
        completed = runner.invoke(app, command)
        label = " ".join(command[:3])
        allow_warning_exit = command[0] == "doctor"
        if completed.exit_code != 0 and not allow_warning_exit:
            failures.append(f"{label} exit={completed.exit_code} output={completed.output[:500]}")
        else:
            print(f"OK {label}")

    violations = assert_no_direct_provider_usage()
    if violations:
        failures.append(f"direct provider usage: {violations}")

    doctor = run_doctor(vault)
    hard = [
        finding
        for finding in doctor.findings
        if finding.severity in {"error", "critical"}
        and finding.code not in _DOCTOR_IGNORE_WITH_GATE_STUB
    ]
    if hard:
        failures.append(f"doctor hard findings: {[(f.code, f.message) for f in hard]}")

    if failures:
        for item in failures:
            print(f"FAIL {item}", file=sys.stderr)
        raise SystemExit(1)

    print("V031_ACCEPTANCE_E2E=passed")


if __name__ == "__main__":
    main()
