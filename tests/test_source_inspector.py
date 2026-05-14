from pathlib import Path

import pytest

from indbase_core.source_inspector import (
    SourceInspectionError,
    classify_extension,
    hash_file,
    inspect_source,
    scan_sources,
)


def test_classify_extension_tiers() -> None:
    assert classify_extension("md") == "tier1"
    assert classify_extension(".json") == "tier1"
    assert classify_extension("docx") == "tier2"
    assert classify_extension("pdf") == "tier2"
    assert classify_extension("png") == "unsupported"


def test_inspect_source_records_metadata_and_hash(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    content = b"# Title\nBody\n"
    source.write_bytes(content)

    inspection = inspect_source(source)

    assert inspection.path == source
    assert inspection.source_uri == str(source)
    assert inspection.normalized_source_uri.endswith("/note.md")
    assert inspection.original_filename == "note.md"
    assert inspection.original_ext == "md"
    assert inspection.mime_type in {"text/markdown", "text/plain", None}
    assert inspection.size_bytes == len(content)
    assert inspection.source_hash == hash_file(source)
    assert inspection.tier == "tier1"
    assert inspection.is_supported is True


def test_scan_sources_respects_recursive_flag(tmp_path: Path) -> None:
    (tmp_path / "root.txt").write_text("root", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "child.txt").write_text("child", encoding="utf-8")

    shallow = scan_sources(tmp_path, recursive=False)
    recursive = scan_sources(tmp_path, recursive=True)

    assert [item.original_filename for item in shallow] == ["root.txt"]
    assert [item.original_filename for item in recursive] == ["child.txt", "root.txt"]


def test_inspect_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceInspectionError):
        inspect_source(tmp_path / "missing.md")
