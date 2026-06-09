"""Check indbase documentation lifecycle constraints."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def line_count(path: Path) -> int:
    return len(read_text(path).splitlines())


def strip_fragment(path_text: str) -> str:
    return path_text.split("#", 1)[0]


def manifest_paths(text: str) -> set[str]:
    path_re = re.compile(r"\b(?:docs|scripts|tests|src|\.github)/[A-Za-z0-9_./#-]+")
    paths: set[str] = set()
    for line in text.replace("\\", "/").splitlines():
        for match in path_re.finditer(line):
            prefix = line[: match.start()]
            if ":/" in prefix:
                continue
            cleaned = strip_fragment(match.group(0)).rstrip(".,;")
            if cleaned:
                paths.add(cleaned)
    return paths


def parse_current_manifest(text: str) -> dict[str, dict[str, str]]:
    areas: dict[str, dict[str, str]] = {}
    in_current = False
    current_area: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line == "current:":
            in_current = True
            continue
        if in_current and re.match(r"^[a-z_]+:", line):
            break
        if not in_current:
            continue

        area_match = re.match(r"^  ([A-Za-z0-9_-]+):$", line)
        if area_match:
            current_area = area_match.group(1)
            areas[current_area] = {}
            continue

        key_match = re.match(r"^    ([A-Za-z0-9_-]+):\s*(.+?)\s*$", line)
        if current_area and key_match:
            areas[current_area][key_match.group(1)] = key_match.group(2).strip("'\"")

    return areas


def check_entry_sizes(errors: list[str]) -> None:
    budgets = {
        "AGENTS.md": 250,
        "CONTEXT.md": 150,
        "docs/testing.md": 300,
    }
    for relative, maximum in budgets.items():
        path = ROOT / relative
        if not path.exists():
            fail(errors, f"{relative} is missing")
            continue
        count = line_count(path)
        if count > maximum:
            fail(errors, f"{relative} has {count} lines; expected <= {maximum}")


def check_manifest(errors: list[str]) -> None:
    manifest = ROOT / "docs" / "phase-manifest.yaml"
    if not manifest.exists():
        fail(errors, "docs/phase-manifest.yaml is missing")
        return

    text = read_text(manifest)
    areas = parse_current_manifest(text)
    active_areas = {area: values for area, values in areas.items() if values.get("status") == "active"}
    if len(active_areas) > 1:
        fail(
            errors,
            f"expected at most one active current area in phase manifest; found {len(active_areas)}",
        )
    if not areas:
        fail(errors, "phase manifest has no current areas")
    if areas and not active_areas:
        current_doc = ROOT / "docs" / "active" / "current.md"
        if not current_doc.exists() or "There is no active indbase implementation phase" not in read_text(current_doc):
            fail(errors, "phase manifest has no active area but docs/active/current.md does not say so")

    for area, values in areas.items():
        gate = values.get("release_gate")
        if values.get("status") == "active" and (not gate or gate in {"null", "None"}):
            fail(errors, f"active area {area} has no release_gate")
        elif gate.startswith("scripts/") and not (ROOT / gate).exists():
            fail(errors, f"current area {area} release_gate does not exist: {gate}")

    for relative in sorted(manifest_paths(text)):
        path = ROOT / relative
        if not path.exists():
            fail(errors, f"manifest path does not exist: {relative}")


def check_archive_references(errors: list[str]) -> None:
    agents = ROOT / "AGENTS.md"
    if not agents.exists():
        return
    text = read_text(agents).replace("\\", "/")
    direct_archive_doc = re.compile(
        r"docs/(?:planning|agents|glossary)/archive/[A-Za-z0-9_./-]+\.md"
    )
    matches = sorted(set(direct_archive_doc.findall(text)))
    if matches:
        fail(errors, "AGENTS.md directly references archive docs: " + ", ".join(matches))


def check_superseded_docs(errors: list[str]) -> None:
    folder = ROOT / "docs" / "planning" / "superseded"
    if not folder.exists():
        return
    for path in sorted(folder.glob("*.md")):
        text = read_text(path)
        if "superseded_by:" not in text:
            fail(errors, f"superseded doc lacks superseded_by: {path.relative_to(ROOT)}")


def check_active_plans(errors: list[str]) -> None:
    folder = ROOT / "docs" / "planning" / "active"
    if not folder.exists():
        fail(errors, "docs/planning/active is missing")
        return
    for path in sorted(folder.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        lines = read_text(path).splitlines()
        first = next((line for line in lines if line.strip()), "")
        header = "\n".join(lines[:40])
        if first != "---":
            fail(errors, f"active plan lacks front matter: {path.relative_to(ROOT)}")
        if "doc_type:" not in header or "status:" not in header:
            fail(errors, f"active plan front matter incomplete: {path.relative_to(ROOT)}")


def check_new_archive_metadata(errors: list[str]) -> None:
    planning_archive = ROOT / "docs" / "planning" / "archive"
    agent_archive = ROOT / "docs" / "agents" / "archive" / "indbase"
    archive_files: list[Path] = []
    for pattern in (
        "v0.1/*.plan.md",
        "v0.2/*.md",
        "v0.2/*.plan.md",
        "v0.3.1/*.plan.md",
        "v0.3.2/*.plan.md",
        "v0.3.2.1/*.plan.md",
        "v0.3.2.2/*.plan.md",
        "v0.3.2-retrieval-intelligence/*.plan.md",
        "v0.3.2.3*/*.plan.md",
        "v0.3.3/*.plan.md",
        "v0.3.4/*.plan.md",
    ):
        archive_files.extend(planning_archive.glob(pattern))
    for pattern in (
        "v0.2*.md",
        "v0.3.1*.md",
        "v0.3.2*.md",
        "v0.3.3*.md",
        "v0.3.4*.md",
    ):
        archive_files.extend(agent_archive.glob(pattern))

    for path in sorted(archive_files):
        text = read_text(path)
        lines = text.splitlines()
        first = next((line for line in lines if line.strip()), "")
        header = "\n".join(lines[:30])
        if first != "---":
            fail(errors, f"archive doc lacks front matter: {path.relative_to(ROOT)}")
        if "read_by_default: false" not in header:
            fail(errors, f"archive doc is not marked read_by_default false: {path.relative_to(ROOT)}")
        if "status:" not in header:
            fail(errors, f"archive doc lacks lifecycle status: {path.relative_to(ROOT)}")


def check_testing_archive(errors: list[str]) -> None:
    folder = ROOT / "docs" / "testing" / "archive"
    index = folder / "README.md"
    if not index.exists():
        fail(errors, "docs/testing/archive/README.md is missing")
    closeout_patterns = (
        "v0.1*-closeout.md",
        "v0.2*-closeout.md",
        "v0.3.1*-closeout.md",
        "v0.3.2*-closeout.md",
        "v0.3.3*-closeout.md",
        "v0.3.4*-closeout.md",
    )
    closeouts: list[Path] = []
    for pattern in closeout_patterns:
        closeouts.extend(folder.glob(pattern))
    for path in sorted(set(closeouts)):
        text = read_text(path)
        lines = text.splitlines()
        first = next((line for line in lines if line.strip()), "")
        header = "\n".join(lines[:20])
        if first != "---":
            fail(errors, f"closeout lacks front matter: {path.relative_to(ROOT)}")
        if "read_by_default: false" not in header:
            fail(errors, f"closeout is not marked read_by_default false: {path.relative_to(ROOT)}")
        if len(lines) > 180:
            fail(errors, f"closeout has {len(lines)} lines; expected <= 180: {path.relative_to(ROOT)}")


def main() -> int:
    errors: list[str] = []
    check_entry_sizes(errors)
    check_manifest(errors)
    check_archive_references(errors)
    check_superseded_docs(errors)
    check_active_plans(errors)
    check_new_archive_metadata(errors)
    check_testing_archive(errors)

    if errors:
        for error in errors:
            print(f"DOCS_CHECK_FAIL: {error}", file=sys.stderr)
        return 1

    print("DOCS_CHECK_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
