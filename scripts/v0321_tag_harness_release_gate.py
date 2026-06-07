#!/usr/bin/env python3
"""v0.3.2.1 tag harness hardening release gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from indbase_core.tag_harness_eval import run_tag_harness_eval


def main() -> int:
    summary = run_tag_harness_eval()
    print(json.dumps(summary.to_dict(), indent=2))
    return 0 if summary.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
