"""Run v0.3.1 taxonomy N1 gate stack (n1–n6)."""

from __future__ import annotations

from datetime import datetime
import json
import sys

from gate_common import ROOT, run_subprocess_gate


GATES = (
    "n1_taxonomy_schema_gate.py",
    "n2_profile_feature_gate.py",
    "n3_tag_candidate_manager_gate.py",
    "n4_category_manager_gate.py",
    "n5_taxonomy_janitor_gate.py",
    "n6_llm_harness_suggestion_gate.py",
)


def main() -> None:
    gate_root = ROOT / ".tmp" / f"v031-taxonomy-gate-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    gate_root.mkdir(parents=True, exist_ok=True)
    layers: list[dict[str, object]] = []
    for script in GATES:
        layers.append(run_subprocess_gate(script))
    summary = {"gate": "v031_taxonomy", "layers": layers, "passed": len(layers)}
    (gate_root / "layers.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    print("V031_TAXONOMY_RELEASE_GATE=passed")
    print(f"GATE_ROOT={gate_root}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"V031_TAXONOMY_RELEASE_GATE=failed: {exc}", file=sys.stderr)
        raise
