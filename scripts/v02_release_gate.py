"""Aggregate v0.2 release gate stack (A–E).

Active release standard:
  A pytest + B compileall: daily required
  C deterministic v0.2 vault + doctor negative: release required
  D real-tool smoke: required when env is available; skipped otherwise
  E real-corpus dogfood: required before formal release unless waived
"""

from __future__ import annotations

from datetime import datetime
import json
import os
import sys

from gate_common import ROOT, env_enabled, gate_skip, run_compile_gate, run_pytest_gate, run_subprocess_gate


def main() -> None:
    gate_root = ROOT / ".tmp" / f"v02-release-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    gate_root.mkdir(parents=True)
    layers: list[dict[str, object]] = []

    layers.append(run_pytest_gate())
    layers.append(run_compile_gate())
    layers.append(run_subprocess_gate("v02_deterministic_release_gate.py"))
    layers.append(run_subprocess_gate("doctor_negative_gate.py"))

    if env_enabled("INDBASE_SWALLOW_SMOKE"):
        layers.append(run_subprocess_gate("v02_swallow_smoke_gate.py"))
    else:
        layers.append(
            gate_skip("D", "v02_swallow_smoke", "Set INDBASE_SWALLOW_SMOKE=1 when real swallow is available.")
        )

    if env_enabled("INDBASE_TRANSITION_SMOKE"):
        layers.append(run_subprocess_gate("v02_transition_smoke_gate.py"))
    else:
        layers.append(
            gate_skip(
                "D",
                "v02_transition_smoke",
                "Set INDBASE_TRANSITION_SMOKE=1 when real transition runtime is available.",
            )
        )

    if env_enabled("INDB_REAL_CORPUS"):
        layers.append(run_subprocess_gate("v02_real_corpus_dogfood_gate.py"))
    elif env_enabled("INDB_V02_DOGFOOD_WAIVED"):
        layers.append(
            {
                "status": "waived",
                "layer": "E",
                "name": "v02_real_corpus_dogfood",
                "reason": os.environ.get("INDB_V02_DOGFOOD_WAIVER_REASON", "explicit waiver"),
            }
        )
    else:
        layers.append(
            gate_skip(
                "E",
                "v02_real_corpus_dogfood",
                "Set INDB_REAL_CORPUS for formal dogfood, or INDB_V02_DOGFOOD_WAIVED=1 with INDB_V02_DOGFOOD_WAIVER_REASON.",
            )
        )

    failed = [layer for layer in layers if layer.get("status") not in {"passed", "skipped", "waived"}]
    if failed:
        raise RuntimeError(f"v0.2 release gate failed layers: {failed}")

    summary = {
        "gate": "v02_release",
        "layers": layers,
        "passed": sum(1 for layer in layers if layer.get("status") == "passed"),
        "skipped": sum(1 for layer in layers if layer.get("status") == "skipped"),
        "waived": sum(1 for layer in layers if layer.get("status") == "waived"),
    }
    (gate_root / "layers.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    print("V02_RELEASE_GATE=passed")
    print(f"GATE_ROOT={gate_root}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"V02_RELEASE_GATE=failed: {exc}", file=sys.stderr)
        raise
