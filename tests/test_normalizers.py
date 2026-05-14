from pathlib import Path

import pytest

import indbase_core.normalizers as normalizers
from indbase_core.normalizers import normalize_tier1_source


def test_markdown_and_text_normalizers_preserve_body_with_normalized_newline(tmp_path: Path) -> None:
    markdown = tmp_path / "note.md"
    text = tmp_path / "note.txt"
    markdown.write_bytes(b"# Title\r\nBody")
    text.write_bytes("plain text".encode("utf-8"))

    normalized_markdown = normalize_tier1_source(markdown, "md")
    normalized_text = normalize_tier1_source(text, "txt")

    assert normalized_markdown.markdown == "# Title\nBody\n"
    assert normalized_markdown.converter_name == "direct_normalizer"
    assert normalized_text.markdown == "plain text\n"
    assert normalized_text.converter_name == "direct_normalizer"


def test_csv_normalizer_outputs_markdown_table(tmp_path: Path) -> None:
    source = tmp_path / "data.csv"
    source.write_text("name,value\nalpha,1\nbeta,2\n", encoding="utf-8")

    normalized = normalize_tier1_source(source, "csv")

    assert normalized.converter_name == "csv_normalizer"
    assert normalized.markdown == (
        "| name | value |\n"
        "| --- | --- |\n"
        "| alpha | 1 |\n"
        "| beta | 2 |\n"
    )


def test_json_normalizer_outputs_pretty_json_fence(tmp_path: Path) -> None:
    source = tmp_path / "data.json"
    source.write_text('{"b": 2, "a": 1}', encoding="utf-8")

    normalized = normalize_tier1_source(source, "json")

    assert normalized.converter_name == "json_normalizer"
    assert normalized.markdown == '```json\n{\n  "a": 1,\n  "b": 2\n}\n```\n'
    assert normalized.warnings == ()


def test_invalid_json_falls_back_to_raw_text_fence(tmp_path: Path) -> None:
    source = tmp_path / "broken.json"
    source.write_text('{"missing": true', encoding="utf-8")

    normalized = normalize_tier1_source(source, "json")

    assert normalized.converter_name == "json_normalizer"
    assert normalized.markdown == '```text\n{"missing": true\n```\n'
    assert normalized.warnings == ("json_parse_failed_fallback_raw",)


def test_html_normalizer_uses_markitdown_when_available(tmp_path: Path) -> None:
    pytest.importorskip("markitdown")
    source = tmp_path / "page.html"
    source.write_text("<html><body><h1>Hello</h1><p>World</p></body></html>", encoding="utf-8")

    normalized = normalize_tier1_source(source, "html")

    assert normalized.converter_name == "markitdown"
    assert "# Hello" in normalized.markdown
    assert "World" in normalized.markdown
    assert normalized.warnings == ()


def test_html_normalizer_falls_back_when_markitdown_fails(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "page.html"
    source.write_text(
        "<html><head><style>.x{}</style></head><body><h1>Hello</h1><p>World</p><script>bad()</script></body></html>",
        encoding="utf-8",
    )

    def fail_markitdown(_path: Path) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(normalizers, "_run_markitdown_html", fail_markitdown)

    normalized = normalize_tier1_source(source, "html")

    assert normalized.converter_name == "html_fallback_normalizer"
    assert normalized.markdown == "# Hello\n\nWorld\n"
    assert normalized.warnings == ("markitdown_failed_fallback_html",)
