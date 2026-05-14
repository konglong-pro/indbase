"""Shared helpers for MVP release close-out gates."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Iterable


ROOT = Path.cwd()
IND_B = ROOT / ".venv" / "Scripts" / "indb.exe"


def run_indb(
    expected: int | set[int],
    *args: str,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    expected_codes = {expected} if isinstance(expected, int) else expected
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [str(IND_B), *args],
        cwd=cwd or ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode not in expected_codes:
        raise RuntimeError(
            f"indb {' '.join(args)} exited {completed.returncode}, "
            f"expected {sorted(expected_codes)}\n{completed.stdout}"
        )
    return completed


def stage_real_corpus(root: Path, *, target_files: int = 85) -> dict[str, object]:
    """Stage a 50-100 file corpus from real repo docs and code text.

    If INDB_REAL_CORPUS is set, the pointed directory is copied as-is and
    should contain supported file types for dogfood. Otherwise, repo-local
    Markdown files are copied directly and code/config files are mirrored as
    .txt so the corpus still uses real project content while exercising the
    supported text ingest path.
    """
    sources = root / "sources"
    sources.mkdir(parents=True)
    external = os.environ.get("INDB_REAL_CORPUS")
    if external:
        external_root = Path(external).expanduser().resolve()
        if not external_root.is_dir():
            raise RuntimeError(f"INDB_REAL_CORPUS is not a directory: {external_root}")
        copied = _copy_supported_tree(external_root, sources)
        return {
            "source_mode": "external",
            "source_root": str(external_root),
            "sources": sources,
            "input_files": copied,
            "direct_files": copied,
            "mirrored_text_files": 0,
        }

    direct_files = [ROOT / "README.md", ROOT / "AGENTS.md"]
    direct_files.extend(sorted((ROOT / "docs" / "planning").glob("*.md")))
    mirrored_files: list[Path] = []
    for folder in ("src", "tests", "scripts"):
        mirrored_files.extend(sorted((ROOT / folder).rglob("*.py")))
    mirrored_files.append(ROOT / "pyproject.toml")

    staged_direct = 0
    staged_mirrored = 0
    staged_total = 0
    for path in direct_files:
        if staged_total >= target_files:
            break
        if path.is_file():
            rel = path.relative_to(ROOT)
            dest = sources / "documents" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            staged_direct += 1
            staged_total += 1

    for path in mirrored_files:
        if staged_total >= target_files:
            break
        if path.is_file():
            rel = path.relative_to(ROOT)
            dest = sources / "mirrored-text" / rel.parent / f"{rel.name}.txt"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
            staged_mirrored += 1
            staged_total += 1

    if staged_total < 50:
        raise RuntimeError(f"repo-local real corpus has too few staged files: {staged_total}")
    return {
        "source_mode": "repo-local",
        "source_root": str(ROOT),
        "sources": sources,
        "input_files": staged_total,
        "direct_files": staged_direct,
        "mirrored_text_files": staged_mirrored,
    }


def directory_size(path: Path) -> int:
    return sum(candidate.stat().st_size for candidate in path.rglob("*") if candidate.is_file())


def parse_first_json_line(output: str) -> dict[str, object]:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            import json

            return json.loads(stripped)
    raise RuntimeError(f"no JSON line found in output\n{output}")


def _copy_supported_tree(source_root: Path, dest_root: Path) -> int:
    supported = {".md", ".txt", ".html", ".csv", ".json", ".docx", ".xlsx", ".pptx", ".pdf", ".png", ".zip"}
    copied = 0
    for path in _sorted_files(source_root):
        if path.suffix.casefold() not in supported:
            continue
        rel = path.relative_to(source_root)
        dest = dest_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        copied += 1
    if copied < 1:
        raise RuntimeError(f"external real corpus had no supported or expected boundary files: {source_root}")
    return copied


def _sorted_files(root: Path) -> Iterable[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())
